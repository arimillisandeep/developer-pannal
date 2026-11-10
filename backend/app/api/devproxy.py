"""Reverse proxy that puts a live dev server (Vite / React / Express) in the IDE preview.

Why a proxy at all: a dev server serves modules on demand and rewrites imports at
request time, so you cannot just copy its output the way you copy ``dist/``.

The token and server id live in the *path*, not the query string, because a browser
resolves ``<script src="./x.js">`` against the directory and would drop ``?token=``.
"""
from __future__ import annotations

import re

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.api.workspace import _preview_user
from app.core.db import get_db
from app.models import Project
from app.services import devserver

router = APIRouter(prefix="/workspace", tags=["Dev proxy"])

MAX_BYTES = 12 * 1024 * 1024
HOP = {"connection", "keep-alive", "transfer-encoding", "content-encoding", "content-length",
       "host", "authorization", "accept-encoding", "upgrade"}

# Absolute-root URLs a dev server emits ("/src/main.jsx", "/@vite/client") have to be
# re-rooted under the proxy prefix or the iframe would fetch them from WORKCODE itself.
_ATTR = re.compile(r'(src|href|action|poster|data-src)=(["\'])/(?!/)(?!api/workspace/)')
_SPEC = re.compile(r'(from|import)\s*(["\'])/(?!/)(?!api/workspace/)')
_VITE = re.compile(r'(["\'])/(?=@vite|@react-refresh|@id/|@fs/|src/|node_modules/|assets/|static/)')
_CSS = re.compile(r'url\(\s*(["\']?)/(?!/)(?!api/workspace/)')


def _rewrite_text(text: str, prefix: str, css: bool = False) -> str:
    if css:
        return _CSS.sub(lambda m: f"url({m.group(1)}{prefix}", text)
    text = _ATTR.sub(lambda m: f"{m.group(1)}={m.group(2)}{prefix}", text)
    text = _SPEC.sub(lambda m: f"{m.group(1)} {m.group(2)}{prefix}", text)
    text = _VITE.sub(lambda m: f"{m.group(1)}{prefix}", text)
    return text


def _is_textual(ctype: str) -> bool:
    return any(t in ctype for t in ("text/html", "javascript", "ecmascript", "text/css", "application/json", "text/plain"))


@router.api_route("/{project_id}/devproxy/{server_id}/{token}/{path:path}",
                  methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
                  include_in_schema=False)
async def devproxy(project_id: str, server_id: str, token: str, path: str,
                   request: Request, db: Session = Depends(get_db)):
    user = _preview_user(request, token, db)
    project = db.get(Project, project_id)
    if not project or project.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Project not found")
    if user.role != "admin" and project.owner_id != user.id:
        raise HTTPException(status_code=403, detail="You do not have access to this project")

    item = devserver.processes.get(server_id)
    if not item or item.project_id != project_id or not item.port:
        raise HTTPException(status_code=404, detail="No such running server")
    if item.status in ("stopped", "exited"):
        raise HTTPException(status_code=409, detail="That dev server is not running")

    prefix = f"/api/workspace/{project_id}/devproxy/{server_id}/{token}/"
    target = f"http://127.0.0.1:{item.port}/{path}"
    if request.url.query:
        target += f"?{request.url.query}"

    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP}
    body = await request.body()
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=False) as client:
            upstream = await client.request(request.method, target, headers=headers,
                                            content=body or None)
    except httpx.ConnectError:
        raise HTTPException(status_code=502, detail="Dev server is not accepting connections yet")
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Dev server timed out")

    ctype = upstream.headers.get("content-type", "application/octet-stream")
    raw = upstream.content[:MAX_BYTES]
    if _is_textual(ctype) and raw:
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = None
        if text is not None:
            raw = _rewrite_text(text, prefix, css="text/css" in ctype).encode("utf-8")

    out = {k: v for k, v in upstream.headers.items()
           if k.lower() not in HOP and k.lower() != "set-cookie"}
    out["cache-control"] = "no-store"
    out["x-frame-options"] = "SAMEORIGIN"
    out["content-type"] = ctype
    return Response(content=raw, status_code=upstream.status_code, headers=out)
