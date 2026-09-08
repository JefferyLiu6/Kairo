# Case study: capture found a quoted-task completion bug

Base revision: `0521e31`. Reproduced September 8, 2026 in an isolated temporary store.

## Reproduction

1. `Add task to prepare interview examples`
2. `List my todos`
3. `Mark the 'prepare interview examples' todo as done`

Before the fix, the third response was:

> Couldn't find a task matching 'prepare interview examples' as done'.

The independent persisted-state snapshot still showed `done=false`. Creation and listing had
succeeded, so the completion request should have resolved the existing title.

## Cause and change

The generic lookup cleaner retained command suffix text in the task query. A narrow full-match
pattern in `extract_pm_entities` now extracts the quoted title for `mark ... done/complete/completed`
commands, optionally including `the/my`, `todo/task`, and `as`. Only the COMPLETE_TODO path uses
this pattern. Quoted title content is preserved, including `as done` inside the title.

## Evidence

After the fix, the same capture sequence changes the task from `done=false` to `done=true`.
The regression test independently reads persisted task state; it does not merely assert that the
assistant says “done.” Near-neighbor extraction tests cover embedded command-like words and an
apostrophe in titles. Existing deterministic workflow evals are rerun to detect collateral changes.

## Interview explanation

“I added a capture pipeline that paired real responses with independently read state. It exposed
a completion parser bug even though the existing offline suite was green. I reduced it to a specific
command, fixed the title extraction, and added a state-based regression test. This showed why
expanding the examples matters more than repeatedly quoting an existing 100% score.”

## Limits

This was a deterministic task workflow; no live judge or orchestrator humanizer was involved.
Captured labels remain unreviewed. The parser fix does not establish support for arbitrary quoted
commands, all languages, or all ambiguous titles. The LLM judge still needs independent calibration.
