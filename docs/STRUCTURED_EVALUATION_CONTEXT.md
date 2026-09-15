# Structured evaluation context — v9

September 14, 2026. Local implementation and contract checks; no new live model calls. The frozen first fresh v8 result remains 10/12 and all original human labels remain unchanged.

## Why this change

The fresh v8 run returned valid citations on every example but failed two decisions: it treated a UTC date as the Los Angeles local date, and returned Fail for an absent candidate despite explaining that the candidate could not be assessed. Evidence was available in export prose, but the old calendar adapter recognized only a narrow sentence format; response absence also depended on model interpretation of a placeholder.

V9 makes these record properties explicit. Scoring dimensions and human task/response separation stay the same. The prompt version changes to response-quality-v9; calendar facts are v2 and metrics v3. The existing calendar confirmation guard stays v2.

## Record contract

An optional `evaluation_context` object is accepted by the benchmark runner and `evaluate_response`. Example:

```json
{
  "response_available": true,
  "request_timestamp": "2032-03-10T00:15:00Z",
  "user_timezone": "America/Los_Angeles",
  "stored_date": "2032-03-10"
}
```

Fields are strict: availability is a Boolean, timestamp includes an offset, timezone must resolve through zoneinfo, and stored date uses YYYY-MM-DD. Unknown fields, invalid calendar values and contradictory candidate availability fail validation before a model call. An unavailable response must be represented as `response: ""` and `response_available: false`; an available response must contain text. Missing the response field entirely is invalid, not an implicit absence.

Metadata is supplied by the exporter/caller, outside candidate text. Candidate text cannot set these fields. This validates types and consistency, not the truth or authority of an arbitrary caller: upstream capture must obtain values from real request/session/write records. No model extracts trusted metadata from prose here. The deterministic local task capture now explicitly emits response availability for captured replies; it does not invent calendar fields for task-only workflows.

## Behavior

For an unavailable response, the evaluator returns status ok, label Abstain, scores null, model_invoked false and decision_source missing_response. It makes no model call and fabricates no numerical model scores or citations. Malformed metadata returns an invalid operational result, not a correct semantic abstention.

For supported single book/schedule-for-tomorrow requests, complete typed calendar fields produce local timestamp, local date, requested date and stored-date alignment using datetime/zoneinfo. The fresh-case timestamp maps to March 9, 2032 at 16:15 in Los Angeles, so tomorrow is March 10. Full candidate/evidence text remains in the payload. Correct storage alone does not establish every response-quality dimension; no blanket Pass is applied.

When a context object is supplied, its calendar fields are authoritative for computation: incomplete fields yield unsupported facts rather than silently falling back to prose. This avoids mixing conflicting sources. Unknown request grammar and incomplete metadata remain outside the date calculator's scope. The legacy path remains available when no context object is supplied, including its narrow prose adapter. The legacy missing-anchor guard is not applied to typed-context records; the checked wrong-date guard remains available when appropriate.

## Review and measurement integrity

Context is included in case hashes, blind review exports and review comparisons. Changing metadata after human review invalidates the packet. Existing v8 review packets must not be imported as newly reviewed v9 packets.

Metrics distinguish final evaluator decisions from model attempts. Deterministic abstention counts toward final agreement and uncertainty recognition, but is excluded from model agreement and model-attempt latency. Reports include model attempts, model agreement over attempted labeled rows and precheck counts. The old model-agreement-over-all-cases field is undefined when any model was skipped. The dashboard displays different model/final denominators, model coverage, precheck abstentions and “No model grade” for a skipped call. This prevents claiming model improvement for work done by a rule.

Older rows without the new invocation flag retain their previous assumed-attempt interpretation. Replay attempts are still authored/replayed data, never actual API call counts; report mode must be considered. A live wrapper must count actual invocations and retain skipped rows without synthesizing model-only grades.

## Offline evidence

567 backend tests passed, including 19 new context cases. Workflow, response-quality replay and runtime contracts passed; backend lint passed. Tests cover strict input validation, source trust boundaries, missing-answer call avoidance, date/year/leap boundaries, hash-bound review metadata, compatibility, and different metric denominators.

Two records were explicitly adapted into a separate local derivative of the fresh set: case 5 copied the timestamp/timezone/stored date already present in evidence; case 7 replaced the absence placeholder with empty text and an unavailable flag. Their source hash and exact transformations are recorded. Labels were copied unchanged for diagnostic replay; this is not a new human certification of v9 input representation. Frozen originals were not edited.

Replaying the saved v8 outputs with that adapter results in 11/12 final agreement and 10/11 among replayed model decisions. The absent response now abstains without invoking the replay function. The old timezone Fail remains Fail even though supplied calendar facts are now correct. This is saved-output postprocessing with zero live calls, not live v9 accuracy. No model response was repaired or relabeled to claim the timezone judgment was solved.

## Tradeoffs and next step

Typed context adds exporter/schema work but reduces reliance on sentence formatting and lets code handle record absence directly. Date computation becomes testable; the judge still must assess claims correctly. Extending prose regexes would be smaller but more brittle. A larger judge could still make arithmetic or abstention mistakes and would add model cost.

Before a new live check, preserve the v8 baseline, record any input adaptation and keep labels local. At most 11 model calls would be needed for the adapted twelve-case set because one candidate is explicitly absent. Live effectiveness is currently unmeasured. No deployment or commit was performed.

Interview explanation: “I separated observable record metadata from model interpretation. Missing answers abstain before model invocation, while typed timestamps feed deterministic timezone facts into the judge. I report model and full-evaluator performance with different denominators so a skipped call cannot become a claimed model success.”


## Live verification completed

See [V9_LIVE_VERIFICATION.md](V9_LIVE_VERIFICATION.md): 11/11 model agreement and 12/12 final agreement on adapted known cases, with one no-call abstention. This supersedes earlier unmeasured status; original baselines remain unchanged.
