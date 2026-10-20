from psycopg.errors import UniqueViolation

from app.errors import APIError
from app.security import decode_token, password_hash, tokens


def register(db, body):
    try:
        return db.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (%s, %s, %s) "
            "RETURNING id, username, role, created_at",
            (body.username, password_hash.hash(body.password), body.role.value),
        ).fetchone()
    except UniqueViolation:
        raise APIError(409, "USERNAME_TAKEN", "Username already registered")


def login(db, body):
    user = db.execute("SELECT * FROM users WHERE username = %s", (body.username,)).fetchone()
    if not user or not password_hash.verify(body.password, user["password_hash"]):
        raise APIError(401, "AUTHENTICATION_FAILED", "Invalid username or password")
    return tokens(user)


def refresh(db, body):
    payload = decode_token(body.refresh_token, "refresh")
    user = db.execute("SELECT id, role FROM users WHERE id = %s", (payload["sub"],)).fetchone()
    if not user or user["role"] != payload["role"]:
        raise APIError(401, "REFRESH_TOKEN_INVALID", "User or role no longer valid")
    return tokens(user)
