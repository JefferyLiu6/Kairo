"""
Dedicated thread pool for blocking sync work invoked from async handlers.

`asyncio.to_thread()` uses the event loop's *default* executor. After
`loop.shutdown_default_executor()` (e.g. graceful teardown or edge cases in the
stack), the loop marks the default executor as shut down and `to_thread` raises
RuntimeError('Executor shutdown has been called'). A separate pool avoids that.
"""
from __future__ import annotations

import concurrent.futures

SYNC_WORKERS = concurrent.futures.ThreadPoolExecutor(
    max_workers=8,
    thread_name_prefix="assistant_sync",
)


def shutdown_sync_workers() -> None:
    SYNC_WORKERS.shutdown(wait=False)
