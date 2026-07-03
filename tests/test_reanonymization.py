from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.enums import MaterialStatus, MaterialType, SourceKind
from app.db.models import Material, RedactionEvent
from app.db.repositories import MaterialRepository, SourceRepository, TaxonomyRepository
from app.db.seed import seed_initial_data
from app.db.session import create_database_engine
from app.search import SearchService
from app.services.material_reanonymization import MaterialReanonymizationService


def test_reanonymization_dry_run_and_execute_updates_existing_public_text() -> None:
    engine = create_database_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    with session_factory() as session:
        seed_initial_data(session)
        taxonomy = TaxonomyRepository(session)
        housing = taxonomy.get_topic_by_slug("housing")
        assert housing is not None
        water = taxonomy.get_category(topic_id=housing.id, slug="water")
        assert water is not None
        source = SourceRepository(session).create(
            code="reanonymization-source",
            name="Официальный канал",
            kind=SourceKind.OFFICIAL_CHANNEL,
        )
        material = MaterialRepository(session).create(
            source_id=source.id,
            topic_id=housing.id,
            category_id=water.id,
            material_type=MaterialType.OFFICIAL_ANSWER,
            status=MaterialStatus.ACTIVE,
            published_at=datetime(2026, 7, 2, tzinfo=UTC),
            original_text="@brulik0708, Здравствуйте. Управлением транспорта указано.",
            public_text="@brulik0708, Здравствуйте. Управлением транспорта указано.",
        )
        SearchService(session).rebuild_index()
        material_id = material.id
        service = MaterialReanonymizationService(session)

        preview = service.preview()

        assert preview.scanned == 1
        assert preview.would_update == 1
        assert preview.redactions == 1
        assert session.get(Material, material_id).public_text.startswith("@brulik0708")

        result = service.execute()
        session.commit()

        assert result.updated == 1
        updated = session.get(Material, material_id)
        assert updated is not None
        assert updated.status == MaterialStatus.ACTIVE
        assert updated.public_text.startswith("Здравствуйте")
        assert "@brulik0708" not in updated.public_text
        event = session.query(RedactionEvent).filter_by(redaction_type="salutation_addressee").one()
        assert event.original_fragment == "@brulik0708,"
        assert event.replacement == ""
        assert SearchService(session).search_public("@brulik0708", record_problem_query=False).materials == []
        assert SearchService(session).search_public("Здравствуйте", record_problem_query=False).materials[0].id == material_id
