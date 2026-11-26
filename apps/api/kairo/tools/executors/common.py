"""Shared executor helpers."""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel


def _plain(command: BaseModel, *, set_fields_only: bool = False) -> dict[str, Any]:
    """`command` as the plain dict the stores and the calendar service take.

    `set_fields_only` is for a patch: a field the command did not set is left
    out, so the store does not change it.
    """
    return command.model_dump(mode="json", by_alias=True, exclude_none=set_fields_only, exclude_unset=set_fields_only)


def _result(message: str, *, ok: Optional[bool] = None) -> dict[str, Any]:
    success = not message.lower().startswith("error:") if ok is None else ok
    return {"ok": success, "message": message}
