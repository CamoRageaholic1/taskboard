import importlib
import io
import os
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))


@pytest.fixture
def client(tmp_path, monkeypatch):
    db = tmp_path / "data.db"
    files = tmp_path / "files"
    monkeypatch.setenv("TASKBOARD_DB_PATH", str(db))
    monkeypatch.setenv("TASKBOARD_FILES_DIR", str(files))
    monkeypatch.setenv("TASKBOARD_MAX_UPLOAD_BYTES", str(64 * 1024))  # 64 KB cap for tests

    # Force a fresh import so module-level constants pick up env vars
    if "api" in sys.modules:
        del sys.modules["api"]
    api = importlib.import_module("api")
    api.APP.config["TESTING"] = True
    with api.APP.test_client() as c:
        yield c


@pytest.fixture
def make_file():
    def _make(name="hello.txt", contents=b"hello world", mime="text/plain"):
        return (io.BytesIO(contents), name, mime)
    return _make
