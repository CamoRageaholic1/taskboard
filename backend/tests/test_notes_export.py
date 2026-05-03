import io
import zipfile


def test_export_requires_auth(anon_client):
    assert anon_client.get("/api/notes/export").status_code == 401
    assert anon_client.get("/api/notes/export.zip").status_code == 401


def test_export_day_empty_returns_header_only(client):
    r = client.get("/api/notes/export?date=2026-05-03")
    assert r.status_code == 200
    assert "text/markdown" in r.headers["Content-Type"]
    assert 'filename="notes-2026-05-03.md"' in r.headers["Content-Disposition"]
    body = r.get_data(as_text=True)
    assert body.startswith("# Notes — 2026-05-03")
    assert "_(no notes)_" in body


def test_export_day_renders_each_note(client):
    client.post("/api/notes", json={"date": "2026-05-03", "title": "first",
                                    "body": "**hello**\n\n- a\n- b"})
    client.post("/api/notes", json={"date": "2026-05-03", "title": "second",
                                    "body": "more text"})
    body = client.get("/api/notes/export?date=2026-05-03").get_data(as_text=True)
    assert "# Notes — 2026-05-03" in body
    assert "## first" in body
    assert "## second" in body
    assert "**hello**" in body
    assert "more text" in body
    # Separator between notes
    assert "\n---\n" in body


def test_export_day_validates_date(client):
    assert client.get("/api/notes/export?date=bogus").status_code == 400


def test_export_zip_groups_by_date(client):
    client.post("/api/notes", json={"date": "2026-05-01", "title": "may1", "body": "a"})
    client.post("/api/notes", json={"date": "2026-05-02", "title": "may2a", "body": "b"})
    client.post("/api/notes", json={"date": "2026-05-02", "title": "may2b", "body": "c"})

    r = client.get("/api/notes/export.zip")
    assert r.status_code == 200
    assert r.headers["Content-Type"] == "application/zip"
    assert r.headers["Content-Disposition"].startswith('attachment; filename="taskboard-notes-')

    zf = zipfile.ZipFile(io.BytesIO(r.data))
    names = sorted(zf.namelist())
    assert "README.md" in names
    assert "notes/2026-05-01.md" in names
    assert "notes/2026-05-02.md" in names

    may2 = zf.read("notes/2026-05-02.md").decode("utf-8")
    assert "## may2a" in may2 and "## may2b" in may2

    readme = zf.read("README.md").decode("utf-8")
    assert "Total dates: 2" in readme


def test_export_zip_empty_user_still_valid_zip(client):
    r = client.get("/api/notes/export.zip")
    assert r.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(r.data))
    assert "README.md" in zf.namelist()
    assert "Total dates: 0" in zf.read("README.md").decode("utf-8")


def test_export_per_user_isolation(api):
    """alice's notes must not appear in bob's export."""
    from conftest import _login, _seed_users
    _seed_users(api)
    auth = __import__("auth")
    ts = api.now_iso()
    with api.db() as conn:
        conn.execute(
            "INSERT INTO users(username,password_hash,role,is_active,created_at) VALUES(?,?,?,1,?)",
            ("bob", auth.hash_password("Bobpass1!"), "user", ts),
        )
    with api.APP.test_client() as a:
        _login(a, "alice", "Alicepass1!")
        a.post("/api/notes", json={"date": "2026-05-03", "title": "alice-secret", "body": "x"})

    with api.APP.test_client() as b:
        _login(b, "bob", "Bobpass1!")
        # day export
        body = b.get("/api/notes/export?date=2026-05-03").get_data(as_text=True)
        assert "alice-secret" not in body
        # zip export
        z = zipfile.ZipFile(io.BytesIO(b.get("/api/notes/export.zip").data))
        for name in z.namelist():
            if name.startswith("notes/"):
                assert "alice-secret" not in z.read(name).decode("utf-8")
