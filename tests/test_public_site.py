from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings
from app.db.base import Base
from app.db.enums import MaterialStatus, MaterialType, SourceKind
from app.db.models import AdminNote, Material, ProblemQuery
from app.db.repositories import MaterialRepository, SourceRepository, TaxonomyRepository
from app.db.seed import seed_initial_data
from app.db.session import create_database_engine, get_db_session
from app.main import create_app
from app.public.routes import SAFE_PROBLEM_QUERY_TEXT


@pytest.fixture()
def public_app_context() -> tuple[TestClient, sessionmaker[Session]]:
    engine = create_database_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    with session_factory() as seed_session:
        seed_initial_data(seed_session)
        seed_public_materials(seed_session)

    def override_db() -> Generator[Session, None, None]:
        with session_factory() as db_session:
            yield db_session

    app = create_app()
    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_settings] = lambda: Settings(AUTO_DB_BOOTSTRAP=False)
    return TestClient(app), session_factory


def seed_public_materials(session: Session) -> None:
    taxonomy = TaxonomyRepository(session)
    source = SourceRepository(session).create(
        code="public-official",
        name="Официальный канал администрации",
        kind=SourceKind.OFFICIAL_CHANNEL,
        url="https://example.test/channel",
    )
    private_source = SourceRepository(session).create(
        code="private-source",
        name="Неофициальный источник",
        kind=SourceKind.WEBSITE,
        is_official=False,
    )
    housing = taxonomy.get_topic_by_slug("housing")
    transport = taxonomy.get_topic_by_slug("transport")
    assert housing is not None
    assert transport is not None
    heating = taxonomy.get_category(topic_id=housing.id, slug="heating")
    water = taxonomy.get_category(topic_id=housing.id, slug="water")
    dogs = taxonomy.get_category(topic_id=housing.id, slug="stray_dogs")
    assert heating is not None
    assert water is not None
    assert dogs is not None

    repo = MaterialRepository(session)
    heating_material = repo.create(
        source_id=source.id,
        topic_id=housing.id,
        category_id=heating.id,
        material_type=MaterialType.OFFICIAL_ANSWER,
        status=MaterialStatus.ACTIVE,
        published_at=datetime(2026, 1, 15, tzinfo=UTC),
        source_url="https://example.test/heating",
        original_text="Оригинал: отопление восстановят после ремонта.",
        public_text="Отопление восстановят после ремонта тепловой сети.",
        metadata_json={"internal": "secret import metadata"},
    )
    repo.create(
        source_id=source.id,
        topic_id=housing.id,
        category_id=heating.id,
        material_type=MaterialType.OFFICIAL_POST,
        status=MaterialStatus.ACTIVE,
        published_at=datetime(2026, 1, 16, tzinfo=UTC),
        original_text="Похожий материал про отопление.",
        public_text="По вопросам отопления работает диспетчерская служба.",
    )
    repo.create(
        source_id=source.id,
        topic_id=housing.id,
        category_id=water.id,
        material_type=MaterialType.OFFICIAL_POST,
        status=MaterialStatus.ACTIVE,
        published_at=datetime(2026, 1, 17, tzinfo=UTC),
        original_text="Материал про горячую воду.",
        public_text="Горячая вода будет включена после завершения работ.",
    )
    repo.create(
        source_id=source.id,
        topic_id=housing.id,
        category_id=dogs.id,
        material_type=MaterialType.OFFICIAL_ANSWER,
        status=MaterialStatus.ACTIVE,
        published_at=datetime(2026, 1, 18, tzinfo=UTC),
        original_text="Материал про безнадзорных собак.",
        public_text="По безнадзорным собакам можно обратиться в службу отлова.",
    )
    repo.create(
        source_id=source.id,
        topic_id=housing.id,
        category_id=heating.id,
        material_type=MaterialType.OFFICIAL_POST,
        status=MaterialStatus.HIDDEN,
        published_at=datetime(2026, 1, 19, tzinfo=UTC),
        original_text="Скрытый материал про отопление.",
        public_text="Скрытый материал про отопление не должен показываться.",
    )
    repo.create(
        source_id=source.id,
        topic_id=transport.id,
        material_type=MaterialType.OFFICIAL_POST,
        status=MaterialStatus.ACTIVE,
        published_at=datetime(2026, 1, 20, tzinfo=UTC),
        original_text="Транспортный материал.",
        public_text="Автобусный маршрут не должен показываться в публичном MVP.",
    )
    repo.create(
        source_id=private_source.id,
        topic_id=housing.id,
        category_id=heating.id,
        material_type=MaterialType.OFFICIAL_POST,
        status=MaterialStatus.ACTIVE,
        published_at=datetime(2026, 1, 21, tzinfo=UTC),
        original_text="Неофициальный материал.",
        public_text="Неофициальный материал про отопление не должен показываться.",
        is_official=False,
    )
    session.add(
        AdminNote(
            material_id=heating_material.id,
            body="Внутренняя заметка администратора не для публикации.",
            author="admin",
        )
    )
    session.commit()


def test_homepage_contains_search_categories_popular_disclaimer_and_legal_links(
    public_app_context: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, _ = public_app_context

    response = client.get("/")

    assert response.status_code == 200
    assert "Задайте вопрос по ЖКХ или теме животных" in response.text
    assert "ЖКХ" in response.text
    assert "Безнадзорные собаки" in response.text
    assert response.text.count('class="topic-block"') == 1
    assert response.text.index("Правила содержания животных") < response.text.index("Другое")
    assert "Почему нет отопления?" in response.text
    assert "не является официальным ресурсом администрации" in response.text
    assert "/legal/terms" in response.text
    assert "/legal/privacy" in response.text
    assert "/legal/moderation" in response.text


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("/legal/terms", "Пользовательское соглашение"),
        ("/legal/privacy", "Политика конфиденциальности"),
        ("/legal/personal-data-consent", "Согласие на обработку персональных данных"),
        ("/legal/disclaimer", "информационно-поискового сервиса справочного характера"),
        ("/legal/cookies", "Согласие на cookies"),
        ("/legal/moderation", "Политика модерации"),
    ],
)
def test_legal_pages_open_and_contain_required_placeholders(
    public_app_context: tuple[TestClient, sessionmaker[Session]],
    url: str,
    expected: str,
) -> None:
    client, _ = public_app_context

    response = client.get(url)

    assert response.status_code == 200
    assert expected in response.text
    assert "[НАЗВАНИЕ СЕРВИСА]" in response.text
    assert "[ВЛАДЕЛЕЦ / ОПЕРАТОР СЕРВИСА]" in response.text
    assert "[ДОМЕН СЕРВИСА]" in response.text
    assert "[EMAIL ДЛЯ ОБРАЩЕНИЙ]" in response.text
    assert "[КОНТАКТЫ ОПЕРАТОРА]" in response.text


def test_temporary_search_finds_only_active_official_public_topic_materials(
    public_app_context: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, _ = public_app_context

    response = client.get("/search?q=отопление")

    assert response.status_code == 200
    assert "Отопление восстановят после ремонта тепловой сети." in response.text
    assert "По вопросам отопления работает диспетчерская служба." in response.text
    assert "Скрытый материал про отопление" not in response.text
    assert "Автобусный маршрут" not in response.text
    assert "Неофициальный материал" not in response.text


def test_search_filters_by_public_category(
    public_app_context: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, session_factory = public_app_context
    with session_factory() as session:
        heating = TaxonomyRepository(session).get_topic_by_slug("housing")
        assert heating is not None
        category = TaxonomyRepository(session).get_category(topic_id=heating.id, slug="heating")
        assert category is not None
        category_id = category.id

    response = client.get(f"/search?category_id={category_id}")

    assert response.status_code == 200
    assert "Отопление восстановят" in response.text
    assert "Горячая вода будет включена" not in response.text


def test_empty_search_state_is_calm_and_lists_categories(
    public_app_context: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, _ = public_app_context

    response = client.get("/search?q=несуществующийзапрос")

    assert response.status_code == 200
    assert "Подходящих материалов пока нет" in response.text
    assert "Попробуйте переформулировать вопрос" in response.text
    assert "Безнадзорные собаки" in response.text


def test_material_card_shows_public_fields_similar_and_source_url_only_when_present(
    public_app_context: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, session_factory = public_app_context
    with session_factory() as session:
        material = session.query(Material).filter(Material.public_text.like("%восстановят%")).one()
        material_id = material.id

    response = client.get(f"/materials/{material_id}")

    assert response.status_code == 200
    assert "15.01.2026" in response.text
    assert "Официальный канал администрации" in response.text
    assert "Официальный ответ" in response.text
    assert "ЖКХ" in response.text
    assert "Отопление" in response.text
    assert "Отопление восстановят после ремонта тепловой сети." in response.text
    assert "https://example.test/heating" in response.text
    assert "По вопросам отопления работает диспетчерская служба." in response.text
    assert "Оригинал:" not in response.text
    assert "Внутренняя заметка администратора" not in response.text
    assert "secret import metadata" not in response.text


def test_material_without_source_url_does_not_render_source_link(
    public_app_context: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, session_factory = public_app_context
    with session_factory() as session:
        material = MaterialRepository(session).search_public(query="диспетчерская")[0]
        material_id = material.id

    response = client.get(f"/materials/{material_id}")

    assert response.status_code == 200
    assert "Открыть источник" not in response.text


def test_not_helpful_creates_problem_query_without_raw_user_text(
    public_app_context: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, session_factory = public_app_context
    with session_factory() as session:
        material = MaterialRepository(session).search_public(query="восстановят")[0]
        material_id = material.id
        category_id = material.category_id

    response = client.post(
        f"/materials/{material_id}/not-helpful",
        data={"similar_material_ids": "2,3"},
    )

    assert response.status_code == 200
    assert "Текст вашего запроса не сохранен" in response.text
    with session_factory() as session:
        problem_query = session.query(ProblemQuery).one()
        assert problem_query.anonymized_text == SAFE_PROBLEM_QUERY_TEXT
        assert problem_query.normalized_text is None
        assert problem_query.shown_material_id == material_id
        assert problem_query.category_id == category_id
        assert problem_query.similar_material_ids == [2, 3]
        assert problem_query.channel.value == "website"
        assert problem_query.user_action.value == "rephrase"
        assert problem_query.match_level == "not_helpful"
