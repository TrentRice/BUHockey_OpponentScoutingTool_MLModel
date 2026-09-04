"""
Downloads raw BU men's hockey box score HTML pages from goterriers.com.

This script reads the processed BU schedule CSV, finds box_score_url values,
downloads each page, and saves the raw HTML locally for later parsing.

Usage:
    uv run python -m src.ingestion.bu_boxscore_scraper --season 2025-26

Input:
    data/processed/bu_schedule_2025_26.csv

Output:
    data/raw/boxscores/2025_26/<game_id>.html
"""

import argparse
import time
from pathlib import Path

import pandas as pd
import requests

PROCESSED_DATA_DIR = Path("data/processed")
RAW_BOXSCORE_DIR = Path("data/raw/boxscores")

HEADERS = {"User-Agent": "bu-hockey-scouting-tool/0.1 (personal project; contact via GitHub)"}


def fetch_boxscore_html(url: str) -> str:
    """
    Fetches one box score HTML page.
    """
    resp = requests.get(url, headers=HEADERS, timeout=20)

    print(f"Requested URL: {resp.url}")
    print(f"Status code: {resp.status_code}")

    resp.raise_for_status()

    # Be polite to the server.
    time.sleep(1)

    return resp.text


def load_processed_schedule(season: str) -> pd.DataFrame:
    """
    Loads processed BU schedule CSV for a season.
    """
    path = PROCESSED_DATA_DIR / f"bu_schedule_{season.replace('-', '_')}.csv"

    if not path.exists():
        raise FileNotFoundError(
            f"Could not find processed schedule CSV: {path}. Run process_bu_schedule.py first."
        )

    return pd.read_csv(path)


def save_boxscore_html(html: str, season: str, game_id: str) -> Path:
    """
    Saves raw box score HTML to disk.
    """
    out_dir = RAW_BOXSCORE_DIR / season.replace("-", "_")
    out_dir.mkdir(parents=True, exist_ok=True)

    out_path = out_dir / f"{game_id}.html"

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)

    return out_path


def download_boxscores(season: str, overwrite: bool = False) -> None:
    """
    Downloads all box score pages listed in the processed schedule.
    """
    df = load_processed_schedule(season)

    if "box_score_url" not in df.columns:
        raise ValueError("Processed schedule is missing box_score_url column.")

    if "game_id" not in df.columns:
        raise ValueError("Processed schedule is missing game_id column.")

    boxscore_rows = df[df["box_score_url"].notna()].copy()

    print(f"Found {len(boxscore_rows)} rows with box score URLs.")

    downloaded = 0
    skipped = 0
    failed = 0

    for _, row in boxscore_rows.iterrows():
        game_id = row["game_id"]
        url = row["box_score_url"]

        out_dir = RAW_BOXSCORE_DIR / season.replace("-", "_")
        out_path = out_dir / f"{game_id}.html"

        if out_path.exists() and not overwrite:
            print(f"Skipping existing file: {out_path}")
            skipped += 1
            continue

        print()
        print(f"Downloading box score for {game_id}")
        print(url)

        try:
            html = fetch_boxscore_html(url)
            saved_path = save_boxscore_html(html, season, game_id)
            print(f"Saved to {saved_path}")
            downloaded += 1

        except requests.HTTPError as e:
            print(f"HTTP error for {game_id}: {e}")
            failed += 1

        except requests.RequestException as e:
            print(f"Request error for {game_id}: {e}")
            failed += 1

    print()
    print("Done.")
    print(f"Downloaded: {downloaded}")
    print(f"Skipped:    {skipped}")
    print(f"Failed:     {failed}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Download raw BU hockey box score HTML pages")
    parser.add_argument(
        "--season",
        default="2025-26",
        help="Season, e.g. 2025-26",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Re-download box scores even if HTML files already exist.",
    )

    args = parser.parse_args()

    download_boxscores(args.season, overwrite=args.overwrite)


if __name__ == "__main__":
    main()
