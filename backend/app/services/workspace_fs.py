"""Disk-first workspace file system.

The persistent workspace on disk (``storage/users/<uid>/projects/<pid>/source_files``)
is the single source of truth. PostgreSQL ``project_files`` is an index kept in sync
so that versions, submissions and diffing keep working — and so that files created
inside the terminal (``npm create vite``) show up in the explorer.

Every path is validated against the project root; nothing escapes it.
"""
from __future__ import annotations

import os
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from app.models import Project, ProjectFile
from app.services import storage

IGNORED_DIRS = {"node_modules", ".git", "__pycache__", ".venv", "venv", "dist",
                ".python", ".next", ".cache", ".pytest_cache", ".mypy_cache", "target", "build"}
MAX_INDEX_BYTES = 512 * 1024
MAX_INDEX_FILES = 4000


# --------------------------------------------------------------------- helpers
def root(project: Project) -> Path:
    return storage.source_dir(project.owner_id, project.id)


def home_dir(project: Project) -> Path:
    return storage._ensure(storage.user_root(project.owner_id) / "home")


def safe(project: Project, rel: str) -> Path:
    rel = (rel or "").replace("\\", "/").strip()
    if rel in ("", ".", "/"):
        return root(project)
    return storage.safe_join(root(project), rel)


def rel_of(project: Project, path: Path) -> str:
    return os.path.relpath(os.path.realpath(path), os.path.realpath(root(project))).replace("\\", "/")


def _ignored(rel: str) -> bool:
    return any(part in IGNORED_DIRS for part in rel.split("/"))


# ------------------------------------------------------------------- read side
def tree(project: Project, include_ignored: bool = False) -> list[dict]:
    base = root(project)
    base.mkdir(parents=True, exist_ok=True)
    entries: list[dict] = []
    for dirpath, dirnames, filenames in os.walk(base):
        rel_dir = os.path.relpath(dirpath, base).replace("\\", "/")
        rel_dir = "" if rel_dir == "." else rel_dir
        if not include_ignored:
            dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS]
        dirnames.sort()
        for d in dirnames:
            rel = f"{rel_dir}/{d}".lstrip("/")
            entries.append({"path": rel, "name": d, "is_dir": True, "size": 0,
                            "ext": "", "modified": None})
        for f in sorted(filenames):
            rel = f"{rel_dir}/{f}".lstrip("/")
            try:
                stat = os.stat(os.path.join(dirpath, f))
                size, mtime = stat.st_size, datetime.fromtimestamp(stat.st_mtime, timezone.utc)
            except OSError:
                continue
            entries.append({
                "path": rel, "name": f, "is_dir": False, "size": size,
                "ext": Path(f).suffix.lstrip(".").lower(),
                "modified": mtime.isoformat(),
            })
    entries.sort(key=lambda e: (e["path"].count("/"), not e["is_dir"], e["name"].lower()))
    return entries


def read(project: Project, rel: str) -> dict:
    path = safe(project, rel)
    if not path.exists() or path.is_dir():
        raise FileNotFoundError(rel)
    if path.stat().st_size > 5 * 1024 * 1024:
        raise ValueError("File is larger than 5 MB — download it instead")
    try:
        return {"path": rel, "content": path.read_text(encoding="utf-8"), "binary": False,
                "size": path.stat().st_size}
    except UnicodeDecodeError:
        import base64
        return {"path": rel, "content": base64.b64encode(path.read_bytes()).decode(),
                "binary": True, "size": path.stat().st_size}


# ------------------------------------------------------------------ write side
def write(project: Project, rel: str, content: str) -> dict:
    path = safe(project, rel)
    if path.is_dir():
        raise IsADirectoryError(rel)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return {"path": rel_of(project, path), "size": path.stat().st_size}


def mkdir(project: Project, rel: str) -> dict:
    path = safe(project, rel)
    path.mkdir(parents=True, exist_ok=True)
    return {"path": rel_of(project, path), "is_dir": True}


def rename(project: Project, old: str, new: str) -> dict:
    src, dst = safe(project, old), safe(project, new)
    if not src.exists():
        raise FileNotFoundError(old)
    if dst.exists():
        raise FileExistsError(new)
    dst.parent.mkdir(parents=True, exist_ok=True)
    src.rename(dst)
    return {"from": old, "to": rel_of(project, dst)}


def delete(project: Project, rel: str) -> dict:
    path = safe(project, rel)
    if path == root(project):
        raise ValueError("Refusing to delete the project root")
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()
    else:
        raise FileNotFoundError(rel)
    return {"deleted": rel}


def copy(project: Project, src_rel: str, dst_rel: str) -> dict:
    src, dst = safe(project, src_rel), safe(project, dst_rel)
    if not src.exists():
        raise FileNotFoundError(src_rel)
    if dst.exists():
        raise FileExistsError(dst_rel)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(src, dst)
    else:
        shutil.copy2(src, dst)
    return {"from": src_rel, "to": rel_of(project, dst)}


def duplicate(project: Project, rel: str) -> dict:
    src = safe(project, rel)
    if not src.exists():
        raise FileNotFoundError(rel)
    if src.is_dir():
        target = f"{rel.rstrip('/')}-copy"
        i = 2
        while safe(project, target).exists():
            target = f"{rel.rstrip('/')}-copy-{i}"
            i += 1
        return copy(project, rel, target)
    stem, suffix = os.path.splitext(rel)
    target, i = f"{stem}-copy{suffix}", 2
    while safe(project, target).exists():
        target, i = f"{stem}-copy-{i}{suffix}", i + 1
    return copy(project, rel, target)


def save_upload(project: Project, rel_dir: str, filename: str, data: bytes) -> dict:
    clean = "".join(c for c in filename if c.isalnum() or c in "-_. ()[]").strip() or "upload.bin"
    rel = f"{rel_dir.rstrip('/')}/{clean}".lstrip("/")
    path = safe(project, rel)
    if path.exists():
        stem, suffix = os.path.splitext(rel)
        i = 2
        while safe(project, f"{stem}-{i}{suffix}").exists():
            i += 1
        rel = f"{stem}-{i}{suffix}"
        path = safe(project, rel)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return {"path": rel, "size": len(data)}


def search(project: Project, query: str, limit: int = 60) -> list[dict]:
    """Filename + content search, skipping ignored folders and binaries."""
    q = (query or "").strip().lower()
    if not q:
        return []
    hits: list[dict] = []
    for entry in tree(project):
        if entry["is_dir"]:
            continue
        rel = entry["path"]
        if q in entry["name"].lower():
            hits.append({"path": rel, "line": 0, "text": entry["name"], "kind": "filename"})
        if entry["size"] > MAX_INDEX_BYTES or len(hits) >= limit:
            continue
        try:
            text = safe(project, rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if q in line.lower():
                hits.append({"path": rel, "line": number, "text": line.strip()[:200], "kind": "content"})
                break
        if len(hits) >= limit:
            break
    return hits[:limit]


def zip_project(project: Project) -> bytes:
    import io
    base = root(project)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS]
            for f in filenames:
                full = Path(dirpath) / f
                try:
                    zf.write(full, os.path.relpath(os.path.realpath(full), os.path.realpath(base)))
                except OSError:
                    continue
    return buf.getvalue()


# ------------------------------------------------------- index <-> disk sync
def materialize(db: Session, project: Project) -> int:
    """Write DB-indexed files to disk when the workspace is missing them."""
    base = root(project)
    base.mkdir(parents=True, exist_ok=True)
    written = 0
    for row in db.query(ProjectFile).filter(ProjectFile.project_id == project.id).all():
        try:
            path = safe(project, row.path)
        except ValueError:
            continue
        if row.is_dir:
            path.mkdir(parents=True, exist_ok=True)
            continue
        if not path.exists() and row.content:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(row.content, encoding="utf-8")
            written += 1
    return written


def sync_index(db: Session, project: Project) -> dict:
    """Reconcile the DB index with the real files (terminal-created files included)."""
    base = root(project)
    base.mkdir(parents=True, exist_ok=True)
    existing = {r.path: r for r in db.query(ProjectFile).filter(ProjectFile.project_id == project.id)}
    seen: set[str] = set()
    added = updated = removed = 0

    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS]
        rel_dir = os.path.relpath(dirpath, base).replace("\\", "/")
        rel_dir = "" if rel_dir == "." else rel_dir
        for d in dirnames:
            rel = f"{rel_dir}/{d}".lstrip("/")
            seen.add(rel)
            if rel not in existing:
                db.add(ProjectFile(project_id=project.id, path=rel, content="", is_dir=True, size=0))
                added += 1
        for f in filenames:
            rel = f"{rel_dir}/{f}".lstrip("/")
            if rel in seen:
                continue
            seen.add(rel)
            try:
                stat = os.stat(os.path.join(dirpath, f))
            except OSError:
                continue
            content = ""
            if stat.st_size <= MAX_INDEX_BYTES:
                try:
                    content = (Path(dirpath) / f).read_text(encoding="utf-8")
                except (UnicodeDecodeError, OSError):
                    content = ""
            row = existing.get(rel)
            if row is None:
                if len(seen) <= MAX_INDEX_FILES:
                    db.add(ProjectFile(project_id=project.id, path=rel, content=content,
                                       is_dir=False, size=stat.st_size))
                    added += 1
            else:
                if row.content != content or row.size != stat.st_size:
                    row.content, row.size = content, stat.st_size
                    updated += 1
                if row.is_dir:
                    row.is_dir = False

    for rel, row in existing.items():
        if rel not in seen and not row.is_dir:
            db.delete(row)
            removed += 1
    db.flush()
    return {"added": added, "updated": updated, "removed": removed, "files": len(seen)}


# ---------------------------------------------------------------- entry points
def entry_point(project: Project) -> str | None:
    base = root(project)
    lang = (project.language or "").lower()

    def first(*candidates):
        for c in candidates:
            if c and (base / c).exists():
                return c
        return None

    if lang in ("html", "css", "react", "vue", "angular"):
        return first("index.html", "public/index.html", "dist/index.html", "src/index.html", "main.html")
    if lang in ("javascript", "node", "express", "typescript"):
        pkg = base / "package.json"
        if pkg.exists():
            import json
            try:
                data = json.loads(pkg.read_text())
                entry = (data.get("main") or "").lstrip("./")
                if entry and (base / entry).exists():
                    return entry
                if "dev" in (data.get("scripts") or {}):
                    return "package.json"
            except (ValueError, OSError):
                pass
        return first("src/index.js", "index.js", "main.js", "app.js", "server.js", "src/main.ts", "main.ts")
    if lang == "python":
        return first("main.py", "app.py", "run.py", "src/main.py", "manage.py", "__main__.py")
    if lang == "java":
        for candidate in sorted(base.rglob("*.java")):
            return rel_of(project, candidate)
        return None
    if lang == "c":
        return first("main.c", "src/main.c")
    if lang == "cpp":
        return first("main.cpp", "src/main.cpp")
    if lang == "php":
        return first("index.php", "main.php")
    if lang == "go":
        return first("main.go", "cmd/main.go")
    if lang == "rust":
        return first("src/main.rs", "main.rs")
    if lang == "sql":
        return first("main.sql", "schema.sql", "queries.sql")
    if lang == "shell":
        return first("main.sh", "script.sh", "run.sh")
    if lang == "ruby":
        return first("main.rb", "app.rb")
    if lang == "csharp":
        return first("Program.cs")
    return first("main.py", "index.js", "index.html", "main.c")
