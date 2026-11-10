"""Request / response schemas."""
from datetime import date, datetime
from typing import Any

import re
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _check_email(v: str) -> str:
    if not _EMAIL_RE.match(v or ""):
        raise ValueError("Invalid email address")
    return v.lower()


Email = Annotated[str, AfterValidator(_check_email)]


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ------------------------------------------------------------------------ auth
class LoginIn(BaseModel):
    identifier: str = Field(description="email or username")
    password: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: "UserOut"


class RefreshIn(BaseModel):
    refresh_token: str


class ForgotPasswordIn(BaseModel):
    email: Email


class ResetPasswordIn(BaseModel):
    token: str
    new_password: str


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str


# ----------------------------------------------------------------------- users
class UserOut(ORM):
    id: str
    email: str
    username: str
    full_name: str
    role: str
    is_active: bool
    phone: str | None = ""
    designation: str | None = ""
    avatar_color: str | None = "#4f8cff"
    last_login_at: datetime | None = None
    created_at: datetime | None = None


class ProfileOut(ORM):
    id: str
    user_id: str
    employee_code: str
    team_id: str | None = None
    manager_id: str | None = None
    joining_date: date
    expected_end_date: date | None = None
    employment_status: str | None = "active"
    skills: str | None = ""
    address: str | None = ""
    emergency_contact: str | None = ""


class UserDetail(UserOut):
    profile: ProfileOut | None = None


class UserCreate(BaseModel):
    email: Email
    username: str
    full_name: str
    password: str
    role: str
    phone: str = ""
    designation: str = ""
    team_id: str | None = None
    joining_date: date | None = None
    period_months: int | None = None  # off-roll / intern initial period
    skills: str = ""


class UserUpdate(BaseModel):
    full_name: str | None = None
    email: Email | None = None
    phone: str | None = None
    designation: str | None = None
    team_id: str | None = None
    is_active: bool | None = None
    skills: str | None = None
    expected_end_date: date | None = None


class ConvertIn(BaseModel):
    to_role: str = "developer_onroll"
    effective_date: date | None = None
    notes: str = ""


class TeamIn(BaseModel):
    name: str
    description: str = ""
    lead_user_id: str | None = None


class TeamOut(ORM):
    id: str
    name: str
    description: str | None = ""
    lead_user_id: str | None = None
    created_at: datetime | None = None


class TeamMemberIn(BaseModel):
    user_id: str
    role_in_team: str = "member"


# -------------------------------------------------------------------- projects
class ProjectIn(BaseModel):
    name: str
    description: str = ""
    language: str = "python"
    team_id: str | None = None


class ProjectOut(ORM):
    id: str
    owner_id: str
    team_id: str | None = None
    name: str
    description: str | None = ""
    language: str
    current_version: int
    is_archived: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None


class FileIn(BaseModel):
    path: str
    content: str = ""
    is_dir: bool = False


class FileOut(ORM):
    id: str
    path: str
    content: str | None = ""
    is_dir: bool
    size: int
    updated_at: datetime | None = None


class SaveProjectIn(BaseModel):
    files: list[FileIn]
    label: str = ""


class RunIn(BaseModel):
    language: str = "python"
    project_id: str | None = None
    filename: str | None = None
    code: str | None = None
    stdin: str = ""


class RunOut(BaseModel):
    ok: bool
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0
    runner: str = "local"
    timed_out: bool = False
    truncated: bool = False


# ----------------------------------------------------------------------- tasks
class TaskIn(BaseModel):
    title: str
    description: str = ""
    assignee_id: str
    project_id: str | None = None
    team_id: str | None = None
    priority: str = "medium"
    start_date: date | None = None
    due_date: date | None = None


class TaskUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    priority: str | None = None
    status: str | None = None
    progress: int | None = None
    due_date: date | None = None
    assignee_id: str | None = None
    team_id: str | None = None
    project_id: str | None = None


class TaskOut(ORM):
    id: str
    code: str
    title: str
    description: str | None = ""
    assigned_by: str | None = None
    assignee_id: str
    team_id: str | None = None
    project_id: str | None = None
    priority: str
    status: str
    progress: int
    start_date: date | None = None
    due_date: date | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class CommentIn(BaseModel):
    body: str


class SubmissionTestCase(BaseModel):
    index: int = Field(ge=1)
    expected: str = ""
    actual: str = ""
    error: str = ""
    passed: bool


class SubmissionIn(BaseModel):
    task_id: str
    project_id: str | None = None
    notes: str = ""
    test_results: list[SubmissionTestCase] = Field(default_factory=list, max_length=50)


class ReviewIn(BaseModel):
    decision: str  # approved | rejected | rework
    comments: str = ""


class SubmissionOut(ORM):
    id: str
    task_id: str
    user_id: str
    project_id: str | None = None
    version_no: int
    notes: str | None = ""
    status: str
    reviewer_id: str | None = None
    review_notes: str | None = ""
    created_at: datetime | None = None
    reviewed_at: datetime | None = None


# ----------------------------------------------------------------------- timer
class TimerEventIn(BaseModel):
    event: str  # heartbeat | hidden | visible | disconnect
    meta: dict[str, Any] = {}


class TimerOut(ORM):
    id: str
    user_id: str
    status: str
    started_at: datetime
    ended_at: datetime | None = None
    accumulated_seconds: int
    break_seconds: int
    break_count: int
    last_heartbeat_at: datetime | None = None


# ------------------------------------------------------------------ recordings
class RecordingStartIn(BaseModel):
    session_id: str | None = None
    task_id: str | None = None
    project_id: str | None = None
    consent: bool = False
    mime_type: str = "video/webm"


class RecordingOut(ORM):
    id: str
    user_id: str
    status: str
    started_at: datetime
    ended_at: datetime | None = None
    duration_seconds: int
    size_bytes: int
    chunk_count: int
    mime_type: str
    retention_until: datetime | None = None


# ------------------------------------------------------------------------ chat
class RoomIn(BaseModel):
    type: str = "direct"  # direct | group | team
    name: str = ""
    participant_ids: list[str] = []
    team_id: str | None = None


class RoomOut(ORM):
    id: str
    name: str | None = ""
    type: str
    team_id: str | None = None
    created_at: datetime | None = None


class MessageIn(BaseModel):
    body: str = ""
    reply_to_id: str | None = None


class MessageOut(ORM):
    id: str
    room_id: str
    sender_id: str
    body: str | None = ""
    attachment_key: str | None = None
    attachment_name: str | None = None
    reply_to_id: str | None = None
    created_at: datetime


# --------------------------------------------------------------- notifications
class NotificationOut(ORM):
    id: str
    type: str
    title: str
    body: str | None = ""
    link: str | None = ""
    is_read: bool
    created_at: datetime


# -------------------------------------------------------------------- meetings
class MeetingIn(BaseModel):
    title: str
    agenda: str = ""
    starts_at: datetime
    ends_at: datetime
    participant_ids: list[str] = []
    create_meet_link: bool = True


class MeetingOut(ORM):
    id: str
    title: str
    agenda: str | None = ""
    created_by: str
    starts_at: datetime
    ends_at: datetime
    meet_url: str | None = None
    html_link: str | None = None
    provider: str
    status: str
    error_message: str | None = None


class GoogleCodeIn(BaseModel):
    code: str
    redirect_uri: str | None = None


TokenPair.model_rebuild()
