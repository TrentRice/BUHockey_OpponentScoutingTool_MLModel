## Project Status

**BU Hockey Opponent Scouting Tool** automates the repetitive statistical groundwork behind pre-game opponent scouting reports for Boston University Men's Hockey, using only public data. The end goal is a short pre-game report for BU's next opponent covering schedule and results, recent form, special teams, top scorers, goalie usage, and eventually model-based predictions.

### Completed

**1. BU official data pipeline (2025-26), validated**
Schedule and box scores from goterriers.com:

`BU schedule page -> raw JSON -> processed schedule CSV -> box score HTML -> game results / team game stats / player game stats CSVs -> validation`

- Handles neutral-site games, team-name aliases (e.g. "Boston U.", "Michigan St."), and overtime columns in player tables
- Validation checks that player goals, shots, and blocks sum to team totals for both teams; all core checks pass

**2. League-wide schedule/results scraper (College Hockey News)**
BU's box scores only cover games BU played, so scouting an opponent requires that opponent's full season. `chn_team_schedule_scraper.py` collects schedules and results for all 63 Division I teams:

- Raw HTML is cached in `data/raw/chn/schedules/`, so re-runs don't re-download
- Outputs one CSV per team plus a combined `all_team_schedules_2025_26.csv`
- Fields include date, result, score, home/away/neutral, opponent, overtime, exhibition, conference flags, event notes (Beanpot, tournaments), and box score / metrics URLs
- Includes team and opponent conference, so same-league games can be identified

### In Progress

**CHN box score ingestion for all games.** Download and parse CHN box scores for every game to get team and player stats (shots, special teams, goalies, scoring) for each opponent's full season, not just their games against BU.

### Planned

- **Feature building** (`src/features/`): record, last-5 form, goals for/against, shot differential, special teams, goalie workload, top scorers, point pace
- **Report generation** (`src/reports/`): HTML or PDF opponent report for BU's next game
- **Modeling** (`src/models/`): game outcome predictions

### Tech Stack
Python, requests, BeautifulSoup (lxml), pandas, managed with uv.

### Project Structure
```
data/{raw,interim,processed}/
src/{ingestion,evaluation,features,models,serving}/
```
