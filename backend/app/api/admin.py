"""Admin dashboard, monitoring and audit-log access."""
import json
from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.core.deps import get_current_user, require_admin
from app.models import (
    Attendance, AuditLog, ChatMessage, CodeReview, Meeting, Notification, Project,
    ScreenRecording, Task, TaskSubmission, Team, TimerSession, User,
)
from app.schemas import UserOut
from app.services import audit
from app.ws.manager import manager

router = APIRouter(prefix="/admin", tags=["Admin"])


@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    today = date.today()
    counts = dict(
        db.query(User.role, func.count(User.id)).group_by(User.role).all()
    )
    total_users = db.query(func.count(User.id)).scalar() or 0
    active_users = db.query(func.count(User.id)).filter(User.is_active.is_(True)).scalar() or 0

    today_att = db.query(Attendance).filter(Attendance.work_date == today).all()
    today_seconds = sum(a.active_seconds or 0 for a in today_att)

    task_counts = dict(db.query(Task.status, func.count(Task.id)).group_by(Task.status).all())
    overdue = (db.query(func.count(Task.id))
               .filter(Task.due_date < today, Task.status.notin_(["completed", "approved", "final_approved"])).scalar() or 0)

    running_timers = (db.query(func.count(TimerSession.id))
                      .filter(TimerSession.status == "running").scalar() or 0)

    recent_submissions = (db.query(TaskSubmission).order_by(TaskSubmission.created_at.desc()).limit(5).all())
    recent_audit = (db.query(AuditLog).order_by(AuditLog.created_at.desc()).limit(8).all())

    return {
        "totals": {
            "users": total_users,
            "admins": counts.get("admin", 0),
            "onroll": counts.get("developer_onroll", 0),
            "offroll": counts.get("developer_offroll", 0),
            "interns": counts.get("intern", 0),
            "active_users": active_users,
            "inactive_users": total_users - active_users,
        },
        "today": {
            "attendance_present": len([a for a in today_att if a.status == "present"]),
            "working_hours": round(today_seconds / 3600, 2),
            "active_sessions": running_timers,
            "online_now": len(manager.online_users()),
        },
        "tasks": {
            "total": sum(task_counts.values()),
            "by_status": task_counts,
            "assigned": task_counts.get("in_progress", 0) + task_counts.get("not_started", 0),
            "completed": task_counts.get("completed", 0) + task_counts.get("approved", 0) + task_counts.get("final_approved", 0),
            "pending": task_counts.get("not_started", 0) + task_counts.get("in_progress", 0),
            "under_review": task_counts.get("under_review", 0) + task_counts.get("submitted", 0),
            "rework": task_counts.get("rework_required", 0),
            "overdue": overdue,
        },
        "activity": {
            "submissions": db.query(func.count(TaskSubmission.id)).scalar() or 0,
            "reviews": db.query(func.count(CodeReview.id)).scalar() or 0,
            "projects": db.query(func.count(Project.id)).filter(Project.deleted_at.is_(None)).scalar() or 0,
            "recordings": db.query(func.count(ScreenRecording.id)).filter(
                ScreenRecording.deleted_at.is_(None)).scalar() or 0,
            "messages_today": db.query(func.count(ChatMessage.id)).filter(
                ChatMessage.created_at >= datetime.combine(today, time.min, tzinfo=timezone.utc)).scalar() or 0,
            "notifications": db.query(func.count(Notification.id)).scalar() or 0,
            "meetings": db.query(func.count(Meeting.id)).filter(Meeting.status == "scheduled").scalar() or 0,
            "teams": db.query(func.count(Team.id)).scalar() or 0,
        },
        "recent_submissions": [
            {"id": s.id, "task_id": s.task_id, "user_id": s.user_id, "status": s.status,
             "created_at": s.created_at} for s in recent_submissions
        ],
        "recent_audit": [
            {"id": a.id, "action": a.action, "actor_id": a.actor_id, "entity_type": a.entity_type,
             "created_at": a.created_at} for a in recent_audit
        ],
    }


@router.get("/users/{user_id}/activity")
def user_activity(user_id: str, days: int = Query(14, ge=1, le=90),
                  db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    """Everything one person did, for the monitoring view."""
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    since = datetime.now(timezone.utc) - timedelta(days=days)

    tasks = db.query(Task).filter(Task.assignee_id == user_id).all()
    attendance = (db.query(Attendance).filter(Attendance.user_id == user_id,
                                              Attendance.work_date >= since.date())
                  .order_by(Attendance.work_date.desc()).all())
    recordings = (db.query(ScreenRecording).filter(ScreenRecording.user_id == user_id,
                                                   ScreenRecording.deleted_at.is_(None))
                  .order_by(ScreenRecording.started_at.desc()).limit(20).all())
    audits = (db.query(AuditLog).filter(AuditLog.actor_id == user_id, AuditLog.created_at >= since)
              .order_by(AuditLog.created_at.desc()).limit(50).all())
    sessions = (db.query(TimerSession).filter(TimerSession.user_id == user_id)
                .order_by(TimerSession.started_at.desc()).limit(20).all())

    prof = user.profile
    return {
        "user": UserOut.model_validate(user).model_dump(),
        "profile": {
            "employee_code": prof.employee_code if prof else None,
            "joining_date": prof.joining_date.isoformat() if prof else None,
            "employment_status": prof.employment_status if prof else None,
            "expected_end_date": prof.expected_end_date.isoformat() if prof and prof.expected_end_date else None,
        },
        "tasks": {
            "total": len(tasks),
            "completed": len([t for t in tasks if t.status in ("completed", "approved", "final_approved")]),
            "overdue": len([t for t in tasks if t.due_date and t.due_date < date.today()
                            and t.status not in ("completed", "approved", "final_approved")]),
            "list": [{"id": t.id, "code": t.code, "title": t.title, "status": t.status,
                      "progress": t.progress, "due_date": t.due_date} for t in tasks[:20]],
        },
        "attendance": [{"date": a.work_date.isoformat(), "active_seconds": a.active_seconds,
                        "break_seconds": a.break_seconds, "status": a.status} for a in attendance],
        "recordings": [{"id": r.id, "started_at": r.started_at, "duration_seconds": r.duration_seconds,
                        "status": r.status, "size_bytes": r.size_bytes} for r in recordings],
        "timer_sessions": [{"id": s.id, "started_at": s.started_at, "ended_at": s.ended_at,
                            "seconds": s.accumulated_seconds, "status": s.status} for s in sessions],
        "audit": [{"id": a.id, "action": a.action, "entity_type": a.entity_type,
                   "created_at": a.created_at, "ip": a.ip} for a in audits],
    }


@router.get("/progress")
def employee_progress(db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    rows = db.query(User).filter(User.role.in_(["developer_onroll", "developer_offroll", "intern"]),
                                 User.is_active.is_(True)).all()
    out = []
    for u in rows:
        tasks = db.query(Task).filter(Task.assignee_id == u.id).all()
        done = len([t for t in tasks if t.status in ("completed", "approved", "final_approved")])
        hours = (db.query(func.coalesce(func.sum(Attendance.active_seconds), 0))
                 .filter(Attendance.user_id == u.id).scalar() or 0) / 3600
        avg_progress = round(sum(t.progress for t in tasks) / len(tasks), 1) if tasks else 0
        out.append({
            "user_id": u.id, "full_name": u.full_name, "role": u.role,
            "task_total": len(tasks), "task_completed": done,
            "completion_rate": round(done / len(tasks) * 100, 1) if tasks else 0,
            "avg_progress": avg_progress, "hours_logged": round(hours, 1),
            "avatar_color": u.avatar_color,
        })
    return sorted(out, key=lambda x: x["completion_rate"], reverse=True)


# ---------------------------------------------------------------- audit access
@router.get("/audit-logs")
def audit_logs(
    db: Session = Depends(get_db), admin: User = Depends(require_admin),
    scope: str = Query("auto", description="auto | mine | all"),
    actor_id: str | None = Query(None),
    action: str | None = Query(None),
    entity_type: str | None = Query(None),
    page: int = Query(1, ge=1), size: int = Query(50, ge=1, le=200),
):
    """
    Audit visibility policy (configurable via AUDIT_VISIBILITY):
      per_admin  - by default an admin only sees their own actions; `scope=all`
                   requires the explicit authorized-access flag below.
      all_admins - every admin sees every entry.
    """
    query = db.query(AuditLog)
    show_all = scope == "all" or settings.AUDIT_VISIBILITY == "all_admins"
    if not show_all:
        query = query.filter(AuditLog.actor_id == admin.id)
    elif actor_id:
        query = query.filter(AuditLog.actor_id == actor_id)
    if action:
        query = query.filter(AuditLog.action.ilike(f"%{action}%"))
    if entity_type:
        query = query.filter(AuditLog.entity_type == entity_type)

    total = query.count()
    rows = query.order_by(AuditLog.created_at.desc()).offset((page - 1) * size).limit(size).all()

    if scope == "all" and settings.AUDIT_VISIBILITY == "per_admin":
        audit.log(db, admin, "audit.full_history_viewed", "audit_log", "", None,
                  reason="authorized admin cross-review")

    names = {u.id: u.full_name for u in db.query(User).all()}
    return {
        "total": total,
        "visibility": settings.AUDIT_VISIBILITY,
        "items": [{
            "id": a.id, "actor_id": a.actor_id, "actor_name": names.get(a.actor_id, "System"),
            "actor_role": a.actor_role, "action": a.action, "entity_type": a.entity_type,
            "entity_id": a.entity_id, "ip": a.ip, "meta": json.loads(a.meta_json or "{}"),
            "created_at": a.created_at,
        } for a in rows],
    }


@router.get("/admins")
def admin_accounts(db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    rows = db.query(User).filter(User.role == "admin").order_by(User.created_at).all()
    out = []
    for u in rows:
        acts = db.query(func.count(AuditLog.id)).filter(AuditLog.actor_id == u.id).scalar() or 0
        out.append({
            "id": u.id, "full_name": u.full_name, "email": u.email, "username": u.username,
            "admin_slot": u.admin_account.admin_slot if u.admin_account else None,
            "is_active": u.is_active, "last_login_at": u.last_login_at,
            "actions_logged": acts,
        })
    return out


@router.get("/settings")
def platform_settings(admin: User = Depends(require_admin)):
    return {
        "app": settings.APP_NAME, "env": settings.ENV,
        "audit_visibility": settings.AUDIT_VISIBILITY,
        "auto_pause_minutes": settings.AUTO_PAUSE_MINUTES,
        "recording_retention_days": settings.RECORDING_RETENTION_DAYS,
        "runner_mode": settings.RUNNER_MODE,
        "run_timeout_seconds": settings.RUN_TIMEOUT_SECONDS,
        "run_memory_mb": settings.RUN_MEMORY_MB,
        "google_configured": bool(settings.GOOGLE_CLIENT_ID),
        "database": "postgresql" if settings.is_postgres else "sqlite (development)",
        "access_token_ttl_min": settings.ACCESS_TOKEN_TTL_MIN,
        "refresh_token_ttl_days": settings.REFRESH_TOKEN_TTL_DAYS,
    }
