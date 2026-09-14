# V8 citation transport: selection instead of transcription

September 14, 2026. Experimental development change; no new live model calls. V7's measured 9/12 remains unchanged. V6's 11/12 comparison remains unchanged. Human task/response labels and rationales are preserved.

## Problem and decision

In the v7 live run, examples 8 and 10 assigned failure-level scores but joined response text and tool evidence into a single supposed exact quote. The strict validator correctly rejected both. Relaxing substring validation would conceal this failure. V8 instead supplies source-bound citation options and asks the judge to select one identifier.

The scoring policy remains v7's policy. The rubric version changes to `response-quality-v8` because the payload and output contract change; old rubric-bound human review packets must not be silently imported as new v8 reviews.

## Architecture

1. Preserve the full request, response, tool evidence and deterministic calendar facts in the model payload.
2. Add `citation_options` containing exact response/evidence windows: record-scoped ID, source field, start/end offsets and text. Windows are at most 400 Python Unicode characters, with a 300-character stride (up to 100 characters of overlap). No window crosses source fields. Whitespace-only spans are skipped; empty inputs do not invent evidence. Full source text remains available for context.
3. The judge returns `reason`, `citation_id`, three dimension scores and `abstain`. Worked pass/fail/abstain examples use the same catalog/ID format.
4. The parser recomputes the catalog from the current response/evidence, resolves one exact ID, and produces the existing normalized `evidence_quote` score record. Unknown IDs, IDs bound to another response/evidence record, conflicting quote-plus-ID fields and malformed score fields fail validation.
5. Successful results retain `citation_format` and `model_citation` source/offset metadata. A calendar rule may change the final quote/grade; `model_citation` still describes the original model selection, alongside preserved model scores when a guard applies.

IDs contain a truncated content digest for record binding, not an authentication or authorization mechanism. Citation text remains untrusted task data. An ID existing proves location, not relevance, truth or that it supports the assigned score. The remaining false pass about “tomorrow” is not solved by this change.

## Compatibility and tradeoffs

The reader also accepts the old exact-quote format, with the original strict contiguous-substring check. This supports authored replay fixtures and existing integrations. It is not an automatic repair path: the two saved concatenated quotes still fail. New live prompts request IDs only; any future result must report legacy-format responses separately rather than claiming ID adoption from general validity alone.

Selection avoids quote transcription and concatenation errors for accepted ID outputs. Costs: additional input tokens from the catalog, a new wire contract, and coarser excerpts than a human-selected phrase. Overlap helps with boundaries but does not guarantee every proposition fits one window. Full context remains available. This implementation does not enable provider-enforced structured output, introduce another model call, or change model selection.

## Verification and meaningful next measurements

- 515 backend tests passed, including 16 new citation cases: valid resolution; concatenation rejection; unknown/wrong-type/foreign IDs; mixed representations; strict score validation; Unicode offsets and source boundaries; deterministic catalog; empty inputs; and the fact that a valid citation cannot repair a wrong semantic grade.
- Workflow evaluation, judge replay and runtime contracts passed. Changed Python files passed lint.
- Two explicitly authored transport fixtures, based on the failure shapes in examples 8/10, supplied valid IDs and parsed successfully. Original raw outputs remained invalid. This is a contract demonstration, not new model accuracy or a re-scored baseline.
- No new calls, no relabeling, no deployment and no commits.

A future separately approved live run should report usable verdict rate (operational reliability), valid ID-format outputs / all attempts (actual contract adoption), false passes and failure detection (semantic reliability), deliberate abstention separately from errors, and input token usage (catalog cost). Each denominator must include errors where appropriate. Do not report offline fixtures as evidence that the model will select correct IDs or correct judgments.

## Interview explanation

“The model sometimes gave the right judgment but an invalid citation. I kept the validator strict and changed the interface: select an evidence ID rather than generate a quotation. Code resolves the exact source span. That makes accepted citations traceable, but traceability does not prove the reasoning is correct, so I measure output validity separately from human-label agreement.”

The general approach of task-specific tests, logging failures and combining automated checks with human judgment follows [OpenAI evaluation best practices](https://developers.openai.com/api/docs/guides/evaluation-best-practices). The citation-ID design is a project-specific engineering choice, not an official guarantee of model reliability.
