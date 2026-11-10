"""Server-controlled working timer, inactivity handling and attendance."""
import json
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.core.deps import ADMIN, get_current_user
from app.models import Attendance, AwayInterval, Project, Task, TimerEvent, TimerSession, User
from app.services import monitoring
from app.schemas import TimerEventIn, TimerOut
from app.services import audit
from app.services.notify import notify_admins

router = APIRouter(prefix="/timer", tags=["Working Timer"])


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _accrue(session: TimerSession, now: datetime) -> int:
    """Add elapsed time since the last heartbeat, capped to avoid inflated hours."""
    last = _aware(session.last_heartbeat_at) or _aware(session.started_at) or now
    delta = (now - last).total_seconds()
    if delta < 0:
        delta = 0
    delta = min(delta, settings.HEARTBEAT_SECONDS * 3)
    session.accumulated_seconds = (session.accumulated_seconds or 0) + int(delta)
    session.last_heartbeat_at = now
    return int(delta)


def _attendance(db: Session, user_id: str) -> Attendance:
    today = date.today()
    row = db.query(Attendance).filter(Attendance.user_id == user_id,
                                      Attendance.work_date == today).one_or_none()
    if not row:
        row = Attendance(user_id=user_id, work_date=today, status="present")
        db.add(row)
        db.flush()
    return row


def _active(db: Session, user_id: str) -> TimerSession | None:
    return (db.query(TimerSession)
            .filter(TimerSession.user_id == user_id, TimerSession.status.in_(["running", "paused"]))
            .order_by(TimerSession.started_at.desc()).first())


def _assert_worker(user: User) -> None:
    if user.role == ADMIN:
        raise HTTPException(status_code=403, detail="Admin accounts do not use the work timer")


@router.get("/current")
def current(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _ensure_auto_session(db, user)
    _assert_worker(user)
    s = _active(db, user.id)
    if not s:
        return {"active": False, "session": None}
    acc = s.accumulated_seconds or 0
    live = acc
    if s.status == "running":
        last = _aware(s.last_heartbeat_at) or _aware(s.started_at)
        live = acc + min(int((_now() - last).total_seconds()), settings.HEARTBEAT_SECONDS * 3)
    return {"active": True, "session": TimerOut.model_validate(s).model_dump(),
            "seconds_today": live, "grace_minutes": settings.AUTO_PAUSE_MINUTES,
            "heartbeat_seconds": settings.HEARTBEAT_SECONDS}


@router.post("/start", response_model=TimerOut)
def start(request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _assert_worker(user)
    existing = _active(db, user.id)
    if existing:
        return existing
    s = TimerSession(user_id=user.id, status="running", started_at=_now(), last_heartbeat_at=_now())
    db.add(s)
    db.flush()
    db.add(TimerEvent(session_id=s.id, user_id=user.id, event="start"))
    att = _attendance(db, user.id)
    att.first_start_at = att.first_start_at or s.started_at
    audit.log(db, user, "timer.started", "timer_session", s.id, request)
    db.flush()
    monitoring.hub.publish({"type": "status", "user_id": user.id, "status": "active",
                            "at": monitoring.ist(s.started_at), "event": "login"})
    return s


@router.post("/pause", response_model=TimerOut)
def pause(payload: TimerEventIn | None = None, request: Request = None,
          db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _assert_worker(user)
    s = _active(db, user.id)
    if not s or s.status != "running":
        raise HTTPException(status_code=409, detail="No running session to pause")
    _accrue(s, _now())
    s.status = "paused"
    s.paused_at = _now()
    s.break_count = (s.break_count or 0) + 1
    db.add(TimerEvent(session_id=s.id, user_id=user.id,
                      event=(payload.event if payload and payload.event else "pause"),
                      meta_json=json.dumps(payload.meta if payload else {})))
    audit.log(db, user, "timer.paused", "timer_session", s.id, request)
    db.flush()
    return s


@router.post("/resume", response_model=TimerOut)
def resume(request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _assert_worker(user)
    s = _active(db, user.id)
    if not s:
        raise HTTPException(status_code=409, detail="No session to resume")
    if s.paused_at:
        brk = int((_now() - _aware(s.paused_at)).total_seconds())
        s.break_seconds = (s.break_seconds or 0) + max(brk, 0)
        att = _attendance(db, user.id)
        att.break_seconds = (att.break_seconds or 0) + max(brk, 0)
        s.paused_at = None
    s.status = "running"
    s.last_heartbeat_at = _now()
    db.add(TimerEvent(session_id=s.id, user_id=user.id, event="resume"))
    audit.log(db, user, "timer.resumed", "timer_session", s.id, request)
    db.flush()
    return s


@router.post("/stop", response_model=TimerOut)
def stop(request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _assert_worker(user)
    s = _active(db, user.id)
    if not s:
        raise HTTPException(status_code=409, detail="No active session")
    if s.status == "running":
        _accrue(s, _now())
    elif s.paused_at:
        brk = int((_now() - _aware(s.paused_at)).total_seconds())
        s.break_seconds = (s.break_seconds or 0) + max(brk, 0)
        s.paused_at = None
    s.status = "stopped"
    s.ended_at = _now()
    db.add(TimerEvent(session_id=s.id, user_id=user.id, event="stop"))

    att = _attendance(db, user.id)
    att.last_stop_at = s.ended_at
    att.active_seconds = (att.active_seconds or 0) + (s.accumulated_seconds or 0)
    att.break_seconds = max(att.break_seconds or 0, s.break_seconds or 0)
    if att.active_seconds < 4 * 3600:
        att.status = "half_day" if att.active_seconds > 3600 else att.status

    audit.log(db, user, "timer.stopped", "timer_session", s.id, request,
              seconds=s.accumulated_seconds)
    db.flush()
    monitoring.hub.publish({"type": "status", "user_id": user.id, "status": "logged_out",
                            "at": monitoring.ist(s.ended_at), "event": "logout"})
    return s


@router.post("/heartbeat")
def heartbeat(payload: TimerEventIn, request: Request, db: Session = Depends(get_db),
              user: User = Depends(get_current_user)):
    """Client heartbeat + browser visibility reporting.

    The server owns the clock: inactive gaps larger than the grace period cause
    an automatic pause, and the client is told to show the Resume banner.
    """
    _assert_worker(user)
    s = _active(db, user.id)
    if not s:
        return {"active": False, "should_pause": False}

    now = _now()
    last = _aware(s.last_heartbeat_at) or now
    gap = (now - last).total_seconds()

    if payload.event in ("hidden", "disconnect"):
        db.add(TimerEvent(session_id=s.id, user_id=user.id, event=payload.event,
                          meta_json=json.dumps(payload.meta)))
        if s.status == "running":
            _accrue(s, now)
        db.flush()
        return {"active": True, "status": s.status, "gap_seconds": int(gap),
                "should_pause": gap > settings.AUTO_PAUSE_MINUTES * 60,
                "grace_minutes": settings.AUTO_PAUSE_MINUTES}

    if s.status == "running" and gap > settings.AUTO_PAUSE_MINUTES * 60:
        _accrue(s, now)
        s.status = "paused"
        s.paused_at = now
        s.break_count = (s.break_count or 0) + 1
        db.add(TimerEvent(session_id=s.id, user_id=user.id, event="inactivity_pause",
                          meta_json=json.dumps({"gap_seconds": int(gap)})))
        notify_admins(db, "inactivity", "Idle employee session paused",
                      f"{user.full_name} was inactive for {int(gap // 60)} minutes.", "/admin/monitoring")
        audit.log(db, user, "timer.inactivity_pause", "timer_session", s.id, request, gap=int(gap))
        db.flush()
        return {"active": True, "status": "paused", "should_pause": True,
                "reason": "inactivity", "gap_seconds": int(gap)}

    if s.status == "running":
        _accrue(s, now)
    db.add(TimerEvent(session_id=s.id, user_id=user.id, event="heartbeat"))
    db.flush()
    return {"active": True, "status": s.status, "seconds": s.accumulated_seconds,
            "gap_seconds": int(gap), "should_pause": False}


@router.post("/away")
def away(payload: TimerEventIn, request: Request, db: Session = Depends(get_db),
         user: User = Depends(get_current_user)):
    """Explicit leave/return transitions from the browser (Page Visibility API).

    Idempotent per session: a duplicate 'leave' returns the open interval instead of
    creating a second one, and 'return' closes whichever interval is open.
    """
    _assert_worker(user)
    s = _active(db, user.id)
    if not s:
        return {"active": False}
    now = _now()
    kind = (payload.event or "leave").lower()
    open_iv = (db.query(AwayInterval).filter(AwayInterval.session_id == s.id,
                                             AwayInterval.returned_at.is_(None))
               .order_by(AwayInterval.left_at.desc()).first())
    if kind == "leave":
        if not open_iv:
            if s.status == "running":
                _accrue(s, now)
            open_iv = AwayInterval(session_id=s.id, user_id=user.id, left_at=now,
                                   reason=(payload.meta or {}).get("reason", "hidden"))
            db.add(open_iv)
            s.status = "paused"
            s.paused_at = now
            s.break_count = (s.break_count or 0) + 1
            db.add(TimerEvent(session_id=s.id, user_id=user.id, event="away",
                              meta_json=json.dumps(payload.meta)))
        db.flush()
        monitoring.hub.publish({"type": "status", "user_id": user.id,
                                "status": "away", "at": monitoring.ist(now)})
        return {"active": True, "status": "paused", "away_since": monitoring.ist(open_iv.left_at)}
    if kind == "return":
        if open_iv:
            secs = max(0, int((now - _aware(open_iv.left_at)).total_seconds()))
            open_iv.returned_at = now
            open_iv.seconds = secs
            s.break_seconds = (s.break_seconds or 0) + secs
            s.paused_at = None
        s.status = "running"
        s.last_heartbeat_at = now
        db.add(TimerEvent(session_id=s.id, user_id=user.id, event="return"))
        db.flush()
        monitoring.hub.publish({"type": "status", "user_id": user.id,
                                "status": "active", "at": monitoring.ist(now)})
        return {"active": True, "status": "running",
                "away_seconds": open_iv.seconds if open_iv else 0}
    raise HTTPException(status_code=422, detail="event must be 'leave' or 'return'")


@router.get("/sessions")
def sessions(db: Session = Depends(get_db), user: User = Depends(get_current_user),
             limit: int = Query(30, ge=1, le=200)):
    _assert_worker(user)
    rows = (db.query(TimerSession).filter(TimerSession.user_id == user.id)
            .order_by(TimerSession.started_at.desc()).limit(limit).all())
    return [{"id": s.id, "status": s.status, "started_at": s.started_at, "ended_at": s.ended_at,
             "active_seconds": s.accumulated_seconds, "break_seconds": s.break_seconds,
             "break_count": s.break_count} for s in rows]


@router.get("/events/{session_id}")
def session_events(session_id: str, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    s = db.get(TimerSession, session_id)
    if not s or (s.user_id != user.id and user.role != ADMIN):
        raise HTTPException(status_code=404, detail="Session not found")
    rows = (db.query(TimerEvent).filter(TimerEvent.session_id == session_id)
            .order_by(TimerEvent.at).all())
    return [{"event": e.event, "at": e.at, "meta": json.loads(e.meta_json or "{}")} for e in rows]


@router.get("/attendance")
def my_attendance(db: Session = Depends(get_db), user: User = Depends(get_current_user),
                  days: int = Query(30, ge=1, le=365)):
    _assert_worker(user)
    since = date.today() - timedelta(days=days)
    rows = (db.query(Attendance).filter(Attendance.user_id == user.id,
                                        Attendance.work_date >= since)
            .order_by(Attendance.work_date.desc()).all())
    total = sum(a.active_seconds or 0 for a in rows)
    return {
        "days": [{"date": a.work_date.isoformat(), "status": a.status,
                  "active_seconds": a.active_seconds, "break_seconds": a.break_seconds,
                  "first_start_at": a.first_start_at, "last_stop_at": a.last_stop_at} for a in rows],
        "total_hours": round(total / 3600, 2),
        "avg_hours_per_day": round(total / 3600 / max(len(rows), 1), 2),
    }


def _ensure_auto_session(db: Session, user: User) -> None:
    """Auto-start tracking for employees the moment they use the app (no Start button)."""
    if user.role == "admin":
        return
    row = (db.query(TimerSession).filter(TimerSession.user_id == user.id)
           .order_by(TimerSession.started_at.desc()).first())
    if row and row.status in ("running", "paused"):
        return
    today_ist = (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).date()
    if row and (row.started_at + timedelta(hours=5, minutes=30)).date() == today_ist:
        return  # already tracked today; a stopped session must stay stopped
    now = datetime.now(timezone.utc)
    s = TimerSession(user_id=user.id, status="running", started_at=now, last_heartbeat_at=now)
    db.add(s)
    db.flush()
    db.add(TimerEvent(session_id=s.id, user_id=user.id, event="start", meta_json='{"auto": true}'))
    work_day = (now + timedelta(hours=5, minutes=30)).date()
    day = (db.query(Attendance).filter(Attendance.user_id == user.id,
                                       Attendance.work_date == work_day).one_or_none())
    if day is None:
        db.add(Attendance(user_id=user.id, work_date=work_day, first_start_at=now))
    elif day.first_start_at is None:
        day.first_start_at = now
    db.commit()
