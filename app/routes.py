from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Response
from psycopg import Connection

from app.db import get_db
from app.generated import models
from app.security import Context, get_context, require_roles
from app.services import auth, products, orders, promos

router = APIRouter()


@router.post(
    "/auth/register", status_code=201, response_model=models.UserResponse, operation_id="auth_register"
)
def auth_register(body: models.RegisterRequest, db: Connection = Depends(get_db, scope="function")):
    return auth.register(db=db, body=body)


@router.post("/auth/login", status_code=200, response_model=models.TokenResponse, operation_id="auth_login")
def auth_login(body: models.LoginRequest, db: Connection = Depends(get_db, scope="function")):
    return auth.login(db=db, body=body)


@router.post(
    "/auth/refresh", status_code=200, response_model=models.TokenResponse, operation_id="auth_refresh"
)
def auth_refresh(body: models.RefreshRequest, db: Connection = Depends(get_db, scope="function")):
    return auth.refresh(db=db, body=body)


@router.post(
    "/products", status_code=201, response_model=models.ProductResponse, operation_id="products_create"
)
def products_create(body: models.ProductCreate, ctx: Context = Depends(get_context, scope="function")):
    require_roles(ctx, "SELLER", "ADMIN")
    return products.create(ctx=ctx, body=body)


@router.get(
    "/products", status_code=200, response_model=models.ProductPage, operation_id="products_list_products"
)
def products_list_products(
    ctx: Context = Depends(get_context, scope="function"),
    page: int = Query(0, ge=0),
    size: int = Query(20, ge=1, le=100),
    status: models.ProductStatus | None = Query(None),
    category: str | None = Query(None, min_length=1, max_length=100),
):
    require_roles(ctx, "USER", "SELLER", "ADMIN")
    return products.list_products(ctx=ctx, page=page, size=size, status=status, category=category)


@router.get(
    "/products/{id}", status_code=200, response_model=models.ProductResponse, operation_id="products_get"
)
def products_get(ctx: Context = Depends(get_context, scope="function"), id: UUID = Path(...)):
    require_roles(ctx, "USER", "SELLER", "ADMIN")
    return products.get(ctx=ctx, id=id)


@router.put(
    "/products/{id}", status_code=200, response_model=models.ProductResponse, operation_id="products_update"
)
def products_update(
    body: models.ProductUpdate, ctx: Context = Depends(get_context, scope="function"), id: UUID = Path(...)
):
    require_roles(ctx, "SELLER", "ADMIN")
    return products.update(ctx=ctx, body=body, id=id)


@router.delete("/products/{id}", status_code=204, response_model=None, operation_id="products_archive")
def products_archive(ctx: Context = Depends(get_context, scope="function"), id: UUID = Path(...)):
    require_roles(ctx, "SELLER", "ADMIN")
    products.archive(ctx=ctx, id=id)
    return Response(status_code=204)


@router.post("/orders", status_code=201, response_model=models.OrderResponse, operation_id="orders_create")
def orders_create(body: models.OrderCreate, ctx: Context = Depends(get_context, scope="function")):
    require_roles(ctx, "USER", "ADMIN")
    return orders.create(ctx=ctx, body=body)


@router.get("/orders/{id}", status_code=200, response_model=models.OrderResponse, operation_id="orders_get")
def orders_get(ctx: Context = Depends(get_context, scope="function"), id: UUID = Path(...)):
    require_roles(ctx, "USER", "ADMIN")
    return orders.get(ctx=ctx, id=id)


@router.put(
    "/orders/{id}", status_code=200, response_model=models.OrderResponse, operation_id="orders_update"
)
def orders_update(
    body: models.OrderUpdate, ctx: Context = Depends(get_context, scope="function"), id: UUID = Path(...)
):
    require_roles(ctx, "USER", "ADMIN")
    return orders.update(ctx=ctx, body=body, id=id)


@router.post(
    "/orders/{id}/cancel", status_code=200, response_model=models.OrderResponse, operation_id="orders_cancel"
)
def orders_cancel(ctx: Context = Depends(get_context, scope="function"), id: UUID = Path(...)):
    require_roles(ctx, "USER", "ADMIN")
    return orders.cancel(ctx=ctx, id=id)


@router.post(
    "/orders/{id}/status",
    status_code=200,
    response_model=models.OrderResponse,
    operation_id="orders_transition",
)
def orders_transition(
    body: models.OrderStatusUpdate,
    ctx: Context = Depends(get_context, scope="function"),
    id: UUID = Path(...),
):
    require_roles(ctx, "ADMIN")
    return orders.transition(ctx=ctx, body=body, id=id)


@router.post(
    "/promo-codes", status_code=201, response_model=models.PromoCodeResponse, operation_id="promos_create"
)
def promos_create(body: models.PromoCodeCreate, ctx: Context = Depends(get_context, scope="function")):
    require_roles(ctx, "SELLER", "ADMIN")
    return promos.create(ctx=ctx, body=body)
