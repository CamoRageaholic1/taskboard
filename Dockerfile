# syntax=docker/dockerfile:1.7
#
# Single-container deployment of taskboard.
# Runs gunicorn (production WSGI) serving both /api/* (Flask) and the
# frontend static files. SQLite + file blobs persist on a named volume
# mounted at /var/lib/taskboard.
#
# Build:    docker build -t taskboard .
# Run:      docker run -d --name taskboard -p 8083:5050 \
#               -e TASKBOARD_SECRET_KEY=$(openssl rand -hex 32) \
#               -v taskboard-data:/var/lib/taskboard \
#               taskboard
# Or use:   docker compose up -d
#
# slim-bookworm matches Debian on the Pi; bcrypt ships pre-built wheels
# for amd64 + arm64 so no compiler toolchain is needed.
FROM python:3.11-slim-bookworm

# Single layer for system needs: curl is only used by HEALTHCHECK below.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# --- Python deps (cached separately from app code so edits don't bust the layer) ---
WORKDIR /app
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt gunicorn==22.0.0

# --- App code (changes more often → comes after deps) ---
COPY backend/ /app/backend/
COPY frontend/ /app/frontend/
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# --- Non-root user (UID 1000 matches typical Linux host user for bind mounts) ---
RUN useradd --system --uid 1000 --home-dir /var/lib/taskboard --shell /usr/sbin/nologin taskboard \
 && mkdir -p /var/lib/taskboard \
 && chown -R taskboard:taskboard /var/lib/taskboard /app

# --- Configuration via env (override any of these at run time) ---
ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/backend \
    TASKBOARD_DB_PATH=/var/lib/taskboard/data.db \
    TASKBOARD_FILES_DIR=/var/lib/taskboard/files \
    TASKBOARD_STATIC_DIR=/app/frontend \
    PORT=5050

VOLUME ["/var/lib/taskboard"]
EXPOSE 5050

USER taskboard

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD curl -fsS http://127.0.0.1:${PORT}/api/health || exit 1

# entrypoint runs migrate + idempotent bootstrap, then exec's the CMD
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["sh", "-c", "exec gunicorn --workers 2 --threads 4 --bind 0.0.0.0:${PORT} --access-logfile - --error-logfile - api:APP"]
