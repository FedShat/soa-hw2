from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from psycopg.errors import UniqueViolation

from app.errors import APIError

CENT = Decimal("0.01")


def create(ctx, body):
    try:
        return ctx.db.execute(
            "INSERT INTO promo_codes (code, discount_type, discount_value, min_order_amount, "
            "max_uses, valid_from, valid_until, active) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
            (
                body.code,
                body.discount_type.value,
                body.discount_value,
                body.min_order_amount,
                body.max_uses,
                body.valid_from,
                body.valid_until,
                body.active,
            ),
        ).fetchone()
    except UniqueViolation:
        raise APIError(409, "PROMO_CODE_EXISTS", "Promo code already exists")


def validate(promo, already_used=False):
    now = datetime.now(timezone.utc)
    if (
        not promo
        or not promo["active"]
        or not promo["valid_from"] <= now <= promo["valid_until"]
        or promo["current_uses"] - int(already_used) >= promo["max_uses"]
    ):
        raise APIError(422, "PROMO_CODE_INVALID", "Promo code is missing, inactive, expired or exhausted")


def discount(promo, subtotal):
    if promo["discount_type"] == "FIXED_AMOUNT":
        return min(promo["discount_value"], subtotal)
    if promo["discount_value"] > 70:
        return Decimal("0.00")
    return (subtotal * promo["discount_value"] / 100).quantize(CENT, rounding=ROUND_HALF_UP)
