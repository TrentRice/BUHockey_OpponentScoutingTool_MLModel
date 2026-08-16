"""
Scrapes Boston University Men's Ice Hockey schedule and results from the
official BU Athletics site (goterriers.com), which runs on the SIDEARM Sports
platform used by most D1 athletic departments.

Usage:
    python -m src.ingestion.bu_schedule_scraper --season 2025-26

Output:
    data/raw/schedule_<season>.json
"""

import argparse
import json
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://goterriers.com/sports/mens-ice-hockey/schedule"
HEADERS = {
    # Identify ourselves honestly rather than spoofing a browser UA.
    "User-Agent": "bu-hockey-scouting-tool/0.1 (personal project; contact via GitHub)"
}
RAW_DATA_DIR = Path("data/raw")


def fetch_schedule_page(season: str | None = None) -> str:
    """
    Fetch the raw HTML of the schedule page. If season is None, fetches the
    current/default season shown on the page.
    """
    params = {}
    if season:
        params["season"] = season  # SIDEARM sites often support a season query param;
        # confirm the actual param name once we see the live page — may need adjusting.

    resp = requests.get(BASE_URL, headers=HEADERS, params=params, timeout=15)
    resp.raise_for_status()
    time.sleep(1)  # be polite — don't hammer the server
    return resp.text


def parse_schedule(html: str) -> list[dict]:
    """
    Parses SIDEARM's standard schedule markup into a list of game dicts.

    NOTE: SIDEARM sites typically render this list with elements like
    <li class="sidearm-schedule-game">. Selectors below are based on that
    common pattern. If this returns an empty list, the page is likely
    rendering the schedule client-side via JS, and we'll need a headless
    browser (Playwright) instead of plain requests — that's a genuinely
    useful thing to find out at this stage, not a bug to silently fix.
    """
    soup = BeautifulSoup(html, "lxml")
    games = []

    game_elements = soup.select("li.sidearm-schedule-game")

    for el in game_elements:
        game = {}

        # Date
        date_el = el.select_one(".sidearm-schedule-game-opponent-date, .date")
        game["date_raw"] = date_el.get_text(strip=True) if date_el else None

        # Opponent name
        opp_el = el.select_one(".sidearm-schedule-game-opponent-name")
        game["opponent"] = opp_el.get_text(strip=True) if opp_el else None

        # Home/away/neutral
        location_el = el.select_one(".sidearm-schedule-game-conference-status, .location-indicator")
        game["location_type_raw"] = location_el.get_text(strip=True) if location_el else None

        # Result (if game has been played)
        result_el = el.select_one(".sidearm-schedule-game-result")
        game["result_raw"] = result_el.get_text(strip=True) if result_el else None

        # Box score link
        box_score_el = el.select_one("a[href*='boxscore.aspx']")
        game["box_score_url"] = box_score_el["href"] if box_score_el else None
        if game["box_score_url"] and game["box_score_url"].startswith("/"):
            game["box_score_url"] = "https://goterriers.com" + game["box_score_url"]

        games.append(game)

    return games


def save_raw(games: list[dict], season: str) -> Path:
    RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RAW_DATA_DIR / f"schedule_{season.replace('-', '_')}.json"
    with open(out_path, "w") as f:
        json.dump(games, f, indent=2)
    return out_path


def main():
    parser = argparse.ArgumentParser(description="Scrape BU hockey schedule from goterriers.com")
    parser.add_argument("--season", default="2025-26", help="Season, e.g. 2025-26")
    args = parser.parse_args()

    print(f"Fetching schedule page for season {args.season}...")
    html = fetch_schedule_page(args.season)

    print("Parsing games...")
    games = parse_schedule(html)

    if not games:
        print(
            "WARNING: No games parsed. This likely means the schedule is rendered "
            "client-side via JavaScript and requests/BeautifulSoup can't see it. "
            "Next step in that case: switch to Playwright for a headless-browser fetch. "
            "Saving the raw HTML for inspection instead."
        )
        RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)
        debug_path = RAW_DATA_DIR / "schedule_page_raw.html"
        with open(debug_path, "w") as f:
            f.write(html)
        print(f"Raw HTML saved to {debug_path} for inspection.")
        return

    out_path = save_raw(games, args.season)
    print(f"Saved {len(games)} games to {out_path}")


if __name__ == "__main__":
    main()
