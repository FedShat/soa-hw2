from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.config import settings
from app.errors import APIError, invalid_field
from app.services import promos


def lock_actor(ctx):
    # Serializes rate-limit checks and active-order creation for one actor.
    ctx.db.execute("SELECT id FROM users WHERE id = %s FOR UPDATE", (ctx.user["id"],))


def check_rate(ctx, operation):
    latest = ctx.db.execute(
        "SELECT created_at FROM user_operations WHERE user_id=%s AND operation_type=%s "
        "ORDER BY created_at DESC LIMIT 1",
        (ctx.user["id"], operation),
    ).fetchone()
    if latest:
        retry_at = latest["created_at"] + timedelta(minutes=settings.order_interval_minutes)
        if datetime.now(timezone.utc) < retry_at:
            raise APIError(
                429,
                "ORDER_LIMIT_EXCEEDED",
                "Order operation is too frequent",
                {
                    "operation_type": operation,
                    "retry_at": retry_at.isoformat(),
                },
            )


def record(ctx, operation):
    ctx.db.execute(
        "INSERT INTO user_operations (user_id, operation_type, created_at) VALUES (%s,%s,clock_timestamp())",
        (ctx.user["id"], operation),
    )


def find(ctx, id, lock=False):
    order = ctx.db.execute(
        "SELECT * FROM orders WHERE id=%s" + (" FOR UPDATE" if lock else ""), (id,)
    ).fetchone()
    if not order:
        raise APIError(404, "ORDER_NOT_FOUND", "Order not found", {"order_id": str(id)})
    if ctx.user["role"] != "ADMIN" and order["user_id"] != ctx.user["id"]:
        raise APIError(403, "ORDER_OWNERSHIP_VIOLATION", "Order belongs to another user")
    return order


def order_items(db, id):
    return db.execute("SELECT * FROM order_items WHERE order_id=%s ORDER BY product_id", (id,)).fetchall()


def response(db, order):
    items = order_items(db, order["id"])
    order["items"] = [{k: v for k, v in item.items() if k != "order_id"} for item in items]
    return order


def get(ctx, id):
    # Shared row lock prevents a concurrent replacement between reading order and items.
    order = ctx.db.execute("SELECT * FROM orders WHERE id=%s FOR SHARE", (id,)).fetchone()
    if not order:
        raise APIError(404, "ORDER_NOT_FOUND", "Order not found", {"order_id": str(id)})
    if ctx.user["role"] != "ADMIN" and order["user_id"] != ctx.user["id"]:
        raise APIError(403, "ORDER_OWNERSHIP_VIOLATION", "Order belongs to another user")
    return response(ctx.db, order)


def check_state(order, allowed):
    if order["status"] not in allowed:
        raise APIError(
            409,
            "INVALID_STATE_TRANSITION",
            "Operation is not allowed in this state",
            {
                "current_status": order["status"],
                "allowed_statuses": list(allowed),
            },
        )


def lock_products(db, ids):
    # Every order locks products in UUID order to avoid deadlocks between overlapping carts.
    rows = db.execute(
        "SELECT * FROM products WHERE id = ANY(%s) ORDER BY id FOR UPDATE", (list(ids),)
    ).fetchall()
    return {row["id"]: row for row in rows}


def validate_catalog(items, products):
    for item in items:
        product = products.get(item.product_id)
        if product is None:
            raise APIError(
                404, "PRODUCT_NOT_FOUND", "Product not found", {"product_id": str(item.product_id)}
            )
        if product["status"] != "ACTIVE":
            raise APIError(
                409, "PRODUCT_INACTIVE", "Product is not active", {"product_id": str(item.product_id)}
            )


def reserve(db, items, products):
    shortages = [
        {
            "product_id": str(item.product_id),
            "requested": item.quantity,
            "available": products[item.product_id]["stock"],
        }
        for item in items
        if item.quantity > products[item.product_id]["stock"]
    ]
    if shortages:
        raise APIError(409, "INSUFFICIENT_STOCK", "Insufficient stock", {"products": shortages})
    for item in items:
        db.execute("UPDATE products SET stock=stock-%s WHERE id=%s", (item.quantity, item.product_id))


def restore(db, items, products):
    for item in items:
        db.execute("UPDATE products SET stock=stock+%s WHERE id=%s", (item["quantity"], item["product_id"]))
        products[item["product_id"]]["stock"] += item["quantity"]


def prepare_items(items, products, previous=()):
    snapshots = {item["product_id"]: item["price_at_order"] for item in previous}
    rows = [
        (item.product_id, item.quantity, snapshots.get(item.product_id, products[item.product_id]["price"]))
        for item in items
    ]
    subtotal = sum((quantity * price for _, quantity, price in rows), Decimal("0.00"))
    if subtotal > Decimal("9999999999.99"):
        invalid_field("items", "Order amount exceeds DECIMAL(12,2)")
    return rows, subtotal


def insert_items(db, id, rows):
    with db.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO order_items (order_id, product_id, quantity, price_at_order) VALUES (%s,%s,%s,%s)",
            [(id, *row) for row in rows],
        )


def create(ctx, body):
    lock_actor(ctx)
    check_rate(ctx, "CREATE_ORDER")
    if ctx.db.execute(
        "SELECT id FROM orders WHERE user_id=%s AND status IN ('CREATED','PAYMENT_PENDING')",
        (ctx.user["id"],),
    ).fetchone():
        raise APIError(409, "ORDER_HAS_ACTIVE", "User already has an active order")
    products = lock_products(ctx.db, {item.product_id for item in body.items})
    validate_catalog(body.items, products)
    reserve(ctx.db, body.items, products)
    rows, subtotal = prepare_items(body.items, products)
    promo_id, discount = None, Decimal("0.00")
    if body.promo_code is not None:
        promo = ctx.db.execute(
            "SELECT * FROM promo_codes WHERE code=%s FOR UPDATE", (body.promo_code,)
        ).fetchone()
        promos.validate(promo)
        if subtotal < promo["min_order_amount"]:
            raise APIError(
                422,
                "PROMO_CODE_MIN_AMOUNT",
                "Order amount is below promo minimum",
                {
                    "min_order_amount": str(promo["min_order_amount"]),
                    "order_amount": str(subtotal),
                },
            )
        promo_id, discount = promo["id"], promos.discount(promo, subtotal)
        ctx.db.execute("UPDATE promo_codes SET current_uses=current_uses+1 WHERE id=%s", (promo_id,))
    order = ctx.db.execute(
        "INSERT INTO orders (user_id, status, promo_code_id, total_amount, discount_amount) "
        "VALUES (%s,'CREATED',%s,%s,%s) RETURNING *",
        (ctx.user["id"], promo_id, subtotal - discount, discount),
    ).fetchone()
    insert_items(ctx.db, order["id"], rows)
    record(ctx, "CREATE_ORDER")
    return response(ctx.db, order)


def update(ctx, id, body):
    lock_actor(ctx)
    order = find(ctx, id, lock=True)
    check_state(order, ["CREATED"])
    check_rate(ctx, "UPDATE_ORDER")
    previous = order_items(ctx.db, id)
    ids = {item["product_id"] for item in previous} | {item.product_id for item in body.items}
    products = lock_products(ctx.db, ids)
    restore(ctx.db, previous, products)
    validate_catalog(body.items, products)
    reserve(ctx.db, body.items, products)
    rows, subtotal = prepare_items(body.items, products, previous)
    discount, promo_id = Decimal("0.00"), order["promo_code_id"]
    if promo_id:
        promo = ctx.db.execute("SELECT * FROM promo_codes WHERE id=%s FOR UPDATE", (promo_id,)).fetchone()
        promos.validate(promo, already_used=True)
        if subtotal < promo["min_order_amount"]:
            ctx.db.execute("UPDATE promo_codes SET current_uses=current_uses-1 WHERE id=%s", (promo_id,))
            promo_id = None
        else:
            discount = promos.discount(promo, subtotal)
    ctx.db.execute("DELETE FROM order_items WHERE order_id=%s", (id,))
    insert_items(ctx.db, id, rows)
    order = ctx.db.execute(
        "UPDATE orders SET total_amount=%s, discount_amount=%s, promo_code_id=%s WHERE id=%s RETURNING *",
        (subtotal - discount, discount, promo_id, id),
    ).fetchone()
    record(ctx, "UPDATE_ORDER")
    return response(ctx.db, order)


def cancel(ctx, id):
    lock_actor(ctx)
    order = find(ctx, id, lock=True)
    check_state(order, ["CREATED", "PAYMENT_PENDING"])
    previous = order_items(ctx.db, id)
    products = lock_products(ctx.db, {item["product_id"] for item in previous})
    restore(ctx.db, previous, products)
    if order["promo_code_id"]:
        ctx.db.execute(
            "UPDATE promo_codes SET current_uses=current_uses-1 WHERE id=%s", (order["promo_code_id"],)
        )
    order = ctx.db.execute("UPDATE orders SET status='CANCELED' WHERE id=%s RETURNING *", (id,)).fetchone()
    return response(ctx.db, order)


def transition(ctx, id, body):
    order = find(ctx, id, lock=True)
    allowed = {
        "CREATED": "PAYMENT_PENDING",
        "PAYMENT_PENDING": "PAID",
        "PAID": "SHIPPED",
        "SHIPPED": "COMPLETED",
    }
    if allowed.get(order["status"]) != body.status.value:
        raise APIError(
            409,
            "INVALID_STATE_TRANSITION",
            "Only the next lifecycle state is allowed",
            {
                "current_status": order["status"],
                "requested_status": body.status.value,
            },
        )
    order = ctx.db.execute(
        "UPDATE orders SET status=%s WHERE id=%s RETURNING *", (body.status.value, id)
    ).fetchone()
    return response(ctx.db, order)
