from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from psycopg import Connection
from pwdlib import PasswordHash

from app.config import settings
from app.db import get_db
from app.errors import APIError

password_hash = PasswordHash.recommended()
bearer = HTTPBearer(auto_error=False)


@dataclass
class Context:
    db: Connection
    user: dict


def decode_token(token: str, kind: str) -> dict:
    invalid = "REFRESH_TOKEN_INVALID" if kind == "refresh" else "TOKEN_INVALID"
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=["HS256"],
            issuer="marketplace",
            audience="marketplace-api",
            options={"require": ["sub", "role", "type", "exp", "iat", "jti"]},
        )
        if payload["type"] != kind or payload["role"] not in {"USER", "SELLER", "ADMIN"}:
            raise ValueError("Wrong token type or role")
        UUID(payload["sub"])
        return payload
    except jwt.ExpiredSignatureError:
        raise APIError(401, "TOKEN_EXPIRED" if kind == "access" else invalid, "Token expired")
    except (jwt.InvalidTokenError, ValueError, TypeError, AttributeError):
        raise APIError(401, invalid, "Invalid token")


def tokens(user: dict) -> dict:
    now = datetime.now(timezone.utc)

    def issue(kind, lifetime):
        return jwt.encode(
            {
                "sub": str(user["id"]),
                "role": user["role"],
                "type": kind,
                "iat": now,
                "exp": now + lifetime,
                "jti": str(uuid4()),
                "iss": "marketplace",
                "aud": "marketplace-api",
            },
            settings.jwt_secret,
            algorithm="HS256",
        )

    return {
        "access_token": issue("access", timedelta(minutes=settings.access_minutes)),
        "refresh_token": issue("refresh", timedelta(days=settings.refresh_days)),
        "token_type": "bearer",
        "expires_in": settings.access_minutes * 60,
    }


def get_context(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Connection = Depends(get_db, scope="function"),
) -> Context:
    if credentials is None:
        raise APIError(401, "TOKEN_INVALID", "Bearer access token required")
    payload = decode_token(credentials.credentials, "access")
    user = db.execute("SELECT id, username, role FROM users WHERE id = %s", (payload["sub"],)).fetchone()
    if not user or user["role"] != payload["role"]:
        raise APIError(401, "TOKEN_INVALID", "User or role no longer valid; log in again")
    request.state.user_id = str(user["id"])
    return Context(db, user)


def require_roles(ctx: Context, *roles: str):
    if ctx.user["role"] not in roles:
        raise APIError(403, "ACCESS_DENIED", "Role does not permit this operation")
