"""Admin CLI for taskboard.

Usage:
  python -m cli bootstrap [--username NAME] [--password PASS]
  python -m cli user list
  python -m cli user add USERNAME [--admin] [--password PASS]
  python -m cli user passwd USERNAME [--password PASS]
  python -m cli user role USERNAME admin|user
  python -m cli user disable USERNAME
  python -m cli user enable USERNAME
"""
import argparse
import contextlib
import sqlite3
import sys

# Import after env is read
import api  # noqa: E402
from auth import generate_password, hash_password, password_problems
from migrate import migrate


def _conn():
    return api.db()


def cmd_bootstrap(args):
    with _conn() as c:
        migrate(c)
        n = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        if n > 0:
            print("[bootstrap] users already exist; nothing to do")
            return 0
        username = (args.username or "admin").strip().lower()
        password = args.password or generate_password()
        err = password_problems(password)
        if err:
            print(f"[bootstrap] password rejected: {err}", file=sys.stderr)
            return 2
        ph = hash_password(password)
        ts = api.now_iso()
        cur = c.execute(
            "INSERT INTO users(username,password_hash,role,is_active,created_at) VALUES(?,?,?,1,?)",
            (username, ph, "admin", ts),
        )
        uid = cur.lastrowid
        # Migrate legacy single-row state if present
        legacy = None
        with contextlib.suppress(sqlite3.OperationalError):
            legacy = c.execute("SELECT data, updated_at FROM state_legacy WHERE id=1").fetchone()
        if legacy:
            c.execute(
                "INSERT INTO state(user_id,data,updated_at) VALUES(?,?,?)",
                (uid, legacy[0], legacy[1]),
            )
            c.execute("DROP TABLE state_legacy")
            print(f"[bootstrap] migrated legacy state to user '{username}' (uid={uid})")
        # Reassign orphaned attachments / backups
        for tbl in ("attachments", "backups"):
            n = c.execute(f"UPDATE {tbl} SET user_id=? WHERE user_id IS NULL", (uid,)).rowcount
            if n:
                print(f"[bootstrap] reassigned {n} legacy rows in {tbl}")
        print(f"[bootstrap] admin user: {username}")
        print(f"[bootstrap] password:   {password}")
        print("[bootstrap] *** save the password — it is not shown again ***")
    return 0


def cmd_user_list(args):
    with _conn() as c:
        rows = c.execute(
            "SELECT id, username, role, is_active, created_at, last_login_at FROM users ORDER BY id"
        ).fetchall()
    if not rows:
        print("(no users)")
        return 0
    print(f"{'id':>3}  {'username':<20} {'role':<6} active  created                          last_login")
    for r in rows:
        print(f"{r[0]:>3}  {r[1]:<20} {r[2]:<6} {bool(r[3]):<6}  {r[4]}  {r[5] or '-'}")
    return 0


def _err(msg):
    print(msg, file=sys.stderr)
    return 2


def cmd_user_add(args):
    username = args.username.strip().lower()
    password = args.password or generate_password()
    err = password_problems(password)
    if err:
        return _err(f"password rejected: {err}")
    role = "admin" if args.admin else "user"
    ph = hash_password(password)
    ts = api.now_iso()
    try:
        with _conn() as c:
            cur = c.execute(
                "INSERT INTO users(username,password_hash,role,is_active,created_at) VALUES(?,?,?,1,?)",
                (username, ph, role, ts),
            )
            uid = cur.lastrowid
    except sqlite3.IntegrityError:
        return _err(f"username '{username}' already taken")
    print(f"created user '{username}' (uid={uid}, role={role})")
    print(f"password: {password}")
    return 0


def cmd_user_passwd(args):
    password = args.password or generate_password()
    err = password_problems(password)
    if err:
        return _err(f"password rejected: {err}")
    ph = hash_password(password)
    with _conn() as c:
        cur = c.execute(
            "UPDATE users SET password_hash=? WHERE username=?",
            (ph, args.username.strip().lower()),
        )
    if cur.rowcount == 0:
        return _err(f"user '{args.username}' not found")
    print(f"password for '{args.username}' set to: {password}")
    return 0


def cmd_user_role(args):
    if args.role not in ("admin", "user"):
        return _err("role must be admin|user")
    with _conn() as c:
        cur = c.execute(
            "UPDATE users SET role=? WHERE username=?",
            (args.role, args.username.strip().lower()),
        )
    if cur.rowcount == 0:
        return _err(f"user '{args.username}' not found")
    print(f"role for '{args.username}' -> {args.role}")
    return 0


def _set_active(username, val):
    with _conn() as c:
        cur = c.execute(
            "UPDATE users SET is_active=? WHERE username=?",
            (val, username.strip().lower()),
        )
    return cur.rowcount


def cmd_user_disable(args):
    if _set_active(args.username, 0) == 0:
        return _err(f"user '{args.username}' not found")
    print(f"disabled '{args.username}'")
    return 0


def cmd_user_enable(args):
    if _set_active(args.username, 1) == 0:
        return _err(f"user '{args.username}' not found")
    print(f"enabled '{args.username}'")
    return 0


def main():
    p = argparse.ArgumentParser(prog="taskboard-cli")
    sub = p.add_subparsers(dest="cmd", required=True)

    pb = sub.add_parser("bootstrap", help="initialize admin user (idempotent)")
    pb.add_argument("--username", default="admin")
    pb.add_argument("--password", default=None)
    pb.set_defaults(fn=cmd_bootstrap)

    pu = sub.add_parser("user")
    sub_u = pu.add_subparsers(dest="usercmd", required=True)

    sl = sub_u.add_parser("list")
    sl.set_defaults(fn=cmd_user_list)

    sa = sub_u.add_parser("add")
    sa.add_argument("username")
    sa.add_argument("--admin", action="store_true")
    sa.add_argument("--password", default=None)
    sa.set_defaults(fn=cmd_user_add)

    sp = sub_u.add_parser("passwd")
    sp.add_argument("username")
    sp.add_argument("--password", default=None)
    sp.set_defaults(fn=cmd_user_passwd)

    sr = sub_u.add_parser("role")
    sr.add_argument("username")
    sr.add_argument("role")
    sr.set_defaults(fn=cmd_user_role)

    sd = sub_u.add_parser("disable")
    sd.add_argument("username")
    sd.set_defaults(fn=cmd_user_disable)

    se = sub_u.add_parser("enable")
    se.add_argument("username")
    se.set_defaults(fn=cmd_user_enable)

    args = p.parse_args()
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()
