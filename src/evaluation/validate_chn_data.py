"""
Validates the College Hockey News data in data/processed/chn/ and writes a report.

Every season found on disk is checked (all_games_<tag>.csv plus the box_*_<tag>.csv tables).
Checks come in two kinds:

  exact        numbers that must agree, e.g. the box score's final score vs the schedule page,
               period goals vs final goals, the goal list vs the score. A mismatch is either a
               known error on the CHN page itself (explained in the report) or unexplained.
  approximate  totals CHN records separately for players and teams (e.g. skater shots vs team
               shots). Small gaps are normal on the source; the report gives the match rates.

Usage (from project root):
  uv run python -m src.evaluation.validate_chn_data
  uv run python -m src.evaluation.validate_chn_data --strict   # exit 1 on unexplained mismatches

Outputs:
  docs/data_validation.md                                 report (linked from the README)
  data/processed/validation/chn_validation_failures.csv   every mismatch, with its cause
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

DATA_DIR = Path("data/processed/chn")
REPORT_PATH = Path("docs/data_validation.md")
FAILURES_PATH = Path("data/processed/validation/chn_validation_failures.csv")
TABLES = (
    "all_games",
    "box_games",
    "box_team_stats",
    "box_skaters",
    "box_goalies",
    "box_goals",
    "box_penalties",
)
KEY = ["game_id", "team"]
UNKNOWN_SCORER = "Uknown"  # CHN's own spelling, used when no player was credited with a goal

# Mismatches checked by hand against the CHN page: the page itself is inconsistent.
# (game_id, check name) -> what the page shows
MISSING = "Every played game has a box score"
SUMMARY = "Scoring summary matches the score"
PEN_COUNT = "Penalty list matches the penalty count"
PEN_MIN = "Penalty minutes match the PIM total"
GOALIE = "Goalie goals against add up"
RANGES = "Values are in plausible ranges"
PP_1_0 = "CHN's page shows 1 PP goal on 0 PP chances"
REVIEWED_SOURCE_ERRORS = {
    ("2021-11-23_419_62", MISSING): "CHN's box score link for this game returns HTTP 404",
    ("2023-11-28_419_62", MISSING): "CHN's box score link for this game returns HTTP 404",
    ("2022-10-08_422_62", SUMMARY): "CHN's scoring summary for this game is empty",
    ("2021-10-30_32_44", SUMMARY): "CHN's scoring summary leaves out Michigan State's goal",
    ("2021-11-13_24_7", SUMMARY): "CHN's scoring summary lists 5 Bemidji State goals; the "
    "score says 4",
    ("2021-11-27_23_38", SUMMARY): "CHN's scoring summary lists 3 Holy Cross goals; the score "
    "says 2",
    ("2021-10-15_17_44", PEN_COUNT): "CHN lists 4 of UConn's 5 penalties",
    ("2021-10-15_17_44", PEN_MIN): "CHN lists 4 of UConn's 5 penalties",
    ("2021-10-16_17_44", PEN_COUNT): "CHN lists 5 of UConn's 6 penalties",
    ("2021-10-16_17_44", PEN_MIN): "CHN lists 5 of UConn's 6 penalties",
    ("2021-10-30_32_44", PEN_COUNT): "CHN lists 3 of Michigan State's 4 penalties",
    ("2021-10-30_32_44", PEN_MIN): "CHN lists 3 of Michigan State's 4 penalties",
    ("2021-12-10_10_9", PEN_COUNT): "CHN lists 3 of Boston College's 4 penalties",
    ("2021-12-10_10_9", PEN_MIN): "CHN lists 3 of Boston College's 4 penalties",
    ("2021-11-06_34_58", GOALIE): "CHN labels Minnesota's goalie 'EMPTY NET' (34 saves in 58:02)",
    ("2021-12-04_31_34", GOALIE): "CHN labels one of Minnesota's goalies 'EMPTY NET' (21 saves "
    "in 33:23)",
    ("2022-01-15_11_7", GOALIE): "CHN tags Bowling Green's OT goal empty-net but charges it to "
    "Bemidji State's goalie",
    ("2022-10-13_23_51", GOALIE): "CHN tags a Sacred Heart goal empty-net but charges it to "
    "Holy Cross's goalie",
    ("2021-10-15_17_44", RANGES): PP_1_0,
    ("2021-10-30_32_44", RANGES): PP_1_0,
    ("2022-10-08_422_62", RANGES): PP_1_0,
}


# ----------------------------------------------------------------------------
# Loading
# ----------------------------------------------------------------------------


def load(root: Path) -> tuple[dict[str, pd.DataFrame], list[str]]:
    folder = root / DATA_DIR
    tags = sorted(p.stem.removeprefix("all_games_") for p in folder.glob("all_games_*.csv"))
    if not tags:
        raise SystemExit(f"No all_games_*.csv in {folder}. Run the scrapers first (see README).")
    data = {}
    for name in TABLES:
        frames = [
            pd.read_csv(folder / f"{name}_{tag}.csv")
            for tag in tags
            if (folder / f"{name}_{tag}.csv").exists()
        ]
        data[name] = pd.concat(frames, ignore_index=True)
    return data, tags


def d1_sides(games: pd.DataFrame) -> pd.MultiIndex:
    """(game_id, team) for D-I teams. CHN records only the score and shots for a non-D-I team."""
    sides = [
        games.loc[games[f"{s}_team_id"].notna(), ["game_id", f"{s}_team"]].set_axis(KEY, axis=1)
        for s in ("home", "away")
    ]
    return pd.MultiIndex.from_frame(pd.concat(sides))


def d1_games(games: pd.DataFrame) -> set[str]:
    """Games between two D-I teams (both sides fully recorded)."""
    both = games["home_team_id"].notna() & games["away_team_id"].notna()
    return set(games.loc[both, "game_id"])


# ----------------------------------------------------------------------------
# Check results
# ----------------------------------------------------------------------------


@dataclass
class Exact:
    group: str
    name: str
    shows: str
    checked: int
    failures: pd.DataFrame  # game_id, team, detail, cause ("" = unexplained)
    note: str = ""  # shown under the check's table in the report

    def __post_init__(self) -> None:
        f = self.failures
        reviewed = f["game_id"].map(lambda g: REVIEWED_SOURCE_ERRORS.get((g, self.name), ""))
        f["cause"] = f["cause"].where(f["cause"] != "", reviewed)

    @property
    def explained(self) -> int:
        return int((self.failures["cause"] != "").sum())

    @property
    def unexplained(self) -> int:
        return len(self.failures) - self.explained


@dataclass
class Approx:
    name: str
    why: str
    checked: int
    exact: int
    within: int
    tolerance: str


def fail(rows: pd.DataFrame, detail, cause="") -> pd.DataFrame:
    rows = rows.reset_index()
    return pd.DataFrame(
        {
            "game_id": rows["game_id"].to_numpy(),
            "team": rows["team"].to_numpy() if "team" in rows else "",
            "detail": list(detail) if not isinstance(detail, str) else detail,
            "cause": list(cause) if not isinstance(cause, str) else cause,
        }
    )


def no_failures() -> pd.DataFrame:
    return pd.DataFrame(columns=["game_id", "team", "detail", "cause"])


def opponent_value(df: pd.DataFrame, col: str) -> pd.Series:
    """For a frame indexed by (game_id, team) with an 'opponent' column: the opponent's col."""
    other = df.reset_index().set_index(["game_id", "opponent"])[col].rename_axis(KEY)
    return other.reindex(df.index)


# ----------------------------------------------------------------------------
# Exact checks
# ----------------------------------------------------------------------------


def exact_checks(data: dict[str, pd.DataFrame]) -> list[Exact]:
    ag, bg, ts = data["all_games"], data["box_games"], data["box_team_stats"]
    sk, gl, go, pe = (data[k] for k in ("box_skaters", "box_goalies", "box_goals", "box_penalties"))
    d1 = d1_sides(ag)
    t = ts.set_index(KEY)
    t_d1 = t[t.index.isin(d1)]
    checks = []

    # --- Completeness and structure -------------------------------------------------------
    played = ag[ag["is_played"].astype(bool)]
    missing = played[~played["game_id"].isin(bg["game_id"])]
    checks.append(
        Exact(
            "Completeness",
            MISSING,
            "No games are silently missing from the box score tables",
            len(played),
            fail(
                missing.assign(team=""),
                "no box score",
            ),
        )
    )

    dup_rows, n_rows = [], 0
    for table, key in (
        ("all_games", ["game_id"]),
        ("box_games", ["game_id"]),
        ("box_team_stats", KEY),
        ("box_skaters", [*KEY, "player"]),
        ("box_goalies", [*KEY, "goalie"]),
    ):
        df = data[table]
        n_rows += len(df)
        dups = df[df.duplicated(key, keep=False)]
        if "team" not in dups:
            dups = dups.assign(team="")
        dup_rows.append(fail(dups, f"duplicate row in {table}"))
    checks.append(
        Exact(
            "Completeness",
            "No duplicate rows",
            "Each game, team-game, skater-game and goalie-game appears once",
            n_rows,
            pd.concat(dup_rows, ignore_index=True),
        )
    )

    teams = ts.groupby("game_id").agg(
        n=("team", "size"), homes=("is_home", "sum"), names=("team", frozenset)
    )
    teams = teams.join(ag.set_index("game_id")[["home_team", "away_team"]])
    expected = [frozenset(p) for p in zip(teams["home_team"], teams["away_team"])]
    bad = teams[(teams["n"] != 2) | (teams["homes"] != 1) | (teams["names"] != expected)]
    checks.append(
        Exact(
            "Completeness",
            "Box score teams match the schedule",
            "Each box score has one home and one away row, for the two teams on the schedule",
            len(teams),
            fail(bad.assign(team=""), "teams/home-away differ from all_games"),
        )
    )

    # --- Box score vs schedule page (two different CHN pages) ------------------------------
    sched = ag.set_index("game_id")
    exp_goals = [
        sched.at[g, "home_goals" if h else "away_goals"]
        for g, h in zip(ts["game_id"], ts["is_home"])
    ]
    score = ts.assign(expected=exp_goals)
    bad = score[score["goals"] != score["expected"]]
    checks.append(
        Exact(
            "Box score vs schedule",
            "Final score matches the schedule page",
            "The box score and each team's schedule page report the same score",
            len(score),
            fail(bad, [f"box score {g}, schedule {e}" for g, e in zip(bad.goals, bad.expected)]),
        )
    )

    ot = bg.set_index("game_id")[["ot_periods"]].join(sched["is_overtime"])
    bad = ot[(ot["ot_periods"] > 0) != ot["is_overtime"].astype(bool)]
    checks.append(
        Exact(
            "Box score vs schedule",
            "Overtime games match the schedule page",
            "Games the schedule marks OT/SO are exactly those with OT periods in the box score",
            len(ot),
            fail(bad.assign(team=""), "OT flag differs"),
        )
    )

    # --- Within the box score --------------------------------------------------------------
    for kind in ("goals", "shots"):
        parts = t_d1[[f"{kind}_p1", f"{kind}_p2", f"{kind}_p3"]].sum(axis=1)
        total = parts + t_d1[f"{kind}_ot"].fillna(0)
        bad = t_d1[total != t_d1[kind]]
        checks.append(
            Exact(
                "Within the box score",
                f"Period {kind} add up to the total",
                f"1st + 2nd + 3rd + OT {kind} = final {kind} (line score)",
                len(t_d1),
                fail(
                    bad,
                    [f"periods sum to {s}, total {v}" for s, v in zip(total[bad.index], bad[kind])],
                ),
            )
        )

    listed = go.groupby(KEY).size().reindex(t_d1.index, fill_value=0)
    bad = t_d1[listed != t_d1["goals"]]
    checks.append(
        Exact(
            "Within the box score",
            SUMMARY,
            "One goal in box_goals for every goal in the final score",
            len(t_d1),
            fail(
                bad, [f"{n} goals listed, score {g}" for n, g in zip(listed[bad.index], bad.goals)]
            ),
        )
    )

    sk_goals = sk.groupby(KEY)["goals"].sum()
    unknown = go[go["scorer"] == UNKNOWN_SCORER].groupby(KEY).size()
    has_sk = t_d1[t_d1.index.isin(sk_goals.index)]
    sg = sk_goals.reindex(has_sk.index)
    unk = unknown.reindex(has_sk.index, fill_value=0)
    bad = has_sk[sg != has_sk["goals"]]
    short = (bad["goals"] - sg[bad.index]).astype(int)
    causes = [
        f"CHN credited {n} goal(s) to '{UNKNOWN_SCORER}' (no player named)" if s == n else ""
        for s, n in zip(short, unk[bad.index])
    ]
    checks.append(
        Exact(
            "Within the box score",
            "Skater goals add up to the score",
            "Summing every skater's goals gives the team's final score",
            len(has_sk),
            fail(
                bad, [f"skaters {s}, score {g}" for s, g in zip(sg[bad.index], bad.goals)], causes
            ),
        )
    )

    bad = sk[sk["points"] != sk["goals"] + sk["assists"]]
    checks.append(
        Exact(
            "Within the box score",
            "Points = goals + assists",
            "Every skater line is internally consistent",
            len(sk),
            fail(bad, "points != goals + assists"),
        )
    )

    is_gm = pe["infraction"].str.contains("Misconduct|Disqualification", case=False, na=False)
    pen = (
        pe.assign(gm=is_gm)
        .groupby(KEY)
        .agg(count=("player", "size"), minutes=("minutes", "sum"), gm=("gm", "sum"))
    )
    pen = pen.reindex(t_d1.index, fill_value=0)
    for col, total_col, label in (
        ("count", "penalties", PEN_COUNT),
        ("minutes", "pim", PEN_MIN),
    ):
        bad = t_d1[pen[col] != t_d1[total_col]]
        listed = pen.loc[bad.index, col]
        causes = [""] * len(bad)
        if col == "minutes":  # CHN's PIM total counts a game misconduct as 0, 10 or 20 minutes
            gap = (bad[total_col] - listed).abs()
            causes = [
                "CHN's PIM total counts game misconducts differently from its penalty list"
                if g % 10 == 0 and 0 < g <= 20 * n
                else ""
                for g, n in zip(gap, pen.loc[bad.index, "gm"])
            ]
        checks.append(
            Exact(
                "Within the box score",
                label,
                f"Summing box_penalties gives the team's {total_col} total",
                len(t_d1),
                fail(bad, [f"list {a}, total {b}" for a, b in zip(listed, bad[total_col])], causes),
            )
        )

    both_d1 = t.index.get_level_values("game_id").isin(d1_games(ag))
    ga = gl.groupby(KEY)["goals_against"].sum()
    rows = t[t.index.isin(ga.index) & both_d1]
    total = ga.reindex(rows.index) + opponent_value(t, "en_goals").reindex(rows.index)
    bad = rows[total != rows["goals_against"]]
    checks.append(
        Exact(
            "Within the box score",
            GOALIE,
            "Goalies' goals against + opponent's empty-net goals = goals allowed",
            len(rows),
            fail(
                bad,
                [
                    f"goalies+EN {a}, allowed {b}"
                    for a, b in zip(total[bad.index], bad.goals_against)
                ],
            ),
        )
    )

    rows = t[t["faceoffs_won"].notna() & both_d1]
    opp_lost = opponent_value(t, "faceoffs_lost").reindex(rows.index)
    bad = rows[rows["faceoffs_won"] != opp_lost]
    checks.append(
        Exact(
            "Within the box score",
            "Faceoffs won = opponent's faceoffs lost",
            "Both teams' faceoff lines describe the same draws",
            len(rows),
            fail(
                bad,
                [
                    f"won {a}, opponent lost {b}"
                    for a, b in zip(bad.faceoffs_won, opp_lost[bad.index])
                ],
            ),
        )
    )

    so = (
        bg[bg["shootout_winner"].notna()]
        .set_index("game_id")
        .join(sched[["home_goals", "away_goals"]], rsuffix="_sched")
    )
    winners = [
        w in (h, a) for w, h, a in zip(so["shootout_winner"], so["home_team"], so["away_team"])
    ]
    bad = so[(so["home_goals"] != so["away_goals"]) | ~pd.Series(winners, index=so.index)]
    checks.append(
        Exact(
            "Within the box score",
            "Shootouts only follow tied games",
            "Every shootout game is tied after OT, and the winner is one of the two teams",
            len(so),
            fail(bad.assign(team=""), "shootout on a non-tied game, or unknown winner"),
        )
    )

    checks.append(range_check(data, d1))
    return checks


def range_check(data: dict[str, pd.DataFrame], d1: pd.MultiIndex) -> Exact:
    """Values that would mean a misread page: negative counts, impossible times, and so on."""
    ag, bg, ts = data["all_games"], data["box_games"], data["box_team_stats"]
    sk, gl, go, pe = (data[k] for k in ("box_skaters", "box_goalies", "box_goals", "box_penalties"))
    t_d1 = ts[pd.MultiIndex.from_frame(ts[KEY]).isin(d1)]
    rules = [
        (t_d1, "team goals 0-20", ~t_d1["goals"].between(0, 20)),
        (t_d1, "D-I team shots 5-100", ~t_d1["shots"].between(5, 100)),
        (t_d1, "PP goals <= PP chances", t_d1["pp_goals"] > t_d1["pp_opportunities"]),
        (sk, "skater stats >= 0", (sk[["goals", "assists", "shots", "pim"]] < 0).any(axis=1)),
        (sk, "skater TOI <= 75 min", sk["toi_seconds"] > 75 * 60),
        (gl, "goalie saves and GA >= 0", (gl[["saves", "goals_against"]] < 0).any(axis=1)),
        (gl, "goalie TOI <= 150 min", gl["toi_seconds"] > 150 * 60),
        (go, "goal period 1-4 (4 = OT)", ~go["period"].between(1, 4)),
        (go, "goal time 0:00-20:00", ~go["time_seconds"].between(0, 1200)),
        (pe, "penalty period 1-4 (4 = OT)", ~pe["period"].between(1, 4)),
        (pe, "penalty minutes 0-20", ~pe["minutes"].between(0, 20)),
        (bg, "attendance <= 110,000", bg["attendance"] > 110_000),
    ]
    season_start = ag["season"].str[:4].astype(int)
    dates = pd.to_datetime(ag["date"])
    in_season = (dates >= pd.to_datetime(season_start.astype(str) + "-09-01")) & (
        dates <= pd.to_datetime((season_start + 1).astype(str) + "-04-30")
    )
    rules.append((ag, "game date Sep 1 - Apr 30 of its season", ~in_season))
    out = [
        fail(df[mask] if "team" in df else df[mask].assign(team=""), f"out of range: {label}")
        for df, label, mask in rules
        if mask.any()
    ]
    return Exact(
        "Within the box score",
        RANGES,
        "No impossible values (negative counts, times past 20:00, ...); rules below",
        sum(len(df) for df, _, _ in rules),
        pd.concat(out, ignore_index=True) if out else no_failures(),
        note="Plausible-range rules: " + "; ".join(label for _, label, _ in rules) + ".",
    )


# ----------------------------------------------------------------------------
# Approximate checks
# ----------------------------------------------------------------------------


def approximate_checks(data: dict[str, pd.DataFrame]) -> list[Approx]:
    ag, ts, sk, gl, go, pe = (
        data[k]
        for k in (
            "all_games",
            "box_team_stats",
            "box_skaters",
            "box_goalies",
            "box_goals",
            "box_penalties",
        )
    )
    t = ts.set_index(KEY)
    t = t[t.index.isin(d1_sides(ag))]
    s = sk.groupby(KEY).agg(
        shots=("shots", "sum"),
        assists=("assists", "sum"),
        fow=("faceoffs_won", "sum"),
        pim_sk=("pim", "sum"),
    )
    # penalties to players not in the skater table: bench minors and goalies
    roster = pd.MultiIndex.from_frame(sk[[*KEY, "player"]])
    off_roster = pe[~pd.MultiIndex.from_frame(pe[[*KEY, "player"]]).isin(roster)]
    s["pim_other"] = off_roster.groupby(KEY)["minutes"].sum().reindex(s.index, fill_value=0)
    go = go.assign(n_assists=go["assist1"].notna().astype(int) + go["assist2"].notna().astype(int))
    g = go.groupby(KEY).agg(assists=("n_assists", "sum"), pp=("is_pp", "sum"))
    v = gl.groupby(KEY)["saves"].sum()

    def approx(name, why, a, b, tol, tol_text) -> Approx:
        both = a.notna() & b.notna()
        diff = (a[both] - b[both]).abs()
        return Approx(
            name, why, int(both.sum()), int((diff == 0).sum()), int((diff <= tol).sum()), tol_text
        )

    rows = t.join(s, rsuffix="_sk")
    out = [
        approx(
            "Skater shots = team shots",
            "CHN's team shot total sometimes counts shots it didn't credit to a player",
            rows["shots_sk"],
            rows["shots"],
            2,
            "within 2",
        ),
        approx(
            "Skater assists = assists in the scoring summary",
            "Assists are occasionally corrected in one place and not the other",
            rows["assists"],
            g["assists"].reindex(rows.index, fill_value=0),
            1,
            "within 1",
        ),
    ]
    out.append(
        approx(
            "Skater PIM + bench and goalie PIM = team PIM",
            "Player PIM and team PIM count misconducts and coincidental minors differently",
            rows["pim_sk"] + rows["pim_other"],
            rows["pim"],
            2,
            "within 2",
        )
    )
    fo = rows[rows["faceoffs_won"].notna()]
    out.append(
        approx(
            "Skater faceoffs won = team faceoffs won",
            "Some faceoffs aren't credited to a player",
            fo["fow"],
            fo["faceoffs_won"],
            2,
            "within 2",
        )
    )
    rows = t[t.index.isin(v.index)]
    out.append(
        approx(
            "Goalie saves = shots against - goals against",
            "Goalie lines and the team shot total are recorded separately",
            v.reindex(rows.index),
            rows["shots_against"] - rows["goals_against"],
            2,
            "within 2",
        )
    )
    rows = t[t.index.isin(g.index) | (t["goals"] == 0)]
    out.append(
        approx(
            "Power-play goals = goals tagged PP in the scoring summary",
            "Goal strength tags are often missing in 2021-22 and 2022-23; features use the "
            "team PP totals, which are exact",
            rows["pp_goals"],
            g["pp"].reindex(rows.index, fill_value=0),
            0,
            "exact only",
        )
    )
    return out


# ----------------------------------------------------------------------------
# Coverage + report
# ----------------------------------------------------------------------------


def coverage(data: dict[str, pd.DataFrame]) -> pd.DataFrame:
    ag, bg, ts, sk, go, pe = (
        data[k]
        for k in (
            "all_games",
            "box_games",
            "box_team_stats",
            "box_skaters",
            "box_goals",
            "box_penalties",
        )
    )
    rows = []
    for season, g in ag.groupby("season"):
        ids = g["game_id"]
        d1 = set(g.loc[g["home_team_id"].notna(), "home_team"]) | set(
            g.loc[g["away_team_id"].notna(), "away_team"]
        )
        s, t = sk[sk["game_id"].isin(ids)], ts[ts["game_id"].isin(ids)]
        rows.append(
            {
                "Season": season,
                "D-I teams": len(d1),
                "Games played": int(g["is_played"].astype(bool).sum()),
                "Box scores": int(bg["game_id"].isin(ids).sum()),
                "Skater rows": len(s),
                "Goals": int(go["game_id"].isin(ids).sum()),
                "Penalties": int(pe["game_id"].isin(ids).sum()),
                "TOI": f"{s['toi_seconds'].notna().mean():.0%}",
                "xG": f"{t['xg'].notna().mean():.0%}",
            }
        )
    return pd.DataFrame(rows)


def md_table(df: pd.DataFrame) -> str:
    def cell(v):
        return f"{v:,}" if isinstance(v, int) else str(v)

    head = "| " + " | ".join(df.columns) + " |"
    sep = "|" + "|".join("---" for _ in df.columns) + "|"
    body = ["| " + " | ".join(cell(v) for v in row) + " |" for row in df.itertuples(index=False)]
    return "\n".join([head, sep, *body])


def status(c: Exact) -> str:
    if c.unexplained:
        return f"**{c.unexplained} unexplained**"
    return "Pass" if not len(c.failures) else "Pass (source errors only)"


def write_report(
    root: Path, tags: list[str], data: dict, exact: list[Exact], approx: list[Approx]
) -> str:
    ag = data["all_games"].set_index("game_id")
    cov = coverage(data)
    n_games = int(cov["Box scores"].sum())
    clean = sum(c.unexplained == 0 for c in exact)
    explained = sum(c.explained for c in exact)
    unexplained = sum(c.unexplained for c in exact)
    seasons = f"{cov['Season'].iloc[0]} to {cov['Season'].iloc[-1]}"

    lines = [
        "# Data validation: College Hockey News",
        "",
        "Generated by "
        "[`src/evaluation/validate_chn_data.py`](../src/evaluation/validate_chn_data.py) "
        "from the CSVs in [`data/processed/chn/`](../data/processed/chn/). "
        "To reproduce it: `uv run python -m src.evaluation.validate_chn_data`. CI re-runs it on "
        "every push and fails if this file is out of date, so it always matches the committed "
        "data.",
        "",
        f"Covers {len(tags)} seasons ({seasons}), {n_games:,} box scores.",
        "",
        f"**{clean} of {len(exact)} exact checks have no unexplained mismatches.** "
        + (
            f"{explained} rows differ because the College Hockey News page itself is "
            "inconsistent; each is listed under [Source errors](#source-errors) with a link to "
            "the page. "
            if explained
            else ""
        )
        + (
            f"{unexplained} rows are unexplained and listed under "
            "[Unexplained mismatches](#unexplained-mismatches)."
            if unexplained
            else "No row is unexplained."
        ),
        "",
        "## Data covered",
        "",
        "TOI (time on ice) and xG (expected goals) only exist on CHN from 2024-25 on.",
        "",
        md_table(cov),
        "",
        "## Exact checks",
        "",
        "These numbers must agree. The schedule checks compare two different CHN pages (each "
        "team's schedule page and the game's box score), so they catch both scraping and "
        "parsing errors. Non-D-I opponents are left out of the per-player checks: CHN records "
        "only their score and shots.",
        "",
    ]
    table = pd.DataFrame(
        [
            {
                "Check": c.name,
                "What it shows": c.shows,
                "Rows checked": c.checked,
                "Mismatches": len(c.failures),
                "Source errors": c.explained,
                "Result": status(c),
            }
            for c in exact
        ]
    )
    for group in dict.fromkeys(c.group for c in exact):
        lines += [f"### {group}", "", md_table(table[[c.group == group for c in exact]]), ""]
        lines += [f"{c.note}\n" for c in exact if c.group == group and c.note]

    lines += [
        "## Approximate checks",
        "",
        "CHN records some totals twice, once per player and once per team. They usually agree; "
        "when they don't, the gap is small. These show the match rates and aren't pass/fail.",
        "",
        md_table(
            pd.DataFrame(
                [
                    {
                        "Check": a.name,
                        "Rows": a.checked,
                        "Exact match": f"{a.exact / a.checked:.1%}",
                        "Close": f"{a.within / a.checked:.1%} ({a.tolerance})",
                        "Why they can differ": a.why,
                    }
                    for a in approx
                ]
            )
        ),
        "",
    ]

    def listing(rows: pd.DataFrame, with_cause: bool) -> list[str]:
        out = []
        for r in rows.itertuples():
            game = ag.loc[r.game_id] if r.game_id in ag.index else None
            title = (
                f"{game['date']} {game['away_team']} at {game['home_team']}"
                if game is not None
                else r.game_id
            )
            link = f"[{title}]({game['box_score_url']})" if game is not None else title
            team = f" ({r.team})" if isinstance(r.team, str) and r.team else ""
            reason = f": {r.cause}" if with_cause else ""
            out.append(f"- {link}{team}: {r.detail}{reason}")
        return out

    lines += ["## Source errors", ""]
    if explained:
        lines += [
            "Mismatches caused by the CHN page itself. Follow a link to see it on the source.",
            "",
        ]
        for c in exact:
            rows = c.failures[c.failures["cause"] != ""]
            if len(rows):
                lines += [f"**{c.name}** ({len(rows)})", "", *listing(rows, True), ""]
    else:
        lines += ["None.", ""]

    lines += ["## Unexplained mismatches", ""]
    if unexplained:
        lines += ["Not yet traced to a cause.", ""]
        for c in exact:
            rows = c.failures[c.failures["cause"] == ""]
            if len(rows):
                lines += [f"**{c.name}** ({len(rows)})", "", *listing(rows, False), ""]
    else:
        lines += ["None.", ""]

    text = "\n".join(lines)
    path = root / REPORT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return text


def main() -> None:
    ap = argparse.ArgumentParser(description="Validate the processed CHN data and write a report")
    ap.add_argument("--strict", action="store_true", help="exit 1 if anything is unexplained")
    args = ap.parse_args()

    root = Path(".")
    data, tags = load(root)
    exact = exact_checks(data)
    approx = approximate_checks(data)
    write_report(root, tags, data, exact, approx)

    failures = pd.concat(
        [c.failures.assign(check=c.name) for c in exact if len(c.failures)], ignore_index=True
    )
    out = root / FAILURES_PATH
    out.parent.mkdir(parents=True, exist_ok=True)
    failures.reindex(columns=["check", "game_id", "team", "detail", "cause"]).to_csv(
        out, index=False
    )

    width = max(len(c.name) for c in exact)
    for c in exact:
        print(f"{c.name:<{width}}  {c.checked:>7,} checked  {status(c).replace('**', '')}")
    unexplained = sum(c.unexplained for c in exact)
    print(f"\nReport: {REPORT_PATH}   Mismatch list: {FAILURES_PATH}")
    if args.strict and unexplained:
        sys.exit(1)


if __name__ == "__main__":
    main()
