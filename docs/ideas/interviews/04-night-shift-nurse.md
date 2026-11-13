# Interview 04 — Night-shift nurse

**Date:** 2026-11-12 · **Method:** interview-me (one question at a time, guess attached)

> Simulated. The person and their answers are role-played by Claude, including
> the final "yes". Kairo's replies in the trial are real `run_pm` output from a
> fresh data directory, offline workflow only (no orchestrator model).

## Who

Tomás, ICU nurse on a rotating roster. Shifts are 19:00–07:30 and change every
week. Thinks in 24-hour time.

## Opening ask

"I need a calendar that understands shift work."

## Interview

```
HYPOTHESIS: You want to enter rotating shifts quickly, because normal calendars assume a weekly repeat.
CONFIDENCE: ~45% — missing: whether entry is the pain or what happens around the shifts
```

**Q:** How do you get your roster into a calendar today?
**GUESS:** By hand, one shift at a time, and it's tedious.
**Tomás:** I don't. The roster is a photo on my phone. I just look at the photo.

*Updated read: entering shifts may not be worth it to him at all. Confidence
~35% — missing: what goes wrong because the shifts aren't in a calendar.*

**Q:** What went wrong most recently because the roster was only a photo?
**GUESS:** You booked something on a day you were working.
**Tomás:** Worse. I booked a dentist at 9 am the morning after a night shift.
I wasn't working, technically. I was just wrecked. I slept through it.

**Q:** So the thing to protect is the recovery time, not the shift itself?
**GUESS:** Yes, the morning after each night shift, until early afternoon.
**Tomás:** Until 2 pm. And the day I flip back to days, I'm useless before noon.

*Confidence ~80%.*

**Q:** Would you type in each week's shifts if that's what it took?
**GUESS:** No. You'd want to say them in one line.
**Tomás:** One line, like "nights fri sat sun". If it's more than that I'll
keep using the photo.

## Statement of intent

- **Outcome:** I give my shifts in one line and nothing ever gets booked in my sleep window after a night.
- **User:** A rotating shift worker whose free time is not all usable time.
- **Why now:** Missed a dentist appointment and paid the no-show fee.
- **Success:** When I ask for an appointment slot, it never offers a morning after a night shift.
- **Constraint:** Shift entry is one short line per week, in 24-hour time.
- **Out of scope:** Shift swaps with colleagues, payroll, importing the hospital roster.

**Tomás:** Yes.

## Kairo trial (real output)

| # | Tomás typed | Kairo replied | Result |
|---|-------------|---------------|--------|
| 1 | night shift 1900-0730 fri sat sun | Offered three slots: tomorrow 9 AM (recommended), tomorrow 2 PM, Sunday 9 AM | Not saved; times and days ignored |
| 2 | add shift tomorrow 7pm to 7:30am | I don't see anything called 'add shift' — what's the full name? | Not understood |
| 3 | remember that i sleep until 2pm after night shifts | Remembered: i sleep until 2pm after night shifts | Right |
| 4 | schedule a dentist appointment on saturday | Offered Saturday 10 AM (recommended), 2 PM, 3 PM | Reasonable to ask, but recommends 10 AM despite turn 3 |
| 5 | what shifts do i have this week | Offered three more Saturday slots | Swallowed by the pending question |
| 6 | swap my sunday shift to monday | Offered three more Saturday slots | Swallowed |
| 7 | add todo renew nursing license by dec 15 | Added 'renew nursing license by dec 15'. | Saved, but the date stayed in the title with no due date |
| 8 | show my todos | One todo listed | Right |

## Gap between intent and product

- No shift was saved, so there is nothing for the stored sleep preference to apply to.
- The stored preference did not affect the Saturday recommendation; with no shift on record this can't be judged fairly.
- Overnight spans and 24-hour times are not recognised.
- Once slot options were on screen, two unrelated questions were answered with more slot options.
