from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.db.enums import MaterialStatus, ReviewStatus
from app.db.models import Material
from app.search import SearchService
from app.services.anonymization import has_unredacted_salutation_addressee


@dataclass(frozen=True)
class BulkPublicationPreview:
    eligible: int
    blocked_needs_review: int
    blocked_manual_drafts: int
    blocked_non_public_topic: int
    blocked_person_name_review: int
    blocked_salutation: int
    by_category: list[tuple[str, int]]

    @property
    def blocked_total(self) -> int:
        return (
            self.blocked_needs_review
            + self.blocked_manual_drafts
            + self.blocked_non_public_topic
            + self.blocked_person_name_review
            + self.blocked_salutation
        )


@dataclass(frozen=True)
class BulkPublicationResult:
    published: int
    preview: BulkPublicationPreview


class BulkPublicationService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def preview_imported_drafts(self) -> BulkPublicationPreview:
        imported_drafts = self._imported_drafts()
        eligible = self._eligible_imported_drafts(imported_drafts)
        category_counts = Counter(self._category_label(material) for material in eligible)
        return BulkPublicationPreview(
            eligible=len(eligible),
            blocked_needs_review=self._count_needs_review(),
            blocked_manual_drafts=self._count_manual_drafts(),
            blocked_non_public_topic=self._count_imported_drafts_non_public_topic(imported_drafts),
            blocked_person_name_review=self._count_imported_drafts_with_person_name_review(imported_drafts),
            blocked_salutation=self._count_imported_drafts_with_salutation_issue(imported_drafts),
            by_category=sorted(category_counts.items(), key=lambda item: (-item[1], item[0])),
        )

    def publish_imported_drafts(self) -> BulkPublicationResult:
        eligible = self._eligible_imported_drafts(self._imported_drafts())
        for material in eligible:
            material.status = MaterialStatus.ACTIVE
        self.session.flush()
        SearchService(self.session).rebuild_index()
        return BulkPublicationResult(
            published=len(eligible),
            preview=self.preview_imported_drafts(),
        )

    def _eligible_imported_drafts(self, imported_drafts: list[Material]) -> list[Material]:
        return [
            material
            for material in imported_drafts
            if material.topic is not None
            and material.topic.is_public is True
            and material.is_official is True
            and material.needs_person_name_review is False
            and not self._has_pending_person_name_review(material)
            and not has_unredacted_salutation_addressee(material.public_text)
        ]

    def _imported_drafts(self) -> list[Material]:
        return list(
            self.session.scalars(
                select(Material)
                .where(
                    Material.status == MaterialStatus.DRAFT,
                    Material.import_batch_id.is_not(None),
                )
                .options(
                    selectinload(Material.topic),
                    selectinload(Material.category),
                    selectinload(Material.person_name_reviews),
                )
            )
        )

    def _count_needs_review(self) -> int:
        return int(
            self.session.scalar(
                select(func.count(Material.id))
                .where(
                    Material.status == MaterialStatus.NEEDS_REVIEW,
                    Material.import_batch_id.is_not(None),
                )
            )
            or 0
        )

    def _count_manual_drafts(self) -> int:
        return int(
            self.session.scalar(
                select(func.count(Material.id))
                .where(
                    Material.status == MaterialStatus.DRAFT,
                    Material.import_batch_id.is_(None),
                )
            )
            or 0
        )

    def _count_imported_drafts_non_public_topic(self, imported_drafts: list[Material]) -> int:
        return sum(
            1
            for material in imported_drafts
            if material.topic is None or material.topic.is_public is not True or material.is_official is not True
        )

    def _count_imported_drafts_with_person_name_review(self, imported_drafts: list[Material]) -> int:
        return sum(
            1
            for material in imported_drafts
            if material.needs_person_name_review or self._has_pending_person_name_review(material)
        )

    def _count_imported_drafts_with_salutation_issue(self, imported_drafts: list[Material]) -> int:
        return sum(
            1
            for material in imported_drafts
            if has_unredacted_salutation_addressee(material.public_text)
        )

    @staticmethod
    def _has_pending_person_name_review(material: Material) -> bool:
        return any(review.status == ReviewStatus.PENDING for review in material.person_name_reviews)

    @staticmethod
    def _category_label(material: Material) -> str:
        if material.category is not None:
            return material.category.name
        if material.topic is not None:
            return material.topic.name
        return "Без категории"
