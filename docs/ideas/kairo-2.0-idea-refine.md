# Kairo 2.0 — Idea Refine (working doc)

**Started:** 2026-11-12 · **Status:** Direction, metrics and build order confirmed. Step 1 (blind eval) is done; baselines are in [BLIND_EVAL.md](../BLIND_EVAL.md)
**Inputs:** [ten simulated interviews](interviews/README.md)

We fill this in together. Claude updates it after each answer.

---

## Decisions so far

| # | Question | Answer | Date |
|---|----------|--------|------|
| 1 | Who is 2.0 for? | **Outside users** ("ideally"). Not primarily a portfolio piece. | 2026-11-12 |
| 2 | What does success look like in 3 months? | **Reach:** a lot of people have tried it (sign-ups, traffic, stars), whether or not they stay. Target number still to set. | 2026-11-12 |
| 3 | What is the binding constraint? | **Money.** Hosting and model costs must stay near free. | 2026-11-12 |
| 4 | Which directions resonate? | **1 Right the first time, 2 Ten-second try, 3 Brain dump to plan** — these first; others later. | 2026-11-12 |
| 5 | Where do the first visitors come from? | **Developer communities** (Hacker News, GitHub, r/LocalLLaMA). | 2026-11-12 |
| 6 | Final direction confirmed? | **Yes**, with the routing choice (workflow-first vs model-first) still to be compared. | 2026-11-12 |
| 7 | Routing: workflow-first, model-first, or trimmed model-first? | **Superseded by decision 9.** Jeffery stopped the comparison; partial live results are noted under decision 9. | 2026-11-12 |
| 9 | How to upgrade the agent | **Re-architect, do not patch.** Jeffery: the keyword extraction is over-complicated and a main cause of the mistakes. Replace it with better state management, a coordinator agent, and sub-agents with their own assigned tools. Proposal: [kairo-2.0-agent-architecture.md](kairo-2.0-agent-architecture.md), awaiting review. | 2026-11-12 |
| 10 | Build order | **Measure first.** Nine steps in [the architecture doc](kairo-2.0-agent-architecture.md#11-migration). Step 1 done 2026-11-12: baselines are 47% right for the offline workflow and 34% for the web-chat path on `gpt-4o-mini`, with 9% and 18% wrong-but-reported-done. Both fail all three bars. Step 2 done the same day: typed tool calls get dates right on `gpt-5.4-mini` (about 1% wrong) but not on `gpt-4o-mini` (9%); see [MODEL_DATE_TEST.md](../MODEL_DATE_TEST.md). Step 3 done the same day: writes on the web-chat path are answered from what was stored. | 2026-11-12 |
| 8 | KPIs and targets | **Accepted as proposed** (Jeffery: "ok"), including a $20 monthly model spend cap. Any number can still be changed. | 2026-11-12 |

---

## Phase 1 — Understand and expand

### Problem statement (draft)

How might we make Kairo something a person keeps using after the first five
messages, given that it currently stores items reliably only for people who
phrase things like the README?

_Audience confirmed as outside users. Still draft until we know which outside
users (question 2 onward)._

**What decision 1 changes:**

- Demo mode, 5-credit accounts and non-durable storage become blockers, not
footnotes. Real users need accounts and data that persist.
- Direction 6 (the eval as the product) drops to a side effect; it serves
reviewers, not users.
- Directions 1 and 5 become mandatory groundwork: outside users will not
forgive a wrong save reported as done.
- Running cost matters: every chat turn calls a model, and outside users do not
bring their own key.

**What decision 2 changes:**

- The first five messages are the whole product. Most people who try it will
never send a sixth, so first-message understanding and a good answer to
"what can you do" outrank every deeper feature.
- Trying must be free of friction: no sign-up wall before the first message,
and no 5-credit ceiling that ends the trial mid-thought.
- There has to be a reason to share it. A reliable assistant is not a reason;
something a person can show in one screenshot is.
- Cost per trial is the scaling limit. Many tries at one model call per turn
adds up fast.
- Direction 3 (speaks first) and deep retention features lose priority; they
pay off after week one, which this success measure does not count.
- Tension to keep in view: decision 1 says "outside users", decision 2 measures
tries, not use. A launch can hit the number and leave nobody using it.

**What decision 3 changes:**

- The offline typed workflow is the cost advantage. A turn it handles costs
nothing; a turn that falls through to a model costs money. Every phrasing the
workflow learns to understand is a cheaper trial, so direction 1 is now a
cost measure as well as a quality one.
- Reach and near-zero cost pull against each other. The ways to hold both:
workflow first and model only on fallback, a small cheap model for the
fallback, a hard per-visitor turn budget, or bring-your-own-key for heavy
users.
- Anything that spends model calls with nobody watching (proactive briefs,
background summaries) is out for now.
- Durable storage for many accounts also has to fit a free tier.

### Directions re-ranked after decisions 1–3

For outside users, measured by tries, at near-zero cost:

| Rank | Direction | Why it moved |
|------|-----------|--------------|
| 1 | **Right the first time** | First messages decide everything, and workflow-handled turns are free |
| 2 | **Ten-second try** (new) | No sign-up before the first message; a guided first run that shows one thing working and the approval gate in action |
| 3 | **Brain dump to plan** (new) | Paste a messy paragraph about your week, get todos and events laid out for one-tap approval. One model call, one screenshot-worthy result. The shareable hook |
| 4 | **Receipts** | Full read-back and before/after approval; what makes a stranger trust it enough to continue |
| 5 | One person, one job | Still sensible, but narrowing the audience works against reach in month one |
| 6 | Answer, don't just store | Mostly needs model calls and pays off after several sessions |
| — | Kairo speaks first | Background model spend; excluded by decision 3 |
| — | The eval is the product | Serves reviewers; excluded by decision 1 |

### What we know

- Kairo 1.0 is a deployed, heavily verified portfolio demo: 280 tests, 252/252
offline evals, approval gates, audit log, decision trace.
- In ten simulated trials, 29 of 85 un-coached turns did what the user meant.
Offline workflow only; the live orchestrator was not tested.
- Real observed defects: wrong saves reported as done, pending questions that
swallow later messages, habits that cannot be created with natural phrasing,
explicit times ignored in favour of slot suggestions.
- Role-played (unverified) demand: answering questions over stored data,
speaking first, full read-back.

### Six directions

| # | Direction | One line | Cost | Evidence |
|---|-----------|----------|------|----------|
| 1 | Right the first time | No new features; fix wrong saves and pending-state traps; blind-phrasing set becomes the headline metric | Low | Real trial output |
| 2 | Answer, don't just store | "Can I fit this in?", "what next?", "what do I owe?", "what slipped?" | Medium | Role-play only |
| 3 | Kairo speaks first | Morning brief, evening review, "about to miss it" prompts | High (needs outbound channel and scheduler) | Role-play only |
| 4 | One person, one job | Commitments tracker for the over-committed; cut habits and journal | Medium | Role-play only |
| 5 | Receipts | Full read-back on every write, before/after in approvals, undo, "show me everything" | Low–medium | Real trial output plus role-play |
| 6 | The eval is the product | Blind benchmark and saved-versus-asked checker as the portfolio headline | Low | Real trial output |

Left out: multi-user features (event owners, notifying a partner). Different product.

### Claude's lean (not a decision)

1 + 5 as the foundation, then 4 using the questions from 2. Hold 3. Write up 6
as the story.

### Jeffery's reactions

_To fill in._

---

## Phase 2 — Evaluate and converge

The three chosen directions are a sequence, not alternatives: 1 makes the
first messages work, 2 gets people to the first message, 3 gives them something
to show.

### Finding from the code that affects all three

`apps/api/kairo/agents/orchestrator/agent.py` (`_astream_orchestrator`): a
normal web-chat turn that touches data makes up to **four model calls** — route,
translate, judge, humanize — plus more on retry. Only approve/reject commands
skip the model. So today the free typed workflow never answers a web turn on
its own, and each turn a visitor sends costs several calls.

Same function: when a question is pending, the next message is passed to the
workflow as-is (`continue_request`), without translation. That makes the
"pending question swallows later messages" defect likely to reproduce in the
live app. Not yet confirmed by running it.

### Stress test

**1. Right the first time**

- User value: painkiller. A wrong save reported as done ends a trial.
- Feasibility: mostly known bugs with real reproductions (pending-state exit,
digits-as-time, ignored explicit times, title fragments, habits). The larger
piece is a workflow-first path for web chat: try the typed workflow, call a
model only when it does not recognise the message. That changes four calls
per turn to zero for anything the workflow handles.
- Differentiation: none on its own. Nobody tries a product because it is
correct. It is the entry fee.
- Betting on: the workflow can cover most first messages without a model.
Trial data says it covers about a third today.
- Could kill it: workflow-first replies read as robotic next to the humanized
ones, and the product feels worse even though it is right.
- Ignoring for now: long-tail phrasing. The model fallback covers it.

**2. Ten-second try**

- User value: removes the reason most visitors leave before typing anything.
- Feasibility: partly built. The public frontend already starts in temporary
demo mode. The gaps are the 5-credit limit, no guided first run, and no
answer to "what can you do". Abuse control exists (per-IP demo limits).
- Differentiation: low, but it is where the approval gate can be shown in the
first minute, and that is Kairo's one distinctive behaviour.
- Betting on: people will type into an empty chat box if nudged with examples.
- Could kill it: open, sign-up-free access with model calls behind it gets
scraped or spammed and the bill arrives. Only safe once direction 1's
workflow-first path is in.
- Ignoring for now: durable accounts. Temporary data is acceptable for a trial,
but it caps how "used" the product can ever be. Conflicts with decision 1
beyond month three.

**3. Brain dump to plan**

- User value: unknown. This is the weakest evidence of the three; it comes from
one role-played persona. If real, it is the only direction that gives a
visitor a result worth showing someone.
- Feasibility: one model call to split a paragraph into typed actions, then the
existing workflow and approval queue. The typed-action and approval pieces
already exist; batch approval ("approve all 7") may not.
- Differentiation: moderate. Other assistants turn text into tasks. Showing
every proposed change for approval before anything is saved is the Kairo
angle.
- Betting on: people have a messy week in their head and will paste it into a
stranger's website.
- Could kill it: extraction is 80% right, and the visitor's first impression is
correcting the other 20%.
- Ignoring for now: calendar import, recurring patterns inside the dump.

### Order and dependency

1 → 2 → 3. Opening access (2) before the workflow-first path (1) exists breaks
decision 3. Direction 3 is the only one that spends a model call by design, so
it needs a per-visitor budget.

### Gap none of the three covers

Decision 2 is "a lot of people try it". Nothing above brings anyone to the
site. Distribution is unplanned, and the channel decides the audience, which
decides what the first run should show.

---

## Phase 3 — One-pager

**Draft — awaiting Jeffery's yes / no / refine (decision 6).**

### What decision 5 changes

- Developers judge in the first minute and look for the catch. A wrong save
reported as done would be the top comment. Direction 1 is non-negotiable.
- Bring-your-own-key becomes natural and solves cost for the heaviest users.
- The repo is part of the product: a one-command self-host matters as much as
the hosted demo.
- The blind-phrasing result ("suite said 100%, un-coached test said 34%") is a
launch-post asset for this audience. It returns as the story, not the product.
- Brain dump to plan loses its natural audience. It was the hook for
productivity communities.

### Problem statement

How might we let a developer try Kairo in ten seconds at near-zero cost to us,
and have its first five messages be right or honestly say they are not?

### Recommended direction

**Kairo 2.0 is the assistant that shows its work, and you can try it without
signing up.** A visitor lands, sees three example messages, sends one, and
watches it get understood by the typed workflow, gated for approval where it
changes something, and explained in the decision trace. Those pieces exist.
What is missing is that they work on a stranger's phrasing and that the visitor
reaches them in the first minute.

The build is two steps. First, make web chat workflow-first, so a turn the
typed workflow recognises costs nothing and a model is called only on
fallback; fix the known wrong-save and pending-state defects against a blind
phrasing set. Second, open the front door: no sign-up, a guided first run,
a visible turn budget, and a field for the visitor's own model key once the
budget is spent.

Brain dump to plan is held as the first experiment after launch. It needs a
model call by design, its evidence is one role-played persona, and the launch
audience is not the one it was meant for.

### Key assumptions to validate

- [ ] The trial defects reproduce in the live app. Test: run the 85 trial
  ```
  messages through `/orchestrator/stream` with a model key. Do this first.
  ```
- [ ] The typed workflow can handle a majority of first messages with no
  ```
  model. Test: after fixes, re-run the blind set offline; target at least
  70% handled correctly, up from about a third.
  ```
- [ ] Workflow-only replies are acceptable without the humanize step. Test:
  ```
  show five people both versions of ten replies.
  ```
- [ ] Developers will try a personal assistant demo at all, as opposed to
  ```
  reading the repo. Test: the launch itself; measure visitors who send at
  least one message.
  ```
- [ ] Free-tier hosting survives a front-page spike when most turns make no
  ```
  model call. Test: load test the workflow-first path.
  ```

### MVP scope

In:

- Cheaper, faster routing for web chat. Which form (workflow-first or trimmed
model-first) is decision 7, settled by the comparison experiment below.
- Fixes with reproductions on file: pending question that traps later
messages, digits in titles read as times, explicit times ignored for slot
suggestions, habit creation, title fragments, durations.
- Full read-back on every write and before/after in approval prompts (the
cheap half of "Receipts"; a developer audience will check).
- No-sign-up trial with a guided first run, an answer to "what can you do",
and a per-visitor turn budget shown on screen.
- Bring-your-own-key field.
- A blind phrasing set in the repo, with the before and after numbers.
- One-command self-host that works from a fresh clone.

Out: everything in the next section.

### Not doing (and why)

- **Brain dump to plan** — unvalidated, costs a model call per use, wrong
audience for this launch. First experiment afterwards.
- **Kairo speaks first** — background model spend; excluded by the cost
constraint.
- **Durable accounts for the hosted demo** — trial data can be temporary.
Self-hosters get persistence. Revisit if people ask to keep their data.
- **Narrowing to one persona** — works against reach in the first three months.
- **Questions over stored data** ("what slipped", "what do I owe") — pays off
after several sessions, which this launch does not measure.
- **Multi-user features** — different product.
- **More judge rubric versions** — the gap is in inputs, not grading.

### Routing comparison: workflow-first vs model-first

**How a web turn runs today (model-first).** A keyword check decides whether
the message needs data. If so: a model translates it into a trigger phrase, the
typed workflow executes, a model judges the result, a model rewrites the reply.
That is three model calls for a typical data turn, four when the keyword check
is unsure, more on retry. Approve and reject skip the model.

**Workflow-first.** The raw message goes to the typed workflow. A model is
called only when the workflow says it does not recognise the message.

**Trimmed model-first (a third option).** Keep the translate call so the model
does the understanding. Drop the judge and humanize calls when the workflow
returns a typed, deterministic result. One call per turn.

| | Model-first (today) | Workflow-first | Trimmed model-first |
|---|---|---|---|
| Model calls per data turn | 3–4 | 0 when recognised, 3–4 otherwise | 1 |
| Understanding of un-coached phrasing | Expected to be best; **not measured** | About a third of turns right in offline trials | Same as model-first |
| Wrong save reported as done | Unknown live. The translator may normalise away some causes | **Exposed directly.** The workflow does not know when it is wrong ("v2" → 2 AM, "1:1" → 1 AM) | Same as model-first, minus the judge as a safety net |
| Latency | Three sequential calls; seconds | Milliseconds when recognised | One call |
| Survives a traffic spike | Depends on provider rate limits | Yes for recognised turns | Depends on provider, a third of the load |
| Reply tone | Humanized | Terse workflow text | Terse workflow text |
| Build effort | None | Medium: needs the workflow to abstain reliably before it can go first | Small |
| Pending-question bug | Likely present (messages bypass translation when a question is open) | Present | Likely present |

**Rough cost, to be measured.** Assuming about 1,000 input and 150 output
tokens per call and small-model pricing in the range of gpt-4o-mini, a
three-call turn costs well under a tenth of a cent. 1,000 visitors at five
turns each is a few dollars; a 10,000-visitor spike is tens of dollars, more
with a Haiku-class model. These are estimates from memory of list prices and
guessed token counts. The code already emits `llm_usage` per call, so the real
figure is one test run away.

**What this changes.** Earlier in this doc the case for workflow-first rested
on cost. On these estimates, cost with a per-visitor turn budget is small
either way. The stronger arguments for workflow-first are latency and not
depending on a provider during a spike. The strongest argument against it is
the trial evidence: the workflow's typical failure is a confident wrong save,
and going workflow-first puts that failure in front of every visitor.

**Claude's prior (not a decision).** Trimmed model-first, plus a workflow fast
path for a short list of exact patterns it already gets right ("show my
todos", "add todo …", approve, reject). Move to workflow-first later, once the
workflow can reliably say "I am not sure".

**The experiment that decides it.** Run the same 85 trial messages through
three arms and record, per arm: turns right, wrong-but-reported-done, not
understood, model calls, tokens, and latency. Needs one model key. The offline
arm is already done (29 of 85).

### Metrics

**Accepted as proposed on 2026-11-12 (decision 8).**

At a 1 cent cost per activated visitor, a $20 cap covers about 2,000 activated
visitors a month. A larger spike has to fall back to bring-your-own-key or a
"demo is busy" state; that behaviour needs designing.

#### Launch KPIs (the three months after 2.0 ships)

| | Metric | Definition | Proposed target | Why this one |
|---|--------|------------|-----------------|--------------|
| **Primary** | Activated visitors | Unique visitors who send at least one message | 1,000 in the launch month | This is "a lot of people tried it" (decision 2). Page views and stars do not prove anyone tried anything |
| Funnel | Activation rate | Activated visitors ÷ unique visitors | 30% | Measures the ten-second try. Low means the front door is the problem |
| Funnel | Reached the gate | Activated visitors who see at least one approval prompt | 40% | The approval gate is the one thing Kairo does that others do not; a trial that never shows it has shown nothing |
| Funnel | Five-message sessions | Activated visitors who send five or more messages | 25% | Cheapest available signal that the first messages went well |
| Secondary | GitHub stars | Stars gained since launch | 200 | Developer-audience signal (decision 5). Secondary because it does not require trying the product |
| Secondary | Self-host starts | Clones, or runs of the one-command setup if measurable | Track only | Closest thing to real use for this audience |

Retention is deliberately not a KPI for this period (decision 2). Track
returning visitors anyway, without a target, so month four has a baseline.

#### Quality bar (measured on the blind phrasing set before launch)

| Metric | Definition | Today (offline workflow) | Proposed bar to ship |
|--------|------------|--------------------------|----------------------|
| Right | Saved state or answer matches what was asked | 29 of 85 (34%) | 70% or better |
| **Wrong but reported done** | Kairo confirms a write whose saved state differs from the request | 12 of 77 in round 2 (16%); not separately counted for the 85 | **2% or lower** |
| Honest miss | Kairo says it did not understand, and saves nothing | — | Whatever remains |
| Trapped turns | Messages answered with a repeat of an unrelated pending question | Seen in 3 of 10 trials | 0 |

"Wrong but reported done" is the guardrail that outranks the others. A miss
costs one message; a false "Done!" costs the visitor's trust and, with this
audience, the comment thread.

Scoring rule: a turn is judged on what ended up stored, not on the wording of
the reply. Expected outcomes are written down before a run, not after.

#### Cost and speed guardrails

| Metric | Definition | Proposed limit |
|--------|------------|----------------|
| Model cost per activated visitor | Total model spend ÷ activated visitors | 1 cent or lower |
| Monthly spend cap | Hard stop on hosted-demo model spend | $20 |
| Reply time, typical | Median time from send to full reply | 2 seconds or lower |
| Reply time, slow end | 95th percentile | 6 seconds or lower |
| Turn budget per visitor | Free turns before bring-your-own-key | 15 (today: 5) |

#### Decision rule for the routing experiment (decision 7)

Set before running, so the result cannot be argued into shape afterwards.

1. Discard any arm whose wrong-but-reported-done rate is above 5% on the 85

   messages. (Looser than the ship bar because known defects are not yet fixed.)
2. Among the rest, take the arm with the highest right rate. Arms within 10

   points of each other count as tied; 85 messages cannot resolve smaller gaps.
3. Break ties by cost per turn, then by median reply time.
4. If no arm passes step 1, the routing choice waits and the wrong-save fixes

   go first.

Recorded per arm: right, wrong-but-reported-done, honest miss, trapped turns,
model calls per turn, input and output tokens, cost per turn, median and 95th
percentile reply time.

### Open questions

- What number is "a lot"? Now proposed as the primary KPI above. Proposed: 1,000 visitors who send at least one
message in the launch month, and 200 GitHub stars. Jeffery to set.
- Developers will try it and star it; few will run their life on it. Decision 1
says the product is for outside users. Is this launch the route to them, or
a detour? Worth an explicit answer before month four.
- Which model for the fallback path, and what does a fallback turn cost?
- Does the hosted demo's free tier allow enough durable storage for even
temporary per-visitor data at spike volume?
