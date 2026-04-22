# Kairo — Backend

FastAPI backend for the Kairo orchestrator and controlled workflow. The PM
fallback path still uses LangGraph for low-risk chat. For the project pitch,
architecture, and portfolio context, see the top-level
[`README.md`](../README.md) and [`ARCHITECTURE.md`](../ARCHITECTURE.md).

## Run

From the repo root, prefer the Makefile:

```bash
make dev          # Backend on :8766, frontend on :5173
make test         # pytest + eval harness + smoke check
make eval-report  # Regenerate ../eval-report.md
```

Or directly in this directory:

```bash
uv sync
uv run uvicorn main:app --host 0.0.0.0 --port 8766 --reload
uv run pytest
uv run python -m assistant.personal_manager.evals.runner
uv run python scripts/demo_walkthrough.py   # headless end-to-end demo
uv run python scripts/seed_demo.py          # seed sample session data
```

## Layout

```
assistant/
├── http/pm_app.py              # HTTP API (FastAPI)
├── orchestrator/               # Direct/delegate router, translator, harness, humanizer
└── personal_manager/
    ├── agent.py                # Entrypoint (run_pm / astream_pm)
    ├── workflow.py             # Typed workflow coordinator
    ├── application/            # Use cases: clarify · plan · approval · learning
    ├── domain/                 # Typed intents, commands, plan extractions
    ├── extractors/             # Deterministic intent + entity extraction
    ├── resolvers/              # Time / target / event resolution
    ├── executors/              # Action handlers (schedule · todos · habits · journal · memory)
    ├── persistence/            # SQLite: working_memory · approvals · audit · patterns
    ├── calendar/               # Google Calendar OAuth + sync
    ├── presentation/           # Reply formatting
    ├── parsing/                # Date / time / text helpers
    └── evals/                  # Offline eval harness (252 cases, 1518 checks)

tests/                          # 227 unit + integration tests
scripts/
├── seed_demo.py                # Populate the 'demo' session
└── demo_walkthrough.py         # Headless nine-turn end-to-end walkthrough
```

## Tests

- `pytest tests/` — 227 unit + integration tests
- `pytest tests/test_pm_adversarial.py` — 68 adversarial hardening tests
- `python -m assistant.personal_manager.evals.runner` — 252 eval cases, 1518 checks

Current eval results are in [`../eval-report.md`](../eval-report.md). The adversarial regression coverage lives in [`tests/test_pm_adversarial.py`](tests/test_pm_adversarial.py).
