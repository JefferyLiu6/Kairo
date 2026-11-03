"""Shared executor helpers."""
from __future__ import annotations

from typing import Any, Optional


def _result(message: str, *, ok: Optional[bool] = None) -> dict[str, Any]:
    success = not message.lower().startswith("error:") if ok is None else ok
    return {"ok": success, "message": message}
