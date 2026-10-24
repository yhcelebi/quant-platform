# qp — common tasks. Run `make help` for the list.
.DEFAULT_GOAL := help
SHELL := /bin/bash
UV ?= uv

.PHONY: help setup lock lint format typecheck imports test test-cov check \
        db-up db-down migrate doctor terminal workers trader-paper deps-smoke graph

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

setup: ## Install deps (dev group), pre-commit hooks, and create .env from the example
	$(UV) sync --locked
	$(UV) run pre-commit install
	@test -f .env || (cp .env.example .env && echo "created .env from .env.example; edit QP_DB__PASSWORD")

lock: ## Re-resolve uv.lock
	$(UV) lock

lint: ## ruff lint + format check + lockfile check
	$(UV) lock --check
	$(UV) run ruff check .
	$(UV) run ruff format --check .

format: ## Auto-format and auto-fix
	$(UV) run ruff format .
	$(UV) run ruff check --fix .

typecheck: ## mypy (strict on core, platform, ledger, stats, performance, risk, alerts)
	$(UV) run mypy

imports: ## import-linter layer and library-boundary contracts
	$(UV) run lint-imports

test: ## pytest (integration tests skip without PostgreSQL unless QP_REQUIRE_DB=1)
	$(UV) run pytest

test-cov: ## pytest with coverage report
	$(UV) run pytest --cov --cov-report=term-missing

check: lint typecheck imports test ## Everything CI runs: lint, types, contracts, tests

db-up: ## Start PostgreSQL 16 (docker compose) and wait until healthy
	docker compose up -d --wait postgres

db-down: ## Stop PostgreSQL (data volume is kept)
	docker compose down

migrate: ## Apply Alembic migrations to head
	$(UV) run alembic upgrade head

doctor: ## Environment, config, DB, migrations and lake health check
	$(UV) run qp doctor

terminal: ## Run the Streamlit terminal
	$(UV) run streamlit run apps/terminal/app.py

workers: ## Run the APScheduler workers process
	$(UV) run python -m apps.workers.main

trader-paper: ## Run the trader in paper mode (phase 0: stub)
	$(UV) run python -m apps.trader.main --mode paper

deps-smoke: ## Install all dependency groups and smoke-test research/trading libraries
	$(UV) sync --locked --all-groups
	$(UV) run python scripts/deps_smoke.py

graph: ## Rebuild the graphify code graph in graphify-out/ (offline; needs `uv tool install graphifyy`)
	@command -v graphify >/dev/null || (echo "graphify not found: uv tool install graphifyy" && exit 1)
	GRAPHIFY_BACKEND=none graphify extract . --code-only
	GRAPHIFY_BACKEND=none graphify cluster-only .
