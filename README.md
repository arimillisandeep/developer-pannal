# WORKCODE

Developer management, monitoring, task tracking and cloud coding platform.

```
WORKCODE-FINAL-UPDATED/
├── backend/        FastAPI + SQLAlchemy + Alembic, WebSockets, code runner
├── docker/         Compose stack, backend image, runner/terminal images, nginx
├── documentation/  API, database, deployment, IDE, security notes
├── frontend/       React 18 + Vite SPA (Monaco editor, xterm terminal)
└── postman/        Collection + environment for the REST API
```

---

## Quick start (local development)

Requirements: **Python 3.11+**, **Node 18+**, **PostgreSQL 14+**.
Docker is only needed for sandboxed code execution and the interactive terminal.

### 1. Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env        # then set SECRET_KEY + DATABASE_URL / POSTGRES_PASSWORD
python scripts/init_admins.py                    # creates the 3 admin accounts
python scripts/seed_demo.py --admin-email admin1@workcode.local   # optional demo data
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

API docs: <http://localhost:8000/api/docs> · health: `/api/health`

### 2. Frontend

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173 — proxies /api and /ws to the backend
```

### 3. Sign in

| Role | Email | Password |
|---|---|---|
| Admin | `admin1@workcode.local` | whatever you passed to `init_admins.py` |
| Developer (demo) | `arun.kumar@workcode.local` | `Workcode#2026` |

---

## Tests

The backend suite runs against a **real PostgreSQL database** and creates an isolated
throw-away schema per run — SQLite is deliberately not supported.

```bash
cd backend
.venv/bin/python -m pytest            # 220 passed, 4 skipped
```

Frontend tests:

```bash
cd frontend
npm test                              # node --test
```

---

## Notes

- `backend/.env`, `backend/credentials.json`, `backend/token.json` and `backend/storage/`
  are **git-ignored** — never commit real secrets or user source code.
- `STORAGE_ROOT` is resolved relative to `backend/`, so `./storage` always means
  `backend/storage` no matter which directory you launch from.
- `RUNNER_MODE=docker` is required for any untrusted code execution; `local` disables
  run/terminal by design (fail-closed). Use Compose for anything production-like.
- Production deployment, TLS and volume handling: see [`documentation/DEPLOYMENT.md`](documentation/DEPLOYMENT.md).
