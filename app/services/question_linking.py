from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.enums import LinkReason
from app.db.models import ImportBatch, Material, MaterialLink, ResidentQuestion
from app.db.repositories import QuestionRepository, TaxonomyRepository
from app.importers.telegram_json import (
    extract_export_identifiers,
    parse_telegram_message,
)
from app.search.normalization import normalize_text
from app.services.anonymization import anonymize_text
from app.services.classification import HOUSING_TOPIC, classify_material_text


@dataclass(frozen=True)
class QuestionLinkRebuildResult:
    scanned: int
    questions_existing: int
    questions_created: int
    links_existing: int
    links_created: int


class QuestionLinkRebuildService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def rebuild_for_batch(
        self,
        *,
        batch_id: int,
        export_path: Path | None = None,
        execute: bool = False,
    ) -> QuestionLinkRebuildResult:
        batch = self.session.get(ImportBatch, batch_id)
        if batch is None:
            raise ValueError(f"Import batch {batch_id} not found")
        path = export_path or Path(batch.source_file_path or "")
        if not path.exists():
            raise ValueError(f"Telegram JSON export not found: {path}")

        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        messages = payload.get("messages")
        if not isinstance(messages, list):
            raise ValueError("Telegram JSON export must contain a messages list")
        export_identifiers = extract_export_identifiers(payload)
        material_by_external_id = {
            str(external_id): material
            for external_id, material in self.session.execute(
                select(Material.external_message_id, Material).where(
                    Material.import_batch_id == batch_id,
                    Material.external_message_id.is_not(None),
                )
            )
            if external_id is not None
        }
        existing_questions = {
            str(external_id): question
            for external_id, question in self.session.execute(
                select(ResidentQuestion.external_message_id, ResidentQuestion).where(
                    ResidentQuestion.import_batch_id == batch_id,
                    ResidentQuestion.external_message_id.is_not(None),
                )
            )
            if external_id is not None
        }
        existing_links = {
            (question_id, material_id)
            for question_id, material_id in self.session.execute(
                select(MaterialLink.question_id, MaterialLink.material_id).join(Material)
                .where(Material.import_batch_id == batch_id)
            )
        }

        scanned = 0
        questions_existing = 0
        questions_created = 0
        links_existing = 0
        links_created = 0
        questions_by_message_id: dict[str, ResidentQuestion] = {}
        pending_links: dict[str, list[int]] = {}

        for index, raw_message in enumerate(messages):
            parsed = parse_telegram_message(raw_message, index=index, export_identifiers=export_identifiers)
            if parsed.is_service or not parsed.text or not parsed.message_id:
                continue
            scanned += 1
            material = material_by_external_id.get(parsed.message_id)
            if material is not None:
                if parsed.reply_to_message_id:
                    question = questions_by_message_id.get(parsed.reply_to_message_id) or existing_questions.get(
                        parsed.reply_to_message_id
                    )
                    if question is None:
                        pending_links.setdefault(parsed.reply_to_message_id, []).append(material.id)
                    else:
                        created = self._link(
                            question.id,
                            material.id,
                            existing_links=existing_links,
                            execute=execute,
                        )
                        links_created += int(created)
                        links_existing += int(not created)
                continue

            question = existing_questions.get(parsed.message_id)
            if question is None:
                questions_created += 1
                if execute:
                    question = self._create_question(parsed, batch_id=batch_id)
                    existing_questions[parsed.message_id] = question
            else:
                questions_existing += 1
            if question is not None:
                questions_by_message_id[parsed.message_id] = question
                for material_id in pending_links.pop(parsed.message_id, []):
                    created = self._link(
                        question.id,
                        material_id,
                        existing_links=existing_links,
                        execute=execute,
                    )
                    links_created += int(created)
                    links_existing += int(not created)

        return QuestionLinkRebuildResult(
            scanned=scanned,
            questions_existing=questions_existing,
            questions_created=questions_created,
            links_existing=links_existing,
            links_created=links_created,
        )

    def _create_question(self, parsed, *, batch_id: int) -> ResidentQuestion:
        anonymization = anonymize_text(parsed.text)
        taxonomy = TaxonomyRepository(self.session)
        classification = classify_material_text(parsed.text)
        category = None
        if classification.topic_slug == HOUSING_TOPIC and classification.category_slug is not None:
            topic = taxonomy.get_topic_by_slug(HOUSING_TOPIC)
            if topic is not None:
                category = taxonomy.get_category(topic_id=topic.id, slug=classification.category_slug)
        return QuestionRepository(self.session).create_resident_question(
            anonymized_text=anonymization.text,
            normalized_text=normalize_text(anonymization.text),
            category_id=category.id if category is not None else None,
            import_batch_id=batch_id,
            external_message_id=parsed.message_id,
            source_channel="telegram",
        )

    def _link(
        self,
        question_id: int,
        material_id: int,
        *,
        existing_links: set[tuple[int, int]],
        execute: bool,
    ) -> bool:
        link_key = (question_id, material_id)
        if link_key in existing_links:
            return False
        if execute:
            QuestionRepository(self.session).link_to_material(
                question_id=question_id,
                material_id=material_id,
                reason=LinkReason.IMPORTED_PAIR,
                confidence=100,
            )
        existing_links.add(link_key)
        return True
