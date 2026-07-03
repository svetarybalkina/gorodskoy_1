import logging
from collections.abc import Generator
from pathlib import Path

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


def test_browser_acceptance_smoke_routes_open_or_redirect() -> None:
    client = create_seeded_client()

    health = client.get("/health")
    homepage = client.get("/")
    search = client.get("/search?q=отопление")
    admin = client.get("/admin", follow_redirects=False)
    legal = client.get("/legal/disclaimer")

    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert homepage.status_code == 200
    assert "Задайте вопрос по ЖКХ" in homepage.text
    assert search.status_code == 200
    assert "технических терминов" not in search.text.lower()
    assert admin.status_code == 303
    assert admin.headers["location"] == "/admin/login"
    assert legal.status_code == 200
    assert "не является официальным ресурсом администрации" in legal.text


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


def test_action_controls_have_shared_spacing_styles() -> None:
    css = Path("app/static/styles.css").read_text(encoding="utf-8")

    assert ".action-grid" in css
    assert ".table-actions" in css
    assert "gap: 12px;" in css
    assert "gap: 8px;" in css
    assert ".search-row button" in css


def test_readme_documents_public_launch_blocker_and_acceptance_commands() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "готова для локальной демонстрации" in readme
    assert "не готова к публичному запуску" in readme
    assert "[НАЗВАНИЕ СЕРВИСА]" in readme
    assert "[ВЛАДЕЛЕЦ / ОПЕРАТОР СЕРВИСА]" in readme
    assert "[ДОМЕН СЕРВИСА]" in readme
    assert "[EMAIL ДЛЯ ОБРАЩЕНИЙ]" in readme
    assert "[КОНТАКТЫ ОПЕРАТОРА]" in readme
    assert "SERVICE_NAME" in readme
    assert "PUBLIC_BASE_URL" in readme
    assert "docker compose run --rm app pytest" in readme
    assert "docker compose up -d --build" in readme
    assert "http://localhost:8000/search?q=отопление" in readme


def test_env_example_contains_only_safe_placeholders_for_secrets() -> None:
    env_example = Path(".env.example").read_text(encoding="utf-8")

    assert "your_telegram_bot_token_here" in env_example
    assert "your_admin_username" in env_example
    assert "your_strong_admin_password_here" in env_example
    assert "replace_with_long_random_secret" in env_example
    assert "change_me" not in env_example
    assert "ADMIN_USERNAME=admin" not in env_example
    assert "ADMIN_PASSWORD=admin" not in env_example
    assert "SECRET_KEY=secret" not in env_example


def test_private_files_are_ignored_by_git_and_docker_context() -> None:
    gitignore = Path(".gitignore").read_text(encoding="utf-8")
    dockerignore = Path(".dockerignore").read_text(encoding="utf-8")

    for pattern in [
        ".env",
        "data/*",
        "imports/*",
        "exports/*",
        "logs/*",
        "*.db",
        "*.sqlite",
        "*.sqlite3",
        "*.log",
        "ChatExport*/",
        "Telegram Desktop/",
    ]:
        assert pattern in gitignore
        assert pattern in dockerignore

    assert "imports/*.json" in gitignore
    assert "exports/*.json" in gitignore
