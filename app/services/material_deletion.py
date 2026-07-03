from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, func, select, text, update
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
)


@dataclass(frozen=True)
class MaterialDeletePreview:
    material_id: int
    admin_notes: int
    question_variants: int
    question_links: int
    redaction_events: int
    person_name_reviews: int
    dictionary_candidates: int
    material_recommendations: int
    problem_queries_to_unlink: int
    duplicate_children_to_unlink: int
    search_index_rows: int


class MaterialDeletionService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def preview(self, material_id: int) -> MaterialDeletePreview | None:
        if self.session.get(Material, material_id) is None:
            return None
        return MaterialDeletePreview(
            material_id=material_id,
            admin_notes=self._count(AdminNote.material_id, material_id),
            question_variants=self._count(QuestionVariant.material_id, material_id),
            question_links=self._count(MaterialLink.material_id, material_id),
            redaction_events=self._count(RedactionEvent.material_id, material_id),
            person_name_reviews=self._count(PersonNameReview.material_id, material_id),
            dictionary_candidates=self._count(DictionaryCandidate.material_id, material_id),
            material_recommendations=self._count(MaterialRecommendation.material_id, material_id),
            problem_queries_to_unlink=self._count(ProblemQuery.shown_material_id, material_id),
            duplicate_children_to_unlink=self._count(Material.duplicate_of_id, material_id),
            search_index_rows=self._search_index_count(material_id),
        )

    def mark_pending_delete(self, material_id: int) -> Material | None:
        material = self.session.get(Material, material_id)
        if material is None:
            return None
        material.status = MaterialStatus.PENDING_DELETE
        self.session.execute(text("DELETE FROM search_index WHERE material_id = :material_id"), {"material_id": material_id})
        self.session.flush()
        return material

    def delete_permanently(self, material_id: int) -> bool:
        material = self.session.get(Material, material_id)
        if material is None:
            return False
        if material.status != MaterialStatus.PENDING_DELETE:
            raise ValueError("Material must be marked for deletion before permanent delete")

        self.session.execute(text("DELETE FROM search_index WHERE material_id = :material_id"), {"material_id": material_id})
        self.session.execute(
            update(ProblemQuery)
            .where(ProblemQuery.shown_material_id == material_id)
            .values(shown_material_id=None)
        )
        for problem_query in self.session.scalars(select(ProblemQuery)).all():
            if material_id in (problem_query.similar_material_ids or []):
                problem_query.similar_material_ids = [
                    item_id for item_id in problem_query.similar_material_ids if item_id != material_id
                ]
        self.session.execute(
            update(Material).where(Material.duplicate_of_id == material_id).values(duplicate_of_id=None)
        )
        self.session.execute(delete(AdminNote).where(AdminNote.material_id == material_id))
        self.session.execute(delete(QuestionVariant).where(QuestionVariant.material_id == material_id))
        self.session.execute(delete(MaterialLink).where(MaterialLink.material_id == material_id))
        self.session.execute(delete(RedactionEvent).where(RedactionEvent.material_id == material_id))
        self.session.execute(delete(PersonNameReview).where(PersonNameReview.material_id == material_id))
        self.session.execute(delete(DictionaryCandidate).where(DictionaryCandidate.material_id == material_id))
        self.session.execute(delete(MaterialRecommendation).where(MaterialRecommendation.material_id == material_id))
        self.session.delete(material)
        self.session.flush()
        return True

    def _count(self, column, material_id: int) -> int:
        return int(self.session.scalar(select(func.count()).where(column == material_id)) or 0)

    def _search_index_count(self, material_id: int) -> int:
        self.session.execute(
            text(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5(
                    material_id UNINDEXED,
                    normalized_text,
                    public_text,
                    category_text,
                    recommendation_text,
                    tokenize='unicode61 remove_diacritics 2'
                )
                """
            )
        )
        return int(
            self.session.scalar(
                text("SELECT count(*) FROM search_index WHERE material_id = :material_id"),
                {"material_id": material_id},
            )
            or 0
        )
