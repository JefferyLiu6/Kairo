# Interview 03 — Sales manager

**Date:** 2026-11-12 · **Method:** interview-me (one question at a time, guess attached)

> Simulated. The person and their answers are role-played by Claude, including
> the final "yes". Kairo's replies in the trial are real `run_pm` output from a
> fresh data directory, offline workflow only (no orchestrator model).

## Who

Renee, regional sales manager, six to eight calls a day. Writes complete,
businesslike sentences.

## Opening ask

"I want AI to handle my scheduling so I get time back."

## Interview

```
HYPOTHESIS: You want meetings booked and moved by typing a sentence instead of clicking through a calendar.
CONFIDENCE: ~40% — missing: where the time actually goes, and what "handle" covers
```

**Q:** Roughly how many minutes a day do you spend creating or moving calendar
events yourself?
**GUESS:** Twenty to thirty.
**Renee:** Five, maybe. My customers book through a link. Scheduling isn't
really where I lose time, now that you make me count it.

*Updated read: "scheduling" was the conventional ask. Confidence ~35% — missing:
the real time sink.*

**Q:** After a call ends, what do you do in the next ten minutes?
**GUESS:** Write down what you promised, then forget half of it by the next call.
**Renee:** I don't even write it down. I go straight into the next call. At
6 pm I try to remember who I owe what.

**Q:** What would you want a modern assistant to do about that?
**GUESS:** Keep a list of promises per customer.
**Renee:** Something that leverages AI to drive pipeline efficiency.

**Q:** If you didn't have to justify it to your VP, what would you actually want?
**GUESS:** To say one sentence right after a call and have the follow-up exist
with a date on it.
**Renee:** That. Thirty seconds after I hang up: "send Contoso pricing by
tomorrow." And at the end of the day, show me everything I owe, by person.

*Confidence ~90%.*

## Statement of intent

- **Outcome:** Every promise I make on a call becomes a dated follow-up in under thirty seconds, and I can see what I owe each person.
- **User:** Someone with back-to-back calls and no gap to take notes.
- **Why now:** Lost a renewal last quarter because a promised quote never went out.
- **Success:** At 6 pm I ask "what do I owe people?" and the list is complete.
- **Constraint:** Capture must be one sentence; no forms, no CRM fields.
- **Out of scope:** Booking meetings, writing the emails, CRM sync.

**Renee:** Yes.

## Kairo trial (real output)

| # | Renee typed | Kairo replied | Result |
|---|-------------|---------------|--------|
| 1 | Schedule a pipeline review with the team every Monday at 9am | Done! Added 'pipeline review with the team every' every Mon at 9 AM. | Right (stray "every" in title) |
| 2 | Add a call with Dana at Contoso tomorrow at 2pm for 30 minutes | Done! Added 'call with Dana at Contoso' tomorrow at 2 PM. | Saved as 1h, not 30 minutes |
| 3 | Add todo: send Contoso the pricing deck after tomorrow's call | Added 'todo: send Contoso the pricing deck after 's call' — due tomorrow. | Saved, title mangled |
| 4 | What's on my schedule tomorrow? | Listed tomorrow plus the next two Mondays | Right, with extra days |
| 5 | Push the Contoso call to 3pm | Approval required [48ab62cc]: Update schedule event | Right |
| 6 | approve | Done! Moved it to 3 PM. | Right |
| 7 | Add todo: follow up with Liam about renewal by Friday | Added 'todo: follow up with Liam about renewal' — due tomorrow. | Right |
| 8 | What follow-ups do I owe people? | Not understood (generic fallback) | Core question unanswered |

## Gap between intent and product

- Capture mostly works for her phrasing, which is the best fit of any trial.
- Todos have no link to a person, so "what do I owe, by person" has nothing to answer from.
- Stated durations are ignored and titles pick up fragments of the sentence.
