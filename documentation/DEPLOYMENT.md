# Deployment

Two supported paths: **single server with Docker Compose** (recommended) or **manual
systemd + nginx**. The app is a single HTTP service (FastAPI serves the built React bundle),
so you only need one public port behind the proxy.

---

## 1. Docker Compose (production)

```bash
cp backend/.env.example backend/.env
# edit: SECRET_KEY, POSTGRES_PASSWORD, GOOGLE_*, CORS_ORIGINS=https://your-domain.com

# Build the interactive IDE terminal image (bash, Node/npm, Python/pip, Git, compilers)
docker build -f docker/runner/Dockerfile.terminal -t workcode-terminal:latest .

docker compose --env-file backend/.env -f docker/docker-compose.yml up -d --build
docker compose --env-file backend/.env -f docker/docker-compose.yml exec backend python scripts/init_admins.py
```

Services:

| Service | Port | Purpose |
|---|---|---|
| `db` | 5432 (internal) | PostgreSQL 16 with a persistent volume |
| `backend` | 8000 (internal) | FastAPI + built frontend |
| `nginx` | 80/443 | TLS termination, reverse proxy, gzip |

The backend container mounts the existing `workcode_storage` volume at `/app/storage` for
code, versions, submissions, and recordings. Keep its name and data intact when updating the
Compose file.

Docker execution and terminals require Docker daemon access. The Compose socket mount remains
commented because access to `/var/run/docker.sock` grants broad Docker-host control. If you
choose to enable it, set `RUNNER_MODE=docker` and set `DOCKER_STORAGE_VOLUME` to the **actual
existing Docker volume name** used for storage (including any Compose project prefix). The
runtime mounts only an existing project `source_files` subdirectory with `volume-subpath`.
Do not set a new volume name as a shortcut; that can hide the existing workspace data. Without
a correctly configured daemon and volume, execution fails closed. Terminal containers have no
network, so they cannot download packages.

### TLS

Put your certificate in `docker/certs/` (`fullchain.pem`, `privkey.pem`), or use Caddy /
Traefik in front for automatic Let's Encrypt. `docker/nginx.conf` already redirects
HTTP → HTTPS and sets HSTS.

---

## 2. Manual deployment (Ubuntu 22.04/24.04)

```bash
sudo apt update && sudo apt install -y python3.12-venv postgresql nginx nodejs npm git

# database
sudo -u postgres psql -c "CREATE USER workcode WITH PASSWORD 'CHANGE_ME';"
sudo -u postgres psql -c "CREATE DATABASE workcode OWNER workcode;"

# backend
sudo useradd -m -s /bin/bash workcode
sudo -u workcode git clone <repo> /home/workcode/workcode
cd /home/workcode/workcode/backend
sudo -u workcode python3 -m venv .venv
sudo -u workcode .venv/bin/pip install -r requirements.txt
sudo -u workcode cp .env.example .env      # then edit it
sudo -u workcode .venv/bin/alembic upgrade head
sudo -u workcode .venv/bin/python scripts/init_admins.py

# frontend
cd ../frontend
npm ci && npm run build                    # writes frontend/dist, served by FastAPI
```

`/etc/systemd/system/workcode.service`:

```ini
[Unit]
Description=WORKCODE API
After=network.target postgresql.service

[Service]
User=workcode
WorkingDirectory=/home/workcode/workcode/backend
EnvironmentFile=/home/workcode/workcode/backend/.env
ExecStart=/home/workcode/workcode/backend/.venv/bin/uvicorn app.main:app \
          --host 127.0.0.1 --port 8000 --workers 4 --proxy-headers
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now workcode
```

nginx site:

```nginx
server {
    listen 80;
    server_name workcode.example.com;
    return 301 https://$host$request_uri;
}
server {
    listen 443 ssl http2;
    server_name workcode.example.com;
    ssl_certificate     /etc/letsencrypt/live/workcode.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/workcode.example.com/privkey.pem;

    client_max_body_size 60M;

    location /ws {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 3600s;
    }
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

```bash
sudo certbot --nginx -d workcode.example.com
```

---

## 3. Docker runner images (code execution)

`RUNNER_MODE=docker` uses images named `workcode-runner-<language>:latest`. On the first Run
for a language, the backend checks Docker Desktop and builds a thin local alias with
`docker/runner/Dockerfile.runtime` using that language's existing official runtime image.
Concurrent first Runs for the same language share one build. Runtime starts with pull
disabled, so an already-built runner does not call the registry or credential helper.
Docker Desktop must be running and must be able to download the selected base image once.
The CLI is resolved from PATH, per-user Docker Desktop, and machine-wide Docker Desktop
locations; the backend adds its directory only to Docker child-process PATH so the bundled
credential helper remains discoverable. No global PATH or Docker credential configuration
is changed.

The base runtime is specified in `backend/app/services/runner.py` for each runnable language.
Static web stacks use Live Preview, and SQL uses a temporary in-memory SQLite database.
The interactive terminal image continues to use `docker/runner/Dockerfile.terminal` and is
built on first terminal startup if it is missing.

Each container runs with `--network none --read-only --cap-drop ALL --user 65534:65534`
and no volume other than the temporary workspace.

---

## 4. Operations

**Health check** — `GET /api/health` returns app, database and runner mode.

**Backups** (daily cron):

```bash
pg_dump --format=custom --file=/backups/workcode-$(date +%F).dump "$DATABASE_URL"
tar -czf /backups/storage-$(date +%F).tar.gz /home/workcode/workcode/backend/storage
find /backups -name '*.dump' -mtime +30 -delete
```

**Retention purge** (weekly): `python scripts/purge_retention.py`.

**Logs**: `journalctl -u workcode -f` or `docker compose logs -f backend`.
Structured exception logging is on by default; plug in Sentry by adding
`sentry_sdk.init(dsn=...)` to `app/main.py`.

**Scaling note**: the rate limiter and WebSocket hub are in-process. For more than one
worker/instance, move both to Redis (rate-limit buckets and a pub/sub fan-out for
`manager.publish`).

---

## 5. Local verification performed

- Frontend production build: passed with Vite 6.4.5 (132 modules transformed).
- Complete backend suite (Docker execution verification): 222 passed, one upstream Starlette deprecation warning.
- Docker CLI discovery, local image builds, authenticated workspace execution, restricted container runtime, timeout cleanup, and language samples were verified. See WORKCODE-CURRENT-DELIVERY-REPORT.md for exact coverage.
- Browser screen capture and playback were not end-to-end tested because local browser automation is disabled.

Backend tests use a unique PostgreSQL test schema and drop only that schema. Use the existing private backend `.env` for normal local setup; do not initialize a replacement database or rerun the admin initializer against existing accounts. Detailed results and limits are in `WORKCODE-CURRENT-DELIVERY-REPORT.md`. Google Meet and SMTP still require external credentials/infrastructure.

