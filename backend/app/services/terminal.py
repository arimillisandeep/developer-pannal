"""Persistent PTY terminal sessions.

One real pseudo-terminal per terminal tab, running bash (or docker exec into the
project's container) with its working directory fixed to the project's workspace.

Sessions survive disconnects and are destroyed when the user kills them,
they exit, or the idle reaper collects them.
"""

from __future__ import annotations

import asyncio
import os
import struct
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field

try:
    import fcntl
    import pty
    import select
except ImportError:
    fcntl = None
    pty = None
    select = None

import signal

try:
    from winpty import PtyProcess as WinPtyProcess
except ImportError:
    WinPtyProcess = None


MAX_SCROLLBACK = 400_000
IDLE_TTL_SECONDS = 2 * 60 * 60
MAX_TERMINALS_PER_PROJECT = 6


@dataclass
class TerminalSession:
    id: str
    user_id: str
    project_id: str
    cwd: str
    shell: str
    docker_container: str | None
    home: str = ""
    cols: int = 120
    rows: int = 30
    created_at: float = field(default_factory=time.time)
    alive: bool = True
    exit_code: int | None = None
    scrollback: bytearray = field(default_factory=bytearray)
    last_activity: float = field(default_factory=time.time)

    _master: int = -1
    _proc: subprocess.Popen | None = None
    _winpty_proc: object | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _subs: list = field(default_factory=list)
    _ready: threading.Event = field(default_factory=threading.Event)

    def start(self) -> None:
        venv_root = os.path.join(self.cwd, ".venv")
        python_bin_dir = os.path.join(
            venv_root, "Scripts" if os.name == "nt" else "bin"
        )

        python_executable = os.path.join(
            python_bin_dir,
            "python.exe" if os.name == "nt" else "python",
        )

        if not self.docker_container and not os.path.isfile(python_executable):
            try:
                created = subprocess.run(
                    [sys.executable, "-m", "venv", venv_root],
                    capture_output=True,
                    text=True,
                    timeout=90,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                raise RuntimeError(
                    f"Could not create the project Python environment: {exc}"
                ) from exc

            if created.returncode != 0:
                detail = (
                    created.stderr
                    or created.stdout
                    or "venv creation failed"
                ).strip()

                raise RuntimeError(
                    f"Could not create the project Python environment: "
                    f"{detail[-1000:]}"
                )

        path_prefix = (
            python_bin_dir if os.path.isdir(python_bin_dir) else ""
        )

        env = {
            "PATH": os.pathsep.join(
                filter(
                    None,
                    (path_prefix, os.environ.get("PATH", "")),
                )
            ),
            "HOME": self.home or self.cwd,
            "VIRTUAL_ENV": (
                venv_root if os.path.isdir(venv_root) else ""
            ),
            "TERM": "xterm-256color",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "SHELL": self.shell,
            "PROJECT_ROOT": self.cwd,
            "NPM_CONFIG_UPDATE_NOTIFIER": "false",
            "CI": "0",
            "FORCE_COLOR": "1",
        }

        if os.name == "nt":
            for key in (
                "SYSTEMROOT",
                "WINDIR",
                "PATHEXT",
                "TEMP",
                "TMP",
                "USERPROFILE",
                "APPDATA",
                "LOCALAPPDATA",
                "COMSPEC",
            ):
                value = os.environ.get(key)
                if value:
                    env[key] = value

            env["USERPROFILE"] = os.environ.get(
                "USERPROFILE", env["HOME"]
            )

        # Docker-backed terminal
        if self.docker_container:
            from app.services import runner

            argv = [
                *runner._docker_argv(
                    "exec",
                    "-it",
                    "-w",
                    "/work",
                ),
                self.docker_container,
                "bash",
                "-l",
            ]

            env["PATH"] = runner._docker_env().get(
                "PATH", env["PATH"]
            )

        elif os.name == "nt":
            shell_name = os.path.basename(self.shell).lower()

            argv = (
                [self.shell, "-NoLogo", "-NoExit"]
                if "powershell" in shell_name
                or shell_name == "pwsh.exe"
                else [self.shell]
            )

        else:
            argv = [self.shell, "-l"]

        # Windows ConPTY
        if os.name == "nt":
            if WinPtyProcess is None:
                raise RuntimeError(
                    "Windows terminal requires pywinpty. "
                    "Install it with: python -m pip install pywinpty"
                )

            # IMPORTANT:
            # Pass argv as a list. Do not convert it into a command string.
            # This prevents Windows from splitting Docker paths containing spaces.
            self._winpty_proc = WinPtyProcess.spawn(
                argv,
                cwd=self.cwd,
                env=env,
                dimensions=(self.rows, self.cols),
            )

            threading.Thread(
                target=self._pump_windows,
                name=f"winpty-{self.id[:6]}",
                daemon=True,
            ).start()

            return

        # Linux/macOS native PTY
        if pty is None:
            raise RuntimeError(
                "Unix PTY is not available on this platform"
            )

        self._master, slave = pty.openpty()

        def _become_session_leader():
            if hasattr(os, "setsid"):
                os.setsid()

            if fcntl is not None:
                try:
                    fcntl.ioctl(0, 0x540E, 0)
                except OSError:
                    pass

        try:
            self._proc = subprocess.Popen(
                argv,
                stdin=slave,
                stdout=slave,
                stderr=slave,
                cwd=self.cwd,
                env=env,
                close_fds=True,
                **(
                    {"preexec_fn": _become_session_leader}
                    if hasattr(os, "setsid")
                    else {}
                ),
            )
        finally:
            os.close(slave)

        self.resize(self.cols, self.rows)

        threading.Thread(
            target=self._pump,
            name=f"pty-{self.id[:6]}",
            daemon=True,
        ).start()

    def _pump(self) -> None:
        """Read master fd and send output to subscribers."""

        if select is None:
            self.alive = False
            return

        while True:
            try:
                ready, _, _ = select.select(
                    [self._master], [], [], 0.4
                )
            except (OSError, ValueError):
                break

            if not ready:
                if self._proc and self._proc.poll() is not None:
                    break
                continue

            try:
                chunk = os.read(self._master, 65536)
            except OSError:
                break

            if not chunk:
                break

            self._record_output(chunk)

        code = self._proc.poll() if self._proc else None

        self.alive = False
        self.exit_code = code

        self._broadcast(
            "\r\n\x1b[90m[process exited]\x1b[0m\r\n"
        )

        self._signal_subscribers(None)

        try:
            os.close(self._master)
        except OSError:
            pass

    def _record_output(self, chunk: bytes) -> None:
        """Store output and detect development servers."""

        self.last_activity = time.time()

        self.scrollback.extend(chunk)

        if len(self.scrollback) > MAX_SCROLLBACK:
            del self.scrollback[
                : len(self.scrollback) - MAX_SCROLLBACK
            ]

        text = chunk.decode("utf-8", "replace")

        if (
            os.name != "nt"
            or "> " in bytes(
                self.scrollback[-512:]
            ).decode("utf-8", "replace")
        ):
            self._ready.set()

        self._broadcast(text)

        try:
            from app.services import server_detect

            server_detect.feed(
                text,
                self.project_id,
                self.user_id,
                self.cwd,
                self.id,
            )
        except Exception:
            pass

    def _pump_windows(self) -> None:
        """Read output from the Windows ConPTY process."""

        process = self._winpty_proc

        try:
            while process is not None:
                try:
                    chunk = process.read(65536)
                except (EOFError, OSError, ValueError):
                    break

                if not chunk:
                    try:
                        if not process.isalive():
                            break
                    except Exception:
                        break

                    continue

                raw = (
                    chunk
                    if isinstance(chunk, bytes)
                    else chunk.encode("utf-8", "replace")
                )

                self._record_output(raw)

                try:
                    if not process.isalive():
                        break
                except Exception:
                    break

        finally:
            code = None

            try:
                getter = getattr(
                    process, "get_exitstatus", None
                )

                if callable(getter):
                    code = getter()

            except Exception:
                pass

            self.alive = False
            self.exit_code = code

            self._broadcast(
                "\r\n\x1b[90m[process exited]\x1b[0m\r\n"
            )

            self._signal_subscribers(None)

    def _broadcast(self, text: str) -> None:
        for loop, queue in list(self._subs):
            try:
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    {"type": "output", "data": text},
                )
            except RuntimeError:
                self._subs.remove((loop, queue))

    def _signal_subscribers(self, payload) -> None:
        for loop, queue in list(self._subs):
            try:
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    payload,
                )
            except RuntimeError:
                pass

    def write(self, data: str) -> None:
        if not self.alive:
            return

        try:
            if os.name == "nt":
                if self._winpty_proc is not None:
                    self._winpty_proc.write(data)

            elif self._master >= 0:
                os.write(
                    self._master,
                    data.encode("utf-8"),
                )

            self.last_activity = time.time()

        except (OSError, EOFError, ValueError):
            self.alive = False

    def resize(self, cols: int, rows: int) -> None:
        self.cols = max(20, int(cols))
        self.rows = max(5, int(rows))

        try:
            if os.name == "nt":
                if self._winpty_proc is not None:
                    self._winpty_proc.setwinsize(
                        self.rows,
                        self.cols,
                    )

            elif fcntl is not None and self._master >= 0:
                fcntl.ioctl(
                    self._master,
                    0x5414,
                    struct.pack(
                        "HHHH",
                        self.rows,
                        self.cols,
                        0,
                        0,
                    ),
                )

        except (OSError, ValueError):
            pass

    def kill(self) -> None:
        if os.name == "nt":
            process = self._winpty_proc

            if process is not None:
                try:
                    process.close(force=True)
                except Exception:
                    try:
                        process.terminate(force=True)
                    except Exception:
                        pass

        elif self._proc and self._proc.poll() is None:
            try:
                if (
                    hasattr(os, "killpg")
                    and hasattr(os, "getpgid")
                ):
                    os.killpg(
                        os.getpgid(self._proc.pid),
                        signal.SIGTERM,
                    )

                    time.sleep(0.15)

                    try:
                        os.killpg(
                            os.getpgid(self._proc.pid),
                            signal.SIGKILL,
                        )
                    except ProcessLookupError:
                        pass

                else:
                    self._proc.terminate()

            except (
                ProcessLookupError,
                PermissionError,
                AttributeError,
                OSError,
            ):
                try:
                    self._proc.terminate()
                except OSError:
                    pass

        if self.docker_container:
            try:
                from app.services import runner

                subprocess.run(
                    runner._docker_argv(
                        "rm",
                        "-f",
                        self.docker_container,
                    ),
                    capture_output=True,
                    timeout=10,
                    check=False,
                    env=runner._docker_env(),
                )

            except (OSError, subprocess.SubprocessError):
                pass

        self.alive = False
        self._signal_subscribers(None)

    def attach(self) -> tuple[asyncio.Queue, str]:
        loop = asyncio.get_running_loop()

        queue = asyncio.Queue(maxsize=2000)

        self._subs.append((loop, queue))
        self.last_activity = time.time()

        replay = bytes(
            self.scrollback
        ).decode("utf-8", "replace")

        return queue, replay

    def detach(self, queue: asyncio.Queue) -> None:
        self._subs = [
            (loop, q)
            for loop, q in self._subs
            if q is not queue
        ]

    def info(self) -> dict:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "cwd": self.cwd,
            "alive": self.alive,
            "exit_code": self.exit_code,
            "cols": self.cols,
            "rows": self.rows,
            "created_at": self.created_at,
            "last_activity": self.last_activity,
            "subscribers": len(self._subs),
            "docker_container": self.docker_container,
        }


class TerminalRegistry:

    def __init__(self) -> None:
        self._sessions: dict[str, TerminalSession] = {}
        self._lock = threading.Lock()

    def count_for_project(self, project_id: str) -> int:
        return sum(
            1
            for session in self._sessions.values()
            if session.project_id == project_id
        )

    def create(
        self,
        *,
        user_id: str,
        project_id: str,
        cwd: str,
        home: str,
        docker_container: str | None = None,
    ) -> TerminalSession:

        if (
            self.count_for_project(project_id)
            >= MAX_TERMINALS_PER_PROJECT
        ):
            raise RuntimeError(
                f"At most {MAX_TERMINALS_PER_PROJECT} terminals per project"
            )

        os.makedirs(cwd, exist_ok=True)
        os.makedirs(home, exist_ok=True)

        configured_shell = os.environ.get(
            "WORKCODE_TERMINAL_SHELL"
        )

        if os.name == "nt":
            shell = (
                configured_shell
                or os.environ.get(
                    "WORKCODE_POWERSHELL",
                    "powershell.exe",
                )
            )
        else:
            shell = (
                configured_shell
                or os.environ.get("SHELL", "/bin/bash")
            )

        session = TerminalSession(
            id=uuid.uuid4().hex[:12],
            user_id=user_id,
            project_id=project_id,
            cwd=cwd,
            home=home,
            shell="/bin/bash" if docker_container else shell,
            docker_container=docker_container,
        )

        session.start()

        # Wait for the first prompt/output.
        session._ready.wait(timeout=5)

        with self._lock:
            self._sessions[session.id] = session

        try:
            from app.services import server_detect

            server_detect.ensure_sweeper()

        except Exception:
            pass

        return session

    def get(self, session_id: str) -> TerminalSession | None:
        return self._sessions.get(session_id)

    def list_for_project(self, project_id: str) -> list[dict]:
        return [
            session.info()
            for session in self._sessions.values()
            if session.project_id == project_id
        ]

    def kill(self, session_id: str) -> bool:
        session = self._sessions.pop(session_id, None)

        if not session:
            return False

        session.kill()
        return True

    def reap(self) -> int:
        now = time.time()

        dead = [
            session_id
            for session_id, session in self._sessions.items()
            if (
                not session.alive
                and session.subscribers == 0
            )
            or (
                now - session.last_activity
                > IDLE_TTL_SECONDS
            )
        ]

        for session_id in dead:
            self.kill(session_id)

        return len(dead)


terminals = TerminalRegistry()