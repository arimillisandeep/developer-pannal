"""Unit coverage for Docker discovery, image preparation, and fail-closed runs."""
from pathlib import Path
import subprocess

from app.services import runner


def test_finds_per_user_docker_desktop_install(tmp_path, monkeypatch):
    binary = tmp_path / "Programs" / "DockerDesktop" / "resources" / "bin" / "docker.exe"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"docker")
    monkeypatch.setattr(runner.shutil, "which", lambda _: None)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("ProgramFiles", raising=False)
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)

    assert runner._find_docker_cli() == binary.resolve()


def test_runner_image_build_is_cached_and_uses_project_dockerfile(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(runner, "DOCKER_CLI", Path("C:/Docker/docker.exe"))
    monkeypatch.setattr(runner, "_docker_engine_ready", lambda: (True, ""))

    def fake_run(argv, **kwargs):
        calls.append(argv)
        if "inspect" in argv:
            inspect_calls = sum("inspect" in call for call in calls)
            return subprocess.CompletedProcess(argv, 1 if inspect_calls == 1 else 0,
                                               stdout="" if inspect_calls == 1 else "image-id", stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="built", stderr="")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    dockerfile = tmp_path / "docker" / "runner" / "Dockerfile.runtime"
    dockerfile.parent.mkdir(parents=True)
    dockerfile.write_text("FROM scratch\n", encoding="utf-8")

    assert runner._ensure_local_image("workcode-runner-test:latest", dockerfile, ["RUNNER_BASE_IMAGE=python:3.12-alpine"])[0]
    assert runner._ensure_local_image("workcode-runner-test:latest", dockerfile)[0]
    assert len(calls) == 3
    build = next(call for call in calls if call[1] == "build")
    assert "--build-arg" in build
    assert "RUNNER_BASE_IMAGE=python:3.12-alpine" in build
    assert build[0].endswith("docker.exe")


def test_run_never_attempts_remote_pull_or_host_fallback(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(runner, "_runner_image", lambda language: (True, f"workcode-runner-{language}:latest"))

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="isolated-ok", stderr="")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    source = tmp_path / "main.py"
    source.write_text("print('isolated-ok')", encoding="utf-8")
    result = runner._docker_run("python", source, "main.py", tmp_path)

    assert result["ok"] is True
    assert calls and calls[0][0] == str(runner.DOCKER_CLI or "docker")
    assert "--pull=never" in calls[0]
    assert calls[0][calls[0].index("--network") + 1] == "none"
    assert "--read-only" in calls[0]


def test_missing_local_image_error_does_not_attempt_execution(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(runner, "_runner_image", lambda language: (False, "runner image build failed"))
    monkeypatch.setattr(runner.subprocess, "run", lambda *args, **kwargs: calls.append(args))
    source = tmp_path / "main.py"
    source.write_text("print('must not run')", encoding="utf-8")

    result = runner._docker_run("python", source, "main.py", tmp_path)

    assert result["ok"] is False
    assert "runner image build failed" in result["stderr"]
    assert calls == []
