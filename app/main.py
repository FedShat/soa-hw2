import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from uuid import uuid4

import yaml
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from app.errors import APIError
from app.routes import router

app = FastAPI(title="Marketplace API", redirect_slashes=False)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)
CONTRACT = yaml.safe_load((Path(__file__).resolve().parents[1] / "openapi/marketplace.yaml").read_text())
app.openapi = lambda: CONTRACT

logger = logging.getLogger("marketplace.requests")
logger.setLevel(logging.INFO)
logger.propagate = False
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)


def redact(value):
    if isinstance(value, dict):
        return {
            key: (
                "***"
                if any(secret in key.lower() for secret in ("password", "token", "secret", "authorization"))
                else redact(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


@app.middleware("http")
async def log_request(request: Request, call_next):
    request_id = str(uuid4())
    timestamp = datetime.now(timezone.utc).isoformat()
    start = perf_counter()
    body = None
    if request.method in {"POST", "PUT", "DELETE"}:
        raw = await request.body()
        if raw:
            try:
                body = redact(json.loads(raw))
            except (ValueError, UnicodeDecodeError):
                body = "<invalid JSON>"
    try:
        result = await call_next(request)
    except Exception:
        # Never leak SQL, credentials or exception internals to the client or request log.
        result = JSONResponse(
            status_code=500, content={"error_code": "INTERNAL_ERROR", "message": "Internal server error"}
        )
    result.headers["X-Request-Id"] = request_id
    event = {
        "request_id": request_id,
        "method": request.method,
        "endpoint": request.url.path,
        "status_code": result.status_code,
        "duration_ms": round((perf_counter() - start) * 1000, 3),
        "user_id": getattr(request.state, "user_id", None),
        "timestamp": timestamp,
    }
    if request.method in {"POST", "PUT", "DELETE"}:
        event["request_body"] = body
    logger.info(json.dumps(event, ensure_ascii=False))
    return result


@app.exception_handler(APIError)
async def api_error(request, exc):
    headers = {"WWW-Authenticate": "Bearer"} if exc.status == 401 else None
    return JSONResponse(status_code=exc.status, content=exc.payload(), headers=headers)


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    # Do not include Pydantic's input values: they may contain passwords or tokens.
    violations = [
        {
            "field": ".".join(str(part) for part in error["loc"]),
            "message": error["msg"],
            "type": error["type"],
        }
        for error in exc.errors()
    ]
    return JSONResponse(
        status_code=400,
        content={
            "error_code": "VALIDATION_ERROR",
            "message": "Invalid request",
            "details": {"violations": violations},
        },
    )


@app.exception_handler(HTTPException)
async def http_error(request, exc):
    code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(exc.status_code, "VALIDATION_ERROR")
    return JSONResponse(status_code=exc.status_code, content={"error_code": code, "message": str(exc.detail)})
