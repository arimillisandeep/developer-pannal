# WORKCODE IDE — architecture and verification

## Isolated command execution

User code and shell commands run only through Docker. `RUNNER_MODE` defaults to `docker`; local mode refuses execution rather than starting a host process. The one-shot language runner, build, legacy one-command terminal, and interactive terminal use restricted Docker containers with network disabled, bounded resources, a non-root UID, read-only container root where supported, dropped capabilities, and `no-new-privileges`.

Interactive terminals use one container per tab. The project source mount is scoped to that project; temporary filesystems are used for `/tmp` and terminal home. WebSocket sessions are authenticated. Docker startup requires a running daemon, configured socket/storage volume, and the `workcode-terminal:latest` image. Live development servers remain disabled until isolated Docker port forwarding is implemented.

The backend resolves Docker Desktop from PATH, per-user, or machine-wide locations. Missing runner images are built locally and cached; container starts use `--pull=never`. The CLI directory is added only to Docker child-process PATH so Docker Desktop’s helper is available.

For Compose deployment, follow [DEPLOYMENT.md](DEPLOYMENT.md) and [SECURITY.md](SECURITY.md). Set `DOCKER_STORAGE_VOLUME` to the actual existing storage volume name; do not replace the workspace volume to work around a mount failure.

## Terminal interaction

The terminal UI blocks copy/cut events and the native right-click menu. It offers a Paste control and Clear control; Ctrl+V is supported while focused. Ctrl+C continues to be sent to the PTY as the process interrupt. These controls do not prevent copying via developer tools, extensions, screenshots, or operating-system access. The terminal image build and a restricted non-root container command passed; interactive WebSocket/PTy use was not verified in the browser.

## Workspace storage and review

`backend/app/services/workspace_fs.py` stores project files and indexes them in PostgreSQL. Path traversal is rejected. Save, sync, versions, submissions, static preview, and downloads use the existing workspace. Submission snapshots and review decisions are retained. Backend tests use a unique PostgreSQL test schema and drop only that test-owned schema.

Assigned Intern, Off-Roll, and On-Roll tasks appear in the IDE task selector even when a task
does not yet have a project link. Submit saves pending editor changes, sends any current test
results, and creates an immutable snapshot. New statuses are `pending_permanent_review` and
`pending_admin_review`; On-Roll approval forwards to Admin automatically. Results are stored in
the additive `submission_test_results` table and displayed in the review detail. Repeated submit
requests reuse a pending snapshot instead of creating another notification or version.

## Screen recording

A recorder mounted in the persistent authenticated shell stays alive across WORKCODE route changes. Its controls move into the IDE toolbar when a workspace is open. Workspace entry shows an explicit permission prompt; the user must click Start and choose a screen. Choose Entire Screen to include other tabs. `getDisplayMedia()` requires HTTPS or localhost, so ordinary HTTP LAN origins are blocked by the browser.

MediaRecorder chunks are persisted locally in IndexedDB and uploaded through authenticated recording APIs with retry. Interrupted local sessions can be recovered; local copies remain in the same browser profile until synced. Server playback is authenticated and role-scoped. Actual browser capture, refresh recovery, valid video decoding, playback, and download were not run because browser automation is disabled in this Codex session.

## Timer

Authenticated employee use of `/timer/current` creates or resumes the server-managed timer. Heartbeats continue while the WORKCODE tab is hidden. Long heartbeat gaps can cause the configured inactivity pause. Explicit logout sends a timer stop before clearing authentication. No real cross-tab ChatGPT browser test was possible here.

## Reports

Employees can view their own attendance, timer sessions, tasks, submissions/reviews, recording metadata, and selected audit events. On-Roll users can view reports for employees in their assigned review scope. Admin report filters cover date, employee, role, and task. Personal, scoped team, and Admin exports support CSV, XLSX, and PDF. Away intervals are shown separately from active and paused time; they are not represented as verified idle time. Existing timer sessions have no task link, so time is not allocated to tasks.

## Verification

- Frontend production build: **passed**, Vite 6.4.5; 132 modules transformed.
- Complete backend suite (latest Docker execution verification): **222 passed**, one upstream Starlette deprecation warning.
- Report API tests validate worker privacy, On-Roll scope, and CSV/XLSX/PDF responses. XLSX content is opened with `openpyxl` in tests.
- Docker runner images were built for all 16 configured executable language IDs. Real Docker sample execution passed for Python, JavaScript, TypeScript, C, C++, Java, Go, Ruby, PHP, Rust, shell, PowerShell, Kotlin, Swift, and C#. Container startup, I/O, compiler errors, timeout cleanup, and isolation tests passed. Interactive browser terminal/WebSocket use remains unverified.
- Computer Use listed the open Chrome window, but access was denied when attempting a read-only inspection. Screen sharing, MediaRecorder, IndexedDB refresh recovery, real playback/download, visual layout, and live route/tab continuity were not end-to-end verified.
- Backend tests used and dropped only a unique test-owned PostgreSQL schema. Production data and credentials were left untouched.
