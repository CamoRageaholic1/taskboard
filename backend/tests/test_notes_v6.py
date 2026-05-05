"""Tests for v6 features: pinning, reorder, notebooks, tags, backlinks, image upload."""
import io


def test_create_with_pin_and_toggle(client):
    n = client.post("/api/notes", json={"title": "pinned", "pinned": True}).json
    assert n["pinned"] is True
    listed = client.get(f"/api/notes?date={n['date']}").json
    assert listed[0]["id"] == n["id"]
    # Unpin
    r = client.patch(f"/api/notes/{n['id']}", json={"pinned": False})
    assert r.status_code == 200 and r.json["pinned"] is False


def test_pinned_sort_first(client):
    a = client.post("/api/notes", json={"title": "a"}).json
    b = client.post("/api/notes", json={"title": "b"}).json
    client.post("/api/notes", json={"title": "c"})
    client.patch(f"/api/notes/{b['id']}", json={"pinned": True})
    titles = [n["title"] for n in client.get(f"/api/notes?date={a['date']}").json]
    assert titles[0] == "b"  # pinned first
    assert set(titles[1:]) == {"a", "c"}


def test_reorder_endpoint(client):
    a = client.post("/api/notes", json={"title": "a"}).json
    b = client.post("/api/notes", json={"title": "b"}).json
    c = client.post("/api/notes", json={"title": "c"}).json
    today = a["date"]
    # Reverse the order
    r = client.post("/api/notes/reorder", json={"ids": [c["id"], b["id"], a["id"]]})
    assert r.status_code == 200
    titles = [n["title"] for n in client.get(f"/api/notes?date={today}").json]
    assert titles == ["c", "b", "a"]


def test_reorder_rejects_non_owner(api):
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
        n = a.post("/api/notes", json={"title": "alice"}).json
    with api.APP.test_client() as b:
        _login(b, "bob", "Bobpass1!")
        bn = b.post("/api/notes", json={"title": "bob"}).json
        # Bob includes one of his own + alice's id
        r = b.post("/api/notes/reorder", json={"ids": [bn["id"], n["id"]]})
        assert r.status_code == 404


def test_notebooks_crud(client):
    # Empty
    assert client.get("/api/notebooks").json == []
    # Create
    nb = client.post("/api/notebooks", json={"name": "Project X", "color": "#5c7a3a"}).json
    assert nb["name"] == "Project X"
    assert nb["color"] == "#5c7a3a"
    assert nb["count"] == 0
    # List
    listed = client.get("/api/notebooks").json
    assert len(listed) == 1 and listed[0]["id"] == nb["id"]
    # Patch
    r = client.patch(f"/api/notebooks/{nb['id']}", json={"name": "Renamed"})
    assert r.status_code == 200 and r.json["name"] == "Renamed"
    # Delete
    assert client.delete(f"/api/notebooks/{nb['id']}").status_code == 200
    assert client.get("/api/notebooks").json == []


def test_notebook_notes_isolated_from_daily(client):
    nb = client.post("/api/notebooks", json={"name": "Reading"}).json
    n_daily = client.post("/api/notes", json={"title": "daily"}).json
    client.post("/api/notes", json={"title": "book", "notebook_id": nb["id"]})
    # Default daily list excludes notebook notes
    titles = [n["title"] for n in client.get(f"/api/notes?date={n_daily['date']}").json]
    assert "daily" in titles
    assert "book" not in titles
    # Notebook list has only the notebook's notes
    titles = [n["title"] for n in client.get(f"/api/notes?notebook_id={nb['id']}").json]
    assert titles == ["book"]
    # Counter
    nb_after = client.get("/api/notebooks").json[0]
    assert nb_after["count"] == 1


def test_delete_notebook_orphans_notes(client):
    nb = client.post("/api/notebooks", json={"name": "Reading"}).json
    n = client.post("/api/notes", json={"title": "ghost", "notebook_id": nb["id"]}).json
    client.delete(f"/api/notebooks/{nb['id']}")
    # Note still exists, notebook_id cleared
    g = client.get(f"/api/notes/{n['id']}").json
    assert g["title"] == "ghost"
    assert g["notebook_id"] is None


def test_all_notes_view(client):
    client.post("/api/notes", json={"date": "2026-01-01", "title": "a"})
    client.post("/api/notes", json={"date": "2026-02-01", "title": "b"})
    nb = client.post("/api/notebooks", json={"name": "Inbox"}).json
    client.post("/api/notes", json={"title": "c", "notebook_id": nb["id"]})
    titles = sorted(n["title"] for n in client.get("/api/notes?all=1").json)
    assert titles == ["a", "b", "c"]


def test_tags_index(client):
    client.post("/api/notes", json={"title": "x", "body": "#alpha #beta and #ALPHA again"})
    client.post("/api/notes", json={"title": "y", "body": "see #beta"})
    tags = {row["tag"]: row["count"] for row in client.get("/api/notes/tags").json}
    assert tags == {"alpha": 1, "beta": 2}


def test_filter_by_tag(client):
    client.post("/api/notes", json={"title": "a", "body": "hello #idea world"})
    client.post("/api/notes", json={"title": "b", "body": "no tag here"})
    titles = [n["title"] for n in client.get("/api/notes?tag=idea").json]
    assert titles == ["a"]


def test_backlinks(client):
    target = client.post("/api/notes", json={"title": "Daily Standup"}).json
    linker_a = client.post("/api/notes", json={"title": "linker A",
                                               "body": "context: see [[Daily Standup]] today"}).json
    linker_no = client.post("/api/notes", json={"title": "linker B", "body": "no link"}).json
    bl = client.get(f"/api/notes/{target['id']}/backlinks").json
    ids = {n["id"] for n in bl}
    assert linker_a["id"] in ids
    assert linker_no["id"] not in ids


def test_find_by_title(client):
    n = client.post("/api/notes", json={"title": "Architecture Decisions"}).json
    r = client.get("/api/notes/by-title?title=architecture decisions")  # case-insensitive
    assert r.status_code == 200
    assert r.json["id"] == n["id"]
    assert client.get("/api/notes/by-title?title=does-not-exist").status_code == 404


def test_titles_endpoint(client):
    client.post("/api/notes", json={"title": "Alpha"})
    client.post("/api/notes", json={"title": "Beta"})
    client.post("/api/notes", json={"title": ""})
    titles = [t["title"] for t in client.get("/api/notes/titles").json]
    assert "Alpha" in titles and "Beta" in titles
    assert "" not in titles  # untitled excluded


def test_duplicate_note(client):
    n = client.post("/api/notes", json={"title": "Source", "body": "stuff"}).json
    r = client.post(f"/api/notes/{n['id']}/duplicate")
    assert r.status_code == 201
    assert r.json["title"] == "Source (copy)"
    assert r.json["body"] == "stuff"
    assert r.json["id"] != n["id"]


def test_get_single_note(client):
    n = client.post("/api/notes", json={"title": "x"}).json
    r = client.get(f"/api/notes/{n['id']}")
    assert r.status_code == 200 and r.json["id"] == n["id"]
    assert client.get("/api/notes/zzzzzzz").status_code == 404


def test_image_upload_for_note(client):
    n = client.post("/api/notes", json={"title": "img"}).json
    # 1x1 PNG
    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
        "890000000d49444154789c63600100000005000160e94e1a0000000049454e44ae426082"
    )
    r = client.post(
        f"/api/notes/{n['id']}/images",
        data={"file": (io.BytesIO(png), "tiny.png", "image/png")},
        content_type="multipart/form-data",
    )
    assert r.status_code == 201
    assert r.json["url"].startswith("/api/attachments/")
    assert r.json["mime"] == "image/png"


def test_image_rejects_non_image(client):
    n = client.post("/api/notes", json={"title": "img"}).json
    r = client.post(
        f"/api/notes/{n['id']}/images",
        data={"file": (io.BytesIO(b"hello"), "x.txt", "text/plain")},
        content_type="multipart/form-data",
    )
    assert r.status_code == 400


def test_notebook_per_user_isolation(api):
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
        nb = a.post("/api/notebooks", json={"name": "alice's"}).json
    with api.APP.test_client() as b:
        _login(b, "bob", "Bobpass1!")
        assert b.get("/api/notebooks").json == []
        # Can't write to alice's notebook
        r = b.post("/api/notes", json={"title": "x", "notebook_id": nb["id"]})
        assert r.status_code == 404
        # Can't patch alice's notebook
        assert b.patch(f"/api/notebooks/{nb['id']}", json={"name": "x"}).status_code == 404
