"""FastAPI interface deployed by the Cloudflare Python Worker."""

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

try:
    from .auth import AuthenticationError, Identity, authenticate_password, issue_token, verify_token
    from .models import (BulkFailRequest, ImportChunk, ImportStart, LoginRequest,
                         LoginResponse, ResetRequest, ReviewSubmission)
    from .store import D1ReviewStore, StoreConflict
except ImportError:  # Cloudflare loads Worker modules as top-level modules.
    from auth import AuthenticationError, Identity, authenticate_password, issue_token, verify_token
    from models import (BulkFailRequest, ImportChunk, ImportStart, LoginRequest,
                        LoginResponse, ResetRequest, ReviewSubmission)
    from store import D1ReviewStore, StoreConflict


app = FastAPI(title="Review Desk API", version="1.0.0")


def _env(request: Request):
    return getattr(request.app.state, "env", None) or request.scope["env"]


def _value(env, name: str) -> str:
    value = getattr(env, name, None)
    return "" if value is None else str(value)


def _store(request: Request) -> D1ReviewStore:
    override = getattr(request.app.state, "store", None)
    return override or D1ReviewStore(getattr(_env(request), "DB"))


def _identity(request: Request, role: str | None = None) -> Identity:
    header = request.headers.get("authorization", "")
    if not header.startswith("Bearer "):
        raise HTTPException(401, "Authentication required.")
    try:
        identity = verify_token(header[7:], _value(_env(request), "SESSION_SECRET"))
    except AuthenticationError as exc:
        raise HTTPException(401, str(exc)) from exc
    if role and identity.role != role:
        raise HTTPException(403, "Insufficient permissions.")
    return identity


# This application deliberately registers no HTTP middleware. The Worker serves
# the interface and this API from one origin, and the Vite dev server proxies
# /api, so requests are same-origin everywhere and need no CORS headers.
# A Starlette BaseHTTPMiddleware here also drove roughly one request in six to
# a 1101 "will never generate a response" failure, because its task group could
# be dropped by the Workers event loop before a response was produced.


@app.exception_handler(StoreConflict)
async def store_conflict(_request: Request, exc: StoreConflict):
    return JSONResponse({"detail": str(exc)}, status_code=409)


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.post("/api/auth/login", response_model=LoginResponse)
async def login(payload: LoginRequest, request: Request):
    env = _env(request)
    try:
        identity = authenticate_password(
            payload.password, _value(env, "REVIEWER_PASSWORD"),
            _value(env, "ADMIN_PASSWORD"), payload.reviewer_name,
        )
    except AuthenticationError as exc:
        raise HTTPException(401, str(exc)) from exc
    return LoginResponse(token=issue_token(identity, _value(env, "SESSION_SECRET")),
                         role=identity.role, name=identity.name)


@app.post("/api/reviewer/claim")
async def claim(request: Request, exclude_id: int | None = None):
    identity = _identity(request, "reviewer")
    return await _store(request).claim(identity.name, identity.session_id, exclude_id)


@app.post("/api/reviewer/renew/{question_id}")
async def renew(question_id: int, request: Request):
    identity = _identity(request, "reviewer")
    return {"renewed": await _store(request).renew(
        question_id, identity.name, identity.session_id)}


@app.post("/api/reviewer/skip/{question_id}")
async def skip(question_id: int, request: Request):
    identity = _identity(request, "reviewer")
    await _store(request).skip(question_id, identity.name, identity.session_id)
    return {"ok": True}


@app.post("/api/reviewer/review")
async def review(payload: ReviewSubmission, request: Request):
    identity = _identity(request, "reviewer")
    await _store(request).submit(payload.question_id, identity.name, identity.session_id,
                                 payload.decision, payload.notes)
    return {"ok": True}


@app.post("/api/admin/imports")
async def start_import(payload: ImportStart, request: Request):
    _identity(request, "admin")
    return {"batch_id": await _store(request).start_import(payload.filename, payload.file_hash)}


@app.post("/api/admin/imports/{batch_id}/records")
async def import_chunk(batch_id: int, payload: ImportChunk, request: Request):
    _identity(request, "admin")
    return await _store(request).add_import_chunk(
        batch_id, [record.model_dump() for record in payload.records], payload.skipped_count)


@app.post("/api/admin/imports/{batch_id}/finish")
async def finish_import(batch_id: int, request: Request):
    _identity(request, "admin")
    await _store(request).finish_import(batch_id)
    return {"ok": True}


@app.get("/api/admin/batches")
async def batches(request: Request):
    _identity(request, "admin")
    return await _store(request).list_batches()


@app.delete("/api/admin/batches/{batch_id}")
async def delete_batch(batch_id: int, request: Request):
    identity = _identity(request, "admin")
    return await _store(request).delete_batch(batch_id, identity.name)


@app.get("/api/admin/analytics")
async def analytics(request: Request):
    _identity(request, "admin")
    return await _store(request).analytics()


@app.get("/api/admin/reviews")
async def reviews(request: Request, search: str = ""):
    _identity(request, "admin")
    return await _store(request).list_reviews(search)


@app.post("/api/admin/reviews/reset")
async def reset(payload: ResetRequest, request: Request):
    identity = _identity(request, "admin")
    await _store(request).reset_review(payload.review_id, identity.name)
    return {"ok": True}


@app.post("/api/admin/reviews/bulk-fail")
async def bulk_fail(payload: BulkFailRequest, request: Request):
    identity = _identity(request, "admin")
    return await _store(request).bulk_fail(
        [item.model_dump() for item in payload.items], identity.name)


@app.get("/api/admin/export")
async def export(request: Request):
    _identity(request, "admin")
    return await _store(request).export_rows()
