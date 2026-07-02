from __future__ import annotations

import json
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings
from app.db.base import Base
from app.db.enums import ImportStatus, MaterialStatus, MaterialType, ProblemQueryChannel, SourceKind
from app.db.models import AdminNote, ImportBatch, ImportReport, Material, PersonNameReview, ProblemQuery, RedactionEvent, Source
from app.db.repositories import ImportRepository, MaterialRepository, SourceRepository, TaxonomyRepository
from app.db.seed import seed_initial_data
from app.db.session import create_database_engine, get_db_session
from app.importers.telegram_json import (
    TelegramImportError,
    TelegramJsonImporter,
    extract_text,
    identifiers_match,
    parse_telegram_export,
)
from app.main import create_app
from app.services.import_cleanup import cleanup_test_import_materials, preview_test_import_cleanup


@pytest.fixture()
def session() -> Generator[Session, None, None]:
    engine = create_database_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    with session_factory() as db_session:
        seed_initial_data(db_session)
        yield db_session


def telegram_export(messages: list[dict]) -> bytes:
    return json.dumps(
        {
            "id": "official_channel_1",
            "name": "Администрация",
            "type": "public_channel",
            "messages": messages,
        },
        ensure_ascii=False,
    ).encode("utf-8")


def test_parse_telegram_export_accepts_standard_messages_list() -> None:
    payload = parse_telegram_export(telegram_export([{"id": 1, "type": "message", "text": "Текст"}]))

    assert isinstance(payload["messages"], list)


def test_parse_telegram_export_rejects_html_and_missing_messages() -> None:
    with pytest.raises(TelegramImportError):
        parse_telegram_export(b"<html></html>")
    with pytest.raises(TelegramImportError):
        parse_telegram_export(b'{"name":"chat"}')


def test_extract_text_supports_string_and_fragment_list() -> None:
    assert extract_text("Обычный текст") == "Обычный текст"
    assert (
        extract_text(
            [
                "Первая часть ",
                {"type": "bold", "text": "важная"},
                {"type": "link", "text": " ссылка"},
            ]
        )
        == "Первая часть важная ссылка"
    )


def test_identifiers_match_normalizes_at_and_telegram_prefixes() -> None:
    assert identifiers_match({"@official"}, "official")
    assert identifiers_match({"channel12345"}, "12345")
    assert not identifiers_match({"other"}, "official")


def test_importer_creates_draft_materials_only_for_fixed_official_source(
    session: Session,
    tmp_path: Path,
) -> None:
    settings = Settings(
        AUTO_DB_BOOTSTRAP=False,
        OFFICIAL_TELEGRAM_SOURCE_ID="official_channel_1",
        OFFICIAL_TELEGRAM_SOURCE_NAME="Администрация",
        OFFICIAL_TELEGRAM_SOURCE_KIND="official_channel",
    )
    content = telegram_export(
        [
            {
                "id": 10,
                "type": "message",
                "date": "2026-06-20T10:00:00",
                "from_id": "official_channel_1",
                "text": "Официальный пост про отопление.",
            },
            {
                "id": 11,
                "type": "message",
                "date": "2026-06-20T10:05:00",
                "from_id": "resident_1",
                "text": "Когда включат отопление?",
            },
            {
                "id": 12,
                "type": "message",
                "from_id": "official_channel_1",
                "text": "Официальное сообщение без даты.",
            },
            {"id": 13, "type": "service", "text": ""},
        ]
    )

    result = TelegramJsonImporter(
        session=session,
        settings=settings,
        imports_dir=tmp_path / "imports",
        exports_dir=tmp_path / "exports",
    ).import_bytes(filename="result.json", content=content)
    session.commit()

    source = session.query(Source).one()
    material = session.query(Material).one()
    batch = session.get(ImportBatch, result.batch_id)
    report = session.get(ImportReport, result.report_id)

    assert source.external_id == "official_channel_1"
    assert material.status == MaterialStatus.DRAFT
    assert material.material_type == MaterialType.OFFICIAL_POST
    assert material.original_text == "Официальный пост про отопление."
    assert batch is not None
    assert batch.status == ImportStatus.COMPLETED
    assert report is not None
    assert report.summary["official_materials_found"] == 1
    assert report.summary["resident_questions_found"] == 1
    assert report.summary["needs_review_count"] == 1
    assert report.summary["redactions_applied"] == 0
    assert report.summary["anonymization_status"] == "completed"
    assert report.summary["person_name_reviews_count"] == 0
    assert batch.anonymized_file_path is not None
    assert Path(batch.anonymized_file_path).exists()
    assert Path(report.report_file_path or "").exists()


def test_importer_records_duplicate_without_failing_batch(session: Session, tmp_path: Path) -> None:
    settings = Settings(
        AUTO_DB_BOOTSTRAP=False,
        OFFICIAL_TELEGRAM_SOURCE_ID="official_channel_1",
        OFFICIAL_TELEGRAM_SOURCE_NAME="Администрация",
        OFFICIAL_TELEGRAM_SOURCE_KIND="official_channel",
    )
    content = telegram_export(
        [
            {
                "id": 10,
                "type": "message",
                "date": "2026-06-20T10:00:00",
                "from_id": "official_channel_1",
                "text": "Первый официальный пост.",
            }
        ]
    )
    importer = TelegramJsonImporter(
        session=session,
        settings=settings,
        imports_dir=tmp_path / "imports",
        exports_dir=tmp_path / "exports",
    )
    importer.import_bytes(filename="first.json", content=content)
    duplicate_result = importer.import_bytes(filename="second.json", content=content)
    session.commit()

    report = session.get(ImportReport, duplicate_result.report_id)

    assert session.query(Material).count() == 1
    assert report is not None
    assert report.summary["duplicate_count"] == 1


def test_imported_draft_materials_do_not_appear_in_public_search(
    session: Session,
    tmp_path: Path,
) -> None:
    settings = Settings(
        AUTO_DB_BOOTSTRAP=False,
        OFFICIAL_TELEGRAM_SOURCE_ID="official_channel_1",
        OFFICIAL_TELEGRAM_SOURCE_NAME="Администрация",
        OFFICIAL_TELEGRAM_SOURCE_KIND="official_channel",
    )
    TelegramJsonImporter(
        session=session,
        settings=settings,
        imports_dir=tmp_path / "imports",
        exports_dir=tmp_path / "exports",
    ).import_bytes(
        filename="result.json",
        content=telegram_export(
            [
                {
                    "id": 10,
                    "type": "message",
                    "date": "2026-06-20T10:00:00",
                    "from_id": "official_channel_1",
                    "text": "Официальный пост про отопление.",
                }
            ]
        ),
    )
    session.commit()

    assert MaterialRepository(session).search_public(query="отопление") == []


def test_cleanup_test_import_materials_removes_only_unapproved_imported_materials(
    session: Session,
    tmp_path: Path,
) -> None:
    settings = Settings(
        AUTO_DB_BOOTSTRAP=False,
        OFFICIAL_TELEGRAM_SOURCE_ID="official_channel_1",
        OFFICIAL_TELEGRAM_SOURCE_NAME="РђРґРјРёРЅРёСЃС‚СЂР°С†РёСЏ",
        OFFICIAL_TELEGRAM_SOURCE_KIND="official_channel",
    )
    result = TelegramJsonImporter(
        session=session,
        settings=settings,
        imports_dir=tmp_path / "imports",
        exports_dir=tmp_path / "exports",
    ).import_bytes(
        filename="partial.json",
        content=telegram_export(
            [
                {
                    "id": 10,
                    "type": "message",
                    "date": "2026-06-20T10:00:00",
                    "from_id": "official_channel_1",
                    "text": "РўРµСЃС‚РѕРІС‹Р№ РёРјРїРѕСЂС‚ РїСЂРѕ РІРѕРґСѓ.",
                }
            ]
        ),
    )
    source = session.query(Source).filter_by(external_id="official_channel_1").one()
    taxonomy = TaxonomyRepository(session)
    topic = taxonomy.get_topic_by_slug("housing")
    assert topic is not None
    imported_draft = session.query(Material).filter_by(external_message_id="10").one()
    imported_draft_id = imported_draft.id
    session.add(AdminNote(material_id=imported_draft.id, body="temporary note", author="admin"))
    session.add(
        ProblemQuery(
            anonymized_text="safe query",
            shown_material_id=imported_draft.id,
            channel=ProblemQueryChannel.WEBSITE,
        )
    )
    MaterialRepository(session).create(
        source_id=source.id,
        topic_id=topic.id,
        import_batch_id=result.batch_id,
        external_message_id="11",
        material_type=MaterialType.OFFICIAL_POST,
        status=MaterialStatus.ACTIVE,
        published_at=datetime(2026, 6, 20, tzinfo=UTC),
        original_text="РћРїСѓР±Р»РёРєРѕРІР°РЅРЅС‹Р№ РёРјРїРѕСЂС‚.",
        public_text="РћРїСѓР±Р»РёРєРѕРІР°РЅРЅС‹Р№ РёРјРїРѕСЂС‚.",
    )
    manual_source = SourceRepository(session).create(
        code="manual:test",
        name="Manual",
        kind=SourceKind.WEBSITE,
        external_id="manual",
    )
    manual_draft = MaterialRepository(session).create(
        source_id=manual_source.id,
        topic_id=topic.id,
        material_type=MaterialType.OFFICIAL_POST,
        status=MaterialStatus.DRAFT,
        published_at=datetime(2026, 6, 21, tzinfo=UTC),
        original_text="Р СѓС‡РЅРѕР№ С‡РµСЂРЅРѕРІРёРє.",
        public_text="Р СѓС‡РЅРѕР№ С‡РµСЂРЅРѕРІРёРє.",
    )
    manual_draft_id = manual_draft.id
    session.commit()

    preview = preview_test_import_cleanup(session, source_external_id="official_channel_1")
    assert preview.materials == 1
    assert preview.admin_notes == 1
    assert preview.problem_queries_to_unlink == 1

    cleanup_test_import_materials(session, source_external_id="official_channel_1")
    session.commit()

    assert session.get(Material, imported_draft_id) is None
    assert session.get(Material, manual_draft_id) is not None
    assert session.query(Material).filter_by(external_message_id="11").one().status == MaterialStatus.ACTIVE
    assert session.query(ProblemQuery).one().shown_material_id is None


def test_importer_redacts_personal_data_and_keeps_original_text(
    session: Session,
    tmp_path: Path,
) -> None:
    settings = Settings(
        AUTO_DB_BOOTSTRAP=False,
        OFFICIAL_TELEGRAM_SOURCE_ID="official_channel_1",
        OFFICIAL_TELEGRAM_SOURCE_NAME="Администрация",
        OFFICIAL_TELEGRAM_SOURCE_KIND="official_channel",
    )
    original_text = (
        "По обращению АБ-12345 жителя Иванова Ивана Ивановича по адресу "
        "улица Ленина дом 10 корпус 2 квартира 5 сообщаем: работы выполнены. "
        "Личный профиль заявителя: https://vk.com/ivan.petrov. "
        "Аварийная служба доступна по телефону +7 495 000-00-00."
    )

    result = TelegramJsonImporter(
        session=session,
        settings=settings,
        imports_dir=tmp_path / "imports",
        exports_dir=tmp_path / "exports",
    ).import_bytes(
        filename="result.json",
        content=telegram_export(
            [
                {
                    "id": 20,
                    "type": "message",
                    "date": "2026-06-20T10:00:00",
                    "from_id": "official_channel_1",
                    "text": original_text,
                }
            ]
        ),
    )
    session.commit()

    material = session.query(Material).one()
    batch = session.get(ImportBatch, result.batch_id)
    report = session.get(ImportReport, result.report_id)

    assert material.original_text == original_text
    assert material.public_text != original_text
    assert "[номер обращения скрыт]" in material.public_text
    assert "[адрес скрыт]" in material.public_text
    assert "[ссылка на профиль скрыта]" in material.public_text
    assert "Иванова Ивана Ивановича" in material.public_text
    assert "vk.com/ivan.petrov" not in material.public_text
    assert "+7 495 000-00-00" in material.public_text
    assert material.status == MaterialStatus.NEEDS_REVIEW
    assert material.has_personal_data is True
    assert material.needs_person_name_review is True
    assert session.query(RedactionEvent).count() >= 2
    assert session.query(PersonNameReview).count() == 1
    assert batch is not None
    assert batch.anonymized_file_path is not None
    anonymized_body = json.loads(Path(batch.anonymized_file_path).read_text(encoding="utf-8"))
    assert "[адрес скрыт]" in anonymized_body["messages"][0]["text"]
    assert "Ленина" not in anonymized_body["messages"][0]["text"]
    assert "vk.com/ivan.petrov" not in anonymized_body["messages"][0]["text"]
    assert report is not None
    assert report.summary["redactions_applied"] >= 2
    assert report.summary["person_name_reviews_count"] == 1
    assert report.summary["needs_review_count"] == 1


@pytest.fixture()
def admin_import_app_context(
    tmp_path: Path,
) -> tuple[TestClient, sessionmaker[Session], Path]:
    engine = create_database_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    with session_factory() as seed_session:
        seed_initial_data(seed_session)

    def override_db() -> Generator[Session, None, None]:
        with session_factory() as db_session:
            yield db_session

    settings = Settings(
        AUTO_DB_BOOTSTRAP=False,
        ADMIN_USERNAME="admin",
        ADMIN_PASSWORD="secret",
        SECRET_KEY="test-secret",
        OFFICIAL_TELEGRAM_SOURCE_ID="official_channel_1",
        OFFICIAL_TELEGRAM_SOURCE_NAME="Администрация",
        OFFICIAL_TELEGRAM_SOURCE_KIND="official_channel",
    )
    app = create_app()
    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app), session_factory, tmp_path


def admin_login(client: TestClient) -> str:
    response = client.post(
        "/admin/login",
        data={"username": "admin", "password": "secret"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    page = client.get("/admin/imports")
    assert page.status_code == 200
    marker = 'name="csrf_token" value="'
    start = page.text.index(marker) + len(marker)
    end = page.text.index('"', start)
    return page.text[start:end]


def test_admin_import_requires_login(admin_import_app_context: tuple[TestClient, sessionmaker[Session], Path]) -> None:
    client, _, _ = admin_import_app_context

    response = client.get("/admin/imports", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login"


def test_admin_upload_imports_json_and_downloads_report(
    admin_import_app_context: tuple[TestClient, sessionmaker[Session], Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, session_factory, tmp_path = admin_import_app_context
    monkeypatch.chdir(tmp_path)
    csrf_token = admin_login(client)

    response = client.post(
        "/admin/imports",
        data={"csrf_token": csrf_token},
        files={
            "file": (
                "result.json",
                telegram_export(
                    [
                        {
                            "id": 10,
                            "type": "message",
                            "date": "2026-06-20T10:00:00",
                            "from_id": "official_channel_1",
                            "text": "Официальный пост про воду.",
                        }
                    ]
                ),
                "application/json",
            )
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/admin/imports/")
    detail = client.get(response.headers["location"])
    assert detail.status_code == 200
    assert "Официальных материалов" in detail.text
    with session_factory() as session:
        batch = session.query(ImportBatch).one()
        report = ImportRepository(session).get_report_for_batch(batch.id)
        assert report is not None
    download = client.get(f"/admin/imports/{batch.id}/report/download")
    assert download.status_code == 200
    assert download.headers["content-type"].startswith("application/json")
