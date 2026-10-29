## Project Status

[![CI](https://github.com/TrentRice/BUHockey_OpponentScoutingTool_MLModel/actions/workflows/ci.yml/badge.svg)](https://github.com/TrentRice/BUHockey_OpponentScoutingTool_MLModel/actions/workflows/ci.yml) [Data validation report](docs/data_validation.md)

**BU Hockey Opponent Scouting Tool** automates the repetitive statistical groundwork behind pre-game opponent scouting reports for Boston University Men's Hockey, using only public data. The end goal is a short pre-game report for BU's next opponent covering schedule and results, recent form, special teams, top scorers, goalie usage, and eventually model-based predictions.

### Completed

**League-wide schedule/results scraper (College Hockey News)**
Scouting an opponent requires that opponent's full season. `chn_team_schedule_scraper.py` collects schedules and results for all 63 Division I teams:

- Raw HTML is cached in `data/raw/chn/schedules/` (not committed), so re-runs don't re-download
- Outputs `data/processed/chn/all_games_<season>.csv`, one row per game with home and away team; filter on `home_team`/`away_team` for one team's season
- Fields include date, result, score, home/away/neutral, opponent, overtime, exhibition, conference flags, event notes (Beanpot, tournaments), and box score / metrics URLs
- Includes team and opponent conference, so same-league games can be identified

**Box score ingestion (College Hockey News)**
Two steps, run after the schedule scraper:

- `chn_boxscore_downloader.py` saves each played game's box score page to `data/raw/chn/boxscores/<season>/` (incremental, so in-season re-runs only fetch new games)
- `chn_boxscore_parser.py` turns those pages into `data/processed/chn/box_*_<season>.csv`:
  - `box_games`: venue, attendance, officials, OT periods, shootout result (score and game flags are in `all_games`)
  - `box_team_stats`: goals/shots by period, PP/PK, PIM, faceoffs, xG, empty-net time (one row per team per game)
  - `box_skaters`: G, A, +/-, shots, TOI, PIM, faceoffs, F/D position
  - `box_goalies`: saves, GA, TOI, starter flag
  - `box_goals`: period, time, strength (EV/PP/SH/EN/EA/4x4), scorer, assists
  - `box_penalties`: period, time, player, minutes, infraction
- The parser checks that goals, GA, and penalty counts agree across sections and with the schedule; mismatches go to `box_parse_issues_<season>.csv`

```
uv run python -m src.ingestion.chn_team_schedule_scraper
uv run python -m src.ingestion.chn_boxscore_downloader
uv run python -m src.ingestion.chn_boxscore_parser
```

**Data validation**
Every season on disk (2021-22 on) is checked by `src/evaluation/validate_chn_data.py`, which writes [docs/data_validation.md](docs/data_validation.md). It cross-checks each box score against the separate schedule pages (final score, overtime) and checks that the sections of each box score agree (period totals, scoring summary, skater goals, penalties, goalies, faceoffs). Every remaining mismatch is an error on the source page, listed in the report with a link to it. CI re-runs the checks on every push. Re-run locally with `make validate`.

### Planned

- **Feature building** (`src/features/`): record, last-5 form, goals for/against, shot differential, special teams, goalie workload, top scorers, point pace
- **Report generation** (`src/reports/`): HTML or PDF opponent report for BU's next game
- **Modeling** (`src/models/`): game outcome predictions for each BU game

### Tech Stack
Python, requests, BeautifulSoup (lxml), pandas, managed with uv.

### Project Structure
```
data/{raw,interim,processed}/
src/{ingestion,evaluation,features,models,serving}/
```
