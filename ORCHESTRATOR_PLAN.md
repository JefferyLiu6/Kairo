# Orchestrator Agent — Architecture Plan

## Goal

Build a smart orchestrator agent that wraps the existing PM agent. The orchestrator handles the full user relationship: conversation context, long-term memory, reasoning, and quality assurance. The PM agent stays a fast, stateless tool executor.

---

## System Overview

```
User
 │
 ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     ORCHESTRATOR AGENT                              │
│                   (gpt-4o / Claude Sonnet)                          │
│                                                                     │
│  Reads:  working memory + long-term memory + user profile           │
│  Decides: answer directly OR call PM agent                          │
│  Writes: memory after every turn                                    │
└──────────────┬──────────────────────────────────────────────────────┘
               │ structured trigger prompt
               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    PERSONAL MANAGER AGENT                           │
│              (gpt-4o-mini — fast, stateless, unchanged)             │
│                                                                     │
│  schedule / todos / habits / journal actions only                   │
└──────────────┬──────────────────────────────────────────────────────┘
               │ raw result
               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     QUALITY HARNESS                                 │
│                   (gpt-4o-mini — cheap judge)                       │
│                                                                     │
│  PASS → orchestrator humanizes → reply                              │
│  RETRY → rephrase prompt → call PM again (max 2x)                  │
│  FALLBACK → orchestrator answers from memory + flags issue          │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Component Responsibilities

### 1. Orchestrator Agent

**Role:** The user's primary conversational partner. Owns the relationship.

**Responsibilities:**
- Read working memory and long-term memory at the start of every turn
- Reason about user intent, emotional tone, and unstated needs
- Decide whether to call the PM agent or reply directly
- Translate vague user language into a clean, TRIGGER_PHRASES.md-grounded prompt before calling PM agent
- Receive PM agent result, pass to quality harness
- Humanize the final output — add context, empathy, proactive observations
- Write new facts to long-term memory after every turn

**Direct reply (no PM call) when:**
- Chitchat, venting, emotional support
- Answer already in working memory
- Clarifying question needed before action
- General coaching / reflection with no data needed

**Call PM agent when:**
- Any stateful action: add / update / delete schedule, todo, habit, journal
- Any data read: show schedule, list tasks, check habits
- Anything matching an intent in TRIGGER_PHRASES.md

**Model:** `gpt-4o` (or `claude-sonnet-4-6`) — strong reasoning and long context for memory

---

### 2. Kairo Agent

**Role:** Fast, deterministic tool executor. Stateless. Unchanged from current implementation.

**Responsibilities:**
- Execute one specific action per call
- Read/write schedule, todos, habits, journal
- Return structured result

**Does NOT:**
- Write to user profile or semantic memory (moved to orchestrator)
- Reason about user preferences or tone
- Manage conversation context

**Model:** `gpt-4o-mini` — cheap and fast, enough for structured actions

---

### 3. Quality Harness

**Role:** Judge between PM agent output and what the user actually needs.

**Checks:**

| Criterion | Description |
|---|---|
| **Relevance** | Does the output answer what the user asked? |
| **Completeness** | Is it empty when it shouldn't be? |
| **Alignment** | Does it match user preferences from profile? |
| **Format** | Is it actionable and human-readable? |

**Verdict schema:**
```json
{
  "verdict": "pass | retry | fallback",
  "confidence": 0.0–1.0,
  "reason": "why this verdict was chosen",
  "suggested_fix": "corrected trigger prompt for retry",
  "failure_type": "read_failed | write_failed | irrelevant | empty | null"
}
```

**Retry logic:** max 2 retries before fallback. Each retry uses the harness's `suggested_fix` as the new PM agent input.

**Model:** `gpt-4o-mini` — small structured call, adds ~200ms, minimal cost

---

### Fallback Behavior (precise rules)

Fallback triggers after 2 failed retries, or immediately if the harness detects a non-retryable failure (e.g. PM agent threw an exception).

**Rule 1 — Never fabricate state-changing success.**
If the PM agent was executing a write (add / update / delete) and fallback triggers, the orchestrator MUST NOT say "Done!" or imply the action succeeded. The action state is unknown.

**Rule 2 — Distinguish read failure from write failure.**

| Failure type | What happened | User-facing message |
|---|---|---|
| `read_failed` | Tried to fetch data, got empty or irrelevant result | "I wasn't able to pull that up right now — here's what I have from last time: {cached_snapshot}" |
| `write_failed` | Tried to add/update/delete, could not confirm result | "I couldn't confirm that was saved. Please try again or check your {schedule/tasks}." |

**Rule 3 — For write failures, never guess the outcome.**
Do not say "it might have worked" or "it should be saved." Say explicitly that the action could not be confirmed. Suggest the user verify manually.

**Rule 4 — For read failures, use cached snapshot if available.**
If the PM snapshot cache holds a recent result (within TTL), surface it with a freshness caveat:
`"Here's what I had as of a moment ago — it may not reflect the latest changes."`
If no cache, say so plainly without fabricating content.

**Rule 5 — Log every fallback.**
On every fallback, write a structured entry to the session's fallback log:

```python
{
  "timestamp": "ISO8601",
  "session_id": "...",
  "original_user_message": "...",
  "translated_prompt": "...",      # what was sent to PM agent
  "pm_output": "...",              # raw PM agent response
  "harness_verdict": { ... },      # full verdict object
  "failure_type": "read_failed | write_failed | ...",
  "retry_count": 2,
  "fallback_reply": "..."          # what was said to the user
}
```

Log destination: `data/personal-manager/{session_id}/fallback_log.jsonl` — one JSON object per line, append-only. Used for later inspection and improving trigger translations.

**Rule 6 — Never surface internal error text to the user.**
PM agent errors (`"Error: unsupported PM action"`, stack traces, raw exception messages) are logged but never shown. The user sees only the clean fallback message from Rule 2.

---

## Memory Architecture

### Ownership split

| Store | Owner | Content |
|---|---|---|
| `PROFILE.md` | **Orchestrator** | Preferences, facts, style ("prefers morning meetings") |
| `semantic_memory DB` | **Orchestrator** | Structured facts with polarity and confidence |
| `session working memory` | **Orchestrator** | Current conversation turns, open threads, pending context |
| `session summaries` | **Orchestrator** | Compressed history from past sessions (future) |
| `schedule.json` | PM Agent | Calendar events |
| `todos.json` | PM Agent | Task list |
| `habits DB` | PM Agent | Streak tracking |
| `journal DB` | PM Agent | Log entries |

### Memory layers

```
ALWAYS LOAD (every turn — small, cheap, always relevant)
├── PROFILE.md              ~20–50 facts, fits in ~500 tokens
└── Session working memory  last N turns, open threads, pending context

LAZY LOAD (only when router predicts PM state is relevant)
├── This week's schedule    loaded if message is schedule/time-related
├── Active todos            loaded if message is task-related
└── Active habits           loaded if message is habit/streak-related
```

**Router prediction signals for lazy load:**
- Keywords: schedule, calendar, meeting, appointment, todo, task, habit, streak, today, tomorrow, this week
- Working memory contains an open thread about PM data
- Previous turn used the PM agent

**PM snapshot cache (per session, in-process):**
- After any PM agent read, store the result in a session-scoped dict with a TTL of 60 seconds
- Subsequent turns within the TTL read from cache instead of calling PM agent again
- Cache is invalidated immediately on any PM write (add / update / delete)
- Keeps casual follow-up turns ("what about Thursday specifically?") fast and cheap

```python
# sketch
pm_cache = {
  "schedule": { "data": [...], "expires_at": time() + 60 },
  "todos":    { "data": [...], "expires_at": time() + 60 },
}
```

> RAG is deliberately excluded at this stage. Revisit if journal entries or session summaries grow beyond ~200 items.

### Memory write flow (orchestrator, after every turn)

```
user message + PM result
        │
        ▼
  extract new facts
  (preferences, disclosures, corrections)
        │
        ▼
  write to PROFILE.md  +  semantic_memory DB
        │
        ▼
  update session working memory
```

### Migration from PM agent

The following PM agent functions move to the orchestrator layer:
- `append_profile_fact()` — currently called in `execute_memory_action`
- `save_semantic_memory_candidates()` — currently called in `execute_memory_action`
- `analyze_activity_disclosures()` — currently called in `workflow.py`

The PM agent's `execute_memory_action` is removed entirely. The `SAVE_MEMORY` intent no longer routes through the PM agent — the orchestrator intercepts it before the PM call.

---

## Translation Layer

Before calling the PM agent, the orchestrator translates the user's natural language into a **structured action object**. The `pm_prompt` field is what gets sent to the PM agent; the rest exists for retries, analytics, harness context, and testing.

### Structured action schema

```json
{
  "intent": "show_schedule | add_todo | add_event | update_event | delete_event | add_habit | show_todos | show_habits | journal_append | ...",
  "timeframe": "today | tomorrow | this_week | next_week | specific_date | null",
  "entities": ["Sarah", "standup", "dentist"],
  "confidence": 0.0–1.0,
  "pm_prompt": "show my schedule for next week",
  "is_write": true | false
}
```

**Field definitions:**

| Field | Purpose |
|---|---|
| `intent` | Canonical action name — maps 1:1 to TRIGGER_PHRASES.md intents. Used by harness to validate the PM result makes sense for the intent. |
| `timeframe` | Extracted time scope — used by retry logic to re-anchor the prompt if PM returns empty. |
| `entities` | Named people, events, or items extracted from the user message or working memory. Used to detect referent resolution ("that meeting" → "standup"). |
| `confidence` | How certain the translator is. Below 0.7 → orchestrator asks clarifying question instead of calling PM. |
| `pm_prompt` | The clean trigger phrase sent to PM agent, grounded in TRIGGER_PHRASES.md. |
| `is_write` | True for add/update/delete. Drives fallback Rule 1 and Rule 2 — write failures get stricter messaging than read failures. |

### Examples

| User says | Structured action |
|---|---|
| "I'm swamped next week, anything I can clear?" | `{ "intent": "show_schedule", "timeframe": "next_week", "entities": [], "confidence": 0.91, "pm_prompt": "show my schedule for next week", "is_write": false }` |
| "remind me about that thing with Sarah" | `{ "intent": "add_todo", "timeframe": null, "entities": ["Sarah"], "confidence": 0.85, "pm_prompt": "add task: follow up with Sarah", "is_write": true }` |
| "ugh I keep forgetting to exercise" | `{ "intent": "add_habit", "timeframe": null, "entities": ["exercise"], "confidence": 0.78, "pm_prompt": "add a habit: exercise 30 minutes daily", "is_write": true }` |
| "move that meeting to after lunch" | `{ "intent": "update_event", "timeframe": "today", "entities": ["meeting"], "confidence": 0.82, "pm_prompt": "reschedule the 10am meeting to 1pm", "is_write": true }` |
| "what have I got on?" | `{ "intent": "show_schedule", "timeframe": "today", "entities": [], "confidence": 0.95, "pm_prompt": "show my schedule for today", "is_write": false }` |

### How downstream components use the structured action

**Harness** — uses `intent` to validate that the PM result type matches what was asked. A `show_schedule` intent that returns "Todo list: (empty)" is an instant `retry`.

**Retry** — uses `timeframe` and `entities` to construct a better `pm_prompt` without re-running the full translation step. Cheaper and faster than asking the orchestrator to re-translate from scratch.

**Fallback log** — `is_write` determines which Rule (read vs write) applies to the fallback message. The full structured action is written to `fallback_log.jsonl` alongside the PM output and harness verdict.

**Analytics** — `intent` + `confidence` + `is_write` aggregated over time shows which intents fail most, which trigger phrases need improvement, and where the translator is uncertain.

**Tests** — each example in TRIGGER_PHRASES.md becomes a unit test: input = user message, expected output = structured action with known `intent`, `timeframe`, `entities`, and `pm_prompt`.

The orchestrator's system prompt includes the full `TRIGGER_PHRASES.md` as reference so the translator knows exactly which `pm_prompt` phrasings the PM agent handles reliably.

---

## Data Flow — Full Turn

```
1. User sends message

2. Orchestrator always loads:
   - PROFILE.md
   - Last N session turns (working memory)

3. Router predicts relevance:
   - PM state relevant? → lazy-load from cache or PM agent read
   - PM state not relevant? → skip, saves latency + tokens

4. Orchestrator reasons:
   - What does the user want?
   - Is there an emotional subtext?
   - Do I need PM agent for an action?

5a. NO → reply directly with profile + working memory as context

5b. YES →
   - Translate to trigger phrase (TRIGGER_PHRASES.md grounded)
   - Call PM agent
   - Cache result (60s TTL, invalidated on write)
   - Pass result to quality harness
     - PASS     → humanize + add context from memory → reply
     - RETRY    → rephrase trigger → call PM again (max 2x)
     - FALLBACK → answer from memory + note the issue

6. Extract new facts from turn → write to PROFILE.md + semantic DB
7. Update session working memory
```

---

## API Design

### New endpoint: `/orchestrator/stream`

Replaces `/personal-manager/stream` as the primary frontend endpoint.

```
POST /orchestrator/stream
{
  "message": "what do I need to do this week?",
  "sessionId": "demo",
  "provider": "openai",
  "model": "gpt-4o"
}

→ SSE stream
  { "type": "status", "data": "Thinking..." }
  { "type": "status", "data": "Checking your schedule..." }
  { "type": "token",  "data": "You have a busy week —" }
  ...
  { "type": "done",   "data": "" }
```

The frontend needs no other changes — swap the endpoint URL and the rest works as-is.

---

## Model Assignment

| Component | Provider | Model | Reason |
|---|---|---|---|
| Orchestrator | OpenAI | `gpt-4o` | Strong reasoning, 128k context for memory |
| PM Agent | OpenAI | `gpt-4o-mini` | Fast, cheap, enough for structured actions |
| Quality Harness | OpenAI | `gpt-4o-mini` | Small structured call, minimal cost |
| Profile reconciliation | OpenAI | `gpt-4o` | Background, quality matters |

All use the existing `build_llm()` factory. Requires `OPENAI_API_KEY` in `.env`.

---

## File Structure (new files only)

```
backend/
└── assistant/
    └── orchestrator/
        ├── __init__.py
        ├── agent.py            # main orchestrator run loop + astream
        ├── memory.py           # load/write profile + semantic memory
        ├── router.py           # decide: direct reply vs PM agent call
        ├── translator.py       # user message → trigger phrase
        ├── harness.py          # quality judge loop
        └── prompts.py          # system prompts (includes TRIGGER_PHRASES.md)
```

---

## Build Order

1. **`memory.py`** — load working memory + profile into context dict
2. **`translator.py`** — translate user message to trigger phrase using TRIGGER_PHRASES.md
3. **`harness.py`** — quality judge: verdict schema + retry loop
4. **`router.py`** — direct vs PM decision logic
5. **`agent.py`** — wire all above into a streaming run loop
6. **`prompts.py`** — orchestrator system prompt with memory + trigger reference
7. **HTTP endpoint** — `/orchestrator/stream` in `pm_app.py`
8. **Frontend** — swap endpoint URL in `api.ts`
9. **Migration** — move `SAVE_MEMORY` / profile writes out of PM agent

---

## Success Criteria

- [ ] "what do I need to do this week?" returns schedule, not "Todo list: (empty)"
- [ ] Vague phrasing ("that thing with Sarah") resolves via working memory before PM call
- [ ] User preferences from PROFILE.md appear in orchestrator replies
- [ ] Quality harness catches and retries bad PM outputs automatically
- [ ] No regression in existing PM agent actions (all 97 tests still pass)
- [ ] Frontend works identically — only the endpoint URL changes
