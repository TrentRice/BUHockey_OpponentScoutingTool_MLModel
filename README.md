## Project Status

**BU Hockey Opponent Scouting Tool** automates the repetitive statistical groundwork behind pre-game opponent scouting reports for Boston University Men's Hockey, using only public data. The end goal is a short pre-game report for BU's next opponent covering schedule and results, recent form, special teams, top scorers, goalie usage, and eventually model-based predictions.

### Completed

**League-wide schedule/results scraper (College Hockey News)**
Scouting an opponent requires that opponent's full season. `chn_team_schedule_scraper.py` collects schedules and results for all 63 Division I teams:

- Raw HTML is cached in `data/raw/chn/schedules/`, so re-runs don't re-download
- Outputs one CSV per team plus a combined `all_team_schedules_2025_26.csv`
- Fields include date, result, score, home/away/neutral, opponent, overtime, exhibition, conference flags, event notes (Beanpot, tournaments), and box score / metrics URLs
- Includes team and opponent conference, so same-league games can be identified

### In Progress

**CHN box score ingestion for all games.** Download and parse CHN box scores for every game to get team and player stats (shots, special teams, goalies, scoring) for each opponent's full season.

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
