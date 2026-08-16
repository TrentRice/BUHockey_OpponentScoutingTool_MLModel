# ML Project Template

A reusable scaffold for end-to-end ML projects: data pipeline → experimentation →
containerized deployment → CI/CD → monitoring. Use this as a starting point rather
than rebuilding the same MLOps skeleton from scratch each time.

> Click **"Use this template"** on GitHub to start a new project from this repo.
> See [`TEMPLATE_USAGE.md`](TEMPLATE_USAGE.md) for the setup checklist.

---

## What this template gives you

- A clean separation between data ingestion, feature engineering, modeling, and
  serving — so each stage can be developed, tested, and swapped independently
- Experiment tracking wired up out of the box (MLflow: tracking server + Postgres
  backend + model registry)
- A containerized FastAPI serving app
- A CI/CD pipeline (GitHub Actions → Docker → ECR → ECS Fargate) so "deploy to
  production" isn't an afterthought
- A monitoring stage as a first-class part of the project, not something bolted on
  after the fact

---

## Architecture

```
Data source(s)
      │
      ▼
data/raw → data/interim → data/processed
      │
      ▼
Feature engineering
      │
      ▼
Model training  ──────────►  Experiment tracking (MLflow)
      │                              │
      ▼                              ▼
Model evaluation              Model registry
                                      │
                                      ▼
                          Serving app (FastAPI, Docker)
                                      │
                                      ▼
                   CI/CD (GitHub Actions) → ECR → ECS Fargate
                                      │
                                      ▼
                          Monitoring (drift / performance over time)
```

---

## Repo structure

```
project-name/
├── .github/workflows/     # CI (lint/test/build) and CD (deploy) pipelines
├── data/                  # raw/interim/processed — gitignored, populated by ingestion scripts
├── src/
│   ├── ingestion/         # data collection / loading
│   ├── features/          # feature engineering
│   ├── models/            # training scripts
│   ├── evaluation/        # metrics, validation, backtesting
│   └── serving/           # FastAPI app
├── notebooks/             # exploration only — nothing production runs from here
├── tests/                 # mirrors src/ structure
├── monitoring/            # scheduled drift / performance checks
├── infra/terraform/       # AWS infra as code (ECR, ECS, ALB)
├── Dockerfile
├── docker-compose.yml     # local: API + MLflow server + Postgres
├── pyproject.toml
└── README.md
```

---

## Setup

### Prerequisites
- Python 3.11+
- Docker + Docker Compose
- [uv](https://github.com/astral-sh/uv) or Poetry
- AWS CLI configured, if deploying (not needed for local dev)

### Local development

```bash
git clone https://github.com/<your-username>/<project-name>.git
cd <project-name>

uv sync            # or: poetry install

# spin up the local stack — API + MLflow tracking server + Postgres
docker compose up --build
```

| Service | URL | Purpose |
|---|---|---|
| FastAPI app | http://localhost:8000/docs | Prediction API (Swagger UI) |
| MLflow UI | http://localhost:5000 | Experiment tracking / model registry |
| Postgres | localhost:5432 | MLflow backend store |

### Pipeline commands (rename/replace with your actual scripts)

```bash
python -m src.ingestion.load_data
python -m src.features.build_features
python -m src.models.train --model <model_name>
pytest
```

---

## CI/CD

- **`ci.yml`** — runs on every PR: lint (`ruff`), unit tests (`pytest`), Docker build,
  smoke test against the built image.
- **`cd.yml`** — runs on merge to `main`: pushes image to Amazon ECR, deploys to ECS
  Fargate via `infra/terraform/`.

---

## Monitoring

`monitoring/` holds a scheduled job that compares live predictions/outcomes against
training-time distributions and generates a drift report (e.g. via
[Evidently](https://www.evidentlyai.com/)). Swap the preset (data drift, classification,
regression, etc.) to match your model type.

---

## License

MIT — see [`LICENSE`](LICENSE).
