# Database design

WORKCODE uses the existing PostgreSQL database configured by DATABASE_URL. Do not omit the URL or switch the application to SQLite. SQLAlchemy models are defined in backend/app/models/__init__.py.

## Conventions

- **IDs**: UUID v4 stored as `VARCHAR(36)` — portable across both engines.
- **Timestamps**: `TIMESTAMPTZ`, UTC, set in Python (`default=utcnow`).
- **Money / counts**: integers; durations always in **seconds**.
- **Soft delete**: `deleted_at` on projects and recordings; hard delete only for rows with
  no retention obligation (e.g. a single notification).
- **Foreign keys** use `ON DELETE CASCADE` for owned data (files, chunks, participants) and
  `ON DELETE SET NULL` for references that must survive (e.g. `tasks.assigned_by`).

## Tables

### Identity & employment
| Table | Key columns | Notes |
|---|---|---|
| `users` | `id, email UQ, username UQ, password_hash, role, is_active` | role ∈ admin / developer_onroll / developer_offroll / intern |
| `roles` | `name UQ` | reference list of the four roles |
| `admin_accounts` | `user_id UQ, admin_slot UQ (1-3 default, 4+ for extra admins)` | three admins by default, each with their own login; extra slots via `.env` |
| `employee_profiles` | `user_id UQ, employee_code UQ, team_id, joining_date, expected_end_date, employment_status` | six-month window for off-roll/intern |
| `employment_history` | `user_id, event, from_role, to_role, effective_date, approved_by` | append-only conversion record |
| `teams` | `name UQ, lead_user_id` | |
| `team_members` | `UNIQUE(team_id, user_id)` | drives every team-scoped permission |
| `user_sessions` | `refresh_jti UQ, expires_at, revoked_at` | refresh-token rotation |
| `password_reset_tokens` | `token_hash UQ, expires_at, used_at` | only the SHA-256 hash is stored |

### Work
| Table | Notes |
|---|---|
| `projects` | `UNIQUE(owner_id, name)`, `storage_key`, `current_version`, soft delete |
| `project_files` | `UNIQUE(project_id, path)`, content + size |
| `project_versions` | `UNIQUE(project_id, version_no)`, JSON snapshot of every file |
| `tasks` | `code UQ` (WC-0001…), status, progress, priority, dates |
| `task_assignments` | who assigned what, when (audit of assignment itself) |
| `task_submissions` | immutable `snapshot` JSON, version, review fields |
| `code_reviews` | append-only review decisions (`approved/rework/rejected/forwarded/admin_*`) |
| `timer_sessions` | server-owned accrual: `accumulated_seconds`, `break_seconds` |
| `timer_events` | every start/pause/resume/stop/heartbeat/inactivity/hidden event |
| `attendance` | `UNIQUE(user_id, work_date)` daily roll-up |
| `screen_recordings` | metadata, consent flag, `retention_until` |
| `recording_chunks` | `UNIQUE(recording_id, seq)`, sha256 per chunk |

### Communication
| Table | Notes |
|---|---|
| `chat_rooms` | `direct \| group \| team \| task` |
| `chat_participants` | `UNIQUE(room_id, user_id)`, `last_read_at` powers unread counts |
| `chat_messages` | `reply_to_id` self-FK, attachment key/name |
| `notifications` | per-user history with `is_read` |
| `meetings` | Google event id, real `meet_url`, `status`, `error_message` |
| `meeting_participants` | `UNIQUE(meeting_id, user_id)`, RSVP + join time |
| `google_accounts` | OAuth tokens, one per organiser (never sent to the client) |

### Governance
| Table | Notes |
|---|---|
| `audit_logs` | actor, role, action, entity, IP, UA, JSON meta — append-only |

## Indexes

Created explicitly for the hot paths:

- `users(role)`, `users(is_active)`, `users(email)`
- `tasks(assignee_id, status)`, `tasks(due_date)`, `tasks(code)`
- `chat_messages(room_id, created_at)`
- `audit_logs(actor_id, created_at)`, `audit_logs(action)`
- `screen_recordings(user_id)`, `screen_recordings(started_at)`
- `attendance(user_id, work_date)` (unique)

## Retention policy

| Data | Policy |
|---|---|
| Submitted code snapshots | **never deleted automatically** |
| Project versions | kept (last 100 exposed in the UI) |
| Projects | soft delete; admin can restore/purge |
| Recordings | soft delete + purge after `RECORDING_RETENTION_DAYS` |
| Audit logs | append-only, keep ≥ 1 year |
| Sessions | rows expire after 7 days, revocation is immediate |

A reference purge job lives in `backend/scripts/purge_retention.py` — run it from cron in
production.

## Migration status and safe changes

The checked PostgreSQL database has no alembic_version table. Alembic environment/template files exist, but there are no revision scripts. Do not run create_all, generate an initial migration against the populated schema, or apply schema changes until a reviewed baseline and backup strategy exist. No schema change was made during verification.

The test suite uses a unique workcode_test_<uuid> schema inside the configured PostgreSQL database. It excludes public from search_path, then drops only that test-created schema at teardown.
