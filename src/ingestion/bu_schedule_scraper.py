"""
Scrapes Boston University Men's Ice Hockey schedule and results from the
official BU Athletics site (goterriers.com), which runs on SIDEARM Sports'
newer Vue/Nuxt-based template.

Usage:
    uv run python -m src.ingestion.bu_schedule_scraper --season 2025-26

Output:
    data/raw/schedule_<season>.json
"""

import argparse
import json
import re
import time
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://goterriers.com/sports/mens-ice-hockey/schedule"
HEADERS = {"User-Agent": "bu-hockey-scouting-tool/0.1 (personal project; contact via GitHub)"}
RAW_DATA_DIR = Path("data/raw")


def fetch_schedule_page(season: str | None = None) -> str:
    """
    Fetch the raw HTML of the schedule page for a given season.

    Example:
        season='2025-26'

    Correct URL pattern:
        https://goterriers.com/sports/mens-ice-hockey/schedule/2025-26
    """
    if season:
        url = f"{BASE_URL}/{season}"
    else:
        url = BASE_URL

    resp = requests.get(url, headers=HEADERS, timeout=15)

    print(f"Requested URL: {resp.url}")
    print(f"Status code: {resp.status_code}")

    resp.raise_for_status()

    # Be polite to the server.
    time.sleep(1)

    return resp.text


def parse_game_datetime(
    date_text: str | None,
    time_text: str | None,
    season: str,
) -> dict:
    """
    Handles both SIDEARM date/time formats.

    Format 1: combined date/time in the time element
        date_text = None
        time_text = 'Oct 4 (Sat) 7 PM'

    Format 2: separate date and time elements
        date_text = 'Oct 4 (Sat)'
        time_text = '7 PM'

    Returns:
        {
            "date_raw": "Oct 4 (Sat)",
            "time_raw": "7 PM",
            "date": "2025-10-04",
            "day_of_week": "Sat"
        }
    """
    if date_text:
        combined_text = f"{date_text} {time_text or ''}".strip()
    else:
        combined_text = time_text or ""

    if not combined_text:
        return {
            "date_raw": None,
            "time_raw": None,
            "date": None,
            "day_of_week": None,
        }

    # Matches:
    #   Oct 4 (Sat) 7 PM
    #   Feb 9 (Mon) 7:30 PM
    #   Mar 14 (Sat) 1 PM
    #   Oct 4 (Sat)
    pattern = r"^([A-Za-z]{3})\s+(\d{1,2})\s+\(([A-Za-z]{3})\)(?:\s+(.+))?$"
    match = re.match(pattern, combined_text.strip())

    if not match:
        return {
            "date_raw": date_text,
            "time_raw": time_text,
            "date": None,
            "day_of_week": None,
        }

    month_abbr, day, dow, parsed_time = match.groups()

    start_year = int(season.split("-")[0])
    end_year_suffix = int(season.split("-")[1])
    end_year = int(str(start_year)[:2] + f"{end_year_suffix:02d}")

    month_num = datetime.strptime(month_abbr, "%b").month

    # College hockey season crosses calendar years.
    # Aug-Dec belong to the starting year.
    # Jan-Jul belong to the ending year.
    if month_num >= 8:
        year = start_year
    else:
        year = end_year

    date_obj = datetime(year, month_num, int(day))

    return {
        "date_raw": f"{month_abbr} {day} ({dow})",
        "time_raw": parsed_time or time_text,
        "date": date_obj.strftime("%Y-%m-%d"),
        "day_of_week": dow,
    }


def absolutize_url(url: str | None) -> str | None:
    """
    Converts a relative goterriers.com URL into an absolute URL.
    """
    if not url:
        return None

    if url.startswith("/"):
        return "https://goterriers.com" + url

    return url


def parse_schedule(html: str, season: str) -> list[dict]:
    """
    Parses SIDEARM's Vue/Nuxt schedule card markup into a list of game dicts.

    Each game card is expected to look like:
        <div data-test-id="s-game-card-standard__root">
    """
    soup = BeautifulSoup(html, "lxml")
    games = []

    cards = soup.select('div[data-test-id="s-game-card-standard__root"]')

    for card in cards:
        game = {
            "season": season,
            "team": "Boston University",
        }

        opp_el = card.select_one(
            'a[data-test-id="s-game-card-standard__header-team-opponent-link"]'
        )
        game["opponent"] = opp_el.get_text(" ", strip=True) if opp_el else None

        # "at" = away game, "vs" = home game.
        # Other values may indicate neutral-site or unusual listings.
        stamp_el = card.select_one(".s-stamp__text")
        stamp_text = stamp_el.get_text(" ", strip=True) if stamp_el else None

        game["home_away_raw"] = stamp_text

        if stamp_text == "at":
            game["is_home"] = False
            game["is_neutral"] = False
        elif stamp_text == "vs":
            game["is_home"] = True

            # Some "vs" games are actually neutral-site games, e.g. TD Garden/MSG.
            # We refine this below after parsing location.
            game["is_neutral"] = False
        else:
            game["is_home"] = None
            game["is_neutral"] = None

        date_el = card.select_one('p[data-test-id="s-game-card-standard__header-game-date"]')
        time_el = card.select_one('p[data-test-id="s-game-card-standard__header-game-time"]')

        date_text = date_el.get_text(" ", strip=True) if date_el else None
        time_text = time_el.get_text(" ", strip=True) if time_el else None

        parsed_dt = parse_game_datetime(date_text, time_text, season)
        game.update(parsed_dt)

        location_el = card.select_one(
            'span[data-test-id="s-game-card-facility-and-location__standard-location-details"]'
        )
        game["location"] = location_el.get_text(" ", strip=True) if location_el else None

        facility_el = card.select_one(
            'a[data-test-id="s-game-card-facility-and-location__game-facility-title-link"]'
        )
        game["facility"] = facility_el.get_text(" ", strip=True) if facility_el else None

        # Refine neutral-site detection.
        # BU home games at Agganis are true home games.
        # Games listed as "vs" but played at TD Garden/MSG/etc. are better treated as neutral.
        location_lower = (game["location"] or "").lower()
        facility_lower = (game["facility"] or "").lower()

        neutral_indicators = [
            "td garden",
            "madison square garden",
        ]

        if game["home_away_raw"] == "vs":
            if any(indicator in location_lower for indicator in neutral_indicators) or any(
                indicator in facility_lower for indicator in neutral_indicators
            ):
                game["is_home"] = False
                game["is_neutral"] = True

        # Only present once a game has been played / has stats posted,
        # though SIDEARM may expose future box score URLs early.
        box_el = card.select_one('a[href*="boxscore"]')
        box_score_url = box_el["href"] if box_el and box_el.has_attr("href") else None
        game["box_score_url"] = absolutize_url(box_score_url)

        games.append(game)

    return games


def save_raw(games: list[dict], season: str) -> Path:
    """
    Saves parsed schedule data to data/raw/schedule_<season>.json.
    """
    RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)

    out_path = RAW_DATA_DIR / f"schedule_{season.replace('-', '_')}.json"

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(games, f, indent=2)

    return out_path


def save_debug_html(html: str, season: str) -> Path:
    """
    Saves raw HTML for debugging if parsing fails.
    """
    RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)

    out_path = RAW_DATA_DIR / f"schedule_page_{season.replace('-', '_')}.html"

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)

    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape BU hockey schedule from goterriers.com")
    parser.add_argument(
        "--season",
        default="2025-26",
        help="Season, e.g. 2025-26",
    )

    args = parser.parse_args()

    print(f"Fetching schedule page for season {args.season}...")
    html = fetch_schedule_page(args.season)

    print("Parsing games...")
    games = parse_schedule(html, args.season)

    if not games:
        print(
            "WARNING: No games parsed. SIDEARM may have changed its template, "
            "or this season has no scheduled games yet."
        )

        debug_path = save_debug_html(html, args.season)
        print(f"Raw HTML saved to {debug_path} for inspection.")
        return

    print("First 5 parsed games:")
    for game in games[:5]:
        print(game)

    out_path = save_raw(games, args.season)
    print(f"Saved {len(games)} games to {out_path}")


if __name__ == "__main__":
    main()
