"""Detect development servers started from the integrated terminal.

The registry only knew about processes started through the API; a `npm run dev`
typed into the shell was invisible, so Preview kept saying "No dev servers running".
Two independent signals now feed the registry:

1. terminal output — Vite/Next/Angular print ``Local: http://localhost:5173/``.
   The port is probed before it is trusted.
2. a 4 s sweep of ``ss -ltnp`` restricted to processes whose ancestry leads back to
   one of the project's PTY shells (catches servers that print nothing).
"""
from __future__ import annotations

import re
import os
import socket
import subprocess
import threading
import time

from app.services import devserver

URL_RE = re.compile(r"https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\]):(\d{2,5})")
BARE_RE = re.compile(r"(?:Local|Network|Listening|running at|started server)[^\n]{0,40}?:(\d{4,5})", re.I)

_started = False
_lock = threading.Lock()


def _probe(port: int, timeout: float = 0.4) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def _listening() -> dict[int, int]:
    """Return listening TCP ports and owner PIDs using the host's native tool."""
    if os.name == "nt":
        try:
            out = subprocess.run(["netstat", "-ano", "-p", "tcp"], capture_output=True,
                                 text=True, timeout=5).stdout
        except (OSError, subprocess.SubprocessError):
            return {}
        found: dict[int, int] = {}
        for line in out.splitlines():
            match = re.match(r"\s*TCP\s+\S+:(\d+)\s+\S+\s+LISTENING\s+(\d+)", line, re.I)
            if match:
                found[int(match.group(1))] = int(match.group(2))
        return found
    try:
        out = subprocess.run(["ss", "-ltnp"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    found: dict[int, int] = {}
    for line in out.splitlines():
        port = re.search(r":(\d{2,5})\s", line)
        pid = re.search(r"pid=(\d+)", line)
        if port and pid:
            found[int(port.group(1))] = int(pid.group(1))
    return found


def _ancestry(pid: int, depth: int = 14):
    for _ in range(depth):
        yield pid
        try:
            with open(f"/proc/{pid}/stat", encoding="utf-8") as fh:
                pid = int(fh.read().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            return
        if pid <= 1:
            return


def _owned_by(pid: int, shells: set[int]) -> bool:
    return any(a in shells for a in _ancestry(pid))


def feed(text: str, project_id: str, user_id: str, cwd: str, session_id: str) -> None:
    """Called from the PTY reader for every chunk of shell output."""
    if "://" not in text and ":" not in text:
        return
    ports = {int(p) for p in URL_RE.findall(text)}
    ports |= {int(p) for p in BARE_RE.findall(text)}
    for port in ports:
        if port in (8000, 80, 443):
            continue
        if _probe(port):
            devserver.processes.register_detected(
                project_id=project_id, user_id=user_id, port=port,
                command=f"dev server on :{port} (from terminal)", cwd=cwd, source="terminal",
            )


def sweep() -> int:
    """Scan listening ports owned by this project's shells; keep the registry honest."""
    from app.services.terminal import terminals as sessions  # local import: no cycle

    live = [s for s in sessions._sessions.values()
            if s.alive and (s._proc or s._winpty_proc)]  # noqa: SLF001
    if not live:
        return 0
    ports = _listening()
    if os.name == "nt":
        # ConPTY does not expose a process handle in this registry. Ports
        # announced by a terminal are registered by feed(); netstat is enough
        # to remove stale entries on Windows.
        devserver.processes.prune_detected(set(ports))
        return 0
    shell_pids = {s._proc.pid for s in live}
    kept = 0
    for port, pid in ports.items():
        if port in (8000, 80, 443):
            continue
        owner = next((s for s in live if _owned_by(pid, {s._proc.pid})), None)
        if owner:
            devserver.processes.register_detected(
                project_id=owner.project_id, user_id=owner.user_id, port=port,
                command=f"dev server on :{port} (from terminal)", cwd=owner.cwd, source="terminal",
            )
            kept += 1
    devserver.processes.prune_detected(set(ports) & {p for p in ports})
    return kept


def ensure_sweeper() -> None:
    global _started
    with _lock:
        if _started:
            return
        _started = True

    def loop() -> None:
        while True:
            try:
                sweep()
            except Exception:  # noqa: BLE001 - a watcher must never kill the app
                pass
            time.sleep(4)

    threading.Thread(target=loop, name="dev-server-sweeper", daemon=True).start()
