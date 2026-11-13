# First fresh v8 validation — September 14, 2026

**Model agreement: 10/12. Final agreement: 10/12.** All twelve calls completed with valid citation IDs, no retries, no invalid outputs and no provider errors. No deterministic calendar guard matched or changed a label. Evaluator commit `d1a791f63bd5ee2f15054e22f06a063e58acdc87` and all reviewed case data stayed frozen through this first measurement.

## Meaningful metrics

| Metric | Result | What it measures |
|---|---:|---|
| Raw and final agreement | 10/12 each | Human response-quality agreement across every reference, including Abstain |
| Valid grades / valid ID outputs | 12/12 each | Usable output and actual citation-contract adoption, not semantic correctness |
| Failure detection | 4/4 | Reference-bad answers correctly rejected |
| Good-answer acceptance | 6/7 | Reference-good answers accepted; exposes over-rejection |
| False rejection | 1/7 | Good answer rejected in the timezone case |
| False passes | 0/4 | Bad answers incorrectly accepted; small denominator |
| Uncertainty recognition | 0/1 | Missing candidate should abstain, but was rejected |
| Guard matches / overrides | 0 / 0 | No rule-assisted inflation of agreement |

All 12 paired human reviews preceded model calls. Response references: 7 Pass, 4 Fail, 1 Abstain. Task references: 4 Pass, 2 Fail, 6 Abstain. Only response quality was judged by the model. The example 4 wording clarification remains in history, including the earlier un-applied submission; references were not revised after results.

## Findings

**Example 5 — timezone false rejection.** The model treated the UTC date as the Los Angeles local date. Independent local `datetime`/`zoneinfo` verification gives `2032-03-09T16:15:00-08:00` for `2032-03-10T00:15:00Z`. Tomorrow is March 10, matching the saved mentoring session. Human Response Pass stays unchanged; model Fail is an error.

The deterministic calendar parser returned unsupported for this complete but differently worded evidence record. It recognizes a narrow canonical prose format. The necessary facts existed, but the adapter did not extract them, leaving the arithmetic to the model. This is a coverage limitation, not a lack of evidence.

**Example 7 — missing candidate misclassified.** The judge's reason explicitly says the candidate response is missing and cannot be assessed, but it returned `abstain: false` and failure-level scores. The citation is valid and the JSON parses, so this is a semantic policy/output inconsistency. Successful task execution does not make an unavailable response assessable. Human Task Pass / Response Abstain stays unchanged.

**Other cases:** the new unsupported “takes care of your request for the next day” paraphrase failed correctly, while explicit uncertainty passed. Stale current-state claims failed; stale-cache disclosure passed. The title resembling an instruction was reproduced as data and passed. These observations are limited to this run.

## Before/after interpretation

The earlier v8 run matched 12/12 on the reused development set; this first fresh set matched 10/12. They have different cases and label distributions. Do not pool them or claim a temporal regression rate. Fresh validation exposed generalization limits hidden by the earlier perfect result. Citation handling worked in this run, but semantic accuracy remains imperfect.

These are twelve assistant-authored synthetic cases reviewed with the developer, not an independently sampled production holdout. Contrast pairs are correlated. There was one call per case, so repeat stability is unknown. Freeze and report this result before any changes.

## Run accounting

Pinned `gpt-4.1-mini-2025-04-14`, temperature 0, maximum 1,536 output tokens, 30-second timeout, zero retries; 12 calls. Only synthetic request/response/evidence, calendar facts, citation options and rubric were sent. Human labels/rationales stayed local.

Usage: 56,006 input tokens, 1,166 output tokens, 57,172 total; 47,616 cached input tokens included in input. Attempt latency p50 1891 ms, p95 2840 ms. This is a small observed sample, not an SLA. Dollar cost is not inferred. Exact raw outputs, payloads, frozen implementation and hashes are saved alongside this report.

## Recommended next decision

Prefer explicit structured evaluation metadata: a candidate-presence flag and typed request timestamp/timezone. Code can handle a genuinely absent answer before asking the judge and compute local dates from verified fields. Tradeoff: changes to export/schema plumbing and migration, with clearer provenance and less dependence on prose parsing. Do not infer trusted metadata from arbitrary candidate text.

Alternatives: extend the supported prose grammar (smaller change, more brittle) or compare another judge (extra API cost; extraction remains fragile). No remedy is implemented in this measurement. Keep the frozen result and existing labels as the before baseline, then validate any chosen change with contrast cases. No additional calls, deployment or commit were performed.

## Interview explanation

“I froze the evaluator and collected human labels before the first fresh run. The earlier development set scored 12/12, but fresh validation scored 10/12. All citations were valid; the failures were a missed timezone conversion and failure to abstain when the answer was absent. Separating output validity, failure detection, good-answer acceptance and uncertainty made those weaknesses visible.”
