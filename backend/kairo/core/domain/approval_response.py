"""Recognize the workflow's pending-approval response across text boundaries."""
import re


def has_pending_approval(reply: str) -> bool:
    # Require the complete generated control block, including matching IDs.
    return re.search(
        r"Approval required \[([0-9a-f]{8})\]: [^\n]+\n"
        r"Risk: (?:medium|high)\.\n"
        r"Reply `approve \1` to go ahead, or `reject \1` to cancel\.",
        reply,
    ) is not None
