"""Create the three WORKCODE admin accounts.

Passwords are NEVER hardcoded. Priority order for each admin:
  1. ADMINn_PASSWORD environment variable
  2. --password-n CLI flag
  3. a cryptographically random password, printed ONCE to the terminal

Usage:
    cd backend
    .venv/bin/python scripts/init_admins.py
    .venv/bin/python scripts/init_admins.py --password-1 "Str0ng!pass" ...
"""
import argparse
import os
import secrets
import string
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.core.db import SessionLocal, init_db  # noqa: E402
from app.core.security import hash_password, password_strength_error  # noqa: E402
from app.models import AdminAccount, User  # noqa: E402

ADMIN_SLOTS = 3
COLORS = ["#4f8cff", "#22c55e", "#a855f7"]


def random_password(length: int = 16) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    while True:
        pwd = "".join(secrets.choice(alphabet) for _ in range(length))
        if password_strength_error(pwd) is None:
            return pwd


def main() -> int:
    parser = argparse.ArgumentParser(description="Create the three WORKCODE admin accounts")
    for i in range(1, ADMIN_SLOTS + 1):
        parser.add_argument(f"--password-{i}", dest=f"password_{i}", default=None)
    args = parser.parse_args()

    init_db()
    db = SessionLocal()
    created = []
    try:
        for slot in range(1, ADMIN_SLOTS + 1):
            email = os.getenv(f"ADMIN{slot}_EMAIL", f"admin{slot}@workcode.local").lower()
            username = os.getenv(f"ADMIN{slot}_USERNAME", f"admin{slot}").lower()
            full_name = os.getenv(f"ADMIN{slot}_NAME", f"Platform Admin {slot}")

            existing = db.execute(select(User).where(User.role == "admin")).scalars().all()
            if any(u.email == email for u in existing):
                print(f"[skip] admin slot {slot}: {email} already exists")
                continue
            if len(existing) >= ADMIN_SLOTS:
                print(f"[skip] admin slot {slot}: already {ADMIN_SLOTS} admins exist")
                continue

            pwd = getattr(args, f"password_{slot}") or os.getenv(f"ADMIN{slot}_PASSWORD")
            generated = False
            if not pwd:
                pwd = random_password()
                generated = True

            err = password_strength_error(pwd)
            if err:
                print(f"[error] admin {slot}: {err}")
                return 1

            user = User(
                email=email, username=username, full_name=full_name,
                role="admin", password_hash=hash_password(pwd),
                designation="Platform Administrator", avatar_color=COLORS[slot - 1],
            )
            db.add(user)
            db.flush()
            db.add(AdminAccount(user_id=user.id, admin_slot=slot, title="Platform Administrator"))
            db.commit()
            created.append((slot, email, username, pwd if generated else None))

        print("\n" + "=" * 68)
        if created:
            print(" WORKCODE admin accounts created — store these credentials NOW")
            print("=" * 68)
            for slot, email, username, pwd in created:
                print(f" slot {slot}: {email}  (username: {username})")
                if pwd:
                    print(f"         generated password: {pwd}")
                else:
                    print("         password: (the one you supplied)")
            print("=" * 68)
            print(" Generated passwords are shown once and are not stored in plaintext.")
        else:
            print(" No admin accounts were created (they already exist).")
            print("=" * 68)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
