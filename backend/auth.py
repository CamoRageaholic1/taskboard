"""Password hashing, validation, and session helpers."""
import secrets
import string
from functools import wraps

import bcrypt
from flask import jsonify, session

SPECIAL_CHARS = "!@#$%^&*()-_=+[]{}|;:,.<>?/~"


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def password_problems(p: str) -> str | None:
    """Return None if OK, else a human-readable reason."""
    if not isinstance(p, str):
        return "password must be a string"
    if len(p) < 8:
        return "password must be at least 8 characters"
    if not any(c.isupper() for c in p):
        return "password must include an uppercase letter"
    if not any(c.islower() for c in p):
        return "password must include a lowercase letter"
    if not any(c in SPECIAL_CHARS for c in p):
        return "password must include a special character"
    return None


def generate_password(length: int = 16) -> str:
    """Cryptographically random password that passes password_problems."""
    if length < 8:
        length = 8
    rng = secrets.SystemRandom()
    pool = string.ascii_letters + string.digits + SPECIAL_CHARS
    while True:
        chars = [
            secrets.choice(string.ascii_uppercase),
            secrets.choice(string.ascii_lowercase),
            secrets.choice(string.digits),
            secrets.choice(SPECIAL_CHARS),
        ]
        chars += [secrets.choice(pool) for _ in range(length - len(chars))]
        rng.shuffle(chars)
        candidate = "".join(chars)
        if password_problems(candidate) is None:
            return candidate


def current_user_id() -> int | None:
    return session.get("uid")


def current_user_role() -> str | None:
    return session.get("role")


def login_user(uid: int, role: str) -> None:
    session.clear()
    session["uid"] = uid
    session["role"] = role
    session.permanent = True


def logout_user() -> None:
    session.clear()


def require_user(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if current_user_id() is None:
            return jsonify(error="auth required"), 401
        return fn(*args, **kwargs)
    return wrapper


def require_admin(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if current_user_id() is None:
            return jsonify(error="auth required"), 401
        if current_user_role() != "admin":
            return jsonify(error="admin required"), 403
        return fn(*args, **kwargs)
    return wrapper
