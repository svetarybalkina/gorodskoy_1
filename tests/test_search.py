from __future__ import annotations

import re
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings
from app.db.base import Base
from app.db.enums import (
    DictionaryCandidateSource,
    DictionaryCandidateStatus,
    DictionaryCandidateType,
    LinkReason,
    MaterialStatus,
    MaterialType,
    SourceKind,
)
from app.db.models import DictionaryCandidate, Material, MaterialLink, ProblemQuery, QuestionVariant, ResidentQuestion
from app.db.repositories import (
    DictionaryCandidateRepository,
    MaterialRepository,
    SourceRepository,
    TaxonomyRepository,
)
from app.db.seed import seed_initial_data
from app.db.session import create_database_engine, get_db_session
from app.main import create_app
from app.search.normalization import guess_category_slug, normalize_text
from app.search.service import SearchService


def seeded_session() -> Session:
    engine = create_database_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = session_factory()
    seed_initial_data(session)
    seed_search_materials(session)
    return session


def seed_search_materials(session: Session) -> None:
    taxonomy = TaxonomyRepository(session)
    source = SourceRepository(session).create(
        code="search-source",
        name="Официальный канал",
        kind=SourceKind.OFFICIAL_CHANNEL,
    )
    housing = taxonomy.get_topic_by_slug("housing")
    transport = taxonomy.get_topic_by_slug("transport")
    assert housing is not None
    assert transport is not None

    categories = {
        slug: taxonomy.get_category(topic_id=housing.id, slug=slug)
        for slug in (
            "heating",
            "water",
            "entrance",
            "yard",
            "waste",
            "management_company",
            "bills",
            "animals",
        )
    }
    assert all(category is not None for category in categories.values())

    repo = MaterialRepository(session)
    repo.create(
        source_id=source.id,
        topic_id=housing.id,
        category_id=categories["heating"].id,
        material_type=MaterialType.OFFICIAL_ANSWER,
        status=MaterialStatus.ACTIVE,
        published_at=datetime(2026, 1, 1, tzinfo=UTC),
        original_text="Отопление восстановят после ремонта.",
        public_text="Отопление восстановят после ремонта тепловой сети.",
    )
    repo.create(
        source_id=source.id,
        topic_id=housing.id,
        category_id=categories["animals"].id,
        material_type=MaterialType.OFFICIAL_POST,
        status=MaterialStatus.ACTIVE,
        published_at=datetime(2026, 1, 2, tzinfo=UTC),
        original_text="Ответ про агрессивных собак.",
        public_text="По агрессивным собакам можно обратиться в службу отлова.",
    )
    repo.create(
        source_id=source.id,
        topic_id=transport.id,
        material_type=MaterialType.OFFICIAL_POST,
        status=MaterialStatus.ACTIVE,
        published_at=datetime(2026, 1, 3, tzinfo=UTC),
        original_text="Ответ про автобус.",
        public_text="Автобусный маршрут не участвует в публичном поиске MVP.",
    )
    entrance_cleaning = repo.create(
        source_id=source.id,
        topic_id=housing.id,
        category_id=categories["entrance"].id,
        material_type=MaterialType.OFFICIAL_ANSWER,
        status=MaterialStatus.ACTIVE,
        published_at=datetime(2026, 1, 4, tzinfo=UTC),
        original_text="График уборки подъездов можно уточнить в управляющей компании.",
        public_text="График уборки подъездов в доме можно уточнить в управляющей компании. При нарушении уборки подъездов нужно оставить заявку.",
    )
    entrance_domofon = repo.create(
        source_id=source.id,
        topic_id=housing.id,
        category_id=categories["entrance"].id,
        material_type=MaterialType.OFFICIAL_ANSWER,
        status=MaterialStatus.ACTIVE,
        published_at=datetime(2026, 1, 5, tzinfo=UTC),
        original_text="По неисправному домофону обращайтесь в управляющую компанию.",
        public_text="По неисправному домофону обращайтесь в управляющую компанию дома или в диспетчерскую службу.",
    )
    repo.create(
        source_id=source.id,
        topic_id=housing.id,
        category_id=categories["yard"].id,
        material_type=MaterialType.OFFICIAL_ANSWER,
        status=MaterialStatus.ACTIVE,
        published_at=datetime(2026, 1, 6, tzinfo=UTC),
        original_text="Яму во дворе включат в план ямочного ремонта.",
        public_text="Яму во дворе включат в план ямочного ремонта дворовой территории.",
    )
    waste_material = repo.create(
        source_id=source.id,
        topic_id=housing.id,
        category_id=categories["waste"].id,
        material_type=MaterialType.OFFICIAL_ANSWER,
        status=MaterialStatus.ACTIVE,
        published_at=datetime(2026, 1, 7, tzinfo=UTC),
        original_text="Региональному оператору направлено письмо о вывозе мусора.",
        public_text="Региональному оператору направлено письмо о необходимости своевременно вывозить мусор с контейнерной площадки.",
    )
    management_material = repo.create(
        source_id=source.id,
        topic_id=housing.id,
        category_id=categories["management_company"].id,
        material_type=MaterialType.OFFICIAL_ANSWER,
        status=MaterialStatus.ACTIVE,
        published_at=datetime(2026, 1, 8, tzinfo=UTC),
        original_text="Собственники вправе направить обращение в Госжилинспекцию.",
        public_text="Собственники жилья вправе направить обращение в Госжилинспекцию области, указав на нарушения управляющей компании.",
    )
    repo.create(
        source_id=source.id,
        topic_id=housing.id,
        category_id=categories["bills"].id,
        material_type=MaterialType.OFFICIAL_ANSWER,
        status=MaterialStatus.ACTIVE,
        published_at=datetime(2026, 1, 9, tzinfo=UTC),
        original_text="По некорректным начислениям нужно запросить перерасчет.",
        public_text="По некорректным начислениям в квитанции нужно обратиться за перерасчетом и приложить копию платежного документа.",
    )
    repo.create(
        source_id=source.id,
        topic_id=housing.id,
        category_id=categories["water"].id,
        material_type=MaterialType.OFFICIAL_ANSWER,
        status=MaterialStatus.ACTIVE,
        published_at=datetime(2026, 1, 10, tzinfo=UTC),
        original_text="Ваш вопрос направлен специалистам.",
        public_text="Ваш вопрос направлен специалистам для детальной проработки проблемы, вернемся с ответом позже.",
    )

    _link_question(session, entrance_cleaning, "Грязно в подъезде")
    _link_question(session, entrance_domofon, "Сломан домофон")
    _link_question(session, waste_material, "Не вывозят мусор")
    _link_question(session, management_material, "Куда жаловаться на управляющую компанию?")
    session.commit()


def _link_question(session: Session, material: Material, question_text: str) -> None:
    question = ResidentQuestion(
        category_id=material.category_id,
        anonymized_text=question_text,
        normalized_text=normalize_text(question_text),
        source_channel="official-chat",
    )
    session.add(question)
    session.flush()
    session.add(
        MaterialLink(
            question_id=question.id,
            material_id=material.id,
            reason=LinkReason.IMPORTED_PAIR,
            confidence=100,
        )
    )


def test_russian_morphology_and_category_dictionaries() -> None:
    assert normalize_text("собака собаки собакой") == "собака собака собака"
    assert normalize_text("Что делать, если нет горячей воды?") == "горячий вода"
    assert guess_category_slug("во дворе агрессивные собаки") == "animals"
    assert guess_category_slug("куда жаловаться на УК по квитанции") in {"management_company", "bills"}


def test_search_finds_public_materials_with_word_forms_and_animals_category() -> None:
    session = seeded_session()
    taxonomy = TaxonomyRepository(session)
    housing = taxonomy.get_topic_by_slug("housing")
    assert housing is not None
    animals = taxonomy.get_category(topic_id=housing.id, slug="animals")
    assert animals is not None
    service = SearchService(session)
    service.rebuild_index()

    heating = service.search_public("нет отопления", record_problem_query=False)
    dogs = service.search_public("собакой во дворе", category_id=animals.id, record_problem_query=False)
    transport = service.search_public("автобусный маршрут", record_problem_query=False)

    assert heating.match_level == "high"
    assert "Отопление восстановят" in heating.materials[0].public_text
    assert dogs.match_level in {"high", "medium"}
    assert "агрессивным собакам" in dogs.materials[0].public_text
    assert transport.match_level == "none"
    assert transport.materials == []


def test_hot_water_query_does_not_rank_road_salt_water_material_first() -> None:
    session = seeded_session()
    taxonomy = TaxonomyRepository(session)
    housing = taxonomy.get_topic_by_slug("housing")
    assert housing is not None
    water = taxonomy.get_category(topic_id=housing.id, slug="water")
    assert water is not None
    source = session.query(Material).first().source
    repo = MaterialRepository(session)
    salt_material = repo.create(
        source_id=source.id,
        topic_id=housing.id,
        category_id=water.id,
        material_type=MaterialType.OFFICIAL_ANSWER,
        status=MaterialStatus.ACTIVE,
        published_at=datetime(2026, 1, 11, tzinfo=UTC),
        original_text="Зимой дороги отсыпают пескосоляной смесью. Песок делает дорогу шероховатой, соль понижает температуру замерзания воды.",
        public_text="Зимой дороги отсыпают пескосоляной смесью. Песок делает дорогу шероховатой, соль понижает температуру замерзания воды.",
    )
    hot_water_material = repo.create(
        source_id=source.id,
        topic_id=housing.id,
        category_id=water.id,
        material_type=MaterialType.OFFICIAL_ANSWER,
        status=MaterialStatus.ACTIVE,
        published_at=datetime(2026, 1, 12, tzinfo=UTC),
        original_text="Подача горячей воды восстановлена после ремонта сетей.",
        public_text="Подача горячей воды восстановлена после ремонта сетей.",
    )
    service = SearchService(session)
    service.rebuild_index()

    response = service.search_public("Что делать, если нет горячей воды?", record_problem_query=False)

    assert response.materials[0].id == hot_water_material.id
    assert salt_material.id in [material.id for material in response.materials]
    assert response.materials.index(hot_water_material) < response.materials.index(salt_material)


def test_search_item_snippet_uses_matching_fragment_from_public_text() -> None:
    session = seeded_session()
    taxonomy = TaxonomyRepository(session)
    housing = taxonomy.get_topic_by_slug("housing")
    assert housing is not None
    waste = taxonomy.get_category(topic_id=housing.id, slug="waste")
    assert waste is not None
    source = session.query(Material).first().source
    leading_text = "Начальный нерелевантный блок без нужных слов. " * 10
    target = MaterialRepository(session).create(
        source_id=source.id,
        topic_id=housing.id,
        category_id=waste.id,
        material_type=MaterialType.OFFICIAL_ANSWER,
        status=MaterialStatus.ACTIVE,
        published_at=datetime(2026, 1, 13, tzinfo=UTC),
        original_text=leading_text + "В середине ответа указано, что мусорные контейнеры вывезет региональный оператор.",
        public_text=leading_text + "В середине ответа указано, что мусорные контейнеры вывезет региональный оператор.",
    )
    service = SearchService(session)
    service.rebuild_index()

    response = service.search_public("мусорные контейнеры", record_problem_query=False)
    item = next(search_item for search_item in response.items if search_item.material.id == target.id)

    assert "мусорные контейнеры" in item.snippet
    assert not item.snippet.startswith("Начальный нерелевантный блок")


def test_search_item_snippet_falls_back_when_match_is_not_in_public_text() -> None:
    session = seeded_session()
    taxonomy = TaxonomyRepository(session)
    housing = taxonomy.get_topic_by_slug("housing")
    assert housing is not None
    heating = taxonomy.get_category(topic_id=housing.id, slug="heating")
    assert heating is not None
    source = session.query(Material).first().source
    public_text = "Первое предложение без поискового маркера. Второе предложение тоже справочное."
    target = MaterialRepository(session).create(
        source_id=source.id,
        topic_id=housing.id,
        category_id=heating.id,
        material_type=MaterialType.OFFICIAL_ANSWER,
        status=MaterialStatus.ACTIVE,
        published_at=datetime(2026, 1, 14, tzinfo=UTC),
        original_text=public_text,
        public_text=public_text,
    )
    service = SearchService(session)
    service.rebuild_index()

    response = service.search_public("котельная", record_problem_query=False)
    item = next(search_item for search_item in response.items if search_item.material.id == target.id)

    assert item.snippet == public_text


def test_category_only_search_uses_category_markers_for_snippet() -> None:
    session = seeded_session()
    taxonomy = TaxonomyRepository(session)
    housing = taxonomy.get_topic_by_slug("housing")
    assert housing is not None
    animals = taxonomy.get_category(topic_id=housing.id, slug="animals")
    assert animals is not None
    source = session.query(Material).first().source
    leading_text = "В начале ответа приведены общие сведения без тематических слов. " * 8
    target = MaterialRepository(session).create(
        source_id=source.id,
        topic_id=housing.id,
        category_id=animals.id,
        material_type=MaterialType.OFFICIAL_ANSWER,
        status=MaterialStatus.ACTIVE,
        published_at=datetime(2026, 1, 15, tzinfo=UTC),
        original_text=leading_text + "Далее указано: для отлова животных необходимо обратиться в профильную службу.",
        public_text=leading_text + "Далее указано: для отлова животных необходимо обратиться в профильную службу.",
    )
    service = SearchService(session)
    service.rebuild_index()

    response = service.search_public("", category_id=animals.id, record_problem_query=False)

    assert response.items[0].material.id == target.id
    assert "отлова животных" in response.items[0].snippet
    assert not response.items[0].snippet.startswith("В начале ответа")


def test_category_filter_requires_public_text_category_evidence() -> None:
    session = seeded_session()
    taxonomy = TaxonomyRepository(session)
    housing = taxonomy.get_topic_by_slug("housing")
    assert housing is not None
    animals = taxonomy.get_category(topic_id=housing.id, slug="animals")
    assert animals is not None
    source = session.query(Material).first().source
    wrong_category_material = MaterialRepository(session).create(
        source_id=source.id,
        topic_id=housing.id,
        category_id=animals.id,
        material_type=MaterialType.OFFICIAL_ANSWER,
        status=MaterialStatus.ACTIVE,
        published_at=datetime(2026, 1, 16, tzinfo=UTC),
        original_text="Ремонт автомобильной дороги включен в план работ.",
        public_text="Ремонт автомобильной дороги включен в план работ.",
    )
    service = SearchService(session)
    service.rebuild_index()

    response = service.search_public("", category_id=animals.id, record_problem_query=False)

    assert wrong_category_material.id not in [item.material.id for item in response.items]


def test_low_confidence_search_saves_anonymized_problem_query_and_candidate() -> None:
    session = seeded_session()
    service = SearchService(session)
    service.rebuild_index()

    response = service.search_public("вопрос из кв 15 телефон +7 999 111-22-33", record_problem_query=True)

    assert response.match_level in {"low", "none"}
    assert response.problem_query_saved is True
    problem = session.query(ProblemQuery).one()
    assert "+7 999 111-22-33" not in problem.anonymized_text
    assert "кв 15" not in problem.anonymized_text.lower()
    assert problem.normalized_text is not None
    candidate = session.query(DictionaryCandidate).one()
    assert candidate.status == DictionaryCandidateStatus.PENDING
    assert candidate.source == DictionaryCandidateSource.SEARCH


@pytest.mark.parametrize(
    ("query", "expected_fragment", "expected_category"),
    [
        ("Грязно в подъезде", "График уборки подъездов", "entrance"),
        ("Сломан домофон", "неисправному домофону", "entrance"),
        ("Яма во дворе", "Яму во дворе", "yard"),
        ("Не вывозят мусор", "вывозить мусор", "waste"),
        ("Куда жаловаться на управляющую компанию?", "Госжилинспекцию", "management_company"),
        ("Неправильные начисления в квитанции", "некорректным начислениям", "bills"),
        ("Во дворе агрессивная собака", "агрессивным собакам", "animals"),
    ],
)
def test_search_ranks_relevant_household_materials_across_categories(
    query: str,
    expected_fragment: str,
    expected_category: str,
) -> None:
    session = seeded_session()
    service = SearchService(session)
    service.rebuild_index()

    response = service.search_public(query, record_problem_query=False)

    assert response.materials
    assert response.materials[0].category is not None
    assert response.materials[0].category.slug == expected_category
    assert expected_fragment in response.materials[0].public_text
    assert response.match_level in {"high", "medium"}


def test_dirty_entrance_query_does_not_rank_heating_above_cleaning_material() -> None:
    session = seeded_session()
    service = SearchService(session)
    service.rebuild_index()

    response = service.search_public("Грязно в подъезде", record_problem_query=False)

    assert response.materials[0].category is not None
    assert response.materials[0].category.slug == "entrance"
    assert "уборки подъездов" in response.materials[0].public_text
    assert "температура" not in response.materials[0].public_text


def test_flooded_basement_query_is_not_marked_as_high_when_only_generic_answer_exists() -> None:
    session = seeded_session()
    service = SearchService(session)
    service.rebuild_index()

    response = service.search_public("Затопило подвал", record_problem_query=False)

    assert response.match_level in {"low", "none"}


def test_search_uses_linked_resident_questions_as_strong_signal() -> None:
    session = seeded_session()
    service = SearchService(session)
    service.rebuild_index()

    response = service.search_public("Куда жаловаться на управляющую компанию?", record_problem_query=False)

    assert response.materials[0].category is not None
    assert response.materials[0].category.slug == "management_company"
    assert response.items[0].signals.question_overlap > 0.0


def test_unconfirmed_question_variant_does_not_affect_search_until_approval() -> None:
    session = seeded_session()
    heating_material = session.query(QuestionVariant).count()
    assert heating_material == 0
    target = session.query(Material).filter(Material.public_text.like("%Отопление%")).one()
    service = SearchService(session)
    service.rebuild_index()
    session.add(
        DictionaryCandidate(
            text="промерзший радиатор",
            normalized_text=normalize_text("промерзший радиатор"),
            candidate_type=DictionaryCandidateType.QUESTION_VARIANT,
            source=DictionaryCandidateSource.SEARCH,
            status=DictionaryCandidateStatus.PENDING,
            material_id=target.id,
            category_id=target.category_id,
        )
    )
    session.flush()

    before = service.search_public("промерзший радиатор", record_problem_query=False)
    assert before.materials == []

    candidate = session.query(DictionaryCandidate).one()
    service.approve_candidate(candidate.id)
    after = service.search_public("промерзший радиатор", record_problem_query=False)

    assert after.materials[0].id == target.id
    assert session.query(QuestionVariant).filter_by(material_id=target.id, is_confirmed=True).one()


def test_admin_search_quality_page_and_candidate_actions() -> None:
    engine = create_database_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    with session_factory() as session:
        seed_initial_data(session)
        seed_search_materials(session)
        material = session.query(Material).filter(Material.public_text.like("%Отопление%")).one()
        DictionaryCandidateRepository(session).create_or_increment(
            text="промерзший радиатор",
            normalized_text=normalize_text("промерзший радиатор"),
            candidate_type=DictionaryCandidateType.QUESTION_VARIANT,
            source=DictionaryCandidateSource.SEARCH,
            category_id=material.category_id,
            material_id=material.id,
        )
        session.commit()

    def override_db():
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
    client = TestClient(app)
    login = client.post("/admin/login", data={"username": "admin", "password": "secret"}, follow_redirects=False)
    assert login.status_code == 303
    page = client.get("/admin/search-quality")
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    assert page.status_code == 200
    assert csrf is not None
    assert "промерзший радиатор" in page.text
    assert 'class="table-actions"' in page.text

    with session_factory() as session:
        candidate_id = session.query(DictionaryCandidate).one().id
    response = client.post(
        f"/admin/search-quality/candidates/{candidate_id}/approve",
        data={"csrf_token": csrf.group(1)},
        follow_redirects=False,
    )

    assert response.status_code == 303
    with session_factory() as session:
        candidate = session.get(DictionaryCandidate, candidate_id)
        assert candidate is not None
        assert candidate.status == DictionaryCandidateStatus.APPROVED
