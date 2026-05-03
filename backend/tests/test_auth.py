def test_unauthenticated_state_blocked(anon_client):
    assert anon_client.get("/api/data").status_code == 401
    assert anon_client.post("/api/data", json={"data": {}}).status_code == 401


def test_health_is_public(anon_client):
    assert anon_client.get("/api/health").status_code == 200


def test_session_endpoint_unauthenticated(anon_client):
    r = anon_client.get("/api/session")
    assert r.status_code == 200
    assert r.json["authenticated"] is False


def test_login_logout_round_trip(api):
    from conftest import _seed_users
    _seed_users(api)
    with api.APP.test_client() as c:
        # bad password
        assert c.post("/api/session", json={"username": "alice", "password": "wrong"}).status_code == 401
        # good
        r = c.post("/api/session", json={"username": "alice", "password": "Alicepass1!"})
        assert r.status_code == 200
        assert r.json["username"] == "alice"
        assert r.json["role"] == "user"
        # whoami
        r = c.get("/api/session")
        assert r.json["authenticated"] is True
        assert r.json["username"] == "alice"
        # logout
        assert c.delete("/api/session").status_code == 200
        assert c.get("/api/session").json["authenticated"] is False


def test_disabled_user_cannot_login(api):
    from conftest import _seed_users
    _seed_users(api)
    with api.db() as conn:
        conn.execute("UPDATE users SET is_active=0 WHERE username='alice'")
    with api.APP.test_client() as c:
        assert c.post("/api/session", json={"username": "alice", "password": "Alicepass1!"}).status_code == 401


def test_password_rules():
    from auth import password_problems
    assert password_problems("short!A") is not None
    assert password_problems("alllowercase1!") is not None
    assert password_problems("ALLUPPERCASE1!") is not None
    assert password_problems("NoSpecialChars1") is not None
    assert password_problems("Goodpass1!") is None


def test_generated_password_passes_rules():
    from auth import generate_password, password_problems
    for _ in range(50):
        assert password_problems(generate_password()) is None


def test_users_per_user_isolation(api, make_file):
    """alice's data must be invisible to bob and vice versa."""
    from conftest import _login, _seed_users
    _seed_users(api)
    auth = __import__("auth")
    ts = api.now_iso()
    with api.db() as conn:
        conn.execute(
            "INSERT INTO users(username,password_hash,role,is_active,created_at) VALUES(?,?,?,1,?)",
            ("bob", auth.hash_password("Bobpass1!"), "user", ts),
        )

    # alice writes state + uploads attachment
    with api.APP.test_client() as a:
        _login(a, "alice", "Alicepass1!")
        a.post("/api/data", json={"data": {"projects": [{"id": "p1", "name": "alice-only", "tasks": []}],
                                          "activeProjectId": "p1", "activeView": "project"}})
        att = a.post("/api/attachments?task_id=t1",
                     data={"file": make_file("a.txt", b"alice")},
                     content_type="multipart/form-data").json
        assert att["filename"] == "a.txt"

    # bob can't see alice's state, attachments, or download her file
    with api.APP.test_client() as b:
        _login(b, "bob", "Bobpass1!")
        assert b.get("/api/data").json == {"data": None, "updated_at": None}
        assert b.get("/api/attachments?task_id=t1").json == []
        assert b.get(f"/api/attachments/{att['id']}").status_code == 403
        assert b.delete(f"/api/attachments/{att['id']}").status_code == 403


def test_admin_can_access_other_users_attachments(admin_client, api, make_file):
    """Admin can read/delete any user's data (intentional — for support)."""
    from conftest import _login
    with api.APP.test_client() as a:
        _login(a, "alice", "Alicepass1!")
        att = a.post("/api/attachments?task_id=t1",
                     data={"file": make_file("a.txt", b"hi")},
                     content_type="multipart/form-data").json

    assert admin_client.get(f"/api/attachments/{att['id']}").status_code == 200
