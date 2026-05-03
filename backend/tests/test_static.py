"""Tests for the opt-in static-file serving (used by the Docker image)."""
import importlib
import sys


def _reload_api_with_static(tmp_path, monkeypatch):
    static = tmp_path / "fe"
    static.mkdir()
    (static / "index.html").write_text("<h1>root</h1>", encoding="utf-8")
    (static / "login.html").write_text("<h1>login</h1>", encoding="utf-8")
    sub = static / "css"
    sub.mkdir()
    (sub / "app.css").write_text("body{color:red}", encoding="utf-8")

    db = tmp_path / "data.db"
    files = tmp_path / "files"
    monkeypatch.setenv("TASKBOARD_DB_PATH", str(db))
    monkeypatch.setenv("TASKBOARD_FILES_DIR", str(files))
    monkeypatch.setenv("TASKBOARD_STATIC_DIR", str(static))
    monkeypatch.setenv("TASKBOARD_SECRET_KEY", "x" * 64)
    for m in ("api", "auth", "migrate"):
        sys.modules.pop(m, None)
    return importlib.import_module("api"), static


def test_static_disabled_by_default(anon_client):
    # Default conftest does not set TASKBOARD_STATIC_DIR
    assert anon_client.get("/").status_code == 404
    assert anon_client.get("/index.html").status_code == 404


def test_static_serves_when_configured(tmp_path, monkeypatch):
    api, static = _reload_api_with_static(tmp_path, monkeypatch)
    with api.APP.test_client() as c:
        r = c.get("/")
        assert r.status_code == 200
        assert b"<h1>root</h1>" in r.data
        r = c.get("/login.html")
        assert r.status_code == 200
        r = c.get("/css/app.css")
        assert r.status_code == 200
        assert b"color:red" in r.data
        # missing file → 404
        assert c.get("/no-such-file.png").status_code == 404


def test_api_routes_take_precedence_over_static(tmp_path, monkeypatch):
    api, static = _reload_api_with_static(tmp_path, monkeypatch)
    # Even with a file that would shadow an api path, /api/* wins (or 404 if not registered)
    (static / "api").mkdir()
    (static / "api" / "data").write_text("nope", encoding="utf-8")
    with api.APP.test_client() as c:
        # Health is public — should be JSON, not the file content
        r = c.get("/api/health")
        assert r.status_code == 200
        assert r.is_json
        # Unknown /api path returns JSON 404 from the catch-all check, not the file
        r = c.get("/api/data")
        # This route exists and requires auth → 401, not 200 from the file
        assert r.status_code == 401


def test_static_path_traversal_blocked(tmp_path, monkeypatch):
    api, static = _reload_api_with_static(tmp_path, monkeypatch)
    with api.APP.test_client() as c:
        # Attempts to escape static root must 404, not return /etc/passwd
        r = c.get("/../../../../etc/passwd")
        assert r.status_code in (404, 308)  # 308 if Werkzeug normalizes
