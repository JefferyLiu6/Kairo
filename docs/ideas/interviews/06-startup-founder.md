# Interview 06 — Startup founder

**Date:** 2026-11-12 · **Method:** interview-me (one question at a time, guess attached)

> Simulated. The person and their answers are role-played by Claude, including
> the final "yes". Kairo's replies in the trial are real `run_pm` output from a
> fresh data directory, offline workflow only (no orchestrator model).

## Who

Arjun, CEO of a twelve-person startup. Has an EA two days a week. Reads
productivity blogs.

## Opening ask

"I want an AI chief of staff."

## Interview

```
HYPOTHESIS: You want something that manages your calendar and task list the way an EA would on the days yours isn't in.
CONFIDENCE: ~25% — missing: what "chief of staff" means to you in one concrete week
```

**Q:** On the three days your EA isn't there, what specifically falls apart?
**GUESS:** Meeting logistics: rescheduling, finding slots.
**Arjun:** Not logistics. Those wait. What falls apart is that I lose the
thread. By Thursday I can't tell you what I committed to on Monday.

**Q:** What would a chief of staff do about that?
**GUESS:** Hold a running list of your commitments.
**Arjun:** Drive strategic alignment across my priorities and give me leverage.

**Q:** If you didn't have to make it sound like a job description, what would
you actually want on a Friday afternoon?
**GUESS:** A plain list of what you said you'd do this week and didn't.
**Arjun:** …Yes. That. "Here's what slipped." And honestly, the two things I
keep pushing week after week, because those are the ones I'm avoiding.

*Updated read: a weekly "what slipped" review, with repeat offenders called out.
Confidence ~80% — missing: where the commitments come from.*

**Q:** Do the commitments already live somewhere, or only in your head?
**GUESS:** Scattered: some in your task app, most in your head.
**Arjun:** Head and Slack. I'd type them in if it took one line each. I won't
maintain a system.

*Confidence ~90%.*

## Statement of intent

- **Outcome:** Every Friday I'm shown what I committed to and didn't finish, with the items I've pushed more than once called out.
- **User:** A founder who over-commits and loses track mid-week.
- **Why now:** A board member pointed out the same action item had been open for three meetings.
- **Success:** The Friday list is short enough to read in a minute and I act on at least one avoided item each week.
- **Constraint:** One line to capture a commitment; zero upkeep.
- **Out of scope:** Scheduling, email triage, delegating to the team, OKR tooling.

**Arjun:** Yes.

## Kairo trial (real output)

| # | Arjun typed | Kairo replied | Result |
|---|-------------|---------------|--------|
| 1 | Add todo: send investor update by Friday | Added 'todo: send investor update' — due tomorrow. | Right |
| 2 | Add todo: review Q4 hiring plan | Added 'todo: review Q4 hiring plan'. | Right |
| 3 | Schedule a board prep session Tuesday at 3pm | Done! Added 'board prep session' Tuesday at 3 PM. | Right |
| 4 | Add a weekly 1:1 with Sara every Wednesday at 10am | Done! Added 'weekly 1:1 with Sara every' every Wed at 1 AM. | Wrong: 1 AM instead of 10 AM |
| 5 | What did I not finish this week? | Not understood (generic fallback) | Core question unanswered |
| 6 | What's my week look like? | Not understood | Lost |
| 7 | Cancel the board prep session | Not understood | Lost |
| 8 | approve | There's no pending action to approve right now. | Follows from 7 |
| 9 | Plan my day tomorrow | Not understood | Lost |

## Gap between intent and product

- Capture works for his phrasing. Review does not exist: there is no "what slipped" and no record of how often a task has been pushed.
- "1:1" in a title was read as a clock time, producing a recurring 1 AM meeting reported as done.
