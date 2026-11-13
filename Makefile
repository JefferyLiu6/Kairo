BACKEND_DIR := apps/api
WEB_DIR     := apps/web
PYTHON      := python3

.PHONY: help install dev seed clean \
        test test-backend test-golden test-evals smoke \
        eval eval-report \
        lint lint-py lint-web build-web verify

help:
	@echo ""
	@echo "  make install      Install all dependencies (backend + frontend)"
	@echo "  make dev          Start backend + frontend dev servers"
	@echo "  make seed         Seed demo data for the 'demo' session"
	@echo "  make test         Run backend tests + evals + smoke check"
	@echo "  make eval-offline Run local regression and judge contracts; save a report"
	@echo "  make eval-blind   Score un-coached messages by stored state (path=pm|orchestrator)"
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

# Same clock for the app and the checks (see tests/conftest.py); CI runs in UTC.
test-evals smoke: export TZ := America/Toronto
test-evals smoke: export KAIRO_TIMEZONE := America/Toronto

test-backend:
	cd $(BACKEND_DIR) && uv run pytest

test-golden:
	cd $(BACKEND_DIR) && uv run --extra dev pytest tests/golden -v

test-evals:
	cd $(BACKEND_DIR) && uv run python -m evals.pm_runner

eval:
	cd $(BACKEND_DIR) && uv run python -m evals.pm_runner --json | tee ../eval-results.json
	@echo "Results saved to eval-results.json"

eval-report:
	cd $(BACKEND_DIR) && uv run python -m evals.pm_runner --report | tee ../eval-report.md
	@echo "Report saved to eval-report.md"

smoke:
	cd $(BACKEND_DIR) && uv run python -c "from tempfile import TemporaryDirectory; from kairo.agents.personal_manager.agent import PMConfig, run_pm; d = TemporaryDirectory(); reply = run_pm('Add task to smoke test', PMConfig(model='unused', data_dir=d.name, session_id='pm-smoke')); assert \"Added 'smoke test'\" in reply; d.cleanup()"

build-web:
	pnpm --dir $(WEB_DIR) build

lint: lint-py lint-web

lint-py:
	cd $(BACKEND_DIR) && uv run --extra dev ruff check kairo evals tests
	cd $(BACKEND_DIR) && uv run --extra dev python -m compileall -q kairo evals tests

lint-web:
	pnpm --dir $(WEB_DIR) exec tsc -b

verify: lint test build-web

clean:
	rm -rf $(BACKEND_DIR)/data $(WEB_DIR)/node_modules $(WEB_DIR)/dist

.PHONY: eval-judge
eval-judge:
	cd $(BACKEND_DIR) && uv run python -m evals.judge_eval --output ../artifacts/judge-replay.json

.PHONY: eval-runtime
eval-runtime:
	cd $(BACKEND_DIR) && uv run python -m evals.runtime_eval --output ../artifacts/runtime-replay-$$(date +%s).json

.PHONY: eval-offline
eval-offline:
	cd $(BACKEND_DIR) && uv run --offline --extra dev python -m evals.offline_eval

# Un-coached messages scored by what ends up stored. `make eval-blind` runs the offline
# workflow; `make eval-blind path=orchestrator` runs the web-chat path and calls the model.
.PHONY: eval-blind
path ?= pm
eval-blind:
	cd $(BACKEND_DIR) && uv run --extra dev python -m evals.blind --path $(path)
