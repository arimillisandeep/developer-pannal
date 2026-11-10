# WORKCODE API reference

Base URL: `http://localhost:8000/api` · Interactive docs: `/api/docs` · OpenAPI: `/api/openapi.json`

## Authentication

All protected endpoints expect `Authorization: Bearer <access_token>`.

| Method | Path | Who | Description |
|---|---|---|---|
| POST | `/auth/login` | public | `{identifier, password}` → access + refresh + `redirect_to` |
| POST | `/auth/refresh` | public | Rotate refresh token → new pair (old one is revoked) |
| POST | `/auth/logout` | any | Revokes the given refresh token |
| GET | `/auth/me` | any | Current user |
| POST | `/auth/forgot-password` | public | Always 200 (no user enumeration); dev returns `dev_token` |
| POST | `/auth/reset-password` | public | `{token, new_password}`; revokes all sessions |
| POST | `/auth/change-password` | any | `{current_password, new_password}` |
| GET | `/auth/sessions` | any | Active refresh sessions |
| GET | `/auth/online` | admin | Users with a live WebSocket |

Tokens: access 30 min, refresh 7 days (both configurable). Refresh tokens are single-use.

## Users & employees

| Method | Path | Who | Description |
|---|---|---|---|
| GET | `/users` | admin | Paginated, filter `role`, `q`, `is_active` |
| POST | `/users` | admin | Create developer/intern (admins only via script) |
| GET/PATCH | `/users/{id}` | admin | Read / update |
| POST | `/users/{id}/status?is_active=` | admin | Activate / deactivate |
| POST | `/users/{id}/reset-password?new_password=` | admin | Force reset |
| POST | `/users/{id}/convert` | admin | Off-Roll/Intern → On-Roll (explicit only) |
| GET | `/users/{id}/history` | admin | Employment timeline |
| GET/PATCH | `/users/me/profile` | any | Own profile |
| GET/POST | `/teams` | any / admin | List / create teams |
| GET/POST | `/teams/{id}/members` | any / admin | Membership |
| DELETE | `/teams/{id}/members/{user_id}` | admin | Remove member |

## Admin

| Method | Path | Description |
|---|---|---|
| GET | `/admin/dashboard` | All KPI counters, recent submissions and audit |
| GET | `/admin/users/{id}/activity?days=` | Tasks, attendance, recordings, sessions, audit for one person |
| GET | `/admin/progress` | Per-employee completion rate, hours, avg progress |
| GET | `/admin/audit-logs?scope=auto\|mine\|all` | Audit trail honouring `AUDIT_VISIBILITY` |
| GET | `/admin/admins` | The three admin slots + action counts |
| GET | `/admin/settings` | Effective platform configuration |

## Tasks

| Method | Path | Who | Notes |
|---|---|---|---|
| GET | `/tasks` | any | Scoped automatically: admin=all, dev=team, worker=own |
| POST | `/tasks` | admin, dev | Devs may only assign inside their team |
| GET/PATCH | `/tasks/{id}` | any with access | Workers may change only `status`/`progress` |
| DELETE | `/tasks/{id}` | admin | Blocked (409) if submissions exist |
| GET/POST | `/tasks/{id}/comments` | participants | Task thread (backed by a task chat room) |

Statuses: `not_started, in_progress, submitted, under_review, rework_required, approved,
completed, overdue`.

## Projects & workspace

| Method | Path | Notes |
|---|---|---|
| GET/POST | `/projects` | Owner-scoped; team projects visible to teammates |
| GET/PATCH/DELETE | `/projects/{id}` | Delete is a soft delete (retention) |
| GET/PUT/DELETE | `/projects/{id}/files` | Read tree, write file, delete file (traversal blocked) |
| POST | `/projects/{id}/save` | Replace file set + create immutable version |
| GET | `/projects/{id}/versions` | Version list |
| POST | `/projects/{id}/versions/{n}/restore` | Restore + snapshot the restore |
| GET | `/projects/{id}/download` | ZIP stream |
| GET | `/workspace/languages` | Runtime matrix + local availability |
| POST | `/workspace/run` | `{language, code, filename, project_id, stdin}` |
| POST | `/workspace/terminal` | One shell command in a throw-away project copy |
| GET | `/workspace/preview/{project_id}` | Bundled HTML for Live Preview |

`/workspace/run` response: `{ok, exit_code, stdout, stderr, duration_ms, runner, timed_out}`.

## Submissions & reviews

| Method | Path | Who | Notes |
|---|---|---|---|
| POST | `/submissions` | worker | Snapshots all project files; notifies reviewer + admins |
| GET | `/submissions` | scoped | Filter `task_id`, `status` |
| GET | `/submissions/{id}` | scoped | Includes full file snapshot + review history |
| POST | `/submissions/{id}/review` | assigner/admin | `approved \| rejected \| rework` |
| POST | `/submissions/{id}/forward` | assigner | Escalate to admin |
| POST | `/submissions/{id}/final` | admin | Final decision → `completed` |
| GET | `/submissions/{id}/download` | scoped | ZIP of the snapshot |

Snapshots are append-only: a `rework` never deletes the earlier version.

## Timer & attendance

| Method | Path | Notes |
|---|---|---|
| GET | `/timer/current` | Active session + live seconds |
| POST | `/timer/start` | Refuses admins (403) |
| POST | `/timer/pause` / `/timer/resume` / `/timer/stop` | Server accrues the time |
| POST | `/timer/heartbeat` | `{event: heartbeat\|hidden\|visible\|disconnect}`; auto-pauses after the grace period |
| GET | `/timer/sessions` | History |
| GET | `/timer/events/{session_id}` | Full event stream |
| GET | `/timer/attendance?days=` | Daily rows + totals |

## Recordings

| Method | Path | Notes |
|---|---|---|
| POST | `/recordings/start` | Requires `consent: true` |
| POST | `/recordings/{id}/chunks` | multipart `seq` + `chunk` (10s in the UI) |
| POST | `/recordings/{id}/complete?duration_seconds=` | Finalise |
| GET | `/recordings` | Admin filters: `user_id, role, task_id, project_id, date_from, date_to, min_duration` |
| GET | `/recordings/{id}` | Metadata + chunk list (admin views are audited) |
| GET | `/recordings/{id}/video` | Streamed concatenation of chunks |
| DELETE | `/recordings/{id}` | Soft delete, purged by retention job |

## Chat, notifications, meetings, reports

| Method | Path | Notes |
|---|---|---|
| GET/POST | `/chat/rooms` | direct / group / team (team auto-adds members) |
| GET/POST | `/chat/rooms/{id}/messages` | History / send (also pushed over WS) |
| POST | `/chat/rooms/{id}/read` | Read receipt |
| POST | `/chat/rooms/{id}/attachment` | ≤20 MB |
| GET | `/chat/contacts` | People you may message |
| GET | `/notifications` | History (`unread_only`, `limit`) |
| GET | `/notifications/unread-count` | Badge count |
| POST | `/notifications/{id}/read`, `/read-all` | Read state |
| POST | `/meetings` | Creates Calendar event + Meet link when Google is configured |
| GET | `/meetings`, `/meetings/{id}` | List / detail with participants |
| PATCH | `/meetings/{id}`, `/meetings/{id}/cancel` | Manage own meetings |
| POST | `/meetings/{id}/join` | Returns the real `meet_url` or a clear 409 |
| GET | `/meetings/google/status`, `/google/auth-url` | OAuth state |
| POST | `/meetings/google/callback` | Exchange code, store refresh token server-side |
| GET | `/reports/summary?date_from=&date_to=` | Headcount, attendance, tasks, submissions, per-person |
| GET | `/reports/export/{attendance\|tasks\|employees\|timesheets}` | CSV download |

## WebSocket

`ws://host/ws?token=<access_token>`

Server → client frames: `{"channel": "notification"|"message"|"read"|"typing"|"ready", "data": {...}}`
Client → server: `{"action":"subscribe","room_id":"…"}`, `{"action":"typing","room_id":"…"}`,
`{"action":"ping"}`.

Close codes: `4401` invalid token, `4403` inactive user.

## Errors

| Code | Meaning |
|---|---|
| 401 | Missing / expired / invalid token |
| 403 | Authenticated but not authorized (role, ownership or team) |
| 404 | Not found **or** not visible to you |
| 409 | Conflict (duplicate email, task with submissions, cancelled meeting) |
| 422 | Validation error, includes `errors[{field, message}]` |
| 429 | Rate limited |
