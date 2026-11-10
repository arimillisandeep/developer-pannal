# Security model

## Identity

- **Password hashing**: PBKDF2-HMAC-SHA256, 600,000 iterations, 16-byte random salt,
  constant-time comparison (`hmac.compare_digest`). Format: `pbkdf2_sha256$iters$salt$hash`.
- **Admin bootstrap**: `scripts/init_admins.py` generates three separate accounts with
  cryptographically random passwords (or values you supply via env/flags). No password is
  ever written in the repository.
- **Access token**: JWT HS256, 30 min, carries `sub`, `role`, `jti`.
- **Refresh token**: 7 days, single-use. `user_sessions.refresh_jti` is the allow-list;
  refreshing revokes the old row and issues a new pair (replay of a used token → 401).
- **Password reset**: 32-byte `secrets.token_urlsafe` token, only its SHA-256 is stored,
  30-minute expiry, single use, and every session is revoked on success.
- **No user enumeration**: `/auth/forgot-password` always returns 200.

## Authorization

- Role checks happen in **dependencies on the server** (`require_admin`, `require_roles`),
  never in the frontend. The React guards only improve UX.
- Ownership checks are functions, not conventions:
  `get_project_for()`, `can_view(task)`, `_assignee_allowed()`, `_require_member(room)`.
- Team scoping is derived from `team_members` rows on every request — a developer cannot see
  or assign work outside their team even by guessing IDs.
- Employees cannot promote themselves: task `status` transitions to `approved/completed/
  under_review` are rejected for assignees, and only admins may convert employment status.
- Non-existent and forbidden resources both return **404** where leaking existence matters
  (projects, tasks, submissions).

## Data protection

- **Path traversal**: every storage access goes through `safe_join()`, which rejects `..`,
  absolute paths and anything resolving outside the root. Project file paths are validated
  again in the router (tested).
- **SQL injection**: SQLAlchemy Core/ORM with bound parameters only; no string-built SQL.
- **XSS**: React escapes by default; the only `srcDoc` iframe is sandboxed by the browser and
  contains the user's own files. `dangerouslySetInnerHTML` is not used anywhere.
- **CSRF**: tokens travel in the `Authorization` header (not cookies), so classic CSRF does
  not apply. If you switch to cookie auth, add SameSite=Lax + a double-submit token.
- **CORS**: explicit allow-list from `CORS_ORIGINS`, credentials on.
- **Rate limiting**: sliding window per IP + path prefix (240/min general, 20/min on login).
  Swap the in-process store for Redis when running multiple workers.
- **Uploads**: size caps (50 MB chunk, 20 MB chat attachment), sanitised filenames, stored
  outside the web root, served only through authorized endpoints.
- **Secrets**: all in `.env` (git-ignored). Google refresh tokens live in the database,
  server-side only, never serialised to a client.

## Code execution isolation

| Control | User code and shell execution |
|---|---|
| Runtime | Docker is required; local host subprocess fallback is disabled |
| Filesystem | One temporary code workspace or one project source directory is mounted writable; container root is read-only |
| Memory / CPU / processes | Docker memory, CPU, and PID limits |
| Network | `--network none` |
| Identity | Non-root UID, all Linux capabilities dropped, `no-new-privileges` |
| Temporary paths | Bounded tmpfs for `/tmp` and terminal home |
| Timeout | Docker client timeout; named run containers are removed after timeout |

Interactive terminals use one isolated container per tab and mount only that project's
source directory. The home directory is temporary and no Docker socket is mounted in the
container. Terminal commands cannot reach package registries because container networking is
disabled. Local mode returns an explicit unavailable result for code execution and a 503 for
interactive terminals and shell commands; it never falls back to a host shell.

When the API runs inside Docker Compose with the Docker socket enabled, set
`DOCKER_STORAGE_VOLUME` to the **existing** Docker storage volume name so a runner can mount
only the project's `source_files` subdirectory using `volume-subpath`. The volume and target
subdirectory must already exist. Keep the current volume name to preserve stored projects and
recordings. Without that setting, Docker uses the storage path as a bind mount; this only works
when the path is visible to the Docker daemon. Docker socket access grants the backend broad
control over the Docker host, so enable it only on a host where that trust is acceptable.

**Never enable a local host-shell fallback for user code.**

## Monitoring & privacy

- Screen recording requires an explicit consent flag *and* the browser's own screen-picker
  prompt; a recording indicator is always visible in the OS/browser bar; the user can stop at
  any time; nothing is captured secretly.
- Admin access to a recording writes an `audit_logs` row (`recording.viewed`).
- Chat access by an admin who is not a participant is audited (`chat.history_reviewed`) and
  is limited to team/group rooms, never private 1:1 conversations or task threads.
- **Audit visibility**: `AUDIT_VISIBILITY=per_admin` means an admin sees only their own
  actions by default; `scope=all` is an explicit, audited cross-admin access decision.

## Audit trail

Every privileged action writes `audit_logs` with actor id, actor role, action, entity,
IP, user-agent, timestamp and a JSON payload. Rows are never updated or deleted by the app.
Examples: `auth.login`, `user.created`, `employee.converted`, `task.created`,
`project.saved`, `submission.created`, `submission.approved`, `timer.inactivity_pause`,
`recording.viewed`, `meeting.created`, `chat.history_reviewed`.

## Hardening checklist before going live

- [ ] `SECRET_KEY` = 64 random bytes (`openssl rand -hex 32`)
- [ ] `ENV=production` (hides `dev_token` from password reset)
- [ ] `RUNNER_MODE=docker` and only the images you need are pulled
- [ ] HTTPS at the proxy, HSTS enabled, HTTP → HTTPS redirect
- [ ] PostgreSQL not exposed to the internet; strong password; `pg_dump` backups scheduled
- [ ] `CORS_ORIGINS` limited to your real domain
- [ ] SMTP wired for password-reset emails
- [ ] Retention purge job scheduled
- [ ] Log shipping / error monitoring configured
- [ ] Rotate the three admin passwords after first login
