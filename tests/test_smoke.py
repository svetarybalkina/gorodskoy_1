import logging
from collections.abc import Generator

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.bot.runner import run_bot_if_configured
from app.core.config import Settings
from app.db.base import Base
from app.db.seed import seed_initial_data
from app.db.session import create_database_engine, get_db_session
from app.main import create_app


def create_seeded_client() -> TestClient:
    engine = create_database_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    with session_factory() as seed_session:
        seed_initial_data(seed_session)

    def override_db() -> Generator[Session, None, None]:
        with session_factory() as db_session:
            yield db_session

    app = create_app()
    app.dependency_overrides[get_db_session] = override_db
    return TestClient(app)


def test_health_endpoint() -> None:
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_public_homepage_opens() -> None:
    client = create_seeded_client()

    response = client.get("/")

    assert response.status_code == 200
    assert "Городской справочник" in response.text
    assert "Задайте вопрос по ЖКХ" in response.text


def test_admin_redirects_to_login() -> None:
    client = TestClient(create_app())

    response = client.get("/admin", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login"


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
