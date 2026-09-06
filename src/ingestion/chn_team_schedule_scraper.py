"""
Scrapes a team's schedule/results from College Hockey News.

Example CHN URL:
    https://www.collegehockeynews.com/schedules/team/Boston-University/10/20252026

Usage:
    uv run python -m src.ingestion.chn_team_schedule_scraper \
        --team "Boston University" \
        --slug Boston-University \
        --team-id 10 \
        --season 2025-26

Output:
    data/raw/chn/schedules/2025_26/boston_university.html
    data/raw/chn/schedules/2025_26/boston_university_tables.json
    data/processed/chn/team_schedule_boston_university_2025_26.csv
"""

import argparse
import json
import re
import time
from pathlib import Path
from urllib.parse import urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup

CHN_BASE_URL = "https://www.collegehockeynews.com"
RAW_CHN_SCHEDULE_DIR = Path("data/raw/chn/schedules")
PROCESSED_CHN_DIR = Path("data/processed/chn")

HEADERS = {"User-Agent": "bu-hockey-scouting-tool/0.1 (personal project; contact via GitHub)"}


def season_to_chn_key(season: str) -> str:
    """
    Converts:
        2025-26
    to:
        20252026
    """
    start_year = int(season.split("-")[0])
    end_suffix = int(season.split("-")[1])
    end_year = int(str(start_year)[:2] + f"{end_suffix:02d}")

    return f"{start_year}{end_year}"


def slugify(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    value = value.strip("_")
    return value


def build_chn_schedule_url(slug: str, team_id: str | int, season: str) -> str:
    season_key = season_to_chn_key(season)
    return f"{CHN_BASE_URL}/schedules/team/{slug}/{team_id}/{season_key}"


def fetch_html(url: str) -> str:
    resp = requests.get(url, headers=HEADERS, timeout=20)

    print(f"Requested URL: {resp.url}")
    print(f"Status code: {resp.status_code}")

    resp.raise_for_status()

    time.sleep(1)

    return resp.text


def save_raw_html(html: str, team: str, season: str) -> Path:
    out_dir = RAW_CHN_SCHEDULE_DIR / season.replace("-", "_")
    out_dir.mkdir(parents=True, exist_ok=True)

    out_path = out_dir / f"{slugify(team)}.html"

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)

    return out_path


def inspect_tables_with_pandas(html: str) -> list[pd.DataFrame]:
    """
    Uses pandas.read_html to inspect all HTML tables on the CHN page.

    This is often the fastest way to parse sports schedule tables.
    """
    try:
        tables = pd.read_html(html)
    except ValueError:
        return []

    return tables


def save_tables_debug(tables: list[pd.DataFrame], team: str, season: str) -> Path:
    out_dir = RAW_CHN_SCHEDULE_DIR / season.replace("-", "_")
    out_dir.mkdir(parents=True, exist_ok=True)

    out_path = out_dir / f"{slugify(team)}_tables.json"

    payload = []

    for idx, table in enumerate(tables):
        payload.append(
            {
                "table_index": idx,
                "shape": list(table.shape),
                "columns": [str(col) for col in table.columns],
                "head": table.head(10).astype(str).to_dict(orient="records"),
            }
        )

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    return out_path


def choose_schedule_table(tables: list[pd.DataFrame]) -> pd.DataFrame | None:
    """
    Chooses the most likely schedule table.

    We look for a table with columns or content related to:
        Date, Opponent, Result, Time, Box
    """
    if not tables:
        return None

    best_table = None
    best_score = -1

    keywords = [
        "date",
        "opponent",
        "result",
        "time",
        "score",
        "conf",
        "location",
        "venue",
    ]

    for table in tables:
        columns_text = " ".join(str(col).lower() for col in table.columns)
        sample_text = " ".join(
            table.head(5).astype(str).fillna("").to_numpy().flatten().tolist()
        ).lower()

        text = columns_text + " " + sample_text

        score = sum(1 for keyword in keywords if keyword in text)

        # Prefer larger tables too.
        score += min(len(table), 40) / 10

        if score > best_score:
            best_score = score
            best_table = table

    return best_table


def clean_column_name(col) -> str:
    col = str(col).strip().lower()
    col = re.sub(r"[^a-z0-9]+", "_", col)
    col = col.strip("_")
    return col


def normalize_schedule_table(
    df: pd.DataFrame, team: str, season: str, source_url: str
) -> pd.DataFrame:
    """
    Normalizes a CHN schedule table as best as possible.

    Because CHN table structure may differ, this keeps original columns too.
    """
    df = df.copy()

    # Flatten MultiIndex columns if any.
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [
            "_".join(str(part) for part in col if str(part) != "nan").strip() for col in df.columns
        ]

    original_columns = list(df.columns)

    df.columns = [clean_column_name(col) for col in df.columns]

    df["season"] = season
    df["team"] = team
    df["source"] = "college_hockey_news"
    df["source_url"] = source_url

    # Try to identify common columns.
    # We keep this intentionally broad for first pass.
    col_map_candidates = {
        "date": ["date", "game_date"],
        "opponent": ["opponent", "opp"],
        "result": ["result", "res"],
        "score": ["score"],
        "time": ["time"],
        "location": ["location", "site", "venue"],
    }

    for canonical, candidates in col_map_candidates.items():
        if canonical in df.columns:
            continue

        for candidate in candidates:
            matches = [col for col in df.columns if col == candidate or candidate in col]
            if matches:
                df[canonical] = df[matches[0]]
                break

        if canonical not in df.columns:
            df[canonical] = None

    df["original_columns"] = ", ".join(str(col) for col in original_columns)

    return df


def parse_schedule_with_bs4(html: str, team: str, season: str, source_url: str) -> pd.DataFrame:
    """
    Backup parser using BeautifulSoup.

    This collects table rows into generic columns if pandas parsing is insufficient.
    """
    soup = BeautifulSoup(html, "lxml")

    tables = soup.select("table")

    parsed_tables = []

    for table_idx, table in enumerate(tables):
        rows = []

        for tr in table.select("tr"):
            cells = [cell.get_text(" ", strip=True) for cell in tr.select("th, td")]

            if cells:
                rows.append(cells)

        if not rows:
            continue

        max_len = max(len(row) for row in rows)
        normalized_rows = [row + [None] * (max_len - len(row)) for row in rows]

        df = pd.DataFrame(normalized_rows)
        df["table_index"] = table_idx
        parsed_tables.append(df)

    if not parsed_tables:
        return pd.DataFrame()

    # Use the largest parsed table.
    best = max(parsed_tables, key=len)
    best["season"] = season
    best["team"] = team
    best["source"] = "college_hockey_news"
    best["source_url"] = source_url

    return best


def scrape_chn_team_schedule(team: str, slug: str, team_id: str | int, season: str) -> pd.DataFrame:
    url = build_chn_schedule_url(slug=slug, team_id=team_id, season=season)

    html = fetch_html(url)

    raw_path = save_raw_html(html, team=team, season=season)
    print(f"Saved raw HTML to {raw_path}")

    tables = inspect_tables_with_pandas(html)
    print(f"Found {len(tables)} tables with pandas.read_html")

    debug_tables_path = save_tables_debug(tables, team=team, season=season)
    print(f"Saved table debug JSON to {debug_tables_path}")

    schedule_table = choose_schedule_table(tables)

    if schedule_table is not None:
        print(f"Selected schedule table shape: {schedule_table.shape}")
        df = normalize_schedule_table(
            schedule_table,
            team=team,
            season=season,
            source_url=url,
        )
    else:
        print("No pandas table selected. Trying BeautifulSoup fallback.")
        df = parse_schedule_with_bs4(
            html,
            team=team,
            season=season,
            source_url=url,
        )

    return df


def save_processed_schedule(df: pd.DataFrame, team: str, season: str) -> Path:
    PROCESSED_CHN_DIR.mkdir(parents=True, exist_ok=True)

    out_path = PROCESSED_CHN_DIR / f"team_schedule_{slugify(team)}_{season.replace('-', '_')}.csv"

    df.to_csv(out_path, index=False)

    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape College Hockey News team schedule")

    parser.add_argument(
        "--team",
        default="Boston University",
        help='Canonical team name, e.g. "Boston University"',
    )

    parser.add_argument(
        "--slug",
        default="Boston-University",
        help='CHN team slug, e.g. "Boston-University"',
    )

    parser.add_argument(
        "--team-id",
        default="10",
        help="CHN numeric team id, e.g. 10 for Boston University",
    )

    parser.add_argument(
        "--season",
        default="2025-26",
        help="Season, e.g. 2025-26",
    )

    args = parser.parse_args()

    print(
        f"Scraping CHN schedule for {args.team} "
        f"({args.slug}, id={args.team_id}) season {args.season}"
    )

    df = scrape_chn_team_schedule(
        team=args.team,
        slug=args.slug,
        team_id=args.team_id,
        season=args.season,
    )

    if df.empty:
        print("WARNING: No schedule rows parsed.")
        return

    out_path = save_processed_schedule(df, team=args.team, season=args.season)

    print()
    print(f"Saved {len(df)} rows to {out_path}")
    print()
    print(df.head(20).to_string(index=False))


if __name__ == "__main__":
    main()
