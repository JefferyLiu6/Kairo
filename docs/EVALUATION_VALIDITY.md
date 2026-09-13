# Evaluation validity upgrade — September 12, 2026

This change improves the measurement system. It does not establish live model accuracy.

## Accepted scoring decision

Jeffery selected: unsupported execution-success claims fail groundedness; abstain when the
evaluation record itself is incomplete or unusable. This is `response-quality-v2`.
An explicit unknown write outcome or absence of supporting tool records is enough to reject
an unqualified success claim. A truncated evaluation export missing the request agreement and
execution record may require abstention. A clearly established defect still fails even when
other information is missing. We measure support, not unknowable real-world truth.

The 0–2 dimensions remain groundedness, relevance, completeness; pass requires all 2.
The rubric now includes partial-score anchors. The old unsupported-save fixture changes from
abstain to fail, and a truncated-record fixture provides a separate abstention example.
There are now 16 authored replay fixtures. This is a policy change, not measured model improvement.

Review packets now bind rubric version/hash as well as example content. Old schema-1 packets
are preserved but rejected by the new importer: export a new packet and explicitly re-review.
Do not silently carry labels across changed policies. The new eight-case development dataset
has null reference labels. It covers identity, ambiguity, stale evidence, partial execution,
approvals, embedded instructions, missing context, and dates. These are authored development
examples, not independent annotations or a hidden holdout.

## What changed and why

| Before | After | Verified effect and limitation |
|---|---|---|
| Errors and deliberate abstention shared headline count | Separate judge_abstentions and grading_failures; scored false-pass rate and negative coverage | Outage test shows legacy false-pass=0, scored false-pass=null, negative coverage=0. Legacy metric retained for compatibility. |
| One 3-turn lifecycle | Optional expanded suite: 6 isolated scenarios / 9 total turns | Controlled real workflow/model-stub integration passes nine state assertions. No live reasoning claim. |
| Offline quality rubric only; runtime semantic measurement absent | Runtime harness benchmark shares actual decision function and retry policy | 11 replay cases match authored decisions; 3 unsafe/nonallowlisted retries blocked; one intentional invalid grade and one injected provider failure. |
| Only captured replies enter quality grading | evaluation.json reports every attempted turn; optional judge join bound to exact content | Test with one response failure and two passing grades reports 2/3 passes per attempted turn/repeat, with successful state mutation still visible. |
| Review policy not bound in packet | Rubric version/hash checked at import | Old-policy packet rejection tested; human identity and independence remain unverified. |

Production harness semantics and retry budget are unchanged. Logic was extracted into shared
functions so benchmark and application cannot accidentally use separate decision implementations.
Runtime replay calls no model. Live runtime evaluation uses the production prompt, excludes the
two transport/parser injection fixtures, and reports decision agreement separately from semantic
grades and provider/parse errors. References are authored, not human-validated. It benchmarks one
harness decision and retry eligibility, not retry exhaustion, tool execution, or end-to-end safety.

The expanded final suite uses separate synthetic users/stores per scenario:

- Lifecycle: create, list, complete (three dependent turns).
- Empty task list: read without mutation.
- Target identity: complete one task among two; preserve the other.
- Ambiguous task: preserve state until target is resolved.
- Missing target: preserve existing tasks.
- Repeated completion: two turns on the same target, preserving identity and other fields.

An unchanged-state assertion does not prove the reply asked the right question. Final-answer
review remains necessary. The expanded suite does not yet cover full calendar/habit/journal flows,
cache freshness, approval conversations, or concurrent writes. Those require additional scenarios;
the authored quality examples are not a substitute for executing them.

## Commands

Run these from `backend`. Offline work needs no API key:

```sh
uv run python -m assistant.orchestrator.judge_eval --output ../artifacts/judge-v2-replay.json
uv run python -m assistant.orchestrator.runtime_eval --output ../artifacts/runtime-replay.json
uv run python -m assistant.orchestrator.label_review export --cases tests/fixtures/judge_development_cases.json --output ../artifacts/development-review-v2.json
```

Choose new output paths to preserve earlier reports. Complete review labels, rationale, and
reviewer yourself, then import:

```sh
uv run python -m assistant.orchestrator.label_review apply --cases tests/fixtures/judge_development_cases.json --packet ../artifacts/development-review-v2.json --output ../artifacts/development-reviewed-v2.json
```

When model access exists, the user can run these explicitly billable steps:

```sh
uv run python -m assistant.orchestrator.final_eval --live --suite expanded --provider openai --model kairo-eval --output-dir ../artifacts/azure-expanded-01
uv run python -m assistant.orchestrator.runtime_eval --live --provider openai --model kairo-eval --output ../artifacts/runtime-live-01.json
```

Final capture produces `evaluation.json` with judge_status=not_run, plus cases/run/monitor/telemetry.
Export and complete a review packet for the captured cases, import it into a new reviewed dataset,
then use `judge_eval --live --cases` on that dataset. Join the resulting report:

```sh
uv run python -m assistant.orchestrator.eval_report --run ../artifacts/azure-expanded-01/run.json --judge ../artifacts/final-judge-01.json --output ../artifacts/combined-01.json
```

The join checks a label-independent content hash, exact case/repeat membership, categories, and
result statuses. Human label review may change labels/provenance, but not captured answers/evidence.
Old judge reports without the content hash must be regenerated. Hashes prevent accidental mismatch;
self-reported metadata is not cryptographic attestation against malicious report editing.

A model deployment name alone does not identify an immutable model version. Record its underlying
version and endpoint configuration in the run notes. Logical call ceilings are not dollar caps;
expanded final capture has nine turns, each potentially making multiple calls. No automatic live
run, credential setup, or Azure deployment is performed by this upgrade.

## Human involvement and experiment discipline

Assistant prepares options, tooling, and evidence. Jeffery chooses scoring policy and supplies
reference judgments. Review method (guided independent vs AI-assisted vs solo) is still undecided.
The starter/reference packets have not been human-labeled. Reserve new unseen examples before
prompt tuning; do not call the public authored development examples a holdout. Use a second reviewer
for disputed cases if available; agreement between human reviewers has not been measured.

This separation of task-specific checks, human calibration, and representative examples follows
[OpenAI evaluation guidance](https://developers.openai.com/api/docs/guides/evaluation-best-practices).
No aggregate live quality gate is introduced before calibration. Report failures, coverage, and
category counts with any judge agreement figure.


## Current judge scoring update

Rubric v3 and metrics v2 now supersede the prompt descriptions above. See [Judge metrics and prompt](JUDGE_METRICS_AND_PROMPT.md) for formulas, purposes, worked examples, sources and verified effects. Historical rubric-bound review packets must be re-reviewed, not silently migrated.
