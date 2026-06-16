from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from http.cookies import SimpleCookie
from typing import Any

from starlette.datastructures import MutableHeaders
from starlette.requests import HTTPConnection
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class SessionMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        secret_key: str,
        session_cookie: str = "session",
        max_age: int = 14 * 24 * 60 * 60,
        same_site: str = "lax",
        https_only: bool = False,
    ) -> None:
        self.app = app
        self.secret_key = secret_key.encode("utf-8")
        self.session_cookie = session_cookie
        self.max_age = max_age
        self.same_site = same_site
        self.https_only = https_only

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        connection = HTTPConnection(scope)
        initial_session_was_empty = True
        scope["session"] = {}
        if self.session_cookie in connection.cookies:
            session = self._decode_session(connection.cookies[self.session_cookie])
            if session is not None:
                scope["session"] = session
                initial_session_was_empty = False

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                session = scope.get("session", {})
                if session:
                    headers.append("Set-Cookie", self._build_cookie(self._encode_session(session)))
                elif not initial_session_was_empty:
                    headers.append("Set-Cookie", self._build_cookie("null", expires=True))
            await send(message)

        await self.app(scope, receive, send_wrapper)

    def _encode_session(self, session: dict[str, Any]) -> str:
        payload = {
            "data": session,
            "expires": int(time.time()) + self.max_age,
        }
        raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        encoded = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
        signature = hmac.new(self.secret_key, encoded.encode("ascii"), hashlib.sha256).hexdigest()
        return f"{encoded}.{signature}"

    def _decode_session(self, value: str) -> dict[str, Any] | None:
        try:
            encoded, signature = value.rsplit(".", 1)
        except ValueError:
            return None

        expected = hmac.new(self.secret_key, encoded.encode("ascii"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None

        padding = "=" * (-len(encoded) % 4)
        try:
            payload = json.loads(base64.urlsafe_b64decode(encoded + padding).decode("utf-8"))
        except (ValueError, json.JSONDecodeError):
            return None

        if not isinstance(payload, dict) or payload.get("expires", 0) < time.time():
            return None
        data = payload.get("data")
        return data if isinstance(data, dict) else None

    def _build_cookie(self, value: str, *, expires: bool = False) -> str:
        cookie: SimpleCookie[str] = SimpleCookie()
        cookie[self.session_cookie] = value
        cookie[self.session_cookie]["path"] = "/"
        cookie[self.session_cookie]["httponly"] = True
        cookie[self.session_cookie]["samesite"] = self.same_site
        if self.https_only:
            cookie[self.session_cookie]["secure"] = True
        if expires:
            cookie[self.session_cookie]["expires"] = "Thu, 01 Jan 1970 00:00:00 GMT"
            cookie[self.session_cookie]["max-age"] = "0"
        else:
            cookie[self.session_cookie]["max-age"] = str(self.max_age)
        return cookie.output(header="").strip()
