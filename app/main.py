from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from app.admin.routes import router as admin_router
from app.bot.runner import run_bot_if_configured
from app.core.config import get_settings
from app.core.logging import configure_logging


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    settings = get_settings()
    run_bot_if_configured(settings)
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.service_name, lifespan=lifespan)
    app.include_router(admin_router)

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return (
            "<!doctype html>"
            "<html lang=\"ru\">"
            "<head><meta charset=\"utf-8\"><title>"
            f"{settings.service_name}"
            "</title></head>"
            "<body>"
            f"<h1>{settings.service_name}</h1>"
            "<p>Информационно-поисковый сервис справочного характера.</p>"
            "<p>Сервис помогает найти опубликованные ответы администрации, "
            "но не является официальным ресурсом администрации.</p>"
            "<p><a href=\"/admin\">Админка</a></p>"
            "</body></html>"
        )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": settings.service_name}

    return app


app = create_app()
