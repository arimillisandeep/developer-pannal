"""All WORKCODE tables (29 tables) in one declarative module.

Portable on PostgreSQL and SQLite: UUIDs are stored as 36-char strings and all
timestamps are timezone-aware UTC.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, Column, Date, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.core.db import Base


def uid() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


# --------------------------------------------------------------------- identity
class Role(Base):
    __tablename__ = "roles"
    id = Column(Integer, primary_key=True)
    name = Column(String(40), unique=True, nullable=False)
    description = Column(String(255), default="")


class User(Base, TimestampMixin):
    __tablename__ = "users"
    id = Column(String(36), primary_key=True, default=uid)
    email = Column(String(255), unique=True, nullable=False, index=True)
    username = Column(String(80), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    full_name = Column(String(160), nullable=False)
    role = Column(String(32), nullable=False, index=True)  # admin | developer_onroll | developer_offroll | intern
    is_active = Column(Boolean, default=True, nullable=False, index=True)
    phone = Column(String(32), default="")
    designation = Column(String(120), default="")
    avatar_color = Column(String(16), default="#4f8cff")
    last_login_at = Column(DateTime(timezone=True))

    profile = relationship("EmployeeProfile", back_populates="user", uselist=False,
                           cascade="all, delete-orphan",
                           foreign_keys="EmployeeProfile.user_id")
    admin_account = relationship("AdminAccount", back_populates="user", uselist=False,
                                 cascade="all, delete-orphan")


class AdminAccount(Base):
    __tablename__ = "admin_accounts"
    id = Column(String(36), primary_key=True, default=uid)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    admin_slot = Column(Integer, unique=True, nullable=False)  # 1-3 default, 4+ extra admins
    title = Column(String(120), default="Platform Administrator")
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    user = relationship("User", back_populates="admin_account")


class EmployeeProfile(Base, TimestampMixin):
    __tablename__ = "employee_profiles"
    id = Column(String(36), primary_key=True, default=uid)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    employee_code = Column(String(32), unique=True, nullable=False, index=True)
    team_id = Column(String(36), ForeignKey("teams.id", ondelete="SET NULL"), index=True)
    manager_id = Column(String(36), ForeignKey("users.id", ondelete="SET NULL"))
    joining_date = Column(Date, nullable=False)
    expected_end_date = Column(Date)          # off-roll / intern period end
    employment_status = Column(String(32), default="active")  # active | probation | converted | exited
    skills = Column(Text, default="")
    address = Column(String(255), default="")
    emergency_contact = Column(String(120), default="")
    user = relationship("User", back_populates="profile", foreign_keys=[user_id])


class EmploymentHistory(Base):
    __tablename__ = "employment_history"
    id = Column(String(36), primary_key=True, default=uid)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    event = Column(String(40), nullable=False)   # joined | converted | role_changed | exited | reactivated
    from_role = Column(String(32))
    to_role = Column(String(32))
    effective_date = Column(Date, nullable=False)
    approved_by = Column(String(36), ForeignKey("users.id", ondelete="SET NULL"))
    notes = Column(Text, default="")
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)


class Team(Base, TimestampMixin):
    __tablename__ = "teams"
    id = Column(String(36), primary_key=True, default=uid)
    name = Column(String(120), unique=True, nullable=False)
    description = Column(Text, default="")
    lead_user_id = Column(String(36), ForeignKey("users.id", ondelete="SET NULL"))


class TeamMember(Base):
    __tablename__ = "team_members"
    __table_args__ = (UniqueConstraint("team_id", "user_id", name="uq_team_member"),)
    id = Column(String(36), primary_key=True, default=uid)
    team_id = Column(String(36), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    role_in_team = Column(String(40), default="member")
    joining_date = Column(Date)
    joined_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"
    id = Column(String(36), primary_key=True, default=uid)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash = Column(String(64), unique=True, nullable=False, index=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)


class UserSession(Base):
    __tablename__ = "user_sessions"
    id = Column(String(36), primary_key=True, default=uid)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    refresh_jti = Column(String(64), unique=True, nullable=False, index=True)
    ip = Column(String(64), default="")
    user_agent = Column(String(400), default="")
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_seen_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    revoked_at = Column(DateTime(timezone=True))


# --------------------------------------------------------------------- projects
class Project(Base, TimestampMixin):
    __tablename__ = "projects"
    __table_args__ = (UniqueConstraint("owner_id", "name", name="uq_project_owner_name"),)
    id = Column(String(36), primary_key=True, default=uid)
    owner_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    team_id = Column(String(36), ForeignKey("teams.id", ondelete="SET NULL"), index=True)
    name = Column(String(160), nullable=False)
    description = Column(Text, default="")
    language = Column(String(40), default="python")
    storage_key = Column(String(255), nullable=False)
    current_version = Column(Integer, default=1, nullable=False)
    is_archived = Column(Boolean, default=False)
    deleted_at = Column(DateTime(timezone=True))
    last_opened_at = Column(DateTime(timezone=True))


class ProjectFile(Base):
    __tablename__ = "project_files"
    __table_args__ = (UniqueConstraint("project_id", "path", name="uq_project_file_path"),)
    id = Column(String(36), primary_key=True, default=uid)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    path = Column(String(500), nullable=False)
    content = Column(Text, default="")
    is_dir = Column(Boolean, default=False)
    size = Column(Integer, default=0)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class ProjectVersion(Base):
    __tablename__ = "project_versions"
    __table_args__ = (UniqueConstraint("project_id", "version_no", name="uq_project_version"),)
    id = Column(String(36), primary_key=True, default=uid)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    version_no = Column(Integer, nullable=False)
    label = Column(String(160), default="")
    snapshot = Column(Text, default="[]")     # JSON [{path, content}]
    created_by = Column(String(36), ForeignKey("users.id", ondelete="SET NULL"))
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)


# ------------------------------------------------------------------------ tasks
class Task(Base, TimestampMixin):
    __tablename__ = "tasks"
    id = Column(String(36), primary_key=True, default=uid)
    code = Column(String(24), unique=True, nullable=False, index=True)
    title = Column(String(200), nullable=False)
    description = Column(Text, default="")
    assigned_by = Column(String(36), ForeignKey("users.id", ondelete="SET NULL"), index=True)
    assignee_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    team_id = Column(String(36), ForeignKey("teams.id", ondelete="SET NULL"))
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="SET NULL"))
    priority = Column(String(16), default="medium")   # low | medium | high | critical
    status = Column(String(24), default="not_started", index=True)
    progress = Column(Integer, default=0)
    start_date = Column(Date)
    due_date = Column(Date, index=True)
    completed_at = Column(DateTime(timezone=True))


class TaskAssignment(Base):
    __tablename__ = "task_assignments"
    id = Column(String(36), primary_key=True, default=uid)
    task_id = Column(String(36), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    assigned_by = Column(String(36), ForeignKey("users.id", ondelete="SET NULL"))
    is_primary = Column(Boolean, default=True)
    assigned_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)


class TaskSubmission(Base):
    __tablename__ = "task_submissions"
    id = Column(String(36), primary_key=True, default=uid)
    task_id = Column(String(36), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="SET NULL"))
    version_no = Column(Integer, default=1)
    snapshot = Column(Text, default="[]")     # immutable submitted code snapshot
    notes = Column(Text, default="")
    status = Column(String(24), default="submitted", index=True)
    reviewer_id = Column(String(36), ForeignKey("users.id", ondelete="SET NULL"))
    review_notes = Column(Text, default="")
    reviewed_at = Column(DateTime(timezone=True))
    forwarded_to_admin_at = Column(DateTime(timezone=True))
    final_decision = Column(String(24))
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)


class SubmissionTestResults(Base):
    """Immutable test output captured with one submission snapshot."""
    __tablename__ = "submission_test_results"
    submission_id = Column(String(36), ForeignKey("task_submissions.id", ondelete="CASCADE"),
                           primary_key=True)
    results_json = Column(Text, nullable=False, default="[]")
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)


class CodeReview(Base):
    __tablename__ = "code_reviews"
    id = Column(String(36), primary_key=True, default=uid)
    submission_id = Column(String(36), ForeignKey("task_submissions.id", ondelete="CASCADE"),
                           nullable=False, index=True)
    reviewer_id = Column(String(36), ForeignKey("users.id", ondelete="SET NULL"))
    decision = Column(String(24), nullable=False)  # approved | rejected | rework | forwarded
    comments = Column(Text, default="")
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)


# ----------------------------------------------------------------------- timer
class TimerSession(Base):
    __tablename__ = "timer_sessions"
    id = Column(String(36), primary_key=True, default=uid)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    status = Column(String(16), default="running", index=True)  # running | paused | stopped
    started_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    ended_at = Column(DateTime(timezone=True))
    last_heartbeat_at = Column(DateTime(timezone=True), default=utcnow)
    accumulated_seconds = Column(Integer, default=0)
    break_seconds = Column(Integer, default=0)
    break_count = Column(Integer, default=0)
    paused_at = Column(DateTime(timezone=True))
    note = Column(String(255), default="")


class TimerEvent(Base):
    __tablename__ = "timer_events"
    id = Column(String(36), primary_key=True, default=uid)
    session_id = Column(String(36), ForeignKey("timer_sessions.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    event = Column(String(40), nullable=False)  # start|pause|resume|stop|heartbeat|inactivity_pause|hidden|disconnect
    at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    meta_json = Column(Text, default="{}")


class Attendance(Base):
    __tablename__ = "attendance"
    __table_args__ = (UniqueConstraint("user_id", "work_date", name="uq_attendance_day"),)
    id = Column(String(36), primary_key=True, default=uid)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    work_date = Column(Date, nullable=False, index=True)
    first_start_at = Column(DateTime(timezone=True))
    last_stop_at = Column(DateTime(timezone=True))
    active_seconds = Column(Integer, default=0)
    break_seconds = Column(Integer, default=0)
    status = Column(String(16), default="present")  # present | absent | half_day | leave
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)


# ------------------------------------------------------------------- recordings
class ScreenRecording(Base):
    __tablename__ = "screen_recordings"
    id = Column(String(36), primary_key=True, default=uid)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    session_id = Column(String(36), ForeignKey("timer_sessions.id", ondelete="SET NULL"))
    task_id = Column(String(36), ForeignKey("tasks.id", ondelete="SET NULL"))
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="SET NULL"))
    status = Column(String(16), default="recording", index=True)
    started_at = Column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    ended_at = Column(DateTime(timezone=True))
    duration_seconds = Column(Integer, default=0)
    size_bytes = Column(Integer, default=0)
    chunk_count = Column(Integer, default=0)
    mime_type = Column(String(80), default="video/webm")
    storage_key = Column(String(255), default="")
    retention_until = Column(DateTime(timezone=True))
    consent_given = Column(Boolean, default=False)
    deleted_at = Column(DateTime(timezone=True))


class RecordingChunk(Base):
    __tablename__ = "recording_chunks"
    __table_args__ = (UniqueConstraint("recording_id", "seq", name="uq_recording_chunk"),)
    id = Column(String(36), primary_key=True, default=uid)
    recording_id = Column(String(36), ForeignKey("screen_recordings.id", ondelete="CASCADE"),
                          nullable=False, index=True)
    seq = Column(Integer, nullable=False)
    size_bytes = Column(Integer, default=0)
    sha256 = Column(String(64), default="")
    storage_key = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)


# ------------------------------------------------------------------------- chat
class ChatRoom(Base):
    __tablename__ = "chat_rooms"
    id = Column(String(36), primary_key=True, default=uid)
    name = Column(String(160), default="")
    type = Column(String(16), default="direct")  # direct | group | team
    team_id = Column(String(36), ForeignKey("teams.id", ondelete="CASCADE"))
    created_by = Column(String(36), ForeignKey("users.id", ondelete="SET NULL"))
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)


class ChatParticipant(Base):
    __tablename__ = "chat_participants"
    __table_args__ = (UniqueConstraint("room_id", "user_id", name="uq_room_participant"),)
    id = Column(String(36), primary_key=True, default=uid)
    room_id = Column(String(36), ForeignKey("chat_rooms.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    last_read_at = Column(DateTime(timezone=True), default=utcnow)


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    id = Column(String(36), primary_key=True, default=uid)
    room_id = Column(String(36), ForeignKey("chat_rooms.id", ondelete="CASCADE"), nullable=False, index=True)
    sender_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    body = Column(Text, default="")
    attachment_key = Column(String(255))
    attachment_name = Column(String(255))
    reply_to_id = Column(String(36), ForeignKey("chat_messages.id", ondelete="SET NULL"))
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    edited_at = Column(DateTime(timezone=True))
    deleted_at = Column(DateTime(timezone=True))


# ---------------------------------------------------------------- notifications
class Notification(Base):
    __tablename__ = "notifications"
    id = Column(String(36), primary_key=True, default=uid)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    type = Column(String(48), default="general", index=True)
    title = Column(String(200), nullable=False)
    body = Column(Text, default="")
    link = Column(String(255), default="")
    is_read = Column(Boolean, default=False, index=True)
    read_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)


# --------------------------------------------------------------------- meetings
class Meeting(Base, TimestampMixin):
    __tablename__ = "meetings"
    id = Column(String(36), primary_key=True, default=uid)
    title = Column(String(200), nullable=False)
    agenda = Column(Text, default="")
    created_by = Column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=False, index=True)
    starts_at = Column(DateTime(timezone=True), nullable=False, index=True)
    ends_at = Column(DateTime(timezone=True), nullable=False)
    meet_url = Column(String(500))
    html_link = Column(String(500))
    calendar_event_id = Column(String(255))
    provider = Column(String(16), default="google")  # google | manual
    status = Column(String(16), default="scheduled")  # scheduled | cancelled | completed
    error_message = Column(String(400))


class MeetingParticipant(Base):
    __tablename__ = "meeting_participants"
    __table_args__ = (UniqueConstraint("meeting_id", "user_id", name="uq_meeting_participant"),)
    id = Column(String(36), primary_key=True, default=uid)
    meeting_id = Column(String(36), ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    response = Column(String(16), default="invited")  # invited | accepted | declined
    joined_at = Column(DateTime(timezone=True))


class GoogleAccount(Base):
    __tablename__ = "google_accounts"
    id = Column(String(36), primary_key=True, default=uid)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True)
    google_email = Column(String(255), default="")
    access_token = Column(Text)
    refresh_token = Column(Text)
    token_expires_at = Column(DateTime(timezone=True))
    scopes = Column(Text, default="")
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)


# ------------------------------------------------------------------------ audit
class AuditLog(Base):
    __tablename__ = "audit_logs"
    id = Column(String(36), primary_key=True, default=uid)
    actor_id = Column(String(36), ForeignKey("users.id", ondelete="SET NULL"), index=True)
    actor_role = Column(String(32), default="")
    action = Column(String(80), nullable=False, index=True)
    entity_type = Column(String(48), default="", index=True)
    entity_id = Column(String(36), default="")
    ip = Column(String(64), default="")
    user_agent = Column(String(400), default="")
    meta_json = Column(Text, default="{}")
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)


Index("ix_tasks_assignee_status", Task.assignee_id, Task.status)
Index("ix_messages_room_created", ChatMessage.room_id, ChatMessage.created_at)
Index("ix_audit_actor_created", AuditLog.actor_id, AuditLog.created_at)


class AwayInterval(Base):
    """One explicit 'left the site' -> 'came back' interval of a work session."""
    __tablename__ = "away_intervals"
    id = Column(String(36), primary_key=True, default=uid)
    session_id = Column(String(36), ForeignKey("timer_sessions.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    left_at = Column(DateTime(timezone=True), nullable=False)
    returned_at = Column(DateTime(timezone=True))
    seconds = Column(Integer, default=0)
    reason = Column(String(24), default="hidden")  # hidden | blur | disconnect | timeout
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)


# ---------------------------------------------------------------- chat v2
class ChatAttachment(Base):
    __tablename__ = "chat_attachments"
    id = Column(String(36), primary_key=True, default=uid)
    message_id = Column(String(36), ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=False, index=True)
    room_id = Column(String(36), ForeignKey("chat_rooms.id", ondelete="CASCADE"), nullable=False, index=True)
    uploader_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    file_name = Column(String(255), nullable=False)
    mime = Column(String(120), default="application/octet-stream")
    size_bytes = Column(Integer, default=0)
    storage_key = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)


class ChatReadReceipt(Base):
    __tablename__ = "chat_read_receipts"
    __table_args__ = (UniqueConstraint("message_id", "user_id", name="uq_msg_read"),)
    id = Column(String(36), primary_key=True, default=uid)
    message_id = Column(String(36), ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    read_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)


class ChatGroupMeta(Base):
    __tablename__ = "chat_group_meta"
    room_id = Column(String(36), ForeignKey("chat_rooms.id", ondelete="CASCADE"), primary_key=True)
    description = Column(String(400), default="")
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ChatMonitorGrant(Base):
    """Explicit permission for an On-Roll employee to monitor all conversations."""
    __tablename__ = "chat_monitor_grants"
    id = Column(String(36), primary_key=True, default=uid)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True)
    granted_by = Column(String(36), ForeignKey("users.id", ondelete="SET NULL"))
    scope = Column(String(40), default="all")  # all | team
    active = Column(Boolean, default=True)
    note = Column(String(255), default="")
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    revoked_at = Column(DateTime(timezone=True))


class ChatAccessAudit(Base):
    __tablename__ = "chat_access_audit"
    id = Column(String(36), primary_key=True, default=uid)
    actor_id = Column(String(36), ForeignKey("users.id", ondelete="SET NULL"), index=True)
    actor_role = Column(String(30), default="")
    action = Column(String(40), nullable=False, index=True)
    room_id = Column(String(36), index=True)
    target_user_id = Column(String(36), index=True)
    query = Column(String(255), default="")
    message_count = Column(Integer, default=0)
    ip = Column(String(60), default="")
    meta_json = Column(Text, default="{}")
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)
