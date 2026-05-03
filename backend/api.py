"""Taskboard API — multi-user state, attachments, backups; SQLite + content-addressed file store."""
import hashlib
import json
import os
import secrets
import sqlite3
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from auth import (
    current_user_id,
    current_user_role,
    hash_password,
    login_user,
    logout_user,
    password_problems,
    require_admin,
    require_user,
    verify_password,
)
from flask import Flask, abort, jsonify, request, send_file
from migrate import migrate

DB_PATH = Path(os.environ.get("TASKBOARD_DB_PATH", "/var/lib/taskboard/data.db"))
FILES_DIR = Path(os.environ.get("TASKBOARD_FILES_DIR", "/var/lib/taskboard/files"))
MAX_UPLOAD_BYTES = int(os.environ.get("TASKBOARD_MAX_UPLOAD_BYTES", 25 * 1024 * 1024))
SECRET_KEY = os.environ.get("TASKBOARD_SECRET_KEY") or secrets.token_hex(32)
SESSION_DAYS = int(os.environ.get("TASKBOARD_SESSION_DAYS", "30"))

APP = Flask(__name__)
APP.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES + 1024
APP.config["SECRET_KEY"] = SECRET_KEY
APP.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=SESSION_DAYS)
APP.config["SESSION_COOKIE_HTTPONLY"] = True
APP.config["SESSION_COOKIE_SAMESITE"] = "Lax"


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
        migrate(conn)


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def short_id() -> str:
    return secrets.token_urlsafe(8)


def file_path_for(sha: str) -> Path:
    return FILES_DIR / sha[:2] / sha


def user_dict(row):
    return {
        "id": row[0], "username": row[1], "role": row[2],
        "is_active": bool(row[3]), "created_at": row[4], "last_login_at": row[5],
    }


# ---------- session / auth ----------

@APP.get("/api/health")
def health():
    return jsonify(status="ok", ts=now_iso())


@APP.post("/api/session")
def login():
    payload = request.get_json(silent=True) or {}
    username = (payload.get("username") or "").strip().lower()
    password = payload.get("password") or ""
    if not username or not password:
        return jsonify(error="username and password required"), 400
    with db() as conn:
        row = conn.execute(
            "SELECT id, password_hash, role, is_active FROM users WHERE username=?", (username,)
        ).fetchone()
    if not row or not row[3] or not verify_password(password, row[1]):
        return jsonify(error="invalid credentials"), 401
    uid, _, role, _ = row
    login_user(uid, role)
    with db() as conn:
        conn.execute("UPDATE users SET last_login_at=? WHERE id=?", (now_iso(), uid))
    return jsonify(id=uid, username=username, role=role)


@APP.delete("/api/session")
def logout():
    logout_user()
    return jsonify(ok=True)


@APP.get("/api/session")
def whoami():
    uid = current_user_id()
    if uid is None:
        return jsonify(authenticated=False), 200
    with db() as conn:
        row = conn.execute(
            "SELECT id, username, role, is_active FROM users WHERE id=?", (uid,)
        ).fetchone()
    if not row or not row[3]:
        logout_user()
        return jsonify(authenticated=False), 200
    return jsonify(authenticated=True, id=row[0], username=row[1], role=row[2])


# ---------- state ----------

@APP.get("/api/data")
@require_user
def get_data():
    uid = current_user_id()
    with db() as conn:
        row = conn.execute(
            "SELECT data, updated_at FROM state WHERE user_id=?", (uid,)
        ).fetchone()
    if not row:
        return jsonify(data=None, updated_at=None)
    return jsonify(data=json.loads(row[0]), updated_at=row[1])


@APP.post("/api/data")
@require_user
def put_data():
    payload = request.get_json(silent=True)
    if payload is None or "data" not in payload:
        return jsonify(error="missing 'data'"), 400
    body = json.dumps(payload["data"], separators=(",", ":"))
    if len(body) > 5_000_000:
        return jsonify(error="state too large"), 413
    uid = current_user_id()
    ts = now_iso()
    with db() as conn:
        conn.execute(
            "INSERT INTO state(user_id,data,updated_at) VALUES(?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at",
            (uid, body, ts),
        )
    return jsonify(ok=True, updated_at=ts)


# ---------- attachments ----------

@APP.get("/api/attachments")
@require_user
def list_attachments():
    task_id = request.args.get("task_id")
    if not task_id:
        return jsonify(error="missing task_id"), 400
    uid = current_user_id()
    with db() as conn:
        rows = conn.execute(
            "SELECT id,task_id,filename,mime,size,sha256,uploaded_at "
            "FROM attachments WHERE task_id=? AND user_id=? ORDER BY uploaded_at DESC",
            (task_id, uid),
        ).fetchall()
    return jsonify([
        dict(zip(["id", "task_id", "filename", "mime", "size", "sha256", "uploaded_at"], r, strict=True))
        for r in rows
    ])


@APP.post("/api/attachments")
@require_user
def upload_attachment():
    task_id = request.args.get("task_id") or request.form.get("task_id")
    if not task_id:
        return jsonify(error="missing task_id"), 400
    f = request.files.get("file")
    if f is None or not f.filename:
        return jsonify(error="missing file"), 400

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
                    return jsonify(error="file exceeds limit"), 413
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
        uid = current_user_id()
        with db() as conn:
            conn.execute(
                "INSERT INTO attachments(id,user_id,task_id,filename,mime,size,sha256,uploaded_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (att_id, uid, task_id, f.filename, mime, size, sha, ts),
            )
        return jsonify(id=att_id, task_id=task_id, filename=f.filename,
                       mime=mime, size=size, sha256=sha, uploaded_at=ts), 201
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


@APP.get("/api/attachments/<att_id>")
@require_user
def download_attachment(att_id):
    uid = current_user_id()
    with db() as conn:
        row = conn.execute(
            "SELECT filename,mime,sha256,user_id FROM attachments WHERE id=?", (att_id,)
        ).fetchone()
    if not row:
        abort(404)
    filename, mime, sha, owner = row
    if owner != uid and current_user_role() != "admin":
        abort(403)
    path = file_path_for(sha)
    if not path.exists():
        abort(410)
    return send_file(path, mimetype=mime, as_attachment=False, download_name=filename)


@APP.delete("/api/attachments/<att_id>")
@require_user
def delete_attachment(att_id):
    uid = current_user_id()
    with db() as conn:
        row = conn.execute("SELECT sha256, user_id FROM attachments WHERE id=?", (att_id,)).fetchone()
        if not row:
            return jsonify(error="not found"), 404
        sha, owner = row
        if owner != uid and current_user_role() != "admin":
            return jsonify(error="forbidden"), 403
        conn.execute("DELETE FROM attachments WHERE id=?", (att_id,))
        still = conn.execute("SELECT 1 FROM attachments WHERE sha256=? LIMIT 1", (sha,)).fetchone()
    if not still:
        path = file_path_for(sha)
        if path.exists():
            path.unlink()
    return jsonify(ok=True)


# ---------- backups (per-user) ----------

@APP.get("/api/backups")
@require_user
def list_backups():
    uid = current_user_id()
    with db() as conn:
        rows = conn.execute(
            "SELECT id,size,source,note,created_at FROM backups "
            "WHERE user_id=? ORDER BY id DESC LIMIT 200", (uid,)
        ).fetchall()
    return jsonify([
        dict(zip(["id", "size", "source", "note", "created_at"], r, strict=True)) for r in rows
    ])


@APP.post("/api/backups")
@require_user
def create_backup():
    payload = request.get_json(silent=True) or {}
    source = (payload.get("source") or "manual")[:32]
    note = payload.get("note") or None
    data = payload.get("data")
    uid = current_user_id()
    if data is None:
        with db() as conn:
            row = conn.execute("SELECT data FROM state WHERE user_id=?", (uid,)).fetchone()
        if not row:
            return jsonify(error="no current state to snapshot"), 400
        body = row[0]
    else:
        body = json.dumps(data, separators=(",", ":"))
    ts = now_iso()
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO backups(user_id,data,size,source,note,created_at) VALUES(?,?,?,?,?,?)",
            (uid, body, len(body), source, note, ts),
        )
        bid = cur.lastrowid
    return jsonify(id=bid, size=len(body), source=source, note=note, created_at=ts), 201


@APP.get("/api/backups/<int:bid>")
@require_user
def get_backup(bid):
    uid = current_user_id()
    with db() as conn:
        row = conn.execute(
            "SELECT id,data,size,source,note,created_at,user_id FROM backups WHERE id=?", (bid,)
        ).fetchone()
    if not row:
        abort(404)
    if row[6] != uid and current_user_role() != "admin":
        abort(403)
    return jsonify(
        id=row[0], data=json.loads(row[1]), size=row[2],
        source=row[3], note=row[4], created_at=row[5],
    )


@APP.delete("/api/backups/<int:bid>")
@require_user
def delete_backup(bid):
    uid = current_user_id()
    with db() as conn:
        row = conn.execute("SELECT user_id FROM backups WHERE id=?", (bid,)).fetchone()
        if not row:
            return jsonify(error="not found"), 404
        if row[0] != uid and current_user_role() != "admin":
            return jsonify(error="forbidden"), 403
        conn.execute("DELETE FROM backups WHERE id=?", (bid,))
    return jsonify(ok=True)


# ---------- users (admin) ----------

@APP.get("/api/users")
@require_admin
def list_users():
    with db() as conn:
        rows = conn.execute(
            "SELECT id, username, role, is_active, created_at, last_login_at FROM users ORDER BY id"
        ).fetchall()
    return jsonify([user_dict(r) for r in rows])


@APP.post("/api/users")
@require_admin
def create_user():
    payload = request.get_json(silent=True) or {}
    username = (payload.get("username") or "").strip().lower()
    password = payload.get("password") or ""
    role = payload.get("role") or "user"
    if not username or not username.replace("_", "").replace("-", "").isalnum() or len(username) > 32:
        return jsonify(error="invalid username (alphanumeric, underscore, hyphen; max 32)"), 400
    if role not in ("admin", "user"):
        return jsonify(error="role must be 'admin' or 'user'"), 400
    err = password_problems(password)
    if err:
        return jsonify(error=err), 400
    ph = hash_password(password)
    ts = now_iso()
    try:
        with db() as conn:
            cur = conn.execute(
                "INSERT INTO users(username,password_hash,role,is_active,created_at) VALUES(?,?,?,1,?)",
                (username, ph, role, ts),
            )
        uid = cur.lastrowid
    except sqlite3.IntegrityError:
        return jsonify(error="username already taken"), 409
    return jsonify(id=uid, username=username, role=role, is_active=True,
                   created_at=ts, last_login_at=None), 201


@APP.patch("/api/users/<int:uid>")
@require_admin
def update_user(uid):
    payload = request.get_json(silent=True) or {}
    sets = []
    params = []
    if "role" in payload:
        if payload["role"] not in ("admin", "user"):
            return jsonify(error="role must be 'admin' or 'user'"), 400
        sets.append("role=?")
        params.append(payload["role"])
    if "is_active" in payload:
        sets.append("is_active=?")
        params.append(1 if payload["is_active"] else 0)
    if "password" in payload:
        err = password_problems(payload["password"])
        if err:
            return jsonify(error=err), 400
        sets.append("password_hash=?")
        params.append(hash_password(payload["password"]))
    if not sets:
        return jsonify(error="no fields to update"), 400
    params.append(uid)
    with db() as conn:
        cur = conn.execute(f"UPDATE users SET {', '.join(sets)} WHERE id=?", params)
        if cur.rowcount == 0:
            return jsonify(error="not found"), 404
        row = conn.execute(
            "SELECT id, username, role, is_active, created_at, last_login_at FROM users WHERE id=?", (uid,)
        ).fetchone()
    return jsonify(user_dict(row))


@APP.delete("/api/users/<int:uid>")
@require_admin
def delete_user(uid):
    if uid == current_user_id():
        return jsonify(error="cannot delete yourself"), 400
    with db() as conn:
        # Check admin count to prevent removing last admin
        is_target_admin = conn.execute(
            "SELECT 1 FROM users WHERE id=? AND role='admin' AND is_active=1", (uid,)
        ).fetchone()
        if is_target_admin:
            n = conn.execute(
                "SELECT COUNT(*) FROM users WHERE role='admin' AND is_active=1"
            ).fetchone()[0]
            if n <= 1:
                return jsonify(error="cannot remove the last active admin"), 400
        # Cascade: delete the user's data
        conn.execute("DELETE FROM state WHERE user_id=?", (uid,))
        conn.execute("DELETE FROM attachments WHERE user_id=?", (uid,))
        conn.execute("DELETE FROM backups WHERE user_id=?", (uid,))
        cur = conn.execute("DELETE FROM users WHERE id=?", (uid,))
        if cur.rowcount == 0:
            return jsonify(error="not found"), 404
    return jsonify(ok=True)


@APP.get("/api/admin/stats")
@require_admin
def admin_stats():
    with db() as conn:
        users_total = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        users_active = conn.execute("SELECT COUNT(*) FROM users WHERE is_active=1").fetchone()[0]
        states = conn.execute("SELECT COUNT(*) FROM state").fetchone()[0]
        atts = conn.execute("SELECT COUNT(*), COALESCE(SUM(size),0) FROM attachments").fetchone()
        bks = conn.execute("SELECT COUNT(*), COALESCE(SUM(size),0) FROM backups").fetchone()
    files_disk = sum(p.stat().st_size for p in FILES_DIR.rglob("*") if p.is_file())
    return jsonify(
        users_total=users_total, users_active=users_active, users_with_state=states,
        attachments_count=atts[0], attachments_bytes=atts[1],
        backups_count=bks[0], backups_bytes=bks[1],
        files_on_disk_bytes=files_disk,
    )


init()

if __name__ == "__main__":
    APP.run(host="127.0.0.1", port=5050)
