"""Private server-side project storage. Never trust a path from the client."""
import io
import os
import shutil
import zipfile
from pathlib import Path

from app.core.config import settings

ROOT = Path(settings.STORAGE_ROOT)


def _ensure(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


def user_root(user_id: str) -> Path:
    return _ensure(ROOT / "users" / user_id)


def project_root(user_id: str, project_id: str) -> Path:
    return _ensure(user_root(user_id) / "projects" / project_id)


def source_dir(user_id: str, project_id: str) -> Path:
    return _ensure(project_root(user_id, project_id) / "source_files")


def assets_dir(user_id: str, project_id: str) -> Path:
    return _ensure(project_root(user_id, project_id) / "assets")


def versions_dir(user_id: str, project_id: str) -> Path:
    return _ensure(project_root(user_id, project_id) / "versions")


def submissions_dir(user_id: str, project_id: str) -> Path:
    return _ensure(project_root(user_id, project_id) / "submissions")


def recordings_dir(user_id: str) -> Path:
    return _ensure(user_root(user_id) / "recordings")


def chat_dir() -> Path:
    return _ensure(ROOT / "chat_uploads")


def run_dir() -> Path:
    return _ensure(ROOT / "runs")


def safe_join(root: Path, relative: str) -> Path:
    """Resolve `relative` inside `root`, rejecting traversal / absolute paths."""
    rel = (relative or "").replace("\\", "/").lstrip("/")
    if not rel:
        raise ValueError("Empty path")
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise ValueError("Path traversal blocked")
    resolved_root = root.resolve()
    target = (resolved_root / Path(*parts)).resolve()
    try:
        # Windows may return a short 8.3 path for an existing directory and a
        # long path for its children. Normalize both through the filesystem
        # before comparing so valid descendants are not mistaken for escapes.
        root_name = os.path.normcase(os.path.realpath(resolved_root))
        target_name = os.path.normcase(os.path.realpath(target))
        common = os.path.commonpath((root_name, target_name))
    except (OSError, ValueError):
        raise ValueError("Path escapes storage root")
    if common != root_name:
        raise ValueError("Path escapes storage root")
    return target


def write_text(user_id: str, project_id: str, rel_path: str, content: str) -> Path:
    target = safe_join(source_dir(user_id, project_id), rel_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


def read_text(user_id: str, project_id: str, rel_path: str) -> str:
    return safe_join(source_dir(user_id, project_id), rel_path).read_text(encoding="utf-8")


def delete_project_storage(user_id: str, project_id: str) -> None:
    shutil.rmtree(project_root(user_id, project_id), ignore_errors=True)


def write_snapshot(user_id: str, project_id: str, kind: str, name: str, files: list[dict]) -> str:
    """Materialise a file snapshot (versions/ or submissions/). Returns relative key."""
    base = versions_dir(user_id, project_id) if kind == "versions" else submissions_dir(user_id, project_id)
    folder = _ensure(base / name)
    for f in files:
        if f.get("is_dir"):
            continue
        target = safe_join(folder, f["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f.get("content") or "", encoding="utf-8")
    return str(folder.relative_to(ROOT))


def zip_bytes(files: list[dict]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            if f.get("is_dir") or not f.get("path"):
                continue
            zf.writestr(f["path"], f.get("content") or "")
    return buf.getvalue()
