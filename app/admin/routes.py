from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.core.config import get_settings


router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("", response_class=HTMLResponse)
async def admin_index() -> str:
    settings = get_settings()
    return (
        "<!doctype html>"
        "<html lang=\"ru\">"
        "<head><meta charset=\"utf-8\"><title>Админка</title></head>"
        "<body>"
        f"<h1>Админка: {settings.service_name}</h1>"
        "<p>Базовая страница админки. Авторизация будет добавлена в задаче №4.</p>"
        "</body></html>"
    )
