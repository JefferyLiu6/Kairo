# Kairo 2.0 — Ten Simulated User Interviews

**Date:** 2026-11-12 · **Recorded by:** Claude

Ten separate interviews, one file each. Each file stands alone and does not
refer to the others. This index is the only place they are put side by side.

## What these are, and what they are not

- **Simulated.** Every person, answer and final "yes" is role-played by Claude.
  The interview-me method is meant for a live person; a role-played "yes" is a
  hypothesis about what such a user wants, not a confirmed intent.
- **Kairo's replies are real.** Each persona typed 8–9 messages into `run_pm`
  with a fresh temporary data directory and vault. The tables quote the actual
  output.
- **Offline workflow only.** No model key or local Ollama was available, so the
  orchestrator that rewrites phrasing in the live app did not run. "Not
  understood" results may not reproduce live; wrong saves and swallowed
  messages are more likely to.
- **Independence is limited.** One author wrote all ten in one sitting. They
  share no data or session state, but they are not ten independent minds.

## Index

| # | Person | They asked for | What they turned out to want | Kairo trial: turns that did what was meant |
|---|--------|----------------|------------------------------|--------------------------------------------|
| [01](01-freelance-designer.md) | Freelance designer | A calendar assistant to stop double-booking | To know, before saying yes, whether she still has hours left for her deadlines | 3 of 9 |
| [02](02-grad-student-adhd.md) | Grad student with ADHD | A better to-do app | Dump everything in, be told the one next thing, never see the pile | 3 of 8 |
| [03](03-sales-manager.md) | Sales manager | AI to handle scheduling | Promises made on calls become dated follow-ups, viewable by person | 5 of 8 |
| [04](04-night-shift-nurse.md) | Night-shift nurse | A calendar that understands shifts | Nothing ever booked in the sleep window after a night shift | 3 of 8 |
| [05](05-retiree.md) | Retiree | "My daughter says I should use AI" | It repeats each appointment back in full and tells her on the day | 0 of 8 |
| [06](06-startup-founder.md) | Startup founder | An AI chief of staff | A Friday list of what slipped, with repeat offenders called out | 3 of 9 |
| [07](07-bilingual-user.md) | Bilingual user | Chinese support | Mixed Chinese–English input understood, titles kept exactly as typed | 4 of 8 |
| [08](08-privacy-minded-engineer.md) | Privacy-minded engineer | Local-first, private, full audit trail | Asked before sensitive writes; "show me everything you hold" works | 4 of 8 |
| [09](09-fitness-habit-builder.md) | Fitness habit builder | A habit tracker with streaks | To be made to decide when she is about to miss a run | 1 of 9 |
| [10](10-co-parent.md) | Co-parent | A better shared family calendar | Every kid event has a named adult, and the partner is told on handover | 3 of 9 |

Trial total: 29 of 85 turns.

## Reading across the ten

In all ten, the opening ask named a familiar product category and the stated
intent was something narrower. Since the answers are role-played, treat that as
a prompt for real interviews, not a finding.

Three things recur in the stated intents:

- **Answering a question, not storing an item.** "Can I fit this in?", "what
  next?", "what do I owe?", "what slipped?", "who's got Wednesday?" (01, 02,
  03, 06, 10). Kairo captured the inputs in most of these and could not answer
  the question in any.
- **Being told, not having to look.** 05, 09 and 10 want the assistant to speak
  first. Kairo's chat only responds.
- **Trust through read-back.** 05 and 08 will only rely on it if it shows
  exactly what it stored.

What the real trial output shows, independent of the role-play:

| Observed | Where |
|----------|-------|
| Events saved at 2 AM and 1 AM and reported as done; the likely cause is digits in the title ("v2", "1:1") being read as a time, not confirmed in code | 01, 06 |
| Once slot options are offered, later unrelated messages get more slot options | 04, 09, 10 |
| Explicit days and times ignored in favour of suggested slots | 04, 09 |
| Habits could not be created with natural phrasing; the working form only appears in a hint after listing | 09 |
| Polite or declarative phrasing ("Please…", "I have…") not recognised | 05 |
| Stated durations ignored (30 minutes saved as 1h) | 03 |
| Sentence fragments left in or cut from titles | 01, 02, 03, 05, 10 |
| Sensitive-write approval, rejection and injection refusal all worked | 08 |
| Non-English titles stored unchanged | 07 |

## Next steps this supports

1. Re-run the 85 messages through `/orchestrator/stream` with a model key to
   see which failures the live app already handles.
2. Replace at least three of these personas with real people. The intents above
   are guesses about users; only real interviews can confirm them.
3. Decide who 2.0 is for. Several intents here (handover to a partner, proactive
   nudges, weekly review) are new products, not fixes.
