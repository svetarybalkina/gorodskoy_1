from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.enums import (
    ImportStatus,
    LinkReason,
    MaterialStatus,
    MaterialType,
    ProblemQueryAction,
    ProblemQueryChannel,
    ReviewStatus,
    SourceKind,
)
from app.db.models import (
    AdminNote,
    Category,
    ImportBatch,
    ImportReport,
    Material,
    MaterialLink,
    PersonNameReview,
    ProblemQuery,
    QuestionVariant,
    RedactionEvent,
    ResidentQuestion,
    Setting,
    Source,
    Topic,
)


class SourceRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        code: str,
        name: str,
        kind: SourceKind,
        external_id: str | None = None,
        url: str | None = None,
        is_official: bool = True,
    ) -> Source:
        source = Source(
            code=code,
            name=name,
            kind=kind,
            external_id=external_id,
            url=url,
            is_official=is_official,
        )
        self.session.add(source)
        self.session.flush()
        return source


class TaxonomyRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_topic_by_slug(self, slug: str) -> Topic | None:
        return self.session.scalar(select(Topic).where(Topic.slug == slug))

    def get_category(self, *, topic_id: int, slug: str) -> Category | None:
        return self.session.scalar(
            select(Category).where(Category.topic_id == topic_id, Category.slug == slug)
        )

    def create_topic(
        self, *, slug: str, name: str, is_public: bool = False, sort_order: int = 100
    ) -> Topic:
        topic = Topic(slug=slug, name=name, is_public=is_public, sort_order=sort_order)
        self.session.add(topic)
        self.session.flush()
        return topic

    def create_category(
        self,
        *,
        topic_id: int,
        slug: str,
        name: str,
        is_public: bool = True,
        is_confirmed: bool = True,
        sort_order: int = 100,
    ) -> Category:
        category = Category(
            topic_id=topic_id,
            slug=slug,
            name=name,
            is_public=is_public,
            is_confirmed=is_confirmed,
            sort_order=sort_order,
        )
        self.session.add(category)
        self.session.flush()
        return category


class MaterialRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        source_id: int,
        topic_id: int,
        material_type: MaterialType,
        published_at: datetime,
        original_text: str,
        public_text: str,
        category_id: int | None = None,
        status: MaterialStatus = MaterialStatus.DRAFT,
        external_message_id: str | None = None,
        source_url: str | None = None,
        import_batch_id: int | None = None,
        has_personal_data: bool = False,
        needs_person_name_review: bool = False,
        is_official: bool = True,
        metadata_json: dict[str, Any] | None = None,
    ) -> Material:
        material = Material(
            source_id=source_id,
            topic_id=topic_id,
            category_id=category_id,
            import_batch_id=import_batch_id,
            external_message_id=external_message_id,
            material_type=material_type,
            status=status,
            published_at=published_at,
            source_url=source_url,
            original_text=original_text,
            public_text=public_text,
            has_personal_data=has_personal_data,
            needs_person_name_review=needs_person_name_review,
            is_official=is_official,
            metadata_json=metadata_json,
        )
        self.session.add(material)
        self.session.flush()
        return material

    def list_public_active(self) -> list[Material]:
        return list(
            self.session.scalars(
                select(Material)
                .join(Material.topic)
                .where(
                    Material.status == MaterialStatus.ACTIVE,
                    Material.is_official.is_(True),
                    Topic.is_public.is_(True),
                )
                .order_by(Material.published_at.desc(), Material.id.desc())
            )
        )


class QuestionRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create_resident_question(
        self,
        *,
        anonymized_text: str,
        normalized_text: str | None = None,
        category_id: int | None = None,
        import_batch_id: int | None = None,
        external_message_id: str | None = None,
        source_channel: str | None = None,
    ) -> ResidentQuestion:
        question = ResidentQuestion(
            anonymized_text=anonymized_text,
            normalized_text=normalized_text,
            category_id=category_id,
            import_batch_id=import_batch_id,
            external_message_id=external_message_id,
            source_channel=source_channel,
        )
        self.session.add(question)
        self.session.flush()
        return question

    def link_to_material(
        self,
        *,
        question_id: int,
        material_id: int,
        reason: LinkReason = LinkReason.IMPORTED_PAIR,
        confidence: int | None = None,
    ) -> MaterialLink:
        link = MaterialLink(
            question_id=question_id,
            material_id=material_id,
            reason=reason,
            confidence=confidence,
        )
        self.session.add(link)
        self.session.flush()
        return link

    def create_variant(
        self,
        *,
        material_id: int,
        text: str,
        normalized_text: str | None = None,
        is_confirmed: bool = False,
        created_from_problem_query_id: int | None = None,
    ) -> QuestionVariant:
        variant = QuestionVariant(
            material_id=material_id,
            text=text,
            normalized_text=normalized_text,
            is_confirmed=is_confirmed,
            created_from_problem_query_id=created_from_problem_query_id,
        )
        self.session.add(variant)
        self.session.flush()
        return variant


class ImportRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create_batch(
        self,
        *,
        filename: str,
        source_file_path: str | None = None,
        anonymized_file_path: str | None = None,
        status: ImportStatus = ImportStatus.PENDING,
    ) -> ImportBatch:
        batch = ImportBatch(
            filename=filename,
            source_file_path=source_file_path,
            anonymized_file_path=anonymized_file_path,
            status=status,
        )
        self.session.add(batch)
        self.session.flush()
        return batch

    def create_report(
        self,
        *,
        import_batch_id: int,
        summary: dict[str, Any],
        errors: list[dict[str, Any]] | None = None,
        report_file_path: str | None = None,
    ) -> ImportReport:
        report = ImportReport(
            import_batch_id=import_batch_id,
            summary=summary,
            errors=errors or [],
            report_file_path=report_file_path,
        )
        self.session.add(report)
        self.session.flush()
        return report


class ReviewRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create_redaction_event(
        self,
        *,
        material_id: int,
        field_name: str,
        redaction_type: str,
        replacement: str,
        original_fragment: str | None = None,
        is_confirmed: bool = False,
    ) -> RedactionEvent:
        event = RedactionEvent(
            material_id=material_id,
            field_name=field_name,
            redaction_type=redaction_type,
            original_fragment=original_fragment,
            replacement=replacement,
            is_confirmed=is_confirmed,
        )
        self.session.add(event)
        self.session.flush()
        return event

    def create_person_name_review(
        self,
        *,
        material_id: int,
        detected_name: str,
        context: str | None = None,
        status: ReviewStatus = ReviewStatus.PENDING,
    ) -> PersonNameReview:
        review = PersonNameReview(
            material_id=material_id,
            detected_name=detected_name,
            context=context,
            status=status,
        )
        self.session.add(review)
        self.session.flush()
        return review


class ProblemQueryRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        anonymized_text: str,
        channel: ProblemQueryChannel,
        normalized_text: str | None = None,
        shown_material_id: int | None = None,
        category_id: int | None = None,
        similar_material_ids: list[int] | None = None,
        user_action: ProblemQueryAction = ProblemQueryAction.NO_ACTION,
        match_level: str | None = None,
        selection_reason: str | None = None,
    ) -> ProblemQuery:
        query = ProblemQuery(
            anonymized_text=anonymized_text,
            normalized_text=normalized_text,
            shown_material_id=shown_material_id,
            category_id=category_id,
            similar_material_ids=similar_material_ids or [],
            user_action=user_action,
            channel=channel,
            match_level=match_level,
            selection_reason=selection_reason,
        )
        self.session.add(query)
        self.session.flush()
        return query


class AdminNoteRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, *, material_id: int, body: str, author: str | None = None) -> AdminNote:
        note = AdminNote(material_id=material_id, body=body, author=author)
        self.session.add(note)
        self.session.flush()
        return note


class SettingRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, key: str) -> Setting | None:
        return self.session.scalar(select(Setting).where(Setting.key == key))

    def set(self, *, key: str, value: str, description: str | None = None) -> Setting:
        setting = self.get(key)
        if setting is None:
            setting = Setting(key=key, value=value, description=description)
            self.session.add(setting)
        else:
            setting.value = value
            if description is not None:
                setting.description = description
        self.session.flush()
        return setting
