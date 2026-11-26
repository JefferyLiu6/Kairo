"""Recognising "approve" and "reject" typed into chat."""
from __future__ import annotations

import re

_APPROVAL_COMMAND = re.compile(r"(approve|reject)(?:\s+[0-9a-f]{8})?", re.I)


def is_approval_command(message: str) -> bool:
    """Whether the whole message is "approve" or "reject", with or without an approval id."""
    return _APPROVAL_COMMAND.fullmatch(message.strip()) is not None


def names_an_approval(message: str) -> bool:
    """Whether an approval command says which approval it means."""
    return " " in message.strip()


def approval_verb(message: str) -> str:
    """"approve" or "reject", for a message that is an approval command."""
    return _APPROVAL_COMMAND.fullmatch(message.strip()).group(1).lower()
