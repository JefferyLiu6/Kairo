# Kairo

A personal-manager assistant: a FastAPI backend in `apps/api` and a React web app
in `apps/web`. [ARCHITECTURE.md](ARCHITECTURE.md) has the full design. This file
is what to know before changing code.

## Commands

| Command | What it does |
|---|---|
| `make install` | Install backend (uv) and web (pnpm) dependencies |
| `make dev` | Backend on :8766, web on :5173 |
| `make lint` | ruff and a compile check on the backend, `tsc` on the web app |
| `make test` | Backend tests, web tests, the 252-case offline suite, a smoke check |
| `make verify` | Lint, test and web build; what CI runs |

One backend test: `cd apps/api && uv run pytest tests/graph/test_graph_turns.py -k <name>`.

## Where new code goes

- **Web chat runs on the agent graph**, `apps/api/kairo/agents/graph/`. Its tests
  are in `apps/api/tests/graph/`.
- **A capability is a domain** in `apps/api/kairo/domains/`: typed argument
  models and tools, registered in `catalog.py`. Adding one is a module plus one
  line there; the graph's nodes should not need editing.
- **A tool never writes.** It proposes typed commands (`PMAction`) that the gate
  checks and the executors in `kairo/tools/executors/` carry out. A change that
  needs the user's approval goes through the approval flow.
- **Stores** are in `kairo/storage/`. Per-user data lives under
  `data/users/<user_id>/`; get the directory from `pm_dir`.

## Do not extend

These are kept until the previous chat path is retired. Fix bugs in them; add no
features and do not copy their patterns into new code.

- `kairo/agents/orchestrator/` and the typed workflow in
  `kairo/agents/personal_manager/` (`agent.py`, `workflow.py`, most of
  `application/` and `extractors/`). This is the previous chat path, reached
  with `KAIRO_AGENT=legacy` and through `/personal-manager/chat` and
  `/personal-manager/stream`. The graph still uses its approval flow, pending
  replies and working memory.
- The re-exports and `_sync_*_hooks` in `personal_manager/workflow.py`. They let
  older tests patch names on `workflow.py`. Import from, and patch, the module
  that defines the name.
- `to_legacy()` and the `legacy_*()` methods on the commands in
  `kairo/core/domain/`. They turn a typed command back into a dict. A new
  executor takes the typed command.
- `ScheduleEntry.weekday`. Use `recurrence`.

## Conventions

- When a pattern is replaced, remove the old one in the same change, or add it
  to the list above. Anything left in the repo gets copied.
- Do not import a `_private` name from another module in new code; give it a
  public name where it is defined.
- Do not silence a check: no `# type: ignore`, `any`, `# noqa`, skipped or
  expected-to-fail tests, or `except Exception: pass` to get a change through.
- A change to behaviour or settings updates `SECURITY.md`, `docs/` and the
  three `.env` example files in the same change.
- The offline suite (`make test-evals`) covers the previous path. The graph is
  scored by `make eval-blind path=graph`, which calls the configured model.
- A commit message is one plain sentence saying what changed.
