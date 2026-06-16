from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.admin.routes import router as admin_router
from app.bot.runner import run_bot_if_configured
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.sessions import SessionMiddleware
from app.public.routes import router as public_router
from app.services.bootstrap import bootstrap_database


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    settings = get_settings()
    if settings.auto_db_bootstrap:
        bootstrap_database(settings)
    run_bot_if_configured(settings)
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.service_name, lifespan=lifespan)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        session_cookie="gorodskoy_admin_session",
        max_age=7 * 24 * 60 * 60,
        same_site="lax",
        https_only=False,
    )
    app.mount("/static", StaticFiles(directory="app/static"), name="static")
    app.include_router(public_router)
    app.include_router(admin_router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": settings.service_name}

    return app


app = create_app()
