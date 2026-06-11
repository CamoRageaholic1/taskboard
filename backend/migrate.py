"""Idempotent SQLite schema migrations."""
import sqlite3

SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS state (
  id INTEGER PRIMARY KEY CHECK(id=1),
  data TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS attachments (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL,
  filename TEXT NOT NULL,
  mime TEXT NOT NULL,
  size INTEGER NOT NULL,
  sha256 TEXT NOT NULL,
  uploaded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_attachments_task ON attachments(task_id);
CREATE TABLE IF NOT EXISTS backups (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  data TEXT NOT NULL,
  size INTEGER NOT NULL,
  source TEXT NOT NULL,
  note TEXT,
  created_at TEXT NOT NULL
);
"""


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _has_table(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def _schema_version(conn: sqlite3.Connection) -> int:
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    return int(row[0]) if row else 0


def _set_schema_version(conn: sqlite3.Connection, v: int) -> None:
    conn.execute(
        "INSERT INTO meta(key,value) VALUES('schema_version',?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(v),),
    )


def migrate(conn: sqlite3.Connection) -> int:
    """Run any pending migrations. Returns the new schema version."""
    version = _schema_version(conn)

    if version < 1:
        conn.executescript(SCHEMA_V1)
        _set_schema_version(conn, 1)
        version = 1

    if version < 2:
        # v1 -> v2: introduce users + per-user scoping
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          username TEXT NOT NULL UNIQUE,
          password_hash TEXT NOT NULL,
          role TEXT NOT NULL CHECK(role IN ('admin','user')),
          is_active INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL,
          last_login_at TEXT
        );
        """)

        # state: rebuild as user-keyed
        if _has_table(conn, "state"):
            cols = _table_columns(conn, "state")
            if "user_id" not in cols:
                n = conn.execute("SELECT COUNT(*) FROM state").fetchone()[0]
                if n == 0:
                    conn.execute("DROP TABLE state")
                else:
                    conn.execute("ALTER TABLE state RENAME TO state_legacy")
                conn.execute("""
                CREATE TABLE state (
                  user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                  data TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                )
                """)

        # attachments + backups: add nullable user_id (bootstrap will fill in)
        for tbl in ("attachments", "backups"):
            if _has_table(conn, tbl) and "user_id" not in _table_columns(conn, tbl):
                conn.execute(f"ALTER TABLE {tbl} ADD COLUMN user_id INTEGER REFERENCES users(id)")

        conn.execute("CREATE INDEX IF NOT EXISTS idx_attachments_user ON attachments(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_backups_user ON backups(user_id)")
        _set_schema_version(conn, 2)
        version = 2

    if version < 3:
        # v2 -> v3: daily notes (fast capture pad: many notes per day, markdown body)
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS notes (
          id TEXT PRIMARY KEY,
          user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          date TEXT NOT NULL,
          title TEXT NOT NULL DEFAULT '',
          body TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_notes_user_date ON notes(user_id, date);
        """)
        _set_schema_version(conn, 3)
        version = 3

    if version < 4:
        # v3 -> v4: per-user feed_token for unauthenticated iCal subscription URLs
        if "feed_token" not in _table_columns(conn, "users"):
            conn.execute("ALTER TABLE users ADD COLUMN feed_token TEXT")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_feed_token ON users(feed_token) WHERE feed_token IS NOT NULL")
        _set_schema_version(conn, 4)
        version = 4

    if version < 5:
        # v4 -> v5: TOTP-based 2FA per user
        cols = _table_columns(conn, "users")
        if "totp_secret" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN totp_secret TEXT")
        if "totp_enabled" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN totp_enabled INTEGER NOT NULL DEFAULT 0")
        _set_schema_version(conn, 5)
        version = 5

    if version < 6:
        # v5 -> v6: notebooks + per-note pinning / ordering / notebook membership
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS notebooks (
          id TEXT PRIMARY KEY,
          user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          name TEXT NOT NULL,
          color TEXT NOT NULL DEFAULT '#c1542a',
          icon TEXT NOT NULL DEFAULT 'book',
          sort_order INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_notebooks_user ON notebooks(user_id);
        """)
        ncols = _table_columns(conn, "notes")
        if "pinned" not in ncols:
            conn.execute("ALTER TABLE notes ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0")
        if "sort_order" not in ncols:
            conn.execute("ALTER TABLE notes ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0")
        if "notebook_id" not in ncols:
            conn.execute("ALTER TABLE notes ADD COLUMN notebook_id TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_notes_user_notebook ON notes(user_id, notebook_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_notes_user_pinned ON notes(user_id, pinned)")
        _set_schema_version(conn, 6)
        version = 6

    if version < 7:
        # v6 -> v7: link a note to a board project. project_id references the
        # client-side board blob's project id (no FK — the board is opaque JSON
        # in the state table), so it's just an indexed text tag.
        if "project_id" not in _table_columns(conn, "notes"):
            conn.execute("ALTER TABLE notes ADD COLUMN project_id TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_notes_user_project ON notes(user_id, project_id)")
        _set_schema_version(conn, 7)
        version = 7

    if version < 8:
        # v7 -> v8: link a note to a specific board task (task ids also live in
        # the opaque board blob, so this is an indexed text tag like project_id).
        if "task_id" not in _table_columns(conn, "notes"):
            conn.execute("ALTER TABLE notes ADD COLUMN task_id TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_notes_user_task ON notes(user_id, task_id)")
        _set_schema_version(conn, 8)
        version = 8

    return version
