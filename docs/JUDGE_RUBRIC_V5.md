# Kairo rubric v5: claim scope and calendar verification

Date: September 13, 2026. Status: implemented and locally verified; Azure deployment remains user-operated.

## Problem and decision

The v4 judge could abstain on an honest answer that explicitly disclosed missing agreement history. It also accepted an event on the wrong local date. We changed the prompt to identify the claims actually made before deciding whether missing context blocks evaluation, and added deterministic calendar facts.

The live experiment then showed that facts alone were insufficient: the model explained the date mismatch but still assigned passing scores. We added a conservative final rule for a recognizable, unqualified confirmation of the verified wrong date. This rule changes the final grade to fail and preserves the original model verdict for inspection.

## Before and after, with the same human labels

Twelve live calls used gpt-4.1-mini-2025-04-14, temperature 0, one call per case per version, alternating which version ran first. Labels were excluded from model payloads. The six examples had already been reviewed and informed this change: they are development examples, not an independent holdout.

| Metric | Frozen v4 model | v5 model + calendar facts | v5 with final calendar rule |
|---|---:|---:|---:|
| Agreement with all human labels | 4/6 | 4/6 | 6/6 |
| Failure detection: bad answers rejected | 2/3 | 1/3 | 3/3 |
| Good-answer acceptance | 1/2 | 2/2 | 2/2 |
| Genuine uncertainty recognized | 1/1 | 1/1 | 1/1 |
| Unnecessary abstention on assessable answers | 1/5 | 0/5 | 0/5 |
| Bad answers incorrectly passed | 1/3 | 2/3 | 0/3 |

The final column applies the production deterministic rule to the preserved v5 model outputs. It required zero additional API calls. It is a combined-system result, not a fresh live rerun or a claim of 100% model accuracy. Raw model agreement remains 4/6; two final decisions were overridden. The live v5 model did not improve overall agreement and regressed on one date example. Prompt and computed facts changed together, so this experiment cannot isolate their individual causal effects.

## What each component measures

1. **Claim-scope prompt:** determine what the answer asserts and what it explicitly declines to verify. This addresses unnecessary abstention and good-answer acceptance. A verified Tuesday setting plus an explicit inability to verify the missing agreement can pass; claiming Tuesday matches an omitted agreement remains unassessable.
2. **Calendar facts:** verify the requested local calendar date against the stored date using datetime and ZoneInfo. Convert an offset-bearing timestamp into the user's timezone before adding one calendar day. At May 1, 2027 02:00 UTC, Toronto is still April 30 at 22:00; tomorrow is May 1. This component measures stored-date alignment, not overall response quality.
3. **LLM rubric:** grade response quality using evidence, computed facts, and worked examples. It handles meaning and disclosure that narrow date arithmetic cannot decide. Schema validity measures operational usability, not correctness.
4. **Final calendar rule:** reject a direct scheduling confirmation when its full reported date equals the verified wrong stored date. Match the requested subject or a subject-free confirmation. Preserve model_label, model_scores, decision_source, calendar_guard and label_overridden. This closes the observed false-pass path without hiding the model's error.
5. **Metric reporting:** report final agreement alongside model_agreement_all_cases and calendar_guard_overrides. Failure detection prevents dangerous false assurance; acceptance prevents an always-fail judge appearing useful; uncertainty recall and unnecessary abstention distinguish appropriate caution from avoiding assessable cases. Always show denominators: these slices contain only one to three examples each.

## Examples and scoring boundaries

- **Pass:** “The reminder is set for Tuesday. I cannot verify that it matches our agreement because that part of the history is unavailable.” Evidence verifies Tuesday and explicitly says the agreement is omitted. The answer does not assert agreement compliance.
- **Abstain:** “Yes, the reminder is set for Tuesday” in response to whether it matches the agreed day, with that agreement missing. The implied agreement claim cannot be assessed.
- **Fail:** a complete conversation says Monday was agreed, but the assistant claims the Tuesday setting used the agreed day.
- **Fail:** “Scheduled the resume review for May 2, 2027” when the verified local request date is April 30 and the request is for tomorrow. The final calendar rule applies to this unqualified confirmation.
- **Separate judgment:** an answer that honestly discloses a wrong scheduling date is not automatically failed merely because the stored date is wrong. Task execution success and truthful response quality are different measurements.

The v5 prompt adds a bounded-claim pass example using an address, separate from the reminder evaluation cases. Existing worked examples remain. The exact frozen prompt is saved beside the reports.

## Options and tradeoffs recorded

- **Prompt only:** flexible and simple, but the experiment exposed reasoning/score inconsistency. Insufficient for this observed failure.
- **Computed facts only:** makes date arithmetic auditable, but the model can still ignore those facts when selecting a score. Retained as evidence, not relied upon as enforcement.
- **Chosen: computed facts plus a narrow final rule:** reproducible for supported cases, with raw model decisions retained. The cost is limited language coverage and maintenance of a conservative recognizer.

The adapter accepts only a single schedule/book request “for tomorrow” and complete canonical timestamp/timezone/stored-date evidence. Unknown prose, compound requests, missing timezone, or additional context are unsupported; invalid dates or zones are flagged invalid. Neither case invents a date. The rule requires a full date and an unqualified Scheduled/Booked confirmation, with a matching subject if present. Negation, explanations, additional clauses, and different subjects stay with the model.

This deliberately narrow evidence adapter does not authenticate evidence, verify arbitrary natural language, fix the scheduler, authorize actions, or prove broad runtime coverage. A structured execution-evidence schema is the next useful extension. Provider failures and invalid model outputs remain missing grades; the final rule does not hide them.

## Verification and remaining work

- 465 backend tests passed, including 32 calendar/guard tests.
- Offline checks passed: 252 workflow cases / 1,518 checks, 32 quality contract grades, 11 runtime contracts. These are software checks, not live quality measurements.
- Tests cover timezone conversion, month/year rollover, leap dates, DST boundary dates, unsupported/invalid evidence, truthful error disclosure, guard exclusions, preserved raw scores, provider failure handling, and separate final/model metrics.
- All 12 live calls returned valid verdicts with no provider errors. Final-rule replay used no new calls. Replay retains original model latency; rule overhead was not timed.
- Prior human labels and baseline artifacts are protected by SHA-256 comparisons. No labels were changed to fit the judge.

Next: jointly label fresh, independently authored examples covering supported and unsupported wording, honest error disclosure, and date boundaries before another live test. Do not reuse this six-case result as a generalization estimate.

## Interview explanation

“I calibrated an LLM judge against human decisions and found that it could explain a date error while still passing the answer. Prompt changes improved handling of honest uncertainty but did not improve overall agreement. I moved date arithmetic into deterministic code and added a narrowly scoped rule for false scheduling confirmations. I retained the raw model scores so the report distinguishes model performance from the combined system. The combined rule matched all six development labels on preserved outputs, but I still need independent examples to measure generalization.”
