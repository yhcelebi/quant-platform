# qp — common tasks. Run `make help` for the list.
.DEFAULT_GOAL := help
SHELL := /bin/bash
UV ?= uv

.PHONY: help setup lock lint format typecheck imports test test-cov check \
        db-up db-down migrate doctor terminal workers trader-paper deps-smoke graph \
        app web-install web-build web-dev web-check

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

app: ## Household books web app + API on http://127.0.0.1:8765 (build the web app first)
	$(UV) run python -m apps.api.main

web-install: ## Install the web app's packages (apps/web, pnpm)
	cd apps/web && pnpm install

web-build: ## Build the web app into apps/web/out (served by `make app`)
	cd apps/web && pnpm build

web-check: ## Lint and type-check the web app
	cd apps/web && pnpm lint && pnpm exec tsc --noEmit

web-dev: ## Web app with hot reload on :3000 (API: run `make app` alongside)
	cd apps/web && pnpm dev

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
