from app.errors import APIError, invalid_field


def find(db, id, lock=False):
    row = db.execute(
        "SELECT * FROM products WHERE id = %s" + (" FOR UPDATE" if lock else ""), (id,)
    ).fetchone()
    if not row:
        raise APIError(404, "PRODUCT_NOT_FOUND", "Product not found", {"product_id": str(id)})
    return row


def check_owner(ctx, product):
    if ctx.user["role"] != "ADMIN" and product["seller_id"] != ctx.user["id"]:
        raise APIError(403, "ACCESS_DENIED", "Seller can only manage their own products")


def seller(ctx, requested, fallback):
    seller_id = requested or fallback
    if ctx.user["role"] != "ADMIN" and seller_id != ctx.user["id"]:
        raise APIError(403, "ACCESS_DENIED", "Seller can only assign themselves as owner")
    if not ctx.db.execute("SELECT id FROM users WHERE id = %s", (seller_id,)).fetchone():
        invalid_field("seller_id", "User does not exist")
    return seller_id


def create(ctx, body):
    owner = seller(ctx, body.seller_id, ctx.user["id"])
    return ctx.db.execute(
        "INSERT INTO products (name, description, price, stock, category, status, seller_id) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING *",
        (body.name, body.description, body.price, body.stock, body.category, body.status.value, owner),
    ).fetchone()


def get(ctx, id):
    return find(ctx.db, id)


def list_products(ctx, page=0, size=20, status=None, category=None):
    clauses, params = [], []
    if status is not None:
        clauses.append("status = %s")
        params.append(status.value)
    if category is not None:
        clauses.append("category = %s")
        params.append(category)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    # One SQL snapshot keeps the count and rows consistent during concurrent writes.
    rows = ctx.db.execute(
        "WITH filtered AS MATERIALIZED (SELECT * FROM products" + where + "), "
        "page AS (SELECT * FROM filtered ORDER BY created_at, id LIMIT %s OFFSET %s) "
        "SELECT (SELECT count(*) FROM filtered) AS total, "
        "(SELECT jsonb_agg(to_jsonb(page) ORDER BY created_at, id) FROM page) AS items",
        [*params, size, page * size],
    ).fetchone()
    return {"items": rows["items"] or [], "totalElements": rows["total"], "page": page, "size": size}


def update(ctx, id, body):
    current = find(ctx.db, id, lock=True)
    check_owner(ctx, current)
    owner = seller(ctx, body.seller_id, current["seller_id"])
    return ctx.db.execute(
        "UPDATE products SET name=%s, description=%s, price=%s, stock=%s, category=%s, status=%s, "
        "seller_id=%s WHERE id=%s RETURNING *",
        (body.name, body.description, body.price, body.stock, body.category, body.status.value, owner, id),
    ).fetchone()


def archive(ctx, id):
    current = find(ctx.db, id, lock=True)
    check_owner(ctx, current)
    ctx.db.execute("UPDATE products SET status = 'ARCHIVED' WHERE id = %s", (id,))
