"""FastAPI app: mounts the three routers and shapes errors as CONTRACTS §6 says.

Owner: Winston (shared)
Diagram box: none; wiring only
Contract: docs/CONTRACTS.md §3 (endpoints) and §6 (errors)
"""

import asyncio
import ipaddress
import logging
import re
import secrets
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException

from app import config, evidence, llm, transaction_state
from app.audit import AuditError
from app.audit_http import AuditMiddleware
from app.audit_http import router as audit_router
from app.bank.router import router as bank_router
from app.intent import asr, jev, voiceprint
from app.intent.router import router as intent_router
from app.validation.router import router as validation_router
from web import presentation_v4
from web.jev import router as presentation_router


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Keep one warm connection to the Jev gateway for the server's life (app/intent/jev.py).
    await jev.open_connection()
    await presentation_v4.open_connection()
    await llm.open_connection()
    if config.ASR_PROVIDER != "tencent":
        await asyncio.to_thread(asr.preload)  # MERaLiON weights take a while (D-053)
    if config.VOICEPRINT_ENABLED:
        try:
            await asyncio.to_thread(voiceprint.preload)  # 26 MB download on first start (D-054)
        except Exception:
            logging.getLogger(__name__).warning("Speaker model unavailable; voice checks fail open")

    async def retention():
        while True:
            try:
                await asyncio.to_thread(evidence.purge_expired)
            except AuditError:
                logging.getLogger(__name__).error("Audit retention maintenance failed")
            await asyncio.sleep(3600)

    maintenance = asyncio.create_task(retention())
    try:
        yield
    finally:
        maintenance.cancel()
        with suppress(asyncio.CancelledError):
            await maintenance
        await llm.close_connection()
        await presentation_v4.close_connection()
        await jev.close_connection()


# Security headers for every response. Next.js inlines its bootstrap scripts, so scripts and
# styles allow 'unsafe-inline'; nothing loads from another site and nothing may frame the page.
_HEADERS = [
    (
        b"content-security-policy",
        b"default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
        b"img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; "
        b"frame-ancestors 'none'; base-uri 'none'; form-action 'self'; object-src 'none'",
    ),
    (b"x-frame-options", b"DENY"),
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
    (b"permissions-policy", b"microphone=(self), camera=(), geolocation=()"),
]
# Rate group of each endpoint (config.RATE_PER_MINUTE). Other /api/presentation-* paths spend
# Jev calls too and count as "model"; every remaining /api/ path is "read".
_RATE_GROUPS = {
    "/api/understand": "model",
    "/api/transcribe": "model",
    "/api/preview": "preview",
    "/api/validate": "validate",
    "/api/validation/cancel": "write",
    "/api/challenge": "write",
    "/api/execute": "write",
    "/api/device/register": "write",
    "/api/audit/presentation": "write",
}
# Rate state: tokens and last refill per (client, group), and recent calls per global group.
RATE_BUCKETS: dict[tuple[str, str], tuple[float, float]] = {}
RATE_WINDOW: dict[str, list[float]] = {}
_COOKIE = re.compile(rb"(?:^|;)\s*dcta_client=([A-Za-z0-9_-]{43})(?:;|$)")


class _Guard:
    """Outermost middleware: security headers, body-size caps and per-IP rate limits.

    ponytail: in-memory buckets for one process; move to the reverse proxy (nginx limit_req)
    or a shared store if the app ever runs as several workers.
    """

    def __init__(self, app):
        self.app = app

    @staticmethod
    def _allowed(client: str, group: str) -> bool:
        now = time.monotonic()
        window = RATE_WINDOW.setdefault(group, [])
        window[:] = [t for t in window if now - t < 60]
        if len(window) >= config.RATE_GLOBAL_PER_MINUTE.get(group, float("inf")):
            return False
        rate = config.RATE_PER_MINUTE[group]
        tokens, last = RATE_BUCKETS.get((client, group), (rate, now))
        tokens = min(rate, tokens + (now - last) * rate / 60)
        if tokens < 1:
            return False
        if len(RATE_BUCKETS) > 10_000:
            # A bucket idle for a minute is full again, the same as a new one, so dropping it
            # forgets nothing. ponytail: active buckets still grow with the number of clients.
            for key in [k for k, (_, t) in RATE_BUCKETS.items() if now - t >= 60]:
                del RATE_BUCKETS[key]
        RATE_BUCKETS[(client, group)] = (tokens - 1, now)
        if group in config.RATE_GLOBAL_PER_MINUTE:
            window.append(now)
        return True

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        headers = list(_HEADERS)
        path = scope.get("path", "")

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                message["headers"] = list(message.get("headers", [])) + headers
                if path.startswith("/api/") and not any(
                    k.lower() == b"cache-control" for k, _ in message["headers"]
                ):  # bank data and errors are never cached (L9)
                    message["headers"].append((b"cache-control", b"no-store"))
            await send(message)

        if not path.startswith("/api/"):
            return await self.app(scope, receive, send_with_headers)
        # Every browser gets a secret HttpOnly cookie; conversations belong to it (H1 in the
        # 15 Oct audit), so a leaked session ID alone can't answer, change or sign them.
        found = _COOKIE.search(dict(scope.get("headers", [])).get(b"cookie", b""))
        client_id = found.group(1).decode() if found else secrets.token_urlsafe(32)
        transaction_state.client.set(client_id)
        if not found:
            secure = "; Secure" if scope.get("scheme") == "https" else ""
            cookie = f"dcta_client={client_id}; Path=/api; HttpOnly; SameSite=Strict{secure}"
            headers.append((b"set-cookie", cookie.encode()))
        limit = config.MAX_AUDIO_BYTES if path == "/api/transcribe" else config.MAX_REQUEST_BYTES
        length = dict(scope.get("headers", [])).get(b"content-length", b"0")
        if not length.isdigit() or len(length) > 12 or int(length) > limit:
            return await _error(413, "Request too large.")(scope, receive, send_with_headers)
        group = _RATE_GROUPS.get(path, "model" if path.startswith("/api/presentation-") else "read")
        if not self._allowed(_rate_key((scope.get("client") or ("unknown", 0))[0]), group):
            response = _error(429, "Too many requests. Please wait a moment and try again.")
            response.headers["retry-after"] = "10"
            return await response(scope, receive, send_with_headers)
        # Content-Length can be left out (chunked uploads), so read what actually arrives, up
        # to the cap, and hand the app the same bytes.
        body, more = bytearray(), True
        while more:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            more = message.get("more_body", False)
            if len(body) > limit:
                return await _error(413, "Request too large.")(scope, receive, send_with_headers)
        replayed = False

        async def replay():
            nonlocal replayed
            if replayed:
                return await receive()
            replayed = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        await self.app(scope, replay, send_with_headers)


def _rate_key(client: str) -> str:
    """One IPv6 user usually holds a whole /64, so it shares one bucket."""
    try:
        address = ipaddress.ip_address(client)
    except ValueError:
        return client
    if address.version == 4:
        return client
    return str(ipaddress.ip_network(f"{client}/64", strict=False))


app = FastAPI(
    title="DCTA voice banking agent",
    lifespan=_lifespan,
    docs_url="/docs" if config.API_DOCS else None,
    redoc_url=None,
    openapi_url="/openapi.json" if config.API_DOCS else None,
)
app.add_middleware(AuditMiddleware)
app.include_router(audit_router)
app.include_router(intent_router)
app.include_router(validation_router)
app.include_router(bank_router)
app.include_router(presentation_router)
# The UI's static export (`npm run build` in web/, D-004) is served after the API routes, so
# one server runs the whole app. Without a build, the server is API only.
_WEB = Path(__file__).resolve().parent.parent / "web" / "out"


@app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def _unknown_api(path: str) -> None:
    # Keeps unknown API paths on the JSON 404 of CONTRACTS §6 instead of the UI's 404 page.
    raise HTTPException(404, "Not found.")


if _WEB.is_dir():
    app.mount("/", StaticFiles(directory=_WEB, html=True), name="web")

_ERROR_CODES = {
    400: "bad_request",
    404: "not_found",
    413: "too_large",
    429: "rate_limited",
    502: "upstream_error",
    503: "upstream_error",
}


def _error(status_code: int, message: str) -> JSONResponse:
    code = _ERROR_CODES.get(status_code, "bad_request")
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status_code)


@app.exception_handler(RequestValidationError)
async def _malformed_request(request: Request, exc: RequestValidationError) -> JSONResponse:
    # FastAPI answers 422 by default; the contract promises 400 with a readable message.
    problems = [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()]
    return _error(400, "; ".join(problems))


@app.exception_handler(HTTPException)
async def _http_error(request: Request, exc: HTTPException) -> JSONResponse:
    return _error(exc.status_code, str(exc.detail))


@app.exception_handler(AuditError)
async def _audit_error(request: Request, exc: AuditError) -> JSONResponse:
    return _error(503, "Audit storage unavailable. Request stopped.")


# Added last so it wraps everything, including the audit middleware and the static UI.
app.add_middleware(_Guard)
