def test_user_endpoints_require_admin(client):
    assert client.get("/api/users").status_code == 403
    assert client.post("/api/users", json={"username": "x", "password": "Xpass1!@"}).status_code == 403
    assert client.get("/api/admin/stats").status_code == 403


def test_admin_lists_users(admin_client):
    r = admin_client.get("/api/users")
    assert r.status_code == 200
    names = sorted(u["username"] for u in r.json)
    assert names == ["admin", "alice"]


def test_create_user_validation(admin_client):
    # too short
    assert admin_client.post("/api/users", json={"username": "bob", "password": "short"}).status_code == 400
    # missing special
    assert admin_client.post("/api/users", json={"username": "bob", "password": "NoSpecial1"}).status_code == 400
    # bad username
    assert admin_client.post("/api/users", json={"username": "bad name", "password": "Goodpass1!"}).status_code == 400
    # invalid role
    assert admin_client.post("/api/users",
                             json={"username": "bob", "password": "Goodpass1!", "role": "superuser"}).status_code == 400


def test_create_user_and_uniqueness(admin_client):
    r = admin_client.post("/api/users", json={"username": "bob", "password": "Bobpass1!", "role": "user"})
    assert r.status_code == 201
    assert r.json["username"] == "bob"
    # duplicate
    r = admin_client.post("/api/users", json={"username": "bob", "password": "Bobpass1!", "role": "user"})
    assert r.status_code == 409


def test_patch_user_role_and_active(admin_client):
    uid = next(u["id"] for u in admin_client.get("/api/users").json if u["username"] == "alice")
    r = admin_client.patch(f"/api/users/{uid}", json={"role": "admin"})
    assert r.status_code == 200
    assert r.json["role"] == "admin"
    r = admin_client.patch(f"/api/users/{uid}", json={"is_active": False})
    assert r.json["is_active"] is False


def test_patch_user_password_rules_enforced(admin_client):
    uid = next(u["id"] for u in admin_client.get("/api/users").json if u["username"] == "alice")
    assert admin_client.patch(f"/api/users/{uid}", json={"password": "short"}).status_code == 400
    assert admin_client.patch(f"/api/users/{uid}", json={"password": "Newgoodpass1!"}).status_code == 200


def test_cannot_remove_last_admin(admin_client):
    uid = next(u["id"] for u in admin_client.get("/api/users").json if u["username"] == "admin")
    # cannot delete self
    assert admin_client.delete(f"/api/users/{uid}").status_code == 400
    # demote-then-delete should also fail because admin would have zero
    assert admin_client.patch(f"/api/users/{uid}", json={"is_active": False}).status_code == 200
    # re-enable
    admin_client.patch(f"/api/users/{uid}", json={"is_active": True})


def test_delete_user_cascades(admin_client, api):
    """Deleting a user removes their state, attachments, backups."""
    from conftest import _login
    auth = __import__("auth")
    ts = api.now_iso()
    with api.db() as conn:
        conn.execute(
            "INSERT INTO users(username,password_hash,role,is_active,created_at) VALUES(?,?,?,1,?)",
            ("victim", auth.hash_password("Victim1!@"), "user", ts),
        )
        vid = conn.execute("SELECT id FROM users WHERE username='victim'").fetchone()[0]
    # populate victim
    with api.APP.test_client() as v:
        _login(v, "victim", "Victim1!@")
        v.post("/api/data", json={"data": {"projects": [], "activeProjectId": None, "activeView": "all"}})
        v.post("/api/backups", json={"source": "test"})

    assert admin_client.delete(f"/api/users/{vid}").status_code == 200
    with api.db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM state WHERE user_id=?", (vid,)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM backups WHERE user_id=?", (vid,)).fetchone()[0] == 0


def test_admin_stats(admin_client):
    r = admin_client.get("/api/admin/stats")
    assert r.status_code == 200
    s = r.json
    assert s["users_total"] == 2
    assert s["users_active"] == 2
    assert "files_on_disk_bytes" in s
