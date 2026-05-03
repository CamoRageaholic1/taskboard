import sqlite3


def test_migrate_brings_fresh_db_to_latest_idempotently(tmp_path):
    from migrate import migrate
    db = tmp_path / "fresh.db"
    conn = sqlite3.connect(db, isolation_level=None)
    v = migrate(conn)
    assert v >= 2
    # Second call should be a no-op and return the same version
    assert migrate(conn) == v
    # Schema sanity
    cols = {r[1] for r in conn.execute("PRAGMA table_info(state)")}
    assert "user_id" in cols
    assert "id" not in cols
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"users", "state", "attachments", "backups", "notes", "meta"} <= tables
    conn.close()


def test_legacy_data_preserved_then_migrated_by_bootstrap(tmp_path, monkeypatch):
    """Simulate an existing v1 DB with a state row, then migrate + bootstrap."""
    from migrate import SCHEMA_V1
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(db, isolation_level=None)
    conn.executescript(SCHEMA_V1)
    conn.execute(
        "INSERT INTO state(id,data,updated_at) VALUES(1,?,?)",
        ('{"projects":[{"id":"p1","name":"legacy","tasks":[]}]}', "2026-01-01T00:00:00+00:00"),
    )
    conn.execute(
        "INSERT INTO attachments(id,task_id,filename,mime,size,sha256,uploaded_at) VALUES(?,?,?,?,?,?,?)",
        ("a1", "t1", "f.txt", "text/plain", 5, "x" * 64, "2026-01-01T00:00:00+00:00"),
    )
    conn.close()

    # Simulate v0 -> v2 migrate at startup
    monkeypatch.setenv("TASKBOARD_DB_PATH", str(db))
    monkeypatch.setenv("TASKBOARD_FILES_DIR", str(tmp_path / "files"))
    monkeypatch.setenv("TASKBOARD_SECRET_KEY", "x" * 64)
    import importlib
    import sys
    for m in ("api", "auth", "migrate"):
        sys.modules.pop(m, None)
    api = importlib.import_module("api")

    # state_legacy table should exist with the row
    with api.db() as c:
        row = c.execute("SELECT data FROM state_legacy WHERE id=1").fetchone()
        assert row is not None
        # attachments row has user_id=NULL
        oid = c.execute("SELECT user_id FROM attachments WHERE id='a1'").fetchone()
        assert oid[0] is None

    # Now bootstrap
    from cli import cmd_bootstrap

    class A:
        username = "testadmin"
        password = "Bootpass1!"
    cmd_bootstrap(A())

    with api.db() as c:
        # state migrated
        row = c.execute("SELECT data FROM state WHERE user_id=(SELECT id FROM users WHERE username='testadmin')").fetchone()
        assert row is not None and "legacy" in row[0]
        # legacy table is gone
        assert c.execute("SELECT name FROM sqlite_master WHERE name='state_legacy'").fetchone() is None
        # attachment reassigned
        own = c.execute("SELECT user_id FROM attachments WHERE id='a1'").fetchone()
        assert own[0] is not None
