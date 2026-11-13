# Interview 09 — Fitness habit builder

**Date:** 2026-11-12 · **Method:** interview-me (one question at a time, guess attached)

> Simulated. The person and their answers are role-played by Claude, including
> the final "yes". Kairo's replies in the trial are real `run_pm` output from a
> fresh data directory, offline workflow only (no orchestrator model).

## Who

Keisha, accountant, training for her first 10K. Has a running watch that
already records every run.

## Opening ask

"I want a habit tracker with streaks."

## Interview

```
HYPOTHESIS: You want to log runs and see a streak count to stay motivated.
CONFIDENCE: ~45% — missing: why the watch's own streaks aren't enough
```

**Q:** Your watch already tracks runs and streaks. What is it not giving you?
**GUESS:** It only covers running, and you want other habits in the same place.
**Keisha:** No, running is the only one I care about. The watch shows me a
streak. It just doesn't care when I break it.

*Updated read: the missing piece is accountability, not record-keeping.
Confidence ~60% — missing: what accountability looks like to her.*

**Q:** Think of the last time you skipped a run. What would have got you out
the door?
**GUESS:** A reminder at the right moment.
**Keisha:** I had a reminder. I swiped it away. What would have worked is
something asking me at 7 pm, "you haven't run, are you going or are we moving
it to tomorrow?" Make me decide. Don't let me drift.

**Q:** So a skipped day should become a decision, not a silent miss?
**GUESS:** Yes, and a deliberate reschedule shouldn't break the streak.
**Keisha:** Exactly. Three runs a week. I don't care which days. If I move
Wednesday to Thursday that's fine. If I just don't, I want it on the record.

*Confidence ~85%.*

**Q:** Do you want to log the run yourself, or should it already know?
**GUESS:** Log it yourself in a few words; you won't connect the watch.
**Keisha:** A few words. "ran today." That's the whole interaction.

## Statement of intent

- **Outcome:** I commit to three runs a week, log each in a few words, and get asked to decide whenever I'm about to miss one.
- **User:** Someone who already has tracking and lacks accountability.
- **Why now:** Race is in ten weeks and she skipped four runs last month.
- **Success:** Ten weeks with every missed run either rescheduled on purpose or recorded as skipped.
- **Constraint:** Weekly target, not fixed days; logging is one short message.
- **Out of scope:** Pace and distance stats, watch integration, other habits.

**Keisha:** Yes.

## Kairo trial (real output)

| # | Keisha typed | Kairo replied | Result |
|---|--------------|---------------|--------|
| 1 | Add a habit called morning run | I'll need habit name to do that. | Not understood; the name was given |
| 2 | Track a habit: stretch | I'll need habit name to do that. | Same |
| 3 | I did my morning run | I'll need habit name to do that. | Swallowed |
| 4 | Log morning run for today | Saved to your journal. | Wrong: stored as a journal entry |
| 5 | How's my morning run streak? | Error: no habit matched 'how run streak?' | Raw error text |
| 6 | Schedule gym every Monday, Wednesday and Friday at 6am | Offered Monday 7 AM (recommended), 6 PM, 7 PM | Not saved; days and time ignored |
| 7 | Skip gym this Friday | Offered three more Monday slots | Swallowed |
| 8 | approve | Offered three more Monday slots | Swallowed |
| 9 | list my habits | No habits tracked yet… Say `add habit to drink water` to start one. | Right, and reveals the wording it expects |

## Gap between intent and product

- No habit could be created with three natural phrasings. The hint in turn 9 suggests the expected form is "add habit to …", which she had no way to know until the end.
- A habit log went to the journal and was reported as saved.
- Nothing here asks her anything unprompted, so the accountability she wants has no counterpart.
- "Three times a week, any days" was not tested and would need a look at the habit model.
