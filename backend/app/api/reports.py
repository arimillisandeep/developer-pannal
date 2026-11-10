"""Reports and CSV exports for administration."""
import csv
import io
from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import ADMIN, ONROLL, get_current_user, require_admin
from app.models import (
    Attendance, AuditLog, AwayInterval, CodeReview, Project, ScreenRecording,
    Task, TaskSubmission, Team, TimerSession, User,
)

router = APIRouter(prefix="/reports", tags=["Reports"])


def _employee_report_rows(report: dict) -> tuple[list[str], list[list[str]]]:
    headers = ["section", "date_or_id", "status_or_action", "details"]
    rows: list[list[str]] = []
    for item in report["attendance"]:
        rows.append(["attendance", item["date"], item["status"],
                     f"active {item['active_seconds']} seconds; paused {item['paused_seconds']} seconds"])
    for item in report["sessions"]:
        rows.append(["session", str(item["started_at"]), item["status"],
                     f"active {item['active_seconds']} seconds; paused {item['paused_seconds']} seconds; away {item['away_seconds']} seconds"])
    for item in report["tasks"]:
        rows.append(["task", item["code"], item["status"], item["title"]])
    for item in report["submissions"]:
        rows.append(["submission", str(item["created_at"]), item["status"],
                     f"version {item['version_no']}; {item['review_notes'] or ''}"])
    for item in report["reviews"]:
        rows.append(["review", str(item["created_at"]), item["decision"], item["comments"] or ""])
    for item in report["recordings"]:
        rows.append(["recording", str(item["started_at"]), item["status"],
                     f"{item['duration_seconds'] or 0} seconds; {item['size_bytes'] or 0} bytes"])
    for item in report["activity"]:
        rows.append(["activity", str(item["created_at"]), item["action"],
                     f"{item['entity_type']}:{item['entity_id']}"])
    return headers, rows


def _pdf_text(value) -> str:
    """Escape report text and replace glyphs unsupported by ReportLab's base fonts."""
    from xml.sax.saxutils import escape
    return escape(str(value).encode("latin-1", "replace").decode("latin-1"))


def _employee_report_export(report: dict, export_format: str) -> Response:
    headers, rows = _employee_report_rows(report)
    stem = f"workcode-report-{report['employee']['id'][:8]}-{report['range']['from']}-{report['range']['to']}"
    if export_format == "csv":
        return _csv(rows, headers, filename=f"{stem}.csv")
    if export_format == "xlsx":
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill

        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Work report"
        sheet.append([_safe_spreadsheet_cell(value) for value in headers])
        for cell in sheet[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="1F4E78")
        for row in rows:
            sheet.append([_safe_spreadsheet_cell(value) for value in row])
        for column, width in zip(("A", "B", "C", "D"), (18, 30, 24, 90)):
            sheet.column_dimensions[column].width = width
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        buffer = io.BytesIO()
        workbook.save(buffer)
        return Response(buffer.getvalue(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f'attachment; filename="{stem}.xlsx"'})
    if export_format == "pdf":
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import landscape, letter
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib.units import inch
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

        buffer = io.BytesIO()
        document = SimpleDocTemplate(buffer, pagesize=landscape(letter),
                                     leftMargin=0.45 * inch, rightMargin=0.45 * inch,
                                     topMargin=0.45 * inch, bottomMargin=0.45 * inch)
        styles = getSampleStyleSheet()
        table_rows = [[Paragraph(f"<b>{_pdf_text(value)}</b>", styles["BodyText"]) for value in headers]]
        table_rows.extend([[Paragraph(_pdf_text(value), styles["BodyText"]) for value in row] for row in rows])
        table = Table(table_rows, colWidths=[1.1 * inch, 1.55 * inch, 1.35 * inch, 6.0 * inch], repeatRows=1)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f4e78")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#cbd5e1")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f1f5f9")]),
            ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        document.build([Paragraph(f"Work report - {_pdf_text(report['employee']['name'])}", styles["Title"]),
                        Paragraph(f"{_pdf_text(report['employee']['role'])} · {report['range']['from']} to {report['range']['to']}", styles["BodyText"]),
                        Spacer(1, 0.18 * inch), table])
        return Response(buffer.getvalue(), media_type="application/pdf",
                        headers={"Content-Disposition": f'attachment; filename="{stem}.pdf"'})
    raise HTTPException(status_code=422, detail="format must be csv, xlsx or pdf")


def _range(date_from: date | None, date_to: date | None) -> tuple[date, date]:
    to_d = date_to or date.today()
    from_d = date_from or (to_d - timedelta(days=30))
    if from_d > to_d:
        raise HTTPException(status_code=422, detail="date_from must be on or before date_to")
    return from_d, to_d


def _employee_ids(db: Session, employee_id: str | None, role: str | None) -> list[str]:
    query = db.query(User.id).filter(User.role != ADMIN)
    if employee_id:
        query = query.filter(User.id == employee_id)
    if role:
        query = query.filter(User.role == role)
    return [row[0] for row in query.all()]


def _report_for_user(db: Session, employee: User, from_d: date, to_d: date,
                     reviewer_id: str | None = None) -> dict:
    start = datetime.combine(from_d, time.min, tzinfo=timezone.utc)
    end = datetime.combine(to_d + timedelta(days=1), time.min, tzinfo=timezone.utc)
    tasks_query = db.query(Task)
    if reviewer_id:
        team_ids = [team_id for (team_id,) in db.query(Team.id)
                    .filter(Team.lead_user_id == reviewer_id).all()]
        scopes = [Task.assigned_by == reviewer_id]
        if team_ids:
            scopes.append(Task.team_id.in_(team_ids))
        tasks_query = tasks_query.filter(Task.assignee_id == employee.id, or_(*scopes))
    else:
        tasks_query = tasks_query.filter(Task.assignee_id == employee.id)
    tasks = tasks_query.order_by(Task.created_at.desc()).all()
    task_ids = [task.id for task in tasks]

    attendance = (db.query(Attendance).filter(
        Attendance.user_id == employee.id,
        Attendance.work_date >= from_d,
        Attendance.work_date <= to_d,
    ).order_by(Attendance.work_date).all())
    sessions = (db.query(TimerSession).filter(
        TimerSession.user_id == employee.id,
        TimerSession.started_at < end,
        or_(TimerSession.ended_at.is_(None), TimerSession.ended_at >= start),
    ).order_by(TimerSession.started_at).all())
    session_ids = [session.id for session in sessions]
    away_rows = (db.query(AwayInterval).filter(AwayInterval.session_id.in_(session_ids)).all()
                 if session_ids else [])
    away_by_session: dict[str, int] = {}
    for interval in away_rows:
        away_by_session[interval.session_id] = away_by_session.get(interval.session_id, 0) + (interval.seconds or 0)

    submissions_query = db.query(TaskSubmission)
    if reviewer_id:
        submissions_query = submissions_query.filter(TaskSubmission.task_id.in_(task_ids))
    else:
        submissions_query = submissions_query.filter(TaskSubmission.user_id == employee.id)
    submissions = submissions_query.order_by(TaskSubmission.created_at.desc()).all()
    submission_ids = [item.id for item in submissions]

    reviews_query = db.query(CodeReview)
    if reviewer_id:
        reviews_query = reviews_query.filter(CodeReview.submission_id.in_(submission_ids))
    else:
        reviews_query = reviews_query.filter(CodeReview.submission_id.in_(submission_ids))
    reviews = (reviews_query.filter(CodeReview.created_at >= start, CodeReview.created_at < end)
               .order_by(CodeReview.created_at.desc()).all()) if submission_ids else []

    recordings_query = db.query(ScreenRecording).filter(
        ScreenRecording.deleted_at.is_(None),
        ScreenRecording.started_at >= start,
        ScreenRecording.started_at < end,
    )
    if reviewer_id:
        recordings_query = recordings_query.filter(ScreenRecording.task_id.in_(task_ids)) if task_ids else recordings_query.filter(False)
    else:
        recordings_query = recordings_query.filter(ScreenRecording.user_id == employee.id)
    recordings = recordings_query.order_by(ScreenRecording.started_at.desc()).all()

    activity_query = db.query(AuditLog).filter(
        AuditLog.actor_id == employee.id,
        AuditLog.created_at >= start,
        AuditLog.created_at < end,
        AuditLog.action.like("workspace.%") | AuditLog.action.like("submission.%") |
        AuditLog.action.like("timer.%") | AuditLog.action.like("recording.%") |
        AuditLog.action.like("terminal.%"),
    )
    activity = activity_query.order_by(AuditLog.created_at.desc()).limit(500).all()

    return {
        "employee": {"id": employee.id, "name": employee.full_name, "role": employee.role,
                     "employee_code": employee.profile.employee_code if employee.profile else None},
        "range": {"from": from_d.isoformat(), "to": to_d.isoformat()},
        "attendance": [{"date": item.work_date.isoformat(), "status": item.status,
                        "active_seconds": item.active_seconds or 0, "paused_seconds": item.break_seconds or 0,
                        "started_at": item.first_start_at, "stopped_at": item.last_stop_at}
                       for item in attendance],
        "sessions": [{"id": item.id, "started_at": item.started_at, "ended_at": item.ended_at,
                      "status": item.status, "active_seconds": item.accumulated_seconds or 0,
                      "paused_seconds": item.break_seconds or 0,
                      "away_seconds": away_by_session.get(item.id, 0)} for item in sessions],
        "tasks": [{"id": item.id, "code": item.code, "title": item.title, "status": item.status,
                   "priority": item.priority, "due_date": item.due_date} for item in tasks],
        "submissions": [{"id": item.id, "task_id": item.task_id, "version_no": item.version_no,
                         "status": item.status, "created_at": item.created_at,
                         "review_notes": item.review_notes} for item in submissions],
        "reviews": [{"submission_id": item.submission_id, "decision": item.decision,
                     "comments": item.comments, "created_at": item.created_at} for item in reviews],
        "recordings": [{"id": item.id, "task_id": item.task_id, "status": item.status,
                        "started_at": item.started_at, "duration_seconds": item.duration_seconds,
                        "size_bytes": item.size_bytes} for item in recordings],
        "activity": [{"action": item.action, "entity_type": item.entity_type,
                      "entity_id": item.entity_id, "created_at": item.created_at}
                     for item in activity],
    }


def _reviewer_team_ids(db: Session, reviewer: User) -> list[str]:
    return [team_id for (team_id,) in db.query(Team.id)
            .filter(Team.lead_user_id == reviewer.id).all()]


def _reviewer_task_scope(reviewer: User, team_ids: list[str]):
    task_scope = [Task.assigned_by == reviewer.id]
    if team_ids:
        task_scope.append(Task.team_id.in_(team_ids))
    return or_(*task_scope)


@router.get("/me")
def my_report(date_from: date | None = Query(None), date_to: date | None = Query(None),
              db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if user.role == ADMIN:
        raise HTTPException(status_code=403, detail="Use the administrator reports")
    from_d, to_d = _range(date_from, date_to)
    return _report_for_user(db, user, from_d, to_d)


@router.get("/me/export")
def export_my_report(format: str = Query("csv", pattern="^(csv|xlsx|pdf)$"),
                     date_from: date | None = Query(None), date_to: date | None = Query(None),
                     db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if user.role == ADMIN:
        raise HTTPException(status_code=403, detail="Use the administrator reports")
    from_d, to_d = _range(date_from, date_to)
    return _employee_report_export(_report_for_user(db, user, from_d, to_d), format)


@router.get("/team")
def team_report(date_from: date | None = Query(None), date_to: date | None = Query(None),
                employee_id: str | None = Query(None), db: Session = Depends(get_db),
                reviewer: User = Depends(get_current_user)):
    if reviewer.role != ONROLL:
        raise HTTPException(status_code=403, detail="Only On-Roll reviewers can view team reports")
    from_d, to_d = _range(date_from, date_to)
    team_ids = _reviewer_team_ids(db, reviewer)
    visible_tasks = db.query(Task.id).filter(_reviewer_task_scope(reviewer, team_ids))
    visible_assignees = db.query(Task.assignee_id).filter(Task.id.in_(visible_tasks)).distinct()
    if employee_id:
        employee = db.query(User).filter(User.id == employee_id,
                                         User.id.in_(visible_assignees)).one_or_none()
        if not employee:
            raise HTTPException(status_code=403, detail="Employee is outside your assigned review scope")
        return _report_for_user(db, employee, from_d, to_d, reviewer_id=reviewer.id)
    employees = db.query(User).filter(User.id.in_(visible_assignees), User.role != ADMIN).all()
    return {"range": {"from": from_d.isoformat(), "to": to_d.isoformat()},
            "employees": [_report_for_user(db, employee, from_d, to_d, reviewer_id=reviewer.id)
                          for employee in employees]}


@router.get("/team/{employee_id}/export")
def export_team_report(employee_id: str, format: str = Query("csv", pattern="^(csv|xlsx|pdf)$"),
                       date_from: date | None = Query(None), date_to: date | None = Query(None),
                       db: Session = Depends(get_db), reviewer: User = Depends(get_current_user)):
    if reviewer.role != ONROLL:
        raise HTTPException(status_code=403, detail="Only On-Roll reviewers can export team reports")
    from_d, to_d = _range(date_from, date_to)
    team_ids = _reviewer_team_ids(db, reviewer)
    task_scope = _reviewer_task_scope(reviewer, team_ids)
    visible_assignees = db.query(Task.assignee_id).filter(task_scope).distinct()
    employee = db.query(User).filter(User.id == employee_id, User.id.in_(visible_assignees),
                                     User.role != ADMIN).one_or_none()
    if not employee:
        raise HTTPException(status_code=403, detail="Employee is outside your assigned review scope")
    report = _report_for_user(db, employee, from_d, to_d, reviewer_id=reviewer.id)
    return _employee_report_export(report, format)


@router.get("/summary")
def summary(date_from: date | None = Query(None), date_to: date | None = Query(None),
            employee_id: str | None = Query(None),
            role: str | None = Query(None, pattern="^(developer_onroll|developer_offroll|intern)$"),
            task_id: str | None = Query(None),
            db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    from_d, to_d = _range(date_from, date_to)
    start = datetime.combine(from_d, time.min, tzinfo=timezone.utc)
    end = datetime.combine(to_d + timedelta(days=1), time.min, tzinfo=timezone.utc)

    user_query = db.query(User).filter(User.role != ADMIN)
    if employee_id:
        user_query = user_query.filter(User.id == employee_id)
    if role:
        user_query = user_query.filter(User.role == role)
    employees = user_query.all()
    employee_ids = [employee.id for employee in employees]
    role_query = db.query(User.role, func.count(User.id)).filter(User.id.in_(employee_ids)).group_by(User.role)
    roles = dict(role_query.all())
    attendance = (db.query(Attendance)
                  .filter(Attendance.work_date >= from_d, Attendance.work_date <= to_d,
                          Attendance.user_id.in_(employee_ids)).all())
    task_query = db.query(Task).filter(Task.created_at >= start, Task.created_at < end)
    if employee_id:
        task_query = task_query.filter(Task.assignee_id == employee_id)
    else:
        task_query = task_query.filter(Task.assignee_id.in_(employee_ids))
    if task_id:
        task_query = task_query.filter(Task.id == task_id)
    tasks = task_query.all()
    submission_query = db.query(TaskSubmission).filter(
        TaskSubmission.created_at >= start, TaskSubmission.created_at < end,
        TaskSubmission.user_id.in_(employee_ids))
    if task_id:
        submission_query = submission_query.filter(TaskSubmission.task_id == task_id)
    subs = submission_query.all()
    recording_query = db.query(ScreenRecording).filter(ScreenRecording.deleted_at.is_(None),
                                                  ScreenRecording.started_at >= start,
                                                  ScreenRecording.started_at < end,
                                                  ScreenRecording.user_id.in_(employee_ids))
    if task_id:
        recording_query = recording_query.filter(ScreenRecording.task_id == task_id)
    recordings = recording_query.all()

    total_hours = round(sum(a.active_seconds or 0 for a in attendance) / 3600, 2)
    by_status: dict[str, int] = {}
    for t in tasks:
        by_status[t.status] = by_status.get(t.status, 0) + 1

    per_user = []
    for u in employees:
        u_tasks = [t for t in tasks if t.assignee_id == u.id]
        done = len([t for t in u_tasks if t.status in ("completed", "approved", "final_approved")])
        hours = round(sum(a.active_seconds or 0 for a in attendance if a.user_id == u.id) / 3600, 2)
        per_user.append({
            "user_id": u.id, "name": u.full_name, "role": u.role,
            "tasks": len(u_tasks), "completed": done,
            "completion_rate": round(done / len(u_tasks) * 100, 1) if u_tasks else 0,
            "hours": hours, "submissions": len([s for s in subs if s.user_id == u.id]),
        })

    return {
        "range": {"from": from_d.isoformat(), "to": to_d.isoformat()},
        "headcount": {
            "total": sum(roles.values()), "admins": roles.get("admin", 0),
            "onroll": roles.get("developer_onroll", 0), "offroll": roles.get("developer_offroll", 0),
            "interns": roles.get("intern", 0),
        },
        "attendance": {
            "records": len(attendance),
            "total_hours": total_hours,
            "avg_hours": round(total_hours / max(len({a.user_id for a in attendance}), 1), 2),
            "days_present": len({a.work_date for a in attendance}),
        },
        "tasks": {
            "total": len(tasks), "by_status": by_status,
            "completed": by_status.get("completed", 0) + by_status.get("approved", 0) + by_status.get("final_approved", 0),
            "pending": by_status.get("not_started", 0) + by_status.get("in_progress", 0),
            "overdue": len([t for t in tasks if t.due_date and t.due_date < date.today()
                            and t.status not in ("completed", "approved", "final_approved")]),
            "completion_rate": round((by_status.get("completed", 0) + by_status.get("approved", 0) + by_status.get("final_approved", 0))
                                     / len(tasks) * 100, 1) if tasks else 0,
        },
        "submissions": {"total": len(subs),
                        "approved": len([s for s in subs if s.status in ("approved", "completed", "final_approved")]),
                        "pending_review": len([s for s in subs if s.status in ("submitted", "forwarded_to_admin")])},
        "recordings": {"count": len(recordings),
                       "total_minutes": round(sum(r.duration_seconds or 0 for r in recordings) / 60, 1)},
        "projects": db.query(func.count(Project.id)).filter(Project.deleted_at.is_(None)).scalar() or 0,
        "people": sorted(per_user, key=lambda x: x["completion_rate"], reverse=True),
    }


def _csv(rows: list[list], headers: list[str], filename: str = "workcode-report.csv") -> Response:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([_safe_spreadsheet_cell(value) for value in headers])
    writer.writerows([_safe_spreadsheet_cell(value) for value in row] for row in rows)
    return Response(content=buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def _safe_spreadsheet_cell(value):
    """Prevent spreadsheet programs from treating untrusted text as a formula."""
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def _tabular_export(rows: list[list], headers: list[str], export_format: str,
                    filename: str, title: str) -> Response:
    if export_format == "csv":
        return _csv(rows, headers, filename=f"{filename}.csv")
    if export_format == "xlsx":
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill

        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Report"
        sheet.append([_safe_spreadsheet_cell(value) for value in headers])
        for cell in sheet[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="1F4E78")
        for row in rows:
            sheet.append([_safe_spreadsheet_cell(value) for value in row])
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for column in sheet.columns:
            letter = column[0].column_letter
            sheet.column_dimensions[letter].width = min(48, max(14, max(len(str(cell.value or "")) for cell in column) + 2))
        buffer = io.BytesIO()
        workbook.save(buffer)
        return Response(buffer.getvalue(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f'attachment; filename="{filename}.xlsx"'})
    if export_format == "pdf":
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import landscape, letter
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib.units import inch
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

        buffer = io.BytesIO()
        document = SimpleDocTemplate(buffer, pagesize=landscape(letter),
                                     leftMargin=0.45 * inch, rightMargin=0.45 * inch,
                                     topMargin=0.45 * inch, bottomMargin=0.45 * inch)
        styles = getSampleStyleSheet()
        values = [[Paragraph(f"<b>{_pdf_text(value)}</b>", styles["BodyText"]) for value in headers]]
        values.extend([[Paragraph(_pdf_text(value or ""), styles["BodyText"]) for value in row] for row in rows])
        page_width = landscape(letter)[0] - 0.9 * inch
        column_width = page_width / max(len(headers), 1)
        table = Table(values, colWidths=[column_width] * len(headers), repeatRows=1)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f4e78")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#cbd5e1")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f1f5f9")]),
            ("FONTSIZE", (0, 0), (-1, -1), 7),
        ]))
        document.build([Paragraph(_pdf_text(title), styles["Title"]), Spacer(1, 0.18 * inch), table])
        return Response(buffer.getvalue(), media_type="application/pdf",
                        headers={"Content-Disposition": f'attachment; filename="{filename}.pdf"'})
    raise HTTPException(status_code=422, detail="format must be csv, xlsx or pdf")


@router.get("/export/attendance")
def export_attendance(date_from: date | None = Query(None), date_to: date | None = Query(None),
                      employee_id: str | None = Query(None),
                      role: str | None = Query(None, pattern="^(developer_onroll|developer_offroll|intern)$"),
                      format: str = Query("csv", pattern="^(csv|xlsx|pdf)$"),
                      db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    from_d, to_d = _range(date_from, date_to)
    rows = (db.query(Attendance).filter(Attendance.work_date >= from_d,
                                        Attendance.work_date <= to_d,
                                        Attendance.user_id.in_(_employee_ids(db, employee_id, role)))
            .order_by(Attendance.work_date.desc()).all())
    names = {u.id: u.full_name for u in db.query(User).all()}
    return _tabular_export([[names.get(a.user_id), a.work_date, a.status,
                             round((a.active_seconds or 0) / 3600, 2), round((a.break_seconds or 0) / 60, 1)]
                            for a in rows],
                           ["employee", "date", "status", "worked_hours", "break_minutes"],
                           format, "workcode-attendance", "Attendance report")


@router.get("/export/tasks")
def export_tasks(date_from: date | None = Query(None), date_to: date | None = Query(None),
                 employee_id: str | None = Query(None),
                 role: str | None = Query(None, pattern="^(developer_onroll|developer_offroll|intern)$"),
                 task_id: str | None = Query(None),
                 format: str = Query("csv", pattern="^(csv|xlsx|pdf)$"),
                 db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    from_d, to_d = _range(date_from, date_to)
    start = datetime.combine(from_d, time.min, tzinfo=timezone.utc)
    end = datetime.combine(to_d + timedelta(days=1), time.min, tzinfo=timezone.utc)
    task_query = db.query(Task).filter(Task.created_at >= start, Task.created_at < end,
                                       Task.assignee_id.in_(_employee_ids(db, employee_id, role)))
    if task_id:
        task_query = task_query.filter(Task.id == task_id)
    rows = task_query.order_by(Task.created_at.desc()).all()
    names = {u.id: u.full_name for u in db.query(User).all()}
    return _tabular_export([[t.code, t.title, names.get(t.assignee_id), names.get(t.assigned_by),
                             t.priority, t.status, t.progress, t.start_date, t.due_date]
                            for t in rows],
                           ["code", "title", "assignee", "assigned_by", "priority", "status",
                            "progress", "start_date", "due_date"],
                           format, "workcode-tasks", "Task report")


@router.get("/export/employees")
def export_employees(employee_id: str | None = Query(None),
                     role: str | None = Query(None, pattern="^(developer_onroll|developer_offroll|intern)$"),
                     format: str = Query("csv", pattern="^(csv|xlsx|pdf)$"),
                     db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    user_query = db.query(User).filter(User.role != "admin")
    if employee_id:
        user_query = user_query.filter(User.id == employee_id)
    if role:
        user_query = user_query.filter(User.role == role)
    rows = user_query.all()
    out = []
    for u in rows:
        p = u.profile
        out.append([u.full_name, u.email, u.role, u.is_active,
                    p.employee_code if p else "", p.joining_date if p else "",
                    p.employment_status if p else "", p.expected_end_date if p else ""])
    return _tabular_export(out, ["name", "email", "role", "active", "employee_code", "joining_date",
                                 "employment_status", "period_end"],
                           format, "workcode-employees", "Employee report")


@router.get("/export/timesheets")
def export_timesheets(date_from: date | None = Query(None), date_to: date | None = Query(None),
                      employee_id: str | None = Query(None),
                      role: str | None = Query(None, pattern="^(developer_onroll|developer_offroll|intern)$"),
                      format: str = Query("csv", pattern="^(csv|xlsx|pdf)$"),
                      db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    from_d, to_d = _range(date_from, date_to)
    start = datetime.combine(from_d, time.min, tzinfo=timezone.utc)
    end = datetime.combine(to_d + timedelta(days=1), time.min, tzinfo=timezone.utc)
    rows = (db.query(TimerSession)
            .filter(TimerSession.started_at >= start, TimerSession.started_at < end,
                    TimerSession.user_id.in_(_employee_ids(db, employee_id, role)))
            .order_by(TimerSession.started_at.desc()).all())
    names = {u.id: u.full_name for u in db.query(User).all()}
    return _tabular_export([[names.get(s.user_id), s.started_at, s.ended_at, s.status,
                             round((s.accumulated_seconds or 0) / 3600, 2), round((s.break_seconds or 0) / 60, 1)]
                            for s in rows],
                           ["employee", "started_at", "ended_at", "status", "hours", "break_minutes"],
                           format, "workcode-timesheets", "Timesheet report")
