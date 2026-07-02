from __future__ import annotations

import re
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings
from app.db.base import Base
from app.db.enums import (
    DictionaryCandidateSource,
    DictionaryCandidateStatus,
    DictionaryCandidateType,
    MaterialStatus,
    MaterialType,
    SourceKind,
)
from app.db.models import DictionaryCandidate, Material, ProblemQuery, QuestionVariant
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
    heating = taxonomy.get_category(topic_id=housing.id, slug="heating")
    animals = taxonomy.get_category(topic_id=housing.id, slug="animals")
    assert heating is not None
    assert animals is not None
    repo = MaterialRepository(session)
    repo.create(
        source_id=source.id,
        topic_id=housing.id,
        category_id=heating.id,
        material_type=MaterialType.OFFICIAL_ANSWER,
        status=MaterialStatus.ACTIVE,
        published_at=datetime(2026, 1, 1, tzinfo=UTC),
        original_text="Отопление восстановят после ремонта.",
        public_text="Отопление восстановят после ремонта тепловой сети.",
    )
    repo.create(
        source_id=source.id,
        topic_id=housing.id,
        category_id=animals.id,
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
    session.commit()


def test_russian_morphology_and_category_dictionaries() -> None:
    assert normalize_text("собака собаки собакой") == "собака собака собака"
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
