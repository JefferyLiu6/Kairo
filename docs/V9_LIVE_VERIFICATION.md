# V9 structured-context live verification — September 14, 2026

**Model agreement: 11/11 attempted cases. Final evaluator agreement: 12/12 cases.** Eleven approved OpenAI calls completed with valid citation IDs; the absent-response case produced deterministic Abstain without a call. No retry, provider error, invalid output or calendar label override occurred.

## Before and after

| Measure | Fresh v8 baseline | Adapted v9 check |
|---|---:|---:|
| Model agreement / attempted cases | 10/12 | 11/11 |
| Final agreement / all cases | 10/12 | 12/12 |
| Model coverage | 12/12 | 11/12 |
| Valid citation-ID outputs / calls | 12/12 | 11/11 |
| Failure detection | 4/4 | 4/4 |
| Good-answer acceptance | 6/7 | 7/7 |
| Final uncertainty recognition | 0/1 | 1/1, handled before the model |
| Model uncertainty recognition | 0/1 | Not measured: no reference-Abstain case sent |
| Model calls | 12 | 11 |
| Calendar rule matches / label overrides | 0 / 0 | 0 / 0 |

The v9 model did not grade twelve cases. The full evaluator handled twelve, including one local missing-response decision. The dashboard exposes this difference through model coverage, separate denominators and “No model grade” on case 7. Invalid outputs and provider errors remain operational failures, not correct abstentions.

## The two previous failures

**Case 5 — timezone:** typed request timestamp `2032-03-10T00:15:00Z`, timezone `America/Los_Angeles`, and stored date `2032-03-10` produced local date March 9 and requested date March 10. This time the model correctly accepted the response and explicitly used March 10 as tomorrow in Los Angeles. No rule forced a Pass.

**Case 7 — missing candidate:** the export placeholder was replaced in a separate derivative with an empty response and `response_available: false`, matching the documented absence. The evaluator abstained before invocation and produced no fabricated model scores or citation. No case-7 request or model response file exists because no call was made.

The other ten model decisions also matched the original human references. Citations were valid source locations in all eleven replies; location validity alone is not proof of reasoning quality.

## Provenance and limits

This is a verification run on known development examples with two explicit representation adaptations. It is not a new holdout. Original request/evidence/labels are preserved; the empty candidate in case 7 represents an already documented absence. `adaptation.json` records the source hash and transformations. Original human labels were reused transparently, not re-imported as newly certified v9 annotations.

The input interface, prompt and precheck changed together. One run does not isolate their causal contributions or measure repeat stability. The earlier 10/12 remains the preserved first fresh result. Do not replace it with 12/12, pool the denominators, or call this production accuracy. The missing-response fix is deterministic; the timezone response still requires model judgment informed by computed facts.

## Run accounting

Pinned `gpt-4.1-mini-2025-04-14`, temperature 0, maximum 1,536 output tokens, 30-second timeout, no retries. Eleven calls to OpenAI; human labels and rationales stayed local. Exact synthetic payloads, raw replies, code snapshot, adaptation record, hashes and accounting are preserved with this report.

Provider usage: 52,256 input tokens, 988 output tokens, 53,244 total, including 39,680 cached input tokens. Attempt latency p50 1507 ms, p95 2956 ms; the local precheck is excluded. No dollar charge is inferred without billing verification.

## Completion and next step

The two targeted changes now have successful live verification on the adapted set. 567 backend tests and offline contracts previously passed. Stop tuning these examples. The next useful milestone is an end-to-end agent demonstration with captured task state and telemetry, or a separately scoped stability check. Azure remains paused by user choice. No deployment, commit or further calls were performed.

Interview explanation: “Fresh validation exposed two failures despite valid citations. I introduced typed calendar metadata and explicit response availability. The next run matched 11/11 model judgments and 12/12 evaluator decisions because one absent answer abstained locally. I keep those denominators separate and retain the original 10/12 baseline.”
