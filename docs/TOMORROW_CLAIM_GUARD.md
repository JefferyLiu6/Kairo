# Unsupported “tomorrow” confirmation: targeted correction

September 14, 2026. Calendar confirmation guard v2; response-quality prompt remains v8. This is a deterministic evaluator correction, not new evidence that the LLM judge improved.

## Before and after

The human reference marks this answer Fail: “Done—the reminder is saved for tomorrow, June 7, 2030.” The evidence confirms the stored date but explicitly says the request timestamp and user timezone were omitted. A stored date alone cannot establish its relationship to “tomorrow.” The saved v7 judge output incorrectly passed the answer, even though its reasoning quoted the unsupported claim.

The new guard identifies that bounded assertion and sets final groundedness to 0, semantic abstention to false, and final label to Fail. It preserves `model_label`, `model_scores`, `decision_source`, the rule version/reason and `label_overridden`. Existing reports and human labels remain untouched. Task completion remains unknown; the rule rejects an unsupported response claim rather than computing or disproving the actual requested date.

The paired response “The reminder is saved for June 7, 2030” remains Pass when replaying its saved model result. It reports storage without asserting that the date equals tomorrow.

## Supported scope and exclusions

All three inputs must match a conservative whole-input grammar:

- A single `set`, `schedule` or `book` request for a named subject “for tomorrow.” Compound/quoted requests do not match.
- A canonical saved-date record for that same subject, followed by explicit omission of both request timestamp and user timezone. Generic parsing failure, absent fields without this record, partial omissions, additional context or arbitrary prose do not trigger the rule.
- An unqualified affirmative saved/scheduled/booked confirmation for that subject (or subject omitted), containing “tomorrow, DATE” or “DATE, which is tomorrow.” An optional “Done” prefix is supported. The reported date must parse and equal the saved date.

Whole-answer matching excludes questions, negation, quotes, task titles, uncertainty qualifications, honest error disclosures and additional clauses. Other subjects, invalid dates and different reported dates are left to other evaluation paths. Unknown wording remains the model's responsibility; no general natural-language correctness claim is made. Partial context omissions and broader paraphrases require separate designs and tests.

The guard runs only after a schema-valid model verdict. It never converts provider errors or invalid output into a successful grade. Calendar computation still reports unsupported when the timestamp/timezone cannot be determined; this rule does not invent calendar facts. The existing wrong-date confirmation rule remains available for complete supported records.

## Evidence and measurement

548 backend tests passed, including 33 new cases for this guard. They cover affirmative forms, different subjects/dates, negative controls, explicit context requirements, preserved raw grades and operational failures. Offline workflow, quality replay and runtime contracts passed; changed Python files passed lint.

A separately saved replay of two original v7 outputs shows:

| Case | Original model | New final result | Rule applied |
|---|---|---|---|
| Plain stored-date report | Pass | Pass | No |
| Explicit unsupported tomorrow/date relationship | Pass | Fail | Yes |

The artifact is labeled `saved_output_postprocess`, with zero live calls and the original report hash. This fixes the known evaluator false pass within the supported grammar. It is not a fresh API run, held-out benchmark or improved model agreement. The original v7 live result remains 9/12, including its two invalid citations; v8 citation selection has not been measured live. No invalid historical citations were repaired.

## Decision and tradeoff

A deterministic rule is appropriate for this small, explicit policy boundary after prompt clarification failed on the recorded example. It makes the known decision reproducible without another API call. The cost is deliberately limited coverage and maintenance of a supported grammar. Broadening the rule to every occurrence of “tomorrow” would risk rejecting truthful uncertainty, quoted task data or supported requests. Keep raw/final metrics separate so rule-assisted correctness is not attributed to the model.

Interview explanation: “I preserved the judge's wrong answer, reproduced it locally and added a narrow rule for an explicit claim with explicitly missing evidence. Contrast tests protect truthful reporting. The final grade improves for that case, while the raw model score stays visible. I can explain exactly what the rule proves and what it leaves unresolved.”
