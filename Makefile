# Data Hub — common operations.  `make help`
SHELL := /bin/bash
ENV ?= dev
COMPOSE_OVERRIDE ?= compose/docker-compose.$(ENV).yml
COMPOSE_FILES := -f docker-compose.yml -f $(COMPOSE_OVERRIDE)
COMPOSE := docker compose --env-file envs/$(ENV).env --env-file .env $(COMPOSE_FILES)
PY ?= python3

.DEFAULT_GOAL := help
.PHONY: help secrets config build up down restart ps logs seed-demo-users smoke deploy rollback backup restore \
        lint test test-dagster test-ui test-config test-integration test-e2e dbt-parse dbt-build dashboards clean

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

secrets: ## Create .env with random secrets (once)
	./scripts/generate-secrets.sh

config: ## Validate the Docker Compose configuration of ENV
	$(COMPOSE) config -q && echo "compose config OK ($(ENV))"

build: ## Build the Data Hub images
	DATAHUB_GIT_SHA=$$(git rev-parse --short HEAD 2>/dev/null || echo unknown) $(COMPOSE) build

up: ## Start the stack (ENV=dev|staging|prod)
	DATAHUB_GIT_SHA=$$(git rev-parse --short HEAD 2>/dev/null || echo unknown) $(COMPOSE) up -d --build
	./scripts/wait-healthy.sh $(ENV)

down: ## Stop the stack (data volumes are kept)
	$(COMPOSE) down

restart: ## Restart one service: make restart S=datahub-ui
	$(COMPOSE) restart $(S)

ps: ## Service status
	$(COMPOSE) ps

logs: ## Follow logs: make logs S=dagster-code
	$(COMPOSE) logs -f --tail=200 $(S)

seed-demo-users: ## Create the 4 demo users (dev/staging only)
	$(COMPOSE) exec -e DEMO_USERS_PASSWORD="$$(grep '^DEMO_USERS_PASSWORD=' .env | cut -d= -f2-)" datahub-ui \
	  python -m datahub_ui.tools.seed_demo_users

smoke: ## Smoke tests through the public URL
	./scripts/smoke-test.sh $(ENV)

deploy: ## Backup + build + start + smoke tests
	./scripts/deploy.sh $(ENV)

rollback: ## Redeploy a previous Git tag: make rollback ENV=prod TAG=v1.0.0
	./scripts/rollback.sh $(ENV) $(TAG)

backup: ## Backup PostgreSQL databases + DuckDB + dbt artifacts into ./backups
	./scripts/backup.sh $(ENV)

restore: ## Restore a backup: make restore ENV=prod BACKUP=backups/20260101-0300
	./scripts/restore.sh $(ENV) $(BACKUP)

lint: ## Ruff on both Python services
	cd services/dagster && ruff check . && ruff format --check .
	cd services/datahub-ui && ruff check . && ruff format --check .

dbt-parse: ## Regenerate the dbt manifest
	cd services/dagster/dbt_project && DUCKDB_PATH=/tmp/datahub_parse.duckdb dbt parse --profiles-dir .

dbt-build: ## dbt build (models + tests) on a local DuckDB (needs raw data: run test-dagster first)
	cd services/dagster/dbt_project && dbt build --profiles-dir .

test-dagster: ## Dagster + dbt tests (real pipeline runs on DuckDB)
	cd services/dagster && $(PY) -m pytest -q

test-ui: ## Streamlit Data Hub unit tests (every page, every role)
	cd services/datahub-ui && $(PY) -m pytest -q tests/unit

test-config: ## Static checks of infrastructure configuration
	$(PY) -m pytest -q tests/config

test-integration: ## UI integration tests (needs DAGSTER_GRAPHQL_URL / DATAHUB_TEST_PG_URL)
	cd services/datahub-ui && $(PY) -m pytest -q tests/integration

test-e2e: ## End-to-end tests against a running stack (SSO, RBAC, pipelines)
	set -a && source envs/$(ENV).env && source .env && set +a && $(PY) -m pytest -q tests/e2e

test: test-config test-dagster test-ui ## All tests that do not need a running stack

dashboards: ## Regenerate the Grafana dashboards from services/grafana/build_dashboards.py
	$(PY) services/grafana/build_dashboards.py

clean: ## Remove local caches
	find . -name __pycache__ -type d -prune -exec rm -rf {} + ; rm -rf services/dagster/dbt_project/target services/dagster/dbt_project/logs
