"""Projects: metadata in PostgreSQL, source code in private server storage."""
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import ADMIN, DEVELOPERS, get_current_user
from app.models import Project, ProjectFile, ProjectVersion, TeamMember, User
from app.schemas import FileIn, FileOut, ProjectIn, ProjectOut, SaveProjectIn
from app.services import audit, storage

router = APIRouter(prefix="/projects", tags=["Projects"])


def get_project_for(db: Session, user: User, project_id: str) -> Project:
    """Ownership/authorization check — never trust an id from the client."""
    project = db.get(Project, project_id)
    if not project or project.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Project not found")
    if user.role == ADMIN or project.owner_id == user.id:
        return project
    if user.role in DEVELOPERS and project.team_id:
        member = db.query(TeamMember).filter(TeamMember.team_id == project.team_id,
                                             TeamMember.user_id == user.id).one_or_none()
        if member:
            return project
    raise HTTPException(status_code=403, detail="You do not have access to this project")


def _files_payload(db: Session, project_id: str) -> list[dict]:
    rows = db.query(ProjectFile).filter(ProjectFile.project_id == project_id).order_by(ProjectFile.path).all()
    return [{"path": f.path, "content": f.content, "is_dir": f.is_dir} for f in rows]


def _snapshot_version(db: Session, project: Project, user: User, label: str = "") -> ProjectVersion:
    files = _files_payload(db, project.id)
    project.current_version += 1
    ver = ProjectVersion(project_id=project.id, version_no=project.current_version,
                         label=label or f"v{project.current_version}",
                         snapshot=json.dumps(files), created_by=user.id)
    db.add(ver)
    db.flush()
    try:
        storage.write_snapshot(project.owner_id, project.id, "versions",
                               f"v{ver.version_no:04d}", files)
    except OSError:
        pass
    return ver


@router.get("", response_model=list[ProjectOut])
def list_projects(db: Session = Depends(get_db), user: User = Depends(get_current_user),
                  include_archived: bool = Query(False)):
    query = db.query(Project).filter(Project.deleted_at.is_(None))
    if user.role != ADMIN:
        team_ids = [m.team_id for m in db.query(TeamMember).filter(TeamMember.user_id == user.id)]
        team_ids.extend(team.id for team in db.query(__import__("app.models", fromlist=["Team"]).Team)
                        .filter_by(lead_user_id=user.id))
        conds = [Project.owner_id == user.id]
        if team_ids and user.role in DEVELOPERS:
            conds.append(Project.team_id.in_(team_ids))
        query = query.filter(conds[0]) if len(conds) == 1 else query.filter(or_(*conds))
    if not include_archived:
        query = query.filter(Project.is_archived.is_(False))
    return query.order_by(Project.updated_at.desc()).all()


@router.post("", response_model=ProjectOut, status_code=201)
def create_project(payload: ProjectIn, request: Request, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    proj = Project(owner_id=user.id, name=payload.name.strip(), description=payload.description,
                   language=payload.language, team_id=payload.team_id,
                   storage_key=f"users/{user.id}/projects/TBD")
    db.add(proj)
    try:
        db.flush()
    except IntegrityError:
        raise HTTPException(status_code=409, detail="You already have a project with that name")

    proj.storage_key = f"users/{user.id}/projects/{proj.id}"
    storage.source_dir(user.id, proj.id)

    starter = {
        "python": ("main.py", 'print("Hello from WORKCODE 👋")\n'),
        "javascript": ("main.js", 'console.log("Hello from WORKCODE 👋");\n'),
        "typescript": ("main.ts", 'const msg: string = "Hello from WORKCODE";\nconsole.log(msg);\n'),
        "html": ("index.html", "<!doctype html>\n<html>\n<head><title>WORKCODE</title>"
                               "<style>body{font-family:system-ui;padding:40px}</style></head>\n"
                               "<body><h1>Hello from WORKCODE</h1></body>\n</html>\n"),
        "sql": ("main.sql", "CREATE TABLE demo (id INTEGER PRIMARY KEY, name TEXT);\n"
                            "INSERT INTO demo (name) VALUES ('alice'), ('bob');\nSELECT * FROM demo;\n"),
    }.get(payload.language, ("main.txt", "New WORKCODE project\n"))

    db.add(ProjectFile(project_id=proj.id, path=starter[0], content=starter[1],
                       size=len(starter[1])))
    _snapshot_version(db, proj, user, label="Initial version")
    audit.log(db, user, "project.created", "project", proj.id, request, language=payload.language)
    db.flush()
    return proj


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(project_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return get_project_for(db, user, project_id)


@router.patch("/{project_id}", response_model=ProjectOut)
def update_project(project_id: str, payload: ProjectIn, request: Request,
                   db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    proj = get_project_for(db, user, project_id)
    if user.role != ADMIN and proj.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Only the owner can rename this project")
    proj.name = payload.name.strip() or proj.name
    proj.description = payload.description
    proj.language = payload.language or proj.language
    audit.log(db, user, "project.updated", "project", proj.id, request)
    db.flush()
    return proj


@router.delete("/{project_id}")
def delete_project(project_id: str, request: Request, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    """Soft delete: metadata + storage are retained per the retention policy."""
    proj = get_project_for(db, user, project_id)
    if user.role != ADMIN and proj.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Only the owner can delete this project")
    proj.deleted_at = datetime.now(timezone.utc)
    proj.is_archived = True
    audit.log(db, user, "project.deleted", "project", proj.id, request, mode="soft")
    return {"detail": "Project moved to trash (recoverable by an admin)"}


# ----------------------------------------------------------------------- files
@router.get("/{project_id}/files", response_model=list[FileOut])
def list_files(project_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    proj = get_project_for(db, user, project_id)
    proj.last_opened_at = datetime.now(timezone.utc)
    return db.query(ProjectFile).filter(ProjectFile.project_id == proj.id).order_by(ProjectFile.path).all()


@router.put("/{project_id}/files", response_model=FileOut)
def write_file(project_id: str, payload: FileIn, request: Request, db: Session = Depends(get_db),
               user: User = Depends(get_current_user)):
    proj = get_project_for(db, user, project_id)
    if user.role != ADMIN and proj.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Read-only access to this project")

    path = payload.path.replace("\\", "/").lstrip("/")
    if not path or ".." in path.split("/"):
        raise HTTPException(status_code=422, detail="Invalid file path")

    row = db.query(ProjectFile).filter(ProjectFile.project_id == proj.id,
                                       ProjectFile.path == path).one_or_none()
    if row:
        row.content = payload.content
        row.size = len(payload.content.encode())
        if payload.is_dir:
            row.is_dir = True
    else:
        row = ProjectFile(project_id=proj.id, path=path, content=payload.content,
                          is_dir=payload.is_dir, size=len(payload.content.encode()))
        db.add(row)
    try:
        if not payload.is_dir:
            storage.write_text(proj.owner_id, proj.id, path, payload.content)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"Storage error: {exc}")
    db.flush()
    return row


@router.delete("/{project_id}/files")
def delete_file(project_id: str, path: str = Query(...), db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    proj = get_project_for(db, user, project_id)
    if user.role != ADMIN and proj.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Read-only access")
    row = db.query(ProjectFile).filter(ProjectFile.project_id == proj.id,
                                       ProjectFile.path == path).one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="File not found")
    db.delete(row)
    return {"detail": "File deleted"}


@router.post("/{project_id}/save", response_model=ProjectOut)
def save_project(project_id: str, payload: SaveProjectIn, request: Request,
                 db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Save all files + create an immutable version snapshot."""
    proj = get_project_for(db, user, project_id)
    if user.role != ADMIN and proj.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Read-only access")

    existing = {f.path: f for f in db.query(ProjectFile).filter(ProjectFile.project_id == proj.id)}
    incoming = set()
    for f in payload.files:
        path = f.path.replace("\\", "/").lstrip("/")
        if not path or ".." in path.split("/"):
            continue
        incoming.add(path)
        row = existing.get(path)
        if row:
            row.content, row.size, row.is_dir = f.content, len(f.content.encode()), f.is_dir
        else:
            db.add(ProjectFile(project_id=proj.id, path=path, content=f.content,
                               is_dir=f.is_dir, size=len(f.content.encode())))
    for path, row in existing.items():  # files removed in the editor
        if path not in incoming:
            db.delete(row)

    db.flush()
    for f in payload.files:
        if not f.is_dir:
            try:
                storage.write_text(proj.owner_id, proj.id, f.path, f.content)
            except (OSError, ValueError):
                pass
    ver = _snapshot_version(db, proj, user, label=payload.label or "Manual save")
    audit.log(db, user, "project.saved", "project", proj.id, request, version=ver.version_no,
              files=len(payload.files))
    db.flush()
    return proj


# -------------------------------------------------------------------- versions
@router.get("/{project_id}/versions")
def list_versions(project_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    proj = get_project_for(db, user, project_id)
    rows = (db.query(ProjectVersion).filter(ProjectVersion.project_id == proj.id)
            .order_by(ProjectVersion.version_no.desc()).limit(100).all())
    return [{"id": v.id, "version_no": v.version_no, "label": v.label,
             "created_at": v.created_at, "created_by": v.created_by,
             "file_count": len(json.loads(v.snapshot or "[]"))} for v in rows]


@router.post("/{project_id}/versions/{version_no}/restore", response_model=ProjectOut)
def restore_version(project_id: str, version_no: int, request: Request,
                    db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    proj = get_project_for(db, user, project_id)
    if user.role != ADMIN and proj.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Read-only access")
    ver = db.query(ProjectVersion).filter(ProjectVersion.project_id == proj.id,
                                          ProjectVersion.version_no == version_no).one_or_none()
    if not ver:
        raise HTTPException(status_code=404, detail="Version not found")

    files = json.loads(ver.snapshot or "[]")
    db.query(ProjectFile).filter(ProjectFile.project_id == proj.id).delete()
    for f in files:
        db.add(ProjectFile(project_id=proj.id, path=f["path"], content=f.get("content", ""),
                           is_dir=f.get("is_dir", False), size=len((f.get("content") or "").encode())))
        if not f.get("is_dir"):
            try:
                storage.write_text(proj.owner_id, proj.id, f["path"], f.get("content") or "")
            except (OSError, ValueError):
                pass
    db.flush()
    _snapshot_version(db, proj, user, label=f"Restored from v{version_no}")
    audit.log(db, user, "project.version_restored", "project", proj.id, request, version=version_no)
    db.flush()
    return proj


@router.get("/{project_id}/download")
def download_zip(project_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    proj = get_project_for(db, user, project_id)
    files = _files_payload(db, proj.id)
    data = storage.zip_bytes(files)
    safe_name = "".join(c for c in proj.name if c.isalnum() or c in "-_ ").strip() or "project"
    audit.log(db, user, "project.downloaded", "project", proj.id)
    return Response(content=data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{safe_name}.zip"'})

