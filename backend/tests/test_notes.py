def test_notes_require_auth(anon_client):
    assert anon_client.get("/api/notes").status_code == 401
    assert anon_client.post("/api/notes", json={}).status_code == 401


def test_create_list_update_delete_round_trip(client):
    # Empty to start
    assert client.get("/api/notes").json == []

    # Create one (defaults date to today)
    r = client.post("/api/notes", json={"title": "scratch", "body": "# hi\nworld"})
    assert r.status_code == 201
    nid = r.json["id"]
    assert r.json["title"] == "scratch"
    assert r.json["body"].startswith("# hi")
    assert r.json["date"]  # default date set

    # List for today returns it
    today = r.json["date"]
    r = client.get(f"/api/notes?date={today}")
    assert r.status_code == 200
    assert len(r.json) == 1
    assert r.json[0]["id"] == nid

    # Patch title only
    r = client.patch(f"/api/notes/{nid}", json={"title": "renamed"})
    assert r.status_code == 200
    assert r.json["title"] == "renamed"
    assert r.json["body"].startswith("# hi")

    # Patch body
    r = client.patch(f"/api/notes/{nid}", json={"body": "**bold**"})
    assert r.status_code == 200
    assert r.json["body"] == "**bold**"

    # Delete
    assert client.delete(f"/api/notes/{nid}").status_code == 200
    assert client.get(f"/api/notes?date={today}").json == []


def test_date_validation(client):
    assert client.get("/api/notes?date=not-a-date").status_code == 400
    assert client.post("/api/notes", json={"date": "2026/05/03"}).status_code == 400
    assert client.post("/api/notes", json={"date": "2026-05-03"}).status_code == 201


def test_multiple_notes_per_day_in_creation_order(client):
    a = client.post("/api/notes", json={"title": "first"}).json
    b = client.post("/api/notes", json={"title": "second"}).json
    c = client.post("/api/notes", json={"title": "third"}).json
    today = a["date"]
    titles = [n["title"] for n in client.get(f"/api/notes?date={today}").json]
    assert titles == ["first", "second", "third"]
    assert a["id"] != b["id"] != c["id"]


def test_dates_index(client):
    client.post("/api/notes", json={"date": "2026-01-15", "title": "a"})
    client.post("/api/notes", json={"date": "2026-01-15", "title": "b"})
    client.post("/api/notes", json={"date": "2026-02-20", "title": "c"})
    r = client.get("/api/notes/dates")
    assert r.status_code == 200
    by_date = {row["date"]: row["count"] for row in r.json}
    assert by_date["2026-01-15"] == 2
    assert by_date["2026-02-20"] == 1


def test_notes_per_user_isolation(api):
    """alice's notes must not be visible to bob."""
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
        n = a.post("/api/notes", json={"title": "alice-secret"}).json

    with api.APP.test_client() as b:
        _login(b, "bob", "Bobpass1!")
        assert b.get(f"/api/notes?date={n['date']}").json == []
        # bob can't update or delete it either
        assert b.patch(f"/api/notes/{n['id']}", json={"title": "x"}).status_code == 404
        assert b.delete(f"/api/notes/{n['id']}").status_code == 404


def test_payload_size_limits(client):
    huge = "x" * 60000
    r = client.post("/api/notes", json={"body": huge})
    assert r.status_code == 201
    assert len(r.json["body"]) == 50000  # truncated, not rejected


def test_empty_patch_is_400(client):
    n = client.post("/api/notes", json={"title": "t"}).json
    assert client.patch(f"/api/notes/{n['id']}", json={}).status_code == 400
