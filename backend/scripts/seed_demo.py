"""Optional demo data so a fresh install is not empty.

Creates: 2 teams, 2 on-roll devs, 2 off-roll devs, 2 interns, tasks in every
state, one project per developer and a couple of notifications.

    cd backend && .venv/bin/python scripts/seed_demo.py --admin-email admin1@workcode.local
"""
import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.core.db import SessionLocal, init_db  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.models import (  # noqa: E402
    EmployeeProfile, EmploymentHistory, Project, ProjectFile, Task, Team, TeamMember, User,
)
from app.services.notify import notify  # noqa: E402

DEMO_PASSWORD = "Workcode#2026"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--admin-email", default="admin1@workcode.local")
    args = ap.parse_args()

    init_db()
    db = SessionLocal()
    try:
        admin = db.execute(select(User).where(User.email == args.admin_email)).scalar_one_or_none()
        if not admin:
            print(f"No admin found for {args.admin_email}. Run scripts/init_admins.py first.")
            return 1

        teams = {}
        for name, desc in [("Platform Engineering", "Core services & APIs"),
                           ("Web Experience", "Front-end & design systems")]:
            t = db.execute(select(Team).where(Team.name == name)).scalar_one_or_none()
            if not t:
                t = Team(name=name, description=desc, lead_user_id=admin.id)
                db.add(t)
                db.flush()
            teams[name] = t

        people = [
            ("arun.kumar@workcode.local", "arun", "Arun Kumar", "developer_onroll", "Platform Engineering", 0),
            ("priya.sharma@workcode.local", "priya", "Priya Sharma", "developer_onroll", "Web Experience", 0),
            ("rahul.verma@workcode.local", "rahul", "Rahul Verma", "developer_offroll", "Platform Engineering", 4),
            ("meera.nair@workcode.local", "meera", "Meera Nair", "developer_offroll", "Web Experience", 2),
            ("kabir.singh@workcode.local", "kabir", "Kabir Singh", "intern", "Platform Engineering", 1),
            ("sara.ali@workcode.local", "sara", "Sara Ali", "intern", "Web Experience", 3),
        ]

        created_users = []
        for email, username, name, role, team, months_ago in people:
            u = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
            if u:
                created_users.append(u)
                continue
            joining = date.today() - timedelta(days=30 * months_ago + 15)
            u = User(email=email, username=username, full_name=name, role=role,
                     password_hash=hash_password(DEMO_PASSWORD), designation="Software Engineer")
            db.add(u)
            db.flush()
            n = db.execute(select(EmployeeProfile)).scalars().all()
            db.add(EmployeeProfile(
                user_id=u.id, employee_code=f"WC-E{len(n) + 1:04d}", team_id=teams[team].id,
                manager_id=admin.id, joining_date=joining,
                expected_end_date=None if role == "developer_onroll" else joining + timedelta(days=182),
                employment_status="active" if role == "developer_onroll" else "probation",
            ))
            db.add(TeamMember(team_id=teams[team].id, user_id=u.id, joining_date=joining))
            db.add(EmploymentHistory(user_id=u.id, event="joined", to_role=role,
                                     effective_date=joining, approved_by=admin.id))
            created_users.append(u)

        db.flush()

        for i, u in enumerate(created_users):
            proj = db.execute(select(Project).where(Project.owner_id == u.id)).scalar_one_or_none()
            if not proj:
                proj = Project(owner_id=u.id, name=f"{u.full_name.split()[0]} Sandbox",
                               language="python", description="Demo project",
                               storage_key="pending", team_id=u.profile.team_id if u.profile else None)
                db.add(proj)
                db.flush()
                proj.storage_key = f"users/{u.id}/projects/{proj.id}"
                db.add(ProjectFile(project_id=proj.id, path="main.py",
                                   content='def greet(name):\n    return f"Hello, {name}!"\n\n'
                                           'print(greet("WORKCODE"))\n', size=60))

            for j, (title, status, prio, prog) in enumerate([
                ("Build authentication module", "in_progress", "high", 45),
                ("Write API integration tests", "not_started", "medium", 0),
                ("Refactor dashboard components", "completed", "medium", 100),
            ]):
                code = f"WC-D{i}{j}"
                exists = db.execute(select(Task).where(Task.code == code)).scalar_one_or_none()
                if exists:
                    continue
                db.add(Task(
                    code=code, title=title, description=f"Demo task for {u.full_name}",
                    assigned_by=admin.id, assignee_id=u.id,
                    team_id=u.profile.team_id if u.profile else None, project_id=proj.id,
                    priority=prio, status=status, progress=prog,
                    start_date=date.today() - timedelta(days=5),
                    due_date=date.today() + timedelta(days=7 + j * 3),
                ))

        db.flush()
        notify(db, created_users[0].id, "general", "Welcome to WORKCODE",
               "Your demo workspace is ready — open the Coding Workspace to start.", "/workspace")
        db.commit()
        print(f"Demo data ready. {len(created_users)} employees (password: {DEMO_PASSWORD})")
        print("Sign in at /login — e.g. arun.kumar@workcode.local")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
