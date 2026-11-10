"""Purge data whose retention window has expired. Run from cron, e.g. weekly.

    cd backend && .venv/bin/python scripts/purge_retention.py --dry-run
"""
import argparse
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.db import SessionLocal  # noqa: E402
from app.models import PasswordResetToken, RecordingChunk, ScreenRecording, UserSession  # noqa: E402
from app.services.storage import recordings_dir  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    now = datetime.now(timezone.utc)

    db = SessionLocal()
    try:
        recs = (db.query(ScreenRecording)
                .filter(ScreenRecording.retention_until.isnot(None),
                        ScreenRecording.retention_until < now,
                        ScreenRecording.deleted_at.is_(None)).all())
        print(f"recordings past retention: {len(recs)}")
        for r in recs:
            if args.dry_run:
                continue
            r.deleted_at, r.status = now, "deleted"
            db.query(RecordingChunk).filter(RecordingChunk.recording_id == r.id).delete()
            shutil.rmtree(recordings_dir(r.user_id) / r.id, ignore_errors=True)

        old_tokens = db.query(PasswordResetToken).filter(PasswordResetToken.expires_at < now)
        print(f"expired password-reset tokens: {old_tokens.count()}")
        if not args.dry_run:
            old_tokens.delete(synchronize_session=False)

        old_sessions = db.query(UserSession).filter(UserSession.expires_at < now)
        print(f"expired sessions: {old_sessions.count()}")
        if not args.dry_run:
            old_sessions.delete(synchronize_session=False)

        if args.dry_run:
            db.rollback()
            print("dry run — nothing changed")
        else:
            db.commit()
            print("retention purge complete")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
