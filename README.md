# Taskboard

Self-hosted multi-user task & project board. React UI in single-file HTML, Flask API, SQLite + content-addressed file store. Designed for a small group on a LAN.

## Features
- **Multi-user** with `admin` / `user` roles. Per-user private boards (no cross-visibility for non-admins).
- Projects with colored tasks, subtasks, due dates, priority, recurrence
- Per-task file attachments (max 25 MB, deduped by SHA-256)
- **Daily Notes** — per-user, per-day capture pad: many notes per day, each with title + markdown body, edit/preview toggle, debounced auto-save
- Server-side state snapshots (Cmd/Ctrl-K → "Save snapshot to server" / "Browse server snapshots…")
- LocalStorage acts as offline cache; server is source of truth
- Admin dashboard at `/admin.html` — create/disable/promote users, reset passwords, see usage stats
- 30-day signed-cookie sessions, bcrypt password hashing

## Stack
| Layer    | Tech                                       |
|----------|--------------------------------------------|
| UI       | React 18 via esm.sh, single `index.html` (+ `login.html`, `admin.html`) |
| API      | Flask 2.x — `api.py`, `auth.py`, `migrate.py`, `cli.py` |
| Storage  | SQLite (WAL) + content-addressed blob store |
| Auth     | Username + bcrypt; signed-cookie sessions   |
| Edge     | nginx — static + reverse proxy `/api/`     |
| Service  | systemd unit `taskboard-api.service`       |

## Layout
```
backend/
  api.py        Flask routes
  auth.py       password hashing, session helpers, role decorators
  migrate.py    idempotent SQLite schema migrations
  cli.py        admin CLI: bootstrap, user add/passwd/role/disable
  tests/        pytest suite
frontend/
  index.html    main board (React)
  login.html    sign-in page
  admin.html    admin dashboard (vanilla JS)
deploy/
  taskboard-api.service
  nginx.conf
  deploy.sh     idempotent installer (creates user, installs deps, bootstraps admin)
.github/workflows/ci.yml
```

## Password rules
Minimum 8 characters, must include at least one uppercase, one lowercase, and one special character (any of `!@#$%^&*()-_=+[]{}|;:,.<>?/~`). Auto-generated passwords always satisfy these.

## API
| Method | Path                          | Auth   | Purpose                       |
|--------|-------------------------------|--------|-------------------------------|
| GET    | `/api/health`                 | none   | liveness                      |
| POST   | `/api/session`                | none   | login: `{username, password}` — returns `{requires_totp:true}` if 2FA on |
| POST   | `/api/session/totp`           | pending| second-factor login: `{code}` |
| DELETE | `/api/session`                | none   | logout                        |
| GET    | `/api/session`                | none   | whoami: `{authenticated, ...}`|
| GET    | `/api/2fa/status`             | user   | `{enabled: bool}`              |
| POST   | `/api/2fa/setup`              | user   | issue a candidate secret + otpauth URI |
| POST   | `/api/2fa/enable`             | user   | verify code, flip enabled=true |
| POST   | `/api/2fa/disable`            | user   | verify code, clear secret + disable |
| GET    | `/api/data`                   | user   | this user's board state       |
| POST   | `/api/data`                   | user   | replace state                 |
| GET    | `/api/attachments?task_id=X`  | user   | list this user's files for X  |
| POST   | `/api/attachments?task_id=X`  | user   | multipart upload (max 25 MB)  |
| GET    | `/api/attachments/<id>`       | user/admin | download (admin: any user) |
| DELETE | `/api/attachments/<id>`       | user/admin | remove                  |
| GET    | `/api/backups`                | user   | this user's snapshots         |
| POST   | `/api/backups`                | user   | create snapshot               |
| GET    | `/api/backups/<id>`           | user/admin | fetch snapshot          |
| DELETE | `/api/backups/<id>`           | user/admin | remove                  |
| GET    | `/api/search?q=TERM`          | user   | brute-force search across this user's projects, tasks, subtasks, notes (min 2 chars) |
| GET    | `/api/feed/token`             | user   | get this user's iCal feed token (creates one if missing) |
| POST   | `/api/feed/token`             | user   | rotate the feed token (old subscriptions break) |
| GET    | `/api/calendar/<token>.ics`   | none*  | iCal feed of open due-dated tasks (token IS the auth) |
| GET    | `/api/notes?date=YYYY-MM-DD`  | user   | list this user's notes for that day (default: today) |
| GET    | `/api/notes/dates`            | user   | dates with notes (recent 365) |
| GET    | `/api/notes/export?date=YYYY-MM-DD` | user | one day's notes as a Markdown file (default: today) |
| GET    | `/api/notes/export.zip`       | user   | all notes as a zip — one .md per date + a README |
| POST   | `/api/notes`                  | user   | create note `{title, body, date?}` |
| PATCH  | `/api/notes/<id>`             | user   | update title / body / date    |
| DELETE | `/api/notes/<id>`             | user   | remove                        |
| GET    | `/api/users`                  | admin  | list users                    |
| POST   | `/api/users`                  | admin  | create user                   |
| PATCH  | `/api/users/<id>`             | admin  | role / is_active / password   |
| DELETE | `/api/users/<id>`             | admin  | delete user + cascade their data |
| GET    | `/api/admin/stats`            | admin  | counts + disk usage           |

## Configuration (env)
| Var                          | Default                              |
|------------------------------|--------------------------------------|
| `TASKBOARD_DB_PATH`          | `/var/lib/taskboard/data.db`         |
| `TASKBOARD_FILES_DIR`        | `/var/lib/taskboard/files`           |
| `TASKBOARD_MAX_UPLOAD_BYTES` | `26214400` (25 MB)                   |
| `TASKBOARD_SECRET_KEY`       | (generated by `deploy.sh`, stored in `/etc/taskboard/env`) |
| `TASKBOARD_SESSION_DAYS`     | `30`                                 |

## Deploy

Two paths — pick whichever you prefer. Both produce the same app at the same port.

### Option A — bare metal (systemd + nginx, e.g. on a Pi)
```bash
git pull
./deploy/deploy.sh
```
Idempotent. Creates `taskboard` system user, installs deps, generates secret key on first run, bootstraps an admin account (default username `admin`, override with `--username NAME` to `cli.py bootstrap`) with a random password printed to `/var/log/taskboard-bootstrap.log`. Subsequent runs only update files and restart services.

### Option B — Docker (single container)
```bash
echo "TASKBOARD_SECRET_KEY=$(openssl rand -hex 32)" > .env
docker compose up -d
docker compose logs taskboard | grep -A1 'admin user'   # first-run admin password
```
The image runs gunicorn serving both `/api/*` (Flask) and the frontend static files. Persistent state lives in the `taskboard-data` named volume.

#### Pull a pre-built image (no local build)
Every push to `main` publishes a multi-arch image (linux/amd64 + linux/arm64) to GitHub Container Registry:

```bash
docker pull ghcr.io/camorageaholic1/taskboard:latest
docker run -d --name taskboard -p 8083:5050 \
  -e TASKBOARD_SECRET_KEY=$(openssl rand -hex 32) \
  -v taskboard-data:/var/lib/taskboard \
  ghcr.io/camorageaholic1/taskboard:latest
```

Tags available: `latest` (main HEAD), `sha-<short>` (every commit), `vX.Y.Z` (release tags).

#### Build locally
```bash
docker build -t taskboard .
docker run -d --name taskboard -p 8083:5050 \
  -e TASKBOARD_SECRET_KEY=$(openssl rand -hex 32) \
  -v taskboard-data:/var/lib/taskboard \
  taskboard
```

To back up the volume:
```bash
docker run --rm -v taskboard-data:/data -v "$PWD":/backup alpine \
  tar czf /backup/taskboard-$(date +%F).tgz -C /data .
```

## Admin CLI
```bash
sudo -u taskboard env $(grep ^TASKBOARD_ /etc/taskboard/env) python3 /opt/taskboard/cli.py user add alice
sudo -u taskboard env $(grep ^TASKBOARD_ /etc/taskboard/env) python3 /opt/taskboard/cli.py user passwd alice
sudo -u taskboard env $(grep ^TASKBOARD_ /etc/taskboard/env) python3 /opt/taskboard/cli.py user role alice admin
sudo -u taskboard env $(grep ^TASKBOARD_ /etc/taskboard/env) python3 /opt/taskboard/cli.py user disable alice
```
Or, if the admin is logged in via the web, just use `/admin.html` — same operations.

## Development
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt ruff pytest
ruff check backend
pytest

TASKBOARD_DB_PATH=./dev.db TASKBOARD_FILES_DIR=./dev-files TASKBOARD_SECRET_KEY=$(python -c 'import secrets;print(secrets.token_hex(32))') \
  python -m flask --app backend/api.py run --port 5050
# then in another shell:
TASKBOARD_DB_PATH=./dev.db python backend/cli.py bootstrap --username admin
```

## Backup
```bash
rsync -a /var/lib/taskboard/ /backup/target/
```
Single SQLite file + content-addressed blob tree. Or use the in-app "Save snapshot to server" — stored in the same DB.
