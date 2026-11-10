"""Live employee monitoring: status derivation, per-employee history, CSV reports.

Every number here comes from server timestamps on TimerSession / AwayInterval / Attendance —
the browser only ever *reports* transitions, it never owns the clock.
"""
from __future__ import annotations

import asyncio
import csv
import io
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import Attendance, AwayInterval, TimerEvent, TimerSession, User

IST = timezone(timedelta(hours=5, minutes=30))
CATEGORIES = ("on_roll", "off_roll", "intern")


def ist(dt: datetime | None) -> str | None:
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S")


def category_of(user: User) -> str:
    role = (user.role or "").lower()
    if "intern" in role:
        return "intern"
    if "off" in role:
        return "off_roll"
    return "on_roll"


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def live_seconds(session: TimerSession, now: datetime) -> int:
    total = session.accumulated_seconds or 0
    if session.status == "running":
        last = _aware(session.last_heartbeat_at) or _aware(session.started_at)
        if last:
            total += max(0, int((now - last).total_seconds()))
    return max(0, total)


def session_duration(session: TimerSession, now: datetime) -> int:
    start = _aware(session.started_at)
    end = _aware(session.ended_at) or now
    return max(0, int((end - start).total_seconds())) if start else 0


def current_status(session: TimerSession | None, now: datetime, timeout_seconds: int) -> str:
    if not session:
        return "logged_out"
    if session.status == "interrupted":
        return "interrupted"
    if session.status == "stopped":
        return "logged_out"
    last = _aware(session.last_heartbeat_at)
    if last and (now - last).total_seconds() > timeout_seconds:
        return "interrupted"
    if session.status == "paused":
        return "away"
    return "active"


def employee_row(db: Session, user: User, now: datetime, timeout_seconds: int) -> dict:
    session = (db.query(TimerSession)
               .filter(TimerSession.user_id == user.id)
               .order_by(TimerSession.started_at.desc()).first())
    open_session = session if session and session.status in ("running", "paused") else None
    away = 0
    if open_session:
        away = (db.query(func.coalesce(func.sum(AwayInterval.seconds), 0))
                .filter(AwayInterval.session_id == open_session.id,
                        AwayInterval.returned_at.isnot(None)).scalar() or 0)
        live_open = (db.query(AwayInterval)
                     .filter(AwayInterval.session_id == open_session.id,
                             AwayInterval.returned_at.is_(None)).first())
        if live_open:
            away += max(0, int((now - _aware(live_open.left_at)).total_seconds()))
    active = live_seconds(open_session, now) if open_session else (session.accumulated_seconds or 0 if session else 0)
    total = session_duration(session, now) if session else 0
    status = current_status(session, now, timeout_seconds)
    return {
        "user_id": user.id, "employee_id": (getattr(user, "employee_code", None) or getattr(user, "employee_id", None) or user.id[:8]),
        "full_name": user.full_name, "role": user.role, "category": category_of(user),
        "email": user.email,
        "login_at": ist(session.started_at) if session else None,
        "logout_at": ist(session.ended_at) if session and session.ended_at else None,
        "status": status,
        "active_seconds": int(active), "away_seconds": int(away),
        "session_seconds": int(total),
        "last_activity": ist(session.last_heartbeat_at) if session else None,
        "session_id": session.id if session else None,
    }


def rows(db: Session, *, category: str | None = None, status: str | None = None,
         on_date: date | None = None, timeout_seconds: int = 300) -> list[dict]:
    now = datetime.now(timezone.utc)
    q = db.query(User).filter(User.is_active.is_(True), User.role != "admin")
    out = []
    for user in q.order_by(User.full_name).all():
        row = employee_row(db, user, now, timeout_seconds)
        if category and category != "all" and row["category"] != category:
            continue
        if status and status != "all" and row["status"] != status:
            continue
        if on_date:
            start = datetime.combine(on_date, datetime.min.time(), tzinfo=IST)
            end = start + timedelta(days=1)
            row_date = row["login_at"]
            if not row_date or not (start.astimezone(timezone.utc) <= datetime.fromisoformat(
                    row_date).replace(tzinfo=IST).astimezone(timezone.utc) < end.astimezone(timezone.utc)):
                continue
        out.append(row)
    return out


def detail(db: Session, user: User, timeout_seconds: int = 300) -> dict:
    now = datetime.now(timezone.utc)
    sessions = (db.query(TimerSession).filter(TimerSession.user_id == user.id)
                .order_by(TimerSession.started_at.desc()).limit(50).all())
    timeline = []
    for s in sessions:
        timeline.append({"type": "login", "at": ist(s.started_at), "session_id": s.id})
        for ev in (db.query(TimerEvent).filter(TimerEvent.session_id == s.id)
                   .order_by(TimerEvent.at).all()):
            if ev.event in ("start", "resume", "stop", "hidden", "away", "return", "inactivity_pause"):
                timeline.append({"type": ev.event, "at": ist(ev.at), "session_id": s.id})
        if s.ended_at:
            timeline.append({"type": "logout", "at": ist(s.ended_at), "session_id": s.id})
    away = (db.query(AwayInterval).filter(AwayInterval.user_id == user.id)
            .order_by(AwayInterval.left_at.desc()).limit(200).all())
    attendance = (db.query(Attendance).filter(Attendance.user_id == user.id)
                  .order_by(Attendance.work_date.desc()).limit(31).all())
    return {
        "profile": {"id": user.id, "full_name": user.full_name, "email": user.email,
                    "role": user.role, "category": category_of(user),
                    "employee_code": getattr(user, "employee_code", None), "team": getattr(user, "team", None)},
        "summary": employee_row(db, user, now, timeout_seconds),
        "sessions": [{"id": s.id, "started_at": ist(s.started_at), "ended_at": ist(s.ended_at),
                      "status": s.status, "active_seconds": s.accumulated_seconds or 0,
                      "away_seconds": s.break_seconds or 0} for s in sessions],
        "away_intervals": [{"left_at": ist(a.left_at), "returned_at": ist(a.returned_at),
                            "seconds": a.seconds, "reason": a.reason} for a in away],
        "attendance": [{"date": a.work_date.isoformat(), "active_seconds": a.active_seconds or 0,
                        "away_seconds": a.break_seconds or 0, "status": a.status} for a in attendance],
        "timeline": sorted(timeline, key=lambda x: x["at"] or "")[-200:],
    }


def csv_report(db: Session, rows_in: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Employee ID", "Name", "Role", "Category", "Login (IST)", "Logout (IST)",
                "Status", "Active (h:mm:ss)", "Away (h:mm:ss)", "Session (h:mm:ss)", "Last activity"])
    def hms(s):
        s = int(s or 0)
        return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"
    for r in rows_in:
        w.writerow([r["employee_id"], r["full_name"], r["role"], r["category"],
                    r["login_at"], r["logout_at"], r["status"],
                    hms(r["active_seconds"]), hms(r["away_seconds"]), hms(r["session_seconds"]),
                    r["last_activity"]])
    return buf.getvalue()


# ---------------------------------------------------------------- live websocket hub
class Hub:
    def __init__(self) -> None:
        self._subs: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = []

    def add(self, loop, queue):
        self._subs.append((loop, queue))

    def drop(self, queue):
        self._subs = [(l, q) for l, q in self._subs if q is not queue]

    def publish(self, payload: dict) -> None:
        for loop, queue in list(self._subs):
            try:
                loop.call_soon_threadsafe(queue.put_nowait, payload)
            except RuntimeError:
                pass


hub = Hub()
