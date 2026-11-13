# V7 correction and local interview readiness

September 14, 2026. Code and offline demonstration verified. The completed live v7 run matched 9/12 unchanged development references, versus v6 11/12. V7 remains experimental and did not demonstrate improvement. The frozen v6 first measurement remains 11/12 with one false pass.

## Before and after

1. **Explicit date relationship:** v6 passed “Done—the reminder is saved for tomorrow, June 7, 2030” despite omitted request timestamp/timezone. The model reduced the whole answer to a stored-date report. V7 explicitly separates the saved-date fact from the claim that it equals tomorrow. It requires support for that relationship and includes a worked failure example in a different parcel-pickup scenario.
2. **Missing-request precedence:** the user labeled “Updated the booking” Fail when the request and prior state were omitted. V6's initial blanket missing-request abstention conflicted with that judgment. V7 checks identifiable execution assertions first; unsupported/contradicted update claims fail even when the request is missing. If the remaining answer cannot be assessed without the request, abstain. Missing candidate answers still abstain.
3. **Avoid superficial keyword grading:** negation, quoted data and explicit uncertainty do not become success claims merely because they contain “done” or “tomorrow.” The deterministic calendar rule remains unchanged; no broad keyword override was introduced.

Task completion remains separate from response quality. Fail means unsupported by the supplied evaluation record when that is the criterion, not proof of real-world dishonesty. No historical labels or baseline reports were rewritten; old rubric-bound review packets must not be silently imported as v7 reviews.

## Verification and limits

499 backend tests and all offline stages passed. All worked examples pass the production output parser/label contract. These checks do not prove live judge behavior.

A new offline interview demo ran successfully against temporary synthetic stores: create/list/complete with independent state reads; ambiguous target clarification and selection; controlled timeout after a real local write, verifying one call and one persisted task. The demo uses real PM workflows for success/targeting and production orchestrator recovery with clearly labeled routing/translation/PM/judge fixtures for fault injection. It is not a live end-to-end LLM demo. No API key, real user store, external calendar or cloud deployment is required.

## Final verification plan

Reuse the same 12 synthetic examples and unchanged human labels for one v7 call each. Pin gpt-4.1-mini-2025-04-14, temperature 0, 1,536 maximum output tokens per call, 30-second timeout and zero retries. Record both original model and final labels, provider/parse failures, prompt hash, exact payloads and token usage. Never include human labels/rationales in model requests. This requires approval for up to 12 new billable OpenAI calls; the previous approved run's 12 calls are already complete.

Inspect examples 3/4 together (state report versus explicit tomorrow claim), 7/8 (missing answer versus identifiable assertion with missing request), all failure disclosures, and any newly broken cases. Do not silently exclude a failure. If errors persist, document them as limitations rather than continually adding rules or claiming release readiness. Keep v6's fresh result as the before artifact; this v7 verification uses development cases already examined and is not new generalization evidence.

## Tradeoff

V7 clarifies policy precedence and compositional claims while avoiding a brittle substring classifier. It still depends on semantic model judgment for most wording and evidence. The missing-request change deliberately favors rejecting assessable unsupported assertions over a blanket abstention. Export completeness remains relevant: richer evidence may change a verdict without any change to the actual response.

## Completion status

Implemented: core agent recovery and target resolution, evaluation metrics and calibration, local monitoring, interview documentation, and the repeatable offline demo. Final live verification completed; one false pass and two invalid citations remain. The judge is not a validated automatic release gate. Azure deployment remains user-led and dependent on subscription/quota availability. No automatic deployment, commit or push is performed.


## Final live v7 result — September 14, 2026

Verification completed with all 12 approved calls. V7 usable model/final agreement is **9/12**, versus v6 **11/12** on the same development cases. One false pass remains (example 4); two outputs are invalid because their citations combine separate fields (examples 8 and 10). Valid outputs 10/12, failure detection 3/6, good-answer acceptance 5/5, deliberate uncertainty recognition 1/1; no provider errors or rule overrides. The invalid outputs are not semantic abstentions. No labels, baseline reports, or validator rules changed.

This supersedes earlier pending-verification status. Offline tests/demo passed, but v7 did not demonstrate a live improvement. The local interview milestone is ready; the judge remains experimental and is not a validated release gate. Detailed artifacts are retained outside the repository in the Desktop verification report and four-run dashboard. No further paid calls, deployment, commit or push performed. Azure remains user-led.
