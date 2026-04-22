BACKEND_DIR := backend
WEB_DIR     := web
PYTHON      := python3

.PHONY: help install dev seed clean \
        test test-backend test-evals smoke \
        eval eval-report \
        lint lint-py lint-web build-web verify

help:
	@echo ""
	@echo "  make install      Install all dependencies (backend + frontend)"
	@echo "  make dev          Start backend + frontend dev servers"
	@echo "  make seed         Seed demo data for the 'demo' session"
	@echo "  make test         Run backend tests + evals + smoke check"
	@echo "  make lint         Lint backend (ruff) and frontend (tsc)"
	@echo "  make build-web    Build the production web bundle"
	@echo "  make verify       Lint + test + web build (CI entrypoint)"
	@echo "  make clean        Remove data/ and node_modules/"
	@echo ""

install:
	@echo "→ Installing backend dependencies..."
	cd $(BACKEND_DIR) && uv sync
	@echo "→ Installing frontend dependencies..."
	pnpm --dir $(WEB_DIR) install
	@echo "✓ Dependencies installed."

seed:
	@echo "→ Seeding demo data..."
	cd $(BACKEND_DIR) && uv run python scripts/seed_demo.py

dev:
	@echo "→ Starting backend on :8766 and frontend on :5173"
	@echo "   Press Ctrl-C to stop both."
	@(cd $(BACKEND_DIR) && uv run uvicorn main:app --host 0.0.0.0 --port 8766 --reload) &
	@(pnpm --dir $(WEB_DIR) dev -- --port 5173) ; \
	  kill %1 2>/dev/null ; wait

test: test-backend test-evals smoke

test-backend:
	cd $(BACKEND_DIR) && uv run pytest

test-evals:
	cd $(BACKEND_DIR) && uv run python -m assistant.personal_manager.evals.runner

eval:
	cd $(BACKEND_DIR) && uv run python -m assistant.personal_manager.evals.runner --json | tee ../eval-results.json
	@echo "Results saved to eval-results.json"

eval-report:
	cd $(BACKEND_DIR) && uv run python -m assistant.personal_manager.evals.runner --report | tee ../eval-report.md
	@echo "Report saved to eval-report.md"

smoke:
	cd $(BACKEND_DIR) && uv run python -c "from tempfile import TemporaryDirectory; from assistant.personal_manager.agent import PMConfig, run_pm; d = TemporaryDirectory(); reply = run_pm('Add task to smoke test', PMConfig(model='unused', data_dir=d.name, session_id='pm-smoke')); assert \"Added 'smoke test'\" in reply; d.cleanup()"

build-web:
	pnpm --dir $(WEB_DIR) build

lint: lint-py lint-web

lint-py:
	cd $(BACKEND_DIR) && uv run --extra dev ruff check assistant tests
	cd $(BACKEND_DIR) && uv run --extra dev python -m compileall -q assistant tests

lint-web:
	pnpm --dir $(WEB_DIR) exec tsc -b

verify: lint test build-web

clean:
	rm -rf $(BACKEND_DIR)/data $(WEB_DIR)/node_modules $(WEB_DIR)/dist
