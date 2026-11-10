"""Git integration bound to a project's workspace.

Only a fixed set of subcommands is allowed and every argument is passed as an argv
element — never through a shell — so a commit message cannot inject commands.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import ADMIN, get_current_user
from app.models import Project, User
from app.services import audit, workspace_fs

router = APIRouter(prefix="/workspace", tags=["Git"])


def _project(db: Session, user: User, project_id: str) -> Project:
    project = db.get(Project, project_id)
    if not project or project.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.owner_id != user.id and user.role != ADMIN:
        raise HTTPException(status_code=403, detail="No access to this project")
    return project


def _git(project: Project, args: list[str], user: User, timeout: int = 30) -> dict:
    cwd = str(workspace_fs.root(project))
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(workspace_fs.home_dir(project)),
        "GIT_AUTHOR_NAME": user.full_name,
        "GIT_AUTHOR_EMAIL": user.email,
        "GIT_COMMITTER_NAME": user.full_name,
        "GIT_COMMITTER_EMAIL": user.email,
        "GIT_TERMINAL_PROMPT": "0",
        "LANG": "C.UTF-8",
    }
    try:
        proc = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True,
                              text=True, timeout=timeout)
        return {"ok": proc.returncode == 0, "code": proc.returncode,
                "stdout": proc.stdout[-20000:], "stderr": proc.stderr[-20000:]}
    except FileNotFoundError:
        return {"ok": False, "code": 127, "stdout": "", "stderr": "git is not installed on this server"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "code": 124, "stdout": "", "stderr": f"git timed out after {timeout}s"}


def _is_repo(project: Project) -> bool:
    return (workspace_fs.root(project) / ".git").exists()


@router.get("/{project_id}/git/status")
def status(project_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    project = _project(db, user, project_id)
    if not _is_repo(project):
        return {"initialized": False, "branch": None, "changes": [], "staged": [], "unstaged": []}

    raw = _git(project, ["status", "--porcelain=v1", "-b"], user)
    branch, changes, ahead = None, [], 0
    for line in (raw["stdout"] or "").splitlines():
        if line.startswith("##"):
            head = line[2:].strip()
            branch = head.split("...")[0].replace("No commits yet on ", "").strip()
            if "ahead" in head:
                try:
                    ahead = int(head.split("ahead ")[1].split("]")[0].split(",")[0])
                except (IndexError, ValueError):
                    ahead = 0
            continue
        if len(line) < 3:
            continue
        code, path = line[:2], line[3:].strip()
        staged = code[0] not in (" ", "?")
        changes.append({
            "path": path, "code": code.strip() or "??",
            "staged": staged, "untracked": code == "??",
            "status": {"M": "modified", "A": "added", "D": "deleted", "R": "renamed",
                       "??": "untracked"}.get(code.strip(), "changed"),
        })
    return {
        "initialized": True, "branch": branch or "main", "ahead": ahead,
        "changes": changes,
        "staged": [c for c in changes if c["staged"]],
        "unstaged": [c for c in changes if not c["staged"]],
    }


@router.get("/{project_id}/git/log")
def log(project_id: str, limit: int = Query(30, ge=1, le=200), db: Session = Depends(get_db),
        user: User = Depends(get_current_user)):
    project = _project(db, user, project_id)
    if not _is_repo(project):
        return {"commits": []}
    raw = _git(project, ["log", f"-n{limit}", "--pretty=format:%H%x1f%an%x1f%ae%x1f%at%x1f%s"], user)
    commits = []
    for line in (raw["stdout"] or "").splitlines():
        parts = line.split("\x1f")
        if len(parts) == 5:
            commits.append({"hash": parts[0], "short": parts[0][:7], "author": parts[1],
                            "email": parts[2], "timestamp": int(parts[3]), "subject": parts[4]})
    return {"commits": commits}


@router.get("/{project_id}/git/diff")
def diff(project_id: str, path: str | None = Query(None), staged: bool = Query(False),
         db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    project = _project(db, user, project_id)
    if not _is_repo(project):
        return {"diff": ""}
    args = ["diff", "--no-color"]
    if staged:
        args.append("--cached")
    if path:
        args += ["--", path]
    if not path and not staged:
        args.append("HEAD") if _git(project, ["rev-parse", "--verify", "HEAD"], user)["ok"] else None
    result = _git(project, args, user)
    return {"diff": result["stdout"], "ok": result["ok"]}


@router.get("/{project_id}/git/branches")
def branches(project_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    project = _project(db, user, project_id)
    if not _is_repo(project):
        return {"branches": [], "current": None}
    raw = _git(project, ["branch", "--format=%(refname:short)"], user)
    current = _git(project, ["rev-parse", "--abbrev-ref", "HEAD"], user)["stdout"].strip()
    return {"branches": [b for b in (raw["stdout"] or "").splitlines() if b], "current": current}


class GitAction(BaseModel):
    action: str
    path: str | None = None
    paths: list[str] = []
    message: str | None = None
    branch: str | None = None
    remote: str | None = None
    url: str | None = None


ALLOWED = {"init", "add", "add_all", "unstage", "commit", "checkout", "checkout_new",
           "pull", "push", "reset", "remote_add", "branch"}


@router.post("/{project_id}/git")
def git_action(project_id: str, payload: GitAction, request: Request,
               db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    project = _project(db, user, project_id)
    if project.owner_id != user.id and user.role != "admin":
        raise HTTPException(
        status_code=403,
        detail="Only the project owner or admin can change version control"
    )
    if payload.action not in ALLOWED:
        raise HTTPException(status_code=422, detail=f"Unsupported git action: {payload.action}")

    a = payload.action
    if a == "init":
        result = _git(project, ["init", "-b", "main"], user)
    elif a == "add":
        files = payload.paths or ([payload.path] if payload.path else [])
        if not files:
            raise HTTPException(status_code=422, detail="No files specified")
        result = _git(project, ["add", "--", *files], user)
    elif a == "add_all":
        result = _git(project, ["add", "-A"], user)
    elif a == "unstage":
        files = payload.paths or ([payload.path] if payload.path else [])
        result = _git(project, ["restore", "--staged", "--", *files], user)
    elif a == "commit":
        if not (payload.message or "").strip():
            raise HTTPException(status_code=422, detail="Commit message is required")
        result = _git(project, ["commit", "-m", payload.message.strip()[:2000]], user)
    elif a == "checkout":
        if not payload.branch:
            raise HTTPException(status_code=422, detail="Branch is required")
        result = _git(project, ["checkout", payload.branch], user)
    elif a == "checkout_new":
        if not payload.branch:
            raise HTTPException(status_code=422, detail="Branch name is required")
        result = _git(project, ["checkout", "-b", payload.branch], user)
    elif a == "branch":
        result = _git(project, ["branch"], user)
    elif a == "remote_add":
        if not payload.url:
            raise HTTPException(status_code=422, detail="Remote URL is required")
        result = _git(project, ["remote", "add", payload.remote or "origin", payload.url], user)
    elif a == "pull":
        result = _git(project, ["pull", "--no-rebase", payload.remote or "origin"], user, timeout=90)
    elif a == "push":
        result = _git(project, ["push", "-u", payload.remote or "origin", "HEAD"], user, timeout=90)
    else:  # reset
        result = _git(project, ["reset", "HEAD", "--", payload.path] if payload.path else ["reset"], user)

    workspace_fs.sync_index(db, project)
    audit.log(db, user, "git.action", "project", project.id, request,
              git_action=a, ok=result["ok"], branch=payload.branch)
    return result
