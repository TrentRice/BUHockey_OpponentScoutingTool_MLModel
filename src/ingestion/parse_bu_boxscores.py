"""
Parses saved BU men's hockey box score HTML files into structured tables.

Usage:
    uv run python -m src.ingestion.parse_bu_boxscores --season 2025-26

Input:
    data/raw/boxscores/2025_26/*.html
    data/processed/bu_schedule_2025_26.csv

Output:
    data/processed/boxscores/game_results_2025_26.csv
    data/processed/boxscores/team_game_stats_2025_26.csv
    data/processed/boxscores/player_game_stats_2025_26.csv
    data/interim/boxscore_text/2025_26/*.txt
"""

import argparse
import re
from pathlib import Path

import pandas as pd
from bs4 import BeautifulSoup

RAW_BOXSCORE_DIR = Path("data/raw/boxscores")
PROCESSED_DATA_DIR = Path("data/processed")
INTERIM_TEXT_DIR = Path("data/interim/boxscore_text")
PROCESSED_BOXSCORE_DIR = Path("data/processed/boxscores")

BU_TEAM_NAMES = {"Boston University", "BOS", "BU"}


# ---------------------------------------------------------------------
# General file/loading helpers
# ---------------------------------------------------------------------


def load_schedule(season: str) -> pd.DataFrame:
    """
    Loads the processed BU schedule CSV for a season.
    """
    path = PROCESSED_DATA_DIR / f"bu_schedule_{season.replace('-', '_')}.csv"

    if not path.exists():
        raise FileNotFoundError(
            f"Could not find processed schedule file: {path}. Run process_bu_schedule.py first."
        )

    return pd.read_csv(path)


def html_to_soup(path: Path) -> BeautifulSoup:
    """
    Reads a saved HTML file and converts it to BeautifulSoup.
    """
    with open(path, "r", encoding="utf-8") as f:
        html = f.read()

    return BeautifulSoup(html, "lxml")


def get_clean_lines(soup: BeautifulSoup) -> list[str]:
    """
    Converts a BeautifulSoup page into clean non-empty text lines.
    """
    text = soup.get_text("\n", strip=True)
    return [line.strip() for line in text.splitlines() if line.strip()]


def save_debug_text(lines: list[str], season: str, game_id: str) -> Path:
    """
    Saves cleaned text lines for debugging parser behavior.
    """
    out_dir = INTERIM_TEXT_DIR / season.replace("-", "_")
    out_dir.mkdir(parents=True, exist_ok=True)

    out_path = out_dir / f"{game_id}.txt"

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return out_path


def extract_title_text(soup: BeautifulSoup) -> str | None:
    """
    Extracts page title if available.
    """
    title = soup.select_one("title")
    if title:
        return title.get_text(" ", strip=True)

    h1 = soup.select_one("h1")
    if h1:
        return h1.get_text(" ", strip=True)

    return None


def trim_to_boxscore_content(lines: list[str], schedule_row: pd.Series) -> list[str]:
    """
    Removes site header/navigation text before the actual box score content.

    The goterriers.com page often includes site-wide Upcoming/Results widgets
    before the actual box score. We trim to the matchup line when possible.

    Examples:
        LIU vs Boston University
        Boston University vs UMass
    """
    opponent_clean = str(schedule_row.get("opponent_clean"))

    possible_matchup_lines = [
        f"{opponent_clean} vs Boston University",
        f"Boston University vs {opponent_clean}",
    ]

    for i, line in enumerate(lines):
        if line in possible_matchup_lines:
            return lines[i:]

    return lines


# ---------------------------------------------------------------------
# Type/parsing helpers
# ---------------------------------------------------------------------


def is_int_string(value: str) -> bool:
    return bool(re.fullmatch(r"-?\d+", str(value).strip()))


def is_float_string(value: str) -> bool:
    return bool(re.fullmatch(r"-?\d+(\.\d+)?", str(value).strip()))


def is_player_number(value: str) -> bool:
    """
    Player rows usually start with a jersey number.
    """
    return bool(re.fullmatch(r"\d{1,3}", str(value).strip()))


def looks_like_player_name(value: str) -> bool:
    """
    SIDEARM player names in this text often look like:
        Eiserman,Cole
        McDonald,Casey
        Perdion,JR
    """
    value = str(value).strip()

    if value == "TEAM":
        return True

    return "," in value and not is_float_string(value)


def find_line_index(lines: list[str], target: str) -> int | None:
    """
    Finds the first exact index of a line.
    """
    for i, line in enumerate(lines):
        if line == target:
            return i

    return None


def parse_int_or_none(value: str) -> int | None:
    """
    Parses integer strings, returning None for '-' or invalid values.
    """
    value = str(value).strip()

    if value == "-":
        return None

    if is_int_string(value):
        return int(value)

    return None


def is_team_skater_block_header(line: str) -> bool:
    """
    Matches headers like:
        LIU - 2
        Boston University - 4
        #10/11 Boston College - 1
    """
    return bool(re.fullmatch(r".+\s+-\s+\d+", str(line).strip()))


# ---------------------------------------------------------------------
# Game result parser
# ---------------------------------------------------------------------


def extract_game_result(lines: list[str], schedule_row: pd.Series) -> dict:
    """
    Extracts final score from the top box-score scoreboard.

    Expected pattern after trimming to boxscore content:

        LIU vs Boston University
        ...
        LIU
        1-1-0
        2
        Final
        4
        Boston University
        1-0-0

    Or:
        LIU
        2
        Final
        4
        Boston University

    In SIDEARM output, the left-side score is usually before "Final",
    and the right-side score is usually after "Final".

    If BU is home, BU is usually the right-side team.
    If BU is away, BU is usually the left-side team.
    """
    is_home = bool(schedule_row.get("is_home"))

    bu_score = None
    opponent_score = None
    extraction_status = "score_not_found"

    for i, line in enumerate(lines):
        if line != "Final":
            continue

        left_score = None
        for j in range(i - 1, max(-1, i - 10), -1):
            if is_int_string(lines[j]):
                left_score = int(lines[j])
                break

        right_score = None
        for j in range(i + 1, min(len(lines), i + 10)):
            if is_int_string(lines[j]):
                right_score = int(lines[j])
                break

        if left_score is None or right_score is None:
            continue

        if is_home:
            opponent_score = left_score
            bu_score = right_score
        else:
            bu_score = left_score
            opponent_score = right_score

        extraction_status = "score_found"
        break

    if bu_score is not None and opponent_score is not None:
        if bu_score > opponent_score:
            result = "W"
        elif bu_score < opponent_score:
            result = "L"
        else:
            result = "T"
    else:
        result = None

    return {
        "bu_score": bu_score,
        "opponent_score": opponent_score,
        "result": result,
        "score_extraction_status": extraction_status,
    }


# ---------------------------------------------------------------------
# Team stats parser
# ---------------------------------------------------------------------


def extract_team_stats(lines: list[str], schedule_row: pd.Series) -> dict:
    """
    Extracts Team Statistics block.

    Text pattern:

        Team Statistics
        38
        Shots
        32
        .053
        Shots %
        .125
        32
        Faceoffs Won
        30
        ...

    In SIDEARM output, values are generally:
        left team value
        stat label
        right team value

    If BU is home, BU is right side.
    If BU is away, BU is left side.
    """
    idx = find_line_index(lines, "Team Statistics")

    stats = {
        "bu_shots": None,
        "opponent_shots": None,
        "bu_shot_pct": None,
        "opponent_shot_pct": None,
        "bu_faceoffs_won": None,
        "opponent_faceoffs_won": None,
        "bu_faceoff_pct": None,
        "opponent_faceoff_pct": None,
        "bu_saves": None,
        "opponent_saves": None,
        "bu_save_pct": None,
        "opponent_save_pct": None,
        "bu_penalties": None,
        "opponent_penalties": None,
        "bu_penalty_minutes": None,
        "opponent_penalty_minutes": None,
        "bu_blocks": None,
        "opponent_blocks": None,
        "team_stats_extraction_status": "team_stats_not_found",
    }

    if idx is None:
        return stats

    is_home = bool(schedule_row.get("is_home"))

    # Mapping assumes left team is opponent and right team is BU.
    label_to_columns = {
        "Shots": ("opponent_shots", "bu_shots"),
        "Shots %": ("opponent_shot_pct", "bu_shot_pct"),
        "Faceoffs Won": ("opponent_faceoffs_won", "bu_faceoffs_won"),
        "Faceoffs Won %": ("opponent_faceoff_pct", "bu_faceoff_pct"),
        "Saves": ("opponent_saves", "bu_saves"),
        "Saves %": ("opponent_save_pct", "bu_save_pct"),
        "Penalties": ("opponent_penalties", "bu_penalties"),
        "Penalty Minutes": ("opponent_penalty_minutes", "bu_penalty_minutes"),
        "Blocks": ("opponent_blocks", "bu_blocks"),
    }

    # If BU is away, flip meaning of left/right.
    if not is_home:
        label_to_columns = {
            label: (right_col, left_col)
            for label, (left_col, right_col) in label_to_columns.items()
        }

    # Parse local pattern: value, label, value
    for i in range(idx + 1, min(len(lines) - 2, idx + 100)):
        left_value = lines[i]
        label = lines[i + 1]
        right_value = lines[i + 2]

        if label not in label_to_columns:
            continue

        if not (is_float_string(left_value) and is_float_string(right_value)):
            continue

        left_col, right_col = label_to_columns[label]

        if "." in left_value or "." in right_value:
            left_parsed = float(left_value)
            right_parsed = float(right_value)
        else:
            left_parsed = int(left_value)
            right_parsed = int(right_value)

        stats[left_col] = left_parsed
        stats[right_col] = right_parsed

    if stats["bu_shots"] is not None:
        stats["team_stats_extraction_status"] = "team_stats_found"

    return stats


# ---------------------------------------------------------------------
# Power play parser
# ---------------------------------------------------------------------


def extract_power_play_summary(lines: list[str], schedule_row: pd.Series) -> dict:
    """
    Extracts total PP goals/opportunities from lines like:

        LIU - Power Plays
        ...
        10:00
        9
        1
        0/5

        BOS - Power Plays
        ...
        06:33
        7
        1
        2/5
    """
    stats = {
        "bu_pp_goals": None,
        "bu_pp_opportunities": None,
        "opponent_pp_goals": None,
        "opponent_pp_opportunities": None,
        "power_play_extraction_status": "pp_not_found",
    }

    pp_blocks = []

    for i, line in enumerate(lines):
        if not line.endswith(" - Power Plays"):
            continue

        team_label = line.replace(" - Power Plays", "").strip()

        pp_value = None
        for j in range(i + 1, min(len(lines), i + 100)):
            if re.fullmatch(r"\d+/\d+", lines[j]):
                pp_value = lines[j]
                break

        if pp_value:
            goals, opps = pp_value.split("/")
            pp_blocks.append(
                {
                    "team_label": team_label,
                    "goals": int(goals),
                    "opportunities": int(opps),
                }
            )

    for block in pp_blocks:
        team_label = block["team_label"]

        if team_label in BU_TEAM_NAMES:
            stats["bu_pp_goals"] = block["goals"]
            stats["bu_pp_opportunities"] = block["opportunities"]
        else:
            stats["opponent_pp_goals"] = block["goals"]
            stats["opponent_pp_opportunities"] = block["opportunities"]

    if stats["bu_pp_opportunities"] is not None:
        stats["power_play_extraction_status"] = "pp_found"

    return stats


# ---------------------------------------------------------------------
# Goalie parser
# ---------------------------------------------------------------------


def extract_goalie_stats(lines: list[str], schedule_row: pd.Series) -> dict:
    """
    Extracts basic goalie totals from the Goalkeeping Statistics section.

    Expected block:

        LIU - Goalkeeping
        #
        Player
        Dec
        Minutes
        GA
        EN
        1
        2
        3
        Totals
        30
        Duris,Daniel
        L
        57:37
        4
        0
        5
        13
        10
        28

        BOS - Goalkeeping
        ...
    """
    stats = {
        "bu_goalie": None,
        "opponent_goalie": None,
        "bu_goalie_decision": None,
        "opponent_goalie_decision": None,
        "bu_goalie_minutes": None,
        "opponent_goalie_minutes": None,
        "bu_goals_against": None,
        "opponent_goals_against": None,
        "bu_goalie_saves": None,
        "opponent_goalie_saves": None,
        "goalie_extraction_status": "goalie_not_found",
    }

    idx = find_line_index(lines, "Goalkeeping Statistics")
    if idx is None:
        return stats

    goalie_blocks = []

    for i in range(idx + 1, min(len(lines), idx + 250)):
        if not lines[i].endswith(" - Goalkeeping"):
            continue

        team_label = lines[i].replace(" - Goalkeeping", "").strip()

        row_start = None

        # Find first player row after header.
        for j in range(i + 1, min(len(lines), i + 60)):
            if is_int_string(lines[j]) and j + 9 < len(lines):
                if "," in lines[j + 1] or lines[j + 1] == "TEAM":
                    row_start = j
                    break

        if row_start is None:
            continue

        try:
            player = lines[row_start + 1]
            decision = lines[row_start + 2]
            minutes = lines[row_start + 3]
            goals_against = int(lines[row_start + 4])
            saves_total = int(lines[row_start + 9])

            goalie_blocks.append(
                {
                    "team_label": team_label,
                    "player": player,
                    "decision": decision,
                    "minutes": minutes,
                    "goals_against": goals_against,
                    "saves": saves_total,
                }
            )
        except (IndexError, ValueError):
            continue

    for block in goalie_blocks:
        team_label = block["team_label"]

        if team_label in BU_TEAM_NAMES:
            stats["bu_goalie"] = block["player"]
            stats["bu_goalie_decision"] = block["decision"]
            stats["bu_goalie_minutes"] = block["minutes"]
            stats["bu_goals_against"] = block["goals_against"]
            stats["bu_goalie_saves"] = block["saves"]
        else:
            stats["opponent_goalie"] = block["player"]
            stats["opponent_goalie_decision"] = block["decision"]
            stats["opponent_goalie_minutes"] = block["minutes"]
            stats["opponent_goals_against"] = block["goals_against"]
            stats["opponent_goalie_saves"] = block["saves"]

    if stats["bu_goalie"] is not None:
        stats["goalie_extraction_status"] = "goalie_found"

    return stats


# ---------------------------------------------------------------------
# Player stat parser
# ---------------------------------------------------------------------


def parse_player_stat_block(
    lines: list[str],
    start_idx: int,
    team_name: str,
    team_type: str,
    game_context: dict,
) -> list[dict]:
    """
    Parses one skater stat block.

    Expected block:

        Boston University - 4
        Shots by Period
        #
        Player
        G
        A
        1
        2
        3
        Totals
        +/-
        FO
        Pen
        BLK
        34
        Eiserman,Cole
        2
        0
        3
        2
        2
        7
        0
        -
        -
        0
        ...
        Totals
    """
    players = []

    data_start = None

    # Find the beginning of player rows after BLK.
    for i in range(start_idx, min(len(lines), start_idx + 50)):
        if lines[i] == "BLK":
            data_start = i + 1
            break

    if data_start is None:
        return players

    i = data_start

    while i < len(lines):
        line = lines[i]

        if line == "Totals":
            break

        if line == "Goalkeeping Statistics":
            break

        if line.endswith(" - Goalkeeping"):
            break

        if not is_player_number(line):
            i += 1
            continue

        # Expected 12-field player row:
        # number, player, G, A, p1, p2, p3, total, plus_minus, FO, Pen, BLK
        if i + 11 >= len(lines):
            break

        number = lines[i]
        player_name = lines[i + 1]

        if not looks_like_player_name(player_name):
            i += 1
            continue

        try:
            goals = int(lines[i + 2])
            assists = int(lines[i + 3])
            shots_p1 = int(lines[i + 4])
            shots_p2 = int(lines[i + 5])
            shots_p3 = int(lines[i + 6])
            shots_total = int(lines[i + 7])
            plus_minus = int(lines[i + 8])
            faceoffs = lines[i + 9]
            penalties = lines[i + 10]
            blocks = int(lines[i + 11])
        except ValueError:
            i += 1
            continue

        row = {
            **game_context,
            "team": team_name,
            "team_type": team_type,
            "player_number": number,
            "player_name": player_name,
            "goals": goals,
            "assists": assists,
            "points": goals + assists,
            "shots_p1": shots_p1,
            "shots_p2": shots_p2,
            "shots_p3": shots_p3,
            "shots_total": shots_total,
            "plus_minus": plus_minus,
            "faceoffs": None if faceoffs == "-" else faceoffs,
            "penalties": None if penalties == "-" else penalties,
            "blocks": blocks,
        }

        players.append(row)

        i += 12

    return players


def extract_player_game_stats(
    lines: list[str],
    schedule_row: pd.Series,
    season: str,
) -> list[dict]:
    """
    Extracts skater/player stats for BU and opponent from one box score.
    """
    game_context = {
        "game_id": schedule_row.get("game_id"),
        "season": season,
        "date": schedule_row.get("date"),
        "opponent_clean": schedule_row.get("opponent_clean"),
    }

    player_rows = []

    for i, line in enumerate(lines):
        if not is_team_skater_block_header(line):
            continue

        # The next line should be Shots by Period for a skater table.
        if i + 1 >= len(lines) or lines[i + 1] != "Shots by Period":
            continue

        team_name = re.sub(r"\s+-\s+\d+$", "", line).strip()

        if team_name == "Boston University":
            team_type = "bu"
        else:
            team_type = "opponent"

        block_rows = parse_player_stat_block(
            lines=lines,
            start_idx=i,
            team_name=team_name,
            team_type=team_type,
            game_context=game_context,
        )

        player_rows.extend(block_rows)

    return player_rows


# ---------------------------------------------------------------------
# One-game parse orchestrator
# ---------------------------------------------------------------------


def parse_one_boxscore_from_lines(
    path: Path,
    soup: BeautifulSoup,
    lines: list[str],
    schedule_row: pd.Series,
    season: str,
) -> dict:
    """
    Parses one box score into one game/team-level row.
    """
    game_id = schedule_row["game_id"]

    debug_text_path = INTERIM_TEXT_DIR / season.replace("-", "_") / f"{game_id}.txt"
    title_text = extract_title_text(soup)

    parsed = {
        "game_id": game_id,
        "season": season,
        "date": schedule_row.get("date"),
        "opponent": schedule_row.get("opponent"),
        "opponent_clean": schedule_row.get("opponent_clean"),
        "is_home": schedule_row.get("is_home"),
        "is_neutral": schedule_row.get("is_neutral"),
        "box_score_url": schedule_row.get("box_score_url"),
        "html_file": str(path),
        "debug_text_file": str(debug_text_path),
        "page_title": title_text,
    }

    parsed.update(extract_game_result(lines, schedule_row))
    parsed.update(extract_team_stats(lines, schedule_row))
    parsed.update(extract_power_play_summary(lines, schedule_row))
    parsed.update(extract_goalie_stats(lines, schedule_row))

    return parsed


def parse_boxscores(season: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Parses all saved box score HTML files for a season.

    Returns:
        game_df: one row per game with result/team stats
        player_df: one row per player per game
    """
    schedule_df = load_schedule(season)

    html_dir = RAW_BOXSCORE_DIR / season.replace("-", "_")

    if not html_dir.exists():
        raise FileNotFoundError(
            f"Could not find raw boxscore directory: {html_dir}. Run bu_boxscore_scraper.py first."
        )

    game_rows = []
    player_rows = []

    for _, schedule_row in schedule_df.iterrows():
        game_id = schedule_row["game_id"]
        html_path = html_dir / f"{game_id}.html"

        if not html_path.exists():
            print(f"Missing HTML for {game_id}: {html_path}")
            continue

        print(f"Parsing {html_path}")

        soup = html_to_soup(html_path)
        lines = get_clean_lines(soup)
        lines = trim_to_boxscore_content(lines, schedule_row)

        save_debug_text(lines, season, game_id)

        game_parsed = parse_one_boxscore_from_lines(
            path=html_path,
            soup=soup,
            lines=lines,
            schedule_row=schedule_row,
            season=season,
        )
        game_rows.append(game_parsed)

        players = extract_player_game_stats(lines, schedule_row, season)
        player_rows.extend(players)

    game_df = pd.DataFrame(game_rows)
    player_df = pd.DataFrame(player_rows)

    return game_df, player_df


# ---------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------


def save_outputs(
    game_df: pd.DataFrame,
    player_df: pd.DataFrame,
    season: str,
) -> tuple[Path, Path, Path]:
    """
    Saves parsed game, team, and player stats.
    """
    PROCESSED_BOXSCORE_DIR.mkdir(parents=True, exist_ok=True)

    game_results_path = PROCESSED_BOXSCORE_DIR / f"game_results_{season.replace('-', '_')}.csv"

    team_stats_path = PROCESSED_BOXSCORE_DIR / f"team_game_stats_{season.replace('-', '_')}.csv"

    player_stats_path = PROCESSED_BOXSCORE_DIR / f"player_game_stats_{season.replace('-', '_')}.csv"

    game_result_cols = [
        "game_id",
        "season",
        "date",
        "opponent",
        "opponent_clean",
        "is_home",
        "is_neutral",
        "bu_score",
        "opponent_score",
        "result",
        "score_extraction_status",
        "box_score_url",
    ]

    team_stat_cols = [
        "game_id",
        "season",
        "date",
        "opponent_clean",
        "bu_shots",
        "opponent_shots",
        "bu_shot_pct",
        "opponent_shot_pct",
        "bu_faceoffs_won",
        "opponent_faceoffs_won",
        "bu_faceoff_pct",
        "opponent_faceoff_pct",
        "bu_saves",
        "opponent_saves",
        "bu_save_pct",
        "opponent_save_pct",
        "bu_penalties",
        "opponent_penalties",
        "bu_penalty_minutes",
        "opponent_penalty_minutes",
        "bu_blocks",
        "opponent_blocks",
        "bu_pp_goals",
        "bu_pp_opportunities",
        "opponent_pp_goals",
        "opponent_pp_opportunities",
        "bu_goalie",
        "opponent_goalie",
        "bu_goalie_decision",
        "opponent_goalie_decision",
        "bu_goalie_minutes",
        "opponent_goalie_minutes",
        "bu_goals_against",
        "opponent_goals_against",
        "bu_goalie_saves",
        "opponent_goalie_saves",
        "team_stats_extraction_status",
        "power_play_extraction_status",
        "goalie_extraction_status",
    ]

    player_stat_cols = [
        "game_id",
        "season",
        "date",
        "opponent_clean",
        "team",
        "team_type",
        "player_number",
        "player_name",
        "goals",
        "assists",
        "points",
        "shots_p1",
        "shots_p2",
        "shots_p3",
        "shots_total",
        "plus_minus",
        "faceoffs",
        "penalties",
        "blocks",
    ]

    existing_game_cols = [col for col in game_result_cols if col in game_df.columns]
    existing_team_cols = [col for col in team_stat_cols if col in game_df.columns]
    existing_player_cols = [col for col in player_stat_cols if col in player_df.columns]

    game_df[existing_game_cols].to_csv(game_results_path, index=False)
    game_df[existing_team_cols].to_csv(team_stats_path, index=False)

    if player_df.empty:
        player_df.to_csv(player_stats_path, index=False)
    else:
        player_df[existing_player_cols].to_csv(player_stats_path, index=False)

    return game_results_path, team_stats_path, player_stats_path


# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse saved BU box score HTML files")
    parser.add_argument("--season", default="2025-26", help="Season, e.g. 2025-26")

    args = parser.parse_args()

    print(f"Parsing BU box scores for season {args.season}...")
    game_df, player_df = parse_boxscores(args.season)

    if game_df.empty:
        print("WARNING: No box scores parsed.")
        return

    game_results_path, team_stats_path, player_stats_path = save_outputs(
        game_df=game_df,
        player_df=player_df,
        season=args.season,
    )

    print()
    print(f"Saved game results to {game_results_path}")
    print(f"Saved team game stats to {team_stats_path}")
    print(f"Saved player game stats to {player_stats_path}")
    print()

    preview_cols = [
        "game_id",
        "opponent_clean",
        "bu_score",
        "opponent_score",
        "result",
        "bu_shots",
        "opponent_shots",
        "bu_pp_goals",
        "bu_pp_opportunities",
        "opponent_pp_goals",
        "opponent_pp_opportunities",
    ]

    existing_preview_cols = [col for col in preview_cols if col in game_df.columns]

    print("Game/team preview:")
    print(game_df[existing_preview_cols].head())

    print()
    print(f"Parsed {len(player_df)} player-game rows.")

    if not player_df.empty:
        print()
        print("Player preview:")
        print(player_df.head())


if __name__ == "__main__":
    main()
