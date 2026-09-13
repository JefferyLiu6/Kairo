# Local evaluation development

From the repository root, run:

```sh
make eval-offline
```

The command runs four checks and prints the path to a readable report:

1. Backend pytest regressions (including model-stub workflow tests).
2. Existing deterministic PM workflow evaluations.
3. Response-quality judge replay, 16 authored examples repeated twice.
4. Runtime judge replay, including deliberate parser/provider error fixtures.

The runner invokes the existing modules using the same Python interpreter, from the backend
working directory. It has no live-model or deployment step and needs no API key for these checks.
This is a development convenience command, not a network security sandbox. `uv --offline` prevents
package fetching for this invocation; it does not prohibit arbitrary network calls in future tests.

## First-time dependencies

If uv reports missing packages while offline, install the development dependencies once:

```sh
cd backend
uv sync --extra dev
```

Dependency installation can require internet access. After it succeeds, return to the repository
root and run `make eval-offline`. This upgrade was verified using the existing development Python
environment; a fresh dependency installation was not performed.

## Reports and failures

Each run gets a new directory under `artifacts/offline-<timestamp>-<suffix>/` with:

- `README.md`: readable stage results and links to stdout logs.
- `report.json`: stage status, return codes, elapsed times, and explicit offline scope.
- Per-stage stdout/stderr text, plus full quality/runtime judge JSON reports.

Existing output directories are rejected, preserving previous evidence. The manifest is updated
after each check. A failing or timed-out stage does not hide subsequent results; the overall exit
code remains nonzero. Keyboard interruption records an interrupted status. A hard process kill
can leave status=running; that is incomplete evidence, not success. Check stdout/stderr for failure
details. Logs may contain test fixture text and should be reviewed before sharing.

For a specific output path or per-stage timeout, run from backend:

```sh
uv run --offline --extra dev python -m assistant.orchestrator.offline_eval --output-dir ../artifacts/my-run --timeout 180
```

The default timeout is 180 seconds per stage, not a total-run limit. The runner uses argument
arrays rather than a shell, so report directories containing spaces remain single arguments.
Stage results follow each child command's exit status; they are not new model quality scores.

## Development loop

Change code or a rubric, run the offline checks, inspect failing cases, and preserve the report.
Human-reference review and live-model benchmarking remain separate steps. Passing replay means
the measurement contract works with authored outputs; it does not show how a real model grades.
No Azure configuration is necessary to keep developing locally.

## Verified effects — September 12, 2026

Before: backend tests, workflow evals and judge checks required separate commands and reports.
After: one command runs all four stages and preserves a common result, including failures/timeouts.

Verified runner execution: all four stages passed; 385 backend tests (previous 375), 252 workflow
cases / 1,518 checks, 32 quality replay grades, and 11 runtime replay decisions. New tests cover
failed-stage propagation, timeout output retention, interruption, existing-report preservation,
invalid timeout values, paths with spaces, and absence of live flags. Lint/compilation passed.
The Make recipe was checked in dry-run mode; the runner itself was executed using the existing
Python environment. Cloud checks and fresh dependency installation were not run.
