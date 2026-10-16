"""
CHN box score downloader.

Reads data/processed/chn/all_games_<tag>.csv (written by chn_team_schedule_scraper) and saves the
raw HTML of the box score for every PLAYED game to:

  data/raw/chn/boxscores/<tag>/<game_id>.html

Incremental: a page already on disk is skipped (a final box score doesn't change), so re-running
during a season only fetches games played since the last run. Typical in-season routine:

  uv run python -m src.ingestion.chn_team_schedule_scraper     # refresh schedule/results
  uv run python -m src.ingestion.chn_boxscore_downloader       # fetch the new box scores

Usage (from project root):
  uv run python -m src.ingestion.chn_boxscore_downloader                    # current season
  uv run python -m src.ingestion.chn_boxscore_downloader --limit 5          # try a handful first
  uv run python -m src.ingestion.chn_boxscore_downloader --season 2025-26
  uv run python -m src.ingestion.chn_boxscore_downloader --team "Boston University" --since 2026-10-01
  uv run python -m src.ingestion.chn_boxscore_downloader --dry-run          # just count what's pending
  uv run python -m src.ingestion.chn_boxscore_downloader --refresh          # re-download everything
  uv run python -m src.ingestion.chn_boxscore_downloader --strict           # exit 1 on any problem

Safety rails:
  - a page is only saved if it looks like a box score AND mentions the game's date
  - writes are atomic (a crash never leaves a half-written page that looks complete)
  - transient errors (5xx, 429, timeouts) are retried with backoff; 403/404 are not
  - after several failures in a row the run stops, so a block or outage isn't hammered
  - every run ends with a summary; failures are listed in <box dir>/_failures.csv
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup

from src.ingestion.chn_team_schedule_scraper import HEADERS, Season

REQUEST_DELAY = 2.0  # seconds between requests; be polite to a small, ad-supported site
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = 5.0
MAX_CONSECUTIVE_FAILURES = 5
_last_request = 0.0


# ----------------------------------------------------------------------------
# Paths + inputs
# ----------------------------------------------------------------------------


def games_csv_path(root: Path, season: Season) -> Path:
    return root / f"data/processed/chn/all_games_{season.tag}.csv"


def box_dir(root: Path, season: Season) -> Path:
    return root / f"data/raw/chn/boxscores/{season.tag}"


def box_path(root: Path, season: Season, game_id: str) -> Path:
    return box_dir(root, season) / f"{game_id}.html"


def load_played_games(root: Path, season: Season) -> pd.DataFrame:
    path = games_csv_path(root, season)
    if not path.exists():
        raise SystemExit(
            f"{path} not found. Run the schedule scraper first:\n"
            f"  uv run python -m src.ingestion.chn_team_schedule_scraper --season {season.label}"
        )
    games = pd.read_csv(path)
    need = {"game_id", "date", "home_team", "away_team", "is_played", "box_score_url"}
    if need - set(games.columns):
        raise SystemExit(f"{path} is missing columns: {sorted(need - set(games.columns))}")
    played = games["is_played"].astype(str).str.lower() == "true"
    no_link = games[played & games["box_score_url"].isna()]
    if len(no_link):
        print(f"Note: {len(no_link)} played games have no box score link on CHN (skipped).")
    return games[played & games["box_score_url"].notna()].copy()


# ----------------------------------------------------------------------------
# Fetch + validate + save
# ----------------------------------------------------------------------------


def fetch(url: str) -> tuple[str | None, str | None]:
    """Returns (html, None) on success or (None, error description)."""
    global _last_request
    error = "unknown error"
    for attempt in range(1, MAX_ATTEMPTS + 1):
        wait = REQUEST_DELAY - (time.time() - _last_request)
        if wait > 0:
            time.sleep(wait)
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30)
        except requests.RequestException as e:
            _last_request = time.time()
            error = f"network error ({type(e).__name__})"
        else:
            _last_request = time.time()
            if resp.status_code == 200:
                return resp.text, None
            error = f"HTTP {resp.status_code}"
            if resp.status_code in (403, 404, 410):  # retrying won't help
                return None, error
        if attempt < MAX_ATTEMPTS:
            time.sleep(BACKOFF_SECONDS * attempt)
    return None, error


def check_page(html: str, game_date: str) -> str | None:
    """A problem description, or None if this looks like the right game's box score."""
    text = BeautifulSoup(html, "lxml").get_text(" ", strip=True)
    if not ("Goals" in text and "Shots" in text and "PIM" in text):
        return "doesn't look like a box score (no Goals/Shots/PIM tables)"
    d = date.fromisoformat(game_date)
    if f"{d:%B} {d.day}, {d.year}" not in text:
        return f"page doesn't mention the game date ({d:%B} {d.day}, {d.year})"
    return None


def save_atomic(path: Path, html: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".html.part")
    tmp.write_text(html, encoding="utf-8")
    tmp.replace(path)  # a crash can leave a .part file, never a truncated .html


# ----------------------------------------------------------------------------
# Main routine
# ----------------------------------------------------------------------------


@dataclass
class DownloadResult:
    downloaded: int = 0
    already_had: int = 0
    pending: int = 0
    missing: int = 0
    aborted: bool = False
    failures: list[tuple[str, str, str]] = field(default_factory=list)  # (game_id, url, error)
    limited: bool = False

    @property
    def has_problems(self) -> bool:
        return bool(self.failures) or self.aborted or (self.missing > 0 and not self.limited)


def download_boxscores(
    season: Season,
    root: Path = Path("."),
    refresh: bool = False,
    since: str | None = None,
    team: str | None = None,
    limit: int | None = None,
    dry_run: bool = False,
) -> DownloadResult:
    played = load_played_games(root, season)
    if since:
        played = played[played["date"] >= since]
    if team:
        t = team.lower()
        played = played[
            (played["home_team"].astype(str).str.lower() == t)
            | (played["away_team"].astype(str).str.lower() == t)
        ]
    played = played.sort_values(["date", "game_id"]).reset_index(drop=True)

    have = played["game_id"].map(lambda g: box_path(root, season, g).exists())
    pending = played if refresh else played[~have]
    res = DownloadResult(already_had=0 if refresh else int(have.sum()))
    if limit is not None and len(pending) > limit:
        pending, res.limited = pending.head(limit), True
    res.pending = len(pending)

    print(
        f"Season {season.label}: {len(played)} played games in scope, "
        f"{int(have.sum())} already on disk, {len(pending)} to download"
        + (f" (limited to {limit})" if res.limited else "")
    )
    if dry_run:
        return res

    consecutive = 0
    for i, row in enumerate(pending.itertuples(), start=1):
        html, err = fetch(row.box_score_url)
        if err is None:
            err = check_page(html, row.date)
        if err:
            res.failures.append((row.game_id, row.box_score_url, err))
            consecutive += 1
            print(
                f"[{i}/{len(pending)}] {row.date} {row.away_team} at {row.home_team}: FAILED ({err})"
            )
            if consecutive >= MAX_CONSECUTIVE_FAILURES:
                res.aborted = True
                print(
                    f"Stopping: {consecutive} failures in a row (site down, or blocking this "
                    "computer?). Re-run later; downloaded pages are kept."
                )
                break
            continue
        save_atomic(box_path(root, season, row.game_id), html)
        res.downloaded += 1
        consecutive = 0
        if i % 25 == 0 or i == len(pending):
            print(f"[{i}/{len(pending)}] downloaded {res.downloaded} so far (latest: {row.date})")

    res.missing = int((~played["game_id"].map(lambda g: box_path(root, season, g).exists())).sum())
    fail_path = box_dir(root, season) / "_failures.csv"
    fail_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(res.failures, columns=["game_id", "url", "error"]).to_csv(fail_path, index=False)

    print(
        f"\nDone: {res.downloaded} downloaded, {len(res.failures)} failed. "
        f"On disk: {len(played) - res.missing} of {len(played)} played games in scope."
    )
    if res.failures:
        print(f"Failures listed in {fail_path} (re-run to retry them):")
        for gid, _, err in res.failures[:5]:
            print(f"  {gid}: {err}")
        if len(res.failures) > 5:
            print(f"  ... and {len(res.failures) - 5} more")
    return res


def main() -> None:
    global REQUEST_DELAY
    ap = argparse.ArgumentParser(description="Download CHN box score HTML for played games")
    ap.add_argument("--season", help="e.g. 2026-27 (default: the current season)")
    ap.add_argument("--team", help="only games involving this team (exact display name)")
    ap.add_argument("--since", help="only games on/after this date, YYYY-MM-DD")
    ap.add_argument("--limit", type=int, help="download at most this many pages")
    ap.add_argument("--refresh", action="store_true", help="re-download pages already on disk")
    ap.add_argument("--dry-run", action="store_true", help="count what's pending; download nothing")
    ap.add_argument("--delay", type=float, default=REQUEST_DELAY, help="seconds between requests")
    ap.add_argument("--strict", action="store_true", help="exit with code 1 on any problem")
    args = ap.parse_args()
    REQUEST_DELAY = args.delay

    season = Season.parse(args.season) if args.season else Season.current()
    res = download_boxscores(
        season,
        refresh=args.refresh,
        since=args.since,
        team=args.team,
        limit=args.limit,
        dry_run=args.dry_run,
    )
    if args.strict and res.has_problems:
        sys.exit(1)


if __name__ == "__main__":
    main()