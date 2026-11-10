"""End-to-end API tests: auth, RBAC, tasks, projects, submissions, timer."""
from tests.helpers import auth


def test_health(client):
    assert client.get("/api/health").json()["status"] == "ok"


def test_login_and_me(client, admin_token):
    me = client.get("/api/auth/me", headers=auth(admin_token))
    assert me.status_code == 200
    assert me.json()["role"] == "admin"


def test_login_wrong_password(client):
    r = client.post("/api/auth/login", json={"identifier": "admin1@wc.test", "password": "nope"})
    assert r.status_code == 401


def test_protected_route_without_token(client):
    assert client.get("/api/auth/me").status_code == 401


def test_invalid_token_rejected(client):
    r = client.get("/api/auth/me", headers={"Authorization": "Bearer garbage.token.here"})
    assert r.status_code == 401


def test_refresh_rotation(client):
    pair = client.post("/api/auth/login", json={"identifier": "admin1@wc.test",
                                                "password": "AdminPass123"}).json()
    r = client.post("/api/auth/refresh", json={"refresh_token": pair["refresh_token"]})
    assert r.status_code == 200
    new_token = r.json()["refresh_token"]
    again = client.post("/api/auth/refresh", json={"refresh_token": pair["refresh_token"]})
    assert again.status_code == 401  # old refresh token was revoked
    ok = client.post("/api/auth/refresh", json={"refresh_token": new_token})
    assert ok.status_code == 200


def test_role_isolation(client, intern_token):
    assert client.get("/api/admin/dashboard", headers=auth(intern_token)).status_code == 403
    assert client.get("/api/users", headers=auth(intern_token)).status_code == 403


def test_admin_can_create_user_and_task(client, admin_token, intern_token):
    r = client.post("/api/users", headers=auth(admin_token), json={
        "email": "new.dev@wc.test", "username": "newdev", "full_name": "New Dev",
        "password": "StrongPass123", "role": "developer_offroll", "period_months": 6,
    })
    assert r.status_code == 201, r.text
    new_id = r.json()["id"]

    reviewer_response = client.post("/api/users", headers=auth(admin_token), json={
        "email": "task.reviewer@wc.test", "username": "taskreviewer", "full_name": "Task Reviewer",
        "password": "ReviewerPass123", "role": "developer_onroll"})
    assert reviewer_response.status_code == 201, reviewer_response.text
    reviewer_id = reviewer_response.json()["id"]
    team = client.post("/api/teams", headers=auth(admin_token), json={
        "name": "Off-roll review team", "lead_user_id": reviewer_id}).json()
    task = client.post("/api/tasks", headers=auth(admin_token), json={
        "title": "Ship the login page", "assignee_id": new_id, "team_id": team["id"],
        "priority": "high", "due_date": "2027-01-31",
    })
    assert task.status_code == 201, task.text
    assert task.json()["code"].startswith("WC-")

    # intern cannot see someone else's task
    other = client.get(f"/api/tasks/{task.json()['id']}", headers=auth(intern_token))
    assert other.status_code == 403


def test_duplicate_email_conflict(client, admin_token):
    payload = {"email": "dup@wc.test", "username": "dup", "full_name": "Dup",
               "password": "StrongPass123", "role": "intern"}
    assert client.post("/api/users", headers=auth(admin_token), json=payload).status_code == 201
    dup = dict(payload, username="dup2")
    assert client.post("/api/users", headers=auth(admin_token), json=dup).status_code == 409


def test_weak_password_rejected(client, admin_token):
    r = client.post("/api/users", headers=auth(admin_token), json={
        "email": "weak@wc.test", "username": "weak", "full_name": "Weak",
        "password": "short", "role": "intern"})
    assert r.status_code == 422


def test_project_lifecycle_and_versions(client, dev_token):
    p = client.post("/api/projects", headers=auth(dev_token),
                    json={"name": "Demo Project", "language": "python"})
    assert p.status_code == 201, p.text
    pid = p.json()["id"]

    save = client.post(f"/api/projects/{pid}/save", headers=auth(dev_token), json={
        "files": [{"path": "main.py", "content": "print('hi')\n"},
                  {"path": "utils/helpers.py", "content": "X = 1\n"}],
        "label": "first save"})
    assert save.status_code == 200
    assert save.json()["current_version"] >= 2

    versions = client.get(f"/api/projects/{pid}/versions", headers=auth(dev_token)).json()
    assert len(versions) >= 2

    files = client.get(f"/api/projects/{pid}/files", headers=auth(dev_token)).json()
    assert {f["path"] for f in files} == {"main.py", "utils/helpers.py"}

    zip_r = client.get(f"/api/projects/{pid}/download", headers=auth(dev_token))
    assert zip_r.status_code == 200 and zip_r.content[:2] == b"PK"


def test_path_traversal_blocked(client, dev_token):
    pid = client.post("/api/projects", headers=auth(dev_token),
                      json={"name": "Traversal", "language": "python"}).json()["id"]
    r = client.put(f"/api/projects/{pid}/files", headers=auth(dev_token),
                   json={"path": "../../etc/passwd", "content": "x"})
    assert r.status_code == 422


def test_code_execution_fails_closed_without_docker(client, dev_token):
    from unittest.mock import patch
    from app.core.config import settings

    with patch.object(settings, "RUNNER_MODE", "local"):
        r = client.post("/api/workspace/run", headers=auth(dev_token),
                        json={"language": "python", "code": "print(2+2)"})
    assert r.status_code == 200
    assert r.json()["ok"] is False
    assert "RUNNER_MODE=docker" in r.json()["stderr"]


def test_code_execution_returns_docker_output_and_limits(client, dev_token, monkeypatch):
    import subprocess
    from app.core.config import settings
    from app.services import runner

    seen = {}
    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        return subprocess.CompletedProcess(argv, 0, stdout="4\n", stderr="")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    with patch_settings(settings, "RUNNER_MODE", "docker"):
        response = client.post("/api/workspace/run", headers=auth(dev_token),
                               json={"language": "python", "code": "print(2+2)"})
    assert response.status_code == 200 and response.json()["ok"] is True
    assert response.json()["stdout"] == "4\n"
    argv = seen["argv"]
    assert argv[argv.index("--network") + 1] == "none"
    assert "--read-only" in argv and "--cap-drop" in argv and "ALL" in argv
    assert "--memory" in argv and "--pids-limit" in argv



def test_docker_runner_timeout_cleans_up_container(monkeypatch):
    import subprocess
    from app.core.config import settings
    from app.services import runner

    calls = []
    def fake_run(argv, **kwargs):
        calls.append(argv)
        if argv[1] == "run":
            raise subprocess.TimeoutExpired(argv, kwargs.get("timeout"))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    monkeypatch.setattr(runner, "_runner_image", lambda language: (True, f"workcode-runner-{language}:latest"))
    with patch_settings(settings, "RUNNER_MODE", "docker"):
        result = runner.execute("python", "while True: pass")
    assert result["ok"] is False and result["timed_out"] is True
    assert calls[0][calls[0].index("--network") + 1] == "none"
    assert calls[1][0] == str(runner.DOCKER_CLI or "docker")
    assert calls[1][1:3] == ["rm", "-f"]
    assert calls[1][3] == calls[0][calls[0].index("--name") + 1]

def patch_settings(settings, field, value):
    from unittest.mock import patch
    return patch.object(settings, field, value)


def test_admin_cannot_run_code(client, admin_token):
    r = client.post("/api/workspace/run", headers=auth(admin_token),
                    json={"language": "python", "code": "print(1)"})
    assert r.status_code == 403


def test_submission_and_review_flow(client, admin_token, dev_token, intern_token):
    # Admin assigns an intern task to an On-Roll led team.
    dev_id = client.get("/api/auth/me", headers=auth(dev_token)).json()["id"]
    team = client.post("/api/teams", headers=auth(admin_token), json={
        "name": f"Review team {dev_id[:8]}", "lead_user_id": dev_id}).json()
    intern_id = client.get("/api/users?q=intern", headers=auth(admin_token)).json()[0]["id"]
    task_response = client.post("/api/tasks", headers=auth(admin_token), json={
        "title": "Submission flow", "assignee_id": intern_id, "team_id": team["id"]})
    assert task_response.status_code == 201, task_response.text
    task = task_response.json()
    pid = client.post("/api/projects", headers=auth(intern_token),
                      json={"name": "Intern Work", "language": "python"}).json()["id"]
    client.post(f"/api/projects/{pid}/save", headers=auth(intern_token), json={
        "files": [{"path": "main.py", "content": "print('done')"}]})

    sub = client.post("/api/submissions", headers=auth(intern_token),
                      json={"task_id": task["id"], "project_id": pid, "notes": "ready",
                            "test_results": [{"index": 1, "expected": "done", "actual": "done",
                                              "error": "", "passed": True}]})
    assert sub.status_code == 201, sub.text
    sid = sub.json()["id"]
    retried = client.post("/api/submissions", headers=auth(intern_token),
                          json={"task_id": task["id"], "project_id": pid, "notes": "ready"})
    assert retried.status_code == 200
    assert retried.json()["id"] == sid

    # Another On-Roll user cannot take over the assigned review.
    other = client.post("/api/users", headers=auth(admin_token), json={
        "email": "other.onroll@wc.test", "username": "otheronroll", "full_name": "Other On-Roll",
        "password": "OtherPass123", "role": "developer_onroll"})
    assert other.status_code == 201, other.text
    other_token = client.post("/api/auth/login", json={
        "identifier": "other.onroll@wc.test", "password": "OtherPass123"}).json()["access_token"]
    assert client.post(f"/api/submissions/{sid}/review", headers=auth(other_token),
                       json={"decision": "approved"}).status_code == 403

    # submitter cannot review their own work
    assert client.post(f"/api/submissions/{sid}/review", headers=auth(intern_token),
                       json={"decision": "approved"}).status_code == 403

    # Admin cannot skip the assigned On-Roll review or finalize prematurely.
    assert client.post(f"/api/submissions/{sid}/review", headers=auth(admin_token),
                       json={"decision": "approved"}).status_code == 403
    assert client.post(f"/api/submissions/{sid}/final", headers=auth(admin_token),
                       json={"decision": "approved"}).status_code == 409
    assert client.post(f"/api/submissions/{sid}/review", headers=auth(dev_token),
                       json={"decision": "rejected"}).status_code == 422

    review = client.post(f"/api/submissions/{sid}/review", headers=auth(dev_token),
                         json={"decision": "approved"})
    assert review.status_code == 200
    detail_after_review = client.get(f"/api/submissions/{sid}", headers=auth(admin_token)).json()
    assert detail_after_review["status"] == "pending_admin_review"
    assert detail_after_review["forwarded_to_admin_at"] is not None
    assert detail_after_review["test_results"] == [{"index": 1, "expected": "done", "actual": "done",
                                                     "error": "", "passed": True}]
    assert any(n["type"] == "submission_forwarded" for n in
               client.get("/api/notifications", headers=auth(admin_token)).json())
    # Legacy clients can still call explicit forward after automatic forwarding.
    assert client.post(f"/api/submissions/{sid}/forward", headers=auth(dev_token)).status_code == 200
    final = client.post(f"/api/submissions/{sid}/final", headers=auth(admin_token),
                        json={"decision": "approved", "comments": "Verified"})
    assert final.status_code == 200

    detail = client.get(f"/api/submissions/{sid}", headers=auth(admin_token)).json()
    assert detail["status"] == "final_approved"
    assert len(detail["files"]) == 1  # snapshot preserved
    intern_notices = client.get("/api/notifications", headers=auth(intern_token)).json()
    reviewer_notices = client.get("/api/notifications", headers=auth(dev_token)).json()
    assert any("task_assigned" in notice["type"] for notice in intern_notices)
    assert any("submission_received" in notice["type"] for notice in reviewer_notices)
    assert sum("submission_received" in notice["type"] for notice in reviewer_notices) == 1
    assert any("submission_final_decision" in notice["type"] for notice in intern_notices)
    audit_rows = client.get("/api/admin/audit-logs", headers=auth(admin_token),
                            params={"size": 200, "scope": "all"}).json()["items"]
    actions = {row["action"] for row in audit_rows}
    assert {"task.created", "project.saved", "submission.created", "submission.approved",
            "submission.forwarded", "submission.final_approved"} <= actions

    # Exercise the Off-Roll assignment path as a submitter in the same staged workflow.
    offroll = client.post("/api/users", headers=auth(admin_token), json={
        "email": "workflow.offroll@wc.test", "username": "workflowoffroll",
        "full_name": "Workflow Off-Roll", "password": "StrongPass123",
        "role": "developer_offroll", "team_id": team["id"]})
    assert offroll.status_code == 201, offroll.text
    offroll_token = client.post("/api/auth/login", json={
        "identifier": "workflow.offroll@wc.test", "password": "StrongPass123"}).json()["access_token"]
    offroll_id = offroll.json()["id"]
    assert client.post(f"/api/teams/{team['id']}/members", headers=auth(admin_token),
                       json={"user_id": dev_id}).status_code in (201, 409)
    offroll_task = client.post("/api/tasks", headers=auth(dev_token), json={
        "title": "Off-Roll submission flow", "assignee_id": offroll_id, "team_id": team["id"]})
    assert offroll_task.status_code == 201, offroll_task.text
    offroll_pid = client.post("/api/projects", headers=auth(offroll_token), json={
        "name": "Off-Roll Work", "language": "python"}).json()["id"]
    saved = client.post(f"/api/projects/{offroll_pid}/save", headers=auth(offroll_token), json={
        "files": [{"path": "main.py", "content": "print('offroll')"}]})
    assert saved.status_code == 200
    offroll_sub = client.post("/api/submissions", headers=auth(offroll_token), json={
        "task_id": offroll_task.json()["id"], "project_id": offroll_pid, "notes": "off-roll ready"})
    assert offroll_sub.status_code == 201, offroll_sub.text
    offroll_sid = offroll_sub.json()["id"]
    assert client.post(f"/api/submissions/{offroll_sid}/review", headers=auth(dev_token),
                       json={"decision": "approved"}).status_code == 200
    assert client.post(f"/api/submissions/{offroll_sid}/forward", headers=auth(dev_token)).status_code == 200
    offroll_final = client.post(f"/api/submissions/{offroll_sid}/final", headers=auth(admin_token),
                                json={"decision": "approved", "comments": "Reviewed"})
    assert offroll_final.status_code == 200
    assert client.get(f"/api/submissions/{offroll_sid}", headers=auth(admin_token)).json()["status"] == "final_approved"
    offroll_notices = client.get("/api/notifications", headers=auth(offroll_token)).json()
    assert any("submission_final_decision" in notice["type"] for notice in offroll_notices)

    # On-Roll employees may also submit assigned work when another On-Roll
    # employee is assigned as the reviewer.
    onroll_team = client.post("/api/teams", headers=auth(admin_token), json={
        "name": f"On-Roll submit team {dev_id[:8]}", "lead_user_id": other.json()["id"]})
    onroll_task = client.post("/api/tasks", headers=auth(admin_token), json={
        "title": "On-Roll submission flow", "assignee_id": dev_id,
        "team_id": onroll_team.json()["id"]})
    assert onroll_task.status_code == 201, onroll_task.text
    onroll_pid = client.post("/api/projects", headers=auth(dev_token), json={
        "name": "On-Roll Work", "language": "python"}).json()["id"]
    assert client.post(f"/api/projects/{onroll_pid}/save", headers=auth(dev_token), json={
        "files": [{"path": "main.py", "content": "print('onroll')"}]}).status_code == 200
    onroll_sub = client.post("/api/submissions", headers=auth(dev_token), json={
        "task_id": onroll_task.json()["id"], "project_id": onroll_pid})
    assert onroll_sub.status_code == 201, onroll_sub.text
    onroll_sid = onroll_sub.json()["id"]
    assert onroll_sub.json()["status"] == "pending_permanent_review"
    assert client.post(f"/api/submissions/{onroll_sid}/review", headers=auth(other_token),
                       json={"decision": "approved"}).status_code == 200
    onroll_detail = client.get(f"/api/submissions/{onroll_sid}", headers=auth(admin_token)).json()
    assert onroll_detail["status"] == "pending_admin_review"


def test_timer_flow(client, dev_token):
    start = client.post("/api/timer/start", headers=auth(dev_token))
    assert start.status_code == 200 and start.json()["status"] == "running"

    hb = client.post("/api/timer/heartbeat", headers=auth(dev_token),
                     json={"event": "heartbeat", "meta": {}})
    assert hb.status_code == 200 and hb.json()["active"] is True

    pause = client.post("/api/timer/pause", headers=auth(dev_token), json={"event": "pause"})
    assert pause.status_code == 200 and pause.json()["status"] == "paused"

    resume = client.post("/api/timer/resume", headers=auth(dev_token))
    assert resume.json()["status"] == "running"

    stop = client.post("/api/timer/stop", headers=auth(dev_token))
    assert stop.json()["status"] == "stopped"

    att = client.get("/api/timer/attendance", headers=auth(dev_token)).json()
    assert att["days"], "attendance row must exist after a session"



def test_timer_visibility_heartbeat_keeps_session_running(client, dev_token):
    started = client.post("/api/timer/start", headers=auth(dev_token))
    assert started.status_code == 200
    response = client.post("/api/timer/heartbeat", headers=auth(dev_token), json={
        "event": "hidden", "meta": {"reason": "tab-hidden"}})
    assert response.status_code == 200 and response.json()["status"] == "running"
    current = client.get("/api/timer/current", headers=auth(dev_token)).json()
    assert current["active"] is True and current["session"]["status"] == "running"

def test_admin_has_no_timer(client, admin_token):
    assert client.post("/api/timer/start", headers=auth(admin_token)).status_code == 403


def test_employee_report_is_private_and_available_to_workers(client, admin_token, dev_token,
                                                              intern_token):
    own = client.get("/api/reports/me", headers=auth(intern_token))
    assert own.status_code == 200, own.text
    assert own.json()["employee"]["role"] == "intern"
    assert "sessions" in own.json() and "activity" in own.json()
    assert client.get("/api/reports/me", headers=auth(dev_token)).status_code == 200
    assert client.get("/api/reports/me", headers=auth(admin_token)).status_code == 403
    csv_export = client.get("/api/reports/me/export?format=csv", headers=auth(intern_token))
    assert csv_export.status_code == 200 and csv_export.headers["content-type"].startswith("text/csv")
    excel_export = client.get("/api/reports/me/export?format=xlsx", headers=auth(intern_token))
    assert excel_export.status_code == 200 and excel_export.content.startswith(b"PK")
    from io import BytesIO
    from openpyxl import load_workbook
    workbook = load_workbook(BytesIO(excel_export.content), read_only=True)
    assert workbook.active["A1"].value == "section"
    pdf_export = client.get("/api/reports/me/export?format=pdf", headers=auth(intern_token))
    assert pdf_export.status_code == 200 and pdf_export.content.startswith(b"%PDF-")
    assert client.get("/api/reports/me/export?format=unknown", headers=auth(intern_token)).status_code == 422
    reversed_dates = client.get("/api/reports/me?date_from=2026-12-01&date_to=2026-11-01",
                                headers=auth(intern_token))
    assert reversed_dates.status_code == 422


def test_onroll_team_report_is_scoped_to_assigned_review_team(client, admin_token, dev_token,
                                                               intern_token):
    reviewer_id = client.get("/api/auth/me", headers=auth(dev_token)).json()["id"]
    intern_id = client.get("/api/auth/me", headers=auth(intern_token)).json()["id"]
    team = client.post("/api/teams", headers=auth(admin_token), json={
        "name": "Reports team " + reviewer_id[:8], "lead_user_id": reviewer_id,
    })
    assert team.status_code == 201, team.text
    task = client.post("/api/tasks", headers=auth(admin_token), json={
        "title": "Report scope task", "assignee_id": intern_id, "team_id": team.json()["id"],
        "priority": "normal",
    })
    assert task.status_code == 201, task.text

    report = client.get("/api/reports/team", headers=auth(dev_token))
    assert report.status_code == 200, report.text
    employees = report.json()["employees"]
    intern_report = next(item for item in employees if item["employee"]["id"] == intern_id)
    assert any(item["id"] == task.json()["id"] for item in intern_report["tasks"])
    team_export = client.get(f"/api/reports/team/{intern_id}/export?format=pdf", headers=auth(dev_token))
    assert team_export.status_code == 200 and team_export.content.startswith(b"%PDF-")
    assert client.get("/api/reports/team", headers=auth(intern_token)).status_code == 403
    assert client.get("/api/reports/team", headers=auth(admin_token)).status_code == 403
    assert client.get(f"/api/reports/team/{intern_id}/export", headers=auth(intern_token)).status_code == 403


def test_recording_requires_consent(client, dev_token, admin_token):
    r = client.post("/api/recordings/start", headers=auth(dev_token), json={"consent": False})
    assert r.status_code == 422
    ok = client.post("/api/recordings/start", headers=auth(dev_token), json={"consent": True})
    assert ok.status_code == 201
    rid = ok.json()["id"]
    up = client.post(f"/api/recordings/{rid}/chunks", headers=auth(dev_token),
                     data={"seq": 0}, files={"chunk": ("c.webm", b"0123456789", "video/webm")})
    assert up.status_code == 200
    retry = client.post(f"/api/recordings/{rid}/chunks", headers=auth(dev_token),
                        data={"seq": 0}, files={"chunk": ("c.webm", b"0123456789", "video/webm")})
    assert retry.status_code == 200
    detail = client.get(f"/api/recordings/{rid}", headers=auth(dev_token)).json()
    assert detail["chunk_count"] == 1 and detail["size_bytes"] == 10
    done = client.post(f"/api/recordings/{rid}/complete?duration_seconds=12", headers=auth(dev_token))
    assert done.status_code == 200 and done.json()["status"] == "completed"
    video = client.get(f"/api/recordings/{rid}/video", headers=auth(dev_token))
    assert video.status_code == 200 and video.content == b"0123456789"
    admin_video = client.get(f"/api/recordings/{rid}/video", headers=auth(admin_token))
    assert admin_video.status_code == 200 and admin_video.content == b"0123456789"
    playback = client.get(f"/api/recordings/{rid}/playback", headers=auth(admin_token))
    assert playback.status_code == 200 and playback.json()["url"].endswith(f"/{rid}/video")
    cookie_video = client.get(playback.json()["url"])
    assert cookie_video.status_code == 200 and cookie_video.content == b"0123456789"




def test_recording_cannot_complete_with_a_missing_chunk(client, dev_token):
    rec = client.post("/api/recordings/start", headers=auth(dev_token), json={"consent": True}).json()
    uploaded = client.post(f"/api/recordings/{rec['id']}/chunks", headers=auth(dev_token),
                           data={"seq": 1}, files={"chunk": ("c.webm", b"later", "video/webm")})
    assert uploaded.status_code == 200
    response = client.post(f"/api/recordings/{rec['id']}/complete", headers=auth(dev_token))
    assert response.status_code == 409 and "incomplete" in response.json()["detail"]

def test_recording_access_for_assigned_onroll_reviewer_only(client, admin_token, dev_token, intern_token):
    reviewer_id = client.get("/api/auth/me", headers=auth(dev_token)).json()["id"]
    intern_id = client.get("/api/auth/me", headers=auth(intern_token)).json()["id"]
    team = client.post("/api/teams", headers=auth(admin_token), json={
        "name": f"Recording review {reviewer_id[:8]}", "lead_user_id": reviewer_id}).json()
    task = client.post("/api/tasks", headers=auth(admin_token), json={
        "title": "Recording permissions", "assignee_id": intern_id, "team_id": team["id"]}).json()
    recording = client.post("/api/recordings/start", headers=auth(intern_token), json={
        "consent": True, "task_id": task["id"]}).json()
    uploaded = client.post(f"/api/recordings/{recording['id']}/chunks", headers=auth(intern_token),
                           data={"seq": 0}, files={"chunk": ("c.webm", b"video-chunk", "video/webm")})
    assert uploaded.status_code == 200
    assert client.post(f"/api/recordings/{recording['id']}/complete", headers=auth(intern_token)).status_code == 200

    offroll = client.post("/api/users", headers=auth(admin_token), json={
        "email": "recording.offroll@wc.test", "username": "recordingoffroll",
        "full_name": "Recording Offroll", "password": "StrongPass123", "role": "developer_offroll"})
    assert offroll.status_code == 201
    offroll_token = client.post("/api/auth/login", json={
        "identifier": "recording.offroll@wc.test", "password": "StrongPass123"}).json()["access_token"]

    reviewer_video = client.get(f"/api/recordings/{recording['id']}/video", headers=auth(dev_token))
    assert reviewer_video.status_code == 200 and reviewer_video.content == b"video-chunk"
    assert client.get(f"/api/recordings/{recording['id']}", headers=auth(offroll_token)).status_code == 403
    assert client.get(f"/api/recordings/{recording['id']}/video", headers=auth(offroll_token)).status_code == 403
    admin_video = client.get(f"/api/recordings/{recording['id']}/video", headers=auth(admin_token))
    assert admin_video.status_code == 200 and admin_video.content == b"video-chunk"
    assert any(row["id"] == recording["id"] for row in client.get(
        "/api/recordings", headers=auth(dev_token), params={"task_id": task["id"]}).json())


def test_recording_cannot_be_completed_without_uploaded_data(client, dev_token):
    recording = client.post("/api/recordings/start", headers=auth(dev_token), json={"consent": True})
    assert recording.status_code == 201
    response = client.post(f"/api/recordings/{recording.json()['id']}/complete",
                           headers=auth(dev_token))
    assert response.status_code == 409


def test_chat_room_and_message(client, dev_token, admin_token):
    contacts = client.get("/api/chat/contacts", headers=auth(dev_token)).json()
    assert contacts
    room = client.post("/api/chat/rooms", headers=auth(dev_token),
                       json={"type": "direct", "participant_ids": [contacts[0]["id"]]})
    assert room.status_code == 201
    rid = room.json()["id"]
    msg = client.post(f"/api/chat/rooms/{rid}/messages", headers=auth(dev_token),
                      json={"body": "hello there"})
    assert msg.status_code == 201
    history = client.get(f"/api/chat/rooms/{rid}/messages", headers=auth(dev_token)).json()
    assert history[-1]["body"] == "hello there"


def test_chat_attachment_upload_download_and_rejection(client, dev_token, intern_token):
    intern = client.get("/api/auth/me", headers=auth(intern_token)).json()
    room = client.post("/api/chat/rooms", headers=auth(dev_token),
                       json={"type": "direct", "participant_ids": [
                           intern["id"]
                       ]})
    assert room.status_code == 201, room.text
    room_id = room.json()["id"]

    uploaded = client.post(
        f"/api/chat/rooms/{room_id}/attachments",
        headers=auth(dev_token),
        files={"file": ("notes.txt", b"WORKCODE attachment", "text/plain")},
    )
    assert uploaded.status_code == 201, uploaded.text
    attachment = uploaded.json()["attachments"][0]
    downloaded = client.get(attachment["url"], headers=auth(dev_token))
    assert downloaded.status_code == 200
    assert downloaded.content == b"WORKCODE attachment"

    invalid = client.post(
        f"/api/chat/rooms/{room_id}/attachments",
        headers=auth(dev_token),
        files={"file": ("payload.exe", b"not allowed", "application/octet-stream")},
    )
    assert invalid.status_code == 415


def test_notifications(client, dev_token):
    n = client.get("/api/notifications", headers=auth(dev_token)).json()
    assert isinstance(n, list)
    assert client.get("/api/notifications/unread-count", headers=auth(dev_token)).status_code == 200


def test_audit_visibility_per_admin(client, admin_token):
    r = client.get("/api/admin/audit-logs", headers=auth(admin_token)).json()
    assert r["visibility"] in ("per_admin", "all_admins")
    assert "items" in r


def test_reports_and_csv(client, admin_token):
    s = client.get("/api/reports/summary", headers=auth(admin_token))
    assert s.status_code == 200 and "headcount" in s.json()
    intern_summary = client.get("/api/reports/summary?role=intern", headers=auth(admin_token))
    assert intern_summary.status_code == 200
    assert intern_summary.json()["headcount"]["total"] > 0
    assert intern_summary.json()["headcount"]["interns"] == intern_summary.json()["headcount"]["total"]
    assert all(person["role"] == "intern" for person in intern_summary.json()["people"])
    assert client.get("/api/reports/summary?role=superuser", headers=auth(admin_token)).status_code == 422
    csv_r = client.get("/api/reports/export/tasks", headers=auth(admin_token))
    assert csv_r.status_code == 200 and "code,title" in csv_r.text
    xlsx_r = client.get("/api/reports/export/tasks?format=xlsx", headers=auth(admin_token))
    assert xlsx_r.status_code == 200 and xlsx_r.content.startswith(b"PK")
    pdf_r = client.get("/api/reports/export/tasks?format=pdf", headers=auth(admin_token))
    assert pdf_r.status_code == 200 and pdf_r.content.startswith(b"%PDF-")
    assert client.get("/api/reports/export/tasks?format=doc", headers=auth(admin_token)).status_code == 422


def test_conversion_requires_admin(client, dev_token, admin_token, intern_token):
    interns = client.get("/api/users?role=intern", headers=auth(admin_token)).json()
    target = next(u for u in interns if u["email"] == "intern@wc.test")
    assert client.post(f"/api/users/{target['id']}/convert", headers=auth(dev_token),
                       json={"to_role": "developer_onroll"}).status_code == 403
    r = client.post(f"/api/users/{target['id']}/convert", headers=auth(admin_token),
                    json={"to_role": "developer_onroll", "notes": "strong performer"})
    assert r.status_code == 200 and r.json()["role"] == "developer_onroll"
    hist = client.get(f"/api/users/{target['id']}/history", headers=auth(admin_token)).json()
    assert any(h["event"] == "converted" for h in hist)


def test_meeting_without_google_fails_honestly(client, admin_token):
    r = client.post("/api/meetings", headers=auth(admin_token), json={
        "title": "Sprint review", "starts_at": "2027-01-05T10:00:00Z",
        "ends_at": "2027-01-05T11:00:00Z", "create_meet_link": True, "participant_ids": []})
    assert r.status_code == 201
    body = r.json()
    assert body["meet_url"] is None          # never a fake link
    assert body["error_message"]
