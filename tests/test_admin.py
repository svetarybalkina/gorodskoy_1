from __future__ import annotations

import re
from collections.abc import Generator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings
from app.db.base import Base
from app.db.enums import MaterialStatus, MaterialType, SourceKind
from app.db.models import AdminNote, Material
from app.db.repositories import MaterialRepository, SourceRepository, TaxonomyRepository
from app.db.seed import seed_initial_data
from app.db.session import create_database_engine, get_db_session
from app.main import create_app


@pytest.fixture()
def admin_app_context() -> tuple[TestClient, sessionmaker[Session]]:
    engine = create_database_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    with session_factory() as seed_session:
        seed_initial_data(seed_session)
        seed_admin_materials(seed_session)

    def override_db() -> Generator[Session, None, None]:
        with session_factory() as db_session:
            yield db_session

    app = create_app()
    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_settings] = lambda: Settings(
        AUTO_DB_BOOTSTRAP=False,
        ADMIN_USERNAME="admin",
        ADMIN_PASSWORD="secret",
        SECRET_KEY="test-secret",
    )
    return TestClient(app), session_factory


def seed_admin_materials(session: Session) -> None:
    taxonomy = TaxonomyRepository(session)
    source = SourceRepository(session).create(
        code="admin-source",
        name="Официальный канал администрации",
        kind=SourceKind.OFFICIAL_CHANNEL,
    )
    housing = taxonomy.get_topic_by_slug("housing")
    assert housing is not None
    heating = taxonomy.get_category(topic_id=housing.id, slug="heating")
    water = taxonomy.get_category(topic_id=housing.id, slug="water")
    animals_category = taxonomy.get_category(topic_id=housing.id, slug="animals")
    assert heating is not None
    assert water is not None
    assert animals_category is not None

    repo = MaterialRepository(session)
    active = repo.create(
        source_id=source.id,
        topic_id=housing.id,
        category_id=heating.id,
        material_type=MaterialType.OFFICIAL_ANSWER,
        status=MaterialStatus.ACTIVE,
        published_at=datetime(2026, 6, 16, tzinfo=UTC),
        original_text="Оригинальный официальный текст про отопление.",
        public_text="Публичная версия про отопление.",
    )
    repo.create(
        source_id=source.id,
        topic_id=housing.id,
        category_id=water.id,
        material_type=MaterialType.OFFICIAL_POST,
        status=MaterialStatus.HIDDEN,
        published_at=datetime(2026, 6, 15, tzinfo=UTC),
        original_text="Оригинальный текст про воду.",
        public_text="Скрытая публичная версия про воду.",
    )
    repo.create(
        source_id=source.id,
        topic_id=housing.id,
        category_id=animals_category.id,
        material_type=MaterialType.OFFICIAL_POST,
        status=MaterialStatus.DRAFT,
        published_at=datetime(2026, 6, 14, tzinfo=UTC),
        original_text="Оригинальный текст про собак.",
        public_text="Черновая версия про собак.",
    )
    session.add(AdminNote(material_id=active.id, body="Внутренняя заметка", author="admin"))
    session.commit()


def login(client: TestClient) -> str:
    response = client.post(
        "/admin/login",
        data={"username": "admin", "password": "secret"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    materials_page = client.get("/admin/materials")
    assert materials_page.status_code == 200
    return extract_csrf_token(materials_page.text)


def extract_csrf_token(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match is not None
    return match.group(1)


def test_admin_requires_login(admin_app_context: tuple[TestClient, sessionmaker[Session]]) -> None:
    client, _ = admin_app_context

    response = client.get("/admin/materials", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login"


def test_admin_login_rejects_wrong_password_and_accepts_valid_credentials(
    admin_app_context: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, _ = admin_app_context

    bad_response = client.post(
        "/admin/login",
        data={"username": "admin", "password": "wrong"},
    )
    assert bad_response.status_code == 401
    assert "Неверный логин или пароль" in bad_response.text

    csrf_token = login(client)
    assert csrf_token
    response = client.get("/admin/materials")
    assert response.status_code == 200
    assert "Карточки" in response.text
    assert "Публичная версия про отопление" in response.text


def test_admin_logout_closes_session(admin_app_context: tuple[TestClient, sessionmaker[Session]]) -> None:
    client, _ = admin_app_context
    csrf_token = login(client)

    logout_response = client.post(
        "/admin/logout",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )

    assert logout_response.status_code == 303
    response = client.get("/admin/materials", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login"


def test_admin_materials_filter_by_status_and_category(
    admin_app_context: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, session_factory = admin_app_context
    login(client)
    with session_factory() as session:
        water = TaxonomyRepository(session).get_topic_by_slug("housing")
        assert water is not None
        category = TaxonomyRepository(session).get_category(topic_id=water.id, slug="water")
        assert category is not None
        category_id = category.id

    response = client.get(f"/admin/materials?status=hidden&category_id={category_id}")

    assert response.status_code == 200
    assert "Скрытая публичная версия про воду" in response.text
    assert "Публичная версия про отопление" not in response.text


def test_admin_material_detail_shows_core_fields_and_notes(
    admin_app_context: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, session_factory = admin_app_context
    login(client)
    with session_factory() as session:
        material_id = session.query(Material).filter_by(status=MaterialStatus.ACTIVE).one().id

    response = client.get(f"/admin/materials/{material_id}")

    assert response.status_code == 200
    assert "Оригинальный официальный текст про отопление." in response.text
    assert "Публичная версия про отопление." in response.text
    assert "Официальный канал администрации" in response.text
    assert "Опубликовано" in response.text
    assert "Внутренняя заметка" in response.text


@pytest.mark.parametrize(
    "new_status",
    [
        MaterialStatus.HIDDEN,
        MaterialStatus.ARCHIVED,
        MaterialStatus.DUPLICATE,
        MaterialStatus.NEEDS_REVIEW,
    ],
)
def test_non_public_admin_statuses_remove_material_from_public_search(
    admin_app_context: tuple[TestClient, sessionmaker[Session]],
    new_status: MaterialStatus,
) -> None:
    client, session_factory = admin_app_context
    csrf_token = login(client)
    with session_factory() as session:
        material_id = session.query(Material).filter_by(status=MaterialStatus.ACTIVE).one().id

    response = client.post(
        f"/admin/materials/{material_id}/status",
        data={"csrf_token": csrf_token, "status": new_status.value},
        follow_redirects=False,
    )

    assert response.status_code == 303
    public_response = client.get("/search?q=отопление")
    assert "Публичная версия про отопление." not in public_response.text


def test_active_status_returns_public_topic_material_to_public_search(
    admin_app_context: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, session_factory = admin_app_context
    csrf_token = login(client)
    with session_factory() as session:
        material_id = session.query(Material).filter_by(status=MaterialStatus.HIDDEN).one().id

    response = client.post(
        f"/admin/materials/{material_id}/status",
        data={"csrf_token": csrf_token, "status": "active"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    public_response = client.get("/search?q=воду")
    assert "Скрытая публичная версия про воду." in public_response.text


def test_admin_post_without_csrf_does_not_change_status_or_add_note(
    admin_app_context: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, session_factory = admin_app_context
    login(client)
    with session_factory() as session:
        material_id = session.query(Material).filter_by(status=MaterialStatus.ACTIVE).one().id

    status_response = client.post(
        f"/admin/materials/{material_id}/status",
        data={"status": "hidden"},
    )
    note_response = client.post(
        f"/admin/materials/{material_id}/notes",
        data={"body": "Новая заметка без токена"},
    )

    assert status_response.status_code == 403
    assert note_response.status_code == 403
    with session_factory() as session:
        material = session.get(Material, material_id)
        assert material is not None
        assert material.status == MaterialStatus.ACTIVE
        assert session.query(AdminNote).filter_by(body="Новая заметка без токена").count() == 0


def test_admin_can_change_category_and_add_internal_note_not_visible_publicly(
    admin_app_context: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, session_factory = admin_app_context
    csrf_token = login(client)
    with session_factory() as session:
        material = session.query(Material).filter_by(status=MaterialStatus.ACTIVE).one()
        material_id = material.id
        water = TaxonomyRepository(session).get_category(topic_id=material.topic_id, slug="water")
        assert water is not None
        water_id = water.id

    category_response = client.post(
        f"/admin/materials/{material_id}/category",
        data={"csrf_token": csrf_token, "category_id": str(water_id)},
        follow_redirects=False,
    )
    note_response = client.post(
        f"/admin/materials/{material_id}/notes",
        data={"csrf_token": csrf_token, "body": "Новая внутренняя заметка"},
        follow_redirects=False,
    )

    assert category_response.status_code == 303
    assert note_response.status_code == 303
    with session_factory() as session:
        material = session.get(Material, material_id)
        assert material is not None
        assert material.category_id == water_id
        assert session.query(AdminNote).filter_by(body="Новая внутренняя заметка").one()

    public_response = client.get(f"/materials/{material_id}")
    assert public_response.status_code == 200
    assert "Новая внутренняя заметка" not in public_response.text
