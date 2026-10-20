from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from uuid import UUID, uuid4

import jwt
from openapi_spec_validator import validate
import pytest

from app.config import settings
from app.main import CONTRACT, logger


def cart(product, quantity=1, **extra):
    return {"items": [{"product_id": product["id"], "quantity": quantity}], **extra}


def error(response, status, code):
    assert response.status_code == status, response.text
    assert response.json()["error_code"] == code, response.text
    assert response.json()["message"]
    UUID(response.headers["X-Request-Id"])
    return response.json()


def post_order(client, buyer, product, quantity=1, **extra):
    return client.post("/orders", headers=buyer["headers"], json=cart(product, quantity, **extra))


def age_operations(db):
    db.execute("UPDATE user_operations SET created_at = now() - interval '2 minutes'")


def test_contract(client):
    validate(CONTRACT)
    assert client.get("/openapi.json").json() == CONTRACT
    assert client.get("/docs").status_code == 200


def test_product_crud_pagination_and_soft_delete(client, product, seller, db):
    p = product()
    assert isinstance(p["price"], (int, float))
    product(category="electronics-more")
    product(status="INACTIVE")
    result = client.get(
        "/products?status=ACTIVE&category=electronics&page=0&size=1", headers=seller["headers"]
    )
    assert result.status_code == 200, result.text
    assert result.json()["totalElements"] == 1
    assert result.json()["items"][0]["id"] == p["id"]
    assert client.get("/products?page=99", headers=seller["headers"]).json()["items"] == []
    updated = client.put(
        f"/products/{p['id']}",
        headers=seller["headers"],
        json={"name": "New", "price": 120, "stock": 9, "category": "books", "status": "ACTIVE"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["created_at"] == p["created_at"]
    assert updated.json()["updated_at"] > p["updated_at"]
    deleted = client.delete(f"/products/{p['id']}", headers=seller["headers"])
    assert deleted.status_code == 204
    assert deleted.content == b""
    assert client.get(f"/products/{p['id']}", headers=seller["headers"]).json()["status"] == "ARCHIVED"
    assert (
        db.execute("SELECT status FROM products WHERE id=%s", (p["id"],)).fetchone()["status"] == "ARCHIVED"
    )
    error(client.get(f"/products/{uuid4()}", headers=seller["headers"]), 404, "PRODUCT_NOT_FOUND")


@pytest.mark.parametrize(
    "field,value",
    [
        ("name", ""),
        ("name", "a" * 256),
        ("description", "a" * 4001),
        ("price", 0),
        ("price", -1),
        ("price", 0.001),
        ("price", 1.001),
        ("stock", -1),
        ("stock", 1.5),
        ("stock", True),
        ("stock", "1"),
        ("category", ""),
        ("category", "a" * 101),
        ("status", "BAD"),
    ],
)
def test_product_validation(client, seller, db, field, value):
    body = {"name": "Valid", "price": 1, "stock": 0, "category": "ok", "status": "ACTIVE", field: value}
    result = error(client.post("/products", headers=seller["headers"], json=body), 400, "VALIDATION_ERROR")
    assert any(field in v["field"] for v in result["details"]["violations"])
    assert db.execute("SELECT count(*) AS n FROM products").fetchone()["n"] == 0


@pytest.mark.parametrize("query", ["page=-1", "size=0", "size=101", "status=UNKNOWN"])
def test_query_validation(client, buyer, query):
    error(client.get("/products?" + query, headers=buyer["headers"]), 400, "VALIDATION_ERROR")


@pytest.mark.parametrize(
    "items",
    [
        [],
        [{"product_id": str(uuid4()), "quantity": 0}],
        [{"product_id": str(uuid4()), "quantity": 1000}],
        [{"product_id": str(uuid4()), "quantity": 1}] * 51,
    ],
)
def test_order_validation(client, buyer, db, items):
    error(client.post("/orders", headers=buyer["headers"], json={"items": items}), 400, "VALIDATION_ERROR")
    assert db.execute("SELECT count(*) AS n FROM orders").fetchone()["n"] == 0


def test_validation_boundaries(client, seller, buyer, product):
    p = product(name="n" * 255, description="d" * 4000, category="c" * 100, price=0.01, stock=999)
    r = post_order(client, buyer, p, 999)
    assert r.status_code == 201, r.text
    assert Decimal(str(r.json()["total_amount"])) == Decimal("9.99")
    error(
        client.post("/orders", headers=buyer["headers"], json=cart(p, promo_code="bad")),
        400,
        "VALIDATION_ERROR",
    )
    error(
        client.post(
            "/orders",
            headers=buyer["headers"],
            json={"items": [{"product_id": p["id"], "quantity": 1}, {"product_id": p["id"], "quantity": 2}]},
        ),
        400,
        "VALIDATION_ERROR",
    )


def test_orders_snapshot_update_cancel_and_rate_limits(client, buyer, product, db):
    p = product()
    created = post_order(client, buyer, p, 2)
    assert created.status_code == 201, created.text
    order = created.json()
    assert order["total_amount"] == 200
    assert db.execute("SELECT stock FROM products WHERE id=%s", (p["id"],)).fetchone()["stock"] == 8
    error(post_order(client, buyer, p), 429, "ORDER_LIMIT_EXCEEDED")
    age_operations(db)
    error(post_order(client, buyer, p), 409, "ORDER_HAS_ACTIVE")
    db.execute("UPDATE products SET price=999 WHERE id=%s", (p["id"],))
    assert (
        client.get(f"/orders/{order['id']}", headers=buyer["headers"]).json()["items"][0]["price_at_order"]
        == 100
    )
    updated = client.put(f"/orders/{order['id']}", headers=buyer["headers"], json=cart(p, 3))
    assert updated.status_code == 200, updated.text
    assert updated.json()["total_amount"] == 300
    assert db.execute("SELECT stock FROM products WHERE id=%s", (p["id"],)).fetchone()["stock"] == 7
    error(
        client.put(f"/orders/{order['id']}", headers=buyer["headers"], json=cart(p, 1)),
        429,
        "ORDER_LIMIT_EXCEEDED",
    )
    canceled = client.post(f"/orders/{order['id']}/cancel", headers=buyer["headers"])
    assert canceled.status_code == 200, canceled.text
    assert canceled.json()["status"] == "CANCELED"
    assert db.execute("SELECT stock FROM products WHERE id=%s", (p["id"],)).fetchone()["stock"] == 10
    error(
        client.post(f"/orders/{order['id']}/cancel", headers=buyer["headers"]),
        409,
        "INVALID_STATE_TRANSITION",
    )


def test_stock_and_transaction_rollback(client, buyer, product, db):
    p, other = product(), product(stock=0)
    response = client.post(
        "/orders",
        headers=buyer["headers"],
        json={"items": [{"product_id": p["id"], "quantity": 11}, {"product_id": other["id"], "quantity": 1}]},
    )
    assert len(error(response, 409, "INSUFFICIENT_STOCK")["details"]["products"]) == 2
    assert db.execute("SELECT count(*) AS n FROM orders").fetchone()["n"] == 0
    created = post_order(client, buyer, p, 2).json()
    result = client.put(f"/orders/{created['id']}", headers=buyer["headers"], json=cart(other))
    error(result, 409, "INSUFFICIENT_STOCK")
    assert db.execute("SELECT stock FROM products WHERE id=%s", (p["id"],)).fetchone()["stock"] == 8
    assert (
        db.execute("SELECT quantity FROM order_items WHERE order_id=%s", (created["id"],)).fetchone()[
            "quantity"
        ]
        == 2
    )
    assert (
        db.execute(
            "SELECT count(*) AS n FROM user_operations WHERE operation_type='UPDATE_ORDER'"
        ).fetchone()["n"]
        == 0
    )


def test_catalog_checked_before_stock(client, buyer, product):
    p = product(stock=0)
    inactive = product(status="INACTIVE")
    response = client.post(
        "/orders",
        headers=buyer["headers"],
        json={
            "items": [{"product_id": p["id"], "quantity": 1}, {"product_id": inactive["id"], "quantity": 1}]
        },
    )
    error(response, 409, "PRODUCT_INACTIVE")
    error(post_order(client, buyer, {"id": str(uuid4())}), 404, "PRODUCT_NOT_FOUND")
    error(post_order(client, buyer, product(status="ARCHIVED")), 409, "PRODUCT_INACTIVE")


@pytest.mark.parametrize(
    "kind,value,expected",
    [
        ("PERCENTAGE", 20, 40),
        ("PERCENTAGE", 70, 140),
        ("PERCENTAGE", 71, 0),
        ("PERCENTAGE", 100, 0),
        ("FIXED_AMOUNT", 500, 200),
    ],
)
def test_discount_types(client, buyer, product, promo, db, kind, value, expected):
    p = product()
    code = promo(discount_type=kind, discount_value=value, max_uses=1)
    result = post_order(client, buyer, p, 2, promo_code=code["code"])
    assert result.status_code == 201, result.text
    order = result.json()
    assert order["discount_amount"] == expected
    assert order["total_amount"] == 200 - expected
    assert (
        db.execute("SELECT current_uses FROM promo_codes WHERE id=%s", (code["id"],)).fetchone()[
            "current_uses"
        ]
        == 1
    )
    # Its own reserved use must not prevent updating an order with max_uses=1.
    result = client.put(f"/orders/{order['id']}", headers=buyer["headers"], json=cart(p, 2))
    assert result.status_code == 200, result.text
    client.post(f"/orders/{order['id']}/cancel", headers=buyer["headers"])
    assert (
        db.execute("SELECT current_uses FROM promo_codes WHERE id=%s", (code["id"],)).fetchone()[
            "current_uses"
        ]
        == 0
    )


def test_promo_removed_on_update(client, buyer, product, promo, db):
    p = product()
    code = promo()
    order = post_order(client, buyer, p, 2, promo_code=code["code"]).json()
    result = client.put(f"/orders/{order['id']}", headers=buyer["headers"], json=cart(p))
    assert result.status_code == 200, result.text
    assert result.json()["promo_code_id"] is None
    assert result.json()["discount_amount"] == 0
    assert result.json()["total_amount"] == 100
    client.post(f"/orders/{order['id']}/cancel", headers=buyer["headers"])
    assert (
        db.execute("SELECT current_uses FROM promo_codes WHERE id=%s", (code["id"],)).fetchone()[
            "current_uses"
        ]
        == 0
    )


@pytest.mark.parametrize("reason", ["missing", "inactive", "expired", "future", "exhausted", "minimum"])
def test_invalid_promos_rollback(client, buyer, product, promo, db, reason):
    p = product()
    code = promo()
    if reason == "inactive":
        db.execute("UPDATE promo_codes SET active=false")
    if reason == "expired":
        db.execute("UPDATE promo_codes SET valid_until=now()-interval '1 minute'")
    if reason == "future":
        db.execute("UPDATE promo_codes SET valid_from=now()+interval '1 minute'")
    if reason == "exhausted":
        db.execute("UPDATE promo_codes SET current_uses=max_uses")
    used_code = "MISSING" if reason == "missing" else code["code"]
    response = post_order(client, buyer, p, 1 if reason == "minimum" else 2, promo_code=used_code)
    error(response, 422, "PROMO_CODE_MIN_AMOUNT" if reason == "minimum" else "PROMO_CODE_INVALID")
    assert db.execute("SELECT stock FROM products WHERE id=%s", (p["id"],)).fetchone()["stock"] == 10
    assert db.execute("SELECT count(*) AS n FROM orders").fetchone()["n"] == 0
    assert db.execute("SELECT count(*) AS n FROM user_operations").fetchone()["n"] == 0


def test_promo_expiry_on_update_rolls_back(client, buyer, product, promo, db):
    p = product()
    code = promo()
    order = post_order(client, buyer, p, 2, promo_code=code["code"]).json()
    db.execute("UPDATE promo_codes SET active=false")
    error(
        client.put(f"/orders/{order['id']}", headers=buyer["headers"], json=cart(p, 3)),
        422,
        "PROMO_CODE_INVALID",
    )
    assert db.execute("SELECT stock FROM products WHERE id=%s", (p["id"],)).fetchone()["stock"] == 8
    assert db.execute("SELECT current_uses FROM promo_codes").fetchone()["current_uses"] == 1


def test_roles_and_ownership(client, buyer, seller, account, product, db):
    p = product()
    other = account()
    other_seller = account("SELLER")
    admin = account("ADMIN")
    body = {"name": "Other", "price": 100, "stock": 10, "category": "electronics", "status": "ACTIVE"}
    error(client.post("/products", headers=buyer["headers"], json=body), 403, "ACCESS_DENIED")
    error(
        client.put(f"/products/{p['id']}", headers=other_seller["headers"], json=body), 403, "ACCESS_DENIED"
    )
    error(client.delete(f"/products/{p['id']}", headers=other_seller["headers"]), 403, "ACCESS_DENIED")
    error(
        client.post("/products", headers=seller["headers"], json={**body, "seller_id": other_seller["id"]}),
        403,
        "ACCESS_DENIED",
    )
    assert client.put(f"/products/{p['id']}", headers=admin["headers"], json=body).status_code == 200
    error(post_order(client, seller, p), 403, "ACCESS_DENIED")
    order = post_order(client, buyer, p).json()
    for method, suffix, data in [("get", "", None), ("put", "", cart(p)), ("post", "/cancel", None)]:
        response = client.request(
            method, f"/orders/{order['id']}" + suffix, headers=other["headers"], json=data
        )
        error(response, 403, "ORDER_OWNERSHIP_VIOLATION")
        response = client.request(
            method, f"/orders/{order['id']}" + suffix, headers=seller["headers"], json=data
        )
        error(response, 403, "ACCESS_DENIED")
    assert client.get(f"/orders/{order['id']}", headers=admin["headers"]).status_code == 200
    assert client.put(f"/orders/{order['id']}", headers=admin["headers"], json=cart(p, 2)).status_code == 200
    assert client.post(f"/orders/{order['id']}/cancel", headers=admin["headers"]).status_code == 200
    error(client.get(f"/orders/{uuid4()}", headers=buyer["headers"]), 404, "ORDER_NOT_FOUND")


def test_state_machine(client, buyer, product, account, db):
    p = product()
    admin = account("ADMIN")
    order = post_order(client, buyer, p).json()
    url = f"/orders/{order['id']}"
    error(
        client.post(url + "/status", headers=buyer["headers"], json={"status": "PAID"}), 403, "ACCESS_DENIED"
    )
    error(
        client.post(url + "/status", headers=admin["headers"], json={"status": "PAID"}),
        409,
        "INVALID_STATE_TRANSITION",
    )
    for state in ["PAYMENT_PENDING", "PAID", "SHIPPED", "COMPLETED"]:
        result = client.post(url + "/status", headers=admin["headers"], json={"status": state})
        assert result.status_code == 200, result.text
        error(client.put(url, headers=buyer["headers"], json=cart(p)), 409, "INVALID_STATE_TRANSITION")
        if state != "PAYMENT_PENDING":
            error(client.post(url + "/cancel", headers=buyer["headers"]), 409, "INVALID_STATE_TRANSITION")
    assert db.execute("SELECT stock FROM products WHERE id=%s", (p["id"],)).fetchone()["stock"] == 9
    age_operations(db)
    second = post_order(client, buyer, p).json()
    client.post(
        f"/orders/{second['id']}/status", headers=admin["headers"], json={"status": "PAYMENT_PENDING"}
    )
    assert client.post(f"/orders/{second['id']}/cancel", headers=buyer["headers"]).status_code == 200


def test_authentication_and_refresh(client, buyer, db):
    error(client.get("/products"), 401, "TOKEN_INVALID")
    error(client.get("/products", headers={"Authorization": "Bearer garbage"}), 401, "TOKEN_INVALID")
    error(
        client.get("/products", headers={"Authorization": "Bearer " + buyer["refresh_token"]}),
        401,
        "TOKEN_INVALID",
    )
    error(
        client.post("/auth/refresh", json={"refresh_token": buyer["access_token"]}),
        401,
        "REFRESH_TOKEN_INVALID",
    )
    error(client.post("/auth/refresh", json={"refresh_token": "garbage"}), 401, "REFRESH_TOKEN_INVALID")
    assert client.post("/auth/refresh", json={"refresh_token": buyer["refresh_token"]}).status_code == 200
    for kind, token, code in [
        ("access", buyer["access_token"], "TOKEN_EXPIRED"),
        ("refresh", buyer["refresh_token"], "REFRESH_TOKEN_INVALID"),
    ]:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"], audience="marketplace-api")
        payload["exp"] = datetime.now(timezone.utc) - timedelta(seconds=1)
        expired = jwt.encode(payload, settings.jwt_secret, algorithm="HS256")
        result = (
            client.get("/products", headers={"Authorization": "Bearer " + expired})
            if kind == "access"
            else client.post("/auth/refresh", json={"refresh_token": expired})
        )
        error(result, 401, code)
    error(
        client.post("/auth/register", json={"username": "admin", "password": "password123", "role": "ADMIN"}),
        400,
        "VALIDATION_ERROR",
    )
    error(
        client.post("/auth/login", json={"username": buyer["username"], "password": "incorrect"}),
        401,
        "AUTHENTICATION_FAILED",
    )
    error(
        client.post("/auth/register", json={"username": buyer["username"], "password": "password123"}),
        409,
        "USERNAME_TAKEN",
    )
    assert (
        db.execute("SELECT password_hash FROM users WHERE id=%s", (buyer["id"],))
        .fetchone()["password_hash"]
        .startswith("$argon2id$")
    )


def test_request_logs_and_masking(client, buyer, monkeypatch):
    events = []
    monkeypatch.setattr(logger, "info", lambda text: events.append(json.loads(text)))
    result = client.post("/auth/login", json={"username": buyer["username"], "password": "password123"})
    assert result.status_code == 200
    assert events[-1]["request_body"]["password"] == "***"
    assert "password123" not in json.dumps(events)
    result = client.get("/products", headers=buyer["headers"])
    assert events[-1]["user_id"] == buyer["id"]
    assert events[-1]["request_id"] == result.headers["X-Request-Id"]
    assert {
        "method",
        "endpoint",
        "status_code",
        "duration_ms",
        "timestamp",
        "user_id",
        "request_id",
    } <= events[-1].keys()
    malformed = client.post(
        "/auth/login", content='{"password":"secret"', headers={"content-type": "application/json"}
    )
    error(malformed, 400, "VALIDATION_ERROR")
    assert "secret" not in json.dumps(events)
    error(client.get("/missing"), 404, "NOT_FOUND")
    error(client.patch("/products"), 405, "METHOD_NOT_ALLOWED")


def test_concurrent_stock_reservation(client, buyer, account, product, db):
    p = product(stock=1)
    second = account()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda user: post_order(client, user, p), [buyer, second]))
    assert sorted(r.status_code for r in results) == [201, 409], [r.text for r in results]
    assert next(r for r in results if r.status_code == 409).json()["error_code"] == "INSUFFICIENT_STOCK"
    assert db.execute("SELECT stock FROM products WHERE id=%s", (p["id"],)).fetchone()["stock"] == 0
    assert db.execute("SELECT count(*) AS n FROM orders").fetchone()["n"] == 1


def test_concurrent_same_user(client, buyer, product, db):
    p = product()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: post_order(client, buyer, p), range(2)))
    assert sorted(r.status_code for r in results) == [201, 429], [r.text for r in results]
    assert db.execute("SELECT count(*) AS n FROM orders").fetchone()["n"] == 1


def test_concurrent_last_promo_use(client, buyer, account, product, promo, db):
    first, second = product(), product()
    other = account()
    code = promo(max_uses=1, min_order_amount=0)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda args: post_order(client, args[0], args[1], promo_code=code["code"]),
                [(buyer, first), (other, second)],
            )
        )
    assert sorted(r.status_code for r in results) == [201, 422], [r.text for r in results]
    assert db.execute("SELECT current_uses FROM promo_codes").fetchone()["current_uses"] == 1
    assert db.execute("SELECT sum(stock) AS stock FROM products").fetchone()["stock"] == 19
