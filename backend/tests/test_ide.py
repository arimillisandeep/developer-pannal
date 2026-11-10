"""IDE tests: real PTY terminal, file system, preview, git, workspace execution."""
import json
import os
import shutil
import time

from tests.helpers import auth


def _project(client, token, name, language="python"):
    r = client.post("/api/projects", headers=auth(token), json={"name": name, "language": language})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _write(client, token, pid, path, content=""):
    r = client.post(f"/api/workspace/{pid}/file", headers=auth(token),
                    json={"path": path, "content": content})
    assert r.status_code == 200, r.text
    return r.json()


def _docker_image_available(language):
    if not shutil.which("docker"):
        return False
    import subprocess
    from app.core.config import settings
    try:
        result = subprocess.run(["docker", "image", "inspect",
                                 f"{settings.DOCKER_IMAGE_PREFIX}-{language}"],
                                capture_output=True, timeout=10, check=False)
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


# ------------------------------------------------------------------ file system
def test_file_crud_and_tree(client, dev_token):
    pid = _project(client, dev_token, "FS Project", "python")
    _write(client, dev_token, pid, "src/app.py", "print('hi')\n")
    _write(client, dev_token, pid, "src/lib/util.py", "X = 1\n")
    client.post(f"/api/workspace/{pid}/file", headers=auth(dev_token),
                json={"path": "assets", "is_dir": True})

    tree = client.get(f"/api/workspace/{pid}/tree", headers=auth(dev_token)).json()
    paths = {f["path"] for f in tree["files"]}
    assert {"src/app.py", "src/lib/util.py", "assets"} <= paths

    read = client.get(f"/api/workspace/{pid}/file", headers=auth(dev_token),
                      params={"path": "src/app.py"}).json()
    assert read["content"] == "print('hi')\n"

    renamed = client.patch(f"/api/workspace/{pid}/file", headers=auth(dev_token),
                           json={"from_path": "src/app.py", "to_path": "src/main.py",
                                 "action": "rename"})
    assert renamed.status_code == 200, renamed.text

    dup = client.patch(f"/api/workspace/{pid}/file", headers=auth(dev_token),
                       json={"from_path": "src/main.py", "action": "duplicate"})
    assert dup.json()["to"] == "src/main-copy.py"

    copied = client.patch(f"/api/workspace/{pid}/file", headers=auth(dev_token),
                          json={"from_path": "src/main.py", "to_path": "backup/main.py",
                                "action": "copy"})
    assert copied.status_code == 200

    deleted = client.delete(f"/api/workspace/{pid}/file", headers=auth(dev_token),
                            params={"path": "src/main-copy.py"})
    assert deleted.status_code == 200


def test_path_traversal_blocked_in_fs(client, dev_token):
    pid = _project(client, dev_token, "Traversal FS")
    r = client.post(f"/api/workspace/{pid}/file", headers=auth(dev_token),
                    json={"path": "../../../etc/evil", "content": "x"})
    assert r.status_code == 400


def test_search_finds_content(client, dev_token):
    pid = _project(client, dev_token, "Search Project")
    _write(client, dev_token, pid, "main.py", "def unicorn_marker():\n    return 42\n")
    r = client.get(f"/api/workspace/{pid}/search", headers=auth(dev_token),
                   params={"q": "unicorn_marker"})
    assert r.status_code == 200
    assert any(h["path"] == "main.py" for h in r.json()["results"])


# --------------------------------------------------------------------- terminal
def _drain(ws, needle, limit=2000, timeout=20):
    """Read WS frames until `needle` appears in the output."""
    deadline = time.time() + timeout
    text = ""
    for _ in range(limit):
        if time.time() > deadline:
            break
        message = ws.receive_json()
        if message.get("type") == "output":
            text += message.get("data", "")
            if needle in text:
                return text
    return text


def test_terminal_fails_closed_without_docker(client, dev_token, monkeypatch):
    from app.core.config import settings
    pid = _project(client, dev_token, "Terminal Requires Docker")
    with monkeypatch.context() as patch:
        patch.setattr(settings, "RUNNER_MODE", "local")
        response = client.post(f"/api/workspace/{pid}/terminals", headers=auth(dev_token))
    assert response.status_code == 503
    assert "host-shell fallback is disabled" in response.json()["detail"]


def test_terminal_docker_start_uses_restricted_container(client, dev_token, monkeypatch):
    import subprocess
    from types import SimpleNamespace
    from app.core.config import settings
    from app.services.terminal import terminals

    pid = _project(client, dev_token, "Terminal Docker Flags")
    calls = []
    def fake_run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="container-id", stderr="")
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("app.services.runner.ensure_terminal_image", lambda: (True, "workcode-terminal:latest"))
    monkeypatch.setattr(terminals, "create", lambda **kwargs: SimpleNamespace(
        id="test-terminal", cwd=kwargs["cwd"], shell="/bin/bash"))
    monkeypatch.setattr(terminals, "list_for_project", lambda project_id: [])
    monkeypatch.setattr(settings, "RUNNER_MODE", "docker")

    response = client.post(f"/api/workspace/{pid}/terminals", headers=auth(dev_token))
    assert response.status_code == 200, response.text
    assert response.json()["isolated"] is True
    argv = calls[0]
    assert argv[argv.index("--pull=never")] == "--pull=never"
    assert argv[argv.index("--network") + 1] == "none"
    assert "--read-only" in argv and "--cap-drop" in argv and "ALL" in argv
    assert "--security-opt" in argv and "no-new-privileges" in argv
    assert "--memory" in argv and "--memory-swap" in argv and "--pids-limit" in argv
    volumes = [argv[index + 1] for index, value in enumerate(argv[:-1]) if value == "-v"]
    assert len(volumes) == 1 and volumes[0].endswith(":/work:rw")
    assert all("/home" not in volume for volume in volumes)


def test_terminal_missing_image_returns_local_build_instructions(client, dev_token, monkeypatch):
    from app.core.config import settings
    pid = _project(client, dev_token, "Docker Terminal Missing Image")
    monkeypatch.setattr(settings, "RUNNER_MODE", "docker")
    monkeypatch.setattr("app.services.runner.ensure_terminal_image",
                        lambda: (False, "Docker terminal image build failed; retry after starting Docker Desktop."))

    response = client.post(f"/api/workspace/{pid}/terminals", headers=auth(dev_token))
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert "image build failed" in detail
    assert "docker login" not in detail


def test_terminal_docker_start_failure_is_503(client, dev_token, monkeypatch):
    import subprocess
    from app.core.config import settings
    pid = _project(client, dev_token, "Docker Terminal Failure")
    monkeypatch.setattr(settings, "RUNNER_MODE", "docker")
    monkeypatch.setattr("app.services.runner.ensure_terminal_image", lambda: (True, "workcode-terminal:latest"))
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs:
                        subprocess.CompletedProcess(args[0], 125, stdout="", stderr="daemon unavailable"))
    response = client.post(f"/api/workspace/{pid}/terminals", headers=auth(dev_token))
    assert response.status_code == 503
    assert "daemon unavailable" in response.json()["detail"]


def test_terminal_owner_isolation_is_enforced_before_docker(client, dev_token, admin_token, monkeypatch):
    from app.core.config import settings
    other = client.post("/api/users", headers=auth(admin_token), json={
        "email": "ide.other@wc.test", "username": "ideother", "full_name": "IDE Other",
        "password": "StrongPass123", "role": "developer_onroll"}).json()
    other_token = client.post("/api/auth/login", json={
        "identifier": other["email"], "password": "StrongPass123"}).json()["access_token"]
    pid = _project(client, dev_token, "Owner Only")
    monkeypatch.setattr(settings, "RUNNER_MODE", "docker")
    response = client.post(f"/api/workspace/{pid}/terminals", headers=auth(other_token))
    assert response.status_code == 403

# ---------------------------------------------------------------------- preview
def test_static_preview_serves_real_files(client, dev_token):
    pid = _project(client, dev_token, "Preview Project", "html")
    _write(client, dev_token, pid, "index.html",
           '<html><head><link rel="stylesheet" href="./style.css"></head>'
           '<body><h1 id="t">Hello</h1><script src="./script.js"></script></body></html>')
    _write(client, dev_token, pid, "style.css", "h1{color:red}")
    _write(client, dev_token, pid, "script.js", "console.log('ok')")

    base = f"/api/workspace/preview/{pid}"
    html = client.get(f"{base}?token={dev_token}")
    assert html.status_code == 200
    assert "text/html" in html.headers["content-type"]
    css = client.get(f"{base}/style.css?token={dev_token}")
    assert css.status_code == 200 and "text/css" in css.headers["content-type"]
    assert "color:red" in css.text
    js = client.get(f"{base}/script.js?token={dev_token}")
    assert "javascript" in js.headers["content-type"]

    assert client.get(f"{base}?token=bad").status_code == 401
    assert client.get(f"{base}/missing.txt?token={dev_token}").status_code == 404


# ------------------------------------------------------------------------ runs
def test_multifile_python_run(client, dev_token):
    if not _docker_image_available("python"):
        import pytest
        pytest.skip("Docker runner image is unavailable; user code is never run on the host")
    pid = _project(client, dev_token, "Multi Python", "python")
    _write(client, dev_token, pid, "helpers.py", "def double(n):\n    return n * 2\n")
    _write(client, dev_token, pid, "main.py",
           "from helpers import double\nprint('result', double(21))\n")
    r = client.post("/api/workspace/run", headers=auth(dev_token),
                    json={"language": "python", "project_id": pid})
    body = r.json()
    assert body["ok"] is True, body
    assert "result 42" in body["stdout"]


def test_multifile_html_run_reports_preview(client, dev_token):
    pid = _project(client, dev_token, "Multi HTML", "html")
    _write(client, dev_token, pid, "index.html", "<h1>x</h1>")
    r = client.post("/api/workspace/run", headers=auth(dev_token),
                    json={"language": "html", "project_id": pid}).json()
    assert r["runner"] == "preview"


def test_c_and_cpp_compile_and_run(client, dev_token):
    import pytest
    if not _docker_image_available("c") or not _docker_image_available("cpp"):
        pytest.skip("C/C++ Docker runner images are unavailable; user code is never run on the host")
    cpid = _project(client, dev_token, "C Project", "c")
    _write(client, dev_token, cpid, "main.c",
           '#include <stdio.h>\nint main(){printf("C_OK %d\\n", 6*7);return 0;}\n')
    c = client.post("/api/workspace/run", headers=auth(dev_token),
                    json={"language": "c", "project_id": cpid}).json()
    assert c["ok"] is True and "C_OK 42" in c["stdout"], c

    ppid = _project(client, dev_token, "CPP Project", "cpp")
    _write(client, dev_token, ppid, "main.cpp",
           '#include <iostream>\nint main(){std::cout<<"CPP_OK "<<(6*7)<<std::endl;return 0;}\n')
    cpp = client.post("/api/workspace/run", headers=auth(dev_token),
                      json={"language": "cpp", "project_id": ppid}).json()
    assert cpp["ok"] is True and "CPP_OK 42" in cpp["stdout"], cpp


def test_java_compile_and_run(client, dev_token):
    if not _docker_image_available("java"):
        import pytest
        pytest.skip("Java Docker runner image is unavailable; user code is never run on the host")
    pid = _project(client, dev_token, "Java Project", "java")
    _write(client, dev_token, pid, "Main.java",
           'public class Main { public static void main(String[] a){'
           ' System.out.println("JAVA_OK " + (6*7)); } }')
    r = client.post("/api/workspace/run", headers=auth(dev_token),
                    json={"language": "java", "project_id": pid}).json()
    if "not installed" in (r.get("stderr") or ""):
        import shutil
        assert shutil.which("javac") is None
    else:
        assert r["ok"] is True and "JAVA_OK 42" in r["stdout"], r


def test_compile_error_is_reported(client, dev_token):
    if not _docker_image_available("c"):
        import pytest
        pytest.skip("C Docker runner image is unavailable; user code is never run on the host")
    pid = _project(client, dev_token, "Broken C", "c")
    _write(client, dev_token, pid, "main.c", "int main(){ this is not c }")
    r = client.post("/api/workspace/run", headers=auth(dev_token),
                    json={"language": "c", "project_id": pid}).json()
    assert r["ok"] is False and "Compilation failed:" in (r["stderr"] or "")


# ------------------------------------------------------------------------ git
def test_git_init_add_commit_log(client, dev_token):
    pid = _project(client, dev_token, "Git Project")
    _write(client, dev_token, pid, "main.py", "print('v1')\n")

    assert client.post(f"/api/workspace/{pid}/git", headers=auth(dev_token),
                       json={"action": "init"}).json()["ok"] is True

    status = client.get(f"/api/workspace/{pid}/git/status", headers=auth(dev_token)).json()
    assert status["initialized"] is True
    assert any(c["path"] == "main.py" for c in status["changes"])

    assert client.post(f"/api/workspace/{pid}/git", headers=auth(dev_token),
                       json={"action": "add_all"}).json()["ok"] is True
    commit = client.post(f"/api/workspace/{pid}/git", headers=auth(dev_token),
                         json={"action": "commit", "message": "initial commit"}).json()
    assert commit["ok"] is True, commit

    log = client.get(f"/api/workspace/{pid}/git/log", headers=auth(dev_token)).json()
    assert log["commits"][0]["subject"] == "initial commit"

    branches = client.get(f"/api/workspace/{pid}/git/branches", headers=auth(dev_token)).json()
    assert branches["current"] == "main"


def test_git_commit_requires_message(client, dev_token):
    pid = _project(client, dev_token, "Git No Message")
    client.post(f"/api/workspace/{pid}/git", headers=auth(dev_token), json={"action": "init"})
    r = client.post(f"/api/workspace/{pid}/git", headers=auth(dev_token),
                    json={"action": "commit", "message": "   "})
    assert r.status_code == 422


def test_git_rejects_unknown_action(client, dev_token):
    pid = _project(client, dev_token, "Git Bad Action")
    r = client.post(f"/api/workspace/{pid}/git", headers=auth(dev_token),
                    json={"action": "clean -fdx"})
    assert r.status_code == 422


# ------------------------------------------------------------------- platform
def test_languages_reports_installed_runtimes(client, dev_token):
    data = client.get("/api/workspace/languages", headers=auth(dev_token)).json()
    shells = data["shells"]
    assert shells["bash"] is True and shells["node"] is True and shells["npm"] is True
    assert shells["git"] is True and shells["python"] is True
    ids = {l["id"] for l in data["languages"]}
    assert {"python", "javascript", "html", "java", "c", "cpp", "sql"} <= ids


def test_build_without_script_is_honest(client, dev_token):
    pid = _project(client, dev_token, "No Build Project")
    r = client.post(f"/api/workspace/{pid}/build", headers=auth(dev_token)).json()
    assert r["ok"] is False and "No build script" in r["output"]


def test_dev_servers_fail_closed_until_isolated_port_forwarding_exists(client, dev_token):
    pid = _project(client, dev_token, "Server Project", "javascript")
    _write(client, dev_token, pid, "package.json", '{"scripts":{"dev":"node server.js"}}')
    started = client.post(f"/api/workspace/{pid}/servers", headers=auth(dev_token),
                          json={"command": "npm run dev"})
    assert started.status_code == 503
    assert "isolated Docker port forwarding" in started.json()["detail"]
    dev = client.post(f"/api/workspace/{pid}/dev", headers=auth(dev_token))
    assert dev.status_code == 503
    assert "isolated Docker port forwarding" in dev.json()["detail"]


def test_admin_has_no_ide(client, admin_token):
    assert client.post("/api/workspace/anything/terminals",
                       headers=auth(admin_token)).status_code in (403, 404)


def test_unisolated_mode_refuses_code_execution(client, dev_token):
    from unittest.mock import patch
    from app.core.config import settings

    headers = auth(dev_token)
    with patch.object(settings, "ENV", "production"), patch.object(settings, "RUNNER_MODE", "local"):
        result = client.post("/api/workspace/run", headers=headers,
                             json={"language": "python", "code": "print('should not execute')"})
    assert result.status_code == 200
    assert result.json()["ok"] is False
    assert "RUNNER_MODE=docker" in result.json()["stderr"]

