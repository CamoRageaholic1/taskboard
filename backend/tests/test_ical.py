def test_token_endpoints_require_auth(anon_client):
    assert anon_client.get("/api/feed/token").status_code == 401
    assert anon_client.post("/api/feed/token").status_code == 401


def test_get_token_creates_one_if_missing(client):
    r = client.get("/api/feed/token")
    assert r.status_code == 200
    t = r.json["token"]
    assert len(t) >= 16
    assert r.json["url"] == f"/api/calendar/{t}.ics"
    # Subsequent GETs return the same token (idempotent)
    assert client.get("/api/feed/token").json["token"] == t


def test_rotate_returns_new_token(client):
    t1 = client.get("/api/feed/token").json["token"]
    t2 = client.post("/api/feed/token").json["token"]
    assert t1 != t2
    # Old token no longer works
    assert client.get(f"/api/calendar/{t1}.ics").status_code == 404


def test_calendar_feed_renders_open_due_tasks(client):
    client.post("/api/data", json={"data": {
        "projects": [{"id": "p1", "name": "Work", "color": "#aaa", "createdAt": 1, "tasks": [
            {"id": "t1", "title": "Ship Q2 plan", "completed": False, "priority": "high",
             "dueDate": "2026-06-01", "description": "Include OKRs", "subtasks": []},
            {"id": "t2", "title": "Done already", "completed": True, "priority": "low",
             "dueDate": "2026-05-01", "description": "", "subtasks": []},
            {"id": "t3", "title": "No due date", "completed": False, "priority": "med",
             "dueDate": "", "description": "", "subtasks": []},
        ]}],
        "activeProjectId": "p1", "activeView": "project",
    }})
    token = client.get("/api/feed/token").json["token"]
    r = client.get(f"/api/calendar/{token}.ics")
    assert r.status_code == 200
    assert "text/calendar" in r.headers["Content-Type"]
    body = r.get_data(as_text=True)
    # iCal envelope
    assert body.startswith("BEGIN:VCALENDAR")
    assert body.rstrip("\r\n").endswith("END:VCALENDAR")
    # CRLF line endings per RFC 5545
    assert "\r\n" in body
    # Only t1 should appear
    assert "Ship Q2 plan" in body
    assert "Done already" not in body
    assert "No due date" not in body
    # All-day event with proper dates
    assert "DTSTART;VALUE=DATE:20260601" in body
    assert "DTEND;VALUE=DATE:20260602" in body
    # Description carries project + priority + body
    assert "Project: Work" in body
    assert "Priority: high" in body
    assert "Include OKRs" in body


def test_unknown_token_404(anon_client):
    assert anon_client.get("/api/calendar/short.ics").status_code == 404
    assert anon_client.get("/api/calendar/" + "x" * 32 + ".ics").status_code == 404


def test_disabled_user_feed_404(api):
    from conftest import _login, _seed_users
    _seed_users(api)
    with api.APP.test_client() as a:
        _login(a, "alice", "Alicepass1!")
        token = a.get("/api/feed/token").json["token"]
    # Disable alice
    with api.db() as conn:
        conn.execute("UPDATE users SET is_active=0 WHERE username='alice'")
    with api.APP.test_client() as anon:
        assert anon.get(f"/api/calendar/{token}.ics").status_code == 404


def test_per_user_feeds_are_isolated(api):
    """alice's tasks must not appear in bob's feed."""
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
        a.post("/api/data", json={"data": {
            "projects": [{"id": "pa", "name": "A", "color": "#a", "createdAt": 1, "tasks": [
                {"id": "ta", "title": "alice-task", "completed": False, "priority": "med",
                 "dueDate": "2026-06-01", "description": "", "subtasks": []},
            ]}],
            "activeProjectId": "pa", "activeView": "project",
        }})
        ta = a.get("/api/feed/token").json["token"]

    with api.APP.test_client() as b:
        _login(b, "bob", "Bobpass1!")
        b.post("/api/data", json={"data": {
            "projects": [{"id": "pb", "name": "B", "color": "#b", "createdAt": 1, "tasks": [
                {"id": "tb", "title": "bob-task", "completed": False, "priority": "med",
                 "dueDate": "2026-06-02", "description": "", "subtasks": []},
            ]}],
            "activeProjectId": "pb", "activeView": "project",
        }})
        tb = b.get("/api/feed/token").json["token"]

    with api.APP.test_client() as anon:
        afeed = anon.get(f"/api/calendar/{ta}.ics").get_data(as_text=True)
        bfeed = anon.get(f"/api/calendar/{tb}.ics").get_data(as_text=True)
        assert "alice-task" in afeed and "bob-task" not in afeed
        assert "bob-task" in bfeed and "alice-task" not in bfeed
