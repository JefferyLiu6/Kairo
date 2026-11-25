# Offline eval suite and hardening results (the previous workflow)

Moved from the top-level README. These cover the earlier keyword-driven
workflow, not the agent graph.

## Offline suite

The 100% figures below are scoped to the offline eval suite and its explicit
checks. They cover the earlier keyword-driven workflow, which runs without a
model; they are not claims of general natural-language coverage or production
security. The blind eval above shows how that path does on messages it was not
written for: 30% right as web chat.

| Metric | Result |
| ------ | ------ |
| Unit tests (whole repo) | **1555 passing**, 0 xfail |
| Adversarial tests | **68 passing** |
| Current offline eval cases | **252/252 (100%)** |
| Current offline eval checks | **1518/1518 (100%)** |
| Intent accuracy on eval suite | 100.0% |
| Entity exact-match / F1 on eval suite | 100.0% / 100.0% |
| Action correctness on eval suite | 100.0% |
| Mutation correctness on eval suite | 100.0% |
| Approval precision / recall on eval suite | 100.0% / 100.0% |
| Unsafe-action block rate on eval suite | 100.0% |
| Clarification rate on eval suite | 100.0% |

Current report: [`eval-report.md`](../../eval-report.md).

## Methodology

The offline suite is a deterministic harness over
[`apps/api/tests/fixtures/pm_eval_cases.json`](../../apps/api/tests/fixtures/pm_eval_cases.json).
Each case runs extraction and, when requested, the typed workflow against seeded
local state. It is designed to run in CI without live model credentials.

| Suite | What it validates |
| ----- | ----------------- |
| `core` | Representative end-to-end tasks: todos, schedule creation, deletion approval, private export, sensitive web search, update, journal, memory |
| `intent_classification` | Deterministic priority ladder: approve/reject, todo, list, habit, journal, memory, web search, recurrence, skip/modify/cancel-series |
| `entity_extraction` | Titles, dates, times, due dates, recurrence hints, update targets, journal bodies, memory facts |
| `multi_turn_clarification` | Incomplete requests ask for missing target/date/time instead of mutating state |
| `approval_safety` | Destructive and high-risk actions create approval requests before execution |
| `adversarial` | Prompt injection, unsafe/sensitive web search, over-broad deletes, unsafe approval shortcuts |
| `adversarial_hardening` | Pending-state interruption, context collision, recommendation diversity, memory overreach |
| `ambiguous_nlp` | Messy phrasing and near-neighbor cases where rules should not overfire |
| `calendar_sync` | Google Calendar mirror/write behavior and schedule target resolution around synced events |
| `memory_recall` | Long-term memory and preferences feeding later clarification and scheduling choices |
| `regression` | Previously fixed edge cases kept as permanent guardrails |

Each check validates one of these contracts:

- extracted intent matches expected `PMIntent`
- required entities are present and normalized
- confidence falls inside expected bounds
- planned action type matches the expected mutation path
- approval-required actions create approval records instead of executing directly
- safe actions mutate only intended local state
- clarification cases ask instead of guessing
- unsafe web-search/private-export cases route through safety policy

What this eval does not prove:

- It does not grade final prose quality with an LLM judge.
- It does not exhaustively test live fallback behavior because CI runs without model keys.
- Orchestrator control flow is covered with stubs for routing, translation confidence, retry, fallback, and write-failure safety, but live model routing/translation/humanization quality is not graded offline.
- It does not prove production security, multi-tenant isolation, or encrypted storage.
- It does not cover every natural-language paraphrase.

Failure workflow:

Every failure is reduced to a minimal fixture case, fixed in the relevant layer,
then paired with a near-neighbor regression test proving the fix does not break a
legitimate variant. The hardening cases stay in `adversarial_hardening` after they pass.

## Hardening Results

Before the hardening pass on the earlier workflow, the offline harness exposed
approval-safety and adversarial failures. After patching, that suite is green.
The test count is from that time; the repo has 1555 tests now.

| Suite | Before | After |
| ----- | ------ | ----- |
| Unit tests | 182 passed, 14 xfail | **280 passed, 0 xfail** |
| Eval adversarial suite | 13/16 (81%) | **16/16 (100%)** |
| Eval approval safety | 19/25 (76%) | **25/25 (100%)** |
| Eval overall | 203/216 (94%) | **252/252 (100%)** |

Current report: [`eval-report.md`](../../eval-report.md). Regression coverage:
[`apps/api/tests/personal_manager/test_pm_adversarial.py`](../../apps/api/tests/personal_manager/test_pm_adversarial.py).

## Headless walkthrough

```bash
cd apps/api
uv run python scripts/demo_walkthrough.py
```

The walkthrough drives nine real turns through the previous deterministic
workflow, with no model: create, clarify, approve, learn, block sensitive
search, and refuse injection-like input. The prompts above run on the agent
graph in the UI.

## Judge evaluation and operational monitoring

The response-quality benchmark, runtime judge safeguards, and content-free telemetry are
documented in [docs/archive/EVAL_MONITORING.md](EVAL_MONITORING.md). Run `make eval-judge`
for the credential-free replay contract suite. Replay agreement is not live-model accuracy.
Azure Container Apps monitoring queries are in [docs/archive/monitoring.kql](monitoring.kql).
