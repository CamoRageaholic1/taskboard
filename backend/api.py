"""Taskboard API — state KV + attachments + backups, backed by SQLite + content-addressed file store."""
import hashlib
import json
import os
import secrets
import sqlite3
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_file

DB_PATH = Path(os.environ.get("TASKBOARD_DB_PATH", "/var/lib/taskboard/data.db"))
FILES_DIR = Path(os.environ.get("TASKBOARD_FILES_DIR", "/var/lib/taskboard/files"))
MAX_UPLOAD_BYTES = int(os.environ.get("TASKBOARD_MAX_UPLOAD_BYTES", 25 * 1024 * 1024))

APP = Flask(__name__)
APP.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES + 1024


def db():
    conn = sqlite3.connect(DB_PATH, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    FILES_DIR.mkdir(parents=True, exist_ok=True)
    (FILES_DIR / "tmp").mkdir(parents=True, exist_ok=True)
    with db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS state (
          id INTEGER PRIMARY KEY CHECK(id=1),
          data TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS attachments (
          id TEXT PRIMARY KEY,
          task_id TEXT NOT NULL,
          filename TEXT NOT NULL,
          mime TEXT NOT NULL,
          size INTEGER NOT NULL,
          sha256 TEXT NOT NULL,
          uploaded_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_attachments_task ON attachments(task_id);
        CREATE TABLE IF NOT EXISTS backups (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          data TEXT NOT NULL,
          size INTEGER NOT NULL,
          source TEXT NOT NULL,
          note TEXT,
          created_at TEXT NOT NULL
        );
        """)


def now_iso():
    return datetime.now(UTC).isoformat()


def short_id():
    return secrets.token_urlsafe(8)


def file_path_for(sha):
    return FILES_DIR / sha[:2] / sha


# ---------- state ----------

@APP.get("/api/health")
def health():
    return jsonify(status="ok", ts=now_iso())


@APP.get("/api/data")
def get_data():
    with db() as conn:
        row = conn.execute("SELECT data, updated_at FROM state WHERE id=1").fetchone()
    if not row:
        return jsonify(data=None, updated_at=None)
    return jsonify(data=json.loads(row[0]), updated_at=row[1])


@APP.post("/api/data")
def put_data():
    payload = request.get_json(silent=True)
    if payload is None or "data" not in payload:
        return jsonify(error="missing 'data'"), 400
    body = json.dumps(payload["data"], separators=(",", ":"))
    if len(body) > 5_000_000:
        return jsonify(error="state too large"), 413
    ts = now_iso()
    with db() as conn:
        conn.execute(
            "INSERT INTO state(id,data,updated_at) VALUES(1,?,?) "
            "ON CONFLICT(id) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at",
            (body, ts),
        )
    return jsonify(ok=True, updated_at=ts)


# ---------- attachments ----------

@APP.get("/api/attachments")
def list_attachments():
    task_id = request.args.get("task_id")
    if not task_id:
        return jsonify(error="missing task_id"), 400
    with db() as conn:
        rows = conn.execute(
            "SELECT id,task_id,filename,mime,size,sha256,uploaded_at "
            "FROM attachments WHERE task_id=? ORDER BY uploaded_at DESC",
            (task_id,),
        ).fetchall()
    return jsonify([
        dict(zip(["id", "task_id", "filename", "mime", "size", "sha256", "uploaded_at"], r, strict=True))
        for r in rows
    ])


@APP.post("/api/attachments")
def upload_attachment():
    task_id = request.args.get("task_id") or request.form.get("task_id")
    if not task_id:
        return jsonify(error="missing task_id"), 400
    f = request.files.get("file")
    if f is None or not f.filename:
        return jsonify(error="missing file"), 400

    # Stream to tmp, hashing as we go
    h = hashlib.sha256()
    size = 0
    tmp_fd, tmp_path = tempfile.mkstemp(dir=FILES_DIR / "tmp")
    try:
        with os.fdopen(tmp_fd, "wb") as out:
            while True:
                chunk = f.stream.read(65536)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    return jsonify(error="file exceeds 25MB"), 413
                h.update(chunk)
                out.write(chunk)
        if size == 0:
            return jsonify(error="empty file"), 400
        sha = h.hexdigest()
        dest = file_path_for(sha)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            os.replace(tmp_path, dest)
            tmp_path = None
        att_id = short_id()
        mime = f.mimetype or "application/octet-stream"
        ts = now_iso()
        with db() as conn:
            conn.execute(
                "INSERT INTO attachments(id,task_id,filename,mime,size,sha256,uploaded_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (att_id, task_id, f.filename, mime, size, sha, ts),
            )
        return jsonify(id=att_id, task_id=task_id, filename=f.filename,
                       mime=mime, size=size, sha256=sha, uploaded_at=ts), 201
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


@APP.get("/api/attachments/<att_id>")
def download_attachment(att_id):
    with db() as conn:
        row = conn.execute(
            "SELECT filename,mime,sha256 FROM attachments WHERE id=?",
            (att_id,),
        ).fetchone()
    if not row:
        abort(404)
    filename, mime, sha = row
    path = file_path_for(sha)
    if not path.exists():
        abort(410)
    return send_file(path, mimetype=mime, as_attachment=False, download_name=filename)


@APP.delete("/api/attachments/<att_id>")
def delete_attachment(att_id):
    with db() as conn:
        row = conn.execute("SELECT sha256 FROM attachments WHERE id=?", (att_id,)).fetchone()
        if not row:
            return jsonify(error="not found"), 404
        sha = row[0]
        conn.execute("DELETE FROM attachments WHERE id=?", (att_id,))
        # If no remaining attachment uses this blob, remove file
        still = conn.execute("SELECT 1 FROM attachments WHERE sha256=? LIMIT 1", (sha,)).fetchone()
    if not still:
        path = file_path_for(sha)
        if path.exists():
            path.unlink()
    return jsonify(ok=True)


# ---------- backups (server-side snapshots of the state blob) ----------

@APP.get("/api/backups")
def list_backups():
    with db() as conn:
        rows = conn.execute(
            "SELECT id,size,source,note,created_at FROM backups ORDER BY id DESC LIMIT 200"
        ).fetchall()
    return jsonify([
        dict(zip(["id", "size", "source", "note", "created_at"], r, strict=True)) for r in rows
    ])


@APP.post("/api/backups")
def create_backup():
    payload = request.get_json(silent=True) or {}
    source = (payload.get("source") or "manual")[:32]
    note = (payload.get("note") or None)
    data = payload.get("data")
    if data is None:
        # Snapshot whatever's currently in state
        with db() as conn:
            row = conn.execute("SELECT data FROM state WHERE id=1").fetchone()
        if not row:
            return jsonify(error="no current state to snapshot"), 400
        body = row[0]
    else:
        body = json.dumps(data, separators=(",", ":"))
    ts = now_iso()
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO backups(data,size,source,note,created_at) VALUES(?,?,?,?,?)",
            (body, len(body), source, note, ts),
        )
        bid = cur.lastrowid
    return jsonify(id=bid, size=len(body), source=source, note=note, created_at=ts), 201


@APP.get("/api/backups/<int:bid>")
def get_backup(bid):
    with db() as conn:
        row = conn.execute(
            "SELECT id,data,size,source,note,created_at FROM backups WHERE id=?", (bid,)
        ).fetchone()
    if not row:
        abort(404)
    return jsonify(
        id=row[0], data=json.loads(row[1]), size=row[2],
        source=row[3], note=row[4], created_at=row[5],
    )


@APP.delete("/api/backups/<int:bid>")
def delete_backup(bid):
    with db() as conn:
        n = conn.execute("DELETE FROM backups WHERE id=?", (bid,)).rowcount
    if not n:
        return jsonify(error="not found"), 404
    return jsonify(ok=True)


init()

if __name__ == "__main__":
    APP.run(host="127.0.0.1", port=5050)
