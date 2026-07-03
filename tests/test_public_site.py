from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings
from app.db.base import Base
from app.db.enums import MaterialStatus, MaterialType, SourceKind
from app.db.models import AdminNote, DictionaryCandidate, Material, ProblemQuery
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
    animals_category = taxonomy.get_category(topic_id=housing.id, slug="animals")
    assert heating is not None
    assert water is not None
    assert animals_category is not None

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
        public_text="При отсутствии горячей воды обратитесь в аварийную службу управляющей компании.",
    )
    repo.create(
        source_id=source.id,
        topic_id=housing.id,
        category_id=animals_category.id,
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
    assert "Задайте вопрос по ЖКХ" in response.text
    assert "ЖКХ" in response.text
    assert "Животные" in response.text
    assert "Безнадзорные собаки" not in response.text
    assert "Отлов безнадзорных животных" not in response.text
    assert "Агрессивные животные" not in response.text
    assert "Приюты и передержка" not in response.text
    assert "Правила содержания животных" not in response.text
    assert response.text.count('class="topic-block"') == 1
    assert response.text.index("Животные") < response.text.index("Другое")
    assert "Почему нет отопления?" in response.text
    assert "не является официальным ресурсом администрации" in response.text
    assert "Пожалуйста, не указывайте ФИО, телефон, номер квартиры" in response.text
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
    assert "До внедрения" not in response.text


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
    assert "аварийную службу управляющей компании" not in response.text


def test_search_page_card_uses_matching_public_text_fragment(
    public_app_context: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, session_factory = public_app_context
    leading_text = "Стартовый нерелевантный фрагмент без нужных слов. " * 10
    with session_factory() as session:
        taxonomy = TaxonomyRepository(session)
        housing = taxonomy.get_topic_by_slug("housing")
        assert housing is not None
        waste = taxonomy.get_category(topic_id=housing.id, slug="waste")
        assert waste is not None
        source = session.query(Material).first().source
        MaterialRepository(session).create(
            source_id=source.id,
            topic_id=housing.id,
            category_id=waste.id,
            material_type=MaterialType.OFFICIAL_ANSWER,
            status=MaterialStatus.ACTIVE,
            published_at=datetime(2026, 2, 2, tzinfo=UTC),
            original_text=leading_text + "В середине ответа сказано: мусорные контейнеры вывезет региональный оператор.",
            public_text=leading_text + "В середине ответа сказано: мусорные контейнеры вывезет региональный оператор.",
        )
        session.commit()

    response = client.get("/search?q=мусорные контейнеры")

    assert response.status_code == 200
    assert 'class="search-snippet"' in response.text
    assert "мусорные контейнеры вывезет региональный оператор" in response.text
    assert "Стартовый нерелевантный фрагмент без нужных слов. Стартовый нерелевантный фрагмент" not in response.text


def test_search_page_shows_extracted_recommendations_above_materials(
    public_app_context: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, _ = public_app_context

    response = client.get("/search?q=Что делать, если нет горячей воды?")

    assert response.status_code == 200
    assert "Что указано в найденных материалах" in response.text
    assert "Это вспомогательное извлечение из найденных официальных материалов" in response.text
    assert "Сервис не формирует официальный ответ администрации" in response.text
    assert "При отсутствии горячей воды обратитесь в аварийную службу управляющей компании." in response.text
    assert response.text.index("Что указано в найденных материалах") < response.text.index("Официальная публикация")


def test_animals_filter_collapses_legacy_detailed_animal_categories(
    public_app_context: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, session_factory = public_app_context
    with session_factory() as session:
        taxonomy = TaxonomyRepository(session)
        housing = taxonomy.get_topic_by_slug("housing")
        assert housing is not None
        animals = taxonomy.get_category(topic_id=housing.id, slug="animals")
        assert animals is not None
        legacy_category = taxonomy.create_category(
            topic_id=housing.id,
            slug="animal_capture",
            name="Отлов безнадзорных животных",
            is_public=True,
            is_confirmed=True,
            sort_order=75,
        )
        source = SourceRepository(session).create(
            code="legacy-animal-source",
            name="Официальный канал по животным",
            kind=SourceKind.OFFICIAL_CHANNEL,
        )
        MaterialRepository(session).create(
            source_id=source.id,
            topic_id=housing.id,
            category_id=legacy_category.id,
            material_type=MaterialType.OFFICIAL_POST,
            status=MaterialStatus.ACTIVE,
            published_at=datetime(2026, 2, 1, tzinfo=UTC),
            original_text="Официальный текст про отлов животных.",
            public_text="Заявки на отлов безнадзорных животных принимаются диспетчерской службой.",
        )
        session.commit()
        animals_category_id = animals.id

    filters_response = client.get("/search?q=животных")
    animals_response = client.get(f"/search?category_id={animals_category_id}&q=отлов")

    assert filters_response.status_code == 200
    assert "Животные" in filters_response.text
    assert "Отлов безнадзорных животных" not in filters_response.text
    assert animals_response.status_code == 200
    assert "Заявки на отлов безнадзорных животных принимаются диспетчерской службой." in animals_response.text


def test_empty_search_state_is_calm_and_lists_categories(
    public_app_context: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, _ = public_app_context

    response = client.get("/search?q=несуществующийзапрос")

    assert response.status_code == 200
    assert "Подходящих материалов пока нет" in response.text
    assert "Попробуйте переформулировать вопрос" in response.text
    assert "Животные" in response.text


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


def test_material_card_can_show_extracted_recommendations_before_official_text(
    public_app_context: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, session_factory = public_app_context
    search_response = client.get("/search?q=горячей воды")
    assert search_response.status_code == 200
    with session_factory() as session:
        material = session.query(Material).filter(Material.public_text.like("%аварийную службу%")).one()
        material_id = material.id

    response = client.get(f"/materials/{material_id}")

    assert response.status_code == 200
    assert "Что указано в материале" in response.text
    assert "Это вспомогательное извлечение из официального материала" in response.text
    assert response.text.index("Что указано в материале") < response.text.index("Официальный текст")
    assert "Материал про горячую воду" not in response.text


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
    assert "до внедрения" not in response.text.lower()
    with session_factory() as session:
        problem_query = session.query(ProblemQuery).one()
        assert problem_query.anonymized_text == SAFE_PROBLEM_QUERY_TEXT
        assert problem_query.normalized_text is not None
        assert problem_query.shown_material_id == material_id
        assert problem_query.category_id == category_id
        assert problem_query.similar_material_ids == [2, 3]
        assert problem_query.channel.value == "website"
        assert problem_query.user_action.value == "rephrase"
        assert problem_query.match_level == "not_helpful"
        assert session.query(DictionaryCandidate).count() == 1
