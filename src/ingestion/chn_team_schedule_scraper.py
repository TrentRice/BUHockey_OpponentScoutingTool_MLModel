"""
College Hockey News (CHN) team schedule scraper.

Parses  table.data.schedule.full  from pages like
  https://www.collegehockeynews.com/schedules/team/Boston-University/10/20252026

Usage (from project root):
  uv run python -m src.ingestion.chn_team_schedule_scraper                 # BU only
  uv run python -m src.ingestion.chn_team_schedule_scraper --team "Northeastern"
  uv run python -m src.ingestion.chn_team_schedule_scraper --all           # all 63 D-I teams + combined CSV

Outputs:
  data/raw/chn/schedules/<slug>_<season>.html
  data/processed/chn/team_schedule_<team_snake>_2025_26.csv
"""

from __future__ import annotations

import argparse
import re
import time
from datetime import date
from pathlib import Path
from urllib.parse import urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.collegehockeynews.com"
SEASON_LABEL = "2025-26"
SEASON_CODE = "20252026"

# conference -> [(display name, chn slug, chn team id)]
# Source: the "Other Teams" menu on any CHN 2025-26 schedule page.
_CONFERENCES: dict[str, list[tuple[str, str, int]]] = {
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

# name -> (chn slug, chn team id)
TEAMS: dict[str, tuple[str, int]] = {
    name: (slug, tid) for teams in _CONFERENCES.values() for name, slug, tid in teams
}
TEAM_CONFERENCE: dict[str, str] = {
    name: conf for conf, teams in _CONFERENCES.items() for name, _, _ in teams
}
ID_TO_CONFERENCE: dict[int, str] = {
    tid: conf for conf, teams in _CONFERENCES.items() for _, _, tid in teams
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

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; bu-scouting-tool/0.1; personal research)"}

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
    "home_away_raw",
    "opponent",
    "opponent_chn_url",
    "opponent_team_id",
    "is_conference",
    "is_exhibition",
    "is_overtime",
    "box_score_url",
    "metrics_url",
    "game_note",
    "source",
    "source_url",
    "team_conference",
    "opponent_conference",
    "is_same_conference_opponent",
]


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def to_int(text: str) -> int | None:
    m = re.search(r"\d+", text or "")
    return int(m.group()) if m else None


def schedule_url(slug: str, team_id: int, season_code: str = SEASON_CODE) -> str:
    return f"{BASE_URL}/schedules/team/{slug}/{team_id}/{season_code}"


def fetch_html(url: str, cache_path: Path, refresh: bool = False) -> str:
    if cache_path.exists() and not refresh:
        return cache_path.read_text(encoding="utf-8")
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(resp.text, encoding="utf-8")
    return resp.text


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


def parse_schedule_html(html: str, team: str, source_url: str) -> pd.DataFrame:
    # lxml (not html.parser): CHN leaves <td> tags unclosed, and html.parser nests them.
    soup = BeautifulSoup(html, "lxml")
    table = soup.select_one("table.data.schedule.full")
    if table is None:
        raise ValueError(f"No table.data.schedule.full found for {team} ({source_url})")

    footnotes = parse_footnotes(soup)
    rows, cur_year, cur_month = [], None, None
    for tr in table.find_all("tr"):
        # Month separator: <tr class="stats-section"><td>October 2025</td></tr>
        if "stats-section" in (tr.get("class") or []):
            m = re.match(r"([A-Za-z]+)\s+(\d{4})", clean(tr.get_text()))
            if m and m.group(1) in MONTHS:
                cur_month, cur_year = MONTHS[m.group(1)], int(m.group(2))
            continue

        tds = tr.find_all("td", recursive=False)
        if len(tds) < 8 or cur_month is None:
            continue

        # Date cell: "04 Sat"
        dm = re.match(r"(\d{1,2})\s*([A-Za-z]{3})?", clean(tds[0].get_text(" ")))
        if not dm:
            continue
        game_date = date(cur_year, cur_month, int(dm.group(1)))
        dow = dm.group(2)

        # Result + score
        res_td = tr.select_one("td.result")
        result = clean(res_td.get_text()) if res_td else ""
        idx = tds.index(res_td) if res_td in tds else 2
        gf = to_int(tds[idx + 1].get_text()) if len(tds) > idx + 1 else None
        ga = to_int(tds[idx + 2].get_text()) if len(tds) > idx + 2 else None
        # OT/SO marker lives somewhere in the cells between the score and the opponent
        score_zone = " ".join(clean(t.get_text(" ")) for t in tds[idx : idx + 5])
        is_ot = bool(re.search(r"\b(\d*OT|SO)\b", score_zone, re.I))

        # Cells after the result td: [gf, "- ga", ot, home/away, opponent, box, metrics]
        # Opponent is located by position because exhibition opponents have no link.
        if len(tds) < idx + 6:
            continue
        ha_raw = clean(tds[idx + 4].get_text()).lower()
        opp_td = tds[idx + 5]
        is_away = ha_raw == "at"
        is_neutral = ha_raw.startswith("vs")
        is_home = not (is_away or is_neutral)

        opp = parse_opponent_cell(opp_td)

        marker = clean(tds[1].get_text())
        box = tr.find("a", title="Box Score")
        metrics = tr.find("a", title="Game Metrics")

        rows.append(
            {
                "season": SEASON_LABEL,
                "team": team,
                "date": game_date.isoformat(),
                "day_of_week": dow,
                "result": result or None,
                "goals_for": gf,
                "goals_against": ga,
                "is_home": is_home,
                "is_away": is_away,
                "is_neutral": is_neutral,
                "home_away_raw": ha_raw,
                "opponent": opp["opponent"],
                "opponent_chn_url": opp["opponent_chn_url"],
                "opponent_team_id": opp["opponent_team_id"],
                # Assumption: not (nc) and not (ex) => conference. Refine later for
                # non-league opponents that CHN doesn't tag.
                "is_conference": not opp["is_nc"] and not opp["is_exhibition"],
                "is_exhibition": opp["is_exhibition"],
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
    return df


def scrape_team(team: str, refresh: bool = False, root: Path = Path(".")) -> pd.DataFrame:
    slug, team_id = TEAMS[team]
    url = schedule_url(slug, team_id)
    html = fetch_html(url, root / f"data/raw/chn/schedules/{slug}_{SEASON_CODE}.html", refresh)
    df = parse_schedule_html(html, team, url)

    # Real "same league" flag (is_conference only means "counts in league standings").
    df["team_conference"] = TEAM_CONFERENCE[team]
    df["opponent_conference"] = df["opponent_team_id"].map(ID_TO_CONFERENCE)
    df["is_same_conference_opponent"] = df["opponent_conference"] == df["team_conference"]

    out = root / f"data/processed/chn/team_schedule_{slug.lower().replace('-', '_')}_2025_26.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    played = int(df["result"].notna().sum())
    print(f"{team}: {len(df)} games ({played} played) -> {out}")
    return df


def scrape_many(
    teams: list[str], refresh: bool = False, delay: float = 1.5, root: Path = Path(".")
) -> pd.DataFrame:
    frames, failures = [], []
    for i, t in enumerate(teams):
        try:
            frames.append(scrape_team(t, refresh=refresh, root=root))
        except Exception as e:  # keep going; report at the end
            print(f"{t}: FAILED -> {e}")
            failures.append((t, str(e)))
        if i < len(teams) - 1:
            time.sleep(delay)

    combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=COLUMNS)
    out = root / "data/processed/chn/all_team_schedules_2025_26.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(out, index=False)

    print(f"\nCombined: {len(combined)} team-game rows from {len(frames)} teams -> {out}")
    empty = [
        t
        for t, f in zip([t for t in teams if t not in {x[0] for x in failures}], frames)
        if len(f) == 0
    ]
    if empty:
        print("Teams with 0 parsed rows (check the HTML):", ", ".join(empty))
    if failures:
        print("Failed teams (re-run with the same command; cached pages are skipped):")
        for t, msg in failures:
            print(f"  {t}: {msg}")
    return combined


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--team", default="Boston University", choices=sorted(TEAMS))
    ap.add_argument("--all", action="store_true", help="scrape every D-I team in TEAMS")
    ap.add_argument("--refresh", action="store_true", help="ignore cached HTML")
    ap.add_argument("--delay", type=float, default=1.5, help="seconds between requests")
    args = ap.parse_args()

    if args.all:
        scrape_many(list(TEAMS), refresh=args.refresh, delay=args.delay)
    else:
        scrape_team(args.team, refresh=args.refresh)


if __name__ == "__main__":
    main()
