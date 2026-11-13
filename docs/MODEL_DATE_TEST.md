# Model date test

Step 2 of the [Kairo 2.0 plan](ideas/kairo-2.0-agent-architecture.md#11-migration).
Run on 2026-11-12.

## The question

Kairo 2.0 replaces regex extraction with a model that fills a typed tool call.
That only works if the model gets dates and times right, because a wrong date
that fits the schema is stored exactly as proposed and nothing downstream can
know the user meant another day. This test checks that one assumption in
isolation, before anything is built on it.

## Answer

**Yes, on the right model and with four things in the contract. Not on the
model the repo uses today.**

| Model, single-call arm | Right | Safe miss | Wrong | Decision |
|---|---|---|---|---|
| `gpt-4o-mini` (current `MODEL`) | 89.4% | 1.4% | 9.2% | no-go |
| `gpt-4.1-mini` | 91.1% | 1.4% | 7.4% | no-go |
| `gpt-5.4-nano` | 89.7% | 2.1% | 8.2% | no-go |
| `gpt-5.4-mini` | 97.5% | 2.5% | 0.0% | **go** |

282 answers per model: 94 messages, three times each. "Wrong" means the model
filled in something other than what was said, which would be a wrong save.
"Safe miss" means nothing wrong would be stored: it asked when it could have
answered, or made a call the contract rejects.

`gpt-5.4-mini` was run three times as the contract evolved and had 7, 4 and 0
wrong answers of 282. The first (2.5%) predates a check that would have removed
two of its seven. The second (1.4%) used a faulty version of that check, and
its report was discarded. Treat the model's real rate as about 1%, not 0%.

## Method

One message in, one tool call out, compared with the expected arguments. No
Kairo code path is involved and nothing is stored.

- **Cases:** 94 messages in `apps/api/tests/fixtures/date_fill_cases.json`,
  across ten kinds of phrasing: relative days, weekdays, absolute dates, times
  of day, durations, digits in titles, repeats, task deadlines, messages that
  need a question, and non-English. Each lists every acceptable answer. None is
  a held-out blind-eval message.
- **Clock:** Thursday 2026-11-12, 10:00 Toronto, stated in the prompt.
- **Tools:** `create_event`, `add_todo` and `ask_user`, with strict typed
  arguments: ISO dates, 24-hour times, weekday enums
  (`apps/api/evals/datefill/tools.py`). These are the candidate Kairo 2.0 tool
  shapes.
- **Arms:** `date_only` gives today's date. `calendar` adds the weekday and the
  next fourteen days spelt out. `calendar_single` is `calendar` with the model
  limited to one tool call per message.
- **Bars, fixed before the first run:** go needs wrong ≤ 2% and right ≥ 90%;
  wrong above 5% is no-go; in between needs mitigation. The 2% is the product's
  wrong-but-reported-done bar.

```bash
make eval-dates                       # the configured model, all arms, 3 repeats
make eval-dates model=gpt-5.4-mini
python -m evals.datefill --model gpt-5.4-mini --arm calendar_single --gate
```

## What each part of the contract is worth

Measured on `gpt-4o-mini`, where the effects are largest. Counts are wrong
answers of that kind out of 282 unless stated.

| Contract element | Without it | With it |
|---|---|---|
| **Calendar hint** (weekday plus the next 14 days in the prompt) | 66 wrong dates | 10 |
| **One call per message** | 28 answers with extra or duplicate calls (second run, 258 answers) | 0 |
| **Tool wording that says a task never repeats or holds time** | 20 repeats and time blocks filed as tasks (258 answers) | 0, including on cases written before the wording changed |
| **Reject a date in the past, and a one-off event earlier today** | "jan 14" stored as last January, 3 of 3 | rejected, 3 of 3 |
| **Reject a daily repeat that lists weekdays** | ambiguous repeat stored | rejected |

Examples of what the extra calls were: a second event the next day for an
overnight shift, an invented "Prepare for call" task alongside the call, and
five separate events for "every weekday".

## What no wording fixed on the smaller models

**Guessing a bare hour.** Five messages have an hour that could be morning or
evening with nothing to settle it ("meeting tomorrow at 7", "meet Chris monday
at 8"). The prompt says to ask. `gpt-4o-mini` and `gpt-5.4-nano` guessed on all
15 answers and `gpt-4.1-mini` on 10 of 15. `gpt-5.4-mini` asked on all 15 in
its final run.

**Residual date slips.** Even with the calendar, `gpt-4o-mini` got 11 dates
wrong: "fri" or "by friday" on a Thursday became next week's Friday, and "the
3rd" became a date in November. `gpt-5.4-mini` had none in its final run.

## Decision and what it changes

1. **Use `gpt-5.4-mini`, or a model that passes this test, for filling tool
   arguments.** `gpt-4o-mini` fails at 9% wrong. I do not know current prices
   for `gpt-5.4-mini`; a call here uses about 1,000 input and 50 output tokens,
   so check that against the $20 monthly cap before committing.
2. **Four contract requirements are now evidence-backed** and belong in the
   tool registry: the calendar hint in state, one proposal per request,
   explicit tool wording about what a task cannot be, and the past-date and
   repeat-shape checks in the gate.
3. **"One call per message" has a design consequence.** Several requests in one
   message cannot be left to the model issuing parallel calls. The coordinator
   must split the message first, or the tool must take a list (`add_todos`).
   This supports the architecture's split of coordinator and domain agent.
4. **A decision for Jeffery:** on a bare hour, should Kairo ask, or pick the
   likelier reading and show it in the receipt? Asking only works on
   `gpt-5.4-mini`. Picking-and-showing works on every model but stores a guess.

## How the test changed while running

Recorded so the result can be judged fairly.

- **First run** (76 cases, original wording): `gpt-4o-mini` scored 31.6%,
  13.2% and 5.3% wrong across the three arms. The single-call arm was added
  after seeing that most errors in the first two arms were extra calls.
- **One expectation was corrected.** "schedule a call with the landlord at 6"
  expected only a question. At 10:00, today's 6 am has passed, so 18:00 today
  follows from the prompt's own rule. It now accepts that answer or a question.
- **One acceptable answer was added.** For "gym mon wed fri 7am", three weekly
  series, one per day, is equivalent to one series on three days.
- **Ten cases (`fr*`) were added before the tool wording changed**, and eight
  more (`v*`) before the revised wording was run, so the wording change could
  be checked on messages it was not written against. The repeat and time-block
  fixes held on all of them. The bare-hour rule did not, except on
  `gpt-5.4-mini`.
- **A check I added was wrong at first.** "Start has already passed today"
  rejected repeating events that begin today. It now applies to one-off events
  only. The four reports produced with the faulty check were deleted.

## Limits

- The messages were written by Claude, not collected from users.
- 282 answers per model cannot separate 0% from about 1%. The `gpt-5.4-mini`
  runs ranged from 0 to 7 wrong.
- Whether "meeting at 7" must be asked about is a policy choice built into the
  expectations. Under a pick-and-show policy the three smaller models would
  score 3–4% wrong, not 7–9%, which is still above the bar.
- Only OpenAI models reachable with the configured key were tested.
- This tests one call in isolation. Looking up an existing event and then
  proposing a change to it is not covered here; the blind eval covers it once
  the schedule agent exists.

Reports (local, git-ignored): `artifacts/datefill/`. Final runs:
`gpt-4o-mini-20261112T234039Z`, `gpt-4.1-mini-20261112T234108Z`,
`gpt-5.4-nano-20261112T234142Z`, `gpt-5.4-mini-20261112T234220Z`.
