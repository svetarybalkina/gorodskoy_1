from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

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

DETAILED_ANIMAL_CATEGORY_SLUGS = {
    "stray_dogs",
    "animal_capture",
    "aggressive_animals",
    "shelters",
    "pet_rules",
}


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

    def get_by_code(self, code: str) -> Source | None:
        return self.session.scalar(select(Source).where(Source.code == code))

    def get_or_update_official_telegram_source(
        self,
        *,
        source_id: str,
        name: str,
        kind: SourceKind,
        url: str | None = None,
    ) -> Source:
        code = f"telegram:{source_id.strip().lower()}"
        source = self.get_by_code(code)
        if source is None:
            source = Source(
                code=code,
                name=name,
                kind=kind,
                external_id=source_id,
                url=url,
                is_official=True,
                is_active=True,
            )
            self.session.add(source)
        else:
            source.name = name
            source.kind = kind
            source.external_id = source_id
            source.url = url
            source.is_official = True
            source.is_active = True
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

    def list_public_topics(self) -> list[Topic]:
        return list(
            self.session.scalars(
                select(Topic)
                .where(Topic.is_public.is_(True))
                .options(selectinload(Topic.categories))
                .order_by(Topic.sort_order, Topic.name)
            )
        )

    def list_public_categories(self) -> list[Category]:
        return [
            category
            for category in self.session.scalars(
                self._public_categories_statement().order_by(
                    Topic.sort_order, Category.sort_order, Category.name
                )
            )
            if category.slug not in DETAILED_ANIMAL_CATEGORY_SLUGS
        ]

    def list_public_animal_category_ids(self) -> list[int]:
        categories = list(
            self.session.scalars(
                select(Category)
                .join(Category.topic)
                .where(
                    Topic.slug == "housing",
                    Category.slug.in_({"animals", *DETAILED_ANIMAL_CATEGORY_SLUGS}),
                )
            )
        )
        return [category.id for category in categories]

    def list_admin_topics(self) -> list[Topic]:
        return list(
            self.session.scalars(
                select(Topic)
                .options(selectinload(Topic.categories))
                .order_by(Topic.sort_order, Topic.name)
            )
        )

    def list_admin_categories(self, *, topic_id: int | None = None) -> list[Category]:
        statement = (
            select(Category)
            .join(Category.topic)
            .options(selectinload(Category.topic))
            .order_by(Topic.sort_order, Category.sort_order, Category.name)
        )
        if topic_id is not None:
            statement = statement.where(Category.topic_id == topic_id)
        return list(self.session.scalars(statement))

    def get_public_category_by_id(self, category_id: int) -> Category | None:
        return self.session.scalar(
            self._public_categories_statement()
            .where(Category.id == category_id)
            .where(Category.slug.not_in(DETAILED_ANIMAL_CATEGORY_SLUGS))
        )

    def _public_categories_statement(self):
        return (
            select(Category)
            .join(Category.topic)
            .where(
                Topic.is_public.is_(True),
                Category.is_public.is_(True),
                Category.is_confirmed.is_(True),
            )
            .options(selectinload(Category.topic))
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

    def list_admin(
        self,
        *,
        status: MaterialStatus | None = None,
        category_id: int | None = None,
        topic_id: int | None = None,
        limit: int = 100,
    ) -> list[Material]:
        statement = (
            select(Material)
            .options(
                selectinload(Material.source),
                selectinload(Material.topic),
                selectinload(Material.category).selectinload(Category.topic),
            )
            .order_by(Material.published_at.desc(), Material.id.desc())
            .limit(limit)
        )
        if status is not None:
            statement = statement.where(Material.status == status)
        if category_id is not None:
            statement = statement.where(Material.category_id == category_id)
        if topic_id is not None:
            statement = statement.where(Material.topic_id == topic_id)
        return list(self.session.scalars(statement))

    def get_admin_material(self, material_id: int) -> Material | None:
        return self.session.scalar(
            select(Material)
            .where(Material.id == material_id)
            .options(
                selectinload(Material.source),
                selectinload(Material.topic),
                selectinload(Material.category).selectinload(Category.topic),
                selectinload(Material.admin_notes),
            )
        )

    def update_status(self, material_id: int, status: MaterialStatus) -> Material | None:
        material = self.get_admin_material(material_id)
        if material is None:
            return None
        material.status = status
        self.session.flush()
        return material

    def update_category(self, material_id: int, category_id: int | None) -> Material | None:
        material = self.get_admin_material(material_id)
        if material is None:
            return None
        if category_id is None:
            material.category_id = None
        else:
            category = self.session.get(Category, category_id)
            if category is None or category.topic_id != material.topic_id:
                raise ValueError("Category must belong to the material topic")
            material.category_id = category.id
        self.session.flush()
        return material

    def get_public_material(self, material_id: int) -> Material | None:
        return self.session.scalar(
            select(Material)
            .join(Material.topic)
            .where(
                Material.id == material_id,
                Material.status == MaterialStatus.ACTIVE,
                Material.is_official.is_(True),
                Topic.is_public.is_(True),
            )
            .options(
                selectinload(Material.source),
                selectinload(Material.topic),
                selectinload(Material.category).selectinload(Category.topic),
            )
        )

    def search_public(
        self,
        *,
        query: str | None = None,
        category_id: int | None = None,
        limit: int = 20,
    ) -> list[Material]:
        statement = (
            select(Material)
            .join(Material.topic)
            .outerjoin(Material.category)
            .where(
                Material.status == MaterialStatus.ACTIVE,
                Material.is_official.is_(True),
                Topic.is_public.is_(True),
            )
            .options(
                selectinload(Material.source),
                selectinload(Material.topic),
                selectinload(Material.category).selectinload(Category.topic),
            )
            .order_by(Material.published_at.desc(), Material.id.desc())
            .limit(limit)
        )
        if category_id is not None:
            category = self.session.get(Category, category_id)
            if category is not None and category.slug == "animals":
                category_ids = TaxonomyRepository(self.session).list_public_animal_category_ids()
                statement = statement.where(Material.category_id.in_(category_ids))
            else:
                statement = statement.where(Material.category_id == category_id)

        normalized_query = (query or "").strip().lower()
        if normalized_query:
            patterns = {
                f"%{normalized_query}%",
                f"%{normalized_query.capitalize()}%",
                f"%{normalized_query.upper()}%",
                f"%{(query or '').strip()}%",
            }
            statement = statement.where(
                or_(
                    *[
                        condition
                        for pattern in patterns
                        for condition in (
                            Material.public_text.ilike(pattern),
                            Topic.name.ilike(pattern),
                            Category.name.ilike(pattern),
                            Category.slug.ilike(pattern),
                        )
                    ]
                )
            )

        return list(self.session.scalars(statement))

    def list_similar_public(self, material: Material, *, limit: int = 3) -> list[Material]:
        statement = (
            select(Material)
            .join(Material.topic)
            .where(
                Material.id != material.id,
                Material.status == MaterialStatus.ACTIVE,
                Material.is_official.is_(True),
                Topic.is_public.is_(True),
            )
            .options(
                selectinload(Material.source),
                selectinload(Material.topic),
                selectinload(Material.category),
            )
            .order_by(Material.published_at.desc(), Material.id.desc())
            .limit(limit)
        )
        if material.category_id is not None:
            statement = statement.where(Material.category_id == material.category_id)
        else:
            statement = statement.where(Material.topic_id == material.topic_id)

        return list(self.session.scalars(statement))


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

    def list_batches(self, *, limit: int = 50) -> list[ImportBatch]:
        return list(
            self.session.scalars(
                select(ImportBatch).order_by(ImportBatch.created_at.desc(), ImportBatch.id.desc()).limit(limit)
            )
        )

    def get_batch(self, batch_id: int) -> ImportBatch | None:
        return self.session.scalar(
            select(ImportBatch)
            .where(ImportBatch.id == batch_id)
            .options(selectinload(ImportBatch.reports))
        )

    def get_report_for_batch(self, batch_id: int) -> ImportReport | None:
        return self.session.scalar(
            select(ImportReport)
            .where(ImportReport.import_batch_id == batch_id)
            .order_by(ImportReport.created_at.desc(), ImportReport.id.desc())
        )

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
