"""
Processes raw BU men's hockey schedule JSON into a cleaner tabular dataset.

Usage:
    uv run python -m src.ingestion.process_bu_schedule --season 2025-26

Input:
    data/raw/schedule_2025_26.json

Output:
    data/processed/bu_schedule_2025_26.csv
"""

import argparse
import json
import re
from pathlib import Path

import pandas as pd

RAW_DATA_DIR = Path("data/raw")
PROCESSED_DATA_DIR = Path("data/processed")


def clean_opponent_name(opponent: str | None) -> dict:
    """
    Splits messy opponent labels into structured fields.

    Examples:
        '#3/4 Michigan State'
        'RPI (exh.)'
        '(11) Vermont - Hockey East Opening Round'
        '(3) UConn - Hockey East Quarterfinal'
    """
    if not opponent:
        return {
            "opponent_clean": None,
            "opponent_rank_raw": None,
            "opponent_seed": None,
            "is_exhibition": False,
            "is_postseason": False,
            "postseason_round": None,
        }

    text = opponent.strip()

    is_exhibition = bool(re.search(r"\(exh\.?\)", text, flags=re.IGNORECASE))

    postseason_round = None
    is_postseason = False

    if "Hockey East" in text or "Opening Round" in text or "Quarterfinal" in text:
        is_postseason = True

        if " - " in text:
            text_part, round_part = text.split(" - ", 1)
            postseason_round = round_part.strip()
            text = text_part.strip()

    opponent_seed = None
    seed_match = re.match(r"^\((\d+)\)\s+", text)
    if seed_match:
        opponent_seed = int(seed_match.group(1))
        text = re.sub(r"^\(\d+\)\s+", "", text).strip()

    opponent_rank_raw = None
    rank_match = re.match(r"^(#[0-9/]+)\s+", text)
    if rank_match:
        opponent_rank_raw = rank_match.group(1)
        text = re.sub(r"^#[0-9/]+\s+", "", text).strip()

    text = re.sub(r"\(exh\.?\)", "", text, flags=re.IGNORECASE).strip()

    return {
        "opponent_clean": text,
        "opponent_rank_raw": opponent_rank_raw,
        "opponent_seed": opponent_seed,
        "is_exhibition": is_exhibition,
        "is_postseason": is_postseason,
        "postseason_round": postseason_round,
    }


def make_game_id(row: pd.Series) -> str:
    """
    Creates a stable game id for joining later.

    Example:
        bu_2025-10-04_liu
    """
    date_value = row.get("date")

    if pd.isna(date_value):
        date_str = "unknown_date"
    else:
        date_str = pd.to_datetime(date_value).strftime("%Y-%m-%d")

    opponent = row.get("opponent_clean") or "unknown_opponent"
    opponent_slug = (
        str(opponent)
        .lower()
        .replace(" ", "_")
        .replace("/", "_")
        .replace(".", "")
        .replace("'", "")
        .replace("(", "")
        .replace(")", "")
    )

    return f"bu_{date_str}_{opponent_slug}"


def process_schedule(season: str) -> pd.DataFrame:
    in_path = RAW_DATA_DIR / f"schedule_{season.replace('-', '_')}.json"

    if not in_path.exists():
        raise FileNotFoundError(f"Could not find raw schedule file: {in_path}")

    with open(in_path, "r", encoding="utf-8") as f:
        games = json.load(f)

    df = pd.DataFrame(games)

    if df.empty:
        return df

    opponent_fields = df["opponent"].apply(clean_opponent_name).apply(pd.Series)
    df = pd.concat([df, opponent_fields], axis=1)

    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["date"] = df["date"].dt.strftime("%Y-%m-%d")

    df["game_id"] = df.apply(make_game_id, axis=1)

    # Sort chronologically
    df = df.sort_values("date").reset_index(drop=True)

    # Put important columns first
    preferred_cols = [
        "game_id",
        "season",
        "team",
        "date",
        "day_of_week",
        "time_raw",
        "opponent",
        "opponent_clean",
        "opponent_rank_raw",
        "opponent_seed",
        "is_home",
        "is_neutral",
        "home_away_raw",
        "location",
        "facility",
        "is_exhibition",
        "is_postseason",
        "postseason_round",
        "box_score_url",
    ]

    existing_preferred_cols = [col for col in preferred_cols if col in df.columns]
    other_cols = [col for col in df.columns if col not in existing_preferred_cols]

    df = df[existing_preferred_cols + other_cols]

    return df


def save_processed(df: pd.DataFrame, season: str) -> Path:
    PROCESSED_DATA_DIR.mkdir(parents=True, exist_ok=True)

    out_path = PROCESSED_DATA_DIR / f"bu_schedule_{season.replace('-', '_')}.csv"

    df.to_csv(out_path, index=False)

    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Process raw BU schedule JSON")
    parser.add_argument("--season", default="2025-26", help="Season, e.g. 2025-26")

    args = parser.parse_args()

    print(f"Processing BU schedule for season {args.season}...")
    df = process_schedule(args.season)

    if df.empty:
        print("WARNING: Processed schedule is empty.")
        return

    out_path = save_processed(df, args.season)

    print(f"Saved {len(df)} processed games to {out_path}")
    print()
    print(df.head())


if __name__ == "__main__":
    main()
