from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.enums import MaterialStatus
from app.db.models import Material, PersonNameReview, RedactionEvent
from app.db.repositories import ReviewRepository
from app.search import SearchService
from app.services.anonymization import anonymize_text
from app.services.recommendations import RecommendationExtractionService


@dataclass(frozen=True)
class ReprocessResult:
    processed: int
    needs_review: int
    redactions: int
    person_name_reviews: int


class MaterialReprocessingService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def reprocess_material(self, material_id: int, *, reindex: bool = True) -> ReprocessResult | None:
        material = self.session.get(Material, material_id)
        if material is None:
            return None
        result = self._reprocess(material, reindex=reindex)
        self.session.flush()
        return result

    def reprocess_import_batch(self, batch_id: int) -> ReprocessResult:
        totals = ReprocessResult(processed=0, needs_review=0, redactions=0, person_name_reviews=0)
        materials = self.session.scalars(
            select(Material).where(Material.import_batch_id == batch_id).order_by(Material.id)
        )
        for material in materials:
            item = self._reprocess(material)
            totals = ReprocessResult(
                processed=totals.processed + item.processed,
                needs_review=totals.needs_review + item.needs_review,
                redactions=totals.redactions + item.redactions,
                person_name_reviews=totals.person_name_reviews + item.person_name_reviews,
            )
        self.session.flush()
        return totals

    def _reprocess(self, material: Material, *, reindex: bool = True) -> ReprocessResult:
        anonymized = anonymize_text(material.original_text)
        self.session.execute(delete(RedactionEvent).where(RedactionEvent.material_id == material.id))
        self.session.execute(delete(PersonNameReview).where(PersonNameReview.material_id == material.id))
        material.public_text = anonymized.text
        material.has_personal_data = anonymized.has_personal_data
        material.needs_person_name_review = bool(anonymized.person_names)
        if anonymized.needs_review:
            material.status = MaterialStatus.NEEDS_REVIEW
        elif material.status == MaterialStatus.NEEDS_REVIEW:
            material.status = MaterialStatus.DRAFT

        review_repo = ReviewRepository(self.session)
        for redaction in anonymized.redactions:
            review_repo.create_redaction_event(
                material_id=material.id,
                field_name="public_text",
                redaction_type=redaction.redaction_type,
                original_fragment=redaction.original_fragment,
                replacement=redaction.replacement,
                is_confirmed=not redaction.needs_review,
            )
        for person_name in anonymized.person_names:
            review_repo.create_person_name_review(
                material_id=material.id,
                detected_name=person_name.detected_name,
                context=person_name.context,
            )
        if reindex:
            SearchService(self.session).reindex_material(material.id)
        else:
            RecommendationExtractionService(self.session).refresh_material(material.id)
        return ReprocessResult(
            processed=1,
            needs_review=1 if anonymized.needs_review else 0,
            redactions=len(anonymized.redactions),
            person_name_reviews=len(anonymized.person_names),
        )
