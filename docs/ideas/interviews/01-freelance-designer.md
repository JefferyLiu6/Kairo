# Interview 01 — Freelance designer

**Date:** 2026-11-12 · **Method:** interview-me (one question at a time, guess attached)

> Simulated. The person and their answers are role-played by Claude, including
> the final "yes". Kairo's replies in the trial are real `run_pm` output from a
> fresh data directory, offline workflow only (no orchestrator model).

## Who

Maya, solo brand designer with four or five clients at a time. Lives in her
calendar. Types fast, lowercase, shorthand.

## Opening ask

"I want an AI calendar assistant so I stop double-booking clients."

## Interview

```
HYPOTHESIS: You want something that catches scheduling conflicts before you commit to a client.
CONFIDENCE: ~35% — missing: whether double-booking is the real pain, and what "assistant" does that a calendar doesn't
```

**Q:** When did you last double-book, and what did it cost you?
**GUESS:** Within the last month, and it cost you an awkward apology email.
**Maya:** Honestly I've double-booked maybe twice this year. What happens every
week is that I say yes to a call and it lands in the middle of the only free
afternoon I had for actual design work.

*Updated read: the problem is not collisions between meetings. It is meetings
eating unscheduled work time. Confidence ~55% — missing: how she decides what to
protect.*

**Q:** Is your design time on the calendar at all today?
**GUESS:** No. It's whatever is left over, which is why it gets eaten.
**Maya:** Right. I tried blocking it but I move the blocks the moment a client
asks, so they mean nothing.

**Q:** So when a client asks "can you do Thursday at 2", what do you want to
know before you answer?
**GUESS:** Whether saying yes leaves you enough hours that week to hit your
deadlines.
**Maya:** Yes. Exactly that. "If I take this, do I still have time for the Acme
logo before Monday." I do that maths in my head and get it wrong.

*Confidence ~85%.*

**Q:** Do you want it to refuse on your behalf, or just tell you?
**GUESS:** Just tell you. You want to make the call yourself.
**Maya:** Tell me. I'll still say yes sometimes. I just want to know what I'm
giving up.

## Statement of intent

- **Outcome:** Before I accept a meeting, I can see whether I still have enough working hours left to hit my deadlines.
- **User:** A solo freelancer whose real work is not on the calendar.
- **Why now:** Missed a deadline last month after a week of "quick calls".
- **Success:** I ask "can I fit this in?" and get a yes or no with the hours left for each deadline.
- **Constraint:** It has to take ten seconds; I'm answering a client email at the time.
- **Out of scope:** Auto-declining, emailing clients, invoicing, time tracking.

**Maya:** Yes.

## Kairo trial (real output)

| # | Maya typed | Kairo replied | Result |
|---|------------|---------------|--------|
| 1 | client call with Northwind thurs 2-3pm | Not understood (generic fallback) | Lost |
| 2 | block friday morning for the Acme logo work | Done! Added 'block' tomorrow at 9 AM. | Saved, but the title is just "block" |
| 3 | add a deadline: Acme logo v2 due monday 5pm | Done! Added 'deadline: Acme logo v2 due' Monday at 2 AM. | Wrong: 2 AM instead of 5 PM |
| 4 | can i fit a 90 min workshop on thursday afternoon? | Not understood | Her core question, unanswered |
| 5 | schedule discovery call with new client next wednesday at 11am | Done! Added 'discovery call with new client' Wednesday at 11 AM. | Right |
| 6 | what does thursday look like | Not understood | Lost |
| 7 | move the discovery call to 4pm | Approval required [049ebe31]: Update schedule event | Right |
| 8 | approve | Done! Moved it to 4 PM. | Right |
| 9 | how many hours of client meetings do i have this week | Not understood | Lost |

## Gap between intent and product

- Kairo has no concept of a deadline or of work that needs hours; the deadline became a calendar event at 2 AM (it appears to have read the "2" in "v2").
- The question she actually wants answered (turns 4 and 9) is not something the workflow recognises.
- What she'd keep: the move-with-approval flow.
