"""Calendar dates follow the requesting browser, not the server's timezone."""
from contextvars import ContextVar
from datetime import date, datetime
import os
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


_request_zone: ContextVar[ZoneInfo | None] = ContextVar("calendar_timezone", default=None)


def calendar_zone() -> ZoneInfo:
    zone = _request_zone.get()
    if zone is not None:
        return zone
    name = os.environ.get("KAIRO_TIMEZONE") or os.environ.get("GOOGLE_CALENDAR_TIMEZONE") or "America/Toronto"
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("America/Toronto")


def local_now() -> datetime:
    return datetime.now(calendar_zone())


def local_today() -> date:
    return local_now().date()


class CalendarTimezoneMiddleware:
    """Keep timezone context isolated for the full request, including SSE."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        name = dict(scope.get("headers", [])).get(b"x-timezone", b"").decode("latin-1")
        try:
            zone = ZoneInfo(name) if name else None
        except (ZoneInfoNotFoundError, ValueError):
            zone = None
        token = _request_zone.set(zone)
        try:
            await self.app(scope, receive, send)
        finally:
            _request_zone.reset(token)
