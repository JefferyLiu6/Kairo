# Kairo + Orchestrator Architecture

This document describes the current Kairo orchestrator and controlled workflow
implementation. It is meant to be accurate enough for code review, not a
marketing diagram.

Primary source files:

- [`apps/api/kairo/agents/orchestrator/agent.py`](apps/api/kairo/agents/orchestrator/agent.py) - conversational router/translator/harness/humanizer over the PM agent
- [`apps/api/kairo/agents/orchestrator/router.py`](apps/api/kairo/agents/orchestrator/router.py) - heuristic plus model routing for direct vs delegated turns
- [`apps/api/kairo/agents/orchestrator/translator.py`](apps/api/kairo/agents/orchestrator/translator.py) - user request to structured PM trigger prompt
- [`apps/api/kairo/agents/orchestrator/harness.py`](apps/api/kairo/agents/orchestrator/harness.py) - PM result checking, retry, cache fallback, fallback logging
- [`apps/api/kairo/agents/personal_manager/agent.py`](apps/api/kairo/agents/personal_manager/agent.py) - sync/streaming entrypoints and tool-free fallback
- [`apps/api/kairo/agents/personal_manager/workflow.py`](apps/api/kairo/agents/personal_manager/workflow.py) - typed turn coordinator
- [`apps/api/kairo/agents/personal_manager/application/extraction.py`](apps/api/kairo/agents/personal_manager/application/extraction.py) - deterministic/model extraction arbitration
- [`apps/api/kairo/agents/personal_manager/application/planner.py`](apps/api/kairo/agents/personal_manager/application/planner.py) - intent-to-action planning
- [`apps/api/kairo/agents/personal_manager/application/approval_policy.py`](apps/api/kairo/agents/personal_manager/application/approval_policy.py) - destructive/privacy approval gates
- [`apps/api/kairo/storage/`](apps/api/kairo/storage) - working memory, approvals, audit, traces, personalization, semantic memory

## Package Layout

The backend package is split into layers, and imports only point down:

```text
api  ->  agents  ->  domains  ->  tools  ->  storage / core  ->  utils
```

| Layer | Holds | Examples |
| --- | --- | --- |
| `kairo/api/` | HTTP only: FastAPI app, auth, rate limits | `app.py`, `auth.py` |
| `kairo/agents/` | Anything that decides what to do with a message | `graph/` (2.0 agent graph), `orchestrator/`, `personal_manager/` (workflow, planning, clarification, `pending_reply.py`) |
| `kairo/domains/` | What Kairo can do, declared as typed tools grouped by area | `registry.py`, `tasks.py`, `schedule.py`, `checks.py`, `catalog.py` |
| `kairo/tools/` | Actions the agents can take | `executors/`, `calendar/`, `resolvers/`, `memory/`, `web_search.py` |
| `kairo/storage/` | All SQLite and file persistence | `control_store.py`, `working_memory.py`, `store.py` |
| `kairo/core/` | Shared foundations, no HTTP and no SQL | `llm.py` (contract gateway), `domain/`, `observability/` |
| `kairo/utils/` | Small helpers with no app state | `parsing/`, `clock.py`, `formatters.py`, `sensitivity.py` |
| `evals/` | Eval, judge and monitoring tools, outside the package | `judge_eval.py`, `pm_runner.py` |

`tests/test_architecture.py` fails if any module imports a layer above its own, or if
`kairo` imports `evals`.

## System Map

```mermaid
flowchart TB
    UI["React UI<br/>apps/web/src"] <-->|HTTP + SSE| API["FastAPI<br/>kairo/api/app.py"]

    API -->|/orchestrator/stream| ORCH["Orchestrator<br/>kairo/agents/orchestrator"]
    API -->|/personal-manager/stream| PM["Kairo PM workflow<br/>agent.py"]
    ORCH -->|delegates via astream_pm| PM
    ORCH -->|direct reply| OLLM["Orchestrator LLM<br/>chat, routing, humanizing"]
    ORCH --> OMEM["Orchestrator memory<br/>recent turns (SQLite) + PM cache (in-process)"]
    ORCH --> OTRACE["Orchestrator decision trace<br/>turn_decision_log"]

    PM --> TYPED["Typed workflow<br/>workflow.py"]
    PM --> FALLBACK["Tool-free LangGraph fallback<br/>low-risk chat only"]

    TYPED --> EXTRACT["Plan extraction<br/>deterministic + optional model arbitration"]
    TYPED --> STATE["Dialogue state<br/>working_memory"]
    TYPED --> PLAN["Planner<br/>PMIntent -> PMAction"]
    PLAN --> POLICY["Approval policy"]
    POLICY --> EXEC["Executors<br/>schedule, todo, habit, journal, memory"]

    TYPED --> OBS["PM decision trace + audit events"]
    FALLBACK --> CKPT["checkpoints.db<br/>fallback conversation state"]

    EXEC --> LOCAL["Local JSON state<br/>schedule, todos, private notes"]
    EXEC --> PMDB["pm.db<br/>approvals, audit, traces, prefs, calendar mirror"]
    EXEC --> VAULT["vault/PROFILE.md<br/>shared profile facts"]
    EXEC --> GCAL["Google Calendar<br/>optional sync/write path"]
```

## Orchestrated Web Turn

The React app uses `/orchestrator/stream` for Kairo chat. The older
`/personal-manager/stream` endpoint still exists for direct Kairo PM streaming.

```mermaid
flowchart TD
    A["User message"] --> B["astream_orchestrator"]
    B --> C["Build memory context<br/>profile + recent turns"]
    C --> D{"Needs PM agent?"}

    D -->|no| E["Direct orchestrator reply"]
    D -->|yes| F["Translate to StructuredAction<br/>intent, confidence, pm_prompt, is_write"]

    F --> G{"Confidence < 0.70<br/>or direct intent?"}
    G -->|yes| E
    G -->|no| H["Call PM agent with pm_prompt<br/>astream_pm, inside a receipt collector"]

    H --> W{"Did a write run,<br/>and was it read back?"}
    W -->|yes| RB["Reply from the stored records<br/>no judge, no humanizer"]
    W -->|"workflow answered a write<br/>request without writing"| PT["Workflow's own reply, unchanged"]
    W -->|"no: a read, an unconfirmed write,<br/>or the chat fallback"| I["Harness precheck / model judge"]
    I -->|pass| J["Humanize PM output"]
    I -->|retry| K["Retry with suggested PM prompt<br/>max 2 retries"]
    K --> H
    I -->|fallback| L["Safe fallback reply<br/>read cache if available"]

    RB --> M["Update in-process memory<br/>and log orchestrator turn"]
    PT --> M
    J --> M
    L --> M
    E --> M
    M --> N["SSE token/progress reply"]
```

### Receipts

Storage is the evidence for a write. While the PM agent runs, every typed
action that writes is wrapped by `kairo/tools/receipts.py`: the user's stores
are read before and after, the difference is checked against the command, and a
`Receipt` (`kairo/core/domain/receipt.py`) records the result.

| Receipt | Meaning | Reply |
| --- | --- | --- |
| `verified` | The stores changed the way the command said | One sentence per stored change, from the stored record |
| `mismatch` | The stores changed, but not as the command said | The same, plus a note naming what differs |
| `failed` | The executor refused or errored | Its own explanation, or "nothing was saved" for an internal error |
| `unverified` | Success reported, nothing changed locally (already done, or written to Google Calendar) | Not used; the turn goes to the judge as before |

The decision log row for each orchestrator turn records what produced it in a
`versions` field: the prompts sent (name and version), the models, and the
receipt statuses.

An approved change is read back the same way; the sentence is kept with the
approval so sending the approval again returns the same reply. A receipt shows
what is stored, including the weekday, date and times, so a request the
workflow parsed wrongly is visible in the reply. It does not make the parse
right: the stores are compared with the command, not with what the user meant.

Important boundaries:

- The orchestrator can answer DIRECT for chat, support, or advice that does not
  require PM data.
- Delegated turns are translated into trigger-phrase-like PM prompts before
  reaching the typed PM workflow.
- Write turns invalidate the orchestrator PM cache.
- A write that was read back is never judged or rewritten by a model; the reply
  is built from the stored records.
- Harness fallback never fabricates write success; a write that could not be
  confirmed asks the user to retry or check the target data directly.
- Orchestrator memory is in-process and resets on backend restart. Durable PM
  state remains in Kairo's stores below.

## Agent Graph (Kairo 2.0, behind `KAIRO_AGENT=graph`)

With `KAIRO_AGENT=graph`, `/orchestrator/stream` runs
[`kairo/agents/graph`](apps/api/kairo/agents/graph) instead of the
orchestrator. Without it, nothing below is used. The graph takes domains over
one at a time: tasks and the calendar so far. Habits, journal, memory and
conversation are handed, whole, to the orchestrator described above.

```mermaid
flowchart TD
    A["User message"] --> ING["ingest<br/>injection check, what is waiting on the user,<br/>recent turns"]
    ING -->|"approve / reject"| APP["approval<br/>no model"]
    ING --> CO["coordinate<br/>one model call, one tool call"]
    CO -->|"a tasks tool"| G["gate<br/>schema, date, time and repeat checks"]
    CO -->|"hand_off to schedule"| AG["agent<br/>the schedule agent: its own prompt,<br/>tools and view of the calendar"]
    CO -->|"yes or no to the approval just asked"| APP
    CO -->|"hand_off to assistant,<br/>or nothing usable"| OLD["assistant<br/>the orchestrator answers and logs the turn"]
    CO -->|"ask_user"| R
    AG -->|"a calendar tool"| G
    AG -->|"answer or ask_user"| R
    AG -->|"hand_back"| OLD
    G -->|"rejected, first time:<br/>back to whoever proposed"| CO
    G -->|"rejected, first time"| AG
    G -->|"proposed commands"| ACT["act<br/>approval policy, executors,<br/>read-back"]
    G -->|"a read, an answer, or a question"| R["respond<br/>reply from receipts;<br/>parks an unanswered question"]
    APP --> R
    ACT --> R
    R --> P["persist<br/>turn memory, decision log with versions"]
```

How it differs from the orchestrated turn:

- **A model proposes; code decides.** `coordinate` and `agent` are the only
  nodes that call a model. Each call picks one tool and fills in typed
  arguments. No model has a tool that writes.
- **One parse.** The arguments are the command. Nothing re-reads text
  afterwards, so there is no trigger phrase and no regex extraction on this
  path.
- **The gate can send a proposal back.** A date or time that contradicts the
  message's own wording ("by friday" proposed as next week's Friday, "2-3pm"
  proposed as 3 to 4), a repeat that does not match ("every Monday, Wednesday
  and Friday" proposed without Friday), a date in the past, an event id that is
  not on the calendar, or arguments that break the schema go back once, with
  the reason, to the model that proposed them. If the second attempt fails too,
  nothing is stored and the user is asked.
- **Same execution path as before.** `act` uses `submit_action`
  (`application/approval_flow.py`), the function the typed workflow uses too:
  approval policy, approval queue, executors, audit events, receipts.
- **A domain says when a request is not its own.** A check can answer "this
  belongs to the calendar" (a repeating reminder is not a task) and the graph
  routes it there. A lookup that finds nothing in the domain's own store is
  sent back once, and then handed on, because the thing may live elsewhere.
- **Model calls per turn:** one for a tasks request, two for a calendar
  request (coordinator, then the schedule agent), none for approve or reject.
- **An approval can be answered in plain words**, but only right after it was
  asked: while the approval prompt is the last thing Kairo said and it is the
  thread's only pending approval, the coordinator may record a yes or no to it.
  At any other time the user types `approve` or `reject`.

### The schedule agent

The coordinator hands a calendar message to the schedule agent
([`kairo/domains/schedule.py`](apps/api/kairo/domains/schedule.py)). The agent
is shown the calendar: the next 14 days day by day, later events, and each
repeating event's rule, every one with its id. It refers to an event by id, so
code never has to find an event from a description.

| Tool | Proposes |
|---|---|
| `create_events` | `schedule_add`; several events of one message in one call |
| `update_event` | `schedule_update`; keeps the event's length when only the start moves |
| `remove_event` | `schedule_remove` |
| `skip_occurrence` | `schedule_add_exception` for one day of a repeating event |
| `change_occurrence` | `schedule_add_override` for one day of a repeating event |
| `end_series` | `schedule_cancel_series_from` |
| `show_events` | nothing: lists a day or a range, read from the calendar |
| `answer`, `ask_user`, `hand_back` | nothing: every agent has these three |

Two things are decided by code, not the model: the first day of a daily or
weekly repeat (the next day it falls on), and whether a new event overlaps
something already there (said in the reply as a note). An `answer` may describe
the calendar but may not say that something was added, changed or removed; only
a receipt can say that.

### Questions

A question the graph asks is stored as a typed `GraphQuestion` (who asked, what
was asked, which request it was about).

```mermaid
stateDiagram-v2
    [*] --> open : an agent, the coordinator or the gate asks
    open --> answered : the next message goes to whoever asked
    open --> parked : the next message is about something else
    parked --> answered : the message after that goes to whoever asked
    parked --> dropped : it does not
    answered --> [*]
    dropped --> [*]
```

- The question is offered to the next message, with the request it was about,
  and the completed request is still checked against the wording of both
  messages ("on saturday", then "3pm").
- If that message is about something else, it is handled as a new request and
  the reply ends with `Still open: …`. A parked question is not something the
  user owes an answer to; it is kept for one more message and then dropped.
- A request gets at most two questions in a row.
- A question the orchestrator left open is closed as soon as the graph handles
  a later message itself.

### The registry

Tools come from a registry (`kairo/domains`). A domain declares its tools as
`ToolSpec`s: a name, strict typed arguments whose docstring is the description
shown to the model, an optional `check`, and either `propose` (arguments to
typed commands) or `read`. `Registry.register` refuses a domain with a missing
part. A domain runs one of three ways:

| Mode | How it runs | Now |
|---|---|---|
| `inline` | its tools are given to the coordinator | tasks |
| `agent` | the coordinator hands off to the domain's own agent: its own prompt, tools and view of its store | schedule |
| `legacy` | the coordinator hands off to the orchestrator; the one fallback | everything else |

Each model's prompt and every tool definition it is shown are hashed into a
bundle version, recorded with each turn in the decision log.

## PM Turn Flow

```mermaid
flowchart TD
    A["User message"] --> B{"Sync run_pm<br/>multi-action lines?"}
    B -->|yes| B2["Run each line through typed workflow<br/>no fallback per line"]
    B -->|no| C["run_typed_pm_turn"]
    B2 --> Z["Joined reply"]

    C --> D{"Prompt-injection pattern?"}
    D -->|yes| R["Dedicated refusal"]
    D -->|no| E["Load active working memory"]

    E --> F{"Pending clarification,<br/>choice, confirmation,<br/>or disambiguation?"}
    F -->|yes| G["Resolve, cancel,<br/>or replace pending state"]
    F -->|no| H["Extract PMPlanExtraction"]
    G --> I{"Reply now?"}
    I -->|yes| Z
    I -->|no| H

    H --> J["Apply context, memory,<br/>and semantic handlers"]
    J --> K{"Route"}
    K -->|approve / reject| L["Resolve approval request"]
    K -->|safe unknown chat| M["Tool-free fallback"]
    K -->|needs info| N["Ask clarification or show choices<br/>and save working memory"]
    K -->|actionable| O["Plan PMAction(s)"]

    O --> P{"Approval required?"}
    P -->|yes| Q["Create approval request"]
    P -->|no| S["Execute typed action"]

    R --> Z
    L --> Z
    M --> Z
    N --> Z
    Q --> Z
    S --> Z

    Z --> T["Persist decision trace<br/>and audit events where applicable"]
```

Notes:

- `run_pm()` supports a newline-based multi-task splitter when every non-empty
  line starts with an action prefix.
- `astream_pm()` handles one message and streams only the fallback/model reply;
  the typed workflow still returns whole deterministic replies.
- Fallback is intentionally tool-free. State-changing requests should be handled
  by the typed workflow before fallback is allowed.

## Extraction Arbitration

Extraction does not have a simple "regex first, model second" order. The current
logic compares optional structured model output with deterministic extraction and
keeps the safer or more complete plan.

```mermaid
flowchart TD
    A["Message"] --> B["Optional model plan extraction<br/>if provider/key/config allow it"]
    A --> C["Deterministic plan extraction<br/>clause splitting + intent/entity rules"]

    B --> D{"Model plan usable?"}
    C --> E{"Deterministic plan has<br/>safer/more specific scope?"}

    D -->|no| F["Use deterministic plan"]
    D -->|yes| G{"Arbitration rules"}
    E --> G

    G -->|activity disclosure / coaching guard| F
    G -->|deterministic resolves missing fields| F
    G -->|deterministic preserves recurrence| F
    G -->|multi-task deterministic plan beats single model task| F
    G -->|model better and passes checks| H["Use or merge model plan"]

    F --> I["Normalize tasks and validate entities"]
    H --> I
    I --> J["PMPlanExtraction"]
```

Single-request extraction follows the same idea: model extraction may run when
configured, but deterministic extraction can override missing, unsafe, or
less-specific model output. Entity validation happens before execution.

## Intent And Policy Boundaries

`classify_pm_intent()` is a deterministic priority ladder for common request
families:

- approval/rejection acknowledgements
- destructive schedule/todo operations
- explicit memory export/remember requests
- sensitive or non-sensitive web-search requests
- todos, journal, habits, list/read requests
- schedule create/update/skip/cancel-series operations
- coaching/general conversation
- bare `add X` todo fallback

Policy decisions are deliberately outside the extractor:

- The extractor identifies user intent and entities.
- The workflow decides whether a pending dialogue state exists.
- The planner converts `PMIntent` into one or more typed `PMAction` objects.
- The approval policy decides whether an action can execute immediately.
- Executors mutate only through typed actions, not raw user text.

## Working Memory

Working memory is short-lived structured dialogue state stored in SQLite. It is
used for clarification, ranked choices, contextual confirmations, and activity
disambiguation.

```mermaid
stateDiagram-v2
    [*] --> active : save_working_memory()

    active --> resolved : user reply completes pending work
    active --> replaced : new pending state or high-confidence new request
    active --> cancelled : cancel / never mind / explicit replacement
    active --> expired : expires_at has passed
    active --> active : reply keeps confirmation or choice alive

    note right of active
        modes:
        awaiting_choice - TTL 30m, stale 10m
        awaiting_clarification - TTL 2h, stale 30m
        awaiting_freeform - TTL 2h, stale 30m
        awaiting_confirmation - TTL 10m, stale 5m
        awaiting_disambiguation - TTL 10m, stale 5m
    end note
```

The workflow clears stale pending state when the next message looks like a new
request rather than a dialogue reply. This prevents unrelated turns from being
silently absorbed into an old clarification or confirmation.

## Planning, Approval, Execution

```mermaid
flowchart TD
    A["PMPlanExtraction"] --> B["plan_pm_actions"]
    B --> C["PMAction list"]
    C --> D["apply_approval_policy"]

    D -->|safe action| E["execute_pm_action"]
    D -->|destructive/private/high risk| F["create_approval_request"]

    F --> G["approval_requests<br/>status=pending"]
    G --> H["approve_from_chat / reject_from_chat<br/>or HTTP approval endpoint"]
    H -->|approved| E
    H -->|rejected| I["No mutation"]

    E --> J["Domain executor"]
    J --> K["Local state / calendar / memory"]
    J --> L["audit_events"]
```

Approval-gated actions include todo deletion, schedule removal/update, recurring
schedule modifications, private exports/patches, and sensitive web-search
requests.

## Learning And Personalization

Ranked clarification choices are generated by
`application/field_completion.py`. Candidate ranking can use:

- explicit preferences in `user_preferences`
- promoted behavioral patterns in `behavioral_patterns`
- prior shown/selected choices in `field_choice_memory`
- semantic activity/default windows
- calendar conflict and repetition penalties

Repeated selections can be promoted:

- `promote_patterns_from_choice_memory()` creates a behavioral pattern after at
  least 4 selected samples, 3 distinct selection days, and 55% selection rate
  within the last 90 days.
- `promote_preference_from_repeat_selects()` creates a broader
  `time_band_engagement` preference after repeated selections in the same scope.
- `decay_behavioral_patterns()` reduces inactive pattern confidence by 10% per
  30-day period and archives patterns below 0.25 confidence.

Explicit sensitive habit statements can also write a category-scoped
`preferred_window` preference when they contain a schedulable activity and a
concrete time.

## Persistence

```mermaid
flowchart LR
    subgraph "Per-session JSON files"
        S["schedule.json"]
        T["todos.json"]
        P["private.json<br/>profile, active_plans, notes_private"]
    end

    subgraph "pm.db SQLite"
        WM["working_memory"]
        AR["approval_requests"]
        AE["audit_events"]
        DL["turn_decision_log<br/>PM + orchestrator turns"]
        UP["user_preferences"]
        FCM["field_choice_memory"]
        BP["behavioral_patterns"]
        SM["semantic_memory"]
        CA["calendar_accounts"]
        CM["calendar_event_mirror"]
    end

    subgraph "Shared files"
        VAULT["vault/PROFILE.md"]
        CKPT["personal-manager/checkpoints.db"]
        OFB["fallback_log.jsonl<br/>orchestrator fallback records"]
    end

    subgraph "Process memory"
        OWM["orchestrator WorkingMemory<br/>recent turns + PM cache"]
    end

    TYPED["Typed workflow"] --> S
    TYPED --> T
    TYPED --> P
    TYPED --> WM
    TYPED --> AR
    TYPED --> AE
    TYPED --> DL
    TYPED --> UP
    TYPED --> FCM
    TYPED --> BP
    TYPED --> SM
    TYPED --> VAULT

    FALLBACK["Tool-free fallback"] --> CKPT
    SYNC_SAVE["Sync fallback<br/>passive fact save"] --> VAULT
    SYNC_SAVE --> P

    ORCH["Orchestrator"] --> OWM
    ORCH --> DL
    ORCH --> OFB

    GCAL["Google Calendar sync"] --> CA
    GCAL --> CM
```

Runtime data lives under `data/` or `apps/api/data/` depending on how the app is
started. Those directories are gitignored and should not be published.

## Memory Routing

```mermaid
flowchart TD
    A["User message"] --> B{"Explicit remember/export?"}
    B -->|yes| C["SAVE_MEMORY intent"]
    B -->|no| D{"Passive self-disclosure<br/>or semantic memory candidate?"}

    D -->|no| E["No memory write"]
    D -->|yes| F{"Sensitive or private?"}
    C --> F

    F -->|yes| G["private_note_append<br/>private.json notes_private"]
    F -->|no| H["remember<br/>vault/PROFILE.md + semantic_memory"]

    G --> I{"Schedulable habit<br/>with concrete time?"}
    I -->|yes| J["user_preferences<br/>category preferred_window"]
    I -->|no| K["Done"]
    H --> K
    J --> K
```

The memory policy is conservative: sensitive facts go to private PM storage,
lower-risk stable preferences can be shared through profile/semantic memory, and
fallback memory writes are confidence-gated.
