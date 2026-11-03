"""Where eval inputs live, relative to the API root (backend/, later apps/api/)."""
from __future__ import annotations

from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = API_ROOT / "tests" / "fixtures"
