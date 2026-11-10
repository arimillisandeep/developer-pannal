"""Admin-only employee monitoring API + live status socket."""
from __future__ import annotations

import asyncio
import json
import io
from datetime import date, datetime, timedelta, timezone

import jwt
from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import PlainTextResponse, StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import SessionLocal, get_db
from app.core.deps import ADMIN, get_current_user
from app.core.security import decode_token
from app.models import TimerSession, User
from app.services import monitoring

router = APIRouter(prefix="/monitoring", tags=["Monitoring"])

IST = timezone(timedelta(hours=5, minutes=30))

_ALIASES = {"developer_onroll": "on_roll", "developer_offroll": "off_roll",
            "onroll": "on_roll", "offrole": "off_roll", "off_roll": "off_roll",
            "on_roll": "on_roll", "intern": "intern", "interns": "intern", "all": "all", "": "all"}


def _norm_category(value: str | None) -> str:
    return _ALIASES.get((value or "all").strip().lower(), (value or "all"))


def _admin_only(user: User) -> None:
    if user.role != ADMIN:
        raise HTTPException(status_code=403, detail="Admin access required")


@router.get("/live")
def live(category: str = Query("all"), status: str = Query("all"),
         on_date: date | None = Query(None), db: Session = Depends(get_db),
         user: User = Depends(get_current_user)):
    _admin_only(user)
    items = monitoring.rows(db, category=_norm_category(category), status=status, on_date=on_date,
                            timeout_seconds=settings.HEARTBEAT_TIMEOUT_SECONDS
                            if hasattr(settings, "HEARTBEAT_TIMEOUT_SECONDS") else 300)
    counts = {c: sum(1 for i in items if i["category"] == c) for c in monitoring.CATEGORIES}
    return {"items": items, "counts": counts, "total": len(items),
            "server_time_ist": monitoring.ist(datetime.now(timezone.utc)),
            "timezone": "Asia/Kolkata"}


@router.get("/employees/{user_id}")
def employee(user_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _admin_only(user)
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="Employee not found")
    return monitoring.detail(db, target)


@router.get("/report.csv", response_class=PlainTextResponse)
def report(category: str = Query("all"), status: str = Query("all"),
           date_from: date | None = Query(None), date_to: date | None = Query(None),
           db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _admin_only(user)
    items = monitoring.rows(db, category=_norm_category(category), status=status)
    if date_from or date_to:
        items = [i for i in items if i["login_at"] and
                 (not date_from or i["login_at"][:10] >= date_from.isoformat()) and
                 (not date_to or i["login_at"][:10] <= date_to.isoformat())]
    body = monitoring.csv_report(db, items)
    return PlainTextResponse(body, headers={
        "Content-Disposition": 'attachment; filename="workcode-attendance.csv"',
        "Content-Type": "text/csv; charset=utf-8",
    })


# ------------------------------------------------------------------ self view
@router.get("/me")
def my_time(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Employee-facing view of their own tracking data (no admin rights needed)."""
    if user.role == "admin":
        raise HTTPException(status_code=403, detail="Administrators do not run a work timer")
    from app.services import monitoring as svc
    data = svc.detail(db, user)
    today = date.today()
    data["today"] = {k: v for k, v in data["summary"].items()}
    data["today"]["date"] = today.isoformat()
    return data


def _sessions_in_range(db: Session, date_from: date, date_to: date,
                       category: str | None = None, employee_id: str | None = None):
    start = datetime.combine(date_from, datetime.min.time(), tzinfo=IST)
    end = datetime.combine(date_to, datetime.max.time(), tzinfo=IST)
    q = (db.query(TimerSession)
         .filter(TimerSession.started_at >= start.astimezone(timezone.utc),
                 TimerSession.started_at <= end.astimezone(timezone.utc)))
    rows_ = q.all()
    out = []
    for sm in rows_:
        u = db.get(User, sm.user_id)
        if not u or u.role == "admin":
            continue
        if category and category != "all" and svc_category(u) != category:
            continue
        if employee_id and u.id != employee_id:
            continue
        out.append((sm, u))
    return out


def svc_category(user: User) -> str:
    from app.services.monitoring import category_of
    return category_of(user)


@router.get("/reports")
def reports(period: str = Query("daily"), date_from: str = Query(""), date_to: str = Query(""),
            category: str = Query("all"), employee_id: str = Query(""), sort: str = Query("away"),
            db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _admin_only(user)
    today = date.today()
    if date_from and date_to:
        d0, d1 = date.fromisoformat(date_from), date.fromisoformat(date_to)
    elif period == "daily":
        d0 = d1 = today
    elif period == "weekly":
        d0 = today - timedelta(days=today.weekday()); d1 = today
    else:
        d0 = today.replace(day=1); d1 = today
    pairs = _sessions_in_range(db, d0, d1, category, employee_id or None)
    agg: dict[str, dict] = {}
    for sm, u in pairs:
        a = agg.setdefault(u.id, {
            "user_id": u.id, "full_name": u.full_name, "role": u.role, "category": svc_category(u),
            "employee_id": getattr(u, "employee_code", None) or u.id[:8],
            "sessions": 0, "active_seconds": 0, "away_seconds": 0, "session_seconds": 0, "days": set()})
        a["sessions"] += 1
        a["active_seconds"] += int(sm.accumulated_seconds or 0)
        a["away_seconds"] += int(sm.break_seconds or 0)
        dur = svc_duration(sm)
        a["session_seconds"] += dur
        a["days"].add(sm.started_at.astimezone(IST).date().isoformat())
    out = []
    for a in agg.values():
        dur = max(a["session_seconds"], a["active_seconds"] + a["away_seconds"])
        out.append({**a, "days": len(a["days"]), "session_seconds": dur,
                    "away_ratio": round(a["away_seconds"] / dur, 3) if dur else 0.0,
                    "avg_active_seconds": int(a["active_seconds"] / max(a["sessions"], 1))})
    out.sort(key=lambda r: r["away_seconds"] if sort == "away" else -r["active_seconds"], reverse=True)
    totals = {"employees": len(out), "sessions": sum(r["sessions"] for r in out),
              "active_seconds": sum(r["active_seconds"] for r in out),
              "away_seconds": sum(r["away_seconds"] for r in out),
              "session_seconds": sum(r["session_seconds"] for r in out)}
    return {"period": period, "date_from": d0.isoformat(), "date_to": d1.isoformat(),
            "category": category, "rows": out, "totals": totals}


def svc_duration(session) -> int:
    if session.ended_at and session.started_at:
        return max(0, int((session.ended_at - session.started_at).total_seconds()))
    from app.services.monitoring import session_duration
    return session_duration(session, datetime.now(timezone.utc))


@router.get("/high-away")
def high_away(date_from: str = Query(""), date_to: str = Query(""), min_ratio: float = Query(0.2),
              category: str = Query("all"), db: Session = Depends(get_db),
              user: User = Depends(get_current_user)):
    _admin_only(user)
    data = reports("daily", date_from, date_to, category, "", "away", db, user)
    flagged = [r for r in data["rows"] if r["away_ratio"] >= min_ratio]
    return {"date_from": data["date_from"], "date_to": data["date_to"], "min_ratio": min_ratio,
            "rows": flagged, "count": len(flagged)}


@router.get("/export.xlsx")
def export_xlsx(period: str = Query("daily"), date_from: str = Query(""), date_to: str = Query(""),
                category: str = Query("all"), employee_id: str = Query(""),
                db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _admin_only(user)
    from openpyxl import Workbook
    data = reports(period, date_from, date_to, category, employee_id, "away", db, user)
    wb = Workbook()
    ws = wb.active
    ws.title = "Time report"
    ws.append(["Employee ID", "Name", "Role", "Category", "Sessions", "Days",
               "Active (h:m)", "Away (h:m)", "Session (h:m)", "Away %"])
    def hm(sec):
        return f"{int(sec)//3600}:{(int(sec)%3600)//60:02d}"
    for r in data["rows"]:
        ws.append([r["employee_id"], r["full_name"], r["role"], r["category"], r["sessions"], r["days"],
                   hm(r["active_seconds"]), hm(r["away_seconds"]), hm(r["session_seconds"]),
                   round(r["away_ratio"] * 100, 1)])
    ws2 = wb.create_sheet("Summary")
    ws2.append(["Period", data["period"]]); ws2.append(["From", data["date_from"]]); ws2.append(["To", data["date_to"]])
    for k, v in data["totals"].items():
        ws2.append([k, v])
    buf = io.BytesIO(); wb.save(buf); buf.seek(0)
    return StreamingResponse(buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition": "attachment; filename=workcode-time-report.xlsx"})

@router.websocket("/ws/monitoring")
async def monitoring_socket(ws: WebSocket, token: str = Query(...)):
    try:
        claims = decode_token(token, expected_type="access")
    except jwt.PyJWTError:
        await ws.close(code=4401)
        return
    db = SessionLocal()
    try:
        user = db.get(User, claims.get("sub"))
        if not user or user.role != ADMIN:
            await ws.close(code=4403)
            return
    finally:
        db.close()
    await ws.accept()
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue(maxsize=500)
    monitoring.hub.add(loop, queue)
    try:
        while True:
            payload = await queue.get()
            await ws.send_text(json.dumps(payload))
    except WebSocketDisconnect:
        pass
    finally:
        monitoring.hub.drop(queue)
        try:
            await ws.close()
        except RuntimeError:
            pass
