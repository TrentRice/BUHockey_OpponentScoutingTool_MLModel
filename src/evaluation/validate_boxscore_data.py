"""
Validates parsed BU men's hockey box score data.

This script checks that game/team/player stats agree with each other.

Usage:
    uv run python -m src.evaluation.validate_boxscore_data --season 2025-26

Inputs:
    data/processed/boxscores/game_results_2025_26.csv
    data/processed/boxscores/team_game_stats_2025_26.csv
    data/processed/boxscores/player_game_stats_2025_26.csv

Output:
    data/processed/validation/boxscore_validation_2025_26.csv
"""

import argparse
import re
from pathlib import Path

import pandas as pd

PROCESSED_BOXSCORE_DIR = Path("data/processed/boxscores")
VALIDATION_DIR = Path("data/processed/validation")


def load_processed_boxscore_data(
    season: str,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    season_key = season.replace("-", "_")

    game_results_path = PROCESSED_BOXSCORE_DIR / f"game_results_{season_key}.csv"
    team_stats_path = PROCESSED_BOXSCORE_DIR / f"team_game_stats_{season_key}.csv"
    player_stats_path = PROCESSED_BOXSCORE_DIR / f"player_game_stats_{season_key}.csv"

    missing = [
        path
        for path in [game_results_path, team_stats_path, player_stats_path]
        if not path.exists()
    ]

    if missing:
        missing_text = "\n".join(str(path) for path in missing)
        raise FileNotFoundError(
            f"Missing required processed files:\n{missing_text}\nRun parse_bu_boxscores.py first."
        )

    game_df = pd.read_csv(game_results_path)
    team_df = pd.read_csv(team_stats_path)
    player_df = pd.read_csv(player_stats_path)

    return game_df, team_df, player_df


def parse_penalty_minutes_from_penalties(value) -> int:
    """
    Parses player penalty string like:
        '1-2'
        '2-4'
        '5-10'
        NaN / None / '-'

    In the skater table, Pen appears to be:
        penalties-minutes

    Returns the minutes component.
    """
    if pd.isna(value):
        return 0

    value = str(value).strip()

    if not value or value == "-":
        return 0

    match = re.fullmatch(r"(\d+)-(\d+)", value)
    if not match:
        return 0

    return int(match.group(2))


def safe_int(value) -> int | None:
    if pd.isna(value):
        return None

    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def compare_ints(actual, expected) -> tuple[bool, int | None]:
    actual_int = safe_int(actual)
    expected_int = safe_int(expected)

    if actual_int is None or expected_int is None:
        return False, None

    return actual_int == expected_int, actual_int - expected_int


def validate_one_game(
    game_id: str,
    game_row: pd.Series,
    team_row: pd.Series,
    player_game_df: pd.DataFrame,
) -> dict:
    bu_players = player_game_df[player_game_df["team_type"] == "bu"]
    opp_players = player_game_df[player_game_df["team_type"] == "opponent"]

    bu_player_goals = bu_players["goals"].sum()
    opp_player_goals = opp_players["goals"].sum()

    bu_player_assists = bu_players["assists"].sum()
    opp_player_assists = opp_players["assists"].sum()

    bu_player_points = bu_players["points"].sum()
    opp_player_points = opp_players["points"].sum()

    bu_player_shots = bu_players["shots_total"].sum()
    opp_player_shots = opp_players["shots_total"].sum()

    bu_player_blocks = bu_players["blocks"].sum()
    opp_player_blocks = opp_players["blocks"].sum()

    bu_player_penalty_minutes = (
        bu_players["penalties"].apply(parse_penalty_minutes_from_penalties).sum()
    )

    opp_player_penalty_minutes = (
        opp_players["penalties"].apply(parse_penalty_minutes_from_penalties).sum()
    )

    bu_goals_match, bu_goals_diff = compare_ints(
        bu_player_goals,
        game_row.get("bu_score"),
    )

    opp_goals_match, opp_goals_diff = compare_ints(
        opp_player_goals,
        game_row.get("opponent_score"),
    )

    bu_shots_match, bu_shots_diff = compare_ints(
        bu_player_shots,
        team_row.get("bu_shots"),
    )

    opp_shots_match, opp_shots_diff = compare_ints(
        opp_player_shots,
        team_row.get("opponent_shots"),
    )

    bu_blocks_match, bu_blocks_diff = compare_ints(
        bu_player_blocks,
        team_row.get("bu_blocks"),
    )

    opp_blocks_match, opp_blocks_diff = compare_ints(
        opp_player_blocks,
        team_row.get("opponent_blocks"),
    )

    bu_pen_min_match, bu_pen_min_diff = compare_ints(
        bu_player_penalty_minutes,
        team_row.get("bu_penalty_minutes"),
    )

    opp_pen_min_match, opp_pen_min_diff = compare_ints(
        opp_player_penalty_minutes,
        team_row.get("opponent_penalty_minutes"),
    )

    core_checks = {
        "bu_goals_match": bu_goals_match,
        "opponent_goals_match": opp_goals_match,
        "bu_shots_match": bu_shots_match,
        "opponent_shots_match": opp_shots_match,
        "bu_blocks_match": bu_blocks_match,
        "opponent_blocks_match": opp_blocks_match,
    }

    warning_checks = {
        "bu_penalty_minutes_match": bu_pen_min_match,
        "opponent_penalty_minutes_match": opp_pen_min_match,
    }

    failed_core_checks = [name for name, passed in core_checks.items() if not passed]
    failed_warning_checks = [name for name, passed in warning_checks.items() if not passed]

    return {
        "game_id": game_id,
        "date": game_row.get("date"),
        "opponent_clean": game_row.get("opponent_clean"),
        "result": game_row.get("result"),
        "bu_score": game_row.get("bu_score"),
        "opponent_score": game_row.get("opponent_score"),
        "bu_player_count": len(bu_players),
        "opponent_player_count": len(opp_players),
        "bu_player_goals": bu_player_goals,
        "opponent_player_goals": opp_player_goals,
        "bu_goals_match": bu_goals_match,
        "opponent_goals_match": opp_goals_match,
        "bu_goals_diff": bu_goals_diff,
        "opponent_goals_diff": opp_goals_diff,
        "bu_player_assists": bu_player_assists,
        "opponent_player_assists": opp_player_assists,
        "bu_player_points": bu_player_points,
        "opponent_player_points": opp_player_points,
        "bu_team_shots": team_row.get("bu_shots"),
        "opponent_team_shots": team_row.get("opponent_shots"),
        "bu_player_shots": bu_player_shots,
        "opponent_player_shots": opp_player_shots,
        "bu_shots_match": bu_shots_match,
        "opponent_shots_match": opp_shots_match,
        "bu_shots_diff": bu_shots_diff,
        "opponent_shots_diff": opp_shots_diff,
        "bu_team_blocks": team_row.get("bu_blocks"),
        "opponent_team_blocks": team_row.get("opponent_blocks"),
        "bu_player_blocks": bu_player_blocks,
        "opponent_player_blocks": opp_player_blocks,
        "bu_blocks_match": bu_blocks_match,
        "opponent_blocks_match": opp_blocks_match,
        "bu_blocks_diff": bu_blocks_diff,
        "opponent_blocks_diff": opp_blocks_diff,
        "bu_team_penalty_minutes": team_row.get("bu_penalty_minutes"),
        "opponent_team_penalty_minutes": team_row.get("opponent_penalty_minutes"),
        "bu_player_penalty_minutes": bu_player_penalty_minutes,
        "opponent_player_penalty_minutes": opp_player_penalty_minutes,
        "bu_penalty_minutes_match": bu_pen_min_match,
        "opponent_penalty_minutes_match": opp_pen_min_match,
        "bu_penalty_minutes_diff": bu_pen_min_diff,
        "opponent_penalty_minutes_diff": opp_pen_min_diff,
        "all_core_checks_passed": len(failed_core_checks) == 0,
        "failed_checks": "; ".join(failed_core_checks),
        "warning_checks": "; ".join(failed_warning_checks),
    }


def validate_required_fields(
    game_df: pd.DataFrame,
    team_df: pd.DataFrame,
    player_df: pd.DataFrame,
) -> list[dict]:
    issues = []

    required_game_cols = [
        "game_id",
        "date",
        "opponent_clean",
        "bu_score",
        "opponent_score",
        "result",
    ]

    required_team_cols = [
        "game_id",
        "bu_shots",
        "opponent_shots",
        "bu_penalty_minutes",
        "opponent_penalty_minutes",
    ]

    required_player_cols = [
        "game_id",
        "team_type",
        "player_name",
        "goals",
        "assists",
        "points",
        "shots_total",
    ]

    for col in required_game_cols:
        if col not in game_df.columns:
            issues.append({"level": "game_results", "issue": f"missing_column:{col}"})
        elif game_df[col].isna().any():
            issues.append(
                {
                    "level": "game_results",
                    "issue": f"null_values:{col}",
                    "count": int(game_df[col].isna().sum()),
                }
            )

    for col in required_team_cols:
        if col not in team_df.columns:
            issues.append({"level": "team_game_stats", "issue": f"missing_column:{col}"})
        elif team_df[col].isna().any():
            issues.append(
                {
                    "level": "team_game_stats",
                    "issue": f"null_values:{col}",
                    "count": int(team_df[col].isna().sum()),
                }
            )

    for col in required_player_cols:
        if col not in player_df.columns:
            issues.append({"level": "player_game_stats", "issue": f"missing_column:{col}"})
        elif player_df[col].isna().any():
            issues.append(
                {
                    "level": "player_game_stats",
                    "issue": f"null_values:{col}",
                    "count": int(player_df[col].isna().sum()),
                }
            )

    return issues


def validate_boxscore_data(season: str) -> tuple[pd.DataFrame, list[dict]]:
    game_df, team_df, player_df = load_processed_boxscore_data(season)

    required_field_issues = validate_required_fields(game_df, team_df, player_df)

    game_by_id = game_df.set_index("game_id", drop=False)
    team_by_id = team_df.set_index("game_id", drop=False)

    validation_rows = []

    all_game_ids = sorted(set(game_df["game_id"]).union(set(team_df["game_id"])))

    for game_id in all_game_ids:
        if game_id not in game_by_id.index:
            validation_rows.append(
                {
                    "game_id": game_id,
                    "all_core_checks_passed": False,
                    "failed_checks": "missing_game_results_row",
                    "warning_checks": "",
                }
            )
            continue

        if game_id not in team_by_id.index:
            validation_rows.append(
                {
                    "game_id": game_id,
                    "all_core_checks_passed": False,
                    "failed_checks": "missing_team_stats_row",
                    "warning_checks": "",
                }
            )
            continue

        game_row = game_by_id.loc[game_id]
        team_row = team_by_id.loc[game_id]
        player_game_df = player_df[player_df["game_id"] == game_id]

        if player_game_df.empty:
            validation_rows.append(
                {
                    "game_id": game_id,
                    "date": game_row.get("date"),
                    "opponent_clean": game_row.get("opponent_clean"),
                    "all_core_checks_passed": False,
                    "failed_checks": "missing_player_rows",
                    "warning_checks": "",
                }
            )
            continue

        validation_row = validate_one_game(
            game_id=game_id,
            game_row=game_row,
            team_row=team_row,
            player_game_df=player_game_df,
        )

        validation_rows.append(validation_row)

    validation_df = pd.DataFrame(validation_rows)

    return validation_df, required_field_issues


def save_validation_results(validation_df: pd.DataFrame, season: str) -> Path:
    VALIDATION_DIR.mkdir(parents=True, exist_ok=True)

    out_path = VALIDATION_DIR / f"boxscore_validation_{season.replace('-', '_')}.csv"

    validation_df.to_csv(out_path, index=False)

    return out_path


def print_validation_summary(
    validation_df: pd.DataFrame,
    required_field_issues: list[dict],
) -> None:
    print()
    print("Validation summary")
    print("==================")

    total_games = len(validation_df)
    passed_games = int(validation_df["all_core_checks_passed"].sum())
    failed_games = total_games - passed_games

    print(f"Games checked: {total_games}")
    print(f"Games core passed: {passed_games}")
    print(f"Games core failed: {failed_games}")

    print()

    core_check_cols = [
        "bu_goals_match",
        "opponent_goals_match",
        "bu_shots_match",
        "opponent_shots_match",
        "bu_blocks_match",
        "opponent_blocks_match",
    ]

    warning_check_cols = [
        "bu_penalty_minutes_match",
        "opponent_penalty_minutes_match",
    ]

    print("Core checks")
    print("-----------")
    for col in core_check_cols:
        if col in validation_df.columns:
            passed = int(validation_df[col].fillna(False).sum())
            print(f"{col}: {passed}/{total_games} passed")

    print()
    print("Warning checks")
    print("--------------")
    for col in warning_check_cols:
        if col in validation_df.columns:
            passed = int(validation_df[col].fillna(False).sum())
            print(f"{col}: {passed}/{total_games} passed")

    if required_field_issues:
        print()
        print("Required field issues")
        print("---------------------")
        for issue in required_field_issues:
            print(issue)

    failed_df = validation_df[~validation_df["all_core_checks_passed"]]

    if not failed_df.empty:
        print()
        print("Core failed games")
        print("-----------------")

        display_cols = [
            "game_id",
            "opponent_clean",
            "failed_checks",
            "warning_checks",
            "bu_goals_diff",
            "opponent_goals_diff",
            "bu_shots_diff",
            "opponent_shots_diff",
            "bu_blocks_diff",
            "opponent_blocks_diff",
        ]

        existing_cols = [col for col in display_cols if col in failed_df.columns]
        print(failed_df[existing_cols].to_string(index=False))

    warning_df = validation_df[
        validation_df.get("warning_checks", pd.Series(dtype=str)).fillna("") != ""
    ]

    if not warning_df.empty:
        print()
        print("Warning-only issues")
        print("-------------------")

        display_cols = [
            "game_id",
            "opponent_clean",
            "warning_checks",
            "bu_penalty_minutes_diff",
            "opponent_penalty_minutes_diff",
        ]

        existing_cols = [col for col in display_cols if col in warning_df.columns]
        print(warning_df[existing_cols].to_string(index=False))


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate parsed BU hockey box score data")
    parser.add_argument("--season", default="2025-26", help="Season, e.g. 2025-26")

    args = parser.parse_args()

    print(f"Validating parsed box score data for season {args.season}...")

    validation_df, required_field_issues = validate_boxscore_data(args.season)

    out_path = save_validation_results(validation_df, args.season)

    print_validation_summary(validation_df, required_field_issues)

    print()
    print(f"Saved validation results to {out_path}")


if __name__ == "__main__":
    main()
