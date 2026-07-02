from __future__ import annotations

import json
import re
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.enums import ImportStatus, MaterialStatus, MaterialType, SourceKind
from app.db.models import Material
from app.db.repositories import ImportRepository, MaterialRepository, ReviewRepository, SourceRepository, TaxonomyRepository
from app.services.anonymization import AnonymizationResult, anonymize_text


class TelegramImportError(ValueError):
    pass


@dataclass
class TelegramMessage:
    index: int
    message_id: str | None
    date: datetime | None
    text: str
    source_identifiers: set[str]
    is_service: bool = False


@dataclass
class TelegramImportResult:
    batch_id: int
    report_id: int
    summary: dict[str, Any]
    errors: list[dict[str, Any]]
    report_file_path: str | None


@dataclass
class ImportReportBuilder:
    total_messages: int = 0
    processed_messages: int = 0
    official_materials_found: int = 0
    official_posts_found: int = 0
    official_answers_found: int = 0
    resident_questions_found: int = 0
    duplicate_count: int = 0
    draft_count: int = 0
    needs_review_count: int = 0
    redactions_applied: int = 0
    person_name_reviews_count: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)
    materials: list[dict[str, Any]] = field(default_factory=list)
    review_cases: list[dict[str, Any]] = field(default_factory=list)

    def add_error(
        self,
        *,
        index: int | None,
        message_id: str | None,
        code: str,
        description: str,
    ) -> None:
        self.errors.append(
            {
                "index": index,
                "message_id": message_id,
                "code": code,
                "description": description,
            }
        )

    def add_review_case(
        self,
        *,
        index: int | None,
        message_id: str | None,
        code: str,
        description: str,
    ) -> None:
        self.review_cases.append(
            {
                "index": index,
                "message_id": message_id,
                "code": code,
                "description": description,
            }
        )

    def summary(self) -> dict[str, Any]:
        return {
            "total_messages": self.total_messages,
            "processed_messages": self.processed_messages,
            "official_materials_found": self.official_materials_found,
            "official_posts_found": self.official_posts_found,
            "official_answers_found": self.official_answers_found,
            "resident_questions_found": self.resident_questions_found,
            "errors_count": len(self.errors),
            "duplicate_count": self.duplicate_count,
            "draft_count": self.draft_count,
            "needs_review_count": self.needs_review_count,
            "redactions_applied": self.redactions_applied,
            "person_name_reviews_count": self.person_name_reviews_count,
            "anonymization_status": "completed",
            "materials": self.materials,
            "review_cases": self.review_cases,
        }


class TelegramJsonImporter:
    def __init__(
        self,
        *,
        session: Session,
        settings: Settings,
        imports_dir: Path = Path("imports"),
        exports_dir: Path = Path("exports"),
    ) -> None:
        self.session = session
        self.settings = settings
        self.imports_dir = imports_dir
        self.exports_dir = exports_dir

    def import_bytes(self, *, filename: str, content: bytes) -> TelegramImportResult:
        source_id = self.settings.official_telegram_source_id.strip()
        if not source_id:
            raise TelegramImportError("OFFICIAL_TELEGRAM_SOURCE_ID is not configured")
        source_kind = self._parse_source_kind(self.settings.official_telegram_source_kind)
        source = SourceRepository(self.session).get_or_update_official_telegram_source(
            source_id=source_id,
            name=self.settings.official_telegram_source_name,
            kind=source_kind,
        )

        self.imports_dir.mkdir(parents=True, exist_ok=True)
        self.exports_dir.mkdir(parents=True, exist_ok=True)
        safe_filename = self._safe_filename(filename)
        token = uuid.uuid4().hex
        source_path = self.imports_dir / f"{token}_{safe_filename}"
        source_path.write_bytes(content)

        import_repo = ImportRepository(self.session)
        batch = import_repo.create_batch(
            filename=safe_filename,
            source_file_path=source_path.as_posix(),
            status=ImportStatus.PROCESSING,
        )
        batch.started_at = datetime.now(UTC)
        self.session.flush()

        builder = ImportReportBuilder()
        try:
            payload = parse_telegram_export(content)
            anonymized_payload = deepcopy(payload)
            messages = payload["messages"]
            anonymized_messages = anonymized_payload.get("messages", [])
            export_identifiers = extract_export_identifiers(payload)
            builder.total_messages = len(messages)
            for index, raw_message in enumerate(messages):
                try:
                    parsed = parse_telegram_message(
                        raw_message,
                        index=index,
                        export_identifiers=export_identifiers,
                    )
                    if parsed.is_service:
                        builder.add_review_case(
                            index=index,
                            message_id=parsed.message_id,
                            code="service_message",
                            description="Служебное сообщение Telegram пропущено.",
                        )
                        continue
                    if not parsed.text:
                        builder.add_review_case(
                            index=index,
                            message_id=parsed.message_id,
                            code="empty_text",
                            description="Сообщение без текстового содержимого пропущено.",
                        )
                        continue
                    builder.processed_messages += 1
                    anonymization = anonymize_text(parsed.text)
                    builder.redactions_applied += len(anonymization.redactions)
                    if isinstance(anonymized_messages, list) and index < len(anonymized_messages):
                        anonymized_message = anonymized_messages[index]
                        if isinstance(anonymized_message, dict):
                            anonymized_message["text"] = anonymization.text
                    if identifiers_match(parsed.source_identifiers, source_id):
                        self._create_material(
                            source_id=source.id,
                            source_kind=source_kind,
                            message=parsed,
                            anonymization=anonymization,
                            batch_id=batch.id,
                            builder=builder,
                        )
                    else:
                        builder.resident_questions_found += 1
                except Exception as exc:  # noqa: BLE001
                    builder.add_error(
                        index=index,
                        message_id=_safe_message_id(raw_message),
                        code="message_processing_error",
                        description=f"Сообщение пропущено: {exc.__class__.__name__}.",
                    )
            batch.status = ImportStatus.COMPLETED_WITH_ERRORS if builder.errors else ImportStatus.COMPLETED
        except TelegramImportError as exc:
            batch.status = ImportStatus.FAILED
            anonymized_payload = None
            builder.add_error(index=None, message_id=None, code="invalid_export", description=str(exc))

        batch.total_messages = builder.total_messages
        batch.processed_messages = builder.processed_messages
        batch.error_count = len(builder.errors)
        batch.finished_at = datetime.now(UTC)
        summary = builder.summary()
        anonymized_path = self.exports_dir / f"{token}_anonymized.json"
        if anonymized_payload is not None:
            anonymized_path.write_text(
                json.dumps(anonymized_payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            batch.anonymized_file_path = anonymized_path.as_posix()
        report_path = self.exports_dir / f"{token}_report.json"
        report_body = {
            "batch_id": batch.id,
            "filename": safe_filename,
            "source_id": source.external_id,
            "source_name": source.name,
            "anonymized_file_path": batch.anonymized_file_path,
            "summary": summary,
            "errors": builder.errors,
        }
        report_path.write_text(
            json.dumps(report_body, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        report = import_repo.create_report(
            import_batch_id=batch.id,
            summary=summary,
            errors=builder.errors,
            report_file_path=report_path.as_posix(),
        )
        return TelegramImportResult(
            batch_id=batch.id,
            report_id=report.id,
            summary=summary,
            errors=builder.errors,
            report_file_path=report.report_file_path,
        )

    def _create_material(
        self,
        *,
        source_id: int,
        source_kind: SourceKind,
        message: TelegramMessage,
        anonymization: AnonymizationResult,
        batch_id: int,
        builder: ImportReportBuilder,
    ) -> None:
        if message.message_id and self._material_exists(source_id=source_id, message_id=message.message_id):
            builder.duplicate_count += 1
            builder.add_review_case(
                index=message.index,
                message_id=message.message_id,
                code="duplicate",
                description="Материал с таким Telegram id уже был импортирован.",
            )
            return
        if message.date is None:
            builder.add_review_case(
                index=message.index,
                message_id=message.message_id,
                code="missing_date",
                description="Официальное сообщение без даты не сохранено как материал.",
            )
            builder.needs_review_count += 1
            return

        taxonomy = TaxonomyRepository(self.session)
        topic = taxonomy.get_topic_by_slug("housing")
        if topic is None:
            raise TelegramImportError("Base topic 'housing' is missing")
        category = taxonomy.get_category(topic_id=topic.id, slug=self._guess_category_slug(message.text))
        if category is None:
            category = taxonomy.get_category(topic_id=topic.id, slug="other")

        material_type = (
            MaterialType.OFFICIAL_ANSWER
            if source_kind in {SourceKind.OFFICIAL_BOT, SourceKind.TELEGRAM_BOT}
            else MaterialType.OFFICIAL_POST
        )
        status = MaterialStatus.NEEDS_REVIEW if anonymization.needs_review else MaterialStatus.DRAFT
        material = MaterialRepository(self.session).create(
            source_id=source_id,
            topic_id=topic.id,
            category_id=category.id if category else None,
            import_batch_id=batch_id,
            external_message_id=message.message_id,
            material_type=material_type,
            status=status,
            published_at=message.date,
            original_text=message.text,
            public_text=anonymization.text,
            has_personal_data=anonymization.has_personal_data,
            needs_person_name_review=bool(anonymization.person_names),
            is_official=True,
            metadata_json={
                "telegram_source_identifiers": sorted(message.source_identifiers),
                "anonymization_status": "completed",
                "review_cases": [case.code for case in anonymization.review_cases],
            },
        )
        review_repo = ReviewRepository(self.session)
        for redaction in anonymization.redactions:
            review_repo.create_redaction_event(
                material_id=material.id,
                field_name="public_text",
                redaction_type=redaction.redaction_type,
                original_fragment=redaction.original_fragment,
                replacement=redaction.replacement,
                is_confirmed=not redaction.needs_review,
            )
        for person_name in anonymization.person_names:
            review_repo.create_person_name_review(
                material_id=material.id,
                detected_name=person_name.detected_name,
                context=person_name.context,
            )
            builder.person_name_reviews_count += 1
        for review_case in anonymization.review_cases:
            builder.add_review_case(
                index=message.index,
                message_id=message.message_id,
                code=review_case.code,
                description=review_case.description,
            )
        for person_name in anonymization.person_names:
            builder.add_review_case(
                index=message.index,
                message_id=message.message_id,
                code="person_name_review",
                description=f"ФИО отправлено на ручную проверку: {person_name.detected_name}.",
            )
        builder.official_materials_found += 1
        if status == MaterialStatus.NEEDS_REVIEW:
            builder.needs_review_count += 1
        else:
            builder.draft_count += 1
        if material_type == MaterialType.OFFICIAL_ANSWER:
            builder.official_answers_found += 1
        else:
            builder.official_posts_found += 1
        builder.materials.append(
            {
                "material_id": material.id,
                "telegram_message_id": message.message_id,
                "published_at": message.date.isoformat(),
                "material_type": material_type.value,
                "status": material.status.value,
                "topic": topic.slug,
                "category": category.slug if category else None,
                "redactions_applied": len(anonymization.redactions),
                "person_name_reviews": len(anonymization.person_names),
            }
        )

    def _material_exists(self, *, source_id: int, message_id: str) -> bool:
        return (
            self.session.scalar(
                select(Material.id).where(
                    Material.source_id == source_id,
                    Material.external_message_id == message_id,
                )
            )
            is not None
        )

    def _guess_category_slug(self, text: str) -> str:
        lowered = text.lower()
        markers = [
            ("heating", ["отоп", "батар", "тепл"]),
            ("water", ["вод", "гвс", "хвс", "канализац"]),
            ("entrance", ["подъезд", "лестниц", "лифт"]),
            ("yard", ["двор", "парков", "детск", "площадк"]),
            ("management_company", ["управляющ", "ук ", "жэк"]),
            ("bills", ["квитанц", "начисл", "платеж", "оплат"]),
            ("animals", ["собак", "кош", "животн", "отлов"]),
        ]
        for slug, words in markers:
            if any(word in lowered for word in words):
                return slug
        return "other"

    def _parse_source_kind(self, value: str) -> SourceKind:
        try:
            return SourceKind(value)
        except ValueError as exc:
            raise TelegramImportError("OFFICIAL_TELEGRAM_SOURCE_KIND has unsupported value") from exc

    def _safe_filename(self, filename: str) -> str:
        name = Path(filename or "telegram-export.json").name
        if not name.lower().endswith(".json"):
            raise TelegramImportError("Only Telegram JSON exports are supported")
        return re.sub(r"[^A-Za-z0-9_.-]+", "_", name)[:120] or "telegram-export.json"


def parse_telegram_export(content: bytes) -> dict[str, Any]:
    stripped = content.lstrip()
    if stripped.startswith(b"<"):
        raise TelegramImportError("HTML Telegram export is not supported")
    try:
        payload = json.loads(content.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TelegramImportError("Uploaded file is not a valid Telegram JSON export") from exc
    if not isinstance(payload, dict):
        raise TelegramImportError("Telegram JSON export must be an object")
    messages = payload.get("messages")
    if not isinstance(messages, list):
        raise TelegramImportError("Telegram JSON export must contain a messages list")
    return payload


def parse_telegram_message(
    raw_message: Any,
    *,
    index: int,
    export_identifiers: set[str] | None = None,
) -> TelegramMessage:
    if not isinstance(raw_message, dict):
        raise TelegramImportError("Message must be an object")
    message_type = raw_message.get("type")
    is_service = message_type is not None and message_type != "message"
    text = extract_text(raw_message.get("text"))
    identifiers: set[str] = set()
    for key in ("from_id", "from", "actor_id", "actor", "author", "via_bot", "sender_id", "peer_id"):
        value = raw_message.get(key)
        if isinstance(value, (str, int)):
            identifiers.add(str(value))
    if not identifiers:
        identifiers.update(export_identifiers or set())
    return TelegramMessage(
        index=index,
        message_id=_safe_message_id(raw_message),
        date=parse_telegram_datetime(raw_message.get("date")),
        text=text,
        source_identifiers=identifiers,
        is_service=is_service,
    )


def extract_export_identifiers(payload: dict[str, Any]) -> set[str]:
    identifiers: set[str] = set()
    for key in ("id", "name", "title", "username"):
        value = payload.get(key)
        if isinstance(value, (str, int)):
            identifiers.add(str(value))
    return identifiers


def extract_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        fragments: list[str] = []
        for item in value:
            if isinstance(item, str):
                fragments.append(item)
            elif isinstance(item, dict):
                fragments.append(extract_text_fragment(item.get("text")))
        return "".join(fragments).strip()
    return ""


def extract_text_fragment(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(extract_text_fragment(item.get("text") if isinstance(item, dict) else item) for item in value)
    return ""


def parse_telegram_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw_value = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw_value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def identifiers_match(candidates: set[str], expected: str) -> bool:
    expected_variants = _identifier_variants(expected)
    return any(_identifier_variants(candidate) & expected_variants for candidate in candidates)


def _identifier_variants(value: str) -> set[str]:
    normalized = str(value).strip().lower()
    if not normalized:
        return set()
    without_at = normalized[1:] if normalized.startswith("@") else normalized
    variants = {normalized, without_at}
    if without_at.startswith("channel"):
        variants.add(without_at.removeprefix("channel"))
    if without_at.startswith("user"):
        variants.add(without_at.removeprefix("user"))
    return {variant for variant in variants if variant}


def _safe_message_id(raw_message: Any) -> str | None:
    if isinstance(raw_message, dict):
        value = raw_message.get("id")
        if isinstance(value, (str, int)):
            return str(value)
    return None
