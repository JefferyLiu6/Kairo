"""
LLM gateway — typed JSON contracts for every structured model call.

A stage sends its input as JSON (a Pydantic model) and gets back a validated
Pydantic object, or a ContractError saying exactly what was wrong. There is one
way to find JSON in a reply (`parse_json_object`) and one way to validate it
(`parse_contract`), used by production calls and by the eval replays alike.

`call_contract` makes the call through an injected `invoke(system, user) -> str`,
so each caller keeps its own transport (model choice, telemetry, test stubs).
On an invalid reply it asks the model once to correct itself, quoting the
validation error, before giving up.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Any, Callable, TypeVar

from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

T = TypeVar("T")

Invoke = Callable[[str, str], str]

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
_decoder = json.JSONDecoder()


class Contract(BaseModel):
    """Base for model-facing schemas: unknown fields are an error, not ignored."""

    model_config = ConfigDict(extra="forbid")


class ContractError(ValueError):
    """A model reply was not valid JSON for the expected contract."""


def parse_json_object(text: str) -> dict[str, Any]:
    """Return the first JSON object in `text`, ignoring code fences and prose around it.

    Decodes from the first "{" with a real JSON parser, so trailing prose or a
    second object after it is ignored instead of breaking the match.
    """
    cleaned = _FENCE_RE.sub("", text.strip())
    for start in (i for i, ch in enumerate(cleaned) if ch == "{"):
        try:
            value, _ = _decoder.raw_decode(cleaned, start)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ContractError("reply contains no JSON object")


@lru_cache(maxsize=None)
def _adapter(schema: Any) -> TypeAdapter:
    return TypeAdapter(schema)


def parse_contract(text: str, schema: type[T]) -> T:
    """Find the JSON object in a model reply and validate it as `schema`."""
    payload = parse_json_object(text)
    try:
        return _adapter(schema).validate_python(payload)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or 'reply'}: {err['msg']}" for err in exc.errors()
        )
        raise ContractError(problems) from exc


def to_payload(data: Any) -> str:
    """Serialise a stage input as the JSON the model receives."""
    if isinstance(data, BaseModel):
        return data.model_dump_json()
    return _adapter(type(data)).dump_json(data).decode()


def call_contract(
    invoke: Invoke,
    *,
    system: str,
    data: Any,
    output: type[T],
    repairs: int = 1,
) -> T:
    """Send `data` as JSON, validate the reply as `output`, retry once with the error."""
    user = to_payload(data)
    raw = invoke(system, user)
    for attempt in range(repairs + 1):
        try:
            return parse_contract(raw, output)
        except ContractError as exc:
            if attempt == repairs:
                raise
            raw = invoke(system, _repair_message(user, raw, exc))
    raise AssertionError("unreachable")


def _repair_message(user: str, raw: str, error: ContractError) -> str:
    return (
        f"{user}\n\n"
        f"Your previous reply was not valid: {error}\n"
        f"Previous reply: {raw[:500]}\n"
        "Reply again with only the corrected JSON object."
    )
