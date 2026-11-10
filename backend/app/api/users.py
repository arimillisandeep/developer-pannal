"""User, employee-profile, team and conversion management."""
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import ALL_ROLES, get_current_user, require_admin
from app.core.security import hash_password, password_strength_error
from app.models import EmployeeProfile, EmploymentHistory, Team, TeamMember, User
from app.schemas import (
    ConvertIn, ProfileOut, TeamIn, TeamMemberIn, TeamOut, UserCreate, UserDetail, UserOut, UserUpdate,
)
from app.services import audit
from app.services.notify import notify

router = APIRouter(tags=["Users & Employees"])


def _next_code(db: Session) -> str:
    n = db.query(EmployeeProfile).count() + 1
    return f"WC-E{n:04d}"


def _add_months(d: date, months: int) -> date:
    y = d.year + (d.month - 1 + months) // 12
    m = (d.month - 1 + months) % 12 + 1
    day = min(d.day, [31, 29 if y % 4 == 0 and (y % 100 != 0 or y % 400 == 0) else 28,
                      31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1])
    return date(y, m, day)


# ------------------------------------------------------------------- self view
@router.get("/users/me/profile")
def my_profile(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    prof = db.query(EmployeeProfile).filter(EmployeeProfile.user_id == user.id).one_or_none()
    team = db.get(Team, prof.team_id) if prof and prof.team_id else None
    days_left = None
    if prof and prof.expected_end_date:
        days_left = (prof.expected_end_date - date.today()).days
    return {
        "user": UserOut.model_validate(user).model_dump(),
        "profile": ProfileOut.model_validate(prof).model_dump() if prof else None,
        "team": TeamOut.model_validate(team).model_dump() if team else None,
        "days_remaining": days_left,
        "role_label": {
            "admin": "Administrator",
            "developer_onroll": "Developer — On-Roll",
            "developer_offroll": "Developer — Off-Roll",
            "intern": "Intern",
        }.get(user.role, user.role),
    }


@router.patch("/users/me/profile", response_model=UserOut)
def update_my_profile(payload: UserUpdate, request: Request, db: Session = Depends(get_db),
                      user: User = Depends(get_current_user)):
    if payload.full_name:
        user.full_name = payload.full_name.strip()
    if payload.phone is not None:
        user.phone = payload.phone
    prof = db.query(EmployeeProfile).filter(EmployeeProfile.user_id == user.id).one_or_none()
    if prof and payload.skills is not None:
        prof.skills = payload.skills
    audit.log(db, user, "user.profile_updated", "user", user.id, request)
    return user


# ------------------------------------------------------------------ admin CRUD
@router.get("/users", response_model=list[UserDetail])
def list_users(
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
    role: str | None = Query(None),
    q: str | None = Query(None),
    is_active: bool | None = Query(None),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
):
    query = db.query(User)
    if role:
        query = query.filter(User.role == role)
    if is_active is not None:
        query = query.filter(User.is_active.is_(is_active))
    if q:
        like = f"%{q.lower()}%"
        query = query.filter((User.full_name.ilike(like)) | (User.email.ilike(like)) |
                             (User.username.ilike(like)))
    rows = query.order_by(User.created_at.desc()).offset((page - 1) * size).limit(size).all()
    return rows


@router.post("/users", response_model=UserDetail, status_code=201)
def create_user(payload: UserCreate, request: Request, db: Session = Depends(get_db),
                admin: User = Depends(require_admin)):
    if payload.role not in ALL_ROLES or payload.role == "admin":
        raise HTTPException(status_code=422, detail="Role must be developer_onroll, developer_offroll or intern. "
                                                   "Admin accounts are created with scripts/init_admins.py")
    err = password_strength_error(payload.password)
    if err:
        raise HTTPException(status_code=422, detail=err)

    user = User(
        email=payload.email.lower(), username=payload.username.strip().lower(),
        full_name=payload.full_name.strip(), role=payload.role,
        password_hash=hash_password(payload.password), phone=payload.phone,
        designation=payload.designation,
        avatar_color=["#4f8cff", "#22c55e", "#f59e0b", "#a855f7", "#ef4444", "#06b6d4"][
            db.query(User).count() % 6],
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError:
        raise HTTPException(status_code=409, detail="Email or username already exists")

    joining = payload.joining_date or date.today()
    expected = None
    if payload.role in ("developer_offroll", "intern"):
        expected = _add_months(joining, payload.period_months or 6)

    db.add(EmployeeProfile(
        user_id=user.id, employee_code=_next_code(db), team_id=payload.team_id,
        joining_date=joining, expected_end_date=expected,
        employment_status="probation" if payload.role in ("developer_offroll", "intern") else "active",
        skills=payload.skills,
    ))
    db.add(EmploymentHistory(user_id=user.id, event="joined", to_role=payload.role,
                             effective_date=joining, approved_by=admin.id))
    if payload.team_id:
        db.add(TeamMember(team_id=payload.team_id, user_id=user.id, joining_date=joining))
    audit.log(db, admin, "user.created", "user", user.id, request, role=payload.role)
    notify(db, user.id, "account", "Welcome to WORKCODE",
           f"Your {payload.role.replace('_', ' ')} account has been created.", "/profile")
    db.flush()
    return user


@router.get("/users/{user_id}", response_model=UserDetail)
def get_user(user_id: str, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.patch("/users/{user_id}", response_model=UserDetail)
def update_user(user_id: str, payload: UserUpdate, request: Request, db: Session = Depends(get_db),
                admin: User = Depends(require_admin)):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    data = payload.model_dump(exclude_unset=True)
    team_id = data.pop("team_id", None)
    for k, v in data.items():
        setattr(user, k, v)
    prof = db.query(EmployeeProfile).filter(EmployeeProfile.user_id == user.id).one_or_none()
    if prof:
        if team_id is not None:
            prof.team_id = team_id or None
            db.query(TeamMember).filter(TeamMember.user_id == user.id).delete()
            if team_id:
                db.add(TeamMember(team_id=team_id, user_id=user.id))
        for k, v in data.items():
            if hasattr(prof, k) and k in ("expected_end_date", "skills", "designation"):
                setattr(prof, k, v)
    audit.log(db, admin, "user.updated", "user", user.id, request, fields=list(data.keys()))
    return user


@router.post("/users/{user_id}/status", response_model=UserOut)
def set_status(user_id: str, request: Request, is_active: bool = Query(...),
               db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.role == "admin":
        raise HTTPException(status_code=403, detail="Admin accounts cannot be deactivated here")
    user.is_active = is_active
    audit.log(db, admin, "user.activated" if is_active else "user.deactivated", "user", user.id, request)
    return user


@router.post("/users/{user_id}/reset-password")
def admin_reset_password(user_id: str, request: Request, new_password: str = Query(..., min_length=8),
                         db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    err = password_strength_error(new_password)
    if err:
        raise HTTPException(status_code=422, detail=err)
    user.password_hash = hash_password(new_password)
    audit.log(db, admin, "user.password_reset_by_admin", "user", user.id, request)
    return {"detail": "Password reset"}


@router.post("/users/{user_id}/convert", response_model=UserDetail)
def convert_employee(user_id: str, payload: ConvertIn, request: Request, db: Session = Depends(get_db),
                     admin: User = Depends(require_admin)):
    """Off-Roll / Intern -> On-Roll. Always explicit, never automatic."""
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.role not in ("developer_offroll", "intern"):
        raise HTTPException(status_code=422, detail="Only Off-Roll developers or interns can be converted")
    if payload.to_role not in ("developer_onroll",):
        raise HTTPException(status_code=422, detail="Target role must be developer_onroll")

    prof = db.query(EmployeeProfile).filter(EmployeeProfile.user_id == user.id).one_or_none()
    old_role = user.role
    user.role = payload.to_role
    if prof:
        prof.employment_status = "converted"
    db.add(EmploymentHistory(
        user_id=user.id, event="converted", from_role=old_role, to_role=payload.to_role,
        effective_date=payload.effective_date or date.today(), approved_by=admin.id,
        notes=payload.notes,
    ))
    audit.log(db, admin, "employee.converted", "user", user.id, request,
              from_role=old_role, to_role=payload.to_role)
    notify(db, user.id, "employment", "Congratulations — you are now On-Roll",
           "Your employment status has been converted to Developer On-Roll.", "/profile")
    db.flush()
    return user


@router.get("/users/{user_id}/history")
def employment_history(user_id: str, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    rows = (db.query(EmploymentHistory).filter(EmploymentHistory.user_id == user_id)
            .order_by(EmploymentHistory.created_at.desc()).all())
    return [{"id": r.id, "event": r.event, "from_role": r.from_role, "to_role": r.to_role,
             "effective_date": r.effective_date, "approved_by": r.approved_by,
             "notes": r.notes, "created_at": r.created_at} for r in rows]


# ----------------------------------------------------------------------- teams
@router.get("/teams", response_model=list[TeamOut])
def list_teams(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return db.query(Team).order_by(Team.name).all()


@router.post("/teams", response_model=TeamOut, status_code=201)
def create_team(payload: TeamIn, request: Request, db: Session = Depends(get_db),
                admin: User = Depends(require_admin)):
    team = Team(name=payload.name.strip(), description=payload.description, lead_user_id=payload.lead_user_id)
    db.add(team)
    try:
        db.flush()
    except IntegrityError:
        raise HTTPException(status_code=409, detail="Team name already exists")
    audit.log(db, admin, "team.created", "team", team.id, request, name=team.name)
    return team


@router.get("/teams/{team_id}/members")
def team_members(team_id: str, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    rows = db.query(TeamMember).filter(TeamMember.team_id == team_id).all()
    out = []
    for m in rows:
        u = db.get(User, m.user_id)
        if u:
            out.append({"id": u.id, "full_name": u.full_name, "role": u.role,
                        "email": u.email, "role_in_team": m.role_in_team,
                        "avatar_color": u.avatar_color})
    return out


@router.post("/teams/{team_id}/members", status_code=201)
def add_team_member(team_id: str, payload: TeamMemberIn, request: Request,
                    db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    if not db.get(Team, team_id):
        raise HTTPException(status_code=404, detail="Team not found")
    if not db.get(User, payload.user_id):
        raise HTTPException(status_code=404, detail="User not found")
    exists = db.query(TeamMember).filter(TeamMember.team_id == team_id,
                                         TeamMember.user_id == payload.user_id).one_or_none()
    if exists:
        raise HTTPException(status_code=409, detail="User already in team")
    db.add(TeamMember(team_id=team_id, user_id=payload.user_id, role_in_team=payload.role_in_team))
    audit.log(db, admin, "team.member_added", "team", team_id, request, user_id=payload.user_id)
    return {"detail": "Member added"}


@router.delete("/teams/{team_id}/members/{user_id}")
def remove_team_member(team_id: str, user_id: str, request: Request,
                       db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    row = db.query(TeamMember).filter(TeamMember.team_id == team_id,
                                      TeamMember.user_id == user_id).one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Membership not found")
    db.delete(row)
    audit.log(db, admin, "team.member_removed", "team", team_id, request, user_id=user_id)
    return {"detail": "Member removed"}
