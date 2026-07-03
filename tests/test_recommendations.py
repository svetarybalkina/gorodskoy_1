from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.db.enums import MaterialStatus, MaterialType, RecommendationType, SourceKind
from app.db.models import MaterialRecommendation
from app.db.repositories import MaterialRepository, SourceRepository, TaxonomyRepository
from app.db.seed import seed_initial_data
from app.db.session import create_database_engine
from app.search import SearchService
from app.services.recommendations import RecommendationExtractionService


def seeded_session() -> Session:
    engine = create_database_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = session_factory()
    seed_initial_data(session)
    return session


def test_recommendation_extraction_finds_required_practical_actions() -> None:
    session = seeded_session()
    service = RecommendationExtractionService(session)

    hot_water = service.extract_from_text(
        "При отсутствии горячей воды необходимо обратиться в аварийную службу управляющей компании."
    )
    waste = service.extract_from_text(
        "По вопросам вывоза мусора заявку принимает региональный оператор."
    )
    management = service.extract_from_text(
        "Жалобу на управляющую компанию можно направить в жилищную инспекцию."
    )
    network = service.extract_from_text(
        "При аварии на сетях сообщите в ЕДДС или аварийную службу."
    )

    assert hot_water[0].recommendation_type == RecommendationType.CONTACT
    assert hot_water[0].action_kind == "emergency_contact"
    assert "аварийную службу" in hot_water[0].text
    assert waste[0].action_kind == "operator_contact"
    assert "региональный оператор" in waste[0].text
    assert management[0].action_kind == "oversight"
    assert "жилищную инспекцию" in management[0].text
    assert network[0].action_kind == "emergency_contact"
    assert "ЕДДС" in network[0].text


def test_recommendation_extraction_skips_low_confidence_general_sentences() -> None:
    session = seeded_session()
    service = RecommendationExtractionService(session)

    result = service.extract_from_text("Горячая вода будет включена после завершения работ.")

    assert result == []


def test_search_prioritizes_material_with_relevant_action_and_returns_recommendations() -> None:
    session = seeded_session()
    taxonomy = TaxonomyRepository(session)
    source = SourceRepository(session).create(
        code="recommendation-source",
        name="Официальный канал",
        kind=SourceKind.OFFICIAL_CHANNEL,
    )
    housing = taxonomy.get_topic_by_slug("housing")
    assert housing is not None
    water = taxonomy.get_category(topic_id=housing.id, slug="water")
    assert water is not None
    repo = MaterialRepository(session)
    repo.create(
        source_id=source.id,
        topic_id=housing.id,
        category_id=water.id,
        material_type=MaterialType.OFFICIAL_POST,
        status=MaterialStatus.ACTIVE,
        published_at=datetime(2026, 1, 1, tzinfo=UTC),
        original_text="Горячая вода будет включена после завершения работ.",
        public_text="Горячая вода будет включена после завершения работ.",
    )
    action_material = repo.create(
        source_id=source.id,
        topic_id=housing.id,
        category_id=water.id,
        material_type=MaterialType.OFFICIAL_ANSWER,
        status=MaterialStatus.ACTIVE,
        published_at=datetime(2026, 1, 2, tzinfo=UTC),
        original_text="При отсутствии горячей воды необходимо обратиться в аварийную службу управляющей компании.",
        public_text="При отсутствии горячей воды необходимо обратиться в аварийную службу управляющей компании.",
    )
    service = SearchService(session)
    service.rebuild_index()

    response = service.search_public("Что делать, если нет горячей воды?", record_problem_query=False)

    assert response.materials[0].id == action_material.id
    assert response.recommendations
    assert "аварийную службу" in response.recommendations[0].recommendation.text


def test_recommendations_do_not_surface_non_public_or_unofficial_materials() -> None:
    session = seeded_session()
    taxonomy = TaxonomyRepository(session)
    official_source = SourceRepository(session).create(
        code="official-recommendations",
        name="Официальный канал",
        kind=SourceKind.OFFICIAL_CHANNEL,
    )
    private_source = SourceRepository(session).create(
        code="private-recommendations",
        name="Неофициальный сайт",
        kind=SourceKind.WEBSITE,
        is_official=False,
    )
    housing = taxonomy.get_topic_by_slug("housing")
    transport = taxonomy.get_topic_by_slug("transport")
    assert housing is not None
    assert transport is not None
    waste = taxonomy.get_category(topic_id=housing.id, slug="waste")
    assert waste is not None
    repo = MaterialRepository(session)
    repo.create(
        source_id=official_source.id,
        topic_id=housing.id,
        category_id=waste.id,
        material_type=MaterialType.OFFICIAL_ANSWER,
        status=MaterialStatus.HIDDEN,
        published_at=datetime(2026, 1, 1, tzinfo=UTC),
        original_text="По вопросам мусора обратитесь к региональному оператору.",
        public_text="По вопросам мусора обратитесь к региональному оператору.",
    )
    repo.create(
        source_id=private_source.id,
        topic_id=housing.id,
        category_id=waste.id,
        material_type=MaterialType.OFFICIAL_POST,
        status=MaterialStatus.ACTIVE,
        published_at=datetime(2026, 1, 2, tzinfo=UTC),
        original_text="По вопросам мусора обратитесь к региональному оператору.",
        public_text="По вопросам мусора обратитесь к региональному оператору.",
        is_official=False,
    )
    repo.create(
        source_id=official_source.id,
        topic_id=transport.id,
        material_type=MaterialType.OFFICIAL_POST,
        status=MaterialStatus.ACTIVE,
        published_at=datetime(2026, 1, 3, tzinfo=UTC),
        original_text="По аварии на сетях обратитесь в ЕДДС.",
        public_text="По аварии на сетях обратитесь в ЕДДС.",
    )
    service = SearchService(session)
    service.rebuild_index()

    response = service.search_public("Куда обращаться по мусору?", record_problem_query=False)

    assert response.materials == []
    assert response.recommendations == []


def test_recommendation_rebuild_preview_and_execute_are_idempotent() -> None:
    session = seeded_session()
    taxonomy = TaxonomyRepository(session)
    source = SourceRepository(session).create(
        code="rebuild-source",
        name="Официальный канал",
        kind=SourceKind.OFFICIAL_CHANNEL,
    )
    housing = taxonomy.get_topic_by_slug("housing")
    assert housing is not None
    waste = taxonomy.get_category(topic_id=housing.id, slug="waste")
    assert waste is not None
    MaterialRepository(session).create(
        source_id=source.id,
        topic_id=housing.id,
        category_id=waste.id,
        material_type=MaterialType.OFFICIAL_ANSWER,
        status=MaterialStatus.ACTIVE,
        published_at=datetime(2026, 1, 1, tzinfo=UTC),
        original_text="По вопросам вывоза мусора заявку принимает региональный оператор.",
        public_text="По вопросам вывоза мусора заявку принимает региональный оператор.",
    )
    service = RecommendationExtractionService(session)

    preview = service.rebuild(execute=False)
    first = service.rebuild(execute=True)
    second = service.rebuild(execute=True)

    assert preview.would_change == 1
    assert first.changed == 1
    assert second.changed == 0
    assert session.query(MaterialRecommendation).count() == 1


def test_complaint_about_management_company_prefers_oversight_action() -> None:
    session = seeded_session()
    taxonomy = TaxonomyRepository(session)
    source = SourceRepository(session).create(
        code="management-complaint-source",
        name="Официальный канал",
        kind=SourceKind.OFFICIAL_CHANNEL,
    )
    housing = taxonomy.get_topic_by_slug("housing")
    assert housing is not None
    management = taxonomy.get_category(topic_id=housing.id, slug="management_company")
    assert management is not None
    repo = MaterialRepository(session)
    self_service = repo.create(
        source_id=source.id,
        topic_id=housing.id,
        category_id=management.id,
        material_type=MaterialType.OFFICIAL_ANSWER,
        status=MaterialStatus.ACTIVE,
        published_at=datetime(2026, 1, 1, tzinfo=UTC),
        original_text="Для решения вопроса обратитесь в управляющую компанию.",
        public_text="Для решения вопроса обратитесь в управляющую компанию.",
    )
    oversight = repo.create(
        source_id=source.id,
        topic_id=housing.id,
        category_id=management.id,
        material_type=MaterialType.OFFICIAL_ANSWER,
        status=MaterialStatus.ACTIVE,
        published_at=datetime(2026, 1, 2, tzinfo=UTC),
        original_text="Жалобу на управляющую компанию можно направить в жилищную инспекцию.",
        public_text="Жалобу на управляющую компанию можно направить в жилищную инспекцию.",
    )
    service = SearchService(session)
    service.rebuild_index()

    response = service.search_public("Куда жаловаться на управляющую компанию?", record_problem_query=False)

    assert response.materials[0].id == oversight.id
    assert self_service.id in [material.id for material in response.materials]
    assert response.materials.index(oversight) < response.materials.index(self_service)
    assert response.recommendations
    assert response.recommendations[0].material.id == oversight.id
    assert response.recommendations[0].recommendation.action_kind == "oversight"
    assert all(item.recommendation.action_kind != "self_service" for item in response.recommendations)


def test_complaint_about_management_company_hides_self_service_recommendation_when_no_oversight_exists() -> None:
    session = seeded_session()
    taxonomy = TaxonomyRepository(session)
    source = SourceRepository(session).create(
        code="management-self-service-source",
        name="Официальный канал",
        kind=SourceKind.OFFICIAL_CHANNEL,
    )
    housing = taxonomy.get_topic_by_slug("housing")
    assert housing is not None
    management = taxonomy.get_category(topic_id=housing.id, slug="management_company")
    assert management is not None
    MaterialRepository(session).create(
        source_id=source.id,
        topic_id=housing.id,
        category_id=management.id,
        material_type=MaterialType.OFFICIAL_ANSWER,
        status=MaterialStatus.ACTIVE,
        published_at=datetime(2026, 1, 1, tzinfo=UTC),
        original_text="Для решения вопроса обратитесь в управляющую компанию.",
        public_text="Для решения вопроса обратитесь в управляющую компанию.",
    )
    service = SearchService(session)
    service.rebuild_index()

    response = service.search_public("Куда жаловаться на управляющую компанию?", record_problem_query=False)

    assert response.materials
    assert response.recommendations == []
