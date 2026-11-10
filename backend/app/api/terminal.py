"""Terminal REST + WebSocket API.

REST creates/lists/kills sessions; the WebSocket carries keystrokes and output for
one session. Authentication is the same JWT used everywhere else, and a session can
only be driven by the user who created it.
"""
from __future__ import annotations

import asyncio
import json
import uuid

import jwt
from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.core.deps import ADMIN, get_current_user
from app.core.security import decode_token
from app.models import Project, User
from app.services import audit, runner, workspace_fs
from app.services.terminal import terminals

router = APIRouter(tags=["Terminal"])


def _project_for(db: Session, user: User, project_id: str) -> Project:
    project = db.get(Project, project_id)
    if not project or project.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Only the project owner can open a terminal here")
    return project


def _docker_user() -> str:
    import os
    if os.name != "nt" and hasattr(os, "geteuid") and os.geteuid() > 0:
        return f"{os.geteuid()}:{os.getegid()}"
    return "65534:65534"


def _docker_container(project: Project) -> str | None:
    """Give each terminal tab its own long-lived development container."""
    from app.core.config import settings
    if settings.RUNNER_MODE != "docker":
        return None
    return f"workcode-ws-{project.id[:8]}-{uuid.uuid4().hex[:8]}"


@router.post("/workspace/{project_id}/terminals")
def create_terminal(project_id: str, request: Request, db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    if user.role == ADMIN:
        raise HTTPException(status_code=403, detail="Admin accounts do not have a workspace")
    if settings.RUNNER_MODE != "docker":
        raise HTTPException(status_code=503,
                            detail="Interactive terminals require RUNNER_MODE=docker; host-shell fallback is disabled.")
    project = _project_for(db, user, project_id)
    workspace_fs.materialize(db, project)

    container = _docker_container(project)
    if container:
        import subprocess
        image = settings.DOCKER_TERMINAL_IMAGE
        available, diagnostic = runner.ensure_terminal_image()
        if not available:
            raise HTTPException(status_code=503, detail=diagnostic)
        try:
            started = subprocess.run(runner._docker_argv(
                "run", "--pull=never", "-d", "--name", container,
                "--network", "none", "--memory", "1g", "--memory-swap", "1g", "--cpus", "1",
                "--pids-limit", "128", "--read-only", "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges", "--user", _docker_user(),
                "--tmpfs", "/tmp:rw,nosuid,nodev,size=128m",
                "--tmpfs", "/home/dev:rw,nosuid,nodev,size=64m",
                *runner.docker_mount_args(workspace_fs.root(project), "/work"),
                "-w", "/work", image, "sleep", "infinity",
            ), capture_output=True, text=True, check=False, timeout=30, env=runner._docker_env())
        except FileNotFoundError:
            raise HTTPException(status_code=503, detail="Docker is required for isolated terminals but is not installed or available.")
        except subprocess.TimeoutExpired as exc:
            raise HTTPException(status_code=503, detail=f"Docker terminal startup timed out: {exc}")
        except OSError as exc:
            raise HTTPException(status_code=503, detail=f"Could not start Docker terminal: {exc}")
        if started.returncode != 0:
            detail = (started.stderr or started.stdout or "Docker failed to start the terminal container").strip()
            raise HTTPException(status_code=503, detail=detail[-1000:])

    try:
        session = terminals.create(
            user_id=user.id, project_id=project.id,
            cwd=str(workspace_fs.root(project)),
            home=str(workspace_fs.home_dir(project)),
            docker_container=container,
        )
    except (RuntimeError, OSError) as exc:
        if container:
            import subprocess
            try:
                subprocess.run(runner._docker_argv("rm", "-f", container), capture_output=True,
                               timeout=10, check=False, env=runner._docker_env())
            except (OSError, subprocess.SubprocessError):
                pass
        if isinstance(exc, RuntimeError):
            raise HTTPException(status_code=429, detail=str(exc))
        raise HTTPException(status_code=503, detail=f"Could not attach to Docker terminal: {exc}")

    audit.log(db, user, "terminal.opened", "project", project.id, request, session=session.id)
    return {"id": session.id, "cwd": session.cwd, "shell": session.shell,
            "isolated": bool(container), "terminals": terminals.list_for_project(project.id)}


@router.get("/workspace/{project_id}/terminals")
def list_terminals(project_id: str, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    project = _project_for(db, user, project_id)
    return {"terminals": terminals.list_for_project(project.id)}


@router.delete("/workspace/terminals/{session_id}")
def kill_terminal(session_id: str, request: Request, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    session = terminals.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Terminal not found")
    if session.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not your terminal")
    terminals.kill(session_id)
    audit.log(db, user, "terminal.killed", "terminal", session_id, request)
    return {"detail": "Terminal closed"}


@router.websocket("/ws/terminal/{session_id}")
async def terminal_socket(ws: WebSocket, session_id: str, token: str = Query(...)):
    try:
        claims = decode_token(token, expected_type="access")
    except jwt.PyJWTError:
        await ws.close(code=4401)
        return

    session = terminals.get(session_id)
    if not session or session.user_id != claims.get("sub"):
        await ws.close(code=4403)
        return

    await ws.accept()
    queue, replay = session.attach()
    await ws.send_text(json.dumps({"type": "ready", "id": session.id, "cwd": session.cwd,
                                   "alive": session.alive}))
    if replay:
        await ws.send_text(json.dumps({"type": "output", "data": replay, "replay": True}))

    async def pump():
        while True:
            message = await queue.get()
            if message is None:
                await ws.send_json({"type": "exit", "code": session.exit_code})
                break
            await ws.send_json(message)

    async def read():
        while True:
            raw = await ws.receive_text()
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                session.write(raw)
                continue
            kind = payload.get("type")
            if kind == "input":
                session.write(payload.get("data", ""))
            elif kind == "resize":
                session.resize(payload.get("cols", 120), payload.get("rows", 30))
            elif kind == "ping":
                await ws.send_json({"type": "pong"})

    tasks = [asyncio.create_task(pump()), asyncio.create_task(read())]
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            if task.cancelled():
                continue
            error = task.exception()
            if error and not isinstance(error, (WebSocketDisconnect, OSError, RuntimeError)):
                raise error
        for task in pending:
            task.cancel()
    except WebSocketDisconnect:
        pass
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        session.detach(queue)
        try:
            await ws.close()
        except RuntimeError:
            pass


# Root-level alias: the browser talks to /ws/terminal/<id>, not the router prefix.
root_ws = APIRouter()
root_ws.add_api_websocket_route("/ws/terminal/{session_id}", terminal_socket)
