from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.db.enums import MaterialStatus
from app.db.models import (
    AdminNote,
    DictionaryCandidate,
    Material,
    MaterialLink,
    MaterialRecommendation,
    PersonNameReview,
    ProblemQuery,
    QuestionVariant,
    RedactionEvent,
    Source,
)

SAFE_IMPORTED_STATUSES = (
    MaterialStatus.DRAFT,
    MaterialStatus.NEEDS_REVIEW,
    MaterialStatus.DUPLICATE,
)


@dataclass(frozen=True)
class ImportCleanupPreview:
    materials: int
    admin_notes: int
    question_variants: int
    material_links: int
    redaction_events: int
    person_name_reviews: int
    dictionary_candidates: int
    material_recommendations: int
    problem_queries_to_unlink: int


def preview_test_import_cleanup(
    session: Session,
    *,
    source_external_id: str | None = None,
    all_sources: bool = False,
) -> ImportCleanupPreview:
    material_ids = _target_material_ids(
        session,
        source_external_id=source_external_id,
        all_sources=all_sources,
    )
    return _preview_for_material_ids(session, material_ids)


def cleanup_test_import_materials(
    session: Session,
    *,
    source_external_id: str | None = None,
    all_sources: bool = False,
) -> ImportCleanupPreview:
    material_ids = _target_material_ids(
        session,
        source_external_id=source_external_id,
        all_sources=all_sources,
    )
    preview = _preview_for_material_ids(session, material_ids)
    if not material_ids:
        return preview

    session.execute(
        update(ProblemQuery)
        .where(ProblemQuery.shown_material_id.in_(material_ids))
        .values(shown_material_id=None)
    )
    session.execute(update(Material).where(Material.duplicate_of_id.in_(material_ids)).values(duplicate_of_id=None))
    session.execute(delete(AdminNote).where(AdminNote.material_id.in_(material_ids)))
    session.execute(delete(QuestionVariant).where(QuestionVariant.material_id.in_(material_ids)))
    session.execute(delete(MaterialLink).where(MaterialLink.material_id.in_(material_ids)))
    session.execute(delete(RedactionEvent).where(RedactionEvent.material_id.in_(material_ids)))
    session.execute(delete(PersonNameReview).where(PersonNameReview.material_id.in_(material_ids)))
    session.execute(delete(DictionaryCandidate).where(DictionaryCandidate.material_id.in_(material_ids)))
    session.execute(delete(MaterialRecommendation).where(MaterialRecommendation.material_id.in_(material_ids)))
    session.execute(delete(Material).where(Material.id.in_(material_ids)))
    session.flush()
    return preview


def _target_material_ids(
    session: Session,
    *,
    source_external_id: str | None,
    all_sources: bool,
) -> list[int]:
    if not all_sources and not (source_external_id or "").strip():
        raise ValueError("source_external_id is required unless all_sources=True")

    statement = (
        select(Material.id)
        .join(Material.source)
        .where(
            Material.import_batch_id.is_not(None),
            Material.status.in_(SAFE_IMPORTED_STATUSES),
        )
        .order_by(Material.id)
    )
    if not all_sources:
        statement = statement.where(Source.external_id == source_external_id.strip())
    return list(session.scalars(statement))


def _preview_for_material_ids(session: Session, material_ids: list[int]) -> ImportCleanupPreview:
    if not material_ids:
        return ImportCleanupPreview(
            materials=0,
            admin_notes=0,
            question_variants=0,
            material_links=0,
            redaction_events=0,
            person_name_reviews=0,
            dictionary_candidates=0,
            material_recommendations=0,
            problem_queries_to_unlink=0,
        )
    return ImportCleanupPreview(
        materials=len(material_ids),
        admin_notes=_count(session, AdminNote.material_id, material_ids),
        question_variants=_count(session, QuestionVariant.material_id, material_ids),
        material_links=_count(session, MaterialLink.material_id, material_ids),
        redaction_events=_count(session, RedactionEvent.material_id, material_ids),
        person_name_reviews=_count(session, PersonNameReview.material_id, material_ids),
        dictionary_candidates=_count(session, DictionaryCandidate.material_id, material_ids),
        material_recommendations=_count(session, MaterialRecommendation.material_id, material_ids),
        problem_queries_to_unlink=_count(session, ProblemQuery.shown_material_id, material_ids),
    )


def _count(session: Session, column, material_ids: list[int]) -> int:
    return int(session.scalar(select(func.count()).where(column.in_(material_ids))) or 0)
