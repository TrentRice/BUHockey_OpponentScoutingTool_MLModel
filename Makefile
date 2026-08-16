.PHONY: setup lint format test up down logs train serve clean

# Install dependencies
setup:
	uv sync
	pre-commit install

# Run linter
lint:
	ruff check .

# Auto-format code
format:
	ruff format .
	ruff check --fix .

# Run test suite
test:
	pytest -v

# Start local stack (API + MLflow + Postgres)
up:
	docker compose up --build

# Start local stack in background
up-detached:
	docker compose up --build -d

# Stop local stack
down:
	docker compose down

# Tail logs from local stack
logs:
	docker compose logs -f

# Run data ingestion
ingest:
	python -m src.ingestion.load_data

# Build features
features:
	python -m src.features.build_features

# Train a model (usage: make train MODEL=gbm)
train:
	python -m src.models.train --model $(MODEL)

# Run the serving app locally without Docker
serve:
	uvicorn src.serving.app:app --reload --host 0.0.0.0 --port 8000

# Remove caches, build artifacts
clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type d -name ".pytest_cache" -exec rm -rf {} +
	rm -rf .ruff_cache
