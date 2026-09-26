"""
Server hardening for the Aiva Nex Agent local server.

  * CORS locked to the extension origin (AIVA_EXTENSION_ORIGIN, comma-
    separated for several) plus http://localhost / http://127.0.0.1 on any
    port for development - never "*".
  * Shared-secret handshake: every request except the open paths below must
    carry the token in the X-Aiva-Token header, else 401. The token comes
    from AIVA_SHARED_TOKEN, or from server/.aiva_token, which is created with
    a random value on first run (git-ignored). Paste it into the extension's
    Settings panel once.
  * Request bodies above AIVA_MAX_BODY_BYTES (default 256 KB) get 413.
  * Validation errors (422) never echo the submitted input back.

Nothing in here logs request bodies, headers or token values.
"""

import hmac
import json
import logging
import os
import secrets
from pathlib import Path
from typing import Iterable, List, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

log = logging.getLogger("aiva.security")

TOKEN_HEADER = "X-Aiva-Token"
TOKEN_FILE = Path(__file__).resolve().with_name(".aiva_token")
DEFAULT_MAX_BODY_BYTES = 256 * 1024
# /perceive carries one masked, downscaled (<=1280 px) JPEG as base64, which is
# routinely larger than 256 KB, so it gets its own (still bounded) cap.
DEFAULT_MAX_PERCEIVE_BYTES = 4 * 1024 * 1024
# Paths reachable without the token. /health only says "ok"; the docs pages
# describe the API but carry no data.
OPEN_PATHS = frozenset({"/health", "/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"})
LOCALHOST_ORIGIN_REGEX = r"^http://(localhost|127\.0\.0\.1)(:\d+)?$"


def load_or_create_token(path: Optional[Path] = None) -> str:
    """AIVA_SHARED_TOKEN if set, else the token file (created on first run)."""
    env = os.getenv("AIVA_SHARED_TOKEN", "").strip()
    if env:
        return env
    path = Path(path) if path else TOKEN_FILE
    if path.exists():
        token = path.read_text(encoding="utf-8").strip()
        if token:
            return token
    token = secrets.token_urlsafe(32)
    path.write_text(token + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    log.warning("Created a new shared token in %s - paste it into the extension Settings.", path)
    return token


def allowed_origins() -> List[str]:
    raw = os.getenv("AIVA_EXTENSION_ORIGIN", "")
    origins = [o.strip().rstrip("/") for o in raw.split(",") if o.strip()]
    for o in origins:
        if o == "*":
            raise ValueError("AIVA_EXTENSION_ORIGIN must be a chrome-extension://<id> origin, not '*'.")
    return origins


def max_body_bytes() -> int:
    try:
        return max(1024, int(os.getenv("AIVA_MAX_BODY_BYTES", DEFAULT_MAX_BODY_BYTES)))
    except ValueError:
        return DEFAULT_MAX_BODY_BYTES


def max_perceive_bytes() -> int:
    try:
        return max(1024, int(os.getenv("AIVA_MAX_PERCEIVE_BYTES", DEFAULT_MAX_PERCEIVE_BYTES)))
    except ValueError:
        return DEFAULT_MAX_PERCEIVE_BYTES


async def _send_json(send, status: int, detail: str) -> None:
    body = json.dumps({"detail": detail}).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": body})


class SharedTokenMiddleware:
    """401 unless the X-Aiva-Token header matches (constant-time compare)."""

    def __init__(self, app, token: str, open_paths: Iterable[str] = OPEN_PATHS):
        self.app = app
        self.token = token.encode("utf-8")
        self.open_paths = frozenset(open_paths)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") == "OPTIONS" or scope.get("path") in self.open_paths:
            return await self.app(scope, receive, send)
        supplied = b""
        wanted = TOKEN_HEADER.lower().encode("latin-1")
        for name, value in scope.get("headers") or []:
            if name == wanted:
                supplied = value
                break
        if not supplied or not hmac.compare_digest(supplied, self.token):
            return await _send_json(send, 401, "Missing or wrong X-Aiva-Token. Set the server token in the extension Settings.")
        return await self.app(scope, receive, send)


class BodySizeLimitMiddleware:
    """413 when the request body exceeds max_bytes (checks header and stream)."""

    def __init__(self, app, max_bytes: int = DEFAULT_MAX_BODY_BYTES, path_limits: Optional[dict] = None):
        self.app = app
        self.default_max = max_bytes
        self.path_limits = dict(path_limits or {})

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        max_bytes = self.path_limits.get(scope.get("path"), self.default_max)
        for name, value in scope.get("headers") or []:
            if name == b"content-length":
                try:
                    if int(value) > max_bytes:
                        return await _send_json(send, 413, f"Request body too large (limit {max_bytes} bytes).")
                except ValueError:
                    return await _send_json(send, 400, "Invalid Content-Length header.")
        # Buffer the (small) body so a chunked request without Content-Length
        # is limited too, then replay it to the app.
        chunks, total, more = [], 0, True
        while more:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            total += len(chunk)
            if total > max_bytes:
                return await _send_json(send, 413, f"Request body too large (limit {max_bytes} bytes).")
            chunks.append(chunk)
            more = message.get("more_body", False)
        body = b"".join(chunks)
        sent = False

        async def replay():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        return await self.app(scope, replay, send)


async def _validation_error_handler(_request: Request, exc: RequestValidationError):
    # FastAPI's default 422 body echoes the submitted value ("input"), which
    # could be page text. Return only where and why validation failed.
    errors = [{"loc": list(e.get("loc", [])), "msg": e.get("msg", ""), "type": e.get("type", "")} for e in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": errors})


def install_security(app: FastAPI, token: Optional[str] = None) -> str:
    """Wire CORS, token check, body cap and the 422 handler. Returns the token."""
    token = token or load_or_create_token()
    origins = allowed_origins()
    # Starlette runs the LAST added middleware FIRST: CORS outermost (so 401/
    # 413 responses still carry CORS headers), then the body cap, then auth.
    app.add_middleware(SharedTokenMiddleware, token=token)
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=max_body_bytes(), path_limits={"/perceive": max_perceive_bytes()})
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_origin_regex=LOCALHOST_ORIGIN_REGEX,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", TOKEN_HEADER],
    )
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    if not origins:
        log.warning(
            "AIVA_EXTENSION_ORIGIN is not set - CORS allows only localhost pages. "
            "The extension still works (host_permissions), but set it to chrome-extension://<id> for the demo."
        )
    return token
