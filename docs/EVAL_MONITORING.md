# Evaluation, monitoring, and LLM judging

This increment adds a response-quality benchmark and content-free operational telemetry.
It does not deploy Azure resources or claim measured live-model accuracy.

## Three distinct signals

1. `make test`: deterministic workflow correctness, safety contracts, and regression tests.
2. `make eval-judge`: replays author-written judge outputs on 15 synthetic examples. This tests
   parsing, reporting, abstention handling, and scoring contracts. Its agreement is NOT model accuracy.
3. Explicit live judge runs: a selected model grades the fixed example responses against supplied
   tool evidence. This evaluates a judge, not a live end-to-end agent or the production harness prompt.

The production harness judges raw PM output to choose pass/retry/fallback. The offline quality
judge grades groundedness, relevance, and completeness. Keeping them separate avoids silently
changing runtime behavior when experimenting with a rubric. The offline judge has no tools and
cannot authorize execution. It does not run on every production request.

## Run locally

From the repository root:

```sh
make eval-judge
```

From `backend`, with a supported provider credential in the environment:

```sh
uv run python -m assistant.orchestrator.judge_eval --live --provider ollama --model YOUR_MODEL --repeats 2 --max-calls 30 --output ../artifacts/judge-live.json
```

Choose `openai`, `anthropic`, or `ollama`. Set `JUDGE_API_KEY` and `JUDGE_BASE_URL` when
using a custom endpoint; provider-standard environment keys also work with the respective SDKs.
Do not put secrets in commands or reports. Live mode never loads credentials from a checked-in file.
Azure model availability and trial eligibility must be verified separately. An OpenAI-compatible
endpoint can use the existing provider adapter; native Azure deployment/API-version configuration
is not added by this change.

`--max-calls` caps logical judge evaluations, not dollars. SDK retries are disabled where the
adapter exposes `max_retries`. Provider timeouts use existing `LLM_TIMEOUT` configuration.
Each case is evaluated once per repeat. There is no hidden retry of invalid output.
Outputs include token usage when supplied by the provider; absent usage stays missing. Dollar
cost remains null until a dated model price configuration is supplied. Replay timing is parser
timing, not LLM latency.

Reports include dataset and rubric hashes, model/provider, Git revision and dirty status,
per-case scores, confusion matrices, coverage, false-pass rate, pass precision, p50/p95 latency,
and repeat disagreement. Commit the implementation before publishing a reproducible live result;
a Git hash with a dirty flag does not capture uncommitted code. Reports are gitignored because
custom datasets and judge explanations may contain private text.

Compare live reports only with the same dataset hash and repeat count. Keep model/prompt changes
explicit. Do not compare replay latency or agreement with live results. Provider errors and invalid
grades cause nonzero exit status. Live disagreement is reported for calibration, not automatically
treated as a release failure; no statistically justified live quality threshold is established yet.

## Judge rubric and calibration

Each dimension is an integer 0–2. A pass requires all three to score 2, so high relevance cannot
hide fabricated execution claims. Missing evidence allows abstention. Quotes must occur literally
in the response or supplied tool evidence. This check catches fabricated citations; it does not
prove that a quote supports the conclusion. The judge is told to ignore instructions inside data,
but prompt wording alone is not an injection defense guarantee.

The 15 labels are author-proposed synthetic expectations, not independent expert annotations.
Before using this judge as a quality gate: have a human review labels without seeing judge output;
collect more realistic examples; reserve unseen examples; run repeated grades; inspect disagreements.
Evaluate failures by category rather than optimizing aggregate agreement alone. Keep training/prompt
tuning cases separate from the final holdout. This starter set is public development/calibration data.
Do not claim statistical significance from it.

## Runtime reliability changes

Harness outputs now validate verdict values, finite confidence within [0,1], required fields, and
retry instructions. Confidence remains model-reported and uncalibrated. Malformed output falls back.
Explicit empty schedules/lists are sent to the semantic judge rather than automatically retried.
Errors on writes fall back. At the execution loop, retries are permitted only for allowlisted
read intents (`show_schedule`, `show_todos`, `show_habits`) with `is_write=false`.
This prevents a judge from replaying an uncertain write. It does not supply idempotency for direct
user retries or prove that every downstream tool is side-effect-free.

## Monitoring locally and on Azure

The normal `main.py` entry point configures `kairo.telemetry` JSON logging to stderr.
Set `KAIRO_TELEMETRY=0` to disable the new events. Alternate entry points must call
`configure_telemetry()` themselves. No telemetry exporter package is required.

Events carry random per-turn trace IDs and span/parent IDs, stage duration, completion/error
status, judge verdict/source, retries, and provider-reported token counts. They exclude prompts,
responses, profiles, API keys, exception messages, user IDs, and session IDs. Existing legacy
decision/fallback logs still contain content; the new privacy guarantee applies only to the new
telemetry stream. Do not export legacy content logs indiscriminately.

For Azure Container Apps, enable console-log collection into a Log Analytics workspace, then use
`docs/monitoring.kql`. The queries target the documented `ContainerAppConsoleLogs_CL` schema;
Azure Monitor resource-specific tables can use different column names. Replace the app name and
validate queries against the actual workspace before creating alerts. These queries are supplied
but have not been executed against an Azure account.

Start by examining per-stage p95 latency, runtime fallback rate, judge invalid/provider-error
counts, and token-usage coverage. A completed request is NOT proof of task correctness, and a
successful span means code completed rather than a business operation succeeded. Fallbacks have
separate events. Metrics are derived from best-effort logs, so missing ingestion can bias counts.
There is no durable metrics store, OpenTelemetry exporter, distributed HTTP propagation, or alert
resource deployed in this increment. Adopt OpenTelemetry when cross-service tracing is needed.

Suggested initial alert experiments (not validated SLOs): at least 20 observed turns in 15 minutes
and fallback rate above 20%; judge provider errors above 3 in 15 minutes. Tune using observed
traffic. An absence of logs is not a healthy system signal. Add an external availability probe when
deploying. Use short retention and monitor ingestion spend during the trial; alerts alone do not
stop Azure spending.

## Sources

- [Azure Container Apps log monitoring](https://learn.microsoft.com/en-us/azure/container-apps/log-monitoring)
- [Azure Monitor OpenTelemetry configuration](https://learn.microsoft.com/en-us/azure/azure-monitor/app/opentelemetry-configuration)
- [Azure budget behavior](https://learn.microsoft.com/en-us/azure/cost-management-billing/costs/tutorial-acm-create-budgets)

## Iteration 2: real workflow capture and paired comparisons

Run these commands from `backend`:

```sh
uv run python -m assistant.orchestrator.capture_eval --output ../artifacts/captured-responses.json
uv run python -m assistant.orchestrator.judge_compare ../artifacts/judge-baseline.json ../artifacts/judge-candidate.json --output ../artifacts/judge-comparison.json
uv run python -m assistant.orchestrator.monitor_report ../artifacts/telemetry.jsonl --output ../artifacts/monitor-summary.json
```

Capture executes three real deterministic PM turns against a temporary synthetic task store:
create, list, and complete. Evidence comes from independent before/after reads of persisted tasks.
It does not access a real user's data, call an external calendar, or evaluate orchestrator humanization.
Generated task IDs can vary, so preserve and reuse one capture artifact for paired judge comparisons.
Captured responses may contain generated IDs that are omitted from the compact state evidence.

Captured cases start with `expected: null`, meaning unreviewed. Review them before assigning pass,
fail, or abstain labels. They can be supplied to `judge_eval --live --cases PATH` using the same
provider/model flags documented above. Null labels do not prevent grading, but no all-case accuracy
is calculated and unlabeled cases are excluded from label-dependent metrics. An actual expected
`abstain` label is different from an unreviewed label. Replay refuses unreviewed captures rather
than manufacturing reference scores. Dataset validation happens before model construction.

The comparison command rejects different modes, datasets, repeat counts, duplicate/missing pairs,
and inconsistent reference labels. It recomputes metrics from rows and lists changed grades.
Rubric/model differences are allowed and preserved in metadata because those may be the experiment.
Deltas are candidate minus baseline. Lower false-pass rate is desirable; higher coverage may be
desirable, but neither alone determines the better judge. There is no automatic winner or claim
of statistical significance, and repeated grades are not independent new cases.

The local monitoring command reads the new JSON telemetry stream. To collect it during local
development, redirect the normal server's stderr to a file; other console lines are counted as
malformed/non-Kairo rather than treated as telemetry. It deduplicates identical records and joins
usage to observed LLM-call spans. Unmatched fallback/usage events are counted separately. Rates
describe matched observed events only, not all real requests; low traffic and dropped logs can bias
them. It does not emit conversation text or raw logs in the summary. It retains deduplication keys
in memory, so use bounded log files; use Azure log queries for large production datasets.

`llm_invoke` spans now include failed invocations, so missing usage after a failed call is visible.
Usage instrumentation covers orchestrator `_invoke` calls, not all possible internal PM model calls
or provider-side retries. No monetary total is inferred. The Azure KQL remains a separate, cloud-
unverified implementation; local report tests do not establish correctness of deployed queries.

## Evaluation-driven bug fix

The capture scenario exposed a real failure in quoted task completion:
`Mark the 'prepare interview examples' todo as done` previously retained `as done` in the lookup
query, failed to find the task, and left `done=false`. Entity extraction now recognizes this narrow
quoted completion form and preserves the exact quoted title. Regression tests inspect persisted
state and cover titles containing `as done` or an apostrophe. Other completion phrasings still use
the existing parser. This is a measured deterministic regression fix, not a claim of live-judge
improvement. See `docs/CAPTURE_FAILURE_CASE.md`.

## Iteration 3: review reference labels without seeing the judge's answer

From `backend`, export a fresh review packet:

```sh
uv run python -m assistant.orchestrator.label_review export --cases tests/fixtures/judge_cases.json --output ../artifacts/review-v1.json
```

The packet contains requests, responses, and evidence, plus blank `label`, `rationale`, and
`reviewer` fields. Labels are `pass`, `fail`, or `abstain` for insufficient evidence. Complete each
entry yourself or have another reviewer do so. Prior expected labels, replay verdicts, categories,
descriptive source IDs, and provenance are omitted; neutral case IDs reduce annotation hints.
The actual text may still make the intended test obvious. This is blinding against stored answers,
not a guarantee of unbiased independent evaluation.

Import the completed packet into a new dataset:

```sh
uv run python -m assistant.orchestrator.label_review apply --cases tests/fixtures/judge_cases.json --packet ../artifacts/review-v1.json --output ../artifacts/reviewed-cases-v1.json
```

The importer checks the example-content hash, case membership, unchanged visible text, labels,
reviewer identifier, and rationale. Incomplete packets are rejected. It removes stale replay
verdicts, records the review provenance with `identity_verified: false`, and never modifies the
source dataset. Output paths must be new, preserving review history. A recorded reviewer name is
not proof of a human or an independent annotation. No labels are filled automatically.

Use the resulting dataset with explicit live evaluation. Review the packet before seeing judge
results, reuse exactly the same reviewed dataset for paired runs, and retain disputed cases for
adjudication. No inter-reviewer reliability metric or adjudication workflow is implemented yet.
The Desktop review packet supplied with this increment is blank; human review remains pending.

Provider exceptions are now classified before parsing begins. A provider-side ValueError or
TypeError counts as `provider_error`; a returned malformed response counts as `invalid`. This
distinction helps identify availability/configuration failures versus judge output-contract failures.
Neither is a scored answer, and provider exception text stays out of the result.
