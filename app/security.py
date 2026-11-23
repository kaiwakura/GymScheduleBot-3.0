"""Request protection: CSRF tokens, a request size limit, and limits on Gemini calls.

CSRF: each session gets a random token. The page embeds it, JavaScript sends it in an
X-CSRF-Token header, and plain forms send it as a hidden `csrf` field. A request from
another site can't read the token, so it can't forge one. (The session cookie is also
SameSite=Lax, which already stops most cross-site posts; this is the second layer.)

The AI Coach endpoints are public, and anyone can load the page to get a token, so CSRF
alone doesn't stop a script from spending the Gemini quota. ``AiQuota`` caps calls per
visitor and for the whole site; past a limit the coach falls back to its keyword parser.
"""

from __future__ import annotations

import ipaddress
import json
import os
import secrets
import time
from collections import deque
from threading import Lock

from fastapi import HTTPException, Request

SESSION_KEY = "csrf"


def csrf_token(session: dict) -> str:
    if SESSION_KEY not in session:
        session[SESSION_KEY] = secrets.token_urlsafe(32)
    return session[SESSION_KEY]


async def require_csrf(request: Request) -> None:
    """FastAPI dependency for state-changing routes."""
    expected = request.session.get(SESSION_KEY)
    supplied = request.headers.get("x-csrf-token")
    if supplied is None and request.headers.get("content-type", "").startswith(
        ("application/x-www-form-urlencoded", "multipart/form-data")
    ):
        supplied = (await request.form()).get("csrf")
    if not expected or not supplied or not secrets.compare_digest(expected, str(supplied)):
        raise HTTPException(status_code=403, detail="This page is out of date. Please reload and try again.")


# ------------------------------------------------------------ request size ---
MAX_BODY_BYTES = 6 * 1024 * 1024  # a 4 MB screenshot is about 5.4 MB once base64-encoded


class BodySizeLimit:
    """Turn away request bodies over ``MAX_BODY_BYTES`` before they're read into memory.

    Browsers always send Content-Length with these requests, so a body without one
    (a chunked upload) is refused too, rather than counted while it streams in.
    """

    def __init__(self, app, max_bytes: int = MAX_BODY_BYTES):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["method"] in ("POST", "PUT", "PATCH"):
            headers = dict(scope["headers"])
            length = headers.get(b"content-length", b"")
            if b"chunked" in headers.get(b"transfer-encoding", b"") or not length.isdigit():
                return await _reject(send, 411, "Send a Content-Length header.")
            if int(length) > self.max_bytes:
                return await _reject(send, 413, "That request is too large.")
        await self.app(scope, receive, send)


async def _reject(send, status: int, message: str) -> None:
    body = json.dumps({"error": message}).encode()
    headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


# ----------------------------------------------------------- Gemini quota ---
AI_PER_MINUTE = 5  # per visitor: enough for a real conversation
AI_PER_DAY = 40  # per visitor
DAY = 24 * 60 * 60


def ai_daily_limit() -> int:
    """Gemini calls per 24 hours for the whole site (set AI_DAILY_LIMIT to change it)."""
    try:
        return max(0, int(os.getenv("AI_DAILY_LIMIT", "300")))
    except ValueError:
        return 300


def client_ip(request: Request) -> str:
    """Who's asking, for the AI limits: the visitor's IP address (IPv6 grouped by /64).

    On Render, every request passes through Cloudflare, which sets CF-Connecting-IP to the
    address that connected to it and rejects requests that try to send their own. So that
    header is trusted only when the app runs on Render (Render sets RENDER=true) and holds
    one valid IP. X-Forwarded-For and True-Client-IP are never trusted: a client can write
    them. Anywhere else, or if the header is missing, the direct connection is used; on
    Render that's the proxy, which puts everyone in one bucket: stricter, never looser.
    """
    header = request.headers.get("cf-connecting-ip", "") if os.getenv("RENDER") == "true" else ""
    for candidate in (header, request.client.host if request.client else ""):
        try:
            address = ipaddress.ip_address(candidate.strip())
        except ValueError:
            continue
        if address.version == 6:  # one home or phone usually gets a whole /64 to choose from
            return str(ipaddress.ip_network(f"{address}/64", strict=False))
        return str(address)
    return "unknown"


class AiQuota:
    """Counts Gemini calls in memory, per visitor and for the whole site, over sliding windows.

    In memory is enough for one server process (the app runs as a single Render
    instance); a restart resets the counts, and the site-wide cap still bounds the damage.
    """

    def __init__(self) -> None:
        self._by_visitor: dict[str, deque[float]] = {}
        self._everyone: deque[float] = deque()
        self._lock = Lock()  # FastAPI runs these routes in a thread pool

    def allow(self, visitor: str, now: float | None = None) -> bool:
        """Record one call and return True, or return False if any limit is reached."""
        now = time.time() if now is None else now
        with self._lock:
            _drop_before(self._everyone, now - DAY)
            mine = self._by_visitor.setdefault(visitor, deque())
            _drop_before(mine, now - DAY)
            last_minute = sum(1 for t in mine if t > now - 60)
            if len(self._everyone) >= ai_daily_limit() or len(mine) >= AI_PER_DAY or last_minute >= AI_PER_MINUTE:
                return False
            mine.append(now)
            self._everyone.append(now)
            if len(self._by_visitor) > 5000:  # forget visitors with nothing in the last day
                self._by_visitor = {k: v for k, v in self._by_visitor.items() if v and v[-1] > now - DAY}
            return True


def _drop_before(calls: deque[float], cutoff: float) -> None:
    while calls and calls[0] <= cutoff:
        calls.popleft()


ai_quota = AiQuota()
