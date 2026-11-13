# Interview 10 — Co-parent

**Date:** 2026-11-12 · **Method:** interview-me (one question at a time, guess attached)

> Simulated. The person and their answers are role-played by Claude, including
> the final "yes". Kairo's replies in the trial are real `run_pm` output from a
> fresh data directory, offline workflow only (no orchestrator model).

## Who

Alex, two kids, shares pickups and activities with partner Jordan. Both work
full time. Already has a shared family calendar.

## Opening ask

"We need a better shared family calendar."

## Interview

```
HYPOTHESIS: You want one place where both of you can see and edit the kids' schedule.
CONFIDENCE: ~40% — missing: what's wrong with the shared calendar you already have
```

**Q:** You already share a calendar. What went wrong the last time it failed you?
**GUESS:** One of you forgot to add something to it.
**Alex:** Everything was in it. Jordan and I both saw "swim lesson, Saturday
9." We each assumed the other one was taking her. Nobody did.

*Updated read: the calendar says what and when, not who. Confidence ~65% —
missing: how responsibility gets decided.*

**Q:** How do the two of you decide who's doing a given pickup today?
**GUESS:** A text thread, the night before or that morning.
**Alex:** Texts. "Can you do Wednesday?" "ok." Then it's buried under forty
other messages.

**Q:** If each event simply showed a name next to it, would that solve it?
**GUESS:** Mostly, but you'd also need to know when it changes.
**Alex:** The name is eighty percent. The other twenty is: when I hand one
over, Jordan has to actually know. Not "it's in the calendar." Know.

**Q:** Does Jordan need to use the assistant too, or just get told?
**GUESS:** Just get told. Jordan won't adopt a new app.
**Alex:** Just get told. A text is fine. Jordan is not installing anything.

*Confidence ~90%.*

## Statement of intent

- **Outcome:** Every kid event has a named adult, and when I hand one to Jordan, Jordan is told directly.
- **User:** Two working parents splitting logistics, only one of whom will use the tool.
- **Why now:** A missed swim lesson where both assumed the other had it.
- **Success:** I can ask "who's got Wednesday pickup?" and get a name; no more both-assumed misses.
- **Constraint:** Jordan uses nothing new; a message is the only channel.
- **Out of scope:** A second user account, shared editing, kids' own calendars.

**Alex:** Yes.

## Kairo trial (real output)

| # | Alex typed | Kairo replied | Result |
|---|------------|---------------|--------|
| 1 | Add school pickup every weekday at 3:15pm | Done! Added 'school pickup' every weekday at 3:15 PM. | Right |
| 2 | Jordan is doing pickup on Wednesday instead of me | Not understood (generic fallback) | Core request lost |
| 3 | Add Mia's swim lesson Saturday at 9am | Done! Added 'Mia's swim lesson' Saturday at 9 AM. | Right |
| 4 | Who is picking up on Wednesday? | Not understood | Core question unanswered |
| 5 | Add todo: pack swim bag Friday night | Added 'todo: pack swim bag night' — due tomorrow. | Saved; "Friday" removed from the title |
| 6 | Share this week's schedule with Jordan | Offered three slots: tomorrow 9 AM (recommended)… | Treated as a new event to schedule |
| 7 | Skip school pickup this Wednesday | Offered three Wednesday slots | Swallowed; pickup not skipped |
| 8 | approve | Offered three more Wednesday slots | Swallowed |
| 9 | What's on my schedule this week? | Full listing; Wednesday pickup still present | Right, and confirms turn 7 did nothing |

## Gap between intent and product

- Events have no owner, so neither "Jordan is doing it" nor "who's got it" can be expressed.
- Kairo is single-user with no outbound channel, so telling Jordan is outside what it does today.
- A "share" request was interpreted as something to schedule, and the resulting pending question then absorbed a valid skip request.
