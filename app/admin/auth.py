from __future__ import annotations

import hmac
import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse

from app.core.config import Settings, get_settings

ADMIN_SESSION_KEY = "admin_user"
CSRF_SESSION_KEY = "admin_csrf_token"


def verify_admin_credentials(username: str, password: str, settings: Settings) -> bool:
    return hmac.compare_digest(username, settings.admin_username) and hmac.compare_digest(
        password, settings.admin_password
    )


def login_admin(request: Request, username: str) -> None:
    request.session[ADMIN_SESSION_KEY] = username
    ensure_csrf_token(request)


def logout_admin(request: Request) -> None:
    request.session.clear()


def ensure_csrf_token(request: Request) -> str:
    token = request.session.get(CSRF_SESSION_KEY)
    if not isinstance(token, str) or not token:
        token = secrets.token_urlsafe(32)
        request.session[CSRF_SESSION_KEY] = token
    return token


def validate_csrf_token(request: Request, submitted_token: str | None) -> None:
    session_token = request.session.get(CSRF_SESSION_KEY)
    if (
        not isinstance(session_token, str)
        or not submitted_token
        or not hmac.compare_digest(session_token, submitted_token)
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token")


def get_current_admin(request: Request) -> str:
    admin_user = request.session.get(ADMIN_SESSION_KEY)
    if isinstance(admin_user, str) and admin_user:
        ensure_csrf_token(request)
        return admin_user
    raise HTTPException(
        status_code=status.HTTP_303_SEE_OTHER,
        headers={"Location": "/admin/login"},
    )


CurrentAdmin = Annotated[str, Depends(get_current_admin)]


def redirect_to_login() -> RedirectResponse:
    return RedirectResponse("/admin/login", status_code=status.HTTP_303_SEE_OTHER)
