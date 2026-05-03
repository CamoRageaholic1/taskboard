def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json["status"] == "ok"


def test_state_round_trip(client):
    r = client.get("/api/data")
    assert r.status_code == 200
    assert r.json == {"data": None, "updated_at": None}

    payload = {"data": {"projects": [{"id": "p1", "name": "X", "tasks": []}],
                        "activeProjectId": "p1", "activeView": "project"}}
    r = client.post("/api/data", json=payload)
    assert r.status_code == 200
    assert r.json["ok"] is True

    r = client.get("/api/data")
    assert r.json["data"] == payload["data"]
    assert r.json["updated_at"] is not None


def test_state_validation(client):
    assert client.post("/api/data", json={}).status_code == 400
    assert client.post("/api/data", data="not json", content_type="text/plain").status_code == 400


def test_attachment_upload_list_download_delete(client, make_file):
    f = make_file("note.txt", b"contents-of-note", "text/plain")
    r = client.post("/api/attachments?task_id=t1",
                    data={"file": f},
                    content_type="multipart/form-data")
    assert r.status_code == 201, r.data
    att = r.json
    assert att["filename"] == "note.txt"
    assert att["size"] == len(b"contents-of-note")
    assert att["task_id"] == "t1"
    assert len(att["sha256"]) == 64

    r = client.get("/api/attachments?task_id=t1")
    assert r.status_code == 200
    assert len(r.json) == 1
    assert r.json[0]["id"] == att["id"]

    r = client.get(f"/api/attachments/{att['id']}")
    assert r.status_code == 200
    assert r.data == b"contents-of-note"

    r = client.delete(f"/api/attachments/{att['id']}")
    assert r.status_code == 200

    r = client.get("/api/attachments?task_id=t1")
    assert r.json == []


def test_attachment_dedup_by_sha(client, make_file):
    body = b"shared-bytes"
    a1 = client.post("/api/attachments?task_id=t1", data={"file": make_file("a.txt", body)},
                     content_type="multipart/form-data").json
    a2 = client.post("/api/attachments?task_id=t2", data={"file": make_file("b.txt", body)},
                     content_type="multipart/form-data").json
    assert a1["sha256"] == a2["sha256"]
    assert a1["id"] != a2["id"]

    # delete one — file blob should still be downloadable via the other
    client.delete(f"/api/attachments/{a1['id']}")
    r = client.get(f"/api/attachments/{a2['id']}")
    assert r.status_code == 200
    assert r.data == body


def test_attachment_too_large(client, make_file):
    big = b"x" * (64 * 1024 + 1)
    r = client.post("/api/attachments?task_id=t1", data={"file": make_file("big.bin", big)},
                    content_type="multipart/form-data")
    assert r.status_code in (413, 400)


def test_attachment_requires_task_id(client, make_file):
    r = client.post("/api/attachments", data={"file": make_file()},
                    content_type="multipart/form-data")
    assert r.status_code == 400


def test_backups_round_trip(client):
    payload = {"data": {"projects": [], "activeProjectId": None, "activeView": "all"},
               "source": "test", "note": "first"}
    r = client.post("/api/backups", json=payload)
    assert r.status_code == 201
    bid = r.json["id"]
    assert r.json["source"] == "test"

    r = client.get("/api/backups")
    assert r.status_code == 200
    assert any(b["id"] == bid for b in r.json)

    r = client.get(f"/api/backups/{bid}")
    assert r.status_code == 200
    assert r.json["data"] == payload["data"]

    assert client.delete(f"/api/backups/{bid}").status_code == 200
    assert client.get(f"/api/backups/{bid}").status_code == 404


def test_backup_snapshots_current_state_when_no_data(client):
    client.post("/api/data", json={"data": {"projects": [], "activeProjectId": None, "activeView": "all"}})
    r = client.post("/api/backups", json={"source": "auto"})
    assert r.status_code == 201


def test_backup_without_state_fails(client):
    r = client.post("/api/backups", json={"source": "auto"})
    assert r.status_code == 400
