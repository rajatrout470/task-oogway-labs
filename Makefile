# =============================================================================
# The Lenny Growth Assistant
#
#   make help     list every target
#   make up       start the whole stack (Docker)
#   make ingest   load the transcript corpus
#
# Two paths are supported and both are first-class:
#   * Docker  — one command, matches the deployment topology
#   * Native  — faster iteration; needs local Postgres + pgvector
# =============================================================================

.DEFAULT_GOAL := help
.PHONY: help setup up down logs ps restart ingest ingest-fast reingest status \
        calibrate test test-all lint fmt dev-backend dev-frontend db-up db-shell \
        migrate models clean nuke check

VENV    := backend/.venv
PY      := $(VENV)/bin/python
COMPOSE := docker compose

help: ## Show this help
	@echo ""
	@echo "  The Lenny Growth Assistant"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'
	@echo ""

# --- Setup -------------------------------------------------------------------

setup: ## First-time setup: .env, python venv, node modules
	@test -f .env || (cp .env.example .env && echo "  created .env from .env.example")
	@command -v uv >/dev/null || (echo "  ERROR: uv not installed — https://docs.astral.sh/uv/" && exit 1)
	cd backend && uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e ".[dev]"
	cd frontend && npm install
	@echo ""
	@echo "  Next: pull the models, then ingest"
	@echo "    ollama pull qwen2.5:7b-instruct"
	@echo "    ollama pull nomic-embed-text"
	@echo "    make ingest"
	@echo ""

# --- Docker ------------------------------------------------------------------

up: ## Start the full stack (build if needed)
	$(COMPOSE) up --build -d
	@echo ""
	@echo "  frontend  http://localhost:5173"
	@echo "  api docs  http://localhost:8000/docs"
	@echo "  health    http://localhost:8000/api/health"
	@echo ""
	@echo "  If this is a fresh database, load the corpus:  make ingest"
	@echo ""

down: ## Stop the stack (keeps data)
	$(COMPOSE) down

restart: ## Restart the backend only
	$(COMPOSE) restart backend

logs: ## Tail all logs
	$(COMPOSE) logs -f

logs-backend: ## Tail backend logs only
	$(COMPOSE) logs -f backend

ps: ## Show service status
	$(COMPOSE) ps

db-up: ## Start only PostgreSQL (for native backend development)
	$(COMPOSE) up -d db

db-shell: ## Open a psql shell
	$(COMPOSE) exec db psql -U $${POSTGRES_USER:-lenny} -d $${POSTGRES_DB:-lenny}

# --- Knowledge base ----------------------------------------------------------

ingest: ## Load transcripts into the knowledge base (~20 min for 303 episodes)
	$(COMPOSE) --profile ingest run --rm ingest

ingest-fast: ## Ingest only 15 episodes — quick smoke test of the pipeline
	$(COMPOSE) --profile ingest run --rm ingest python -m app.ingest.cli run --limit 15

reingest: ## Force re-embed everything (needed after changing the embedding model)
	$(COMPOSE) --profile ingest run --rm ingest python -m app.ingest.cli run --force

status: ## Show what is currently indexed
	cd backend && $(CURDIR)/$(PY) -m app.ingest.cli status

calibrate: ## Re-measure the abstention threshold against the live index
	cd backend && $(CURDIR)/$(PY) -m scripts.calibrate_threshold

migrate: ## Apply database migrations
	cd backend && $(CURDIR)/$(PY) -m app.db.migrate

# --- Native development ------------------------------------------------------

dev-backend: ## Run the API natively with auto-reload
	cd backend && $(CURDIR)/$(PY) -m uvicorn app.main:app --reload --port 8000

dev-frontend: ## Run the Vite dev server
	cd frontend && npm run dev

# --- Quality -----------------------------------------------------------------

test: ## Run unit tests (no database or Ollama required)
	cd backend && $(CURDIR)/$(PY) -m pytest -q

test-all: ## Run every test, including integration (needs PostgreSQL)
	cd backend && $(CURDIR)/$(PY) -m pytest -q -m ""

lint: ## Lint backend and typecheck frontend
	cd backend && $(CURDIR)/$(PY) -m ruff check app/ tests/ scripts/
	cd frontend && npx tsc --noEmit

fmt: ## Auto-fix lint issues
	cd backend && $(CURDIR)/$(PY) -m ruff check --fix app/ tests/ scripts/
	cd backend && $(CURDIR)/$(PY) -m ruff format app/ tests/ scripts/

check: lint test ## Lint + test — run before committing

models: ## Show live provider and model status
	@curl -s http://localhost:8000/api/models | $(PY) -m json.tool 2>/dev/null \
		|| echo "  API not reachable — is the stack running? (make up)"

# --- Cleanup -----------------------------------------------------------------

clean: ## Remove build artifacts and caches
	find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -prune -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -prune -exec rm -rf {} + 2>/dev/null || true
	rm -rf frontend/dist

nuke: ## Remove containers AND all data, including the ingested corpus
	@echo "  This deletes the database and the ingested corpus. Re-ingest takes ~20 min."
	@printf "  Continue? [y/N] " && read ans && [ "$$ans" = "y" ]
	$(COMPOSE) down -v
