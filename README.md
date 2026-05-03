# Taskboard

Self-hosted personal task & project board. Single-page React UI in one HTML file, Flask API on a Pi, SQLite + content-addressed file store. Designed for one user on a LAN — no auth (relies on network-level trust).

## Features
- Projects with colored tasks, subtasks, due dates, priority, recurrence
- Per-task file attachments (uploaded via UI, stored on disk under `/var/lib/taskboard/files/<sha[:2]>/<sha>`, deduped by content hash)
- Server-side state snapshots ("Save snapshot to server" / "Browse server snapshots…" in the command palette)
- LocalStorage acts as an offline cache; the Pi is the source of truth across devices
- Cmd/Ctrl-K command palette, dark mode

## Stack
| Layer    | Tech                                       |
|----------|--------------------------------------------|
| UI       | React 18 via esm.sh, single `index.html`   |
| API      | Flask 2.x, single `api.py`                 |
| Storage  | SQLite (WAL mode) + content-addressed files |
| Edge     | nginx — static + reverse proxy `/api/`     |
| Service  | systemd unit `taskboard-api.service`       |

## Layout
```
backend/      Flask API + tests
frontend/     index.html (React, no build)
deploy/       systemd unit, nginx server block, deploy.sh
.github/      CI workflow
```

## API
| Method | Path                          | Purpose                       |
|--------|-------------------------------|-------------------------------|
| GET    | `/api/health`                 | liveness                      |
| GET    | `/api/data`                   | full state blob               |
| POST   | `/api/data`                   | replace state (`{data:...}`)  |
| GET    | `/api/attachments?task_id=X`  | list a task's files           |
| POST   | `/api/attachments?task_id=X`  | multipart upload (max 25 MB)  |
| GET    | `/api/attachments/<id>`       | download                      |
| DELETE | `/api/attachments/<id>`       | remove                        |
| GET    | `/api/backups`                | list snapshots (newest first) |
| POST   | `/api/backups`                | create snapshot               |
| GET    | `/api/backups/<id>`           | fetch snapshot                |
| DELETE | `/api/backups/<id>`           | remove snapshot               |

## Configuration (env)
| Var                          | Default                              |
|------------------------------|--------------------------------------|
| `TASKBOARD_DB_PATH`          | `/var/lib/taskboard/data.db`         |
| `TASKBOARD_FILES_DIR`        | `/var/lib/taskboard/files`           |
| `TASKBOARD_MAX_UPLOAD_BYTES` | `26214400` (25 MB)                   |

## Deploy
```bash
git pull
./deploy/deploy.sh
```
Idempotent. Creates the `taskboard` system user, installs files into `/opt/taskboard`, `/var/www/taskboard`, `/var/lib/taskboard`, the systemd unit, and the nginx server block. Restarts the API and reloads nginx. Runs a health check.

Reach it at `http://<host>:8083/`.

## Development
```bash
# install deps
pip install -r backend/requirements.txt
pip install ruff pytest

# tests
pytest

# run locally (sqlite + files in current dir)
TASKBOARD_DB_PATH=./dev.db TASKBOARD_FILES_DIR=./dev-files \
  python -m flask --app backend/api.py run --port 5050
```

## Backup
Single SQLite file plus the file blob tree:
```bash
rsync -a /var/lib/taskboard/ /backup/target/
```
Or use the in-app "Save snapshot to server" — stored in the same DB.
