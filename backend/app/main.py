"""WORKCODE FastAPI application."""
import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.core.db import SessionLocal, init_db
from app.core.ratelimit import rate_limit_middleware
from app.api import (
    devproxy,
    monitoring,
    admin, auth, chat, git, meetings, notifications, projects, recordings, reports,
    submissions, tasks, terminal, timer, users, workspace,
)
from app.ws.manager import manager
from app.ws.routes import router as ws_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("workcode")


async def _inactivity_sweeper() -> None:
    """Auto-pause sessions whose browser stopped sending heartbeats."""
    from app.models import TimerEvent, TimerSession

    while True:
        await asyncio.sleep(60)
        db = SessionLocal()
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(minutes=settings.AUTO_PAUSE_MINUTES)
            stale = (db.query(TimerSession)
                     .filter(TimerSession.status == "running",
                             TimerSession.last_heartbeat_at < cutoff).all())
            for s in stale:
                last = s.last_heartbeat_at
                if last and last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                delta = min(int((datetime.now(timezone.utc) - last).total_seconds()),
                            settings.HEARTBEAT_SECONDS * 3)
                s.accumulated_seconds = (s.accumulated_seconds or 0) + max(delta, 0)
                s.status = "paused"
                s.paused_at = datetime.now(timezone.utc)
                s.break_count = (s.break_count or 0) + 1
                db.add(TimerEvent(session_id=s.id, user_id=s.user_id, event="inactivity_pause",
                                  meta_json='{"source":"sweeper"}'))
            if stale:
                db.commit()
                log.info("auto-paused %s idle timer session(s)", len(stale))
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            log.warning("sweeper error: %s", exc)
        finally:
            db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    Path(settings.STORAGE_ROOT).mkdir(parents=True, exist_ok=True)
    manager.set_loop(asyncio.get_running_loop())
    sweeper = asyncio.create_task(_inactivity_sweeper())
    log.info("%s started (env=%s, runner=%s)", settings.APP_NAME, settings.ENV, settings.RUNNER_MODE)
    try:
        yield
    finally:
        sweeper.cancel()


app = FastAPI(
    title="WORKCODE API",
    description="Developer management, monitoring, task tracking and cloud coding platform.",
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.middleware("http")(rate_limit_middleware)


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={
        "detail": "Validation error",
        "errors": [{"field": ".".join(str(x) for x in e["loc"][1:]), "message": e["msg"]}
                   for e in exc.errors()],
    })


@app.exception_handler(Exception)
async def unhandled_handler(request: Request, exc: Exception):
    log.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


for r in (auth.router, users.router, admin.router, tasks.router, projects.router,
          devproxy.router,
          monitoring.router,
          workspace.router, submissions.router, timer.router, recordings.router,
          chat.router, notifications.router, meetings.router, reports.router,
          terminal.router, git.router):
    app.include_router(r, prefix=settings.API_PREFIX)

app.include_router(ws_router)
from app.api.terminal import root_ws  # noqa: E402
app.include_router(root_ws)


@app.get("/api/health", tags=["System"])
def health():
    return {"status": "ok", "app": settings.APP_NAME, "env": settings.ENV,
            "database": "postgresql" if settings.is_postgres else "sqlite",
            "runner": settings.RUNNER_MODE, "time": datetime.now(timezone.utc).isoformat()}


# ------------------------------------------------- serve the built React app
dist = Path(settings.FRONTEND_DIST)
if (dist / "index.html").exists():
    if (dist / "assets").exists():
        app.mount("/assets", StaticFiles(directory=str(dist / "assets")), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str):
        candidate = dist / full_path
        if full_path and candidate.is_file():
            return FileResponse(str(candidate))
        return FileResponse(str(dist / "index.html"))


@app.middleware("http")
async def _no_cache_shell(request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.endswith(".html") or not path.startswith("/api"):
        if response.headers.get("content-type", "").startswith("text/html"):
            response.headers["Cache-Control"] = "no-store, must-revalidate"
    return response
