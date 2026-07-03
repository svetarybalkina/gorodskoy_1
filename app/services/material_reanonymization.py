from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.enums import MaterialStatus
from app.db.models import Material
from app.search import SearchService
from app.services.anonymization import anonymize_text
from app.services.reprocessing import MaterialReprocessingService


@dataclass(frozen=True)
class MaterialReanonymizationPreview:
    scanned: int
    would_update: int
    would_need_review: int
    active_would_move_to_review: int
    redactions: int
    person_name_reviews: int


@dataclass(frozen=True)
class MaterialReanonymizationResult:
    scanned: int
    updated: int
    needs_review: int
    active_moved_to_review: int
    redactions: int
    person_name_reviews: int


class MaterialReanonymizationService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def preview(self) -> MaterialReanonymizationPreview:
        scanned = 0
        would_update = 0
        would_need_review = 0
        active_would_move_to_review = 0
        redactions = 0
        person_name_reviews = 0
        for material in self._materials():
            scanned += 1
            anonymized = anonymize_text(material.original_text)
            if material.public_text != anonymized.text:
                would_update += 1
            if anonymized.needs_review:
                would_need_review += 1
            if material.status == MaterialStatus.ACTIVE and anonymized.needs_review:
                active_would_move_to_review += 1
            redactions += len(anonymized.redactions)
            person_name_reviews += len(anonymized.person_names)
        return MaterialReanonymizationPreview(
            scanned=scanned,
            would_update=would_update,
            would_need_review=would_need_review,
            active_would_move_to_review=active_would_move_to_review,
            redactions=redactions,
            person_name_reviews=person_name_reviews,
        )

    def execute(self) -> MaterialReanonymizationResult:
        scanned = 0
        updated = 0
        needs_review = 0
        active_moved_to_review = 0
        redactions = 0
        person_name_reviews = 0
        reprocessor = MaterialReprocessingService(self.session)
        for material_id, was_active, old_public_text in self._material_snapshots():
            scanned += 1
            result = reprocessor.reprocess_material(material_id, reindex=False)
            if result is None:
                continue
            material = self.session.get(Material, material_id)
            if material is None:
                continue
            if material.public_text != old_public_text:
                updated += 1
            if result.needs_review:
                needs_review += 1
            if was_active and material.status == MaterialStatus.NEEDS_REVIEW:
                active_moved_to_review += 1
            redactions += result.redactions
            person_name_reviews += result.person_name_reviews
        SearchService(self.session).rebuild_index()
        self.session.flush()
        return MaterialReanonymizationResult(
            scanned=scanned,
            updated=updated,
            needs_review=needs_review,
            active_moved_to_review=active_moved_to_review,
            redactions=redactions,
            person_name_reviews=person_name_reviews,
        )

    def _materials(self) -> list[Material]:
        return list(self.session.scalars(select(Material).order_by(Material.id)))

    def _material_snapshots(self) -> list[tuple[int, bool, str]]:
        return [
            (material.id, material.status == MaterialStatus.ACTIVE, material.public_text)
            for material in self._materials()
        ]
