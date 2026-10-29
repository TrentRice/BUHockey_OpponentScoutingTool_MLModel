"""
CHN box score parser.

Reads the raw box score pages saved by chn_boxscore_downloader and writes tidy tables for the
season. Team names come from all_games_<tag>.csv so they match the schedule data exactly.

  data/raw/chn/boxscores/<tag>/<game_id>.html   (input)

Outputs (<tag> is e.g. 2026_27), all in data/processed/chn/:
  box_games_<tag>.csv          one row per game: venue, attendance, officials, OT periods, shootout
                               (score, neutral site, conference etc. are in all_games_<tag>.csv)
  box_team_stats_<tag>.csv     one row per team per game: goals/shots by period, PP, PIM, FO, xG
  box_skaters_<tag>.csv        one row per skater per game: G, A, Pt, +/-, Sh, TOI, PIM, FO, F/D
  box_goalies_<tag>.csv        one row per goalie per game: saves, GA, shots against, TOI
  box_goals_<tag>.csv          one row per goal: period, time, strength (PP/SH/EN/EA), scorer, assists
  box_penalties_<tag>.csv      one row per penalty: period, time, player, minutes, infraction
  box_parse_issues_<tag>.csv   games whose numbers don't add up (see check_game)

Usage (from project root):
  uv run python -m src.ingestion.chn_boxscore_parser                    # current season
  uv run python -m src.ingestion.chn_boxscore_parser --season 2025-26

Typical in-season routine:
  uv run python -m src.ingestion.chn_team_schedule_scraper
  uv run python -m src.ingestion.chn_boxscore_downloader
  uv run python -m src.ingestion.chn_boxscore_parser
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
from bs4 import BeautifulSoup

from src.ingestion.chn_boxscore_downloader import box_path, load_played_games
from src.ingestion.chn_team_schedule_scraper import Season, clean

SIDES = ("away", "home")  # CHN lists the visitor first; vscore/hscore rows mark the side


# ----------------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------------


def nums(text: str) -> list[int]:
    """'7‑14' -> [7, 14]. CHN uses a non-breaking hyphen (U+2011), so don't split on '-'."""
    return [int(n) for n in re.findall(r"\d+", text or "")]


def to_int(text: str) -> int | None:
    m = re.fullmatch(r"-?\d+", clean(text or ""))
    return int(m.group()) if m else None


def to_float(text: str) -> float | None:
    try:
        return float(clean(text))
    except ValueError:
        return None


def to_seconds(text: str) -> int | None:
    """'61:16' -> 3676."""
    m = re.fullmatch(r"(\d+):(\d{2})", clean(text or ""))
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


def period_number(label: str) -> int | None:
    """'1st Period' -> 1, 'Overtime' -> 4. CHN heads every overtime 'Overtime' (even in a
    triple-OT game), so 4 means 'overtime', not 'first OT'; box_games.ot_periods has the count."""
    label = clean(label).lower()
    m = re.match(r"(\d)\w*\s+period", label)
    if m:
        return int(m.group(1))
    if "overtime" in label:
        m = re.match(r"(\d)\w*\s+overtime", label)
        return 3 + (int(m.group(1)) if m else 1)
    return None


def own_text(span) -> str:
    """A span's text without nested spans: CHN leaves the event span unclosed, so it swallows
    the venue and attendance spans that follow it."""
    parts = [c if isinstance(c, str) else c.get_text() for c in span.children if c.name != "span"]
    return clean(" ".join(parts))


def cells(tr) -> list[str]:
    return [clean(td.get_text(" ")) for td in tr.find_all("td", recursive=False)]


def side_of(tr) -> str | None:
    cls = tr.get("class") or []
    return "away" if "vscore" in cls else "home" if "hscore" in cls else None


# ----------------------------------------------------------------------------
# Page sections
# ----------------------------------------------------------------------------


@dataclass
class Box:
    """Everything read from one page, before team names are attached."""

    meta: dict
    page_names: dict[str, str]  # side -> team name as printed on the page
    page_ids: dict[str, int | None]  # side -> CHN team id from the header link
    line: dict[str, dict]  # side -> {goals_p1.., shots_p1.., pen, pim, ppg, ppo, fow, fol, xg}
    goals: list[dict] = field(default_factory=list)
    penalties: list[dict] = field(default_factory=list)
    goalies: list[dict] = field(default_factory=list)
    skaters: list[dict] = field(default_factory=list)


def parse_meta(soup) -> tuple[dict, dict, dict]:
    names, ids, scores = {}, {}, {}
    for side, div in zip(SIDES, soup.select("#meta > div.team")):
        a = div.find("h4").find("a")
        names[side] = clean(a.get_text())
        m = re.search(r"/reports/team/[^/]+/(\d+)", a.get("href") or "")
        ids[side] = int(m.group(1)) if m else None
        scores[side] = to_int(div.find("h2").get_text())

    info = soup.select_one("#meta > div:not(.team)")
    meta = {"page_away_goals": scores.get("away"), "page_home_goals": scores.get("home")}
    spans = [own_text(s) for s in info.select("p.sub-meta span")] if info else []
    for text in filter(None, spans):
        key, _, value = text.partition(":")
        key = key.strip().lower()
        if key == "attendance":
            meta["attendance"] = to_int(value.replace(",", "")) or None  # 0 = not reported
        elif key == "referees":
            meta["referees"] = value.strip() or None
        elif key == "asst. referees":
            meta["asst_referees"] = value.strip() or None
        elif text.startswith("at "):
            meta["venue"] = text[3:]
        elif text.endswith("Game") or "Game " in text:
            meta["game_type"] = text
        else:  # e.g. "National Championship", "Beanpot Final"
            meta["event"] = f"{meta['event']}; {text}" if meta.get("event") else text

    # "Arizona State won shootout, 2-0 (2 rounds)"
    so = next((p for p in (info.find_all("p") if info else []) if "shootout" in p.get_text()), None)
    if so:
        m = re.match(r"(.+?) won shootout,\s*(\d+)\D+(\d+)", clean(so.get_text()))
        if m:
            meta["shootout_winner_page_name"] = m.group(1)
            meta["shootout_score"] = f"{m.group(2)}-{m.group(3)}"
    return meta, names, ids


def parse_linescore(soup) -> dict[str, dict]:
    line = {s: {} for s in SIDES}
    ot_periods = 0
    for kind in ("goals", "shots"):
        table = soup.select_one(f"#{kind} table")
        rows = table.find_all("tr")
        labels = cells(rows[0])[1:]  # 1, 2, 3, OT (or 2OT, ...), T
        ot_periods = max(ot_periods, sum("OT" in lab for lab in labels))
        for side, tr in zip(SIDES, rows[1:3]):
            pairs = list(zip(labels, cells(tr)[1:]))
            vals = dict(pairs)
            for p in ("1", "2", "3"):
                line[side][f"{kind}_p{p}"] = to_int(vals.get(p, ""))
            # multi-OT games repeat the label ("OT", "OT"), so sum from pairs, not the dict
            ot = [to_int(v) for lab, v in pairs if "OT" in lab]
            line[side][f"{kind}_ot"] = sum(v or 0 for v in ot) if ot else None
            line[side][kind] = to_int(vals.get("T", ""))

    table = soup.select_one("#pp table")
    rows = table.find_all("tr")
    labels = cells(rows[0])
    for side, tr in zip(SIDES, rows[1:3]):
        vals = dict(zip(labels, cells(tr)))
        pen = nums(vals.get("Pen‑PIM", ""))
        pp = nums(vals.get("PPG‑PPO", ""))
        fo = nums(vals.get("FOW‑L", ""))
        line[side].update(
            penalties=pen[0] if len(pen) == 2 else None,
            pim=pen[1] if len(pen) == 2 else None,
            pp_goals=pp[0] if len(pp) == 2 else None,
            pp_opportunities=pp[1] if len(pp) == 2 else None,
            faceoffs_won=fo[0] if len(fo) == 2 else None,
            faceoffs_lost=fo[1] if len(fo) == 2 else None,
            xg=to_float(vals.get("xG", "")),
        )
    line["ot_periods"] = ot_periods
    return line


def event_rows(soup, div_id: str):
    """Yield (period, side, cell texts, tr) for the scoring / penalty summaries."""
    div = soup.find("div", id=div_id)
    if div is None:
        return
    period = None
    for tr in div.find_all("tr"):
        if tr.find_parent("div", id="shootout"):
            continue  # shootout attempts are a nested table inside #scoring
        if "stats-section" in (tr.get("class") or []):
            period = period_number(tr.get_text()) or period
            continue
        side = side_of(tr)
        if side and period:
            yield period, side, cells(tr), tr


def parse_goals(soup) -> list[dict]:
    out = []
    for period, side, c, tr in event_rows(soup, "scoring"):
        # team | strength | headshot | scorer (n) | assists | time | -
        link = tr.find_all("td", recursive=False)[3].find("a", href=re.compile(r"/players/"))
        m = re.search(r"/(\d+)$", link["href"]) if link else None
        scorer = re.sub(r"\s*\(\d+\)$", "", c[3])
        season_goal = re.search(r"\((\d+)\)$", c[3])
        assists = [a.strip() for a in c[4].split(",") if a.strip()] if len(c) > 4 else []
        strength = c[1]
        flags = {f.strip() for f in strength.split(",")}
        # manpower like "5x3" (scoring team first): CHN uses it instead of PP/SH when the gap is 2
        manpower = [tuple(map(int, f.split("x"))) for f in flags if re.fullmatch(r"\d+x\d+", f)]
        out.append(
            {
                "side": side,
                "period": period,
                "time": c[5] if len(c) > 5 else None,
                "time_seconds": to_seconds(c[5]) if len(c) > 5 else None,
                "strength": strength or "EV",
                "is_pp": "PP" in flags or any(us > them for us, them in manpower),
                "is_sh": "SH" in flags or any(us < them for us, them in manpower),
                "is_en": "EN" in flags,
                "is_extra_attacker": "EA" in flags,
                "is_penalty_shot": "PS" in flags,
                "scorer": scorer,
                "scorer_chn_id": int(m.group(1)) if m else None,
                "scorer_season_goal": int(season_goal.group(1)) if season_goal else None,
                "assist1": assists[0] if len(assists) > 0 else None,
                "assist2": assists[1] if len(assists) > 1 else None,
            }
        )
    return out


def parse_penalties(soup) -> list[dict]:
    out = []
    for period, side, c, _ in event_rows(soup, "penalties"):
        # team | player | minutes | infraction | time | -
        out.append(
            {
                "side": side,
                "period": period,
                "time": c[4] if len(c) > 4 else None,
                "time_seconds": to_seconds(c[4]) if len(c) > 4 else None,
                "player": c[1],
                "minutes": to_int(c[2]),
                "infraction": c[3],
            }
        )
    return out


def parse_goalies(soup, side_by_name: dict[str, str]) -> list[dict]:
    out, side = [], None
    div = soup.find("div", id="goalies")
    for i, tr in enumerate(div.find_all("tr") if div else []):
        c = cells(tr)
        if "stats-header" in (tr.get("class") or []):
            side = side_by_name.get(c[0])
            continue
        if side is None or len(c) < 4:
            continue
        out.append(
            {
                "side": side,
                "goalie": c[0],
                "saves": to_int(c[1]),
                "goals_against": to_int(c[2]),
                "toi_seconds": to_seconds(c[3]),
                "order": i,
            }
        )
    return out


LINEUP_POSITIONS = {"forward": "F", "defense": "D", "goalie": "G"}


def parse_lineups(soup, side_by_name: dict[str, str]) -> dict[tuple[str, str], str]:
    """#linechart: each team's dressed players under Forward / Defense / Extras / Goalie.
    Extras carry their position in the name: 'Jack Rosensteel (F)'. -> {(side, name): F/D/G}"""
    out = {}
    chart = soup.find("div", id="linechart")
    for team in chart.find_all("div", recursive=False) if chart else []:
        h3 = team.find("h3")
        side = side_by_name.get(clean(h3.get_text())) if h3 else None
        if side is None:
            continue
        for h4 in team.find_all("h4"):
            group = h4.find_next_sibling("div")
            pos = LINEUP_POSITIONS.get(clean(h4.get_text()).lower())
            for d in group.find_all("div") if group else []:
                m = re.fullmatch(r"(.+?)\s*\(([FDG])\)", clean(d.get_text()))
                name, p = (m.group(1), m.group(2)) if m else (clean(d.get_text()), pos)
                if p:
                    out[(side, name)] = p
    return out


def parse_skaters(soup, side_by_name: dict[str, str]) -> list[dict]:
    """playersum tables: one per team (order varies), grouped forwards, then D, then the rest;
    each group after the first starts with a row of class 'first'. The last group is mostly
    goalies, but a skater with no listed position lands there too; for that group only, the
    lineup section decides (it mislabels the occasional F/D, so it isn't used for the others)."""
    lineup = parse_lineups(soup, side_by_name)
    out = []
    for table in soup.select("#playersums div.playersum table"):
        rows = table.find_all("tr")
        header = cells(rows[0])
        side = side_by_name.get(header[0])
        if side is None:
            continue
        cols = header[1:]
        group = 0
        for tr in rows[1:]:
            if "first" in (tr.get("class") or []):
                group += 1
            c = cells(tr)
            if len(c) < 2:
                continue
            position = {0: "F", 1: "D"}.get(group) or lineup.get((side, c[0]), "G")
            if position == "G":  # goalies are in the goalie table
                continue
            vals = dict(zip(cols, c[1:]))
            fo = nums(vals.get("FOW‑L", ""))
            out.append(
                {
                    "side": side,
                    "player": c[0],
                    "position": position,
                    "goals": to_int(vals.get("G", "")),
                    "assists": to_int(vals.get("A", "")),
                    "points": to_int(vals.get("Pt.", "")),
                    "plus_minus": to_int(vals.get("+/-", "")),
                    "shots": to_int(vals.get("Sh", "")),
                    "toi_seconds": to_seconds(vals.get("TOI", "")),
                    "pim": to_int(vals.get("PIM", "")),
                    "faceoffs_won": fo[0] if len(fo) == 2 else None,
                    "faceoffs_lost": fo[1] if len(fo) == 2 else None,
                }
            )
    return out


def parse_box_html(html: str) -> Box:
    soup = BeautifulSoup(html, "lxml")
    if soup.select_one("#meta") is None or soup.select_one("#goals table") is None:
        raise ValueError("no #meta / #goals section; not a box score page")
    meta, names, ids = parse_meta(soup)
    line = parse_linescore(soup)
    meta["ot_periods"] = line.pop("ot_periods")
    side_by_name = {n: s for s, n in names.items()}
    return Box(
        meta=meta,
        page_names=names,
        page_ids=ids,
        line=line,
        goals=parse_goals(soup),
        penalties=parse_penalties(soup),
        goalies=parse_goalies(soup, side_by_name),
        skaters=parse_skaters(soup, side_by_name),
    )


# ----------------------------------------------------------------------------
# Consistency checks
# ----------------------------------------------------------------------------


def check_game(box: Box, game) -> list[str]:
    """Numbers that should agree with each other. Returns a list of problems (empty = clean)."""
    problems = []
    other = {"away": "home", "home": "away"}
    for side in SIDES:
        ln = box.line[side]
        expected = game.away_goals if side == "away" else game.home_goals
        if pd.notna(expected) and ln["goals"] != int(expected):
            problems.append(f"{side} goals {ln['goals']} != schedule {int(expected)}")
        if box.page_ids.get(side) is None:
            continue  # non-D-I team: CHN doesn't record its goals, penalties or players
        n_goals = sum(g["side"] == side for g in box.goals)
        if ln["goals"] is not None and n_goals != ln["goals"]:
            problems.append(f"{side} scoring summary has {n_goals} goals, linescore {ln['goals']}")
        sk = [s for s in box.skaters if s["side"] == side]
        if sk:
            sk_goals = sum(s["goals"] or 0 for s in sk)
            if ln["goals"] is not None and sk_goals != ln["goals"]:
                problems.append(f"{side} skater goals {sk_goals} != {ln['goals']}")
        gl = [g for g in box.goalies if g["side"] == side]
        if gl:
            ga = sum(g["goals_against"] or 0 for g in gl)
            opp_goals = box.line[other[side]]["goals"]
            if opp_goals is not None and ga != opp_goals:
                problems.append(f"{side} goalie GA {ga} != opponent goals {opp_goals}")
        n_pen = sum(p["side"] == side for p in box.penalties)
        if ln["penalties"] is not None and n_pen != ln["penalties"]:
            problems.append(
                f"{side} penalty summary has {n_pen} penalties, totals {ln['penalties']}"
            )
    return problems


# ----------------------------------------------------------------------------
# Season run
# ----------------------------------------------------------------------------


def team_names_for(box: Box, game) -> tuple[dict[str, str], str | None]:
    """side -> canonical team name (from the games table), plus a warning if the page's
    home/away disagrees with the schedule's."""
    ids = {"away": game.away_team_id, "home": game.home_team_id}

    def same(schedule_side: str, page_side: str) -> bool:
        page_id = box.page_ids.get(page_side)
        return pd.notna(ids[schedule_side]) and int(ids[schedule_side]) == page_id

    if (same("away", "home") or same("home", "away")) and not (
        same("away", "away") or same("home", "home")
    ):
        return {"away": game.home_team, "home": game.away_team}, "home/away swapped vs schedule"
    return {"away": game.away_team, "home": game.home_team}, None


def parse_season(season: Season, root: Path = Path(".")) -> dict[str, pd.DataFrame]:
    games = load_played_games(root, season)
    games = games.sort_values(["date", "game_id"]).reset_index(drop=True)
    tables: dict[str, list[dict]] = {
        k: [] for k in ("games", "team_stats", "skaters", "goalies", "goals", "penalties")
    }
    issues, missing = [], 0

    for g in games.itertuples():
        path = box_path(root, season, g.game_id)
        if not path.exists():
            missing += 1
            continue
        try:
            box = parse_box_html(path.read_text(encoding="utf-8"))
        except Exception as e:  # keep going; report at the end
            issues.append({"game_id": g.game_id, "issue": f"parse failed: {e}"})
            continue

        names, warn = team_names_for(box, g)
        if warn:
            issues.append({"game_id": g.game_id, "issue": warn})
        for problem in check_game(box, g):
            issues.append({"game_id": g.game_id, "issue": problem})

        base = {"season": season.label, "game_id": g.game_id, "date": g.date}
        opp = {"away": names["home"], "home": names["away"]}

        def team_cols(side: str) -> dict:
            return {"team": names[side], "opponent": opp[side], "is_home": side == "home"}

        winner_page = box.meta.pop("shootout_winner_page_name", None)
        so_winner = next((names[s] for s, n in box.page_names.items() if n == winner_page), None)
        tables["games"].append(
            {
                **base,
                "home_team": names["home"],
                "away_team": names["away"],
                "ot_periods": box.meta.get("ot_periods"),
                "shootout_winner": so_winner,
                "shootout_score": box.meta.get("shootout_score"),
                "game_type": box.meta.get("game_type"),
                "event": box.meta.get("event"),
                "venue": box.meta.get("venue"),
                "attendance": box.meta.get("attendance"),
                "referees": box.meta.get("referees"),
                "asst_referees": box.meta.get("asst_referees"),
            }
        )

        for side in SIDES:
            ln, opp_ln = box.line[side], box.line[opp_side(side)]
            gl = [x for x in box.goalies if x["side"] == side]
            en = sum(x["toi_seconds"] or 0 for x in gl if x["goalie"].upper() == "EMPTY NET")
            goals = [x for x in box.goals if x["side"] == side]
            tables["team_stats"].append(
                {
                    **base,
                    **team_cols(side),
                    **{k: ln[k] for k in ("goals", "goals_p1", "goals_p2", "goals_p3", "goals_ot")},
                    "goals_against": opp_ln["goals"],
                    **{k: ln[k] for k in ("shots", "shots_p1", "shots_p2", "shots_p3", "shots_ot")},
                    "shots_against": opp_ln["shots"],
                    "pp_goals": ln["pp_goals"],
                    "pp_opportunities": ln["pp_opportunities"],
                    "sh_goals": sum(x["is_sh"] for x in goals),
                    "en_goals": sum(x["is_en"] for x in goals),
                    "pp_goals_against": opp_ln["pp_goals"],
                    "times_shorthanded": opp_ln["pp_opportunities"],
                    "penalties": ln["penalties"],
                    "pim": ln["pim"],
                    "faceoffs_won": ln["faceoffs_won"],
                    "faceoffs_lost": ln["faceoffs_lost"],
                    "xg": ln["xg"],
                    "xg_against": opp_ln["xg"],
                    "empty_net_seconds": en,
                }
            )

        for s in box.skaters:
            tables["skaters"].append({**base, **team_cols(s["side"]), **without_side(s)})
        real = [x for x in box.goalies if x["goalie"].upper() != "EMPTY NET"]
        for x in real:
            starter = x is next(y for y in real if y["side"] == x["side"])
            tables["goalies"].append(
                {
                    **base,
                    **team_cols(x["side"]),
                    "goalie": x["goalie"],
                    "is_starter": starter,
                    "saves": x["saves"],
                    "goals_against": x["goals_against"],
                    "shots_against": (x["saves"] or 0) + (x["goals_against"] or 0),
                    "toi_seconds": x["toi_seconds"],
                }
            )
        for x in box.goals:
            tables["goals"].append({**base, **team_cols(x["side"]), **without_side(x)})
        for x in box.penalties:
            tables["penalties"].append({**base, **team_cols(x["side"]), **without_side(x)})

    out = {k: pd.DataFrame(v) for k, v in tables.items()}
    out_dir = root / "data/processed/chn"
    out_dir.mkdir(parents=True, exist_ok=True)
    for k, df in out.items():
        for col in df.columns:
            if df[col].dtype == float and df[col].dropna().apply(float.is_integer).all():
                df[col] = df[col].astype("Int64")  # keep counts as ints when some are missing
        df.to_csv(out_dir / f"box_{k}_{season.tag}.csv", index=False)
    issues_df = pd.DataFrame(issues, columns=["game_id", "issue"])
    issues_df.to_csv(out_dir / f"box_parse_issues_{season.tag}.csv", index=False)

    parsed = len(out["games"])
    print(
        f"Season {season.label}: parsed {parsed} of {len(games)} played games "
        f"({missing} box scores not downloaded yet) -> {out_dir}/box_*_{season.tag}.csv"
    )
    print("  " + ", ".join(f"{k}: {len(v)} rows" for k, v in out.items() if k != "games"))
    if missing:
        print(
            f"  Download the rest with: uv run python -m src.ingestion.chn_boxscore_downloader "
            f"--season {season.label}"
        )
    if issues:
        bad = issues_df["game_id"].nunique()
        print(f"  {bad} games have issues (listed in box_parse_issues_{season.tag}.csv), e.g.:")
        for r in issues[:8]:
            print(f"    {r['game_id']}: {r['issue']}")
    else:
        print("  All consistency checks passed.")
    return out


def opp_side(side: str) -> str:
    return "home" if side == "away" else "away"


def without_side(d: dict) -> dict:
    return {k: v for k, v in d.items() if k not in ("side", "order")}


def main() -> None:
    ap = argparse.ArgumentParser(description="Parse downloaded CHN box scores into CSV tables")
    ap.add_argument("--season", help="e.g. 2026-27 (default: the current season)")
    args = ap.parse_args()
    season = Season.parse(args.season) if args.season else Season.current()
    parse_season(season)


if __name__ == "__main__":
    main()
