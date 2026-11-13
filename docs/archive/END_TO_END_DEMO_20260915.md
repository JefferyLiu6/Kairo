# Kairo backend end-to-end demo — September 15, 2026

**Task execution passed. A final-response quality defect was found.** This is a live backend orchestration demonstration using synthetic temporary task storage, plus a separately labeled offline fault-injection demonstration. It is not a frontend, HTTP, authentication or Azure deployment test.

## Live workflow

| Turn | Request | Verified persisted outcome |
|---|---|---|
| 1 | Add task to prepare end-to-end interview demo | Exactly one task created, open |
| 2 | List my todos | Same task listed; state unchanged |
| 3 | Mark that task done | Same task ID completed; no duplicate |

The successful complete attempt used nine real calls to pinned `gpt-4.1-mini-2025-04-14`: translation, online harness and humanization for each turn. Routing used the production heuristic. PM execution used the production typed workflow. Model construction was wrapped only to enforce a call cap and log usage; no live router/translator/harness/humanizer outputs were scripted. An unexpected PM model fallback was configured to stop rather than make uncontrolled calls. Task state was checked independently before/after each turn.

The live entry point was `run_orchestrator`, not an HTTP endpoint. The offline response-quality v9 judge was not invoked: the online harness is a separate component that grades PM execution output before final humanization.

## Response-quality finding

The first live reply said the user likes tackling important tasks early; the next said “Since you prefer mornings.” The initial profile/context was empty and no user message supplied that preference. This is an unsupported personalization claim. The complete attempt's third response correctly reported completion without that claim.

The earlier attempt showed the same issue. Saved humanizer input proves its initial context was empty. The humanizer prompt encourages personalization and contains a morning-preference example; this is a plausible contributor, not an isolated causal proof. The online harness accepted the PM result before the humanizer added the unsupported text. A passing execution check therefore did not protect the final response.

This finding was identified by inspecting actual inputs/responses, not by inventing human reference labels or running another paid judge. `humanizer_finding.json` points to exact local evidence. No production prompt change was made during the demo.

## Initial demo-check failure preserved

Attempt 1 stopped after two turns because the listing assertion demanded an exact substring. The response said “prepare the end-to-end interview demo,” adding “the” to the stored title. State was unchanged and the listing was semantically correct. This was a demo assertion issue, separate from the real unsupported-preference defect.

The runner's comparison was corrected to ignore articles, with independent state equality still required. Attempt 2 restarted with a new empty temporary store and completed all three turns. Both attempts, their responses and the original failed assertion are preserved; this is not a first-attempt-perfect claim.

## Recovery demonstration (controlled offline)

The existing offline demo passed create/list/complete and ambiguity resolution. An ambiguous interview-task request changed neither task; selection completed only the technical task. A controlled PM call performed one real temporary-store write and then raised a timeout. The production recovery loop disclosed that completion could not be confirmed and did not replay the write: one PM call, one persisted task.

Routing/translation, the fault-producing PM call and judge behavior in this recovery section are fixtures. This proves the tested recovery behavior under controlled injection, not a live provider outage. Recovery telemetry contains one intentional fallback. It is stored separately from live telemetry and must not be interpreted as a production failure rate.

## Monitoring and usage

The complete live attempt has 3 observed turn spans, 9 LLM spans, full reported input/output usage coverage, no observed fallback and no observed judge parsing/provider errors. These are runtime observations, not proof of response correctness—the unsupported preference still occurred.

Attempt 1: 6 calls. Attempt 2: 9 calls. **Total: 15 calls**, within the announced overall cap; SDK retries disabled, maximum 1,024 output tokens per call and 30-second timeout. The second attempt reran the scenario after correcting the test assertion; it was not an SDK retry. Combined provider usage: 14,726 input tokens, 737 output tokens, 15,463 total. No dollar amount is inferred.

The Desktop dashboard now uses only the complete live attempt's telemetry for its runtime snapshot and retains all seven historical judge reports separately. It is not continuous monitoring. The fault-injection logs remain separately labeled. No personal task store, external calendar or Azure resources were used; temporary stores were removed after snapshots were saved.

## Interview walkthrough

1. Open `attempt2/live_demo.json`: show the three requests, actual responses and before/after task states.
2. Open `attempt2/live_runtime_summary.json`: explain observed pipeline stages, nine calls and usage coverage.
3. Show the unsupported preference and empty-context input. Explain why execution success differs from truthful final wording.
4. Open `attempt2/offline_demo.json`: show ambiguity handling and one-write recovery, clearly identifying controlled fixtures.
5. Open the Desktop monitoring dashboard: distinguish historical judge evaluation, this live runtime sample and the offline fault injection.

Next useful fix: constrain final personalization to explicit profile/user evidence and add empty-profile/known-profile contrast checks. Consider validation after humanization as a separate cost/latency decision. The current demo is complete; that discovered defect remains open.

Repository revision observed during the demo: `5c68a8da0888b288292cd0aea46358be6ca25fb2`. No application code, commit or deployment was changed for this run.
