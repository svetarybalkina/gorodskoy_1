from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.db.base import Base
from app.db.enums import (
    LinkReason,
    MaterialStatus,
    MaterialType,
    ProblemQueryAction,
    ProblemQueryChannel,
    SourceKind,
)
from app.db.models import Material, MaterialLink, Setting
from app.db.repositories import (
    AdminNoteRepository,
    ImportRepository,
    MaterialRepository,
    ProblemQueryRepository,
    QuestionRepository,
    ReviewRepository,
    SourceRepository,
    TaxonomyRepository,
)
from app.db.seed import seed_initial_data
from app.db.session import create_database_engine


@pytest.fixture()
def session() -> Session:
    engine = create_database_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    with session_factory() as db_session:
        yield db_session


def test_alembic_migration_creates_mvp_tables(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database_path = tmp_path / "migration.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path.as_posix()}")
    get_settings.cache_clear()

    alembic_config = Config("alembic.ini")
    command.upgrade(alembic_config, "head")

    engine = create_database_engine(f"sqlite:///{database_path.as_posix()}")
    table_names = set(inspect(engine).get_table_names())

    assert {
        "materials",
        "sources",
        "topics",
        "categories",
        "resident_questions",
        "question_variants",
        "material_links",
        "import_batches",
        "import_reports",
        "redaction_events",
        "person_name_reviews",
        "problem_queries",
        "admin_notes",
        "settings",
    }.issubset(table_names)

    get_settings.cache_clear()


def test_seed_initial_data_creates_topics_categories_and_ads_setting(session: Session) -> None:
    seed_initial_data(session)

    taxonomy = TaxonomyRepository(session)
    housing = taxonomy.get_topic_by_slug("housing")
    animals = taxonomy.get_topic_by_slug("animals")
    ads_setting = session.query(Setting).filter_by(key="ADS_ENABLED").one()

    assert housing is not None
    assert housing.is_public is True
    assert animals is not None
    assert animals.is_public is False
    assert taxonomy.get_category(topic_id=housing.id, slug="heating") is not None
    assert taxonomy.get_category(topic_id=housing.id, slug="animals") is not None
    assert taxonomy.get_category(topic_id=housing.id, slug="stray_dogs") is None
    assert ads_setting.value == "false"


def test_repositories_create_core_entities_and_links(session: Session) -> None:
    seed_initial_data(session)
    taxonomy = TaxonomyRepository(session)
    source = SourceRepository(session).create(
        code="official-bot",
        name="Официальный бот",
        kind=SourceKind.OFFICIAL_BOT,
        external_id="@official_bot",
    )
    topic = taxonomy.get_topic_by_slug("housing")
    assert topic is not None
    category = taxonomy.get_category(topic_id=topic.id, slug="heating")
    assert category is not None

    batch = ImportRepository(session).create_batch(
        filename="result.json",
        source_file_path="imports/result.json",
        anonymized_file_path="exports/result.anonymized.json",
    )
    material = MaterialRepository(session).create(
        source_id=source.id,
        topic_id=topic.id,
        category_id=category.id,
        import_batch_id=batch.id,
        external_message_id="100",
        material_type=MaterialType.OFFICIAL_ANSWER,
        status=MaterialStatus.ACTIVE,
        published_at=datetime(2026, 6, 16, tzinfo=UTC),
        original_text="Оригинальный официальный текст.",
        public_text="Оригинальный официальный текст.",
    )
    question_repo = QuestionRepository(session)
    question = question_repo.create_resident_question(
        anonymized_text="Когда включат отопление?",
        normalized_text="когда включить отопление",
        category_id=category.id,
        import_batch_id=batch.id,
    )
    link = question_repo.link_to_material(
        question_id=question.id,
        material_id=material.id,
        reason=LinkReason.IMPORTED_PAIR,
        confidence=90,
    )
    question_repo.create_variant(
        material_id=material.id,
        text="Почему нет отопления?",
        normalized_text="почему нет отопление",
        is_confirmed=True,
    )
    ImportRepository(session).create_report(
        import_batch_id=batch.id,
        summary={"materials_found": 1},
        errors=[],
        report_file_path="exports/report.json",
    )
    ReviewRepository(session).create_redaction_event(
        material_id=material.id,
        field_name="public_text",
        redaction_type="phone",
        original_fragment="+7 999 000-00-00",
        replacement="[телефон скрыт]",
        is_confirmed=True,
    )
    ReviewRepository(session).create_person_name_review(
        material_id=material.id,
        detected_name="Иванов Иван Иванович",
        context="Ответ подписан Ивановым Иваном Ивановичем",
    )
    ProblemQueryRepository(session).create(
        anonymized_text="Ответ не подошел",
        channel=ProblemQueryChannel.WEBSITE,
        shown_material_id=material.id,
        category_id=category.id,
        similar_material_ids=[material.id],
        user_action=ProblemQueryAction.REPHRASE,
        match_level="weak",
    )
    AdminNoteRepository(session).create(
        material_id=material.id,
        body="Проверить актуальность после отопительного сезона",
        author="admin",
    )

    session.commit()

    assert link.material_id == material.id
    assert session.query(Material).count() == 1


def test_public_active_query_excludes_non_public_statuses_and_topics(session: Session) -> None:
    seed_initial_data(session)
    taxonomy = TaxonomyRepository(session)
    source = SourceRepository(session).create(
        code="official-channel",
        name="Официальный канал",
        kind=SourceKind.OFFICIAL_CHANNEL,
    )
    housing = taxonomy.get_topic_by_slug("housing")
    transport = taxonomy.get_topic_by_slug("transport")
    assert housing is not None
    assert transport is not None
    repo = MaterialRepository(session)

    visible = repo.create(
        source_id=source.id,
        topic_id=housing.id,
        material_type=MaterialType.OFFICIAL_POST,
        status=MaterialStatus.ACTIVE,
        published_at=datetime(2026, 6, 16, tzinfo=UTC),
        original_text="Публичный материал.",
        public_text="Публичный материал.",
    )

    for status in [
        MaterialStatus.DRAFT,
        MaterialStatus.NEEDS_REVIEW,
        MaterialStatus.ARCHIVED,
        MaterialStatus.HIDDEN,
        MaterialStatus.DUPLICATE,
        MaterialStatus.PENDING_DELETE,
    ]:
        repo.create(
            source_id=source.id,
            topic_id=housing.id,
            material_type=MaterialType.OFFICIAL_POST,
            status=status,
            published_at=datetime(2026, 6, 16, tzinfo=UTC),
            original_text=f"Материал со статусом {status.value}.",
            public_text=f"Материал со статусом {status.value}.",
        )

    repo.create(
        source_id=source.id,
        topic_id=transport.id,
        material_type=MaterialType.OFFICIAL_POST,
        status=MaterialStatus.ACTIVE,
        published_at=datetime(2026, 6, 16, tzinfo=UTC),
        original_text="Активный материал неактивной темы.",
        public_text="Активный материал неактивной темы.",
    )
    session.commit()

    assert repo.list_public_active() == [visible]


def test_database_constraints_are_enforced(session: Session) -> None:
    seed_initial_data(session)
    source = SourceRepository(session).create(
        code="constraint-source",
        name="Источник",
        kind=SourceKind.OFFICIAL_BOT,
    )
    topic = TaxonomyRepository(session).get_topic_by_slug("housing")
    assert topic is not None

    session.add(
        Material(
            source_id=source.id,
            topic_id=topic.id,
            material_type=MaterialType.OFFICIAL_ANSWER,
            status=MaterialStatus.DRAFT,
            published_at=datetime(2026, 6, 16, tzinfo=UTC),
            public_text="Публичный текст без оригинала.",
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()

    session.add(Setting(key="ADS_ENABLED", value="true"))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()

    session.add(MaterialLink(question_id=999, material_id=999))
    with pytest.raises(IntegrityError):
        session.flush()
