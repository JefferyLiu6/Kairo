# Judge evaluation: metrics, prompt, and evidence

September 12, 2026. Rubric: `response-quality-v3`. Metrics: `judge-metrics-v2`.
This evaluates whether a judge is useful, not merely whether its JSON parses.

## Problem and measurement choices

Kairo's judge can mistakenly accept fabricated task success, reject a useful response, abstain
unnecessarily, or fail to return a grade. Aggregate agreement alone obscures these differences.
The report keeps separate measures rather than inventing a weighted quality score.
“Reference” means a dataset-supplied label; it means a human reference only after actual review.

All proportions below use 0–1 values, with null for empty denominators. A correct prediction must
have status=ok; an API or parsing failure never counts as a correct abstention. Each report's
`metric_details` provides numerator, denominator, question, direction, and limitation for the
seven new metrics, including within category summaries. The balanced metric is a mean of two
rates rather than a single count ratio, so its numerator/denominator fields are null.

| Metric | Formula | Problem it measures and why used | Interpret alongside |
|---|---|---|---|
| Failure detection recall | Valid fail predictions / all reference-fail rows | Can the judge catch bad answers? Unknown/error outcomes count as misses, preventing evasive judges from appearing effective. | False-pass rate, negative-grade coverage |
| Good-answer acceptance | Valid pass predictions / all reference-pass rows | Does the judge preserve useful answers? An always-fail judge cannot succeed here. | Failure recall; improving this alone encourages permissiveness |
| False rejection rate | Valid fail predictions / all reference-pass rows | Unnecessary rejection can cause needless correction and reduce usability. | Acceptance, abstentions and grading failures; errors are not rejections |
| Balanced decision accuracy | Mean of failure recall and good-answer acceptance; only when both reference classes exist | Gives good and bad answers equal weight when the dataset is imbalanced. Errors/abstentions are misses. | Both component recalls; excludes reference-abstain cases and is not a safety gate |
| Uncertainty recall | Valid deliberate abstentions / all reference-abstain rows | Does the judge recognize evaluation records that truly cannot be assessed? | Unnecessary abstention and number of uncertainty examples |
| Unnecessary abstention rate | Valid abstentions / all reference-pass-or-fail rows | Does the judge avoid making decisions on assessable examples? | Human disagreements; reference labels can be wrong |
| Usable verdict rate | All status=ok rows / all attempted grades | Measures output-contract/provider reliability, including valid abstentions. | Correctness; valid JSON is not necessarily a good judgment |
| False-pass rate, scored (existing) | Valid pass predictions on reference-fail / valid non-abstaining grades on reference-fail | Measures dangerous acceptance conditional on actually grading a bad answer. | Negative-grade coverage; zero denominator is null |
| Pass precision (existing) | Reference-pass among valid, labeled predicted passes / all valid, labeled predicted passes | When the judge says pass, how often does the reference agree? | Dataset class balance and number of predicted passes |
| Coverage (existing) | Valid pass/fail grades / all attempted grades | How much of the dataset gets a decisive grade? | Usable verdict rate, which also counts deliberate abstentions |
| Outcome disagreement rate | Repeated cases with any status/label change / all repeated cases | Detects unstable decisions and switching between valid abstention and provider errors. | Correctness; an always-wrong judge is stable |
| Score disagreement rate | Fully scored repeated cases with any dimension change / all fully scored repeated cases | Detects score drift hidden by an unchanged pass/fail label. | Fully scored repeated-case count; missing grades are excluded |

The old `false_pass_rate` is retained for report compatibility: it divides false passes by ALL
reference-fail rows, including errors/abstentions. Never read it without coverage or failure recall.
`agreement_scored` is conditional on available grades. `agreement_all_cases` is only emitted when
every example is labeled. Stability is a separate report section. Latency p50/p95 and token usage
remain operational context; replay duration is not model latency and cost is not inferred.

No confidence/calibration error metric is added: the judge produces ordinal scores, not calibrated
probabilities. No p-value or confidence interval treats repeated grades as independent examples.
The small authored set cannot support broad accuracy claims or justify a release threshold.

## Prompt design and trade-offs

The actual prompt in `backend/assistant/orchestrator/quality_judge.py` now includes:

1. A scoped role: response quality, distinct from execution/authorization.
2. A trust boundary: candidate text and tool records are data; embedded grading instructions,
   authority claims and model identities do not change the rubric.
3. Explicit 0/1/2 anchors for each dimension. Dimensions are scored independently.
4. The user-approved distinction between unsupported execution claims and incomplete exports.
5. Four complete input/output examples: pass, unsupported-success fail, partial-completeness fail,
   and abstain. These use different requests from the scored fixture set.
6. A strict JSON contract with a short evidence-linked justification and a literal quote.

Example pair: “Added buy lentils to your tasks” passes when the execution record confirms that
exact saved task. The identical answer fails groundedness when the complete record says the
write timed out and the outcome is unknown. Same wording, different evidence, different grade.
A separate example omits a requested class time: groundedness/relevance=2, completeness=1.
This illustrates why a failure should not automatically lower every dimension.

We preserve three dimensions and require all 2 for pass. We request a concise rationale rather
than a long reasoning transcript. Prompt examples are generated from `PROMPT_EXAMPLES`, which
are checked by the actual verdict parser. They are teaching examples, not independent test data.
The new prompt is longer, so live token use and latency may increase; no such effect has been
measured. More explicit prompting is a hypothesis, not proof of better accuracy or injection resistance.

Review packets automatically bind the new rubric hash/version. Existing v2 files are preserved;
export/re-review under v3 before applying labels. Never silently migrate completed human labels.
The previous 16 regression fixtures remain authored; eight development examples remain unreviewed.

## Why these practices, rather than a “SOTA” label

OpenAI recommends detailed rubrics with concrete scoring examples, human calibration, and
classification/pass-fail judgments; we implement these practices while retaining independent
state assertions for agent behavior. [Official evaluation guidance](https://developers.openai.com/api/docs/guides/evaluation-best-practices).

Research documents position, verbosity, and self-enhancement biases. Our single-response prompt
explicitly excludes style and model identity as evidence, but wording alone cannot demonstrate
bias removal. [Zheng et al., MT-Bench/Chatbot Arena](https://arxiv.org/abs/2306.05685).

More recent work reports model-dependent effects of bias mitigation, reinforcing the need to
compare configurations empirically instead of declaring a universally best prompt.
[Soumik, Judging the Judges (2026)](https://arxiv.org/abs/2604.23178).

This is research-informed engineering, not a claim that Kairo or its prompt achieves SOTA results.
Pairwise comparison is useful when ranking candidate answers, but our immediate problem is
absolute acceptance/rejection of one answer; adopting it would change the target and cost.

## Recorded before/after effects

Controlled counterexamples use the same constructed predictions and references before and after;
they call no model. The input has nine reference-good answers and one reference-bad answer.

| Constructed judge | Previous aggregate agreement | New balanced accuracy | New failure recall | New good-answer acceptance |
|---|---|---|---|---|
| Always pass | 90% | 50% | 0% | 100% |
| Always fail | 10% | 50% | 100% | 0% |
| Always abstain | 0% | 0% | 0% | 0% |
| Provider outage | 0% | 0% | 0% | 0% |

The old report already exposed coverage and errors. The improvement is making specific failure
modes and denominators explicit, not correcting all old metrics or proving a model got better.
New tests also detect dimension drift under an unchanged fail label and a provider error under
an unchanged abstain label. Earlier label-only repeat disagreement missed those changes.

Verification: 375 backend tests passed (previous 362); lint, compilation and whitespace checks
passed. Replay of 16 authored fixtures over two repeats produced 32 contract grades with agreement
1.0 and zero outcome/score drift. This checks deterministic contracts, not live model stability.
No API key was used, no live judge called, and no deployment performed.

## Live comparison plan and success interpretation

Freeze a reviewed dataset before comparing v2 and v3. Keep model version, input examples, provider,
generation settings and repeats fixed. Preserve both prompt versions and report hashes. The
v2 prompt must be retained from its existing source snapshot; this upgrade does not add a CLI
switch for loading historical prompts. Commit/reproduce each configuration when running a real
comparison; reports currently carry dirty=true while local changes are uncommitted.

Prefer lower false-pass rate with maintained negative coverage, higher failure recall without
collapsing good-answer acceptance, appropriate uncertainty decisions, and low grading errors.
Inspect per-category counts and every changed grade. Do not pick a winner solely by balanced
accuracy, and do not turn a small public calibration set into a release gate. Reserve unseen
examples and review style/verbosity variants before claiming robustness. Live improvement remains
unmeasured until paired human-reference results exist.

From backend, an offline reporting check is:

```sh
uv run python -m assistant.orchestrator.judge_eval --repeats 2 --max-calls 32 --output ../artifacts/judge-v3-replay.json
```

Sixteen cases repeated twice require max-calls=32, above the default 30. Use a new output path
for every preserved run. Provider credentials are needed only for explicit `--live` runs.
