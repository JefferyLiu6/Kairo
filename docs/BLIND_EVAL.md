# Blind eval

Un-coached messages, scored by what ends up stored.

The offline suite (`make test-evals`, 252 cases) checks phrasing the system was
built around and passes at 100%. The blind eval asks a different question: when
someone types the way people text, is the right thing in storage afterwards? It
is the measure every Kairo 2.0 step is judged against
([plan](ideas/kairo-2.0-agent-architecture.md#11-migration)).

## Run it

```bash
make eval-blind                      # offline typed workflow, no model, about a second
make eval-blind path=orchestrator    # the web-chat path; calls the configured model
```

or, from `apps/api`:

```bash
python -m evals.blind --path pm
python -m evals.blind --path orchestrator --usd-in 0.15 --usd-out 0.60   # also report cost
python -m evals.blind --path pm --scenario 01_freelancer --no-save
python -m evals.blind --path pm --gate                                    # exit 1 if the bar is missed
```

A run prints a summary and writes the full report (every turn, reply, outcome
and reason) to `artifacts/blind/<path>-<time>.json`. Reports are never
overwritten. `artifacts/` is git-ignored.

The orchestrator path reads model credentials from `apps/api/.env` the same way
the API does. Eval turns are never sent to LangSmith.

## What is in the set

`apps/api/tests/fixtures/blind_eval_set.json`: ten personas, 85 turns.

| Scenario | Split | Habit |
|---|---|---|
| `01_freelancer` | dev | Fast lowercase shorthand, deadlines |
| `02_adhd_student` | dev | Brain dumps, "what next?" |
| `03_sales_manager` | dev | Full sentences, follow-ups |
| `04_night_nurse` | holdout | 24-hour time, overnight shifts |
| `05_retiree` | dev | Polite full sentences |
| `06_founder` | dev | Commitments and weekly review |
| `07_bilingual` | holdout | Mandarin and English mixed |
| `08_privacy_engineer` | dev | Sensitive data, approvals, injection |
| `09_fitness` | dev | Habits and recurring events |
| `10_coparent` | holdout | Recurring family logistics |

Three personas are **held out**. Tune prompts and tools against the dev split
only, and read the holdout number to see whether an improvement generalises.
The report gives both.

The clock is pinned to **Thursday 2026-11-12, 10:00 Toronto**, the morning the
messages were written. "Tomorrow" is always the 13th, so expectations are plain
dates and a run next month scores the same as a run today.

The messages were written by Claude role-playing users, not collected from real
people. They show where to look, not how often something happens in the wild.
Replacing some scenarios with real transcripts is the most valuable improvement
to this set.

## How a turn is judged

Before and after each message the runner snapshots the user's stores: schedule,
todos, habits, journal, memory, pending approvals and the open question. The
outcome comes from the difference, in this order:

| Outcome | Meaning |
|---|---|
| `right` | What is stored, shown or queued for approval is what was asked |
| `wrong_save` | Something was stored that should not have been, or stored wrongly |
| `trapped` | The same pending question was asked again instead of handling the message |
| `false_claim` | Nothing was stored but the reply says it was, or it describes something not stored |
| `blocked` | An earlier turn failed, so this one could not be attempted fairly |
| `unscored` | Open-ended advice or conversation; only the guardrails above apply |
| `miss` | Nothing wrong was stored or claimed, but the request was not done |

Rules worth knowing:

- **Schedule, todos, habits and journal are strict.** Any change the turn did
  not expect is a wrong save, including a correct item plus an extra one.
- **Memory is soft.** An expected memory must be there, but facts learned along
  the way are not failures unless the scenario forbids them with `never`.
- **A risky change is right only if it waits for approval.** Doing it directly
  is a wrong save.
- **A parked question is fine.** A turn is trapped only when the same question
  is asked again, not when it is left open while the new request is handled.
- **Reply text is read for three things only:** whether it claims success,
  whether it describes something that is not stored, and whether a read names
  the stored items. The rules are in `evals/blind/replies.py`, pinned to real
  replies in `tests/evals/test_blind_replies.py`.

## The bar

| Metric | Definition | Bar |
|---|---|---|
| Right | `right` ÷ scoreable turns (all turns minus `blocked` and `unscored`) | at least 70% |
| Wrong but reported done | (`wrong_save` + `false_claim`) ÷ all turns | at most 2% |
| Trapped turns | count | 0 |

The same run reports median and 95th-percentile reply time, model calls per
turn, tokens, and cost when prices are given. `wrong_save` counts every bad
write, including the few where the reply hedges ("I couldn't confirm that was
saved"); the stored data is wrong either way.

## Baselines (2026-11-12)

One run each, before any Kairo 2.0 change. The orchestrator ran on
`gpt-4o-mini`, as set in `apps/api/.env`; cost uses $0.15 and $0.60 per million
input and output tokens, which should be checked against current pricing.

| | Bar | Offline workflow (`pm`) | Web-chat path (`orchestrator`) |
|---|---|---|---|
| Right, of scoreable turns | ≥ 70% | 47% (30 of 64) | 34% (22 of 65) |
| Wrong but reported done | ≤ 2% | 9% (8 wrong saves) | 18% (13 wrong saves, 2 false claims) |
| Trapped turns | 0 | 8 | 7 |
| Missed | | 18 | 21 |
| Blocked / open-ended | | 11 / 10 | 12 / 8 |
| Dev / holdout right | | 44% / 52% | 34% / 33% |
| Reply time, median / 95th | 2 s / 6 s | 0.006 s / 0.02 s | 4.9 s / 9.3 s |
| Model calls per turn | | 0 | 2.5 |
| Tokens per turn, in / out | | 0 | about 3,130 / 180 |
| Cost for the 85 turns | | $0 | $0.049 |

What the orchestrator run shows:

- **The model layer made results worse, not better.** It understood more
  phrasings but stored less correctly than the workflow it wraps.
- **"I couldn't confirm that was saved" was the reply on 32 of 85 turns.** On a
  plain "Add todo: review Q4 hiring plan" the workflow answered
  "Added 'review Q4 hiring plan'." and the judge model rejected it as "lacks
  verification". The write had succeeded. This reproduces with the real clock,
  so it is not an artefact of the pinned one.
- **Due dates are lost in translation.** Seven of the thirteen wrong saves are
  a task stored with no due date after "by Friday", "due monday 5pm" or
  "by dec 15"; an eighth stored the wrong date.
- **A pending question still captures later messages**, and in one case wrote
  an event in response to "what's the one thing i need to do today".
- **"Remember …" did not store anything** in either memory turn. The web path
  builds its config without a vault directory (`kairo/api/app.py`,
  `orchestrator_stream`), and the memory executor refuses without one.
- **Cost is small.** About $0.0006 per turn, so a 15-turn visitor costs under
  one cent on this model.

Reports: `artifacts/blind/pm-20261112T230237Z.json` and
`artifacts/blind/orchestrator-20261112T230239Z.json` (local, git-ignored).

### After step 3: receipts on the web-chat path

Writes are now read back from storage and the reply is built from the stored
records, with no judge and no humanizer
([ARCHITECTURE.md](../ARCHITECTURE.md#receipts)). One run, same model.

| Web-chat path | Baseline | With receipts |
|---|---|---|
| Right, of scoreable turns | 34% (22 of 65) | 39% (25 of 64) |
| Wrong but reported done | 18% (13 + 2) | 16% (12 + 2) |
| Trapped turns | 7 | 3 |
| Replies saying "I couldn't confirm that was saved" | 32 | 1 |
| Model calls per turn | 2.5 | 2.0 |
| Reply time, median / 95th | 4.9 s / 9.3 s | 3.2 s / 6.3 s |

What changed and what did not:

- **The false "couldn't confirm" replies are gone**, and replies are faster.
- **Replies now show what is stored**, so a wrong save is visible instead of
  hidden: "Added task: Acme logo v2 — no due date." after "due monday 5pm", and
  "Added task: fix figure 3 — due Tue 17 Nov." after "due friday".
- **Wrong saves themselves are unchanged** (13 before, 12 after). They come
  from the translate-then-parse step, which receipts do not touch. All three
  bars still fail.
- **The right rate and trapped count moved within run-to-run noise.** Do not
  read the five-point gain as an improvement in understanding.
- **Reads are untouched and still fragile.** "I wasn't able to retrieve that
  right now" was the reply to 8 turns, including plain "show my todos"; the
  judge model rejects correct lists.

Report: `artifacts/blind/orchestrator-20261112T235639Z.json`.

## Writing expectations

Each turn has what the user says and an `expect`: one expectation, or a list of
acceptable alternatives where the message has more than one fair reading.

```json
{"say": "add a deadline: Acme logo v2 due monday 5pm",
 "expect": [
   {"kind": "write", "adds": [{"in": "todos", "match": {
     "title": {"has": ["acme", "logo"]}, "due": {"prefix": "2026-11-16"}}}]},
   {"kind": "write", "adds": [{"in": "schedule", "match": {
     "title": {"has": ["acme", "logo"]}, "date": "2026-11-16", "start": "17:00"}}]}]}
```

| Kind | Right when |
|---|---|
| `write` | Exactly the listed `adds`, `changes`, `removes` happened |
| `approval` | Nothing changed and one matching approval request is now pending |
| `ask` | Nothing changed and Kairo asked a new question |
| `read` | Nothing changed and the reply names every stored item in `shows` |
| `refuse` | Nothing changed, nothing was queued or asked, and the reply claims nothing |
| `noop` | Nothing changed (a reject, or "that is already there") |
| `open` | Never "right"; unscored unless a guardrail is broken |

`given` lists what must already be stored for the expectation to apply. If no
alternative applies, the turn is `blocked`.

A pattern maps fields to expected values. Dotted paths reach into nested values
and list positions (`recurrence.by_day`, `updates.0.start`).

| Pattern | Fits when |
|---|---|
| `"14:30"` | equal |
| `null` | empty or missing |
| `{"has": ["dentist"]}` | text contains each word, any case; a list contains each element |
| `{"lacks": ["tuesday"]}` | contains none of them |
| `{"any": ["2026-11-12", "2026-11-19"]}` | equals one of them |
| `{"set": ["MO", "WE", "FR"]}` | a list with exactly these elements |
| `{"prefix": "2026-11-16"}` | starts with it |
| `{"between": ["06:00", "11:59"]}` | within the range |

A scenario also lists `entities` (distinctive names a reply could invent, such
as "northwind") and optional `never` clauses (things that must not be stored at
any point, such as a passport number the user declined to save).

The format is validated when the set loads; a misspelt key or an expectation
that does not fit its kind is an error, not a silent pass.

## Scoring a new path

A path turns a message into a reply for one user in one data directory. All
paths write to the same stores, so they share the snapshot reader. Add a class
to `evals/blind/adapters.py` with `name`, `describe()` and `open()`, and
register it in `PATHS`. The Kairo 2.0 graph will be added this way.

## Limits

- The set is simulated and small. Differences under about ten points between
  two runs are not meaningful.
- Model paths are not deterministic; run twice before trusting a small change.
- Only local paths are scored, because the scorer reads the stores directly.
  The deployed service is not covered.
- "Invented" detection is keyword-based: it catches a reply describing a named
  item that is not stored, and it can miss inventions that use other words.
- Open-ended turns (10 of 85) are not scored for quality.
