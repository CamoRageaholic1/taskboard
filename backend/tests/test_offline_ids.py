"""Client-supplied ids for offline-created notes and images must be idempotent."""
import io


def test_create_note_with_client_id(client):
    r = client.post("/api/notes", json={"id": "abc12345", "title": "offline note", "body": "drafted offline"})
    assert r.status_code == 201, r.data
    assert r.json["id"] == "abc12345"
    assert r.json["title"] == "offline note"


def test_create_note_client_id_is_idempotent(client):
    payload = {"id": "dup-note-01", "title": "first", "body": "one"}
    r1 = client.post("/api/notes", json=payload)
    assert r1.status_code == 201

    # Replaying the queued create (same id) must NOT duplicate; returns existing.
    r2 = client.post("/api/notes", json=payload)
    assert r2.status_code == 200, r2.data
    assert r2.json["id"] == "dup-note-01"
    # Title unchanged (existing note returned, not overwritten by the replay)
    assert r2.json["title"] == "first"

    today = r1.json["date"]
    listing = client.get(f"/api/notes?date={today}").json
    assert sum(1 for n in listing if n["id"] == "dup-note-01") == 1


def test_create_note_rejects_bad_client_id(client):
    r = client.post("/api/notes", json={"id": "no spaces!", "title": "x"})
    assert r.status_code == 400


def test_upload_note_image_with_client_id_idempotent(client):
    note = client.post("/api/notes", json={"title": "with image"}).json
    nid = note["id"]
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 32

    def upload():
        return client.post(
            f"/api/notes/{nid}/images",
            data={"id": "att-offline-1", "file": (io.BytesIO(png), "sketch.png", "image/png")},
            content_type="multipart/form-data",
        )

    r1 = upload()
    assert r1.status_code == 201, r1.data
    assert r1.json["id"] == "att-offline-1"
    assert r1.json["url"] == "/api/attachments/att-offline-1"

    # Replaying the queued upload with the same id is idempotent (no duplicate row).
    r2 = upload()
    assert r2.status_code == 200, r2.data
    assert r2.json["id"] == "att-offline-1"

    # And the attachment is fetchable at the stable URL.
    got = client.get("/api/attachments/att-offline-1")
    assert got.status_code == 200
