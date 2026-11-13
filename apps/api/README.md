# Kairo — Backend

FastAPI backend for Kairo. Web chat runs on the agent graph
(`kairo/agents/graph`); the previous orchestrator and typed workflow are still
here and answer web chat under `KAIRO_AGENT=legacy`. For the project pitch,
architecture and results, see the top-level [`README.md`](../../README.md),
[`ARCHITECTURE.md`](../../ARCHITECTURE.md) and
[`docs/BLIND_EVAL.md`](../../docs/BLIND_EVAL.md).

## Run

From the repo root, prefer the Makefile:

```bash
make dev          # Backend on :8766, frontend on :5173
make test         # pytest + eval harness + smoke check
make lint         # ruff and tsc
make eval-blind path=graph   # the blind eval against the live model (needs a provider key)
```

Or directly in this directory:

```bash
uv sync
uv run uvicorn main:app --host 0.0.0.0 --port 8766 --reload
uv run pytest
uv run python -m evals.blind --path graph --scenario 14_office_manager   # one scenario of the blind eval
uv run python -m evals.pm_runner                                         # offline suite for the previous workflow
uv run python scripts/seed_demo.py                                       # seed sample session data
```

## Layout

Imports only point down: `api -> agents -> domains -> tools -> storage / core -> utils`
(`tests/test_architecture.py` enforces it).

```
kairo/
├── api/                        # HTTP only: FastAPI app, auth, rate limits
├── agents/
│   ├── graph/                  # The agent graph: nodes, state, prompts, voice pass
│   ├── orchestrator/           # Previous path: route, translate, judge, humanize
│   └── personal_manager/       # Approval flow and policy (shared), and the previous typed workflow
├── domains/                    # What Kairo can do: registry, one module per area, checks
│   ├── tasks.py  habits.py  journal.py      # inline: the coordinator calls their tools itself
│   ├── schedule.py  memory.py  assistant.py # agents: each has its own prompt and tools
│   └── registry.py  checks.py  catalog.py
├── tools/                      # Executors, receipts, calendar, resolvers, memory, web search
├── storage/                    # SQLite and file persistence
├── core/                       # Prompt versioning, LLM gateway, domain types, observability
└── utils/                      # Parsing, clock, formatters, sensitivity

evals/
├── blind/                      # Un-coached messages scored by what ends up stored
├── datefill/                   # Can a model fill dates and times into typed arguments?
├── pm_runner.py                # Offline suite for the previous workflow (252 cases)
├── prompt_versions.py          # Snapshot of every prompt's version
└── scores.jsonl                # Ledger of recorded blind-eval runs

tests/                          # 1455 tests
├── graph/                      # The graph turn by turn: scripted model, real stores
├── domains/                    # Each domain's tools and checks
├── personal_manager/           # The previous workflow, including adversarial cases
├── evals/  judge/  orchestrator/  api/  shared/  golden/
└── fixtures/                   # The blind eval set, prompt-version snapshot, judge cases
scripts/
├── seed_demo.py                # Populate the demo session
└── demo_walkthrough.py         # Headless end-to-end walkthrough of the previous workflow
```

## Tests

- `uv run pytest` — 1455 tests. They need no provider key and make no model calls.
- `uv run pytest tests/graph` — the agent graph, with the model scripted.
- `uv run pytest tests/personal_manager/test_pm_adversarial.py` — adversarial hardening tests for the previous workflow.
- `uv run python -m evals.pm_runner` — 252 offline eval cases for the previous workflow.

With a provider key in `.env`, a few tests of the previous workflow call the
model and can fail on its answers. CI runs without a key.

A prompt edit changes the prompt set; `tests/evals/test_blind_ledger.py` fails
until a blind-eval run is recorded for it (`make eval-blind path=orchestrator
record="what changed and why"`) and `make prompt-versions` has updated the
snapshot.

Offline eval results for the previous workflow are in
[`../../eval-report.md`](../../eval-report.md). Blind-eval results for the
graph are in [`../../docs/BLIND_EVAL.md`](../../docs/BLIND_EVAL.md).
