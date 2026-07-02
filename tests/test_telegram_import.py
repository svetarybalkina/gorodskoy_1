from __future__ import annotations

import json
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings
from app.db.base import Base
from app.db.enums import ImportStatus, MaterialStatus, MaterialType, SourceKind
from app.db.models import ImportBatch, ImportReport, Material, Source
from app.db.repositories import ImportRepository, MaterialRepository
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
    assert report.summary["anonymization_status"] == "pending_task_7"
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
