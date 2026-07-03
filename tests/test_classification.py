from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.db.enums import MaterialStatus, MaterialType, SourceKind
from app.db.repositories import MaterialRepository, SourceRepository, TaxonomyRepository
from app.db.seed import seed_initial_data
from app.db.session import create_database_engine
from app.search.service import SearchService
from app.services.classification import classify_material_text
from app.services.material_reclassification import MaterialReclassificationService


def test_classifier_routes_transport_without_matching_water() -> None:
    classification = classify_material_text(
        "Здравствуйте. Управлением транспорта руководителю перевозчика указано "
        "провести инструктаж с водителями по маршруту № 8."
    )

    assert classification.topic_slug == "transport"
    assert classification.category_slug is None


def test_classifier_routes_housing_categories_and_avoids_kotelnaya_street_false_positive() -> None:
    assert classify_material_text("Утечка на сетях водоснабжения будет устранена.").category_slug == "water"
    assert classify_material_text("Отключение ГВС связано с ремонтом.").category_slug == "water"
    assert classify_material_text("Отопление восстановят после ремонта котельной.").category_slug == "heating"
    assert classify_material_text("Мусорные контейнеры и отходы вывезет региональный оператор.").category_slug == "waste"

    road = classify_material_text(
        "Текущий ремонт автомобильной дороги на ул. 1-й Котельной запланирован на 2026 год."
    )

    assert road.topic_slug == "improvement"
    assert road.category_slug is None


def test_classifier_ignores_weak_category_words_without_domain_anchor() -> None:
    gasoline = classify_material_text(
        "Здравствуйте. Наблюдается агрессивный спрос на бензин. "
        "Объем поставок топлива корректируется с потребностью потребителей."
    )
    moderation = classify_material_text(
        "Здравствуйте. Реакции, которые ассоциируются с агрессивным недовольством, скрываются в каналах органов власти."
    )
    hot_surface = classify_material_text(
        "Здравствуйте. Горячее обсуждение ремонта дороги не относится к водоснабжению дома."
    )

    assert gasoline.category_slug == "other"
    assert moderation.category_slug == "other"
    assert hot_surface.topic_slug == "improvement"
    assert hot_surface.category_slug is None


def test_reclassification_preview_and_execute_update_topics_categories_and_search_index() -> None:
    engine = create_database_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    with session_factory() as session:
        seed_initial_data(session)
        source = SourceRepository(session).create(
            code="reclass-source",
            name="Официальный источник",
            kind=SourceKind.OFFICIAL_CHANNEL,
        )
        taxonomy = TaxonomyRepository(session)
        housing = taxonomy.get_topic_by_slug("housing")
        assert housing is not None
        water = taxonomy.get_category(topic_id=housing.id, slug="water")
        other = taxonomy.get_category(topic_id=housing.id, slug="other")
        waste = taxonomy.get_category(topic_id=housing.id, slug="waste")
        assert water is not None
        assert other is not None
        assert waste is not None
        repo = MaterialRepository(session)
        transport_material = repo.create(
            source_id=source.id,
            topic_id=housing.id,
            category_id=water.id,
            material_type=MaterialType.OFFICIAL_ANSWER,
            status=MaterialStatus.ACTIVE,
            published_at=datetime(2026, 7, 3, tzinfo=UTC),
            original_text="Водителям автобусов маршрута № 8 проведут инструктаж.",
            public_text="Водителям автобусов маршрута № 8 проведут инструктаж.",
        )
        waste_material = repo.create(
            source_id=source.id,
            topic_id=housing.id,
            category_id=other.id,
            material_type=MaterialType.OFFICIAL_ANSWER,
            status=MaterialStatus.ACTIVE,
            published_at=datetime(2026, 7, 3, tzinfo=UTC),
            original_text="Мусорные контейнеры будут вывезены региональным оператором.",
            public_text="Мусорные контейнеры будут вывезены региональным оператором.",
        )
        public_text_wins_material = repo.create(
            source_id=source.id,
            topic_id=housing.id,
            category_id=water.id,
            material_type=MaterialType.OFFICIAL_ANSWER,
            status=MaterialStatus.ACTIVE,
            published_at=datetime(2026, 7, 3, tzinfo=UTC),
            original_text="Для подачи заявки на отлов безнадзорных животных необходимо обратиться по телефону.",
            public_text="Ремонт автомобильной дороги в районе Северной площади включен в план работ на 2026 год.",
        )
        SearchService(session).rebuild_index()
        session.commit()

        service = MaterialReclassificationService(session)
        preview = service.preview(status=MaterialStatus.ACTIVE)

        assert preview.scanned == 3
        assert preview.would_change == 3

        result = service.execute(status=MaterialStatus.ACTIVE)
        session.commit()

        assert result.changed == 3
        session.refresh(transport_material)
        session.refresh(waste_material)
        session.refresh(public_text_wins_material)
        assert transport_material.topic.slug == "transport"
        assert transport_material.category_id is None
        assert waste_material.topic.slug == "housing"
        assert waste_material.category_id == waste.id
        assert public_text_wins_material.topic.slug == "improvement"
        assert public_text_wins_material.category_id is None
        assert SearchService(session).search_public("автобус маршрут", record_problem_query=False).materials == []
        assert SearchService(session).search_public("мусор контейнеры", record_problem_query=False).materials[0].id == waste_material.id
