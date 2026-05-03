import importlib
import io
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))


def _fresh_app(tmp_path, monkeypatch):
    db = tmp_path / "data.db"
    files = tmp_path / "files"
    monkeypatch.setenv("TASKBOARD_DB_PATH", str(db))
    monkeypatch.setenv("TASKBOARD_FILES_DIR", str(files))
    monkeypatch.setenv("TASKBOARD_MAX_UPLOAD_BYTES", str(64 * 1024))
    monkeypatch.setenv("TASKBOARD_SECRET_KEY", "test-secret-do-not-use-in-prod-test-secret-do-not-use-in-prod")
    for mod in ("api", "auth", "migrate"):
        sys.modules.pop(mod, None)
    api = importlib.import_module("api")
    api.APP.config["TESTING"] = True
    return api


def _seed_users(api):
    """Insert admin + user via direct DB calls (faster than going through endpoints)."""
    auth = sys.modules["auth"]
    ts = api.now_iso()
    with api.db() as conn:
        conn.execute(
            "INSERT INTO users(username,password_hash,role,is_active,created_at) VALUES(?,?,?,1,?)",
            ("admin", auth.hash_password("Adminpass1!"), "admin", ts),
        )
        conn.execute(
            "INSERT INTO users(username,password_hash,role,is_active,created_at) VALUES(?,?,?,1,?)",
            ("alice", auth.hash_password("Alicepass1!"), "user", ts),
        )


def _login(client, username, password):
    r = client.post("/api/session", json={"username": username, "password": password})
    assert r.status_code == 200, r.data
    return r.json


@pytest.fixture
def api(tmp_path, monkeypatch):
    return _fresh_app(tmp_path, monkeypatch)


@pytest.fixture
def anon_client(api):
    with api.APP.test_client() as c:
        yield c


@pytest.fixture
def client(api):
    """Authenticated as 'alice' (regular user). The default for legacy tests."""
    _seed_users(api)
    with api.APP.test_client() as c:
        _login(c, "alice", "Alicepass1!")
        yield c


@pytest.fixture
def admin_client(api):
    _seed_users(api)
    with api.APP.test_client() as c:
        _login(c, "admin", "Adminpass1!")
        yield c


@pytest.fixture
def make_file():
    def _make(name="hello.txt", contents=b"hello world", mime="text/plain"):
        return (io.BytesIO(contents), name, mime)
    return _make
