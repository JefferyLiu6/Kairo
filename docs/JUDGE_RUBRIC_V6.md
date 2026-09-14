# Kairo rubric v6 — assertion support and separate outcomes

September 14, 2026. Implemented policy approved by Jeffery after the v5 challenge review.

## Decision and before/after behavior

Task completion and response quality remain separate. V6 grades whether identifiable assertions are supported by the supplied evaluation record. A failure under this policy does not necessarily establish a real-world falsehood or prove that the acting assistant lacked information at execution time.

| Situation | V6 response-quality policy |
|---|---|
| Verified stored value, no explicit compliance assertion, no proved contradiction | Can pass even if omitted context leaves task completion unknown |
| Explicit “as agreed”, “on the requested date”, “all set”, or yes to a compliance question, without supporting evidence | Fail assertion support, even when the supporting evidence was omitted from the export |
| Known wrong requested date or target confirmed without disclosure | Fail; reporting storage does not excuse a proven mismatch |
| Honest disclosure of execution failure | Can pass response quality while task completion fails |
| Candidate/request missing or unusable; material claims cannot be identified | Abstain |
| Provider error or malformed grade | Operational missing grade, not semantic abstention |

This intentionally changes v5's missing-agreement policy. V5 instructed the judge to abstain when omitted agreement context was necessary to verify a claim. V6 rejects the explicit unsupported assertion instead. Historical labels, review packets and baseline reports are unchanged; v5 packets are not silently recertified under v6.

## Changes made

- Versioned prompt to response-quality-v6 with an explicit claim-scope decision order and the known-wrong versus unknown-correctness distinction.
- Changed the omitted-agreement worked example to Fail; changed the abstention example to an unavailable candidate answer so it no longer contradicts the adopted policy.
- Added a verified stored-date Pass example with omitted timezone, using a delivery scenario separate from the challenge examples. Existing bounded disclosure and known wrong-date examples remain.
- Updated newly exported review-packet guidance to match v6. The importer still rejects mismatched rubric versions/hashes; it was not weakened to accept historical packets.
- Added eight worked-example contract tests using the production parser and quoted-evidence check. These verify valid examples and declared labels, not live model accuracy.
- Calendar arithmetic, final date rule, task executor, output schema and numeric label aggregation were not changed.

## Measured effect on the same eight human-labeled cases

| Metric | Preserved v5 live run | V6 live run |
|---|---:|---:|
| Raw model agreement | 6/8 | 8/8 |
| Final response-quality agreement | 6/8 | 8/8 |
| Human-failed responses rejected | 2/3 | 3/3 |
| Human-passed responses accepted | 4/5 | 5/5 |
| False passes among human-failed responses | 0/3 | 0/3 |
| Unnecessary abstentions against supplied response labels | 1/8 | 0/8 |
| Calendar rule label overrides | 0 | 0 |
| Valid outputs | 8/8 | 8/8 |

Both earlier disagreements changed: the missing-timezone stored-date report now passes; the unsupported “as agreed” assertion now fails. The other six decisions remained aligned with the human labels. Raw model and final scores are identical here: the date rule matched one case whose model grade was already Fail.

Eight new live calls used gpt-4.1-mini-2025-04-14, temperature 0, maximum output 1,536 tokens, no retries, and one call per case. The v5 baseline was preserved rather than rerun. Labels were excluded from model inputs. The historical reviewed dataset was reused explicitly as a comparison reference, not imported as a new v6 human review. Task completion remains separate human annotations (2 Pass / 3 Fail / 3 Abstain); this experiment does not automatically score or execute tasks.

## Why these metrics

Agreement answers whether judge decisions match the user's policy. Failure detection checks rejection of problematic answers; acceptance checks that truthful answers are not over-penalized. Neither metric alone is enough. False passes remain zero in both runs, but v5 missed one human-failed case by abstaining; zero false passes therefore did not mean full failure detection. Calendar overrides expose whether results come from model judgment or deterministic rules. Valid-output rate measures operational reliability.

Response-quality uncertainty recall is **undefined**, since this packet has no response-quality Abstain references. Task-completion abstentions cannot be borrowed for that denominator. Broader abstention behavior is unmeasured by this live set.

## Tradeoffs and limitations

V6 matches the user's stricter record-support preference, but loses v5's distinction between absent evidence due to export truncation and actual lack of support at execution time when grading an explicit claim. Explain Fail as “unsupported by this evaluation record,” not automatically “the agent lied.” More complete telemetry can change the grade without the response changing.

Plain scheduling statements are treated as stored-state reports unless evidence proves a mismatch; explicit compliance language raises the support requirement. This makes phrasing consequential. It needs additional adversarial and paraphrase testing, including minimal-pair phrasing and fully supported agreement claims, to ensure the judge does not reward evasive wording. Do not remove the known-mismatch failure rule.

The eight cases informed v6 design. The 6/8 to 8/8 change is development-set alignment after an approved policy revision, not independent proof of generalization or a pure model-capability improvement. One call per example and separate run timing do not establish repeatability or isolate stochastic variation. We did not modify labels to raise the score. The earlier six-case and v5 results remain intact.

## Verification and next step

474 backend tests passed. Offline workflow, quality replay and runtime contract checks passed. Lint passed for changed Python files. Prompt examples validate through the production parser. Live run returned eight valid grades, with no provider/parser errors.

Next: label a fresh set under the explicit v6 policy, including unusable exports (Abstain), supported agreement claims (Pass), unsupported assertions (Fail), and paraphrases. Freeze it before a new live run. Keep monitoring task completion separately rather than averaging these dimensions.

## Interview explanation

“I separated task outcomes from response quality after human review showed that an honest failure report should not receive two penalties. We then chose an explicit assertion-support policy. Versioning mattered: an omitted agreement had meant Abstain under the old policy, so changing it to Fail was a policy decision, not just fixing a model bug. The new prompt matched eight of eight development labels versus six of eight before; I retained raw model outputs and all original labels, and do not present that small tuned result as production accuracy.”

The workflow uses clear criteria, worked examples, human calibration and explicit scope limits, consistent with [OpenAI evaluation best practices](https://developers.openai.com/api/docs/guides/evaluation-best-practices). Those practices inform the method; they do not establish this project's accuracy.
