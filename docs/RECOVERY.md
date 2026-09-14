# Kairo recovery: before, after and trade-offs

September 13, 2026. Development milestone 3. Implementation and validation record.

## Result

Ten initial fault/control cases ran against the unchanged Desktop checkout (commit b80a563): **6 passed, 4 failed**. The same ten now pass. Eight additional checks bring the recovery suite to **18 passing cases**. Full offline validation: **429 backend tests**, **252 workflow scenarios / 1,518 checks**, **32 quality-judge replay grades**, and **11 runtime-judge contract cases**. No existing expectations were changed in this increment.

These are deterministic development checks using the production orchestrator recovery loop, controlled router/translator/model/PM outputs, and real local task persistence. They do not establish live-model quality, cloud availability, or exactly-once execution. Faults are injected at explicit boundaries; this is not an Azure outage test.

Recorded evidence is retained outside the repository in the Desktop Kairo_Recovery_Results folder. Run `make eval-offline` or `cd backend && uv run --offline --extra dev pytest tests/test_recovery.py -q`.

## Walkthrough

1. Reproduce the failures before changing implementation. Keep the six already-safe controls so progress cannot hide a regression.
2. Catch formatting failure after execution, then return a deterministic fallback. Do not re-enter the PM loop.
3. Validate the actual text of proposed read retries, not just the action's existing is_write flag.
4. Treat invalid existing task data as unavailable, rather than empty.
5. Write and flush a temporary sibling file, then atomically replace the task file.
6. Check both stored state and the final response; run the complete offline regression suite.

| Case | Before | After | Effect |
|---|---|---|---|
| Task add succeeds; humanizer times out | Task exists, but exception escapes without a final answer | One task exists; user receives “I couldn't confirm that was saved. Check your task list directly before trying again.” | Honest recovery without an automatic duplicate |
| Read judge proposes “Add task to unwanted” | Retry inherits read metadata but executes changed prompt, creating unwanted tasks | Rejects proposed retry before another PM call | Judge cannot expand a read retry into a write |
| Existing todos.json is corrupt | Load returns empty; adding a task overwrites evidence | Load raises a storage error; original bytes remain | Missing data and unreadable data are distinguished |
| File replacement fails | Direct file write had no atomic-replacement boundary; fault injection was not reached and new content replaced old | Failed replacement leaves old file intact and removes temporary file | A failed replacement does not destroy the previous snapshot |
| Write commits; PM response times out | Already protected | One invocation, one task, uncertainty disclosed | Retained no-replay control |
| Judge outage, malformed output, or retry verdict after write | Already protected | One invocation per case, one task, no automatic retry | Retained write-safety controls |
| Reads keep failing with cached results | Existing policy | Three attempts total, then explicitly stale snapshot | Bounded latency/cost and no claim of freshness |
| Read fails once then succeeds | Added coverage | Two attempts, fresh result, no mutation | Recovery does useful work rather than only refusing actions |
| Completion repeats | Existing state behavior | Same single completed task | State-idempotent completion; does not imply idempotent creation |

## Decisions and trade-offs

**Execution versus presentation.** A formatter failure happens after the action may have committed. Recovery returns conservative uncertainty and asks the user to check state. Replaying the action to get a nicer response would risk duplication. Alternative: return the raw tool response. That could preserve more detail, but tool text is not uniformly safe or independently verified, so this increment uses the existing deterministic fallback.

**Allowlist retry text, not only metadata.** The same-resource retry accepts a narrow full command grammar: show/list the supported schedule, task, or habit resource, with limited date qualifiers. Compound instructions, mutations, different resources, and dialogue replies are rejected. Existing supported corrected reads still work. This intentionally sacrifices some harmless paraphrases. A future typed read tool would provide broader expressiveness without unrestricted retry prompts. This guard applies to automatic retry prompts; it is not a new validation layer for the initial translator output.

**Missing is not corrupt.** A genuinely absent task file still represents a new empty list. A malformed or unreadable existing file prevents further task writes. This favors preservation over apparent availability. It does not automatically repair corruption or invent missing tasks.

**Atomic replacement.** Serialize into a sibling temporary file, flush and fsync it, then os.replace the old path. Failure before replacement leaves the previous bytes available. This adds a small disk-write cost. It does not provide multi-process locking, prevent concurrent lost updates, guarantee directory durability across power loss, or automatically recover abandoned temporary files after a process is killed.

**Reuse the existing retry policy.** Writes are not automatically retried; allowlisted reads have a maximum of two retries after the initial call. The existing fallback log and turn decision record capture recovery, and content-free telemetry now emits recovery outcomes for blocked retry prompts and response-generation failure. No new monitoring service or Azure resource is needed.

## Metrics and why they apply

| Metric | Definition and observed evidence | What it measures / limitation |
|---|---|---|
| Original recovery contracts | 6/10 before → 10/10 after | Same commands/fault definitions; includes state, response, and call-count assertions. Development set, not production reliability |
| New regression coverage | 18/18 final tests | Adds empty formatting, flush failure, transient read recovery, and five unsafe retry forms. More tests are coverage, not an accuracy increase |
| Automatic write replay | One PM call and one stored task in committed-write timeout, humanizer failure, and each of three judge-fault cases | Protects against duplication within one orchestrator turn; does not cover client resubmission |
| Retry-induced unsafe mutation | Original hostile retry suggestion creates unintended data; after, zero tasks and only the original read call | Checks authority expansion at the exact failure boundary |
| Bounded read attempts | Three when exhausted; two when second attempt succeeds | Measures actual retry behavior and successful recovery separately |
| Storage preservation | Byte-for-byte equality after injected flush/replacement failure and after corrupt-file write attempt | Stronger evidence than a success/error string; excludes power failure and concurrency |
| Disclosure contract | Uncertain writes disclose uncertainty; cached read fallback states that it may not reflect current data; exception sentinel is absent from user reply | Checks honesty and error-message hygiene, not prose quality |

Safety and task completion are different: refusing an unsafe retry is a correct recovery but does not complete the requested read. Likewise, a write may complete while its response cannot be confirmed. These cases are not counted as successful user tasks merely because no duplicate appeared.

## Interview explanation

“I tested recovery at the side-effect boundary. A write can succeed before the response fails, so retrying on an exception is unsafe. Kairo already blocked automatic write retries, but I found that a read retry could inherit read metadata while a judge changed its prompt into a write. I constrained the retry command itself, added a deterministic fallback for response-generation failure, and made task-file writes atomic. I verified real persisted state and invocation counts. Four initial failures became zero, but I do not claim exactly-once execution.”

## Remaining choices and limits

Manual resubmission of an add request can still create another task. A future request-ID design could deduplicate the same submitted operation while allowing two intentionally identical tasks. Deduplicating by title alone would incorrectly reject legitimate repeats, so it was not added.

Mixed-action partial execution remains unchanged and still needs a user policy decision. Durable multi-action resume, cross-process concurrency, transport disconnect recovery, and live Azure behavior remain outside this increment. Next planned milestone is guided human review of judge examples; a separate request-ID increment is an option if duplicate submission is the higher priority.
