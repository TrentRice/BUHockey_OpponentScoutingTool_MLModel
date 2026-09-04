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

BU_TEAM_NAMES = {
    "Boston University",
    "Boston U.",
    "Boston U",
    "BOS",
    "BU",
}


# ---------------------------------------------------------------------
# General file/loading helpers
# ---------------------------------------------------------------------


def load_schedule(season: str) -> pd.DataFrame:
    path = PROCESSED_DATA_DIR / f"bu_schedule_{season.replace('-', '_')}.csv"

    if not path.exists():
        raise FileNotFoundError(
            f"Could not find processed schedule file: {path}. Run process_bu_schedule.py first."
        )

    return pd.read_csv(path)


def html_to_soup(path: Path) -> BeautifulSoup:
    with open(path, "r", encoding="utf-8") as f:
        html = f.read()

    return BeautifulSoup(html, "lxml")


def get_clean_lines(soup: BeautifulSoup) -> list[str]:
    text = soup.get_text("\n", strip=True)
    return [line.strip() for line in text.splitlines() if line.strip()]


def save_debug_text(lines: list[str], season: str, game_id: str) -> Path:
    out_dir = INTERIM_TEXT_DIR / season.replace("-", "_")
    out_dir.mkdir(parents=True, exist_ok=True)

    out_path = out_dir / f"{game_id}.txt"

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return out_path


def extract_title_text(soup: BeautifulSoup) -> str | None:
    title = soup.select_one("title")
    if title:
        return title.get_text(" ", strip=True)

    h1 = soup.select_one("h1")
    if h1:
        return h1.get_text(" ", strip=True)

    return None


def normalize_team_name(name: str | None) -> str | None:
    """
    Normalizes common team aliases in SIDEARM box scores.
    """
    if name is None:
        return None

    text = str(name).strip()

    if text in BU_TEAM_NAMES:
        return "Boston University"

    # Common abbreviation normalization from page titles/table headers.
    replacements = {
        "Boston U.": "Boston University",
        "Boston U": "Boston University",
        "Michigan St.": "Michigan State",
        "UConn": "UConn",
    }

    return replacements.get(text, text)


def is_bu_team_name(name: str | None) -> bool:
    return normalize_team_name(name) == "Boston University"


def trim_to_boxscore_content(lines: list[str], schedule_row: pd.Series) -> list[str]:
    """
    Removes site header/navigation text before the actual box score content.

    The page often includes site-wide Upcoming/Results widgets before the
    actual box score. We try several robust anchors.

    Examples:
        LIU vs Boston University
        Michigan St. vs Boston U.
        Boston University vs UMass
    """

    # Best case: the actual box score area usually includes this nav cluster:
    #   Box Score
    #   Play-by-play
    #   Individual Stats
    #   Team Stats
    #
    # But "Box Score" also appears in site widgets, so require the nearby cluster.
    for i in range(len(lines) - 4):
        if (
            lines[i] == "Box Score"
            and lines[i + 1] == "Play-by-play"
            and lines[i + 2] == "Individual Stats"
            and lines[i + 3] == "Team Stats"
        ):
            # The matchup line is usually a few lines before this.
            start = max(0, i - 3)
            return lines[start:]

    # Second case: look for a matchup line containing vs and Boston.
    for i, line in enumerate(lines):
        lowered = line.lower()
        if " vs " in lowered and "boston" in lowered:
            return lines[i:]

    # Third case: use schedule opponent, but allow common abbreviations.
    opponent_clean = str(schedule_row.get("opponent_clean"))

    possible_matchup_lines = [
        f"{opponent_clean} vs Boston University",
        f"{opponent_clean} vs Boston U.",
        f"Boston University vs {opponent_clean}",
        f"Boston U. vs {opponent_clean}",
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
    return bool(re.fullmatch(r"\d{1,3}", str(value).strip()))


def looks_like_player_name(value: str) -> bool:
    value = str(value).strip()

    if value == "TEAM":
        return True

    return "," in value and not is_float_string(value)


def find_line_index(lines: list[str], target: str) -> int | None:
    for i, line in enumerate(lines):
        if line == target:
            return i

    return None


def parse_int_or_none(value: str) -> int | None:
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
        Boston U. - 3
        Michigan St. - 4
        #10/11 Boston College - 1
        (3) UConn - 5
    """
    line = str(line).strip()

    if line in {"Power Play Summary", "Penalty Summary", "Goalkeeping Statistics"}:
        return False

    return bool(re.fullmatch(r".+\s+-\s+\d+", line))


# ---------------------------------------------------------------------
# Game result parser
# ---------------------------------------------------------------------


def extract_game_result(lines: list[str], schedule_row: pd.Series) -> dict:
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

    if not is_home:
        label_to_columns = {
            label: (right_col, left_col)
            for label, (left_col, right_col) in label_to_columns.items()
        }

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
                    "team_label": normalize_team_name(team_label),
                    "goals": int(goals),
                    "opportunities": int(opps),
                }
            )

    for block in pp_blocks:
        team_label = block["team_label"]

        if is_bu_team_name(team_label):
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

    for i in range(idx + 1, min(len(lines), idx + 300)):
        if not lines[i].endswith(" - Goalkeeping"):
            continue

        team_label = lines[i].replace(" - Goalkeeping", "").strip()
        normalized_team_label = normalize_team_name(team_label)

        row_start = None

        for j in range(i + 1, min(len(lines), i + 70)):
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

            # In 3-period games, saves total is row_start + 9.
            # In OT games, saves total can be row_start + 10 because there is a 4th period column.
            # More robustly, find the "Totals" header in this goalie block.
            totals_header_idx = None
            for k in range(i + 1, min(len(lines), i + 30)):
                if lines[k] == "Totals":
                    totals_header_idx = k
                    break

            if totals_header_idx is not None:
                # Offset from start of goalie data row to Totals column.
                # Header begins at "#", then Player, Dec, Minutes, GA, EN, periods..., Totals.
                header_start_idx = None
                for k in range(i + 1, totals_header_idx + 1):
                    if lines[k] == "#":
                        header_start_idx = k
                        break

                if header_start_idx is not None:
                    totals_offset = totals_header_idx - header_start_idx
                    saves_total = int(lines[row_start + totals_offset])
                else:
                    saves_total = int(lines[row_start + 9])
            else:
                saves_total = int(lines[row_start + 9])

            goalie_blocks.append(
                {
                    "team_label": normalized_team_label,
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

        if is_bu_team_name(team_label):
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


def find_team_header_before_shots_table(
    lines: list[str],
    shots_idx: int,
) -> tuple[str | None, int | None]:
    """
    Given an index where lines[shots_idx] == 'Shots by Period',
    look backward for a team skater table header like:

        Michigan St. - 4
        Boston U. - 3
    """
    for j in range(shots_idx - 1, max(-1, shots_idx - 10), -1):
        line = lines[j]

        if is_team_skater_block_header(line):
            team_name = re.sub(r"\s+-\s+\d+$", "", line).strip()
            return team_name, j

    return None, None


def looks_like_skater_shots_table(lines: list[str], shots_idx: int) -> bool:
    """
    Confirms that a 'Shots by Period' occurrence is a skater table,
    not the top scoreboard/team-by-period table.
    """
    window = lines[shots_idx : min(len(lines), shots_idx + 35)]

    required = {"#", "Player", "G", "A", "Totals", "+/-", "FO", "Pen", "BLK"}

    return required.issubset(set(window))


def parse_player_stat_block(
    lines: list[str],
    start_idx: int,
    team_name: str,
    team_type: str,
    game_context: dict,
) -> list[dict]:
    """
    Parses one skater stat block using dynamic header detection.

    Handles normal 3-period games:

        # Player G A 1 2 3 Totals +/- FO Pen BLK

    and overtime games:

        # Player G A 1 2 3 4 Totals +/- FO Pen BLK
    """
    players = []

    shots_idx = None
    for i in range(start_idx, min(len(lines), start_idx + 20)):
        if lines[i] == "Shots by Period":
            shots_idx = i
            break

    if shots_idx is None:
        return players

    blk_idx = None
    for i in range(shots_idx, min(len(lines), shots_idx + 50)):
        if lines[i] == "BLK":
            blk_idx = i
            break

    if blk_idx is None:
        return players

    headers = lines[shots_idx + 1 : blk_idx + 1]
    data_start = blk_idx + 1

    try:
        goals_pos = headers.index("G")
        assists_pos = headers.index("A")
        totals_pos = headers.index("Totals")
        plus_minus_pos = headers.index("+/-")
        fo_pos = headers.index("FO")
        pen_pos = headers.index("Pen")
        blk_pos = headers.index("BLK")
    except ValueError:
        return players

    row_len = len(headers)

    i = data_start

    while i < len(lines):
        line = lines[i]

        if line == "Totals":
            break

        if line == "Goalkeeping Statistics":
            break

        if line.endswith(" - Goalkeeping"):
            break

        if line == "Faceoff Statistics":
            break

        if not is_player_number(line):
            i += 1
            continue

        if i + row_len - 1 >= len(lines):
            break

        row_values = lines[i : i + row_len]

        number = row_values[0]
        player_name = row_values[1]

        if not looks_like_player_name(player_name):
            i += 1
            continue

        try:
            goals = int(row_values[goals_pos])
            assists = int(row_values[assists_pos])
            shots_total = int(row_values[totals_pos])
            plus_minus = int(row_values[plus_minus_pos])
            faceoffs = row_values[fo_pos]
            penalties = row_values[pen_pos]
            blocks = int(row_values[blk_pos])
        except (ValueError, IndexError):
            i += 1
            continue

        period_headers = headers[assists_pos + 1 : totals_pos]
        period_values = row_values[assists_pos + 1 : totals_pos]

        period_shots = {}
        for header, value in zip(period_headers, period_values):
            header_text = str(header).strip().lower()

            if header_text.isdigit():
                col_name = f"shots_p{header_text}"
            elif header_text in {"ot", "overtime"}:
                col_name = "shots_ot"
            else:
                col_name = f"shots_{header_text}"

            try:
                period_shots[col_name] = int(value)
            except ValueError:
                period_shots[col_name] = None

        normalized_team = normalize_team_name(team_name)

        row = {
            **game_context,
            "team": normalized_team,
            "team_raw": team_name,
            "team_type": team_type,
            "player_number": number,
            "player_name": player_name,
            "goals": goals,
            "assists": assists,
            "points": goals + assists,
            "shots_total": shots_total,
            "plus_minus": plus_minus,
            "faceoffs": None if faceoffs == "-" else faceoffs,
            "penalties": None if penalties == "-" else penalties,
            "blocks": blocks,
            **period_shots,
        }

        players.append(row)

        i += row_len

    return players


def extract_player_game_stats(
    lines: list[str],
    schedule_row: pd.Series,
    season: str,
) -> list[dict]:
    """
    Extracts skater/player stats for BU and opponent from one box score.

    This searches from each 'Shots by Period' table, verifies that it is
    a skater table, then looks backward for the team-score header.
    """
    game_context = {
        "game_id": schedule_row.get("game_id"),
        "season": season,
        "date": schedule_row.get("date"),
        "opponent_clean": schedule_row.get("opponent_clean"),
    }

    player_rows = []

    for i, line in enumerate(lines):
        if line != "Shots by Period":
            continue

        if not looks_like_skater_shots_table(lines, i):
            continue

        team_name, header_idx = find_team_header_before_shots_table(lines, i)

        if team_name is None or header_idx is None:
            continue

        normalized_team = normalize_team_name(team_name)

        if is_bu_team_name(normalized_team):
            team_type = "bu"
        else:
            team_type = "opponent"

        block_rows = parse_player_stat_block(
            lines=lines,
            start_idx=header_idx,
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

    return pd.DataFrame(game_rows), pd.DataFrame(player_rows)


# ---------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------


def save_outputs(
    game_df: pd.DataFrame,
    player_df: pd.DataFrame,
    season: str,
) -> tuple[Path, Path, Path]:
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

    base_player_stat_cols = [
        "game_id",
        "season",
        "date",
        "opponent_clean",
        "team",
        "team_raw",
        "team_type",
        "player_number",
        "player_name",
        "goals",
        "assists",
        "points",
    ]

    shot_period_cols = []
    if not player_df.empty:
        shot_period_cols = sorted(
            [col for col in player_df.columns if col.startswith("shots_p") or col == "shots_ot"]
        )

    ending_player_stat_cols = [
        "shots_total",
        "plus_minus",
        "faceoffs",
        "penalties",
        "blocks",
    ]

    player_stat_cols = base_player_stat_cols + shot_period_cols + ending_player_stat_cols

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
