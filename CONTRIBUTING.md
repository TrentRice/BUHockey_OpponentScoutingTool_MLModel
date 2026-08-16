# Contributing / Dev Setup

Notes for working on this project — written for future-you as much as anyone else.

## First-time setup

```bash
git clone <repo-url>
cd <repo-name>
cp .env.example .env          # fill in real values
make setup                    # installs deps + pre-commit hooks
make up                       # starts API + MLflow + Postgres locally
```

## Day-to-day commands

| Command | What it does |
|---|---|
| `make lint` | Check code style |
| `make format` | Auto-fix formatting |
| `make test` | Run test suite |
| `make ingest` | Run data ingestion |
| `make features` | Build features from processed data |
| `make train MODEL=gbm` | Train a specific model |
| `make serve` | Run the API locally without Docker |
| `make down` | Stop the local stack |

## Branching convention

- `main` — always deployable
- `feature/<short-description>` — new work
- `fix/<short-description>` — bug fixes

Open a PR into `main`; CI must pass before merging.

## Commit messages

Keep them descriptive and in imperative mood: `Add calibration check to evaluation step`,
not `fixed stuff`.

## Adding a new dependency

```bash
uv add <package>              # or: poetry add <package>
```

Commit the updated `pyproject.toml` (and lockfile) in the same commit as the code that
needs it.

## Before opening a PR

- [ ] `make lint` and `make test` pass locally
- [ ] Updated `README.md` if you changed setup steps or architecture
- [ ] Updated `docs/model_card_template.md`-based model card if you trained/promoted a new model
