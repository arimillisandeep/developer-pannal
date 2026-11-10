"""Code submissions and the review workflow.

Retention rule: a submission's snapshot is immutable and is never deleted by
the review flow. `rework_required` keeps the old snapshot so history survives.
"""
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import ADMIN, get_current_user, require_admin
from app.models import (
    CodeReview, Project, ProjectFile, ScreenRecording, SubmissionTestResults, Task,
    TaskSubmission, Team, User,
)
from app.schemas import ReviewIn, SubmissionIn, SubmissionOut
from app.services import audit, storage
from app.services.notify import notify, notify_admins

router = APIRouter(prefix="/submissions", tags=["Code Submissions"])


def _assigned_reviewer(db: Session, task: Task) -> User | None:
    """Resolve the On-Roll reviewer from the assignee or the task's team lead."""
    assigned_by = db.get(User, task.assigned_by) if task.assigned_by else None
    if assigned_by and assigned_by.is_active and assigned_by.role == "developer_onroll":
        return assigned_by

    team = db.get(Team, task.team_id) if task.team_id else None
    reviewer = db.get(User, team.lead_user_id) if team and team.lead_user_id else None
    if reviewer and reviewer.is_active and reviewer.role == "developer_onroll":
        return reviewer
    return None


def _require_assigned_reviewer(db: Session, task: Task, user: User) -> None:
    reviewer = _assigned_reviewer(db, task)
    if user.role != "developer_onroll" or not reviewer or reviewer.id != user.id:
        raise HTTPException(status_code=403, detail="Only the assigned On-Roll reviewer may review this submission")


def _snapshot(db: Session, project_id: str | None) -> list[dict]:
    if not project_id:
        return []
    rows = db.query(ProjectFile).filter(ProjectFile.project_id == project_id).order_by(ProjectFile.path).all()
    return [{"path": f.path, "content": f.content, "is_dir": f.is_dir} for f in rows]


@router.post("", response_model=SubmissionOut, status_code=201)
def submit_code(payload: SubmissionIn, request: Request, response: Response,
                db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if user.role == ADMIN:
        raise HTTPException(status_code=403, detail="Admins do not submit code")
    # Lock the task while checking for an in-flight snapshot. This makes a
    # retried submit request idempotent on PostgreSQL without adding schema.
    task = db.query(Task).filter(Task.id == payload.task_id).with_for_update().first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.assignee_id != user.id:
        raise HTTPException(status_code=403, detail="You can only submit work for your own tasks")
    pending = (db.query(TaskSubmission)
               .filter(TaskSubmission.task_id == task.id,
                       TaskSubmission.user_id == user.id,
                       TaskSubmission.status.in_(("submitted", "pending_permanent_review", "approved",
                                                  "forwarded_to_admin", "pending_admin_review")))
               .order_by(TaskSubmission.created_at.desc()).first())
    if pending:
        response.status_code = 200
        return pending
    reviewer = _assigned_reviewer(db, task)
    if not reviewer:
        raise HTTPException(status_code=409, detail="This task needs an active On-Roll reviewer before code can be submitted")
    if reviewer.id == user.id:
        raise HTTPException(status_code=409, detail="You cannot submit work for review by yourself")

    project = None
    if payload.project_id:
        project = db.get(Project, payload.project_id)
        if not project or project.deleted_at is not None:
            raise HTTPException(status_code=404, detail="Project not found")
        if project.owner_id != user.id:
            raise HTTPException(status_code=403, detail="Project does not belong to you")
    if task.project_id:
        if not project or project.id != task.project_id:
            raise HTTPException(status_code=403, detail="Open the project linked to this task before submitting")
    elif not project:
        raise HTTPException(status_code=422, detail="Select a project to submit for this task")

    files = _snapshot(db, project.id if project else task.project_id)
    if not files:
        raise HTTPException(status_code=422, detail="Nothing to submit — save your project first")

    version_no = db.query(TaskSubmission).filter(TaskSubmission.task_id == task.id).count() + 1
    sub = TaskSubmission(
        task_id=task.id, user_id=user.id, project_id=project.id if project else None,
        version_no=version_no, snapshot=json.dumps(files), notes=payload.notes,
        status="pending_permanent_review",
    )
    db.add(sub)
    db.flush()
    if payload.test_results:
        db.add(SubmissionTestResults(
            submission_id=sub.id,
            results_json=json.dumps([result.model_dump() for result in payload.test_results]),
        ))

    # immutable copy on disk (never overwritten, never auto-deleted)
    try:
        storage.write_snapshot(user.id, (project.id if project else task.id), "submissions",
                               f"sub{version_no:04d}-{sub.id[:8]}", files)
    except OSError:
        pass

    task.status = "submitted"
    task.progress = max(task.progress, 90)

    audit.log(db, user, "submission.created", "submission", sub.id, request,
              task=task.code, version=version_no, files=len(files))

    if reviewer.id != user.id:
        notify(db, reviewer.id, "submission_received",
               f"Code submitted for {task.code}", f"{user.full_name} submitted v{version_no}.",
               f"/reviews")
    db.flush()
    return sub


@router.get("", response_model=list[SubmissionOut])
def list_submissions(db: Session = Depends(get_db), user: User = Depends(get_current_user),
                     task_id: str | None = Query(None), status: str | None = Query(None)):
    query = db.query(TaskSubmission)
    if user.role == ADMIN:
        pass
    elif user.role == "developer_onroll":
        team_ids = db.query(Team.id).filter(Team.lead_user_id == user.id)
        my_tasks = db.query(Task.id).filter(
            (Task.assigned_by == user.id) | Task.team_id.in_(team_ids)
        )
        query = query.filter((TaskSubmission.user_id == user.id) | TaskSubmission.task_id.in_(my_tasks))
    elif user.role == "developer_offroll":
        query = query.filter(TaskSubmission.user_id == user.id)
    else:
        query = query.filter(TaskSubmission.user_id == user.id)
    if task_id:
        query = query.filter(TaskSubmission.task_id == task_id)
    if status:
        query = query.filter(TaskSubmission.status == status)
    return query.order_by(TaskSubmission.created_at.desc()).limit(200).all()


@router.get("/{submission_id}")
def get_submission(submission_id: str, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    sub = db.get(TaskSubmission, submission_id)
    if not sub:
        raise HTTPException(status_code=404, detail="Submission not found")
    task = db.get(Task, sub.task_id)
    reviewer = _assigned_reviewer(db, task) if task else None
    allowed = user.role == ADMIN or sub.user_id == user.id or (reviewer and reviewer.id == user.id)
    if not allowed:
        raise HTTPException(status_code=403, detail="Access denied")

    reviews = (db.query(CodeReview).filter(CodeReview.submission_id == sub.id)
               .order_by(CodeReview.created_at).all())
    test_result_record = db.get(SubmissionTestResults, sub.id)
    recordings = (db.query(ScreenRecording)
                  .filter(ScreenRecording.user_id == sub.user_id,
                          ScreenRecording.task_id == sub.task_id,
                          ScreenRecording.deleted_at.is_(None))
                  .order_by(ScreenRecording.started_at.desc()).all())
    names = {u.id: u.full_name for u in db.query(User).all()}
    return {
        "id": sub.id, "task_id": sub.task_id, "user_id": sub.user_id,
        "reviewer_id": sub.reviewer_id,
        "submitter_name": names.get(sub.user_id), "project_id": sub.project_id,
        "version_no": sub.version_no, "notes": sub.notes, "status": sub.status,
        "review_notes": sub.review_notes, "created_at": sub.created_at,
        "reviewed_at": sub.reviewed_at, "forwarded_to_admin_at": sub.forwarded_to_admin_at,
        "final_decision": sub.final_decision,
        "recordings": [{"id": recording.id, "status": recording.status,
                        "started_at": recording.started_at,
                        "duration_seconds": recording.duration_seconds,
                        "size_bytes": recording.size_bytes}
                       for recording in recordings],
        "files": json.loads(sub.snapshot or "[]"),
        "test_results": json.loads(test_result_record.results_json) if test_result_record else [],
        "reviews": [{"id": r.id, "reviewer_id": r.reviewer_id,
                     "reviewer_name": names.get(r.reviewer_id), "decision": r.decision,
                     "comments": r.comments, "created_at": r.created_at} for r in reviews],
    }


@router.post("/{submission_id}/review")
def review_submission(submission_id: str, payload: ReviewIn, request: Request,
                      db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if payload.decision not in ("approved", "rejected", "rework"):
        raise HTTPException(status_code=422, detail="Decision must be approved, rejected or rework")
    if payload.decision in ("rejected", "rework") and not payload.comments.strip():
        raise HTTPException(status_code=422, detail="A reason is required when rejecting or requesting rework")
    sub = db.get(TaskSubmission, submission_id)
    if not sub:
        raise HTTPException(status_code=404, detail="Submission not found")
    if sub.user_id == user.id:
        raise HTTPException(status_code=403, detail="You cannot review your own submission")

    task = db.get(Task, sub.task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    _require_assigned_reviewer(db, task, user)
    if sub.status not in ("submitted", "pending_permanent_review", "rework_required"):
        raise HTTPException(status_code=409, detail="This submission is not awaiting On-Roll review")

    # On-Roll approval completes the first review stage and immediately puts
    # the submission in the Admin queue in the same request transaction.
    sub.status = {"approved": "pending_admin_review", "rejected": "rejected",
                  "rework": "rework_required"}[payload.decision]
    sub.reviewer_id = user.id
    sub.review_notes = payload.comments
    sub.reviewed_at = datetime.now(timezone.utc)
    db.add(CodeReview(submission_id=sub.id, reviewer_id=user.id,
                      decision=payload.decision, comments=payload.comments))

    if payload.decision == "approved":
        sub.forwarded_to_admin_at = sub.reviewed_at
        db.add(CodeReview(submission_id=sub.id, reviewer_id=user.id,
                          decision="forwarded", comments="Automatically forwarded after On-Roll approval"))
        audit.log(db, user, "submission.forwarded", "submission", sub.id, request,
                  task=task.code, automatic=True)

    if task:
        task.status = "under_review" if payload.decision == "approved" else "in_progress"

    audit.log(db, user, f"submission.{payload.decision}", "submission", sub.id, request,
              task=task.code if task else None)
    notify(db, sub.user_id, f"review_{payload.decision}",
           f"Review result for {task.code if task else 'your submission'}",
           payload.comments or f"Decision: {payload.decision}",
           f"/tasks/{sub.task_id}")
    if payload.decision == "approved":
        notify_admins(db, "submission_forwarded", "Submission needs final review",
                      f"{user.full_name} approved {task.code} and forwarded it for final review.",
                      f"/admin/submissions/{sub.id}")
    db.flush()
    return {"detail": f"Submission {payload.decision}", "status": sub.status}


@router.post("/{submission_id}/forward")
def forward_to_admin(submission_id: str, request: Request, db: Session = Depends(get_db),
                     user: User = Depends(get_current_user)):
    sub = db.get(TaskSubmission, submission_id)
    if not sub:
        raise HTTPException(status_code=404, detail="Submission not found")
    task = db.get(Task, sub.task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    _require_assigned_reviewer(db, task, user)
    # Keep the previous explicit-forward endpoint compatible with clients that
    # may still call it after approval has already forwarded automatically.
    if sub.status in ("forwarded_to_admin", "pending_admin_review") and sub.reviewer_id == user.id:
        return {"detail": "Already forwarded to admin"}
    if sub.status != "approved" or sub.reviewer_id != user.id:
        raise HTTPException(status_code=409, detail="Only your approved On-Roll review can be forwarded")

    sub.forwarded_to_admin_at = datetime.now(timezone.utc)
    sub.status = "forwarded_to_admin"
    task.status = "under_review"
    db.add(CodeReview(submission_id=sub.id, reviewer_id=user.id, decision="forwarded",
                      comments="Forwarded to admin for final review"))
    audit.log(db, user, "submission.forwarded", "submission", sub.id, request)
    notify_admins(db, "submission_forwarded", "Submission needs final review",
                  f"{user.full_name} forwarded {task.code if task else sub.task_id}",
                  f"/admin/submissions/{sub.id}")
    db.flush()
    return {"detail": "Forwarded to admin"}


@router.post("/{submission_id}/final")
def final_decision(submission_id: str, payload: ReviewIn, request: Request,
                   db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    sub = db.get(TaskSubmission, submission_id)
    if not sub:
        raise HTTPException(status_code=404, detail="Submission not found")
    if payload.decision not in ("approved", "rejected", "rework"):
        raise HTTPException(status_code=422, detail="Decision must be approved, rejected or rework")
    if payload.decision in ("rejected", "rework") and not payload.comments.strip():
        raise HTTPException(status_code=422, detail="A reason is required for the Admin decision")
    if sub.status not in ("forwarded_to_admin", "pending_admin_review"):
        raise HTTPException(status_code=409, detail="Submission must be approved by On-Roll and forwarded first")
    sub.final_decision = payload.decision
    sub.status = {"approved": "final_approved", "rejected": "rejected", "rework": "rework_required"}[payload.decision]
    task = db.get(Task, sub.task_id)
    if task:
        task.status = "final_approved" if payload.decision == "approved" else "rework_required"
        if payload.decision == "approved":
            task.progress = 100
            task.completed_at = datetime.now(timezone.utc)
    db.add(CodeReview(submission_id=sub.id, reviewer_id=admin.id,
                      decision=f"admin_{payload.decision}", comments=payload.comments))
    audit.log(db, admin, f"submission.final_{payload.decision}", "submission", sub.id, request)
    notify(db, sub.user_id, "submission_final_decision",
           f"Admin decision: {payload.decision}", payload.comments or "Final approval recorded.",
           f"/tasks/{sub.task_id}")
    reviewer = _assigned_reviewer(db, task) if task else None
    if reviewer:
        notify(db, reviewer.id, "submission_final_decision",
               f"Admin decision: {payload.decision}",
               f"Final decision recorded for {task.code if task else sub.task_id}.",
               f"/reviews")
    db.flush()
    return {"detail": "Final decision recorded", "status": sub.status}


@router.get("/{submission_id}/download")
def download_submission(submission_id: str, db: Session = Depends(get_db),
                        user: User = Depends(get_current_user)):
    from fastapi import Response
    sub = db.get(TaskSubmission, submission_id)
    if not sub:
        raise HTTPException(status_code=404, detail="Submission not found")
    task = db.get(Task, sub.task_id)
    reviewer = _assigned_reviewer(db, task) if task else None
    if user.role != ADMIN and sub.user_id != user.id and not (reviewer and reviewer.id == user.id):
        raise HTTPException(status_code=403, detail="Access denied")
    data = storage.zip_bytes(json.loads(sub.snapshot or "[]"))
    return Response(content=data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="submission-{sub.id[:8]}.zip"'})
