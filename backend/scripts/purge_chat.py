"""Delete all chat content (conversations, messages, attachments, receipts, group metadata).

Monitor grants and the access audit log are kept on purpose: permissions and the
compliance trail must survive a content purge. Pass --all to wipe those too.

    python scripts/purge_chat.py            # wipe conversations + messages + files
    python scripts/purge_chat.py --all      # also wipe grants and audit log
"""
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.db import SessionLocal                      # noqa: E402
from app.models import (ChatAccessAudit, ChatAttachment, ChatGroupMeta, ChatMessage,  # noqa: E402
                        ChatMonitorGrant, ChatParticipant, ChatReadReceipt, ChatRoom)
from app.services import storage                          # noqa: E402


def main() -> None:
    db = SessionLocal()
    counts = {}
    for model in (ChatReadReceipt, ChatAttachment, ChatMessage, ChatGroupMeta, ChatParticipant, ChatRoom):
        counts[model.__tablename__] = db.query(model).delete()
    if "--all" in sys.argv:
        counts["chat_monitor_grants"] = db.query(ChatMonitorGrant).delete()
        counts["chat_access_audit"] = db.query(ChatAccessAudit).delete()
    db.commit()
    removed = 0
    base = storage.chat_dir()
    if base.exists():
        for child in base.iterdir():
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
                removed += 1
            else:
                child.unlink(missing_ok=True)
                removed += 1
    print({"deleted_rows": counts, "deleted_attachment_dirs": removed})


if __name__ == "__main__":
    main()
