"""Long-running process manager: dev servers and builds inside a project workspace.

Processes are real OS processes started with the project directory as their cwd, so
``npm run dev`` keeps serving after the command returns. Output is captured into a
ring buffer that the UI polls.
"""
from __future__ import annotations

import os
import signal
import socket
import subprocess
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

PORT_RANGE = range(5200, 5400)


@dataclass
class ServerProcess:
    id: str
    project_id: str
    user_id: str
    command: str
    cwd: str
    port: int | None
    kind: str = "dev"
    source: str = "api"
    started_at: float = field(default_factory=time.time)
    proc: subprocess.Popen | None = None
    log: deque = field(default_factory=lambda: deque(maxlen=400))
    status: str = "starting"

    def info(self) -> dict:
        return {
            "id": self.id, "project_id": self.project_id, "command": self.command,
            "port": self.port, "kind": self.kind, "status": self.status,
            "started_at": self.started_at, "uptime": round(time.time() - self.started_at, 1),
            "pid": self.proc.pid if self.proc else None,
            "source": self.source,
            "url": f"http://127.0.0.1:{self.port}/" if self.port else None,
            "log": list(self.log)[-120:],
        }


class ProcessManager:
    def __init__(self) -> None:
        self._items: dict[str, ServerProcess] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ helpers
    def _free_port(self) -> int:
        used = {s.port for s in self._items.values() if s.port}
        for port in PORT_RANGE:
            if port in used:
                continue
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    sock.bind(("127.0.0.1", port))
                    return port
                except OSError:
                    continue
        raise RuntimeError("No free preview port available")

    @staticmethod
    def _env(home: str) -> dict:
        env = {
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "HOME": home, "LANG": "C.UTF-8", "TERM": "dumb",
            "NPM_CONFIG_UPDATE_NOTIFIER": "false", "BROWSER": "none",
            "VITE_CJS_IGNORE_WARNING": "true",
        }
        if os.name == "nt":
            for key in ("SYSTEMROOT", "WINDIR", "PATHEXT", "TEMP", "TMP", "USERPROFILE", "APPDATA", "LOCALAPPDATA"):
                if key in os.environ:
                    env[key] = os.environ[key]
        return env

    def _pump(self, item: ServerProcess) -> None:
        assert item.proc and item.proc.stdout
        for line in item.proc.stdout:
            item.log.append(line.rstrip("\n"))
            if item.status == "starting" and item.port and ("ready" in line.lower() or "localhost" in line.lower()):
                item.status = "running"
        item.status = "stopped" if item.proc.poll() is not None else "exited"

    # ---------------------------------------------------------------- lifecycle
    def start(self, *, project_id: str, user_id: str, cwd: str, command: str,
              port: int | None = None, kind: str = "dev", home: str = "") -> ServerProcess:
        port = port or self._free_port()
        item = ServerProcess(id=uuid.uuid4().hex[:10], project_id=project_id, user_id=user_id,
                             command=command, cwd=cwd, port=port, kind=kind)
        if os.name == "nt":
            shell = os.environ.get("WORKCODE_TERMINAL_SHELL", "powershell.exe")
            argv = [shell, "-NoLogo", "-NoProfile", "-Command", command]
            process_options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        else:
            argv = ["bash", "-lc", command]
            process_options = {"preexec_fn": os.setsid}
        item.proc = subprocess.Popen(
            argv, cwd=cwd, env=self._env(home or cwd),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            bufsize=1, **process_options,
        )
        threading.Thread(target=self._pump, args=(item,), daemon=True).start()
        with self._lock:
            self._items[item.id] = item
        return item

    def list_for(self, project_id: str) -> list[dict]:
        out = []
        for item in self._items.values():
            if item.project_id != project_id:
                continue
            if item.proc and item.proc.poll() is not None and item.status in ("starting", "running"):
                item.status = "exited"
            out.append(item.info())
        return out

    def register_detected(self, *, project_id: str, user_id: str, port: int,
                          command: str, cwd: str, source: str = "terminal") -> ServerProcess:
        """Register a server that was started outside the process manager."""
        key = f"term-{project_id[:8]}-{port}"
        with self._lock:
            existing = self._items.get(key)
            if existing:
                existing.status = "running"
                existing.port = port
                return existing
            item = ServerProcess(id=key, project_id=project_id, user_id=user_id,
                                 command=command, cwd=cwd, port=port, kind="dev")
            item.source = source
            item.status = "running"
            self._items[key] = item
            return item

    def prune_detected(self, listening: set[int]) -> int:
        """Drop terminal-detected entries whose port stopped listening."""
        drop = [sid for sid, item in self._items.items()
                if item.source == "terminal" and (item.port not in listening)]
        for sid in drop:
            self._items.pop(sid, None)
        return len(drop)

    def get(self, server_id: str) -> ServerProcess | None:
        return self._items.get(server_id)

    def stop(self, server_id: str) -> bool:
        item = self._items.pop(server_id, None)
        if not item:
            return False
        if item.proc is None and item.port:
            # detected server: find the owning pid and kill its process group
            try:
                import re as _re
                import subprocess as _sp
                if os.name == "nt":
                    out = _sp.run(["netstat", "-ano", "-p", "tcp"], capture_output=True,
                                  text=True, timeout=5).stdout
                    match = next((m for line in out.splitlines()
                                  if (m := _re.match(r"\s*TCP\s+\S+:(\d+)\s+\S+\s+LISTENING\s+(\d+)", line, _re.I))
                                  and int(m.group(1)) == item.port), None)
                    if match:
                        _sp.run(["taskkill", "/PID", match.group(2), "/T", "/F"],
                                capture_output=True, timeout=5)
                else:
                    out = _sp.run(["ss", "-ltnp"], capture_output=True, text=True, timeout=5).stdout
                    for line in out.splitlines():
                        if f":{item.port} " in line:
                            m = _re.search(r"pid=(\d+)", line)
                            if m:
                                import os as _os
                                import signal as _sig
                                _os.killpg(_os.getpgid(int(m.group(1))), _sig.SIGTERM)
                            break
            except Exception:  # noqa: BLE001
                pass
            item.status = "stopped"
            return True
        if item.proc and item.proc.poll() is None:
            try:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(item.proc.pid), "/T", "/F"],
                                   capture_output=True, timeout=5)
                else:
                    os.killpg(os.getpgid(item.proc.pid), signal.SIGTERM)
                    time.sleep(0.2)
                    os.killpg(os.getpgid(item.proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        item.status = "stopped"
        return True

    def stop_all(self) -> int:
        count = 0
        for sid in list(self._items):
            count += int(self.stop(sid))
        return count


processes = ProcessManager()


def build_project(cwd: str, home: str, timeout: int = 240) -> dict:
    """Build a project inside the same network-isolated Docker boundary as shell runs."""
    pkg = Path(cwd) / "package.json"
    command = None
    if pkg.exists():
        import json
        try:
            scripts = json.loads(pkg.read_text(encoding="utf-8")).get("scripts") or {}
            if "build" in scripts:
                command = "npm run build"
        except (ValueError, OSError):
            pass
    if not command:
        if (Path(cwd) / "Makefile").exists():
            command = "make"
        elif (Path(cwd) / "requirements.txt").exists():
            command = "pip install -r requirements.txt"
        else:
            return {"ok": False, "output": "No build script found (expected package.json scripts.build)",
                    "duration_ms": 0, "dist": False}

    from app.services.runner import run_shell
    result = run_shell(command, cwd, home=home, timeout=timeout)
    output = ((result.get("stdout") or "") + (result.get("stderr") or ""))[-20000:]
    return {"ok": result.get("ok", False), "exit_code": result.get("exit_code"),
            "command": command, "output": output, "duration_ms": result.get("duration_ms", 0),
            "dist": (Path(cwd) / "dist").exists(), "timed_out": result.get("timed_out", False),
            "runner": result.get("runner", "none")}
