import os
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
import pytest
from fastapi.testclient import TestClient

if not os.environ.get("DATABASE_URL", "").endswith("/marketplace_test"):
    raise RuntimeError("Run make test: integration tests require the separate marketplace_test database")

from app.main import app


@pytest.fixture
def db():
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row, autocommit=True) as connection:
        connection.execute(
            "TRUNCATE users, products, orders, order_items, promo_codes, user_operations CASCADE"
        )
        yield connection


@pytest.fixture
def client(db):
    with TestClient(app) as client:
        yield client


@pytest.fixture
def account(client, db):
    def create(role="USER"):
        username = "u_" + uuid4().hex[:12]
        credentials = {"username": username, "password": "password123"}
        result = client.post(
            "/auth/register", json={**credentials, "role": "SELLER" if role == "SELLER" else "USER"}
        )
        assert result.status_code == 201, result.text
        user = result.json()
        if role == "ADMIN":
            db.execute("UPDATE users SET role='ADMIN' WHERE id=%s", (user["id"],))
        login = client.post("/auth/login", json=credentials)
        assert login.status_code == 200, login.text
        return {
            **user,
            **login.json(),
            **credentials,
            "role": role,
            "headers": {"Authorization": "Bearer " + login.json()["access_token"]},
        }

    return create


@pytest.fixture
def seller(account):
    return account("SELLER")


@pytest.fixture
def buyer(account):
    return account()


@pytest.fixture
def product(client, seller):
    def create(**overrides):
        body = {
            "name": "Keyboard",
            "description": None,
            "price": 100,
            "stock": 10,
            "category": "electronics",
            "status": "ACTIVE",
            **overrides,
        }
        result = client.post("/products", headers=seller["headers"], json=body)
        assert result.status_code == 201, result.text
        return result.json()

    return create


@pytest.fixture
def promo(client, seller):
    from datetime import datetime, timedelta, timezone

    def create(**overrides):
        now = datetime.now(timezone.utc)
        body = {
            "code": "PROMO_" + uuid4().hex[:8].upper(),
            "discount_type": "PERCENTAGE",
            "discount_value": 20,
            "min_order_amount": 150,
            "max_uses": 5,
            "valid_from": (now - timedelta(days=1)).isoformat(),
            "valid_until": (now + timedelta(days=1)).isoformat(),
            "active": True,
            **overrides,
        }
        result = client.post("/promo-codes", headers=seller["headers"], json=body)
        assert result.status_code == 201, result.text
        return result.json()

    return create
