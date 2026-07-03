from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.enums import (
    DictionaryCandidateSource,
    DictionaryCandidateStatus,
    DictionaryCandidateType,
    ImportStatus,
    LinkReason,
    MaterialStatus,
    MaterialType,
    ProblemQueryAction,
    ProblemQueryChannel,
    RecommendationType,
    ReviewStatus,
    SourceKind,
)


def enum_column(enum_type: type, *, name: str) -> SqlEnum:
    return SqlEnum(
        enum_type,
        name=name,
        values_callable=lambda enum_cls: [item.value for item in enum_cls],
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class Source(TimestampMixin, Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[SourceKind] = mapped_column(
        enum_column(SourceKind, name="source_kind"), nullable=False
    )
    external_id: Mapped[str | None] = mapped_column(String(255))
    url: Mapped[str | None] = mapped_column(String(500))
    is_official: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    materials: Mapped[list[Material]] = relationship(back_populates="source")


class Topic(TimestampMixin, Base):
    __tablename__ = "topics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_public: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=100, nullable=False)

    categories: Mapped[list[Category]] = relationship(back_populates="topic")
    materials: Mapped[list[Material]] = relationship(back_populates="topic")


class Category(TimestampMixin, Base):
    __tablename__ = "categories"
    __table_args__ = (
        UniqueConstraint("topic_id", "slug", name="uq_categories_topic_id_slug"),
        Index("ix_categories_topic_id_is_public", "topic_id", "is_public"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    topic_id: Mapped[int] = mapped_column(ForeignKey("topics.id"), nullable=False)
    slug: Mapped[str] = mapped_column(String(120), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_public: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_confirmed: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=100, nullable=False)

    topic: Mapped[Topic] = relationship(back_populates="categories")
    materials: Mapped[list[Material]] = relationship(back_populates="category")
    resident_questions: Mapped[list[ResidentQuestion]] = relationship(back_populates="category")
    problem_queries: Mapped[list[ProblemQuery]] = relationship(back_populates="category")
    dictionary_candidates: Mapped[list[DictionaryCandidate]] = relationship(back_populates="category")


class ImportBatch(TimestampMixin, Base):
    __tablename__ = "import_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    source_file_path: Mapped[str | None] = mapped_column(String(500))
    anonymized_file_path: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[ImportStatus] = mapped_column(
        enum_column(ImportStatus, name="import_status"),
        default=ImportStatus.PENDING,
        nullable=False,
    )
    total_messages: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    processed_messages: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    materials: Mapped[list[Material]] = relationship(back_populates="import_batch")
    reports: Mapped[list[ImportReport]] = relationship(back_populates="import_batch")


class Material(TimestampMixin, Base):
    __tablename__ = "materials"
    __table_args__ = (
        UniqueConstraint("source_id", "external_message_id", name="uq_materials_source_id_external_message_id"),
        Index("ix_materials_status_topic_category", "status", "topic_id", "category_id"),
        Index("ix_materials_source_id", "source_id"),
        Index("ix_materials_published_at", "published_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), nullable=False)
    topic_id: Mapped[int] = mapped_column(ForeignKey("topics.id"), nullable=False)
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"))
    import_batch_id: Mapped[int | None] = mapped_column(ForeignKey("import_batches.id"))
    external_message_id: Mapped[str | None] = mapped_column(String(255))
    material_type: Mapped[MaterialType] = mapped_column(
        enum_column(MaterialType, name="material_type"), nullable=False
    )
    status: Mapped[MaterialStatus] = mapped_column(
        enum_column(MaterialStatus, name="material_status"),
        default=MaterialStatus.DRAFT,
        nullable=False,
    )
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(500))
    original_text: Mapped[str] = mapped_column(Text, nullable=False)
    public_text: Mapped[str] = mapped_column(Text, nullable=False)
    has_personal_data: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    needs_person_name_review: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_official: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    duplicate_of_id: Mapped[int | None] = mapped_column(ForeignKey("materials.id"))
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    source: Mapped[Source] = relationship(back_populates="materials")
    topic: Mapped[Topic] = relationship(back_populates="materials")
    category: Mapped[Category | None] = relationship(back_populates="materials")
    import_batch: Mapped[ImportBatch | None] = relationship(back_populates="materials")
    duplicate_of: Mapped[Material | None] = relationship(remote_side=[id])
    question_links: Mapped[list[MaterialLink]] = relationship(back_populates="material")
    variants: Mapped[list[QuestionVariant]] = relationship(back_populates="material")
    redaction_events: Mapped[list[RedactionEvent]] = relationship(back_populates="material")
    person_name_reviews: Mapped[list[PersonNameReview]] = relationship(back_populates="material")
    admin_notes: Mapped[list[AdminNote]] = relationship(back_populates="material")
    dictionary_candidates: Mapped[list[DictionaryCandidate]] = relationship(back_populates="material")
    recommendations: Mapped[list[MaterialRecommendation]] = relationship(back_populates="material")


class MaterialRecommendation(TimestampMixin, Base):
    __tablename__ = "material_recommendations"
    __table_args__ = (
        Index("ix_material_recommendations_material_id", "material_id"),
        Index("ix_material_recommendations_type_confidence", "recommendation_type", "confidence"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id"), nullable=False)
    recommendation_type: Mapped[RecommendationType] = mapped_column(
        enum_column(RecommendationType, name="recommendation_type"), nullable=False
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_text: Mapped[str] = mapped_column(Text, nullable=False)
    source_fragment: Mapped[str] = mapped_column(Text, nullable=False)
    action_kind: Mapped[str] = mapped_column(String(80), default="general_action", nullable=False)
    confidence: Mapped[int] = mapped_column(Integer, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=100, nullable=False)

    material: Mapped[Material] = relationship(back_populates="recommendations")


class ResidentQuestion(TimestampMixin, Base):
    __tablename__ = "resident_questions"
    __table_args__ = (
        Index("ix_resident_questions_category_id", "category_id"),
        Index("ix_resident_questions_import_batch_id", "import_batch_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    import_batch_id: Mapped[int | None] = mapped_column(ForeignKey("import_batches.id"))
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"))
    external_message_id: Mapped[str | None] = mapped_column(String(255))
    anonymized_text: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_text: Mapped[str | None] = mapped_column(Text)
    source_channel: Mapped[str | None] = mapped_column(String(120))

    category: Mapped[Category | None] = relationship(back_populates="resident_questions")
    links: Mapped[list[MaterialLink]] = relationship(back_populates="question")


class QuestionVariant(TimestampMixin, Base):
    __tablename__ = "question_variants"
    __table_args__ = (
        Index("ix_question_variants_material_id_is_confirmed", "material_id", "is_confirmed"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id"), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_text: Mapped[str | None] = mapped_column(Text)
    is_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_from_problem_query_id: Mapped[int | None] = mapped_column(ForeignKey("problem_queries.id"))

    material: Mapped[Material] = relationship(back_populates="variants")


class MaterialLink(TimestampMixin, Base):
    __tablename__ = "material_links"
    __table_args__ = (
        UniqueConstraint("question_id", "material_id", name="uq_material_links_question_id_material_id"),
        Index("ix_material_links_material_id", "material_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("resident_questions.id"), nullable=False)
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id"), nullable=False)
    reason: Mapped[LinkReason] = mapped_column(
        enum_column(LinkReason, name="link_reason"),
        default=LinkReason.IMPORTED_PAIR,
        nullable=False,
    )
    confidence: Mapped[int | None] = mapped_column(Integer)

    question: Mapped[ResidentQuestion] = relationship(back_populates="links")
    material: Mapped[Material] = relationship(back_populates="question_links")


class ImportReport(TimestampMixin, Base):
    __tablename__ = "import_reports"
    __table_args__ = (Index("ix_import_reports_import_batch_id", "import_batch_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    import_batch_id: Mapped[int] = mapped_column(ForeignKey("import_batches.id"), nullable=False)
    report_file_path: Mapped[str | None] = mapped_column(String(500))
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    errors: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)

    import_batch: Mapped[ImportBatch] = relationship(back_populates="reports")


class RedactionEvent(TimestampMixin, Base):
    __tablename__ = "redaction_events"
    __table_args__ = (Index("ix_redaction_events_material_id", "material_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id"), nullable=False)
    field_name: Mapped[str] = mapped_column(String(120), nullable=False)
    redaction_type: Mapped[str] = mapped_column(String(120), nullable=False)
    original_fragment: Mapped[str | None] = mapped_column(Text)
    replacement: Mapped[str] = mapped_column(String(255), nullable=False)
    is_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    material: Mapped[Material] = relationship(back_populates="redaction_events")


class PersonNameReview(TimestampMixin, Base):
    __tablename__ = "person_name_reviews"
    __table_args__ = (Index("ix_person_name_reviews_status", "status"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id"), nullable=False)
    detected_name: Mapped[str] = mapped_column(String(255), nullable=False)
    context: Mapped[str | None] = mapped_column(Text)
    status: Mapped[ReviewStatus] = mapped_column(
        enum_column(ReviewStatus, name="review_status"),
        default=ReviewStatus.PENDING,
        nullable=False,
    )
    decision_note: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    material: Mapped[Material] = relationship(back_populates="person_name_reviews")


class ProblemQuery(TimestampMixin, Base):
    __tablename__ = "problem_queries"
    __table_args__ = (
        Index("ix_problem_queries_category_id", "category_id"),
        Index("ix_problem_queries_shown_material_id", "shown_material_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    anonymized_text: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_text: Mapped[str | None] = mapped_column(Text)
    shown_material_id: Mapped[int | None] = mapped_column(ForeignKey("materials.id"))
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"))
    similar_material_ids: Mapped[list[int]] = mapped_column(JSON, default=list, nullable=False)
    user_action: Mapped[ProblemQueryAction] = mapped_column(
        enum_column(ProblemQueryAction, name="problem_query_action"),
        default=ProblemQueryAction.NO_ACTION,
        nullable=False,
    )
    channel: Mapped[ProblemQueryChannel] = mapped_column(
        enum_column(ProblemQueryChannel, name="problem_query_channel"),
        nullable=False,
    )
    match_level: Mapped[str | None] = mapped_column(String(120))
    selection_reason: Mapped[str | None] = mapped_column(Text)
    is_resolved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    shown_material: Mapped[Material | None] = relationship()
    category: Mapped[Category | None] = relationship(back_populates="problem_queries")


class DictionaryCandidate(TimestampMixin, Base):
    __tablename__ = "dictionary_candidates"
    __table_args__ = (
        UniqueConstraint(
            "normalized_text",
            "candidate_type",
            "category_id",
            "material_id",
            name="uq_dictionary_candidates_normalized_type_category_material",
        ),
        Index("ix_dictionary_candidates_status", "status"),
        Index("ix_dictionary_candidates_category_id", "category_id"),
        Index("ix_dictionary_candidates_material_id", "material_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_text: Mapped[str] = mapped_column(Text, nullable=False)
    candidate_type: Mapped[DictionaryCandidateType] = mapped_column(
        enum_column(DictionaryCandidateType, name="dictionary_candidate_type"),
        nullable=False,
    )
    source: Mapped[DictionaryCandidateSource] = mapped_column(
        enum_column(DictionaryCandidateSource, name="dictionary_candidate_source"),
        nullable=False,
    )
    status: Mapped[DictionaryCandidateStatus] = mapped_column(
        enum_column(DictionaryCandidateStatus, name="dictionary_candidate_status"),
        default=DictionaryCandidateStatus.PENDING,
        nullable=False,
    )
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"))
    material_id: Mapped[int | None] = mapped_column(ForeignKey("materials.id"))
    problem_query_id: Mapped[int | None] = mapped_column(ForeignKey("problem_queries.id"))
    occurrences: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    decision_note: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    category: Mapped[Category | None] = relationship(back_populates="dictionary_candidates")
    material: Mapped[Material | None] = relationship(back_populates="dictionary_candidates")
    problem_query: Mapped[ProblemQuery | None] = relationship()


class AdminNote(TimestampMixin, Base):
    __tablename__ = "admin_notes"
    __table_args__ = (Index("ix_admin_notes_material_id", "material_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id"), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    author: Mapped[str | None] = mapped_column(String(120))

    material: Mapped[Material] = relationship(back_populates="admin_notes")


class Setting(TimestampMixin, Base):
    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
