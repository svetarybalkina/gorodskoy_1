import logging

from fastapi.testclient import TestClient

from app.bot.runner import run_bot_if_configured
from app.core.config import Settings
from app.main import create_app


def test_health_endpoint() -> None:
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_public_homepage_opens() -> None:
    client = TestClient(create_app())

    response = client.get("/")

    assert response.status_code == 200
    assert "Городской справочник" in response.text


def test_admin_placeholder_opens() -> None:
    client = TestClient(create_app())

    response = client.get("/admin")

    assert response.status_code == 200
    assert "Авторизация будет добавлена" in response.text


def test_ads_disabled_by_default() -> None:
    settings = Settings()

    assert settings.ads_enabled is False


def test_bot_is_disabled_without_token(caplog) -> None:
    settings = Settings(TELEGRAM_BOT_TOKEN="")

    with caplog.at_level(logging.INFO):
        state = run_bot_if_configured(settings)

    assert state.enabled is False
    assert "Telegram bot is disabled" in state.reason
    assert "Telegram bot is disabled" in caplog.text
