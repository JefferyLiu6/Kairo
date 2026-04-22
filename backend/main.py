"""Entry point for the Kairo demo server."""
import os
import signal

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=False))

from assistant.http.pm_app import app  # noqa: F401


def _handle_signal(signum, _frame) -> None:
    raise SystemExit(0)


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("AGENT_PORT", "8766"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
