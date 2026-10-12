"""FastAPI app: mounts the three routers and shapes errors as CONTRACTS §6 says.

Owner: Winston (shared)
Diagram box: none; wiring only
Contract: docs/CONTRACTS.md §3 (endpoints) and §6 (errors)
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from app.bank.router import router as bank_router
from app.intent import jev
from app.intent.router import router as intent_router
from app.validation.router import router as validation_router
from web.jev import router as presentation_router


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Keep one warm connection to the Jev gateway for the server's life (app/intent/jev.py).
    await jev.open_connection()
    yield
    await jev.close_connection()


app = FastAPI(title="DCTA voice banking agent", lifespan=_lifespan)
app.include_router(intent_router)
app.include_router(validation_router)
app.include_router(bank_router)
app.include_router(presentation_router)
# ponytail: web/ gets mounted here once D-004 picks the stack and its build folder.

_ERROR_CODES = {400: "bad_request", 404: "not_found", 502: "upstream_error"}


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
