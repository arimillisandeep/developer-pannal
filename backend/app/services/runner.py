"""Isolated code execution.

Untrusted code runs only in a throw-away Docker container with no network,
resource limits, a read-only root, dropped capabilities, and a non-root UID.
Local subprocess execution is disabled because it cannot isolate host files or
network access across supported platforms.
"""
import os
import re
import threading
import shutil
import shlex
import subprocess
import tempfile
import time
import uuid

from pathlib import Path

from app.core.config import settings
from app.services.storage import run_dir

# language -> (extension, monaco id, local interpreter, docker image)
LANGUAGES: dict[str, dict] = {
    "python":     {"ext": "py",   "monaco": "python",     "bin": "python3", "image": "python:3.12-alpine", "cmd": ["python3", "{file}"]},
    "javascript": {"ext": "js",   "monaco": "javascript", "bin": "node",    "image": "node:22-alpine",     "cmd": ["node", "{file}"]},
    "typescript": {"ext": "ts",   "monaco": "typescript", "bin": "node",    "image": "node:22-alpine",     "cmd": ["node", "--experimental-strip-types", "{file}"]},
    "node":       {"ext": "js",   "monaco": "javascript", "bin": "node",    "image": "node:22-alpine",     "cmd": ["node", "{file}"]},
    "express":    {"ext": "js",   "monaco": "javascript", "bin": "node",    "image": "node:22-alpine",     "cmd": ["node", "{file}"]},
    "c":          {"ext": "c",    "monaco": "c",          "bin": "gcc",     "image": "gcc:14",             "cmd": ["sh", "-c", "gcc -O2 -o prog {file} && ./prog"]},
    "cpp":        {"ext": "cpp",  "monaco": "cpp",        "bin": "g++",     "image": "gcc:14",             "cmd": ["sh", "-c", "g++ -O2 -o prog {file} && ./prog"]},
    "java":       {"ext": "java", "monaco": "java",       "bin": "javac",   "image": "eclipse-temurin:21-jdk", "cmd": ["sh", "-c", "javac {file} && java {class}"]},
    "go":         {"ext": "go",   "monaco": "go",         "bin": "go",      "image": "golang:1.23-alpine", "cmd": ["sh", "-c", "mkdir -p /work/.gotmp && GOTMPDIR=/work/.gotmp go build -p 1 -o /work/prog {file} && /work/prog"]},
    "ruby":       {"ext": "rb",   "monaco": "ruby",       "bin": "ruby",    "image": "ruby:3.3-alpine",    "cmd": ["ruby", "{file}"]},
    "php":        {"ext": "php",  "monaco": "php",        "bin": "php",     "image": "php:8.3-cli-alpine", "cmd": ["php", "{file}"]},
    "rust":       {"ext": "rs",   "monaco": "rust",       "bin": "rustc",   "image": "rust:1.82-alpine",   "cmd": ["sh", "-c", "rustc -o prog {file} && ./prog"]},
    "csharp":     {"ext": "cs",   "monaco": "csharp",     "bin": "dotnet",  "image": "mcr.microsoft.com/dotnet/sdk:8.0", "cmd": ["sh", "-c", "REF_DIR=$(find /usr/share/dotnet/packs/Microsoft.NETCore.App.Ref -path '*/ref/net8.0' -type d | head -n 1); CSC=$(find /usr/share/dotnet/sdk -path '*/Roslyn/bincore/csc.dll' -type f | head -n 1); set --; for ref in \"$REF_DIR\"/*.dll; do set -- \"$@\" \"-r:$ref\"; done; dotnet \"$CSC\" -noconfig -nostdlib -nologo -target:exe -out:app.dll \"$@\" {file} && printf '%s' '{\"runtimeOptions\":{\"tfm\":\"net8.0\",\"framework\":{\"name\":\"Microsoft.NETCore.App\",\"version\":\"8.0.0\"}}}' > app.runtimeconfig.json && dotnet app.dll"]},
    "sql":        {"ext": "sql",  "monaco": "sql",        "bin": "sqlite3", "image": "python:3.12-alpine", "cmd": []},
    "shell":      {"ext": "sh",   "monaco": "shell",      "bin": "bash",    "image": "alpine:3.20",        "cmd": ["sh", "{file}"]},
    "powershell": {"ext": "ps1",  "monaco": "powershell", "bin": "pwsh",    "image": "mcr.microsoft.com/powershell:lts-alpine", "cmd": ["pwsh", "-File", "{file}"]},
    "kotlin":     {"ext": "kt",   "monaco": "kotlin",     "bin": "kotlinc", "image": "zenika/kotlin",      "cmd": ["sh", "-c", "kotlinc {file} -include-runtime -d app.jar && java -jar app.jar"]},
    "swift":      {"ext": "swift","monaco": "swift",      "bin": "swift",   "image": "swift:5.10",         "cmd": ["swift", "{file}"]},
    # Static / browser-only stacks run through Live Preview instead of a runtime.
    "html":       {"ext": "html", "monaco": "html",       "bin": None, "image": None, "cmd": []},
    "css":        {"ext": "css",  "monaco": "css",        "bin": None, "image": None, "cmd": []},
    "react":      {"ext": "jsx",  "monaco": "javascript", "bin": None, "image": None, "cmd": []},
    "angular":    {"ext": "ts",   "monaco": "typescript", "bin": None, "image": None, "cmd": []},
    "vue":        {"ext": "vue",  "monaco": "html",       "bin": None, "image": None, "cmd": []},
    "text":       {"ext": "txt",  "monaco": "plaintext",  "bin": None, "image": None, "cmd": []},
}

PREVIEW_LANGUAGES = {"html", "css", "react", "angular", "vue"}
MAX_OUTPUT = 200_000


def _find_docker_cli() -> Path | None:
    """Resolve Docker once from PATH or supported Desktop install locations."""
    candidates: list[Path] = []
    found = shutil.which("docker")
    if found:
        candidates.append(Path(found))
    local_app_data = os.environ.get("LOCALAPPDATA")
    program_files = os.environ.get("ProgramFiles")
    program_files_x86 = os.environ.get("ProgramFiles(x86)")
    if local_app_data:
        candidates.extend([
            Path(local_app_data) / "Programs" / "DockerDesktop" / "resources" / "bin" / "docker.exe",
            Path(local_app_data) / "Docker" / "Docker" / "resources" / "bin" / "docker.exe",
        ])
    for root in (program_files, program_files_x86):
        if root:
            candidates.extend([
                Path(root) / "Docker" / "Docker" / "resources" / "bin" / "docker.exe",
                Path(root) / "Docker" / "Docker" / "resources" / "docker.exe",
            ])
    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate.resolve()
        except OSError:
            continue
    return None


# Resolve once when the backend imports the runner. The executable directory is
# added only to child-process PATH so Docker Desktop's credential helper works.
DOCKER_CLI = _find_docker_cli()
_IMAGE_LOCKS: dict[str, threading.Lock] = {}
_IMAGE_LOCKS_GUARD = threading.Lock()
_ENGINE_STATUS_LOCK = threading.Lock()
_ENGINE_STATUS: tuple[float, str | None] = (0.0, None)


def _docker_env() -> dict[str, str]:
    env = os.environ.copy()
    if DOCKER_CLI:
        cli_dir = str(DOCKER_CLI.parent)
        env["PATH"] = cli_dir + os.pathsep + env.get("PATH", "")
    return env


def _docker_argv(*args: str) -> list[str]:
    return [str(DOCKER_CLI or "docker"), *args]


def _docker_error_message(stderr: str, action: str = "Docker") -> str:
    text = (stderr or "").strip()
    lower = text.lower()
    if DOCKER_CLI is None:
        return ("Docker Desktop CLI was not found. Install or start Docker Desktop, "
                "then restart WORKCODE. No host execution fallback was attempted.")
    if any(marker in lower for marker in ("error getting credentials", "credential helper", "0xc000013a")):
        return ("Docker Desktop could not access its credential helper. Confirm Docker Desktop "
                "is running and its bundled docker-credential-desktop.exe is present beside "
                "docker.exe, then restart Docker Desktop and WORKCODE. WORKCODE did not alter "
                "Docker credentials. If this persists, use Docker Desktop's Repair option.")
    if any(marker in lower for marker in ("cannot connect to the docker daemon", "is the docker daemon running", "error during connect")):
        return "Docker Engine is unavailable. Start Docker Desktop and wait for the engine to become ready."
    if not text:
        return f"{action} failed without diagnostic output. Check Docker Desktop status and backend logs."
    # Do not return arbitrary daemon output that could contain configuration data.
    return f"{action} failed: {text[:1200]}"


def _docker_engine_ready() -> tuple[bool, str]:
    """Check CLI and engine with bounded calls, briefly caching the result."""
    global _ENGINE_STATUS
    now = time.monotonic()
    with _ENGINE_STATUS_LOCK:
        checked_at, cached_error = _ENGINE_STATUS
        if now - checked_at < (5 if cached_error else 30):
            return cached_error is None, cached_error or ""
        if DOCKER_CLI is None:
            error = _docker_error_message("")
            _ENGINE_STATUS = (now, error)
            return False, error
        try:
            version = subprocess.run(_docker_argv("--version"), capture_output=True, text=True,
                                     timeout=5, check=False, env=_docker_env())
            if version.returncode:
                error = _docker_error_message(version.stderr or version.stdout, "Docker CLI check")
            else:
                info = subprocess.run(_docker_argv("info", "--format", "{{.ServerVersion}}"),
                                      capture_output=True, text=True, timeout=8, check=False,
                                      env=_docker_env())
                error = None if info.returncode == 0 and info.stdout.strip() else _docker_error_message(
                    info.stderr or info.stdout, "Docker Engine check")
        except (OSError, subprocess.SubprocessError) as exc:
            error = _docker_error_message(str(exc), "Docker Engine check")
        _ENGINE_STATUS = (time.monotonic(), error)
        return error is None, error or ""


def _ensure_local_image(image: str, dockerfile: Path, build_args: list[str] | None = None) -> tuple[bool, str]:
    """Build a missing local image once; never let `docker run` pull implicitly."""
    with _IMAGE_LOCKS_GUARD:
        lock = _IMAGE_LOCKS.setdefault(image, threading.Lock())
    with lock:
        ready, diagnostic = _docker_engine_ready()
        if not ready:
            return False, diagnostic
        try:
            inspected = subprocess.run(_docker_argv("image", "inspect", image), capture_output=True,
                                       text=True, timeout=10, check=False, env=_docker_env())
        except (OSError, subprocess.SubprocessError) as exc:
            return False, _docker_error_message(str(exc), "Docker image check")
        if inspected.returncode == 0:
            return True, image

        if not dockerfile.is_file():
            return False, f"Dockerfile is missing: {dockerfile.name}. Restore the project docker/runner files."
        try:
            built = subprocess.run(
                _docker_argv("build", "-f", str(dockerfile),
                             *[arg for value in (build_args or []) for arg in ("--build-arg", value)],
                             "-t", image,
                             str(dockerfile.parents[2])),
                capture_output=True, text=True, timeout=600, check=False, env=_docker_env(),
            )
        except subprocess.TimeoutExpired:
            return False, f"Building {image} exceeded 10 minutes and was stopped. Check Docker Desktop and retry."
        except OSError as exc:
            return False, _docker_error_message(str(exc), f"Building {image}")
        if built.returncode:
            return False, _docker_error_message(built.stderr or built.stdout, f"Building {image}")
        return True, image


def _runner_image(language: str) -> tuple[bool, str]:
    """Build and cache a language image from the shared local runner Dockerfile."""
    image = f"{settings.DOCKER_IMAGE_PREFIX}-{language}:latest"
    dockerfile = Path(__file__).resolve().parents[3] / "docker" / "runner" / "Dockerfile.runtime"
    return _ensure_local_image(image, dockerfile, [
        f"RUNNER_BASE_IMAGE={LANGUAGES[language]['image']}", f"RUNNER_LANGUAGE={language}",
    ])


def ensure_terminal_image() -> tuple[bool, str]:
    """Ensure the existing interactive terminal image is built locally."""
    dockerfile = Path(__file__).resolve().parents[3] / "docker" / "runner" / "Dockerfile.terminal"
    return _ensure_local_image(settings.DOCKER_TERMINAL_IMAGE, dockerfile)


def list_languages() -> list[dict]:
    out = []
    for name, meta in LANGUAGES.items():
        available = True
        if meta["bin"]:
            candidates = {"python": ("python", "python3", "py"), "shell": ("bash", "sh")}.get(name, (meta["bin"],))
            available = any(shutil.which(binary) for binary in candidates)
        if name == "sql":
            available = True  # handled in-process by sqlite3
        out.append({
            "id": name,
            "extension": meta["ext"],
            "monaco": meta["monaco"],
            "runnable": bool(meta["cmd"]) or name == "sql",
            "preview": name in PREVIEW_LANGUAGES,
            "available_locally": available,
            "docker_image": meta["image"],
        })
    return sorted(out, key=lambda x: x["id"])


def _truncate(text: str) -> tuple[str, bool]:
    if len(text) > MAX_OUTPUT:
        return text[:MAX_OUTPUT] + "\n...[output truncated]", True
    return text, False


def _java_filename(code: str, fallback: str = "Main.java") -> str:
    """Match javac's source filename to its declared top-level entry type."""
    match = re.search(
        r"(?m)^\s*(?:public\s+)?(?:(?:abstract|final|sealed|static)\s+)*(?:class|record|interface|enum)\s+([A-Za-z_$][\w$]*)",
        code,
    )
    return f"{match.group(1)}.java" if match else fallback


def _run_sql(code: str) -> dict:
    """SQL runs against a throw-away in-memory SQLite database."""
    import sqlite3

    started = time.time()
    try:
        con = sqlite3.connect(":memory:")
        cur = con.cursor()
        rows, headers, statements = [], [], 0
        for stmt in [s.strip() for s in code.split(";") if s.strip()]:
            cur.execute(stmt)
            statements += 1
            if cur.description:
                headers = [d[0] for d in cur.description]
                rows = cur.fetchall()
        con.close()
        stdout = ""
        if headers:
            stdout += " | ".join(headers) + "\n" + "-" * 40 + "\n"
            stdout += "\n".join(" | ".join(str(c) for c in r) for r in rows)
        stdout += f"\n\nOK — {statements} statement(s), {len(rows)} row(s)."
        return {"ok": True, "exit_code": 0, "stdout": stdout, "stderr": "",
                "duration_ms": int((time.time() - started) * 1000), "runner": "sqlite", "timed_out": False}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "exit_code": 1, "stdout": "", "stderr": f"SQL error: {exc}",
                "duration_ms": int((time.time() - started) * 1000), "runner": "sqlite", "timed_out": False}


def docker_mount_args(path: str | Path, target: str) -> list[str]:
    """Mount exactly one storage subtree, including from a Compose named volume."""
    source = Path(path).resolve()
    volume = settings.DOCKER_STORAGE_VOLUME.strip()
    if volume:
        root = Path(settings.STORAGE_ROOT).resolve()
        try:
            relative = source.relative_to(root).as_posix()
        except ValueError as exc:
            raise ValueError("Docker storage mounts must stay under STORAGE_ROOT") from exc
        if relative in ("", "."):
            raise ValueError("Refusing to mount the entire storage volume")
        return ["--mount", f"type=volume,source={volume},target={target},volume-subpath={relative}"]
    return ["-v", f"{source}:{target}:rw"]


def _docker_user() -> str:
    """Match the non-root service UID for isolated bind mounts; never run as root."""
    if os.name != "nt" and hasattr(os, "geteuid") and os.geteuid() > 0:
        return f"{os.geteuid()}:{os.getegid()}"
    return "65534:65534"


def _docker_run(language: str, source: Path, filename: str, workdir: Path, stdin: str = "") -> dict:
    meta = LANGUAGES[language]
    image = f"{settings.DOCKER_IMAGE_PREFIX}-{language}"
    available, diagnostic = _runner_image(language)
    if not available:
        return {"ok": False, "exit_code": None, "stdout": "", "stderr": diagnostic,
                "duration_ms": 0, "runner": "docker", "timed_out": False}
    replacements = {"{file}": filename, "{class}": Path(filename).stem}
    inner = " ".join(shlex.quote(part.replace("{file}", replacements["{file}"])
                                 .replace("{class}", replacements["{class}"]))
                     for part in meta["cmd"])
    container_name = f"wc-run-{uuid.uuid4().hex[:12]}"
    docker_timeout = settings.RUN_TIMEOUT_SECONDS
    docker_memory_mb = settings.RUN_MEMORY_MB
    if language == "kotlin":
        docker_timeout = max(docker_timeout, 90)
        docker_memory_mb = max(docker_memory_mb, 768)
    elif language == "go":
        docker_timeout = max(docker_timeout, 30)
    elif language == "swift":
        docker_timeout = max(docker_timeout, 30)
    docker_cmd = [
        *_docker_argv("run", "--pull=never", "--rm", "--name", container_name, "-i", "--network", "none"),
        "--memory", f"{docker_memory_mb}m", "--memory-swap", f"{docker_memory_mb}m",
        "--cpus", "0.5", "--pids-limit", str(settings.RUN_MAX_PROCESSES),
        "--read-only", "--tmpfs", "/tmp:rw,size=64m",
        "-e", "HOME=/tmp", "-e", "XDG_CACHE_HOME=/tmp/.cache", "-e", "TMPDIR=/tmp",
        "-e", "CLANG_MODULE_CACHE_PATH=/tmp/.cache/clang", "-e", "SWIFT_MODULECACHE_PATH=/tmp/.cache/swift",
        "-e", "GOMAXPROCS=1", "-e", "GOCACHE=/tmp/go-cache",
        "-e", "GOPATH=/tmp/go",
        *( ["-e", "JAVA_OPTS=-Xms16m -Xmx512m -XX:ActiveProcessorCount=1"] if language == "kotlin" else [] ),
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--user", _docker_user(),
        *docker_mount_args(workdir, "/work"), "-w", "/work",
        image, "sh", "-c", inner,
    ]
    started = time.time()
    try:
        proc = subprocess.run(docker_cmd, input=stdin or "", capture_output=True, text=True,
                              timeout=docker_timeout + 10, env=_docker_env())
        out, t1 = _truncate(proc.stdout or "")
        err, t2 = _truncate(proc.stderr or "")
        if proc.returncode and language in {"c", "cpp", "java", "rust", "kotlin", "csharp"}:
            if any(marker in err.lower() for marker in ("error:", "error cs", "fatal error:", "compilation failed", "build failed")):
                err = "Compilation failed:\n" + err
        return {"ok": proc.returncode == 0, "exit_code": proc.returncode, "stdout": out,
                "stderr": err, "duration_ms": int((time.time() - started) * 1000),
                "runner": "docker", "timed_out": False, "truncated": t1 or t2}
    except subprocess.TimeoutExpired:
        try:
            subprocess.run(_docker_argv("rm", "-f", container_name), capture_output=True,
                           timeout=10, check=False, env=_docker_env())
        except (OSError, subprocess.SubprocessError):
            pass
        return {"ok": False, "exit_code": None, "stdout": "", "stderr": "Container killed: timeout",
                "duration_ms": int((time.time() - started) * 1000), "runner": "docker", "timed_out": True}
    except FileNotFoundError:
        return {"ok": False, "exit_code": None, "stdout": "",
                "stderr": _docker_error_message(""),
                "duration_ms": int((time.time() - started) * 1000), "runner": "none", "timed_out": False}
    except OSError as exc:
        return {"ok": False, "exit_code": None, "stdout": "",
                "stderr": f"Could not start Docker runner: {exc}",
                "duration_ms": int((time.time() - started) * 1000), "runner": "none", "timed_out": False}


def execute(language: str, code: str, filename: str | None = None, stdin: str = "",
            files: dict[str, str] | None = None) -> dict:
    """Run `code` (plus optional extra project files) in an isolated workspace."""
    lang = (language or "python").lower()
    meta = LANGUAGES.get(lang)
    if not meta:
        return {"ok": False, "exit_code": None, "stdout": "",
                "stderr": f"Unsupported language: {language}", "duration_ms": 0,
                "runner": "none", "timed_out": False}

    if lang in PREVIEW_LANGUAGES or not meta["cmd"]:
        return {"ok": True, "exit_code": 0,
                "stdout": "This stack runs in the browser — open Live Preview to see it render.",
                "stderr": "", "duration_ms": 0, "runner": "preview", "timed_out": False}

    if lang == "sql":
        return _run_sql(code)

    if settings.RUNNER_MODE != "docker":
        return {"ok": False, "exit_code": None, "stdout": "",
                "stderr": "Code execution is disabled: RUNNER_MODE=docker is required for isolation.",
                "duration_ms": 0, "runner": "none", "timed_out": False}

    workdir = Path(tempfile.mkdtemp(dir=str(run_dir())))
    try:
        fname = filename or f"main.{meta['ext']}"
        if lang == "java":
            fname = _java_filename(code, fname if filename else "Main.java")
        from app.services.storage import safe_join
        source_path = safe_join(workdir, fname)
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_text(code, encoding="utf-8")
        for path, content in (files or {}).items():
            try:
                from app.services.storage import safe_join
                target = safe_join(workdir, path)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
            except ValueError:
                continue

        result = _docker_run(lang, source_path, fname, workdir, stdin)
        return result
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# ---------------------------------------------------------------- workspace runs
def execute_in_workspace(language: str, cwd: str, entry: str, stdin: str = "",
                         home: str | None = None, timeout: int | None = None) -> dict:
    """Run a project's real entry point inside its persistent workspace."""
    import os as _os
    lang = (language or "python").lower()
    timeout = timeout or max(20, settings.RUN_TIMEOUT_SECONDS)
    meta = LANGUAGES.get(lang, {})

    if lang in PREVIEW_LANGUAGES or lang in ("html", "css", "vue", "angular", "react"):
        return {"ok": True, "exit_code": 0,
                "stdout": "Static project — open the Preview panel to render it.",
                "stderr": "", "duration_ms": 0, "runner": "preview", "timed_out": False}

    if lang == "sql":
        target = _os.path.join(cwd, entry)
        with open(target, encoding="utf-8") as fh:
            return _run_sql(fh.read())

    if not _os.path.exists(_os.path.join(cwd, entry)):
        return {"ok": False, "exit_code": None, "stdout": "",
                "stderr": f"Entry point not found: {entry}", "duration_ms": 0,
                "runner": "workspace", "timed_out": False}

    if settings.RUNNER_MODE != "docker":
        return {"ok": False, "exit_code": None, "stdout": "",
                "stderr": "Code execution is disabled: RUNNER_MODE=docker is required for isolation.",
                "duration_ms": 0, "runner": "none", "timed_out": False}

    if settings.RUNNER_MODE == "docker":
        if lang not in LANGUAGES or not LANGUAGES[lang].get("cmd"):
            return {"ok": False, "exit_code": None, "stdout": "",
                    "stderr": f"No isolated Docker runner is configured for {lang}.",
                    "duration_ms": 0, "runner": "none", "timed_out": False}
        temp_root = Path(tempfile.mkdtemp(prefix="wc-run-", dir=str(run_dir())))
        try:
            isolated = temp_root / "project"
            shutil.copytree(cwd, isolated, symlinks=True, ignore=shutil.ignore_patterns(
                ".git", ".venv", "__pycache__", "*.pyc", ".env", ".env.*"))
            source = isolated / entry
            if not source.is_file():
                return {"ok": False, "exit_code": None, "stdout": "",
                        "stderr": f"Entry point not found: {entry}", "duration_ms": 0,
                        "runner": "none", "timed_out": False}
            run_entry = entry
            if lang == "java":
                declared_entry = _java_filename(source.read_text(encoding="utf-8"), entry)
                if declared_entry != source.name:
                    java_source = source.with_name(declared_entry)
                    source.replace(java_source)
                    source = java_source
                run_entry = str(Path(entry).with_name(declared_entry))
            result = _docker_run(lang, source, run_entry, isolated, stdin)
            result["duration_ms"] = min(result.get("duration_ms", 0), int(timeout * 1000))
            return result
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)

    return {"ok": False, "exit_code": None, "stdout": "",
            "stderr": "No isolated runner is configured for this language.",
            "duration_ms": 0, "runner": "none", "timed_out": False}


def run_shell(command: str, cwd: str, home: str | None = None, timeout: int = 60) -> dict:
    """Run a legacy one-shot shell command only inside the isolated terminal image."""
    if settings.RUNNER_MODE != "docker":
        return {"ok": False, "exit_code": None, "stdout": "",
                "stderr": "Terminal commands are disabled: RUNNER_MODE=docker is required for isolation.",
                "duration_ms": 0, "runner": "none", "timed_out": False}
    container_name = f"wc-shell-{uuid.uuid4().hex[:12]}"
    image = settings.DOCKER_TERMINAL_IMAGE
    available, diagnostic = _ensure_local_image(
        image, Path(__file__).resolve().parents[3] / "docker" / "runner" / "Dockerfile.terminal")
    if not available:
        return {"ok": False, "exit_code": None, "stdout": "", "stderr": diagnostic,
                "duration_ms": 0, "runner": "docker", "timed_out": False}
    argv = [
        *_docker_argv("run", "--pull=never", "--rm", "--name", container_name, "-i", "--network", "none"),
        "--memory", "512m", "--memory-swap", "512m", "--cpus", "0.5",
        "--pids-limit", str(settings.RUN_MAX_PROCESSES), "--read-only",
        "--tmpfs", "/tmp:rw,nosuid,nodev,size=64m",
        "--tmpfs", "/home/dev:rw,nosuid,nodev,size=64m",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--user", _docker_user(), *docker_mount_args(cwd, "/work"),
        "-w", "/work", image, "bash", "-lc", command,
    ]
    started = time.time()
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False,
                                env=_docker_env())
        out, t1 = _truncate(result.stdout or "")
        err, t2 = _truncate(result.stderr or "")
        return {"ok": result.returncode == 0, "exit_code": result.returncode,
                "stdout": out, "stderr": err, "duration_ms": int((time.time() - started) * 1000),
                "runner": "docker", "timed_out": False, "truncated": t1 or t2}
    except subprocess.TimeoutExpired:
        try:
            subprocess.run(_docker_argv("rm", "-f", container_name), capture_output=True,
                           timeout=10, check=False, env=_docker_env())
        except (OSError, subprocess.SubprocessError):
            pass
        return {"ok": False, "exit_code": None, "stdout": "", "stderr": "Container killed: timeout",
                "duration_ms": int((time.time() - started) * 1000), "runner": "docker", "timed_out": True}
    except FileNotFoundError:
        return {"ok": False, "exit_code": None, "stdout": "",
                "stderr": _docker_error_message(""),
                "duration_ms": int((time.time() - started) * 1000), "runner": "none", "timed_out": False}
    except OSError as exc:
        return {"ok": False, "exit_code": None, "stdout": "",
                "stderr": f"Could not start Docker terminal command: {exc}",
                "duration_ms": int((time.time() - started) * 1000), "runner": "none", "timed_out": False}
