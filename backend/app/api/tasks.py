"""Task management, task threads (comments) and the review hand-off."""
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import ADMIN, DEVELOPERS, WORKERS, get_current_user, require_admin
from app.models import (
    ChatMessage, ChatParticipant, ChatRoom, Project, Task, TaskAssignment, Team, TeamMember, User,
)
from app.schemas import CommentIn, TaskIn, TaskOut, TaskUpdate
from app.services import audit
from app.services.notify import notify, notify_admins
from app.ws.manager import manager

router = APIRouter(prefix="/tasks", tags=["Tasks"])

STATUSES = ["not_started", "in_progress", "submitted", "under_review",
            "rework_required", "approved", "completed", "final_approved", "overdue"]


def _next_code(db: Session) -> str:
    n = (db.query(func.count(Task.id)).scalar() or 0) + 1
    while db.query(Task).filter(Task.code == f"WC-{n:04d}").one_or_none():
        n += 1
    return f"WC-{n:04d}"


def can_view(task: Task, user: User, db: Session) -> bool:
    if user.role == ADMIN:
        return True
    if task.assignee_id == user.id or task.assigned_by == user.id:
        return True
    if user.role in DEVELOPERS and task.team_id:
        team = db.get(Team, task.team_id)
        if team and team.lead_user_id == user.id:
            return True
        return db.query(TeamMember).filter(TeamMember.team_id == task.team_id,
                                           TeamMember.user_id == user.id).one_or_none() is not None
    return False


def _assignee_allowed(db: Session, admin_or_dev: User, assignee: User) -> bool:
    if admin_or_dev.role == ADMIN:
        return assignee.role in WORKERS
    if admin_or_dev.role not in DEVELOPERS:
        return False
    if assignee.role not in WORKERS or assignee.id == admin_or_dev.id:
        return False
    # developers may only assign inside their own team
    my_teams = {m.team_id for m in db.query(TeamMember).filter(TeamMember.user_id == admin_or_dev.id)}
    their_teams = {m.team_id for m in db.query(TeamMember).filter(TeamMember.user_id == assignee.id)}
    return bool(my_teams & their_teams)


@router.get("", response_model=list[TaskOut])
def list_tasks(
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
    status: str | None = Query(None), assignee_id: str | None = Query(None),
    mine: bool = Query(False), q: str | None = Query(None), overdue: bool = Query(False),
    page: int = Query(1, ge=1), size: int = Query(100, ge=1, le=300),
):
    
    query = db.query(Task)

    if mine:
        assigned_task_ids = (
            db.query(TaskAssignment.task_id)
            .filter(TaskAssignment.user_id == user.id)
            .subquery()
        )

        query = query.filter(
            or_(
                Task.assignee_id == user.id,
                Task.id.in_(assigned_task_ids)
            )
        )

    elif user.role == ADMIN:
        if assignee_id:
            query = query.filter(Task.assignee_id == assignee_id)

    elif user.role in DEVELOPERS:
        team_ids = {
            m.team_id
            for m in db.query(TeamMember).filter(
                TeamMember.user_id == user.id
            )
        }

        team_ids.update(
            team.id
            for team in db.query(Team).filter(
                Team.lead_user_id == user.id
            )
        )

        query = query.filter(
            or_(
                Task.assignee_id == user.id,
                Task.assigned_by == user.id,
                Task.team_id.in_(team_ids) if team_ids else False
            )
        )

    else:
        query = query.filter(Task.assignee_id == user.id)

    if status:
        query = query.filter(Task.status == status)
    if q:
        like = f"%{q.lower()}%"
        query = query.filter(or_(Task.title.ilike(like), Task.code.ilike(like)))
    if overdue:
        query = query.filter(Task.due_date < date.today(),
                             Task.status.notin_(["completed", "approved", "final_approved"]))

    rows = (query.order_by(Task.created_at.desc())
            .offset((page - 1) * size).limit(size).all())
    # lazily flag overdue tasks on read (kept separate from stored status history)
    for t in rows:
        if t.due_date and t.due_date < date.today() and t.status not in ("completed", "approved", "final_approved", "overdue"):
            t.status = "overdue"
    return rows


@router.post("", response_model=TaskOut, status_code=201)
def create_task(payload: TaskIn, request: Request, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    if user.role not in (ADMIN, *DEVELOPERS):
        raise HTTPException(status_code=403, detail="Only admins or developers can assign tasks")

    assignee = db.get(User, payload.assignee_id)
    if not assignee or not assignee.is_active:
        raise HTTPException(status_code=404, detail="Assignee not found or inactive")
    if not _assignee_allowed(db, user, assignee):
        raise HTTPException(status_code=403, detail="You may only assign tasks to authorized team members")

    team = None
    if payload.team_id:
        team = db.get(Team, payload.team_id)
        if not team:
            raise HTTPException(status_code=404, detail="Team not found")

    if user.role == ADMIN or user.role == "developer_offroll":
        if not team:
            raise HTTPException(status_code=422, detail="Choose an On-Roll review team for this task")
        reviewer = db.get(User, team.lead_user_id) if team.lead_user_id else None
        if not reviewer or not reviewer.is_active or reviewer.role != "developer_onroll":
            raise HTTPException(status_code=422, detail="Assign the task to a team with an active On-Roll lead reviewer")
    elif user.role == "developer_onroll" and team:
        reviewer = db.get(User, team.lead_user_id) if team.lead_user_id else None
        if reviewer and reviewer.id != user.id:
            raise HTTPException(status_code=422, detail="A task assigned by On-Roll must be reviewed by that On-Roll user")

    if payload.project_id:
        proj = db.get(Project, payload.project_id)
        if not proj:
            raise HTTPException(status_code=404, detail="Project not found")
        if proj.owner_id != assignee.id:
            raise HTTPException(status_code=403, detail="Link only a project owned by the task assignee")
        if user.role != ADMIN and proj.owner_id != user.id and proj.team_id not in {
                m.team_id for m in db.query(TeamMember).filter(TeamMember.user_id == user.id)}:
            raise HTTPException(status_code=403, detail="Project is outside your team")

    task = Task(
        code=_next_code(db), title=payload.title.strip(), description=payload.description,
        assigned_by=user.id, assignee_id=payload.assignee_id, team_id=payload.team_id,
        project_id=payload.project_id, priority=payload.priority,
        start_date=payload.start_date or date.today(), due_date=payload.due_date,
        status="not_started",
    )
    db.add(task)
    db.flush()
    db.add(TaskAssignment(task_id=task.id, user_id=payload.assignee_id,
                          assigned_by=user.id, is_primary=True))
    if team:
        for member_id in (assignee.id, team.lead_user_id):
            if not member_id:
                continue
            reviewer_member = db.query(TeamMember).filter(TeamMember.team_id == team.id,
                                                          TeamMember.user_id == member_id).one_or_none()
            if not reviewer_member:
                db.add(TeamMember(team_id=team.id, user_id=member_id, role_in_team="member"))
    audit.log(db, user, "task.created", "task", task.id, request, code=task.code,
              assignee=payload.assignee_id)
    notify(db, assignee.id, "task_assigned", f"New task assigned: {task.code}",
           task.title, f"/tasks/{task.id}")
    if user.role != ADMIN:
        notify_admins(db, "task_created", "New task created",
                      f"{user.full_name} assigned {task.code} to {assignee.full_name}",
                      f"/admin/tasks/{task.id}")
    db.flush()
    return task


@router.get("/{task_id}", response_model=TaskOut)
def get_task(task_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if not can_view(task, user, db):
        raise HTTPException(status_code=403, detail="You do not have access to this task")
    return task


@router.patch("/{task_id}", response_model=TaskOut)
def update_task(task_id: str, payload: TaskUpdate, request: Request, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if not can_view(task, user, db):
        raise HTTPException(status_code=403, detail="Access denied")

    data = payload.model_dump(exclude_unset=True)

    # assignees may only move their own progress/status; assignment changes are managers only
    if user.role == WORKERS and user.id == task.assignee_id:
        allowed = {"status", "progress"}
        blocked = set(data) - allowed
        if blocked:
            raise HTTPException(status_code=403, detail=f"Employees cannot change: {', '.join(blocked)}")
        if "status" in data and data["status"] in ("approved", "completed", "final_approved", "under_review"):
            raise HTTPException(status_code=403, detail="Review statuses are set by a reviewer")
    elif user.role not in (ADMIN, *DEVELOPERS):
        raise HTTPException(status_code=403, detail="Access denied")

    if "status" in data and data["status"] not in STATUSES:
        raise HTTPException(status_code=422, detail="Invalid status")
    if "project_id" in data:
        project = db.get(Project, data["project_id"]) if data["project_id"] else None
        if data["project_id"] and (not project or project.owner_id != task.assignee_id):
            raise HTTPException(status_code=403, detail="Project must belong to the task assignee")
        if user.role not in (ADMIN, *DEVELOPERS):
            raise HTTPException(status_code=403, detail="Only task managers may link a project")

    if "team_id" in data:
        if user.role != ADMIN:
            raise HTTPException(status_code=403, detail="Only admins may change a task's review team")
        if data["team_id"]:
            team = db.get(Team, data["team_id"])
            reviewer = db.get(User, team.lead_user_id) if team and team.lead_user_id else None
            if not reviewer or not reviewer.is_active or reviewer.role != "developer_onroll":
                raise HTTPException(status_code=422, detail="The review team must have an active On-Roll lead")

    if "assignee_id" in data:
        new_assignee = db.get(User, data["assignee_id"])
        if not new_assignee:
            raise HTTPException(status_code=404, detail="Assignee not found")
        if not _assignee_allowed(db, user, new_assignee):
            raise HTTPException(status_code=403, detail="Assignee is outside your authorization")
        notify(db, new_assignee.id, "task_assigned", f"Task reassigned to you: {task.code}",
               task.title, f"/tasks/{task.id}")

    for k, v in data.items():
        setattr(task, k, v)
    if "team_id" in data and task.team_id:
        for member_id in (task.assignee_id, db.get(Team, task.team_id).lead_user_id):
            if not member_id:
                continue
            team_member = db.query(TeamMember).filter(TeamMember.team_id == task.team_id,
                                                      TeamMember.user_id == member_id).one_or_none()
            if not team_member:
                db.add(TeamMember(team_id=task.team_id, user_id=member_id, role_in_team="member"))
    if data.get("status") in ("completed", "approved", "final_approved"):
        task.completed_at = datetime.now(timezone.utc)
        task.progress = 100

    audit.log(db, user, "task.updated", "task", task.id, request, fields=list(data.keys()))
    if task.assigned_by and task.assigned_by != user.id:
        notify(db, task.assigned_by, "task_updated", f"Task {task.code} updated",
               f"{user.full_name} updated {task.title}", f"/tasks/{task.id}")
    db.flush()
    return task


@router.delete("/{task_id}")
def delete_task(task_id: str, request: Request, db: Session = Depends(get_db),
                user: User = Depends(require_admin)):
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if db.query(__import__("app.models", fromlist=["TaskSubmission"]).TaskSubmission).filter_by(task_id=task_id).count():
        raise HTTPException(status_code=409,
                            detail="Task has submissions and cannot be deleted (retention policy). Archive it instead.")
    db.delete(task)
    audit.log(db, user, "task.deleted", "task", task_id, request, code=task.code)
    return {"detail": "Task deleted"}


# ------------------------------------------------------- task thread (comments)
def _task_room(db: Session, task: Task) -> ChatRoom:
    room = (db.query(ChatRoom).filter(ChatRoom.type == "task", ChatRoom.name == task.id).one_or_none())
    if room:
        return room
    room = ChatRoom(type="task", name=task.id, created_by=task.assigned_by)
    db.add(room)
    db.flush()
    for uid in {task.assignee_id, task.assigned_by}:
        if uid:
            db.add(ChatParticipant(room_id=room.id, user_id=uid))
    return room


@router.get("/{task_id}/comments")
def list_comments(task_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    task = db.get(Task, task_id)
    if not task or not can_view(task, user, db):
        raise HTTPException(status_code=404, detail="Task not found")
    room = db.query(ChatRoom).filter(ChatRoom.type == "task", ChatRoom.name == task_id).one_or_none()
    if not room:
        return []
    rows = (db.query(ChatMessage).filter(ChatMessage.room_id == room.id)
            .order_by(ChatMessage.created_at).limit(300).all())
    names = {u.id: u.full_name for u in db.query(User).all()}
    return [{"id": m.id, "body": m.body, "sender_id": m.sender_id,
             "sender_name": names.get(m.sender_id, "Unknown"), "created_at": m.created_at} for m in rows]


@router.post("/{task_id}/comments", status_code=201)
def add_comment(task_id: str, payload: CommentIn, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if not can_view(task, user, db):
        raise HTTPException(status_code=403, detail="Access denied")
    if not payload.body.strip():
        raise HTTPException(status_code=422, detail="Comment cannot be empty")

    room = _task_room(db, task)
    msg = ChatMessage(room_id=room.id, sender_id=user.id, body=payload.body.strip())
    db.add(msg)
    db.flush()
    for uid in {task.assignee_id, task.assigned_by} - {user.id}:
        if uid:
            notify(db, uid, "task_comment", f"New comment on {task.code}", payload.body[:140],
                   f"/tasks/{task.id}")
    manager.publish_room(room.id, {"channel": "message", "data": {
        "id": msg.id, "room_id": room.id, "sender_id": user.id,
        "sender_name": user.full_name, "body": msg.body,
        "created_at": msg.created_at.isoformat()}})
    return {"id": msg.id, "body": msg.body, "created_at": msg.created_at}
