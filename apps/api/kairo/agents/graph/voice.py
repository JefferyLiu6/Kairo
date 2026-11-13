"""
The voice pass: how a reply is worded, never what it says.

`respond` builds the reply from what was read back from storage. That draft is
correct and reads like a record. A small model rewords it so it sounds like an
assistant, and `changed_the_facts` compares the result with the draft before
anyone sees it. A rewording that fails the comparison is thrown away and the
draft is sent as written, so this pass can cost a reply its tone but not its
truth.

Some replies are never reworded (`sent_as_written`): the ones whose exact words
are the point. And the rewording is only for the user: the history the models
read next turn keeps the draft (`persist`), so the wording of a reply cannot
change a later decision.
"""
from __future__ import annotations

import json
import os
import re
from typing import Optional, Sequence

from kairo.core.domain.receipt import Receipt

VOICE_TIMEOUT = 10     # seconds. A slow rewording is dropped: the draft is already a complete reply.

_NUMBER = re.compile(r"\d+")
_HALF_OF_DAY = re.compile(r"\d\s*([ap])\.?m\b", re.I)
_WORD = re.compile(r"[^\W\d_]+(?:'[^\W\d_]+)*")
_FIRST_PERSON = {"i", "me", "my", "mine", "myself", "i'm", "i've", "i'll", "i'd"}    # "I sleep in" may become "you sleep in"
_DAY = re.compile(r"\b(mon|tue|wed|thu|fri|sat|sun)(?:r|day|sday|nesday|rsday|urday)?s?\b", re.I)
# May is left out: it is also a verb, and "you may" would read as a month.
_MONTH = re.compile(r"\b(jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)"
                    r"(?:uary|ruary|ch|il|e|y|ust|t|tember|ober|ember)?\b", re.I)
_RELATIVE = re.compile(r"\b(today|tonight|tomorrow|yesterday|this week|next week|this month|next month)\b", re.I)
_VERBATIM = re.compile(r"“([^”]+)”|\"([^\"]+)\"|`([^`]+)`|\[([^\]]+)\]")
_LIST_LINE = re.compile(r"^\s*(?:[-•*]|\d+[.)])\s+(.+?)\s*$", re.M)
# Saying or promising that something changed. Without a receipt, a reply may only do that if its draft does.
_SAYS_IT_CHANGED = re.compile(
    r"\b(i['’]ve|i have|i['’]ll|i will|i['’]m|i am|let me)\s+(now\s+|just\s+|already\s+|gone ahead and\s+|go ahead and\s+)?"
    r"(add|schedul|creat|book|mov|updat|chang|cancel|remov|delet|set|sav|block|reschedul|logg|mark)\w*"
    r"|\b(has|have) (now )?been (added|scheduled|created|booked|moved|updated|changed|cancell?ed|removed|deleted|saved"
    r"|rescheduled|logged|marked)\b"
    r"|^(added|scheduled|booked|moved|updated|cancell?ed|removed|deleted|saved|logged|done)\b", re.I | re.M)


def voice_enabled() -> bool:
    """Whether replies are reworded. They are unless KAIRO_VOICE=off asks for the drafts as written."""
    return os.environ.get("KAIRO_VOICE", "").strip().lower() not in ("off", "0", "false", "no")


def voice_input(message: str, draft: str) -> str:
    """What the voice model is shown: the user's message, for tone, and the draft to reword."""
    return json.dumps({"user_message": message, "draft": draft}, ensure_ascii=False)


def sent_as_written(receipts: Sequence[Receipt], approvals: Sequence[str]) -> Optional[str]:
    """Why this reply is not reworded at all, or None if it may be."""
    if approvals:
        return "approval prompt"      # the web app draws its card from this text, and the next turn finds the id in it
    if any(receipt.status != "verified" for receipt in receipts):
        return "unconfirmed write"    # a failure or a mismatch is reported in the words the check produced
    return None


def _numbers(text: str) -> set[str]:
    return {number.lstrip("0") or "0" for number in _NUMBER.findall(text)}


def _named(pattern: re.Pattern[str], text: str) -> set[str]:
    return {match.group(1).casefold() for match in pattern.finditer(text)}


def _words(text: str) -> set[str]:
    """The words of `text`, however a weekday or a month in it is spelled."""
    words = set()
    for word in _WORD.findall(text.casefold().replace("’", "'")):
        named = _DAY.fullmatch(word) or _MONTH.fullmatch(word)
        words.add(named.group(1) if named else word)
    return words - _FIRST_PERSON


def _labels(receipts: Sequence[Receipt]) -> set[str]:
    """What each stored item that changed is called."""
    items = [change.after or change.before or {} for receipt in receipts for change in receipt.changes]
    labels = {str(item.get("title") or item.get("name") or item.get("text") or "").strip() for item in items}
    return labels - {""}


def changed_the_facts(draft: str, reworded: str, receipts: Sequence[Receipt] = ()) -> Optional[str]:
    """The first way `reworded` says something other than `draft`, or None if only the wording changed.

    These compare text, and are strict on purpose: a rewording that spells a number out or
    renames a list item is refused although it may be right. What they cannot see is a name
    changed outside a list, a quote or a receipt, or a sentence turned around with every fact
    still in it.
    """
    if not reworded.strip():
        return "empty"
    if len(reworded) > 2 * len(draft) + 80:
        return "much longer than the draft"
    old, new = draft.casefold(), reworded.casefold()
    if _numbers(draft) != _numbers(reworded) or _named(_HALF_OF_DAY, draft) != _named(_HALF_OF_DAY, reworded):
        return "numbers differ"
    if _named(_DAY, draft) != _named(_DAY, reworded) or _named(_MONTH, draft) != _named(_MONTH, reworded):
        return "days or months differ"
    if _named(_RELATIVE, draft) != _named(_RELATIVE, reworded):
        return "relative days differ"             # the voice model is not shown today's date, so it may not add one
    # Quoted text survives letter for letter. A list item and a stored name survive word for word.
    quoted = [next(group for group in match.groups() if group) for match in _VERBATIM.finditer(draft)]
    named = _LIST_LINE.findall(draft) + [label for label in _labels(receipts) if label.casefold() in old]
    said = _words(reworded)
    missing = next((text for text in quoted if text.casefold() not in new), None)
    missing = missing or next((text for text in named if not _words(text) <= said), None)
    if missing is not None:
        return f"lost {missing[:60]!r}"
    if ("?" in draft) != ("?" in reworded):
        return "adds a question" if "?" in reworded else "drops the question"
    if not receipts and _SAYS_IT_CHANGED.search(reworded) and not _SAYS_IT_CHANGED.search(draft):
        return "claims a change"                  # nothing was stored this turn, and the draft does not say so either
    return None
