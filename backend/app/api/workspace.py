"""Coding workspace API: file system, execution, preview, dev servers, builds.

The disk workspace is authoritative; PostgreSQL is the index (see workspace_fs).
"""
from __future__ import annotations

import mimetypes
import os
from pathlib import Path

import jwt
from fastapi import (APIRouter, Depends, File, Form, HTTPException, Query, Request,
                     Response, UploadFile)
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.core.deps import ADMIN, get_current_user
from app.core.security import decode_token
from app.models import Project, User
from app.schemas import RunIn, RunOut
from app.services import audit, devserver, runner, workspace_fs
from app.services import storage

router = APIRouter(prefix="/workspace", tags=["Coding Workspace"])

BLOCKED_COMMANDS = ("rm -rf /", "mkfs", "shutdown", "reboot", "dd if=", "chmod 777 /", "sudo ")


# ------------------------------------------------------------------ utilities

def _web_root(root: Path) -> Path | None:
    """Find the folder that actually holds index.html (project root, dist, or one level down)."""
    if (root / "index.html").exists():
        return root
    skip = {"node_modules", ".git", "dist", ".venv", "__pycache__", "build"}
    for depth in (1, 2):
        pattern = "/".join(["*"] * depth) + "/index.html"
        for candidate in sorted(root.glob(pattern)):
            parts = Path(os.path.relpath(candidate, root)).parts[:-1]
            if any(part in skip for part in parts):
                continue
            return candidate.parent
    return None


def _project(db: Session, user: User, project_id: str) -> Project:
    project = db.get(Project, project_id)
    if not project or project.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Project not found")
    if user.role == ADMIN or project.owner_id == user.id:
        return project
    raise HTTPException(status_code=403, detail="You do not have access to this project")


def _preview_user(request: Request, token: str | None, db: Session) -> User:
    """Preview iframes cannot send headers — accept ?token= or the preview cookie.

    The cookie matters for assets: a browser resolves ``./style.css`` against the
    directory and drops ``?token=``, so <link>/<script> would 401 without it.
    """
    raw = token or request.cookies.get("wc_preview")
    if not raw:
        header = request.headers.get("authorization", "")
        if header.lower().startswith("bearer "):
            raw = header[7:]
    if not raw:
        raise HTTPException(status_code=401, detail="Preview requires a token")
    try:
        claims = decode_token(raw, expected_type="access")
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid preview token")
    user = db.get(User, claims.get("sub"))
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Invalid preview session")
    return user


# ---------------------------------------------------------------- languages
@router.get("/languages")
def languages(user: User = Depends(get_current_user)):
    import shutil
    langs = runner.list_languages()
    for lang in langs:
        meta = runner.LANGUAGES.get(lang["id"], {})
        binary = meta.get("bin")
        binaries = {"python": ("python", "python3", "py"), "shell": ("bash", "sh")}.get(lang["id"], (binary,))
        lang["installed"] = bool(binary and any(shutil.which(name) for name in binaries)) or lang["id"] == "sql"
    return {
        "runner_mode": settings.RUNNER_MODE,
        "languages": langs,
        "shells": {"bash": bool(shutil.which("bash")), "powershell": bool(shutil.which("powershell.exe") or shutil.which("pwsh.exe")),
                   "cmd": bool(shutil.which("cmd.exe")), "node": bool(shutil.which("node")),
                   "npm": bool(shutil.which("npm")), "git": bool(shutil.which("git")),
                   "python": bool(shutil.which("python") or shutil.which("python3") or shutil.which("py")), "javac": bool(shutil.which("javac")),
                   "gcc": bool(shutil.which("gcc")), "g++": bool(shutil.which("g++")),
                   "php": bool(shutil.which("php")), "ruby": bool(shutil.which("ruby")),
                   "go": bool(shutil.which("go")), "rustc": bool(shutil.which("rustc"))},
    }


# ---------------------------------------------------------------------- run
@router.post("/run", response_model=RunOut)
def run_code(payload: RunIn, request: Request, db: Session = Depends(get_db),
             user: User = Depends(get_current_user)):
    if user.role == ADMIN:
        raise HTTPException(status_code=403, detail="Admin accounts do not have a coding workspace")

    project_id = getattr(payload, "project_id", None)
    if project_id:
        project = _project(db, user, project_id)
        if project.owner_id != user.id and user.role != ADMIN:
            raise HTTPException(status_code=403, detail="Read-only project")
        workspace_fs.materialize(db, project)
        workspace_fs.sync_index(db, project)

        entry = payload.filename or workspace_fs.entry_point(project) or "main.py"
        if payload.code is not None:
            # editor buffer wins over disk, and is persisted so imports see it
            workspace_fs.write(project, entry, payload.code)
            workspace_fs.sync_index(db, project)
        if not workspace_fs.safe(project, entry).exists():
            raise HTTPException(status_code=422, detail=f"Entry point not found: {entry}")

        result = runner.execute_in_workspace(
            project.language, str(workspace_fs.root(project)), entry,
            payload.stdin, home=str(workspace_fs.home_dir(project)),
        )
        audit.log(db, user, "workspace.run", "project", project.id, request,
                  entry=entry, ok=result["ok"], ms=result.get("duration_ms"))
        return result

    if payload.code is None:
        raise HTTPException(status_code=422, detail="Provide code or a project_id")
    result = runner.execute(payload.language, payload.code, payload.filename, payload.stdin)
    audit.log(db, user, "workspace.run_scratch", "project", "", request,
              language=payload.language, ok=result["ok"])
    return result


# --------------------------------------------------------------------- tree
@router.get("/{project_id}/tree")
def tree(project_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user),
         include_ignored: bool = Query(False)):
    project = _project(db, user, project_id)
    workspace_fs.materialize(db, project)
    files = workspace_fs.tree(project, include_ignored=include_ignored)
    return {
        "project": {"id": project.id, "name": project.name, "language": project.language,
                    "current_version": project.current_version},
        "entry": workspace_fs.entry_point(project),
        "files": files,
        "count": len(files),
    }


@router.get("/{project_id}/file")
def read_file(project_id: str, path: str = Query(...), db: Session = Depends(get_db),
              user: User = Depends(get_current_user)):
    project = _project(db, user, project_id)
    try:
        return workspace_fs.read(project, path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="File not found")
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


class FileWriteIn(BaseModel):
    path: str
    content: str = ""
    is_dir: bool = False


class FileMoveIn(BaseModel):
    from_path: str = ""
    to_path: str = ""
    action: str = "rename"  # rename | copy | duplicate


@router.post("/{project_id}/file")
def write_file(project_id: str, payload: FileWriteIn, request: Request,
               db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    project = _project(db, user, project_id)
    if project.owner_id != user.id and user.role != ADMIN:
        raise HTTPException(status_code=403, detail="Read-only access")
    try:
        if payload.is_dir:
            result = workspace_fs.mkdir(project, payload.path)
        else:
            result = workspace_fs.write(project, payload.path, payload.content)
    except (ValueError, OSError, IsADirectoryError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    workspace_fs.sync_index(db, project)
    audit.log(db, user, "workspace.file_created", "project", project.id, request, path=payload.path)
    return result


@router.patch("/{project_id}/file")
def move_file(project_id: str, payload: FileMoveIn, request: Request,
              db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    project = _project(db, user, project_id)
    if project.owner_id != user.id and user.role != ADMIN:
        raise HTTPException(status_code=403, detail="Read-only access")
    try:
        if payload.action == "duplicate":
            result = workspace_fs.duplicate(project, payload.from_path)
        elif payload.action == "copy":
            result = workspace_fs.copy(project, payload.from_path, payload.to_path)
        else:
            result = workspace_fs.rename(project, payload.from_path, payload.to_path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Source not found")
    except FileExistsError:
        raise HTTPException(status_code=409, detail="Target already exists")
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    workspace_fs.sync_index(db, project)
    audit.log(db, user, f"workspace.file_{payload.action}", "project", project.id, request,
              src=payload.from_path, dst=payload.to_path)
    return result


@router.delete("/{project_id}/file")
def delete_file(project_id: str, path: str = Query(...), request: Request = None,
                db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    project = _project(db, user, project_id)
    if project.owner_id != user.id and user.role != ADMIN:
        raise HTTPException(status_code=403, detail="Read-only access")
    try:
        result = workspace_fs.delete(project, path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    workspace_fs.sync_index(db, project)
    audit.log(db, user, "workspace.file_deleted", "project", project.id, request, path=path)
    return result


@router.post("/{project_id}/upload")
async def upload(project_id: str, file: UploadFile = File(...), dir: str = Form(""),
                 db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    project = _project(db, user, project_id)
    data = await file.read()
    if len(data) > settings.MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File too large")
    result = workspace_fs.save_upload(project, dir, file.filename or "upload.bin", data)
    workspace_fs.sync_index(db, project)
    return result


@router.get("/{project_id}/download")
def download(project_id: str, path: str | None = Query(None), db: Session = Depends(get_db),
             user: User = Depends(get_current_user)):
    project = _project(db, user, project_id)
    if path:
        target = workspace_fs.safe(project, path)
        if not target.exists():
            raise HTTPException(status_code=404, detail="Not found")
        if target.is_dir():
            raise HTTPException(status_code=400, detail="Use the project ZIP endpoint for folders")
        return FileResponse(str(target), filename=target.name)
    data = workspace_fs.zip_project(project)
    name = "".join(c for c in project.name if c.isalnum() or c in "-_ ") or "project"
    return Response(content=data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{name}.zip"'})


@router.get("/{project_id}/search")
def search(project_id: str, q: str = Query(..., min_length=1), db: Session = Depends(get_db),
           user: User = Depends(get_current_user)):
    project = _project(db, user, project_id)
    workspace_fs.materialize(db, project)
    return {"query": q, "results": workspace_fs.search(project, q)}


@router.post("/{project_id}/sync")
def sync(project_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Pull terminal-created files into the index and the version system."""
    project = _project(db, user, project_id)
    stats = workspace_fs.sync_index(db, project)
    return {**stats, "files": workspace_fs.tree(project)}


# ------------------------------------------------------------------ preview

def _no_preview_page(root_dir: Path) -> str:
    """Explain the next step instead of a raw JSON 404."""
    pkg = next((p for p in [root_dir / "package.json", *root_dir.glob("*/package.json")]), None)
    where = pkg.parent.name if pkg and pkg.parent != root_dir else "."
    return f"""<!doctype html><html><body style="font-family:system-ui;background:#1e1e1e;color:#cccccc;padding:32px">
<h3 style="font-weight:400">Nothing to preview yet</h3>
<p style="color:#8b8b8b">This project has no <code>index.html</code> and no running development server.</p>
<p style="color:#8b8b8b">Start it from the terminal:</p>
<pre style="background:#252526;padding:12px;border:1px solid #2b2b2b">cd {where} &amp;&amp; npm install &amp;&amp; npm run dev -- --host 0.0.0.0</pre>
<p style="color:#8b8b8b">or press the <b>Run</b> button above — WORKCODE starts the dev server and proxies it here.</p>
</body></html>"""


@router.get("/preview/{project_id}")
@router.get("/preview/{project_id}/{path:path}")
def preview(project_id: str, path: str = "", token: str | None = Query(None),
            request: Request = None, db: Session = Depends(get_db)):
    """Serve the real project files so relative CSS/JS/image paths just work.

    When a production build exists (``dist/``), that is served instead — which is how
    Vite/React apps render inside WORKCODE without a proxy.
    """
    user = _preview_user(request, token, db)
    project = _project(db, user, project_id)

    # A running dev server is the project: never fall back to a stale index.html lookup.
    if not path:
        live = [s for s in devserver.processes.list_for(project.id)
                if s.get("port") and s.get("status") in ("running", "starting")]
        raw_token = token or request.cookies.get("wc_preview")
        if live and raw_token:
            return RedirectResponse(
                f"/api/workspace/{project_id}/devproxy/{live[0]['id']}/{raw_token}/",
                status_code=307)

    root_dir = workspace_fs.root(project)
    dist = root_dir / "dist"
    if (dist / "index.html").exists():
        base = dist
    else:
        base = _web_root(root_dir) or root_dir

    if not path or path.endswith("/"):
        path = f"{path}index.html"
    try:
        target = storage.safe_join(base, path)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid path")
    if not target.exists() or target.is_dir():
        if (base / "index.html").exists() and "." not in Path(path).name:
            target = base / "index.html"  # SPA fallback
        elif not path.endswith("index.html"):
            raise HTTPException(status_code=404, detail=f"{path} not found")
        else:
            return HTMLResponse(_no_preview_page(root_dir), status_code=200)

    mime, _ = mimetypes.guess_type(str(target))
    if target.suffix in (".jsx", ".tsx", ".ts"):
        mime = "application/javascript"
    headers = {"Cache-Control": "no-store", "X-Frame-Options": "SAMEORIGIN"}
    response = FileResponse(str(target), media_type=mime or "application/octet-stream", headers=headers)
    raw_token = token or request.cookies.get("wc_preview")
    if raw_token:
        # scoped to the preview tree so sibling assets authenticate on their own
        response.set_cookie("wc_preview", raw_token, path="/api/workspace/preview",
                            httponly=True, samesite="lax", max_age=3600)
    return response


# --------------------------------------------------------- build & servers
@router.post("/{project_id}/build")
def build(project_id: str, request: Request, db: Session = Depends(get_db),
          user: User = Depends(get_current_user)):
    project = _project(db, user, project_id)
    if project.owner_id != user.id and user.role != ADMIN:
        raise HTTPException(status_code=403, detail="Read-only access")
    workspace_fs.materialize(db, project)
    result = devserver.build_project(str(workspace_fs.root(project)),
                                     str(workspace_fs.home_dir(project)))
    workspace_fs.sync_index(db, project)
    audit.log(db, user, "workspace.build", "project", project.id, request, ok=result["ok"])
    return result


@router.get("/{project_id}/servers")
def list_servers(project_id: str, db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    project = _project(db, user, project_id)
    return {"servers": devserver.processes.list_for(project.id)}


class ServerStartIn(BaseModel):
    command: str = "npm run dev"
    port: int | None = None


@router.post("/{project_id}/servers")
def start_server(project_id: str, payload: ServerStartIn, request: Request,
                 db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    project = _project(db, user, project_id)
    if project.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Only the owner can start servers")
    raise HTTPException(status_code=503,
                        detail="Live dev servers are unavailable until isolated Docker port forwarding is configured.")


@router.delete("/{project_id}/servers/{server_id}")
def stop_server(project_id: str, server_id: str, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    _project(db, user, project_id)
    if not devserver.processes.stop(server_id):
        raise HTTPException(status_code=404, detail="Server not found")
    return {"detail": "Server stopped"}




@router.post("/{project_id}/dev")
def run_dev(project_id: str, request: Request, db: Session = Depends(get_db),
            user: User = Depends(get_current_user)):
    """Do not launch project package scripts as host processes."""
    project = _project(db, user, project_id)
    if project.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Only the owner can run this project")
    raise HTTPException(status_code=503,
                        detail="Live dev servers are unavailable until isolated Docker port forwarding is configured.")

# ------------------------------------------------------------ legacy one-shot
class TerminalIn(BaseModel):
    command: str
    project_id: str | None = None
    cwd: str = ""


@router.post("/terminal")
def terminal(payload: TerminalIn, request: Request, db: Session = Depends(get_db),
             user: User = Depends(get_current_user)):
    """Deprecated single-command endpoint, kept for API compatibility.

    Real interactive shells live at ``POST /workspace/{id}/terminals`` + the
    ``/ws/terminal/{session}`` socket.
    """
    if user.role == ADMIN:
        raise HTTPException(status_code=403, detail="Admin accounts do not have a terminal")
    cmd = (payload.command or "").strip()
    if not cmd:
        raise HTTPException(status_code=422, detail="Empty command")
    if any(bad in cmd for bad in BLOCKED_COMMANDS):
        raise HTTPException(status_code=403, detail="Command blocked by the terminal policy")

    cwd = str(workspace_fs.root(_project(db, user, payload.project_id))) if payload.project_id else str(
        storage.run_dir() / "scratch")
    os.makedirs(cwd, exist_ok=True)
    result = runner.run_shell(cmd, cwd, home=str(workspace_fs.home_dir(
        _project(db, user, payload.project_id))) if payload.project_id else None)
    audit.log(db, user, "workspace.terminal_one_shot", "project", payload.project_id or "", request,
              command=cmd[:200], exit_code=result["exit_code"])
    return result
