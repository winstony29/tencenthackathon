"""Bounded API evidence capture and admin-only reconstruction.

Owner: shared
Contract: docs/CONTRACTS.md, audit evidence extension
"""

import asyncio
import hmac
import json
import re
import time
import uuid
from urllib.parse import parse_qsl

from fastapi import APIRouter, Header, HTTPException, Query
from starlette.responses import JSONResponse

from app import config, evidence
from app.audit import AuditError
from app.contracts import PresentationEvidenceRequest

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("/timeline")
async def timeline(
    session_id: str | None = Query(default=None, max_length=128),
    limit: int = Query(default=100, ge=1, le=500),
    include_evidence: bool = False,
    authorization: str = Header(default=""),
):
    expected = "Bearer " + config.AUDIT_ADMIN_TOKEN
    if not config.AUDIT_ADMIN_TOKEN or not hmac.compare_digest(
        authorization.encode(), expected.encode()
    ):
        raise HTTPException(403, "Audit administrator access required.")
    result = evidence.timeline(session_id, limit, include_evidence)
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.post("/presentation")
async def presentation(body: PresentationEvidenceRequest):
    # This is a browser claim, not proof of pixels shown or of transaction authorization.
    evidence.record(
        "presentation_client_reported",
        body.model_dump(mode="json"),
        session_id=body.session_id,
        draft_id=body.draft_id or "",
    )
    return {"status": "recorded"}


_PATHS = {
    "/api/understand",
    "/api/preview",
    "/api/validate",
    "/api/validation/cancel",
    "/api/challenge",
    "/api/execute",
    "/api/device/register",
    "/api/accounts",
    "/api/quote",
    "/api/fx",
}  # /api/audit/presentation records its own event; capturing it here stored it twice (H3)


class AuditMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if scope["type"] != "http" or not (path in _PATHS or path.startswith("/api/presentation-")):
            return await self.app(scope, receive, send)
        request_id = "req_" + uuid.uuid4().hex
        token = evidence.context.set({"request_id": request_id})
        started = time.monotonic()
        try:
            body = bytearray()
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    evidence.record("request_disconnected", {"path": path})
                    return
                body.extend(message.get("body", b""))
                if len(body) > config.AUDIT_MAX_BYTES:
                    return await JSONResponse({"error": {"message": "Request too large."}}, 413)(
                        scope, receive, send
                    )
                if not message.get("more_body"):
                    break
            try:
                data = json.loads(body) if body else {}
            except ValueError:
                # Do not persist unparsed bodies: they may contain secret fields.
                data = {"malformed_json": True}
            headers = dict(scope.get("headers", []))
            session = headers.get(b"x-audit-session", b"").decode("ascii", errors="ignore")
            draft = data.get("draft", {}) if isinstance(data, dict) else {}
            draft = draft if isinstance(draft, dict) else {}
            if isinstance(data, dict):
                session = draft.get("session_id") or data.get("session_id") or session
            links = {"request_id": request_id}
            if isinstance(session, str) and re.fullmatch(r"s_[A-Za-z0-9_-]{1,100}", session):
                links["session_id"] = session
            evidence.context.set(links)
            evidence.record(
                "request_received",
                {
                    "path": path,
                    "input": data,
                    "correlation_source": "client_claim",
                    "query": {
                        k: v
                        for k, v in parse_qsl(
                            scope.get("query_string", b"").decode("utf-8", errors="replace")
                        )
                        if k in {"user_id", "ticker", "from", "to"}
                    },
                },
            )
            received = False
            messages = []
            size = 0

            async def replay():
                nonlocal received
                if not received:
                    received = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                return await receive()

            async def capture(message):
                nonlocal size
                size += len(message.get("body", b""))
                if size > config.AUDIT_MAX_BYTES:
                    raise AuditError("Response exceeds audit size limit.")
                messages.append(message)

            await self.app(scope, replay, capture)
            response = b"".join(m.get("body", b"") for m in messages)
            try:
                output = json.loads(response)
            except ValueError:
                output = {"non_json_response": True}
            status = next(m["status"] for m in messages if m["type"] == "http.response.start")
            evidence.record(
                "response_completed",
                {
                    "path": path,
                    "output": output,
                    "http_status": status,
                    "duration_ms": round((time.monotonic() - started) * 1000),
                },
            )
            for message in messages:
                if message["type"] == "http.response.start":
                    message["headers"] = list(message.get("headers", [])) + [
                        (b"x-audit-request-id", request_id.encode()),
                        (b"cache-control", b"no-store"),
                    ]
                await send(message)
        except asyncio.CancelledError:
            evidence.record("request_cancelled", {"path": path})
            raise
        except AuditError:
            await JSONResponse(
                {"error": {"message": "Audit storage unavailable. Request stopped."}}, 503
            )(scope, receive, send)
        except Exception as error:
            try:
                evidence.record(
                    "request_failed", {"path": path, "error_type": type(error).__name__}
                )
            except AuditError:
                pass  # Return failure either way; never report an unaudited success.
            raise
        finally:
            evidence.context.reset(token)
