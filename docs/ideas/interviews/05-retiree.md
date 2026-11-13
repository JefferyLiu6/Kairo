# Interview 05 — Retiree

**Date:** 2026-11-12 · **Method:** interview-me (one question at a time, guess attached)

> Simulated. The person and their answers are role-played by Claude, including
> the final "yes". Kairo's replies in the trial are real `run_pm` output from a
> fresh data directory, offline workflow only (no orchestrator model).

## Who

Margaret, 74, retired teacher. Uses a paper diary and a tablet her daughter set
up. Writes to software the way she writes letters.

## Opening ask

"My daughter says I should use one of these AI things for my appointments."

## Interview

```
HYPOTHESIS: You want reminders for appointments and medication so you don't have to rely on the paper diary.
CONFIDENCE: ~30% — missing: whether you want this at all, or your daughter does
```

**Q:** Is the paper diary letting you down, or is it working?
**GUESS:** It's working. This is your daughter's idea more than yours.
**Margaret:** It works perfectly well. I've kept one for fifty years.

*Updated read: there may be no problem to solve for her. Confidence ~30% —
missing: why the daughter is pushing.*

**Q:** What happened that made your daughter bring it up?
**GUESS:** You missed something and she worried.
**Margaret:** I didn't miss it. I went to the cardiologist on the wrong day.
I'd written it on the right page but I read the wrong week. She had to drive
me back two days later.

**Q:** So would it help if something told you, that morning, what today holds?
**GUESS:** Yes, as long as you don't have to go looking for it.
**Margaret:** If it told me. I'm not going to open it and poke about.

**Q:** When you tell it about an appointment, how will you know it understood?
**GUESS:** You'd want it to say the appointment back to you in full.
**Margaret:** Yes. Say it back properly, with the day of the week. If it just
says "done" I shan't trust it, and I'll write it in the diary anyway.

*Confidence ~85%.*

## Statement of intent

- **Outcome:** I tell it an appointment in an ordinary sentence, it repeats it back in full, and on the day it tells me without being asked.
- **User:** Someone who trusts paper and will only switch if the new thing is as legible.
- **Why now:** Turned up to a cardiology appointment on the wrong day.
- **Success:** A month of appointments with none missed and no checking the paper diary to be sure.
- **Constraint:** No special wording. Polite full sentences must work.
- **Out of scope:** To-do lists, habits, journals, anything she has to learn.

**Margaret:** Yes.

## Kairo trial (real output)

| # | Margaret typed | Kairo replied | Result |
|---|----------------|---------------|--------|
| 1 | Please remind me to take my blood pressure pill every morning at 8 | Not understood (generic fallback) | Lost |
| 2 | I have a doctor's appointment on November 23rd at 10:30 in the morning | Not understood | Lost |
| 3 | Did you save my doctor's appointment? | Not understood | No confirmation either way |
| 4 | Add my granddaughter's recital on December 5th at 6 o'clock in the evening | Added 'my granddaughter's recital on December 5th o'clock in the'. | Saved as a to-do with no date or time, title garbled |
| 5 | What do I have coming up? | Not understood | Lost |
| 6 | Please change the doctor's appointment to 11:30 | I don't see anything called 'Please change doctor' — what's the full name? | Follows from turn 2 |
| 7 | Yes please | There's no pending action to approve right now. | — |
| 8 | Thank you | Not understood | Even a thank-you gets the fallback |

## Gap between intent and product

- Nothing she said was saved correctly. Polite framing ("Please…", "I have…") falls outside what the workflow recognises.
- Her trust test, "say it back to me in full", is not met even when a write succeeds; confirmations are short.
- Nothing in the chat is proactive, so "tell me on the day" has no equivalent here.
