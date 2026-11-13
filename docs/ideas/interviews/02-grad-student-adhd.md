# Interview 02 — Grad student with ADHD

**Date:** 2026-11-12 · **Method:** interview-me (one question at a time, guess attached)

> Simulated. The person and their answers are role-played by Claude, including
> the final "yes". Kairo's replies in the trial are real `run_pm` output from a
> fresh data directory, offline workflow only (no orchestrator model).

## Who

Dev, fourth-year PhD student. Has tried and abandoned six to-do apps. Dumps
thoughts in bursts.

## Opening ask

"I need a better to-do app. One that I'll actually keep using."

## Interview

```
HYPOTHESIS: You want a to-do list with less friction to add things, so you don't abandon it.
CONFIDENCE: ~30% — missing: why the last six were abandoned, and whether adding or doing is the hard part
```

**Q:** With the last app you quit, what was happening the week you stopped
opening it?
**GUESS:** Adding tasks took too many taps, so you stopped capturing.
**Dev:** No, capturing was fine. The list hit about sixty items and opening it
made me feel sick. So I stopped opening it.

*Updated read: the failure is on the reading side, not the writing side.
Confidence ~55% — missing: what he wants to see instead of the list.*

**Q:** If the app could show you only one screen when you open it, what's on it?
**GUESS:** Today's three most important tasks.
**Dev:** One. Not three. If I see three I spend twenty minutes deciding which.

**Q:** Who picks the one — you the night before, or the app?
**GUESS:** The app, because choosing is the part that drains you.
**Dev:** The app. I know that sounds lazy. I should probably do a proper
weekly review and prioritise myself.

**Q:** If you didn't have to justify it to anyone, what would you actually want?
**GUESS:** To never see the full list unless you ask for it.
**Dev:** Yeah. I want to throw things in and never look at the pile. Just tell
me the next thing, and when I finish, the next one.

*Confidence ~90%.*

## Statement of intent

- **Outcome:** I dump tasks in whenever they occur to me, and when I ask, I'm told the single next thing to do.
- **User:** Someone who captures well and freezes when faced with a long list.
- **Why now:** Thesis deadline in five months and tasks are living in four places.
- **Success:** I ask "what now?" several times a day and act on the answer without opening the full list.
- **Constraint:** A brain dump is one message with several things in it; it can't make me split them up.
- **Out of scope:** Projects, tags, priorities I set by hand, calendar views.

**Dev:** Yes.

## Kairo trial (real output)

| # | Dev typed | Kairo replied | Result |
|---|-----------|---------------|--------|
| 1 | ok brain dump: email advisor, renew library books, fix figure 3, buy cat food | Not understood (generic fallback) | All four lost |
| 2 | add todo email advisor about committee date | Added 'email advisor about committee date'. | Right |
| 3 | what should i do first | Not understood | Core question unanswered |
| 4 | add todo fix figure 3 due friday | Added 'fix figure 3 due' — due tomorrow. | Right date, "due" left in title |
| 5 | remind me at 4pm to leave for the dentist | Added 'remind me to leave for the dentist'. | Wrong: 4 PM dropped, no reminder time |
| 6 | what's the one thing i need to do today | Not understood | Core question unanswered |
| 7 | done email advisor | Not understood | Lost |
| 8 | show my todos | Three open todos listed | Right |

## Gap between intent and product

- The two behaviours he wants, multi-item capture (turn 1) and "what next" (turns 3, 6), both hit the fallback.
- A timed reminder became an untimed todo, reported as added.
- What he'd keep: single-item capture with a due date works.
