# Humanizer grounding fix — September 15, 2026

## Observed problem

The live backend demo successfully created, listed and completed a task, but final replies claimed a morning/early-work preference despite an empty initial profile. The online harness had already checked the PM output before the humanizer added those claims. Task execution success did not establish final-response truthfulness.

## Change and rationale

The humanizer prompt is versioned as `grounded-humanizer-v2`. It removes the concrete morning-preference example and the requirement to add a proactive observation or produce a minimum number of sentences. Personalization is optional and must be explicitly supported by a profile or direct user statement. It must preserve negation and one-off scope, omit disputed preferences and avoid unsupported action claims.

Humanizer input is now JSON containing the current user message, readable PM result, and separate profile/user-message sources. Profile content is loaded from the user's actual profile file. Only actual user-role working-memory entries are supplied as historical personalization evidence. Flattened memory text and earlier assistant claims are excluded from this final rewrite step, preventing an invented preference from becoming evidence merely because it appeared in an earlier assistant reply.

Routing and translation still receive their normal conversation context; this change is specific to final humanization. The existing `_humanize` signature is retained for callers, but its flattened memory argument is no longer used as personalization evidence. This sacrifices some conversational continuity and can omit implicit confirmations such as a bare “yes” to an earlier assistant question; omission is preferable to inventing a preference.

If removing raw JSON leaves no readable PM output, humanization raises an error instead of answering from context alone. The existing orchestrator recovery path handles that error without replaying a write. The change does not add a post-response model judge, an extra model call or an automatic substring-based preference filter.

## Verification

577 backend tests passed, including 10 new grounding-boundary checks. Updated final-workflow tests consume the new JSON payload. Backend lint passed. Tests cover empty profiles, assistant-history contamination, real user/profile evidence, cross-user isolation, negation and one-off wording, unreadable PM output, uncertain results and task text that resembles a preference statement. Mocked response tests verify plumbing and guards, not live semantic reliability.

Four live humanizer-only calls to pinned `gpt-4.1-mini-2025-04-14`, temperature 0, maximum 512 output tokens and no retries produced:

| Context | Actual response |
|---|---|
| Empty profile/history | Added 'prepare end-to-end interview demo'. |
| Earlier assistant invented a morning preference | Your todo list includes: prepare end-to-end interview demo. |
| Real profile says concise replies; user states afternoon preference | Added 'review notes'. |
| User denies morning preference, gives one-off Friday availability | Your todo list includes: review notes. |

Inspection found no unsupported preference or additional execution claim in these outputs. The known-preference case did not need to mention the preference; personalization is optional. This is targeted component verification, not independent accuracy, repeat stability or a rerun of the full backend pipeline. Prompt and source filtering changed together; the experiment does not isolate their individual contributions.

## Before/after and limits

Before: real empty-profile demo replies invented morning preferences and recycled them in later turns. After: the four targeted live outputs accurately reported the PM result without invented personalization, while deterministic tests verified that earlier assistant claims are excluded from input sources. Original demo artifacts remain unchanged.

Remaining limitations: supplied profile content itself may be inaccurate; direct user text may quote others or contain instructions, so semantic interpretation still matters. A model can still hallucinate despite grounding instructions. No claim of complete hallucination prevention is made. The offline v9 response-quality judge remains separate from the online harness and was not automatically inserted after humanization.

Interview explanation: “An end-to-end run showed correct task writes but an invented preference in the final response. I removed a misleading example, made personalization optional, and separated profile/user evidence from prior assistant claims. Tests verify the source boundary, and a small live contrast check verifies the targeted behavior. I kept the original failure and did not confuse execution correctness with response truthfulness.”
