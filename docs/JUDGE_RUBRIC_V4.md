# Kairo rubric v4: evidence sufficiency and dates

September 13, 2026. Prompt revision implemented; new human review and live comparison pending.

## Before

The preserved v3 live run matched Jeffery's labels on 6/8 development examples. It failed to abstain when the prior agreement was explicitly omitted, and incorrectly accepted September 14 as tomorrow from September 12. Eight valid API outputs did not mean eight correct judgments.

## Changes

1. **Explicit decision order.** Check for a provable defect first. If none is established, check whether a material claim requires explicitly omitted evaluation context. A stored value does not prove agreement with missing history. This must lead to abstention when the claim depends on that history. A complete record reporting a timeout or rejection still cannot support unqualified success. A response that confines itself to verified facts and accurately discloses the gap can pass.
2. **Requested versus stored versus reported date.** Resolve tomorrow using the request timestamp converted to the supplied user timezone, plus one local calendar day. Compare that date with both storage and response, accounting for month/year/leap-day boundaries. Matching storage alone cannot justify a pass. The brief verdict reason must state the resolved requested date and the comparison.
3. **Two distinct worked examples.** An omitted delivery-address agreement teaches the context distinction; a leap-day inspection teaches the date check. Neither copies the original eight examples or the new six review examples. The existing four examples and JSON score schema remain.
4. **Version and review integrity.** Rubric version and hash change to v4. Old human review packets remain v3 and are not silently relabeled or re-certified under v4. A regression verifies that the importer rejects an old-version packet.

These changes clarify the previously agreed policy; they do not change the original human labels. The prior baseline directory and original labeled packets are fingerprinted before and after applying the change.

## Verification and limits

432 backend tests pass; 252 workflow scenarios / 1,518 checks pass; quality replay and runtime contract checks pass. These verify software contracts and example output schemas, not that a model now follows the new instructions. No new live API call was made in this increment. Live improvement remains unmeasured.

Six new contrast examples have blank labels and a frozen v4 review packet. They include explicit omitted context, complete contrary context, bounded uncertainty disclosure, month rollover, year rollover, and timezone conversion. No expected labels or model outputs are supplied to the reviewer. These are newly authored development cases, not independently sampled production traffic or an unseen holdout: their topics were selected in response to observed failures.

## Next comparison

Collect Jeffery's six labels and short rationales before showing model output. Then compare frozen v3 and v4 prompts on the same six cases, same model snapshot gpt-4.1-mini-2025-04-14, temperature zero, one attempt per prompt/case, at most 12 API calls, retries disabled and bounded output. Save all results separately. Do not rewrite the original 6/8 baseline or count it as a matched before measurement for a different dataset.

Report exact matches out of six, per-case disagreements, bad-answer false passes, missing-context recognition, schema/provider failures, and cost. Each class is small, so counts and examples matter more than percentages. One run per case cannot establish repeat stability. Do not tune again on the six before recording the first comparison.

## Trade-offs

The longer prompt may improve attention to these distinctions but costs more tokens and can still make calendar arithmetic errors. It adds no deterministic calendar tool: verify dates independently in application/state tests and do not use a judge score to authorize mutations. A missing-evidence rule must remain bounded so it does not turn clear wrong answers into abstentions.

This workflow follows [OpenAI's evaluation guidance](https://developers.openai.com/api/docs/guides/evaluation-best-practices): use scoped task examples, maintain human feedback, and compare changes against measured results. No claim of state-of-the-art performance is made.

Interview explanation: “I used the live disagreement analysis to refine two decision boundaries, versioned the prompt, and preserved the original labels and result. I separated software-contract verification from semantic evaluation and prepared new contrast cases for human review before a controlled model comparison.”
