def _seed_state(client, projects):
    client.post("/api/data", json={"data": {
        "projects": projects, "activeProjectId": projects[0]["id"] if projects else None,
        "activeView": "project",
    }})


def test_search_requires_auth(anon_client):
    assert anon_client.get("/api/search?q=foo").status_code == 401


def test_short_query_returns_empty(client):
    r = client.get("/api/search?q=a")
    assert r.status_code == 200
    assert r.json == {"results": []}


def test_finds_task_by_title(client):
    _seed_state(client, [{"id": "p1", "name": "Work", "color": "#aaa", "createdAt": 1, "tasks": [
        {"id": "t1", "title": "Refactor billing", "completed": False, "priority": "med",
         "dueDate": "", "description": "", "subtasks": []},
        {"id": "t2", "title": "Write docs", "completed": False, "priority": "low",
         "dueDate": "", "description": "", "subtasks": []},
    ]}])
    r = client.get("/api/search?q=billing")
    hits = r.json["results"]
    assert any(h["kind"] == "task" and h["id"] == "t1" for h in hits)
    assert not any(h["id"] == "t2" for h in hits)


def test_finds_task_by_description(client):
    _seed_state(client, [{"id": "p1", "name": "X", "color": "#aaa", "createdAt": 1, "tasks": [
        {"id": "t1", "title": "Spike", "completed": False, "priority": "high",
         "dueDate": "", "description": "Investigate qdrant for vector search", "subtasks": []},
    ]}])
    hits = client.get("/api/search?q=qdrant").json["results"]
    assert any(h["kind"] == "task" and h["id"] == "t1" for h in hits)
    # snippet should include the matched word
    task_hit = next(h for h in hits if h["id"] == "t1")
    assert "qdrant" in task_hit["snippet"].lower()


def test_finds_subtask(client):
    _seed_state(client, [{"id": "p1", "name": "X", "color": "#aaa", "createdAt": 1, "tasks": [
        {"id": "t1", "title": "Parent", "completed": False, "priority": "med",
         "dueDate": "", "description": "", "subtasks": [
            {"id": "s1", "title": "Implement bm25 ranker", "done": False},
         ]},
    ]}])
    hits = client.get("/api/search?q=bm25").json["results"]
    assert any(h["kind"] == "subtask" and h["id"] == "s1" for h in hits)


def test_finds_project_by_name(client):
    _seed_state(client, [{"id": "p1", "name": "Marketing site", "color": "#aaa",
                          "createdAt": 1, "tasks": []}])
    hits = client.get("/api/search?q=marketing").json["results"]
    assert any(h["kind"] == "project" and h["id"] == "p1" for h in hits)


def test_finds_note(client):
    client.post("/api/notes", json={"title": "weekly review", "body": "discussed roadmap with team"})
    hits = client.get("/api/search?q=roadmap").json["results"]
    assert any(h["kind"] == "note" and "roadmap" in h["snippet"].lower() for h in hits)


def test_per_user_isolation(api):
    """alice's content must not leak into bob's search results."""
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
            "projects": [{"id": "p1", "name": "alice-only-marker", "color": "#aaa",
                          "createdAt": 1, "tasks": []}],
            "activeProjectId": "p1", "activeView": "project",
        }})
    with api.APP.test_client() as b:
        _login(b, "bob", "Bobpass1!")
        hits = b.get("/api/search?q=marker").json["results"]
        assert hits == []


def test_case_insensitive(client):
    _seed_state(client, [{"id": "p1", "name": "X", "color": "#aaa", "createdAt": 1, "tasks": [
        {"id": "t1", "title": "BUDGET review", "completed": False, "priority": "med",
         "dueDate": "", "description": "", "subtasks": []},
    ]}])
    assert any(h["id"] == "t1" for h in client.get("/api/search?q=budget").json["results"])
    assert any(h["id"] == "t1" for h in client.get("/api/search?q=BuDgEt").json["results"])
