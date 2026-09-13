# Kairo: fixing task target resolution

September 12, 2026. Task-resolution implementation and validation record.

## Outcome and evidence

The preserved 14-scenario / 24-turn reproduction set improved from **8 state-contract passes, 5 failures, 1 unscored case** to **13 passes, 0 failures, 1 unscored case**, repeated twice with matching source hashes and normalized outcomes. This is a focused development set, not a production success rate or live judge accuracy estimate. No model fallback was needed on the after runs. Network connections were blocked by the reproduction harness; zero attempts occurred.

The full offline runner passes: **411 backend tests**, **252 workflow scenarios / 1,518 checks**, **32 quality-judge replay grades**, and **11 runtime-judge contract cases**. The new target-resolution module has 26 regression tests. Nine initial tests were verified failing against the unchanged Desktop source before the fix; these now pass. Two existing workflow expectations were corrected, explained below.

The original and after artifacts are retained outside this repository on the developer’s Desktop. Run `make eval-offline` for repository checks; target regressions are in `backend/tests/test_todo_target_resolution.py`.

## Before, after, and effect

| User interaction | Before | After | Why it matters |
|---|---|---|---|
| “Mark my interview task done” with two matches | Search included “done”; not found | Presents candidates and retains their IDs | Ambiguity becomes a recoverable dialogue |
| Follow with “The technical interview”, its ID, or “the first one” | No retained selection; could reach model fallback | Selects exactly one displayed candidate | Completion is verified against stored task state |
| “List my todos”; “Complete the second one” | No completion | Uses the second displayed ID | Reordering storage cannot silently change the target |
| “Actually, complete the portfolio review task instead” | Searches for “portfolio review instead” | Replaces the pending request and completes portfolio review | Explicit correction wins over an old pending target |
| “Add a task” | Creates a task literally called “a task” | Asks for the title; waits | Missing information cannot produce a misleading write |
| “Never mind, cancel that” | Baseline never established pending state, so cancellation was unverified | Cancels real pending creation/selection; subsequent yes/ID cannot execute it | Tests actual cancellation, not just an accidental no-op |
| Remove a nonexistent task | Could create an approval request for an unresolved target | Reports not found with no approval | An approval is attached to a concrete target |

## Decisions and trade-offs

**Resolve deterministically against stored evidence.** This is task identity lookup, so an LLM confidence score is unnecessary. A unique candidate can be selected; multiple matches ask again. This is explainable and works before Azure access, with less flexibility for paraphrases than a semantic matcher. Ambiguous exact duplicate titles still require an ID or number.

**Reuse persisted working memory.** Candidate IDs and task snapshots live in existing SQLite working memory, scoped to account and conversation. The existing disambiguation mode expires after ten minutes. Reusing it provides cancellation, expiry and inspection without adding another memory system. Trade-off: this holds one active conversational choice set; a new request replaces it rather than maintaining a stack of unrelated pending actions.

**Bind an ordinal to the displayed list.** Store the order actually shown, verify the rendered list matches the snapshot, and revalidate the selected task before normal planning. If the selected task was renamed, removed, or otherwise changed, ask for a fresh list. Reordering alone is safe because selection uses a stable ID. Trade-off: conservative rejection can require another user turn. This does not add transactional concurrency control to the underlying JSON task store.

**Keep selection separate from authorization.** Selecting a task resumes the ordinary planner and approval policy. Deletion still requires approval. A bare “first one” after merely listing tasks does not imply an action. A command such as “Complete the second one” supplies that action.

**Limit command cleanup to task syntax.** Remove the command’s completion/correction suffix rather than changing schedule parsing. Existing quoted mark-complete syntax retains literal title text, including apostrophes, “as done”, and “instead”. This is a targeted grammar improvement, not a universal natural-language parser.

**Keep mixed-action policy unchanged.** The mixed request still adds its first task, then reports ambiguity and stops. It remains unscored because Jeffery has not chosen atomic execution versus disclosed partial execution. This fix retains selections for single-action requests only; multi-action resumability is still future work.

## Why these metrics fit the problem

- **Persisted-state contract: 8/13 → 13/13 scored scenarios.** Compare actual IDs, task fields and completion flags against expected state after every turn. This measures whether the right task changed and unrelated tasks remained intact. It catches wrong-target writes that a fluent response might hide. The set was used for development, so it is not an unbiased generalization estimate.
- **Previously failing scenarios recovered: 5/5.** Uses exactly the preserved commands and seeds, so the measured effect is attributable to the behavior change rather than replacing difficult cases.
- **Dialogue evidence.** Retained pending choices, explicit cancellation replies, and zero intercepted model-fallback calls in the after set show the repair is deterministic. State-only no-ops would not establish these properties.
- **Safety regressions.** Tests cover unresolved choices, expiry, cross-account/thread isolation, changed candidates, quoted titles, and approval preservation. These protect invariants; their count is not an accuracy metric.
- **Repeatability.** Two identical source hashes and matching normalized outcomes establish repeatability for this offline harness. They say nothing about a live LLM's variance.

## Eval expectation changes

The old 252-case suite initially reported 250 passes after the implementation. Two intent-classification fixtures, “Delete my gym task” and “Remove task to call dentist”, seeded no matching todos but expected an approval to be created. Their intent expectations stay REMOVE_TODO; action/mutation now expect none and approval false. A new test verifies no approval for a missing target; a separate seeded ambiguity test verifies approval is still requested for an existing selected target. Thus the final 252/252 result uses two corrected expectations, not a wholly unchanged suite.

## Architecture walkthrough for an interview

User text → task entity extraction → target resolver → either a concrete task ID or persisted candidate choices → selected ID revalidation → normal action planner → approval policy → executor → stored-state checks and decision log.

Interview phrasing: “I reproduced failures through the normal local agent entry point and checked the database state rather than trusting its reply. The main bug was missing dialogue state, not model intelligence. I added expiring, conversation-scoped candidate IDs and kept authorization in the existing execution path. Five reproduced failures became zero, while retaining controls for stale targets and deletion approval. I report that as a development regression result, not live model accuracy.”

## Remaining work and your next decision

No Azure deployment or API key is needed for these checks. Live model routing, response rewriting and judge calibration remain unmeasured. The next useful step is fault recovery: simulate a successful write followed by a lost response, then test whether retry creates a duplicate. Before changing mixed-action behavior, choose between all-or-nothing preflight (fewer partial changes, more blocked useful work) and explicit partial execution (more progress, requires reliable step tracking).
