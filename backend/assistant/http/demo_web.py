"""
Demo web gateway — chat/stream (PM only), workspace, sessions, usage.

Excludes: coding agent, master agent, research agent, web search.
"""
from __future__ import annotations

import hmac
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Security, WebSocket, WebSocketDisconnect
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from assistant.personal_manager.agent import PMConfig, astream_pm
from assistant.personal_manager.persistence.store import (
    ScheduleData,
    get_upcoming_events,
    load_schedule as _load_schedule,
    save_schedule as _save_schedule,
)
from assistant.shared.usage import list_all_usage, load_usage
from assistant.shared.llm_env import load_default_llm_from_env

router = APIRouter()

# ── Auth ──────────────────────────────────────────────────────────────────────

_bearer = HTTPBearer(auto_error=False)


def _require_auth(
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
) -> None:
    token = os.environ.get("GATEWAY_TOKEN", "").strip()
    if not token:
        # No token configured → fail closed. Set GATEWAY_TOKEN to use these routes.
        raise HTTPException(status_code=401, detail="Unauthorized")
    if credentials is None or not hmac.compare_digest(credentials.credentials, token):
        raise HTTPException(status_code=401, detail="Unauthorized")


def _websocket_authorized(websocket: WebSocket) -> bool:
    token = os.environ.get("GATEWAY_TOKEN", "").strip()
    if not token:
        return False
    header = websocket.headers.get("authorization", "")
    presented = ""
    if header.lower().startswith("bearer "):
        presented = header[7:].strip()
    else:
        presented = websocket.query_params.get("token", "")
    return hmac.compare_digest(presented, token)


from assistant.http.pm_app import _rate_limit  # noqa: E402 — shared rate limiter


# ── Config ────────────────────────────────────────────────────────────────────

def _cfg() -> dict:
    d = load_default_llm_from_env()
    if d:
        provider = d["provider"]
        model = d["model"]
        api_key = d["api_key"]
        base_url = d["base_url"]
    else:
        provider = os.environ.get("PROVIDER", "anthropic")
        model = os.environ.get("MODEL", "claude-opus-4-6")
        api_key = (
            os.environ.get("API_KEY")
            or os.environ.get("ANTHROPIC_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
        )
        base_url = os.environ.get("BASE_URL")
    return {
        "provider": provider,
        "model": model,
        "api_key": api_key,
        "base_url": base_url,
        "data_dir": os.environ.get("DATA_DIR", "./data"),
        "vault_dir": os.environ.get("VAULT_DIR"),
        "workspace_dir": os.environ.get("WORKSPACE_DIR", "./workspace"),
    }


# ── Chat (PM mode only) ───────────────────────────────────────────────────────

class _StreamReq(BaseModel):
    message: str
    sessionId: str
    mode: str = "personal-manager"


@router.post("/chat/stream")
async def chat_stream(
    req: _StreamReq,
    request: Request,
    _auth: None = Depends(_require_auth),
    _rl: None = Depends(_rate_limit),
):
    """SSE streaming — personal-manager mode only."""
    cfg = _cfg()

    async def _generate():
        sid = (
            f"pm-{req.sessionId}"
            if not req.sessionId.startswith("pm-")
            else req.sessionId
        )
        pm_cfg = PMConfig(
            provider=cfg["provider"], model=cfg["model"],
            api_key=cfg["api_key"], base_url=cfg["base_url"],
            data_dir=cfg["data_dir"], vault_dir=cfg["vault_dir"],
            session_id=sid,
        )
        async for kind, value in astream_pm(req.message, pm_cfg):
            if kind == "progress":
                yield f"data: {json.dumps({'progress': value})}\n\n"
            elif kind == "token":
                yield f"data: {json.dumps({'token': value})}\n\n"
            elif kind == "done":
                break
        yield "data: [DONE]\n\n"

    return StreamingResponse(_generate(), media_type="text/event-stream")


# Sessions endpoint returns empty — no master agent history in this demo
@router.get("/sessions")
def list_sessions(_auth: None = Depends(_require_auth)) -> dict:
    return {"sessions": []}


@router.delete("/sessions/{session_id}", status_code=204)
def delete_session(session_id: str, _auth: None = Depends(_require_auth)):
    pass


# ── Workspace ─────────────────────────────────────────────────────────────────

_IGNORED = frozenset({
    ".git", "__pycache__", ".DS_Store", "node_modules", ".venv",
    ".pytest_cache", "dist", ".next",
})

_LANG_CMDS: dict[str, list[str]] = {
    "Python": ["python3"],
    "Node": ["node"],
    "tsx": ["tsx"],
    "Bash": ["bash"],
    "Go": ["go", "run"],
    "Ruby": ["ruby"],
}

_GUI_IMPORTS_RE = re.compile(
    r"^\s*(?:import|from)\s+(?:tkinter|wx|PyQt5|PyQt6|PySide2|PySide6|pygame)\b",
    re.MULTILINE,
)
_INPUT_CALLS_RE = re.compile(r"\binput\s*\(", re.MULTILINE)
_STDIN_READ_RE = re.compile(r"sys\.stdin\.read|getpass\.", re.MULTILINE)


def _preflight_check(p: Path, language: str, allow_interactive: bool = False) -> Optional[str]:
    lang = language or ""
    if lang not in ("Python", "Node", "tsx"):
        return None
    try:
        source = p.read_text(errors="replace")
    except Exception:
        return None
    if lang == "Python":
        if _GUI_IMPORTS_RE.search(source):
            return "This file imports a GUI library. GUI programs need a display and cannot run in the sandbox."
        if not allow_interactive and (
            _INPUT_CALLS_RE.search(source) or _STDIN_READ_RE.search(source)
        ):
            return "This file calls input() or reads stdin. Use the terminal mode for interactive programs."
    return None


def _walk(abs_path: Path, rel: str) -> list[dict]:
    entries = []
    try:
        items = sorted(abs_path.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    except PermissionError:
        return []
    for item in items:
        if item.name in _IGNORED:
            continue
        item_rel = f"{rel}/{item.name}" if rel else item.name
        if item.is_dir():
            entries.append({"name": item.name, "path": item_rel, "type": "dir", "children": _walk(item, item_rel)})
        else:
            stat = item.stat()
            entries.append({"name": item.name, "path": item_rel, "type": "file", "size": stat.st_size, "modified": int(stat.st_mtime * 1000)})
    return entries


def _ws_root() -> Optional[Path]:
    d = os.environ.get("WORKSPACE_DIR")
    if not d:
        return None
    p = Path(d).expanduser().resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def _safe_path(workspace: Path, rel: str) -> Path:
    p = (workspace / rel).resolve()
    if not str(p).startswith(str(workspace)):
        raise HTTPException(status_code=400, detail="Path outside workspace")
    return p


@router.get("/workspace")
def workspace_tree(_auth: None = Depends(_require_auth)):
    root = _ws_root()
    if root is None:
        return {"root": None, "files": []}
    return {"root": str(root), "files": _walk(root, "")}


@router.get("/workspace/file")
def workspace_read_file(path: str, _auth: None = Depends(_require_auth)):
    root = _ws_root()
    if root is None:
        raise HTTPException(status_code=503, detail="WORKSPACE_DIR not configured")
    p = _safe_path(root, path)
    if not p.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    try:
        return {"content": p.read_text(errors="replace")}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


class _WriteReq(BaseModel):
    path: str
    content: str


@router.put("/workspace/file")
def workspace_write_file(req: _WriteReq, _auth: None = Depends(_require_auth)):
    root = _ws_root()
    if root is None:
        raise HTTPException(status_code=503, detail="WORKSPACE_DIR not configured")
    p = _safe_path(root, req.path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(req.content)
    return {"ok": True}


class _MkdirReq(BaseModel):
    path: str


@router.post("/workspace/mkdir")
def workspace_mkdir(req: _MkdirReq, _auth: None = Depends(_require_auth)):
    root = _ws_root()
    if root is None:
        raise HTTPException(status_code=503, detail="WORKSPACE_DIR not configured")
    p = _safe_path(root, req.path)
    p.mkdir(parents=True, exist_ok=True)
    return {"ok": True}


@router.delete("/workspace/entry", status_code=204)
def workspace_delete(path: str, _auth: None = Depends(_require_auth)):
    root = _ws_root()
    if root is None:
        raise HTTPException(status_code=503, detail="WORKSPACE_DIR not configured")
    p = _safe_path(root, path)
    if p.is_dir():
        shutil.rmtree(p)
    elif p.is_file():
        p.unlink()


@router.get("/workspace/raw/{file_path:path}")
def workspace_raw(file_path: str, _auth: None = Depends(_require_auth)):
    root = _ws_root()
    if root is None:
        raise HTTPException(status_code=503, detail="WORKSPACE_DIR not configured")
    p = _safe_path(root, file_path)
    if not p.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(str(p))


_docker_checked_at: float = 0.0
_docker_is_available: bool = False
_DOCKER_CHECK_TTL = 60.0


def _docker_available() -> bool:
    global _docker_checked_at, _docker_is_available
    now = time.monotonic()
    if now - _docker_checked_at < _DOCKER_CHECK_TTL:
        return _docker_is_available
    try:
        subprocess.run(["docker", "version"], capture_output=True, timeout=5, check=True)
        _docker_is_available = True
    except Exception:
        _docker_is_available = False
    _docker_checked_at = now
    return _docker_is_available


def _run_in_docker(cmd_prefix: list[str], file_rel: str, workspace: Path, timeout: int) -> dict:
    image = (
        os.environ.get("WORKSPACE_RUN_IMAGE")
        or os.environ.get("CODING_DOCKER_IMAGE", "python:3.12-slim")
    )
    docker_cmd = [
        "docker", "run", "--rm", "--network", "none",
        "--memory", "256m", "--cpus", "0.5", "--pids-limit", "64",
        "--security-opt", "no-new-privileges", "--read-only", "--tmpfs", "/tmp",
        "-v", f"{workspace}:/work:ro", "-w", "/work", image,
    ] + cmd_prefix + [file_rel]
    t0 = time.monotonic()
    try:
        result = subprocess.run(docker_cmd, capture_output=True, text=True, timeout=timeout)
        return {"stdout": result.stdout[:20_000], "stderr": result.stderr[:5_000], "exitCode": result.returncode, "durationMs": int((time.monotonic() - t0) * 1000), "image": image, "timedOut": False}
    except subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": f"Process timed out after {timeout}s", "exitCode": None, "durationMs": timeout * 1000, "image": image, "timedOut": True}


class _RunReq(BaseModel):
    path: str
    language: Optional[str] = None


@router.post("/workspace/run")
def workspace_run(req: _RunReq, _auth: None = Depends(_require_auth)):
    root = _ws_root()
    if root is None:
        raise HTTPException(status_code=503, detail="WORKSPACE_DIR not configured")
    p = _safe_path(root, req.path)
    if not p.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    cmd_prefix = _LANG_CMDS.get(req.language or "", [])
    if not cmd_prefix:
        raise HTTPException(status_code=400, detail=f"Unsupported language: {req.language!r}")
    preflight_warn = _preflight_check(p, req.language or "", allow_interactive=False)
    if preflight_warn:
        raise HTTPException(status_code=422, detail=preflight_warn)
    timeout = 30
    use_docker = os.environ.get("WORKSPACE_DOCKER", "1").strip() not in ("0", "false", "no")
    if use_docker and _docker_available():
        try:
            file_rel = str(p.relative_to(root))
            return _run_in_docker(cmd_prefix, file_rel, root, timeout)
        except Exception:
            pass
    cmd = cmd_prefix + [str(p)]
    t0 = time.monotonic()
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=str(root))
        return {"stdout": result.stdout[:20_000], "stderr": result.stderr[:5_000], "exitCode": result.returncode, "durationMs": int((time.monotonic() - t0) * 1000), "image": "local", "timedOut": False}
    except subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": f"Process timed out after {timeout}s", "exitCode": None, "durationMs": timeout * 1000, "image": "local", "timedOut": True}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.websocket("/ws/terminal")
async def terminal_ws(websocket: WebSocket, path: str = "", language: str = "") -> None:
    import asyncio
    if not _websocket_authorized(websocket):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    root = _ws_root()
    if root is None:
        await websocket.send_json({"type": "error", "data": "WORKSPACE_DIR not configured"})
        await websocket.close(code=1011)
        return
    try:
        p = _safe_path(root, path)
    except HTTPException as exc:
        await websocket.send_json({"type": "error", "data": exc.detail})
        await websocket.close(code=1008)
        return
    if not p.is_file():
        await websocket.send_json({"type": "error", "data": "File not found"})
        await websocket.close(code=1008)
        return
    cmd_prefix = _LANG_CMDS.get(language or "", [])
    if not cmd_prefix:
        await websocket.send_json({"type": "error", "data": f"Unsupported language: {language!r}"})
        await websocket.close(code=1008)
        return
    warn = _preflight_check(p, language, allow_interactive=True)
    if warn:
        await websocket.send_json({"type": "error", "data": warn})
        await websocket.close(code=1008)
        return
    cmd = cmd_prefix + [str(p)]
    try:
        proc = await asyncio.create_subprocess_exec(*cmd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, cwd=str(root))
    except Exception as exc:
        await websocket.send_json({"type": "error", "data": f"Failed to start process: {exc}"})
        await websocket.close(code=1011)
        return
    _IDLE_TIMEOUT = 300.0

    async def _pump_stdout() -> None:
        assert proc.stdout is not None
        while True:
            try:
                chunk = await asyncio.wait_for(proc.stdout.read(4096), timeout=_IDLE_TIMEOUT)
            except asyncio.TimeoutError:
                try:
                    await websocket.send_json({"type": "error", "data": "Idle timeout (5 min)"})
                except Exception:
                    pass
                proc.kill()
                return
            if not chunk:
                break
            try:
                await websocket.send_json({"type": "stdout", "data": chunk.decode("utf-8", errors="replace")})
            except Exception:
                proc.kill()
                return

    pump_task = asyncio.create_task(_pump_stdout())
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            kind = msg.get("type")
            if kind == "stdin":
                data = msg.get("data", "")
                if proc.stdin and not proc.stdin.is_closing():
                    proc.stdin.write(data.encode())
                    await proc.stdin.drain()
            elif kind == "signal":
                import signal as _signal_mod
                sig_name = msg.get("signal", "SIGTERM")
                sig = getattr(_signal_mod, sig_name, _signal_mod.SIGTERM)
                try:
                    proc.send_signal(sig)
                except ProcessLookupError:
                    pass
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        pump_task.cancel()
        if proc.returncode is None:
            try:
                proc.kill()
            except Exception:
                pass
        exit_code = await proc.wait()
        try:
            await websocket.send_json({"type": "exit", "data": exit_code})
        except Exception:
            pass


# ── PM schedule + upcoming (query-param style for web UI) ────────────────────

@router.get("/personal-manager/schedule")
def web_get_pm_schedule(sessionId: str, _auth: None = Depends(_require_auth)):
    data_dir = _cfg()["data_dir"]
    sid = f"pm-{sessionId}" if not sessionId.startswith("pm-") else sessionId
    return {"schedule": _load_schedule(sid, data_dir).model_dump()}


class _PmScheduleReq(BaseModel):
    sessionId: str
    schedule: ScheduleData


@router.put("/personal-manager/schedule", status_code=200)
def web_put_pm_schedule(req: _PmScheduleReq, _auth: None = Depends(_require_auth)):
    data_dir = _cfg()["data_dir"]
    sid = f"pm-{req.sessionId}" if not req.sessionId.startswith("pm-") else req.sessionId
    _save_schedule(req.schedule, sid, data_dir)
    return {"ok": True}


@router.get("/usage")
def web_get_usage(sessionId: str, _auth: None = Depends(_require_auth)):
    return load_usage(sessionId, _cfg()["data_dir"])


@router.get("/usage/all")
def web_get_all_usage(_auth: None = Depends(_require_auth)):
    return {"sessions": list_all_usage(_cfg()["data_dir"])}


@router.get("/personal-manager/upcoming")
def web_get_upcoming(sessionId: str, days: int = 1, _auth: None = Depends(_require_auth)):
    data_dir = _cfg()["data_dir"]
    sid = f"pm-{sessionId}" if not sessionId.startswith("pm-") else sessionId
    events = get_upcoming_events(sid, data_dir, days=max(1, min(days, 30)))
    return {"events": events}
