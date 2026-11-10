"""Screen recordings: consent, chunked upload, admin viewing, retention."""
import hashlib
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.core.deps import ADMIN, get_current_user
from app.models import Project, RecordingChunk, ScreenRecording, Task, Team, TimerSession, User
from app.schemas import RecordingOut, RecordingStartIn
from app.services import audit, storage
from app.services.notify import notify_admins

router = APIRouter(prefix="/recordings", tags=["Screen Recordings"])
bearer = HTTPBearer(auto_error=False)


def _can_view_recording(db: Session, user: User, recording: ScreenRecording) -> bool:
    if user.role == ADMIN or recording.user_id == user.id:
        return True
    if user.role != "developer_onroll" or not recording.task_id:
        return False
    task = db.get(Task, recording.task_id)
    if not task:
        return False
    if task.assigned_by == user.id:
        return True
    team = db.get(Team, task.team_id) if task.team_id else None
    return bool(team and team.lead_user_id == user.id)


def _require_recording_view(db: Session, user: User, recording: ScreenRecording) -> None:
    if not _can_view_recording(db, user, recording):
        raise HTTPException(status_code=403, detail="Access denied")


@router.post("/start", response_model=RecordingOut, status_code=201)
def start_recording(payload: RecordingStartIn, request: Request, db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    if user.role == ADMIN:
        raise HTTPException(status_code=403, detail="Admins do not record sessions")
    if not payload.consent:
        raise HTTPException(status_code=422,
                            detail="Explicit screen-share consent is required before recording starts")
    if not payload.mime_type.lower().startswith("video/webm"):
        raise HTTPException(status_code=422, detail="Only browser WebM screen recordings are supported")
    if payload.session_id:
        session = db.get(TimerSession, payload.session_id)
        if not session or session.user_id != user.id:
            raise HTTPException(status_code=404, detail="Coding session not found")
    if payload.project_id:
        project = db.get(Project, payload.project_id)
        if not project or project.deleted_at is not None:
            raise HTTPException(status_code=404, detail="Project not found")
        if project.owner_id != user.id:
            raise HTTPException(status_code=403, detail="You do not have access to this project")
    if payload.task_id:
        task = db.get(Task, payload.task_id)
        if not task or task.assignee_id != user.id:
            raise HTTPException(status_code=404, detail="Task not found")

    rec = ScreenRecording(
        user_id=user.id, session_id=payload.session_id, task_id=payload.task_id,
        project_id=payload.project_id, status="recording", mime_type=payload.mime_type,
        consent_given=True,
        retention_until=datetime.now(timezone.utc) + timedelta(days=settings.RECORDING_RETENTION_DAYS),
    )
    db.add(rec)
    db.flush()
    rec.storage_key = str(storage.recordings_dir(user.id).relative_to(storage.ROOT) / rec.id)
    (storage.recordings_dir(user.id) / rec.id).mkdir(parents=True, exist_ok=True)
    audit.log(db, user, "recording.started", "recording", rec.id, request, consent=True)
    db.flush()
    return rec


@router.post("/{recording_id}/chunks")
async def upload_chunk(recording_id: str, seq: int = Form(..., ge=0), chunk: UploadFile = File(...),
                       db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rec = db.get(ScreenRecording, recording_id)
    if not rec or rec.user_id != user.id:
        raise HTTPException(status_code=404, detail="Recording not found")
    if rec.status not in ("recording", "uploading"):
        raise HTTPException(status_code=409, detail="Recording is closed")

    max_bytes = settings.MAX_UPLOAD_MB * 1024 * 1024
    data = await chunk.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise HTTPException(status_code=413, detail="Chunk too large")

    folder = storage.recordings_dir(user.id) / rec.id
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"chunk-{seq:06d}.webm"
    target.write_bytes(data)

    existing = db.query(RecordingChunk).filter(RecordingChunk.recording_id == rec.id,
                                               RecordingChunk.seq == seq).one_or_none()
    previous_size = existing.size_bytes if existing else 0
    if existing:
        existing.size_bytes = len(data)
        existing.sha256 = hashlib.sha256(data).hexdigest()
    else:
        db.add(RecordingChunk(recording_id=rec.id, seq=seq, size_bytes=len(data),
                              sha256=hashlib.sha256(data).hexdigest(),
                              storage_key=str(target.relative_to(storage.ROOT))))
        rec.chunk_count = (rec.chunk_count or 0) + 1
    rec.size_bytes = max(0, (rec.size_bytes or 0) - previous_size) + len(data)
    db.flush()
    return {"seq": seq, "received": len(data), "chunks": rec.chunk_count}


@router.post("/{recording_id}/complete", response_model=RecordingOut)
def complete_recording(recording_id: str, duration_seconds: int = Query(0, ge=0),
                       request: Request = None, db: Session = Depends(get_db),
                       user: User = Depends(get_current_user)):
    rec = db.get(ScreenRecording, recording_id)
    if not rec or rec.user_id != user.id:
        raise HTTPException(status_code=404, detail="Recording not found")
    if rec.status != "recording" or (rec.chunk_count or 0) == 0:
        raise HTTPException(status_code=409, detail="Upload at least one recording chunk before completing")
    chunk_count, first_seq, last_seq = (
        db.query(func.count(RecordingChunk.id), func.min(RecordingChunk.seq),
                 func.max(RecordingChunk.seq))
        .filter(RecordingChunk.recording_id == rec.id).one()
    )
    if chunk_count != rec.chunk_count or first_seq != 0 or last_seq != chunk_count - 1:
        raise HTTPException(status_code=409, detail="Recording chunks are incomplete; retry missing chunks before completion")
    rec.status = "completed"
    rec.ended_at = datetime.now(timezone.utc)
    rec.duration_seconds = duration_seconds
    audit.log(db, user, "recording.completed", "recording", rec.id, request,
              duration=duration_seconds, chunks=rec.chunk_count)
    notify_admins(db, "recording", "New screen recording available",
                  f"{user.full_name} uploaded a {duration_seconds // 60} min recording.",
                  f"/admin/recordings/{rec.id}")
    db.flush()
    return rec


@router.get("", response_model=list[RecordingOut])
def list_recordings(
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
    user_id: str | None = Query(None), role: str | None = Query(None),
    task_id: str | None = Query(None), project_id: str | None = Query(None),
    date_from: date | None = Query(None), date_to: date | None = Query(None),
    min_duration: int | None = Query(None), page: int = Query(1, ge=1), size: int = Query(50, ge=1, le=200),
):
    query = db.query(ScreenRecording).filter(ScreenRecording.deleted_at.is_(None))
    if user.role == "developer_onroll":
        review_tasks = (db.query(Task.id).outerjoin(Team, Task.team_id == Team.id)
                       .filter((Task.assigned_by == user.id) | (Team.lead_user_id == user.id)))
        query = query.filter(or_(ScreenRecording.user_id == user.id,
                                 ScreenRecording.task_id.in_(review_tasks)))
    elif user.role != ADMIN:
        query = query.filter(ScreenRecording.user_id == user.id)
    else:
        if user_id:
            query = query.filter(ScreenRecording.user_id == user_id)
        if role:
            ids = [u.id for u in db.query(User).filter(User.role == role)]
            query = query.filter(ScreenRecording.user_id.in_(ids))
    if task_id:
        query = query.filter(ScreenRecording.task_id == task_id)
    if project_id:
        query = query.filter(ScreenRecording.project_id == project_id)
    if date_from:
        query = query.filter(ScreenRecording.started_at >= datetime.combine(date_from, datetime.min.time(),
                                                                          tzinfo=timezone.utc))
    if date_to:
        query = query.filter(ScreenRecording.started_at <= datetime.combine(date_to, datetime.max.time(),
                                                                          tzinfo=timezone.utc))
    if min_duration:
        query = query.filter(ScreenRecording.duration_seconds >= min_duration)
    return (query.order_by(ScreenRecording.started_at.desc())
            .offset((page - 1) * size).limit(size).all())


@router.get("/{recording_id}")
def get_recording(recording_id: str, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    rec = db.get(ScreenRecording, recording_id)
    if not rec or rec.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Recording not found")
    _require_recording_view(db, user, rec)
    owner = db.get(User, rec.user_id)
    chunk_rows = (db.query(RecordingChunk).filter(RecordingChunk.recording_id == rec.id)
                  .order_by(RecordingChunk.seq).all())
    if user.role == ADMIN or user.id != rec.user_id:
        audit.log(db, user, "recording.viewed", "recording", rec.id)
    return {
        "id": rec.id, "user_id": rec.user_id,
        "user_name": owner.full_name if owner else None,
        "user_role": owner.role if owner else None,
        "status": rec.status, "started_at": rec.started_at, "ended_at": rec.ended_at,
        "duration_seconds": rec.duration_seconds, "size_bytes": rec.size_bytes,
        "chunk_count": rec.chunk_count, "mime_type": rec.mime_type,
        "retention_until": rec.retention_until, "task_id": rec.task_id,
        "project_id": rec.project_id, "consent_given": rec.consent_given,
        "chunks": [{"seq": c.seq, "size_bytes": c.size_bytes} for c in chunk_rows],
    }


@router.get("/{recording_id}/playback")
def playback_link(recording_id: str, request: Request, response: Response,
                  credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
                  db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Set a short-lived, path-scoped cookie for the browser's native video request."""
    rec = db.get(ScreenRecording, recording_id)
    if not rec or rec.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Recording not found")
    _require_recording_view(db, user, rec)
    token = credentials.credentials if credentials else request.cookies.get("access_token")
    if not token:
        raise HTTPException(status_code=401, detail="Authenticated playback requires a session")
    video_path = f"{settings.API_PREFIX}/recordings/{rec.id}/video"
    response.set_cookie(
        "access_token", token, httponly=True, secure=request.url.scheme == "https",
        samesite="lax", max_age=600, path=video_path,
    )
    if user.role == ADMIN or user.id != rec.user_id:
        audit.log(db, user, "recording.viewed", "recording", rec.id, request)
    return {"url": video_path}


@router.get("/{recording_id}/video")
def stream_video(recording_id: str, db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    rec = db.get(ScreenRecording, recording_id)
    if not rec or rec.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Recording not found")
    _require_recording_view(db, user, rec)
    folder = storage.recordings_dir(rec.user_id) / rec.id
    chunks = sorted(folder.glob("chunk-*.webm")) if folder.exists() else []

    def _iter():
        for c in chunks:
            with open(c, "rb") as fh:
                while True:
                    block = fh.read(1024 * 256)
                    if not block:
                        break
                    yield block

    if not chunks:
        raise HTTPException(status_code=404, detail="No video data uploaded yet")
    return StreamingResponse(_iter(), media_type=rec.mime_type or "video/webm")


@router.delete("/{recording_id}")
def delete_recording(recording_id: str, request: Request, db: Session = Depends(get_db),
                     user: User = Depends(get_current_user)):
    """Deletion is soft and audited; files are purged by the retention job."""
    rec = db.get(ScreenRecording, recording_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Recording not found")
    if user.role != ADMIN and rec.user_id != user.id:
        raise HTTPException(status_code=403, detail="Only the owner or an admin may delete this recording")
    rec.deleted_at = datetime.now(timezone.utc)
    rec.status = "deleted"
    audit.log(db, user, "recording.deleted", "recording", rec.id, request)
    return {"detail": "Recording marked for deletion"}


@router.get("/admin/retention")
def retention_policy(user: User = Depends(get_current_user)):
    return {"retention_days": settings.RECORDING_RETENTION_DAYS,
            "note": "Recordings are purged after the retention window by maintenance/purge.py."}
