"""Chat turns that run once, however many connections ask for them.

A turn sent with an idempotency key runs as a task of its own, not inside the HTTP response, so a
dropped connection does not cut it short. The response, and any retry that arrives while the turn
is still running, follows that task: it gets every event from the start, then each new one as it
comes. When the turn ends, its reply is stored under the key (kairo.storage.idempotency) and later
retries are answered from there.

The registry of running turns lives in this process. Kairo runs as one server process (its stores
are SQLite files), so a key the store says is running but that is not here was cut off by a restart.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncIterator, Callable, Optional

_log = logging.getLogger(__name__)

DONE = "data: [DONE]\n\n"
INTERRUPTED_REPLY = (
    "This message was interrupted before Kairo finished it, so some of it may not have been done. "
    "Check your calendar and tasks before sending it again."
)


def sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def replay(reply: str) -> AsyncIterator[str]:
    """The stream a finished turn is answered with: its whole reply at once."""
    async def lines() -> AsyncIterator[str]:
        if reply:
            yield sse({"token": reply})
        yield DONE
    return lines()


class TurnRun:
    """One running turn: the stream lines it has produced so far, and whether it has ended."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.finished = False
        self._changed = asyncio.Event()

    def _push(self, line: str) -> None:
        self.lines.append(line)
        self._wake()

    def _close(self) -> None:
        self.finished = True
        self._wake()

    def _wake(self) -> None:
        changed, self._changed = self._changed, asyncio.Event()
        changed.set()

    async def follow(self) -> AsyncIterator[str]:
        """Every line from the start, then each new one, until the turn ends. Leaving early stops
        only this follower, never the turn."""
        sent = 0
        while True:
            changed = self._changed          # taken before reading, so no line can slip past unseen
            while sent < len(self.lines):
                yield self.lines[sent]
                sent += 1
            if self.finished:
                return
            await changed.wait()


_running: dict[tuple[str, str], TurnRun] = {}
_tasks: set[asyncio.Task] = set()


def running(user_id: str, key: str) -> Optional[TurnRun]:
    return _running.get((user_id, key))


def start(
    user_id: str,
    key: str,
    events: AsyncIterator[tuple[str, str]],
    on_finish: Callable[[str], None],
) -> TurnRun:
    """Run a turn in the background. `events` gives (stream line, reply text it adds) pairs;
    `on_finish` gets the whole reply once the turn has ended, however it ended."""
    run = TurnRun()
    _running[(user_id, key)] = run

    async def drive() -> None:
        reply: list[str] = []
        try:
            async for line, text in events:
                run._push(line)
                reply.append(text)
        except asyncio.CancelledError:          # the server is shutting down mid-turn
            reply = [INTERRUPTED_REPLY]
            raise
        except Exception:
            # Part of the turn may have run, so say so, now and to any retry, instead of inviting a resend.
            _log.exception("turn failed for user %s", user_id)
            note = ("\n\n" if any(reply) else "") + INTERRUPTED_REPLY
            run._push(sse({"token": note}))
            reply.append(note)
        finally:
            try:
                on_finish("".join(reply))
            except Exception:
                _log.exception("could not store the reply for user %s", user_id)
            run._push(DONE)
            run._close()
            _running.pop((user_id, key), None)

    task = asyncio.create_task(drive())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return run
