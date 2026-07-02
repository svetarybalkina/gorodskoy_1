from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.enums import MaterialStatus, ReviewStatus
from app.db.models import Material, PersonNameReview, RedactionEvent
from app.db.repositories import ReviewRepository
from app.search import SearchService


class PersonNameReviewService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def approve_public(self, review_id: int, *, note: str | None = None) -> PersonNameReview | None:
        review = ReviewRepository(self.session).get_person_name_review(review_id)
        if review is None:
            return None
        review.status = ReviewStatus.APPROVED_PUBLIC
        review.decision_note = note or "ФИО подтверждено как официальный сотрудник."
        review.reviewed_at = datetime.utcnow()
        self._sync_material_review_flag(review.material_id)
        return review

    def redact_name(self, review_id: int, *, note: str | None = None) -> PersonNameReview | None:
        review = ReviewRepository(self.session).get_person_name_review(review_id)
        if review is None:
            return None
        material = review.material
        if review.detected_name in material.public_text:
            material.public_text = material.public_text.replace(review.detected_name, "[ФИО скрыто]")
        existing_event = self.session.scalar(
            select(RedactionEvent).where(
                RedactionEvent.material_id == material.id,
                RedactionEvent.redaction_type == "person_name",
                RedactionEvent.original_fragment == review.detected_name,
            )
        )
        if existing_event is None:
            self.session.add(
                RedactionEvent(
                    material_id=material.id,
                    field_name="public_text",
                    redaction_type="person_name",
                    original_fragment=review.detected_name,
                    replacement="[ФИО скрыто]",
                    is_confirmed=True,
                )
            )
        else:
            existing_event.is_confirmed = True
            existing_event.replacement = "[ФИО скрыто]"
        review.status = ReviewStatus.REDACTED
        review.decision_note = note or "ФИО скрыто в публичной версии."
        review.reviewed_at = datetime.utcnow()
        self._sync_material_review_flag(material.id)
        SearchService(self.session).reindex_material(material.id)
        return review

    def hide_material(self, review_id: int, *, note: str | None = None) -> PersonNameReview | None:
        review = ReviewRepository(self.session).get_person_name_review(review_id)
        if review is None:
            return None
        review.status = ReviewStatus.HIDE_MATERIAL
        review.decision_note = note or "Карточка скрыта после проверки ФИО."
        review.reviewed_at = datetime.utcnow()
        review.material.status = MaterialStatus.HIDDEN
        self._sync_material_review_flag(review.material_id)
        SearchService(self.session).reindex_material(review.material_id)
        return review

    def _sync_material_review_flag(self, material_id: int) -> None:
        self.session.flush()
        pending_exists = self.session.scalar(
            select(PersonNameReview.id).where(
                PersonNameReview.material_id == material_id,
                PersonNameReview.status == ReviewStatus.PENDING,
            )
        )
        material = self.session.get(Material, material_id)
        if material is not None:
            material.needs_person_name_review = pending_exists is not None
        self.session.flush()
