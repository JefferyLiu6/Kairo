# Kairo + Orchestrator Architecture

This document describes the current Kairo orchestrator and controlled workflow
implementation. It is meant to be accurate enough for code review, not a
marketing diagram.

Primary source files:

- [`backend/assistant/orchestrator/agent.py`](backend/assistant/orchestrator/agent.py) - conversational router/translator/harness/humanizer over the PM agent
- [`backend/assistant/orchestrator/router.py`](backend/assistant/orchestrator/router.py) - heuristic plus model routing for direct vs delegated turns
- [`backend/assistant/orchestrator/translator.py`](backend/assistant/orchestrator/translator.py) - user request to structured PM trigger prompt
- [`backend/assistant/orchestrator/harness.py`](backend/assistant/orchestrator/harness.py) - PM result checking, retry, cache fallback, fallback logging
- [`backend/assistant/personal_manager/agent.py`](backend/assistant/personal_manager/agent.py) - sync/streaming entrypoints and tool-free fallback
- [`backend/assistant/personal_manager/workflow.py`](backend/assistant/personal_manager/workflow.py) - typed turn coordinator
- [`backend/assistant/personal_manager/application/extraction.py`](backend/assistant/personal_manager/application/extraction.py) - deterministic/model extraction arbitration
- [`backend/assistant/personal_manager/application/planner.py`](backend/assistant/personal_manager/application/planner.py) - intent-to-action planning
- [`backend/assistant/personal_manager/application/approval_policy.py`](backend/assistant/personal_manager/application/approval_policy.py) - destructive/privacy approval gates
- [`backend/assistant/personal_manager/persistence/`](backend/assistant/personal_manager/persistence) - working memory, approvals, audit, traces, personalization, semantic memory

## System Map

```mermaid
flowchart TB
    UI["React UI<br/>web/src"] <-->|HTTP + SSE| API["FastAPI<br/>assistant/http/pm_app.py"]

    API -->|/orchestrator/stream| ORCH["Orchestrator<br/>assistant/orchestrator"]
    API -->|/personal-manager/stream| PM["Kairo PM workflow<br/>agent.py"]
    ORCH -->|delegates via astream_pm| PM
    ORCH -->|direct reply| OLLM["Orchestrator LLM<br/>chat, routing, humanizing"]
    ORCH --> OMEM["In-process orchestrator memory<br/>recent turns + PM cache"]
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
    G -->|no| H["Call PM agent with pm_prompt<br/>astream_pm"]

    H --> I["Harness precheck / model judge"]
    I -->|pass| J["Humanize PM output"]
    I -->|retry| K["Retry with suggested PM prompt<br/>max 2 retries"]
    K --> H
    I -->|fallback| L["Safe fallback reply<br/>read cache if available"]

    J --> M["Update in-process memory<br/>and log orchestrator turn"]
    L --> M
    E --> M
    M --> N["SSE token/progress reply"]
```

Important boundaries:

- The orchestrator can answer DIRECT for chat, support, or advice that does not
  require PM data.
- Delegated turns are translated into trigger-phrase-like PM prompts before
  reaching the typed PM workflow.
- Write turns invalidate the orchestrator PM cache.
- Harness fallback never fabricates write success; failed writes ask the user to
  retry or check the target data directly.
- Orchestrator memory is in-process and resets on backend restart. Durable PM
  state remains in Kairo's stores below.

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

Runtime data lives under `data/` or `backend/data/` depending on how the app is
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
