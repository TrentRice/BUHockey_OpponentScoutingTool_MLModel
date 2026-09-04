"""
Debugs player stat table detection in saved BU box score text files.

Usage:
    uv run python -m src.evaluation.debug_player_sections --season 2025-26
"""

import argparse
from pathlib import Path

import pandas as pd

PROCESSED_BOXSCORE_DIR = Path("data/processed/boxscores")
INTERIM_TEXT_DIR = Path("data/interim/boxscore_text")


def load_validation(season: str) -> pd.DataFrame:
    path = Path("data/processed/validation") / f"boxscore_validation_{season.replace('-', '_')}.csv"

    if not path.exists():
        raise FileNotFoundError(f"Missing validation file: {path}")

    return pd.read_csv(path)


def load_text_lines(season: str, game_id: str) -> list[str]:
    path = INTERIM_TEXT_DIR / season.replace("-", "_") / f"{game_id}.txt"

    if not path.exists():
        raise FileNotFoundError(f"Missing debug text file: {path}")

    with open(path, "r", encoding="utf-8") as f:
        return [line.strip() for line in f.read().splitlines() if line.strip()]


def print_player_sections(lines: list[str], game_id: str) -> None:
    print()
    print("=" * 100)
    print(game_id)
    print("=" * 100)

    found = False

    for i, line in enumerate(lines):
        if line == "Shots by Period":
            found = True
            start = max(0, i - 3)
            end = min(len(lines), i + 60)

            print()
            print(f"Found 'Shots by Period' at line {i}")
            print("-" * 80)

            for j in range(start, end):
                print(f"{j:05d}: {lines[j]}")

    if not found:
        print("No 'Shots by Period' found.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Debug box score player stat sections")
    parser.add_argument("--season", default="2025-26", help="Season, e.g. 2025-26")
    parser.add_argument(
        "--only-failed",
        action="store_true",
        help="Only print games that failed validation.",
    )

    args = parser.parse_args()

    validation_df = load_validation(args.season)

    if args.only_failed:
        validation_df = validation_df[~validation_df["all_core_checks_passed"]]

    for _, row in validation_df.iterrows():
        game_id = row["game_id"]
        lines = load_text_lines(args.season, game_id)
        print_player_sections(lines, game_id)


if __name__ == "__main__":
    main()
