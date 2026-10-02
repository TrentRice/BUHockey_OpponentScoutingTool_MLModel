"""
College Hockey News (CHN) team schedule scraper.

Parses  table.data.schedule.full  from pages like
  https://www.collegehockeynews.com/schedules/team/Boston-University/10/20252026

Usage (from project root):
  uv run python -m src.ingestion.chn_team_schedule_scraper                        # current season, all teams
  uv run python -m src.ingestion.chn_team_schedule_scraper --season 2025-26        # a specific season
  uv run python -m src.ingestion.chn_team_schedule_scraper --team "Northeastern"   # one team
  uv run python -m src.ingestion.chn_team_schedule_scraper --refresh               # ignore cached HTML

The team list (and conferences) is read from CHN's "Other Teams" menu for the chosen season,
so realignment and new programs are picked up automatically.

Caching: a cached page is reused if it is younger than --max-age-hours, or if every game on it
has been played (a finished season never needs re-downloading). Pages for a season in progress
are re-downloaded once they are older than --max-age-hours, so new results come in on re-run.

Outputs (<tag> is e.g. 2026_27):
  data/raw/chn/schedules/<slug>_<season code>.html
  data/processed/chn/team_schedule_<team_snake>_<tag>.csv
  data/processed/chn/all_games_<tag>.csv   (one row per game, home_team/away_team)
"""

from __future__ import annotations

import argparse
import re
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from urllib.parse import urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.collegehockeynews.com"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; bu-scouting-tool/0.1; personal research)"}

# Any team's page works for discovering the menu; BU is always present.
DISCOVERY_TEAM = ("Boston-University", 10)

# Fallback team list (2025-26) used only if the menu can't be read.
# conference -> [(display name, chn slug, chn team id)]
_FALLBACK_CONFERENCES: dict[str, list[tuple[str, str, int]]] = {
    "Atlantic Hockey": [
        ("Air Force", "Air-Force", 1),
        ("Army", "Army", 6),
        ("Bentley", "Bentley", 8),
        ("Canisius", "Canisius", 13),
        ("Holy Cross", "Holy-Cross", 23),
        ("Mercyhurst", "Mercyhurst", 28),
        ("Niagara", "Niagara", 39),
        ("RIT", "RIT", 49),
        ("Robert Morris", "Robert-Morris", 50),
        ("Sacred Heart", "Sacred-Heart", 51),
    ],
    "Big Ten": [
        ("Michigan", "Michigan", 31),
        ("Michigan State", "Michigan-State", 32),
        ("Minnesota", "Minnesota", 34),
        ("Notre Dame", "Notre-Dame", 43),
        ("Ohio State", "Ohio-State", 44),
        ("Penn State", "Penn-State", 60),
        ("Wisconsin", "Wisconsin", 58),
    ],
    "CCHA": [
        ("Augustana", "Augustana", 64),
        ("Bemidji State", "Bemidji-State", 7),
        ("Bowling Green", "Bowling-Green", 11),
        ("Ferris State", "Ferris-State", 21),
        ("Lake Superior", "Lake-Superior", 24),
        ("Michigan Tech", "Michigan-Tech", 33),
        ("Minnesota State", "Minnesota-State", 35),
        ("Northern Michigan", "Northern-Michigan", 42),
        ("St. Thomas", "St-Thomas", 63),
    ],
    "Independent": [
        ("Alaska", "Alaska", 4),
        ("Alaska-Anchorage", "Alaska-Anchorage", 3),
        ("Lindenwood", "Lindenwood", 433),
        ("Long Island", "Long-Island", 62),
        ("Stonehill", "Stonehill", 422),
    ],
    "ECAC": [
        ("Brown", "Brown", 12),
        ("Clarkson", "Clarkson", 14),
        ("Colgate", "Colgate", 15),
        ("Cornell", "Cornell", 18),
        ("Dartmouth", "Dartmouth", 19),
        ("Harvard", "Harvard", 22),
        ("Princeton", "Princeton", 45),
        ("Quinnipiac", "Quinnipiac", 47),
        ("RPI", "RPI", 48),
        ("St. Lawrence", "St-Lawrence", 53),
        ("Union", "Union", 54),
        ("Yale", "Yale", 59),
    ],
    "Hockey East": [
        ("Boston College", "Boston-College", 9),
        ("Boston University", "Boston-University", 10),
        ("Connecticut", "Connecticut", 17),
        ("Maine", "Maine", 25),
        ("Mass.-Lowell", "Mass-Lowell", 26),
        ("Massachusetts", "Massachusetts", 27),
        ("Merrimack", "Merrimack", 29),
        ("New Hampshire", "New-Hampshire", 38),
        ("Northeastern", "Northeastern", 41),
        ("Providence", "Providence", 46),
        ("Vermont", "Vermont", 55),
    ],
    "NCHC": [
        ("Arizona State", "Arizona-State", 61),
        ("Colorado College", "Colorado-College", 16),
        ("Denver", "Denver", 20),
        ("Miami", "Miami", 30),
        ("Minnesota-Duluth", "Minnesota-Duluth", 36),
        ("North Dakota", "North-Dakota", 40),
        ("Omaha", "Omaha", 37),
        ("St. Cloud State", "St-Cloud-State", 52),
        ("Western Michigan", "Western-Michigan", 57),
    ],
}

MONTHS = {
    m: i
    for i, m in enumerate(
        [
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ],
        start=1,
    )
}

COLUMNS = [
    "season",
    "team",
    "date",
    "day_of_week",
    "result",
    "goals_for",
    "goals_against",
    "is_home",
    "is_away",
    "is_neutral",
    "opponent",
    "opponent_chn_url",
    "opponent_team_id",
    "is_conference",
    "is_overtime",
    "box_score_url",
    "metrics_url",
    "game_note",
    "source",
    "source_url",
    "team_conference",
    "opponent_conference",
]

GAME_COLUMNS = [
    "season",
    "game_id",
    "date",
    "day_of_week",
    "home_team",
    "away_team",
    "home_team_id",
    "away_team_id",
    "home_goals",
    "away_goals",
    "is_played",
    "is_neutral",
    "is_overtime",
    "is_conference",
    "home_conference",
    "away_conference",
    "game_note",
    "box_score_url",
    "metrics_url",
    "source",
]

# Seconds to wait between network requests (cache hits don't wait). Set from --delay.
REQUEST_DELAY = 1.5
_last_request = 0.0


# ----------------------------------------------------------------------------
# Season + team registry
# ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Season:
    start_year: int

    @property
    def label(self) -> str:  # 2026-27
        return f"{self.start_year}-{(self.start_year + 1) % 100:02d}"

    @property
    def code(self) -> str:  # 20262027 (CHN URL form)
        return f"{self.start_year}{self.start_year + 1}"

    @property
    def tag(self) -> str:  # 2026_27 (file names)
        return self.label.replace("-", "_")

    @classmethod
    def parse(cls, text: str) -> Season:
        m = re.fullmatch(r"\s*(\d{4})(?:[-_/]?(\d{2}|\d{4}))?\s*", text)
        if not m:
            raise ValueError(f"Can't read season {text!r}; use e.g. 2026-27")
        start = int(m.group(1))
        if m.group(2) and int(m.group(2)) % 100 != (start + 1) % 100:
            raise ValueError(
                f"Season {text!r} isn't a single season (expected {start}-{(start + 1) % 100:02d})"
            )
        return cls(start)

    @classmethod
    def current(cls, today: date | None = None) -> Season:
        today = today or date.today()
        return cls(today.year if today.month >= 7 else today.year - 1)


@dataclass
class TeamRegistry:
    teams: dict[str, tuple[str, int]]  # display name -> (chn slug, chn team id)
    conference: dict[str, str] = field(default_factory=dict)  # display name -> conference
    source: str = "menu"

    @property
    def id_to_name(self) -> dict[int, str]:
        return {tid: name for name, (_, tid) in self.teams.items()}

    @property
    def id_to_conference(self) -> dict[int, str]:
        return {tid: self.conference.get(name) for name, (_, tid) in self.teams.items()}


def fallback_registry() -> TeamRegistry:
    teams, conf = {}, {}
    for c, rows in _FALLBACK_CONFERENCES.items():
        for name, slug, tid in rows:
            teams[name], conf[name] = (slug, tid), c
    return TeamRegistry(teams, conf, source="fallback (2025-26 list)")


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def to_int(text: str) -> int | None:
    m = re.search(r"\d+", text or "")
    return int(m.group()) if m else None


def schedule_url(slug: str, team_id: int, season: Season) -> str:
    return f"{BASE_URL}/schedules/team/{slug}/{team_id}/{season.code}"


def parse_team_menu(html: str) -> TeamRegistry | None:
    """Read the 'Other Teams' menu: <p>Conference</p><ul><li><a href=".../slug/id/season">Name."""
    soup = BeautifulSoup(html, "lxml")
    menu = soup.select_one("li.teamlist > div")
    if menu is None:
        return None
    teams, confs, conf = {}, {}, "Unknown"
    for el in menu.find_all(["p", "ul"], recursive=False):
        if el.name == "p":
            conf = clean(el.get_text()).replace("D-I Independent", "Independent")
            continue
        for a in el.select("a[href]"):
            m = re.search(r"/schedules/team/([^/]+)/(\d+)/", a["href"])
            if m:
                name = clean(a.get_text())
                teams[name], confs[name] = (m.group(1), int(m.group(2))), conf
    return TeamRegistry(teams, confs) if len(teams) >= 30 else None


def load_registry(
    season: Season, root: Path, refresh: bool = False, max_age_hours: float = 6.0
) -> TeamRegistry:
    slug, tid = DISCOVERY_TEAM
    try:
        html = fetch_html(
            schedule_url(slug, tid, season),
            root / f"data/raw/chn/schedules/{slug}_{season.code}.html",
            refresh,
            max_age_hours,
        )
        reg = parse_team_menu(html)
    except Exception as e:
        print(f"WARNING: couldn't read the team menu ({e}); using the built-in 2025-26 list")
        return fallback_registry()
    if reg is None:
        print("WARNING: team menu not found on the page; using the built-in 2025-26 list")
        return fallback_registry()
    return reg


# ----------------------------------------------------------------------------
# Fetching / caching
# ----------------------------------------------------------------------------


def is_final(html: str) -> bool:
    """True when every game on the schedule page has a result (nothing left to wait for)."""
    table = BeautifulSoup(html, "lxml").select_one("table.data.schedule.full")
    if table is None:
        return False
    rows = [
        tr
        for tr in table.find_all("tr")
        if "stats-section" not in (tr.get("class") or []) and tr.find("td")
    ]
    if not rows:
        return False
    for tr in rows:
        td = tr.select_one("td.result")
        if td is None or not clean(td.get_text()):
            return False
    return True


def fetch_html(
    url: str, cache_path: Path, refresh: bool = False, max_age_hours: float = 6.0
) -> str:
    global _last_request
    if cache_path.exists() and not refresh:
        html = cache_path.read_text(encoding="utf-8")
        age_hours = (time.time() - cache_path.stat().st_mtime) / 3600
        if age_hours < max_age_hours or is_final(html):
            return html
    wait = REQUEST_DELAY - (time.time() - _last_request)
    if wait > 0:
        time.sleep(wait)
    resp = requests.get(url, headers=HEADERS, timeout=30)
    _last_request = time.time()
    resp.raise_for_status()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(resp.text, encoding="utf-8")
    return resp.text


# ----------------------------------------------------------------------------
# Parsing
# ----------------------------------------------------------------------------


def parse_opponent_cell(td) -> dict:
    """Opponent link + trailing markers like (nc) / (ex)."""
    link = td.find("a", href=re.compile(r"/reports/team/"))
    text = clean(td.get_text(" "))
    name = clean(link.get_text()) if link else re.sub(r"\(.*?\)", "", text).strip()
    markers = {m.lower() for m in re.findall(r"\(([^)]*)\)", text)}
    href = urljoin(BASE_URL, link["href"]) if link else None
    team_id = None
    if href:
        m = re.search(r"/reports/team/[^/]+/(\d+)", href)
        team_id = int(m.group(1)) if m else None
    return {
        "opponent": name,
        "opponent_chn_url": href,
        "opponent_team_id": team_id,
        "is_nc": "nc" in markers,
        "is_exhibition": "ex" in markers,
    }


def parse_footnotes(soup) -> dict[str, str]:
    """Footnotes under the table: <b>2</b> Beanpot - TD Garden, Boston, Mass.<br/>"""
    notes = {}
    for box in soup.select("div.factbox"):
        for b in box.find_all("b"):
            marker = clean(b.get_text())
            text = b.next_sibling
            if marker.isdigit() and isinstance(text, str):
                notes[marker] = clean(text)
    return notes


def parse_schedule_html(html: str, team: str, source_url: str, season: Season) -> pd.DataFrame:
    # lxml (not html.parser): CHN leaves <td> tags unclosed, and html.parser nests them.
    soup = BeautifulSoup(html, "lxml")
    table = soup.select_one("table.data.schedule.full")
    if table is None:
        raise ValueError(f"No table.data.schedule.full found for {team} ({source_url})")

    footnotes = parse_footnotes(soup)
    rows, skipped, cur_year, cur_month = [], 0, None, None
    for tr in table.find_all("tr"):
        # Month separator: <tr class="stats-section"><td>October 2025</td></tr>
        if "stats-section" in (tr.get("class") or []):
            m = re.match(r"([A-Za-z]+)\s+(\d{4})", clean(tr.get_text()))
            if m and m.group(1) in MONTHS:
                cur_month, cur_year = MONTHS[m.group(1)], int(m.group(2))
            continue

        tds = tr.find_all("td", recursive=False)
        if not tds or cur_month is None:
            continue

        # Date cell: "04 Sat". Anything else isn't a game row.
        dm = re.match(r"(\d{1,2})\s*([A-Za-z]{3})?", clean(tds[0].get_text(" ")))
        if not dm:
            continue
        game_date = date(cur_year, cur_month, int(dm.group(1)))
        dow = dm.group(2)

        # Result cell is absent/empty for games not yet played.
        res_td = tr.select_one("td.result")
        idx = tds.index(res_td) if res_td is not None and res_td in tds else None
        result = clean(res_td.get_text()) if res_td is not None else ""
        played = bool(result)

        # Opponent cell: the team link if there is one; otherwise (exhibitions/non-D-I have no
        # link) the cell 5 after the result cell, or the cell carrying an (nc)/(ex) marker.
        opp_td = next((t for t in tds if t.find("a", href=re.compile(r"/reports/team/"))), None)
        if opp_td is None and idx is not None and len(tds) > idx + 5:
            opp_td = tds[idx + 5]
        if opp_td is None:
            opp_td = next((t for t in tds if re.search(r"\((nc|ex)\)", t.get_text(), re.I)), None)
        if opp_td is None:
            skipped += 1
            continue
        opp_i = tds.index(opp_td)

        gf = ga = None
        is_ot = False
        if played and idx is not None:
            gf = to_int(tds[idx + 1].get_text()) if len(tds) > idx + 1 else None
            ga = to_int(tds[idx + 2].get_text()) if len(tds) > idx + 2 else None
            # OT/SO marker sits in the cells between the score and the home/away cell
            zone = " ".join(clean(t.get_text(" ")) for t in tds[idx:opp_i])
            is_ot = bool(re.search(r"\b(\d*OT|SO)\b", zone, re.I))

        # Home/away marker: "at" (away), "vs." (neutral), blank (home), somewhere before opponent
        ha_raw = next(
            (
                clean(t.get_text()).lower()
                for t in tds[:opp_i]
                if clean(t.get_text()).lower() in {"at", "vs.", "vs"}
            ),
            "",
        )
        is_away = ha_raw == "at"
        is_neutral = ha_raw.startswith("vs")
        is_home = not (is_away or is_neutral)

        opp = parse_opponent_cell(opp_td)
        if opp["is_exhibition"]:  # exhibitions don't count toward the D-I season; drop them
            continue

        marker = clean(tds[1].get_text()) if len(tds) > 1 else ""
        box = tr.find("a", title="Box Score")
        metrics = tr.find("a", title="Game Metrics")

        rows.append(
            {
                "season": season.label,
                "team": team,
                "date": game_date.isoformat(),
                "day_of_week": dow,
                "result": result or None,
                "goals_for": gf,
                "goals_against": ga,
                "is_home": is_home,
                "is_away": is_away,
                "is_neutral": is_neutral,
                "opponent": opp["opponent"],
                "opponent_chn_url": opp["opponent_chn_url"],
                "opponent_team_id": opp["opponent_team_id"],
                # CHN tags every non-league game (nc), including tournament games
                # (Beanpot, HEA tourney), so untagged == counts in league standings.
                "is_conference": not opp["is_nc"],
                "is_overtime": is_ot,
                "box_score_url": urljoin(BASE_URL, box["href"]) if box else None,
                "metrics_url": urljoin(BASE_URL, metrics["href"]) if metrics else None,
                "game_note": footnotes.get(marker),
                "source": "college_hockey_news",
                "source_url": source_url,
            }
        )

    df = pd.DataFrame(rows, columns=COLUMNS)
    for col in ("goals_for", "goals_against", "opponent_team_id"):
        df[col] = df[col].astype("Int64")
    df.attrs["skipped_rows"] = skipped
    return df


# ----------------------------------------------------------------------------
# Team-level scrape
# ----------------------------------------------------------------------------


def scrape_team(
    team: str,
    season: Season,
    reg: TeamRegistry,
    refresh: bool = False,
    max_age_hours: float = 6.0,
    root: Path = Path("."),
) -> pd.DataFrame:
    slug, team_id = reg.teams[team]
    url = schedule_url(slug, team_id, season)
    html = fetch_html(
        url, root / f"data/raw/chn/schedules/{slug}_{season.code}.html", refresh, max_age_hours
    )
    df = parse_schedule_html(html, team, url, season)

    df["team_conference"] = reg.conference.get(team)
    df["opponent_conference"] = df["opponent_team_id"].map(reg.id_to_conference)

    out = (
        root / f"data/processed/chn/team_schedule_{slug.lower().replace('-', '_')}_{season.tag}.csv"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    played = int(df["result"].notna().sum())
    msg = f"{team}: {len(df)} games ({played} played) -> {out}"
    if df.attrs.get("skipped_rows"):
        msg += f"  [WARNING: {df.attrs['skipped_rows']} rows couldn't be read]"
    print(msg)
    return df


# ----------------------------------------------------------------------------
# One row per game
# ----------------------------------------------------------------------------


def _box_slots(url) -> tuple[str, str] | None:
    """CHN box URLs end in /<away abbr>/<home abbr>/, e.g. /box/final/20251025/bu_/con/."""
    if not isinstance(url, str):
        return None
    parts = url.rstrip("/").split("/")
    return (parts[-2], parts[-1]) if len(parts) >= 2 else None


def _differs(a, b) -> bool:
    if pd.isna(a) and pd.isna(b):
        return False
    if pd.isna(a) or pd.isna(b):
        return True
    return a != b


def build_games_table(team_games: pd.DataFrame, reg: TeamRegistry) -> pd.DataFrame:
    """Collapse team-perspective rows (each game appears once per team) into one row per game.

    Home/away comes from each team's own flags (blank = home, "at" = away). Neutral-site games
    (both teams show "vs.") use the designated home team encoded in the box score URL.
    """
    id_to_name = reg.id_to_name
    d = team_games.copy()
    for col in ("is_home", "is_away", "is_neutral", "is_conference", "is_overtime"):
        d[col] = d[col].astype(bool)  # concat with empty frames can degrade these to object
    d["team_id"] = d["team"].map(lambda t: reg.teams[t][1])
    # Canonical opponent name (CHN link text varies); non-D-I opponents keep their raw name.
    d["opp_name"] = [
        id_to_name.get(int(i), o) if pd.notna(i) else o
        for i, o in zip(d["opponent_team_id"], d["opponent"])
    ]
    d["opp_key"] = [
        int(i) if pd.notna(i) else o for i, o in zip(d["opponent_team_id"], d["opp_name"])
    ]
    d["game_id"] = [
        f"{dt}_{'_'.join(sorted(map(str, (t, o))))}"
        for dt, t, o in zip(d["date"], d["team_id"], d["opp_key"])
    ]

    # Learn box-URL abbreviations (e.g. "bu_" -> Boston University) from non-neutral games.
    abbr: dict[str, str] = {}
    for r in d[~d["is_neutral"]].itertuples():
        slots = _box_slots(r.box_score_url)
        if slots:
            abbr[slots[1] if r.is_home else slots[0]] = r.team

    out, one_sided, mismatches, unresolved, provisional = [], 0, [], [], []
    for gid, g in d.groupby("game_id", sort=False):
        rows = list(g.itertuples())
        if len(rows) == 1:
            one_sided += 1
        r0 = rows[0]

        home_row = next((r for r in rows if r.is_home), None)
        away_row = next((r for r in rows if r.is_away), None)
        if home_row is not None:
            home, away = home_row.team, home_row.opp_name
        elif away_row is not None:
            home, away = away_row.opp_name, away_row.team
        else:  # neutral site: use the designated home team from the box URL
            slots = _box_slots(
                next((r.box_score_url for r in rows if _box_slots(r.box_score_url)), None)
            )
            away, home = (abbr.get(slots[0]), abbr.get(slots[1])) if slots else (None, None)
            names = {r0.team, r0.opp_name}
            if home is None and away is not None:
                home = next(iter(names - {away}), None)
            if away is None and home is not None:
                away = next(iter(names - {home}), None)
            if home is None or away is None:  # last resort: alphabetical, flagged below
                has_box = any(_box_slots(r.box_score_url) for r in rows)
                (unresolved if has_box else provisional).append(gid)
                home, away = sorted(names)[::-1]

        # Goals from a row that has a score (a stale page may not yet), oriented to home/away.
        src = next((r for r in rows if pd.notna(r.goals_for)), r0)
        if src.team == home:
            hg, ag = src.goals_for, src.goals_against
        else:
            hg, ag = src.goals_against, src.goals_for

        if len(rows) == 2:
            a, b = rows
            if (
                a.box_score_url != b.box_score_url
                and pd.notna(a.box_score_url)
                and pd.notna(b.box_score_url)
            ) or (
                _differs(a.goals_for, b.goals_against)
                or _differs(a.goals_against, b.goals_for)
                or a.is_conference != b.is_conference
            ):
                mismatches.append(gid)

        box = next((r.box_score_url for r in rows if isinstance(r.box_score_url, str)), None)
        metrics = next((r.metrics_url for r in rows if isinstance(r.metrics_url, str)), None)
        note = next((r.game_note for r in rows if isinstance(r.game_note, str)), None)
        out.append(
            {
                "season": r0.season,
                "game_id": gid,
                "date": r0.date,
                "day_of_week": r0.day_of_week,
                "home_team": home,
                "away_team": away,
                "home_team_id": reg.teams[home][1] if home in reg.teams else None,
                "away_team_id": reg.teams[away][1] if away in reg.teams else None,
                "home_goals": hg,
                "away_goals": ag,
                "is_played": bool(pd.notna(hg) and pd.notna(ag)),
                "is_neutral": all(r.is_neutral for r in rows),
                "is_overtime": any(r.is_overtime for r in rows),
                "is_conference": all(r.is_conference for r in rows),
                "home_conference": reg.conference.get(home),
                "away_conference": reg.conference.get(away),
                "game_note": note,
                "box_score_url": box,
                "metrics_url": metrics,
                "source": r0.source,
            }
        )

    games = pd.DataFrame(out, columns=GAME_COLUMNS)
    for col in ("home_goals", "away_goals", "home_team_id", "away_team_id"):
        games[col] = games[col].astype("Int64")
    games = games.sort_values(["date", "game_id"]).reset_index(drop=True)

    print(
        f"Games: {len(games)} unique games ({int(games['is_played'].sum())} played) from "
        f"{len(d)} team-game rows ({one_sided} seen from one side only: "
        f"opponent not scraped, e.g. non-D-I or failed)"
    )
    if mismatches:
        print(
            f"WARNING: {len(mismatches)} games differ between the two teams' pages "
            "(often one page is older than the other; re-run with --refresh), e.g.:"
        )
        for gid in mismatches[:5]:
            print(f"  {gid}")
    if unresolved:
        print(
            f"WARNING: home/away guessed for {len(unresolved)} neutral-site games: {unresolved[:5]}"
        )
    if provisional:
        print(
            f"Note: {len(provisional)} unplayed neutral-site games have provisional home/away "
            "(the designated home team appears once a box score exists)."
        )
    return games


# ----------------------------------------------------------------------------
# All teams
# ----------------------------------------------------------------------------


def scrape_many(
    season: Season,
    reg: TeamRegistry,
    refresh: bool = False,
    max_age_hours: float = 6.0,
    root: Path = Path("."),
) -> pd.DataFrame:
    print(f"Season {season.label}: {len(reg.teams)} teams (team list: {reg.source})")
    frames, failures = [], []
    for t in reg.teams:
        try:
            frames.append(scrape_team(t, season, reg, refresh, max_age_hours, root))
        except Exception as e:  # keep going; report at the end
            print(f"{t}: FAILED -> {e}")
            failures.append((t, str(e)))

    nonempty = [f for f in frames if len(f)]
    team_games = (
        pd.concat(nonempty, ignore_index=True) if nonempty else pd.DataFrame(columns=COLUMNS)
    )
    games = (
        build_games_table(team_games, reg)
        if len(team_games)
        else pd.DataFrame(columns=GAME_COLUMNS)
    )
    out = root / f"data/processed/chn/all_games_{season.tag}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    games.to_csv(out, index=False)
    print(f"\nCombined: {len(games)} games from {len(frames)} teams -> {out}")

    empty = [
        t
        for t, f in zip([t for t in reg.teams if t not in {x[0] for x in failures}], frames)
        if len(f) == 0
    ]
    if empty:
        print("Teams with 0 parsed rows (check the HTML):", ", ".join(empty))
    if failures:
        print("Failed teams (re-run with the same command; fresh cached pages are skipped):")
        for t, msg in failures:
            print(f"  {t}: {msg}")
    return games


def resolve_team(reg: TeamRegistry, text: str) -> str:
    by_lower = {n.lower(): n for n in reg.teams}
    if text.lower() in by_lower:
        return by_lower[text.lower()]
    close = [n for n in reg.teams if text.lower() in n.lower()]
    raise SystemExit(
        f"Unknown team {text!r}. " + (f"Did you mean: {', '.join(close)}?" if close else "")
    )


def main() -> None:
    global REQUEST_DELAY
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", help="e.g. 2026-27 (default: the current season)")
    ap.add_argument("--team", help="scrape just this team (default: all teams)")
    ap.add_argument("--all", action="store_true", help="scrape every team (this is the default)")
    ap.add_argument("--refresh", action="store_true", help="ignore cached HTML")
    ap.add_argument("--delay", type=float, default=1.5, help="seconds between requests")
    ap.add_argument(
        "--max-age-hours",
        type=float,
        default=6.0,
        help="re-download cached pages older than this unless the season is finished",
    )
    args = ap.parse_args()
    REQUEST_DELAY = args.delay

    season = Season.parse(args.season) if args.season else Season.current()
    root = Path(".")
    reg = load_registry(season, root, args.refresh, args.max_age_hours)

    if args.team:
        scrape_team(
            resolve_team(reg, args.team), season, reg, args.refresh, args.max_age_hours, root
        )
    else:
        scrape_many(season, reg, args.refresh, args.max_age_hours, root)


if __name__ == "__main__":
    main()
