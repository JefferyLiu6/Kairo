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
make eval-blind path=graph           # the 2.0 agent graph (KAIRO_AGENT=graph); calls the configured model
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

The orchestrator and graph paths read model credentials from `apps/api/.env` the
same way the API does. Eval turns are never sent to LangSmith.

## What is in the set

`apps/api/tests/fixtures/blind_eval_set.json`: thirteen personas, 117 turns.
Until 2026-11-13 it was the first ten (85 turns); results before that date are
on the smaller set and are not comparable with later ones.

| Scenario | Split | Habit |
|---|---|---|
| `01_freelancer` | dev | Fast lowercase shorthand, deadlines |
| `02_adhd_student` | dev | Brain dumps, "what next?" |
| `03_sales_manager` | dev | Full sentences, follow-ups |
| `04_night_nurse` | dev (first holdout) | 24-hour time, overnight shifts |
| `05_retiree` | dev | Polite full sentences |
| `06_founder` | dev | Commitments and weekly review |
| `07_bilingual` | dev (first holdout) | Mandarin and English mixed |
| `08_privacy_engineer` | dev | Sensitive data, approvals, injection |
| `09_fitness` | dev | Habits and recurring events |
| `10_coparent` | dev (first holdout) | Recurring family logistics |
| `11_music_teacher` | holdout | Students' weekly lessons, a parent's preference |
| `12_caregiver` | holdout | A parent's appointments and medication, typos |
| `13_oncall_developer` | holdout | Terse shorthand, tasks, a 1:1 |

Three personas are **held out**. Tune prompts and tools against the dev split
only, and read the holdout number to see whether an improvement generalises.
The report gives both.

A holdout wears out. The first one was read in every run while the graph was
built, and two checks were prompted by its turns, so on 2026-11-13 it became
dev and three new personas took its place. They were committed before any path
was run on them. Their first-look scores are under
[A fresh holdout](#a-fresh-holdout); the fixes that followed used those turns,
so this holdout is already partly spent. The durable answer is messages from
real users, not more simulated personas.

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

### After step 5: tasks on the agent graph

With `KAIRO_AGENT=graph` the coordinator fills task requests as typed, checked
commands ([ARCHITECTURE.md](../ARCHITECTURE.md#agent-graph-kairo-20-behind-kairo_agentgraph)).
Everything else is still handed to the orchestrator, so the fair comparison is
the 20 turns of the set that expect a change to, or a reading of, the task
list. Both runs below are from 2026-11-13 on `gpt-4o-mini`, same prompt set.

| The 20 task turns | Web chat (orchestrator) | Agent graph |
|---|---|---|
| Right | 9 | 19 |
| Wrong but reported done | 9 | 0 |
| Missed (nothing stored, user asked) | 2 | 1 |
| Model calls per turn | 2.25 | 1.05 |
| Reply time, median | 4.5 s | 1.2 s |

Across the four most recent orchestrator runs the same 20 turns scored 7, 9, 7
and 9 right with 6 to 9 wrong saves. The graph scored 17, 17, 17 and 19 right
while it was being built; the one or two wrong saves in the earlier graph runs
led to the checks listed below.

| Whole set, 85 turns | Web chat (orchestrator) | Agent graph |
|---|---|---|
| Right, of scoreable turns | 31% (22 of 70) | 49% (31 of 63) |
| Wrong but reported done | 21% (13 + 5) | 7% (5 + 1) |
| Trapped turns | 9 | 6 |
| Model calls per turn | 1.98 | 2.33 |
| Reply time, median | 3.9 s | 3.4 s |

What changed and what did not:

- **Task turns meet the bar; the whole set does not.** 65 of the 85 turns are
  still answered by the orchestrator, and every wrong save, false claim and
  trapped turn in the graph run is one of those. All three bars still fail.
- **A handed-off turn costs one more model call** (the coordinator's, then the
  orchestrator's own), which is why calls per turn rose over the whole set.
  That goes away as domains move onto the graph.
- **Three checks were added because a run showed the need**: a repeating
  reminder proposed as a one-off task is sent back and handed off; a clock time
  stated in the message must be carried into the task; a request to finish or
  remove something the task list does not have is handed on, since it may be a
  calendar event or a habit.
- **The remaining task miss is a safe one.** "send Contoso the pricing deck
  after tomorrow's call" is proposed for the day after tomorrow twice, the date
  check refuses it both times, nothing is stored and the user is asked. The
  question it asks reads like a log line and should be reworded.
- **Not yet covered.** The date check reads English date words only, so
  "提醒我周五交房租" passed in one run and was stored without its due date in
  two. That scenario is in the holdout, so it was not tuned against; date cases
  in other languages belong in the model date test first.
- **Open-ended task questions get the plain list.** "what should i do first"
  is answered with all open tasks. It is not scored, and it is not an answer.

Reports: `artifacts/blind/graph-20261113T023401Z.json` and
`artifacts/blind/orchestrator-20261113T023915Z.json`.

### After step 6: the calendar on the agent graph

The coordinator now hands calendar messages to a schedule agent with its own
prompt, tools and view of the calendar
([ARCHITECTURE.md](../ARCHITECTURE.md#the-schedule-agent)). Habits, journal,
memory and conversation still go to the orchestrator. Two recorded graph runs
on 2026-11-13, `gpt-4o-mini`; the second has one more sentence in the agent's
prompt (which range to show when none is given).

| Whole set, 85 turns | Web chat (orchestrator), two runs | Agent graph, run 1 | Agent graph, run 2 |
|---|---|---|---|
| Right, of scoreable turns | 33%, 32% | 82% (55 of 67) | 89% (62 of 70) |
| Wrong but reported done | 20%, 14% | 2.4% (2 + 0) | 0% |
| Trapped turns | 5, 9 | 0 | 0 |
| Holdout right | 43%, 33% | 75% | 87% |
| Model calls per turn | 2.04, 1.91 | 1.82 | 1.81 |
| Reply time, median | 4.1 s, 3.8 s | 2.1 s | 2.6 s |
| Bar | fail | fail (wrong 2.4% > 2%) | **pass** |

By the kind of turn (42 calendar, 18 task, 25 other):

| | Web chat, two runs | Agent graph, two runs |
|---|---|---|
| Calendar turns right | 9, 8 | 35, 41 |
| Calendar wrong saves and false claims | 10, 6 | 2, 0 |
| Calendar turns trapped | 5, 8 | 0, 0 |
| Task turns right | 8, 8 | 16, 16 |
| Calendar reply time, median | 4.4 s, 4.2 s | 2.3 s, 2.6 s |

What changed and what did not:

- **One run passes the bar and one misses it by one turn.** The bar for wrong
  saves is 2% of 85 turns, which is one turn. Run 1 had two, both in the
  holdout: three night shifts stored as one event repeating every Fri/Sat/Sun,
  and a Chinese title that lost the person's name. Run 2 had none. Treat the
  graph as at the bar, not safely past it.
- **No turn was trapped in either run.** A question is offered to the next
  message and can no longer capture the ones after it.
- **Reply time is at 2.1 to 2.6 s against a 2 s target.** A calendar turn is
  two model calls.
- **What is still missed is almost all on the orchestrator's side**: adding a
  habit, "remember that …" (the web path has no memory vault configured),
  "Export my data", "Delete all my todos". Those are the next domains.
- **Two things the graph itself still gets wrong**: "send Contoso the pricing
  deck after tomorrow's call" is proposed for the day after tomorrow twice and
  refused twice, so the user is asked (safe, but a miss); and an approval the
  user never answered stays pending, so a later bare "approve" asks which one.
- **The checks read English.** A Chinese or Spanish date word is not compared
  with the proposal, so those turns rest on the model alone.

Reports: `artifacts/blind/graph-20261113T031459Z.json`,
`artifacts/blind/graph-20261113T032430Z.json` and the two
`orchestrator-20261113T03…` runs beside them.

### After step 8: habits, the journal and memory on the agent graph

Habits and the journal are now tools the coordinator calls itself; memory has
its own agent ([ARCHITECTURE.md](../ARCHITECTURE.md#memory)). Only web search,
planning a day and conversation still go to the orchestrator. The last two
recorded runs, on the code and prompts as committed (2026-11-13, `gpt-4o-mini`):

| Whole set, 85 turns | Web chat (orchestrator) | Agent graph, run 9 | Agent graph, run 10 |
|---|---|---|---|
| Right, of scoreable turns | 42% (27 of 64) | 95% (71 of 75) | 96% (71 of 74) |
| Wrong but reported done | 15% (11 + 2) | 1.2% (1 + 0) | 0% |
| Trapped turns | 5 | 0 | 0 |
| Model calls per turn | 2.11 | 1.52 | 1.55 |
| Reply time, median | 3.9 s | 1.6 s | 1.8 s |
| Bar | fail | **pass** | **pass** |

Ten graph runs were recorded during this step, while the code changed between
them. Read the spread, not the best run:

| Ten graph runs, in order | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
|---|---|---|---|---|---|---|---|---|---|---|
| Right | 86% | 95% | 92% | 93% | 92% | 92% | 93% | 92% | 95% | 96% |
| Wrong saves + false claims | 7 | 1 | 3 | 2 | 3 | 2 | 1 | 4 | 1 | 0 |
| Trapped | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| Passes the bar | | yes | | | | | yes | | yes | yes |

What changed and what did not:

- **Habit and memory turns work.** All 6 habit turns and all 5 memory turns of
  the set were right in the last four runs. On web chat they fail outright:
  adding a habit is not understood, and "remember …" has no vault to write to.
- **Reply time is under the 2 s target** (1.5 to 1.9 s median across the ten
  runs): in the last run 78 of 85 turns took at most two model calls.
- **No turn was trapped in any of the ten runs.**
- **The wrong-save bar is one turn in 85, and a run still lands on either side
  of it.** The last two runs pass; four of ten passed overall. Two defects
  accounted for most of the misses in runs 1 to 6 and were fixed in code:
  a repeat nobody asked for ("on call sat sun 9am-5pm" stored as every Sat/Sun),
  and a routine the user asked to be remembered stored as a habit to track.
- **The holdout is no longer a clean test.** Both of those defects first showed
  on holdout turns. Each was reproduced with messages outside the set before it
  was fixed, and the checks are general, but the holdout scores above (100% and
  96% in the last two runs) have been looked at too often to estimate anything.
  New holdout personas, written without looking at these, are needed before
  the numbers are used for a launch decision.
- **Still seen in the last runs:** the schedule agent sometimes asks what to
  call an event or which Thursday is meant when today is Thursday; "what do I
  have coming up?" is sometimes answered from the task list; "send Contoso the
  pricing deck after tomorrow's call" is still refused twice and asked about;
  a Chinese title lost a person's name in 2 of 10 runs.
- **Not built:** forgetting a remembered fact, changing a task's due date, and
  removing every task at once (the user is told tasks are removed one at a
  time).

Reports: `artifacts/blind/graph-20261113T043135Z.json`,
`artifacts/blind/graph-20261113T043400Z.json`, and the eight earlier
`graph-20261113T03…`/`04…` runs listed in the ledger.

### A fresh holdout

Three new personas (32 turns), written after step 8 and committed before any
run. First look, with the step 8 code unchanged, two runs each:

| First look, 2026-11-13 | Web chat | Agent graph, run 1 | Agent graph, run 2 |
|---|---|---|---|
| Fresh holdout right | 27% | 90% | 90% |
| Whole 117-turn set right | 36% | 92% | 94% |
| Whole set, wrong saves + false claims | 15 | 3 | 2 |
| Whole set, trapped | 12 | 0 | 0 |
| Bar | fail | fail (3 of 117 is 2.6%) | pass |

So the graph generalised: 90% right on messages it had never been run on,
against 27% for web chat. Both graph runs failed the same three holdout turns,
which makes them defects rather than noise:

- "and Leo has guitar on Thursdays 5:30 to 6:15pm" also re-added the previous
  message's lesson, so it was on the calendar twice.
- "standup daily 9:30" was answered with "What day should I start?", and the
  later "delete the standup" then had nothing to delete.
- "did i add the cardiology thing" was answered from the task list.

They were then fixed as classes, each checked with messages outside the set: an
event already on the calendar is dropped by code instead of stored twice, the
schedule agent no longer asks when a repeat starts, and the coordinator sends a
reference to something not among the tasks or habits to the calendar. Probing
those turned up a fourth: after the agent asked a question, the next unrelated
calendar request was checked against the old request's wording and refused. A
message that names what it is about is now a request of its own.

After those fixes (not a first look for the holdout any more):

| After the fixes | Web chat | Agent graph, run 3 | Agent graph, run 4 |
|---|---|---|---|
| Whole 117-turn set right | 28% | 95% | 96% |
| Wrong saves + false claims | 20 | 0 | 1 |
| Trapped | 13 | 0 | 0 |
| Reply time, median | 3.7 s | 1.9 s | 1.7 s |
| Bar | fail | **pass** | **pass** |

What still fails in those two runs: "send Contoso the pricing deck after
tomorrow's call" (refused twice, asked about), the schedule agent asking for an
end time for the recital, a Chinese title that lost the person's name once, and
"remember that i sleep until 2pm after night shifts" once reworded by the model
and refused. One "miss" is the scorer's: "Yes, you have a cardiology appointment
for your dad on Tuesday, November 24" is correct but does not repeat the stored
title word for word.

Reports: `artifacts/blind/graph-20261113T044759Z.json` and `…T045107Z.json`
(first look), `…T050718Z.json` and `…T051045Z.json` (after the fixes).

### After step 9: every message on the graph

Web chat now runs on the graph by default, and conversation, planning and web
search are answered by an assistant agent instead of being handed to the
orchestrator. Two recorded runs of the 117-turn set, 2026-11-13, `gpt-4o-mini`:

| Whole set, 117 turns | Previous path (`KAIRO_AGENT=legacy`) | Agent graph, run 1 | Agent graph, run 2 |
|---|---|---|---|
| Right, of scoreable turns | 30% (24 of 81) | 97% (103 of 106) | 97% (103 of 106) |
| Wrong but reported done | 18% (19 + 2) | 0 | 1 |
| Trapped turns | 9 | 0 | 0 |
| Model calls per turn | 2.05 | 1.52 | 1.51 |
| Reply time, median / 95th | 3.7 s / 6.1 s | 1.7 s / 3.7 s | 1.7 s / 4.1 s |
| Bar | fail | **pass** | **pass** |

- **No turn takes more than four model calls**, and 112 of 117 take two or
  fewer. Before this step a turn handed to the orchestrator cost three or four.
- **What still misses:** "send Contoso the pricing deck after tomorrow's call"
  (refused twice, asked about); the schedule agent once asked what to call an
  event; "block friday morning" was once stored on two Fridays. One counted
  miss is the scorer's: "Yes, you have a cardiology appointment for your dad on
  Tuesday, November 24" is right but does not repeat the stored title.
- **The holdout figure (97% in both runs) is not a first look**; see
  [A fresh holdout](#a-fresh-holdout).

Reports: `artifacts/blind/graph-20261113T053448Z.json` and `…T053811Z.json`.

### After the voice pass

A small model now rewords each reply after it is built, and the rewording is
sent only if it keeps the draft's facts (`kairo/agents/graph/voice.py`). Two
recorded runs of the 117-turn set, 2026-11-13, `gpt-4o-mini`:

| Whole set, 117 turns | Before the voice pass (two runs) | Run 1: models read the rewording | Run 2: models read the draft |
|---|---|---|---|
| Right, of scoreable turns | 97% and 97% | 94% (98 of 104) | 96% (100 of 104) |
| Wrong but reported done | 0 and 1 | 2 | 1 |
| Trapped turns | 0 | 0 | 0 |
| Model calls per turn | 1.52 and 1.51 | 2.34 | 2.38 |
| Reply time, median / 95th | 1.7 s / 3.7–4.1 s | 2.3 s / 4.7 s | 2.6 s / 6.3 s |
| Bar | pass | **pass** | **pass** |

- **Run 1 kept the reworded reply in the history the models read.** One turn
  went wrong in a way that pointed at that: after a reworded reply, "add todo
  fix figure 3 due friday" was answered by checking the task off. Run 2 keeps
  the draft in the models' history and shows the rewording only to the user,
  so wording cannot change a later decision. That is how it runs now.
- **What missed in run 2 was decided before the reply was worded:** the Contoso
  task (as before), the cardiology title (as before), and "standup daily 9:30"
  refused over its repeat, which left two later turns without the event they
  needed.
- **Run 1 exposed a memory fault that is not the voice pass's.** "Maya's mum
  prefers texts over calls" was confirmed as saved and then rewritten to
  "User's mum prefers texts over calls" by the background profile tidy-up.
  Replies used to return before that finished, so the scorer did not see it.
- **One run each.** The difference between 96% and 97% is one turn.

Reports: `artifacts/blind/graph-20261113T123719Z.json` and `…T124444Z.json`.

### After the calendar agent looks before it asks

Given a day but no time, the calendar agent now reads the day's free time
(`find_free_time`) and suggests one (`suggest_event`), which waits for the
user's approval. The runner counts what each turn hands back to the user: a
new question, or an approval to give. One turn's expectation changed with the
behaviour: "schedule a dentist appointment on saturday" is right if a Saturday
time is suggested for approval, or if a question is asked. Two recorded runs,
2026-11-13, `gpt-4o-mini`:

| Whole set, 117 turns | Before (voice pass, run 2) | Run 1 | Run 2 |
|---|---|---|---|
| Right, of scoreable turns | 96% (100 of 104) | 96% (102 of 106) | 98% (104 of 106) |
| Wrong but reported done | 1 | 1 | 0 |
| Trapped turns | 0 | 0 | 0 |
| Replies that end in a question | 9 | 4 | 4 |
| Approvals asked for | 16 | 20 | 19 |
| Model calls per turn | 2.38 | 2.40 | 2.38 |
| Reply time, median / 95th | 2.6 s / 6.3 s | 2.3 s / 5.5 s | 2.1 s / 4.2 s |
| Bar | pass | **pass** | **pass** |

- **Questions went from 9 to 4, but only one of the five is this change.** The
  dentist turn is the only one in the set that names a day and no time. The
  other four differ because of what the models decided on earlier turns, which
  varies between runs. The set needs more turns of this kind before the count
  means much.
- **The suggestion uses what is remembered.** The nurse had said "i sleep until
  2pm after night shifts". The first version suggested 7:30 AM, straight after
  a night shift. Listing the usual hours first moved it to 9 AM. Making the
  agent write what limits the time before it gives a start moved it to 2 PM,
  in three runs of three.
- **Run 1 found a regression, fixed before run 2.** "block friday morning for
  the Acme logo work" was suggested for approval instead of stored as 9 to 12.
  A part of the day with no length is now refused as a suggestion.
- **An update that changes nothing is sent back.** "add shift tomorrow 7pm to
  7:30am", with that shift already there, had been turned into an approval to
  move it to the time it already had.
- **The memory fault is still there.** Run 1's one false claim is the
  background profile tidy-up rewording "Maya's mum" to "User's mum" again.

Reports: `artifacts/blind/graph-20261113T175000Z.json` and `…T175538Z.json`.

### After batch approvals

Several removals are now proposed in one call (`remove_todos`, `remove_events`)
and asked for in one approval that lists each. Two expectations changed with
the behaviour: "Delete all my todos" and the "reject" after it accept a `batch`
approval as well as a single one. Two recorded runs, 2026-11-13, `gpt-4o-mini`:

| Whole set, 117 turns | Run 1 | Run 2 |
|---|---|---|
| Right, of scoreable turns | 96% (101 of 105) | 97% (102 of 105) |
| Wrong but reported done | 2 (1 wrong save, 1 false claim) | 0 |
| Trapped turns | 0 | 0 |
| Questions put to the user | 3 | 3 |
| Approvals asked for | 18 | 20 |
| Model calls per turn | 2.39 | 2.42 |
| Reply time, median / 95th | 2.1 s / 5.6 s | 2.0 s / 4.4 s |
| Bar | pass, at the limit | **pass** |

- **Run 1's wrong save came from the previous step.** Told not to ask for a
  time, the agent stored "pharmacy pickup tmrw" at 9 AM through `create_events`.
  The gate now refuses a time in `create_events` when the message has no digit
  and no word that places it in the day. In run 2 the same turn became a
  suggestion waiting for approval, which the set counts as a miss: it expects a
  task or a question there.
- **Run 1's false claim was the profile tidy-up again**, this time turning a
  fact stored in Chinese into English. Its prompt asked for facts to be merged
  and put in the third person; it now says to copy every kept fact exactly. No
  rewritten fact in run 2. One run is not proof it is gone.
- **Found while trying scenarios:** "can i fit a 90 min workshop on thursday
  afternoon?" was answered with an event to approve. A question is now refused
  as a suggestion and answered from the free-time lookup instead.
- **The set has one bulk request,** and at that point its list holds one task,
  so the batch path is covered by the scripted tests, not by these runs.

Reports: `artifacts/blind/graph-20261113T181829Z.json` and the run after it.

## The scores ledger

A prompt's version says that it changed, not whether it got better. The ledger,
`apps/api/evals/scores.jsonl`, ties each recorded run to the prompt versions,
model, eval set and commit that produced it. It is committed with the code and
only ever appended to.

```bash
make eval-blind path=orchestrator record="what changed and why"   # run and record
make eval-history                                                  # print the ledger
```

- **A note is required**, so the history reads as a list of reasons.
- **Only whole-set runs can be recorded.** A run of selected scenarios cannot
  be compared with the others.
- **The prompt set** is one short hash over every prompt's version. Two runs
  with the same prompt set, model and eval set differ only by code and chance.
- **A prompt edit is not finished until it is scored.**
  `tests/evals/test_blind_ledger.py` fails when the current prompt set has no
  recorded web-chat run, and says which command to run.

The three runs from 2026-11-12 were added from their saved reports. A fourth,
recorded after a change that did not alter behaviour, shows how far two runs of
the same system differ:

| Same system, two runs | Run A | Run B |
|---|---|---|
| Right, of scoreable turns | 39% | 38% |
| Holdout right | 33% | 26% |
| Wrong but reported done | 16% | 16% |
| Trapped turns | 3 | 5 |
| Model calls per turn | 2.04 | 2.15 |
| Reply time, median | 3.2 s | 3.6 s |

Read a difference of that size as noise. The holdout split is three personas,
so it swings the most.

Each web-chat turn also records what produced it. The decision log row for an
orchestrator turn has a `versions` field with the prompts sent (name and
version), the models, and the receipt statuses, and the `llm_usage` telemetry
event names the prompt and model of each call. Fallback and mismatch rates in
real use can therefore be grouped by prompt version or model.

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
register it in `PATHS`. The Kairo 2.0 graph was added this way (`GraphPath`,
which reuses the web-chat configuration and only changes how a message is sent).

## Limits

- The set is simulated and small. Differences under about ten points between
  two runs are not meaningful.
- Model paths are not deterministic; run twice before trusting a small change.
- Only local paths are scored, because the scorer reads the stores directly.
  The deployed service is not covered.
- "Invented" detection is keyword-based: it catches a reply describing a named
  item that is not stored, and it can miss inventions that use other words.
- Open-ended turns (10 of 85) are not scored for quality.
