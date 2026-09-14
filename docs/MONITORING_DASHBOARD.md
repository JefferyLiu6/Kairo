# Local evaluation monitoring dashboard

September 14, 2026. Local snapshot dashboard; no Azure deployment, continuous collection, automatic alerts, or API calls.

## What changed and why

Before: judge experiments and runtime telemetry were inspected in separate JSON reports. It was easy to confuse valid output with correct judgments, or final rule-assisted agreement with model agreement.

After: `assistant.orchestrator.monitor_dashboard` reads explicitly selected judge reports and optional runtime logs, recomputes aggregate metrics from rows, and creates a self-contained HTML dashboard plus an optional content-minimized JSON summary. The user can select a run and expand its decision table. No external assets, services, browser uploads, or dependencies are required.

The supplied Desktop snapshot displays the preserved v5 and v6 eight-case live runs. V5 agreement is 6/8 and v6 is 8/8. Those are the previously measured development results, not new calls made by this dashboard. Runtime health is shown as unknown because no runtime log was supplied.

## Generate a dashboard

From `backend`, in your existing project environment:

```bash
python -m assistant.orchestrator.monitor_dashboard   /path/to/baseline/report.json /path/to/candidate/report.json   --output ../artifacts/monitoring/index.html   --summary ../artifacts/monitoring/summary.json
```

Open the generated HTML in a browser. No web server is required for the saved file. To include existing Kairo JSON telemetry, add `--telemetry /path/to/telemetry.jsonl`. Regenerate the snapshot after a new report or log capture; it does not refresh itself. Supplying no reports creates an explicit empty state, not a healthy status.

Use final `report.json` artifacts, not both a model-only report and its final counterpart: the dashboard already separates raw and final grades. Exact repeated row-and-metadata inputs are deduplicated, even if their saved summaries differ. Different reports are kept separate, never pooled into a single accuracy number. Live metadata, replay, unknown mode and saved-output postprocessing are displayed distinctly; only reports declaring live mode have timings labeled live judge-attempt latency.

## Metric definitions and interpretation

| Signal | Why it is shown | Denominator / limit |
|---|---|---|
| Raw model agreement | Exposes the model's own decisions before rules | Human-labeled response rows only; errors are misses |
| Final agreement | Measures the complete evaluator result | Same reference denominator; not task completion |
| Failure detection | Detects problematic responses, including misses hidden by abstention | Human-failed responses; unavailable with none |
| Good-answer acceptance | Prevents an always-fail judge appearing useful | Human-passed responses |
| Uncertainty recall | Checks deliberate semantic abstention | Human-abstain response references; task abstentions do not count |
| False passes | Shows false reassurance | Human-failed references; pair with failure detection |
| Human-label coverage | Reveals incomplete annotation | Labeled rows / all rows, including repeats |
| Usable grades | Operational output reliability | Valid outputs / all rows; not correctness |
| Provider and parsing errors | Separates infrastructure/format problems from semantic abstention | Counts per selected run |
| Calendar matches and overrides | Makes rule contributions visible | Override means raw and final labels differ; matches can have no override |
| Latency p50 / p95 | Shows observed evaluation-attempt duration | Finite nonnegative durations only; coverage displayed; attempts include errors when timed |
| Runtime fallback and stage duration | Supports operational debugging from existing logs | Best-effort observed spans, not a complete request census |

Agreement is recomputed from per-row status, reference and decisions; supplied aggregate summaries cannot inflate results. Partial human labels produce agreement over the labeled subset and explicit coverage. Missing timings are omitted and counted as missing, not zero. Provider errors never become successful semantic abstentions. Unsupported schemas fail clearly rather than displaying fabricated values.

The HTML and exported summary omit prompts, responses, tool evidence, judge rationales and case IDs. Decision rows use ordinal numbers for lookup in the original local report. Report filenames, model/rubric identifiers, run timestamps and hashes remain visible for provenance. Metadata and runtime stage names are escaped before HTML rendering. This is data minimization, not a guarantee that a user-created filename or metadata field cannot itself contain sensitive text. Keep original detailed reports private.

## Tradeoffs

A standalone snapshot works before Azure quota is available, costs nothing to generate, and avoids changing the user-facing application or evaluation policy. Its limitation is manual regeneration: it cannot detect a new outage, alert in the background, prove availability, or establish production quality. Runtime logging ingestion remains best effort. No report/live data means unknown health. No dollar cost is inferred from absent usage or price configuration.

Reports from different datasets, rubrics or timings cannot establish an accuracy trend merely because they appear together. Small development sets are not release gates. The eight-case v6 set has no response-quality abstention reference, so uncertainty recall is explicitly unavailable. The next fresh set includes missing candidate/request exports to cover this gap.

## Validation and before/after evidence

- 20 focused tests passed: raw/final separation, reference denominators, provider versus semantic abstention, missing/invalid latency, no-live labels on offline durations, HTML escaping, omission of raw content, deduplication, empty states, optional telemetry, invalid grades, protected input files and CLI output.
- 494 backend tests passed; offline workflow, quality replay and runtime contract checks passed.
- Browser verification: report selector hid the other report; expanding decisions displayed all eight rows; overview and decision layout inspected visually.
- No judge/API calls made; no rubric or date-rule changes. The original v5/v6 reports stay unchanged.

## Fresh v6 validation in parallel

Twelve assistant-authored examples were frozen against committed v6 (`1e8adcdc`), covering supported/unsupported agreements, plain versus explicit date compliance, truthful failures, false success, missing exports, timezone conversion, date paraphrases, task-title injection and partial success. This is prospective development validation, not an independently sampled production holdout.

Jeffery reviewed example 1: task completion Pass, response quality Pass. Eleven paired reviews remain before the first judge measurement. Labels and first-judgment history are stored separately on Desktop. Monitoring code must not change the frozen judge implementation. No new accuracy claim is available yet.

## Interview explanation

“I built monitoring that distinguishes judge reliability from judge correctness. It shows reference-label coverage and separates model decisions from deterministic overrides, so a high final score cannot hide model errors. I use explicit unknown states for missing runtime data and undefined metrics instead of showing reassuring zeros. The first version is a local snapshot because it works without cloud quota; continuous ingestion and alerts are a separate deployment step.”
