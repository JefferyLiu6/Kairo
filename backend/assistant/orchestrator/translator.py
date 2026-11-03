"""Translate user messages into structured PM agent action objects."""
from __future__ import annotations

import re

from assistant.shared.llm import ContractError, parse_contract

from .contracts import StructuredAction, TranslatorOutput


def unparsed_action() -> StructuredAction:
    """What an unusable translator reply becomes: ask the user instead of acting."""
    return StructuredAction(
        intent="direct",
        timeframe=None,
        entities=[],
        confidence=0.0,
        pm_prompt="",
        is_write=False,
    )


def parse_translator_response(raw: str) -> StructuredAction:
    """Validate a raw translator reply; an invalid one becomes `unparsed_action()`."""
    try:
        return parse_contract(raw, TranslatorOutput).to_action()
    except ContractError:
        return unparsed_action()


def build_retry_prompt(action: StructuredAction, harness_fix: str) -> StructuredAction:
    """Build a corrected action for retry using the harness suggested_fix."""
    return StructuredAction(
        intent=action.intent,
        timeframe=action.timeframe,
        entities=action.entities,
        confidence=action.confidence,
        pm_prompt=harness_fix or action.pm_prompt,
        is_write=action.is_write,
    )


def is_safe_read_retry(action: StructuredAction) -> bool:
    """A retry may only issue a narrow, complete read command for the same resource.

    Deliberately fail closed on novel paraphrases: judge-generated text must not
    expand the authority of a read into mutations or multi-action instructions.
    """
    nouns = {"show_schedule": r"schedule|calendar", "show_todos": r"todos|tasks|todo list|task list",
             "show_habits": r"habits"}
    if action.is_write or action.intent not in nouns:
        return False
    return re.fullmatch(
        r"(?:show|list)(?: me)? (?:my |the |today's |tomorrow's )?(?:" + nouns[action.intent]
        + r")(?: (?:for )?(?:today|tomorrow|this week|next week))?[.!?]?",
        action.pm_prompt.strip(), re.IGNORECASE,
    ) is not None
