from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.orm import Session, selectinload

from app.db.enums import (
    DictionaryCandidateSource,
    DictionaryCandidateStatus,
    DictionaryCandidateType,
    MaterialStatus,
    ProblemQueryAction,
    ProblemQueryChannel,
)
from app.db.models import Category, DictionaryCandidate, Material, QuestionVariant, Topic
from app.db.repositories import (
    DictionaryCandidateRepository,
    ProblemQueryRepository,
    TaxonomyRepository,
)
from app.search.normalization import category_marker_text, guess_category_slug, normalize_text, tokens
from app.services.anonymization import anonymize_text


MATCH_LEVELS = {"high", "medium", "low", "none", "not_helpful"}


@dataclass(frozen=True)
class SearchItem:
    material: Material
    score: float


@dataclass(frozen=True)
class SearchResponse:
    items: list[SearchItem]
    match_level: str
    normalized_query: str
    problem_query_saved: bool = False

    @property
    def materials(self) -> list[Material]:
        return [item.material for item in self.items]


class SearchService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self._ensure_index()

    def search_public(
        self,
        query: str,
        *,
        category_id: int | None = None,
        limit: int = 20,
        record_problem_query: bool = True,
    ) -> SearchResponse:
        selected_category = None
        category_ids = None
        if category_id is not None:
            selected_category = self.session.get(Category, category_id)
            if selected_category is not None and selected_category.slug == "animals":
                category_ids = TaxonomyRepository(self.session).list_public_animal_category_ids()
            else:
                category_ids = [category_id]

        normalized_query = normalize_text(query)
        if not normalized_query:
            materials = self._load_recent_public(category_ids=category_ids, limit=limit)
            return SearchResponse(
                items=[SearchItem(material=material, score=0.0) for material in materials],
                match_level="none" if not materials else "medium",
                normalized_query="",
                problem_query_saved=False,
            )

        rows = self._fts_rows(normalized_query, category_ids=category_ids, limit=limit)
        if not rows:
            self.rebuild_index()
            rows = self._fts_rows(normalized_query, category_ids=category_ids, limit=limit)
        material_ids = [int(row["material_id"]) for row in rows]
        materials_by_id = self._load_materials(material_ids)
        items = [
            SearchItem(material=materials_by_id[material_id], score=float(row["score"]))
            for row, material_id in zip(rows, material_ids, strict=False)
            if material_id in materials_by_id
        ]
        match_level = self._match_level(items=items, normalized_query=normalized_query)
        problem_query_saved = False
        if record_problem_query and match_level in {"low", "none"}:
            self._record_problem_query(
                original_query=query,
                normalized_query=normalized_query,
                category_id=category_id,
                shown_material_id=items[0].material.id if items else None,
                similar_material_ids=[item.material.id for item in items[1:4]],
                match_level=match_level,
            )
            problem_query_saved = True
        return SearchResponse(
            items=items,
            match_level=match_level,
            normalized_query=normalized_query,
            problem_query_saved=problem_query_saved,
        )

    def reindex_material(self, material_id: int) -> None:
        self._ensure_index()
        self.session.execute(text("DELETE FROM search_index WHERE material_id = :material_id"), {"material_id": material_id})
        material = self.session.scalar(
            select(Material)
            .where(Material.id == material_id)
            .options(
                selectinload(Material.topic),
                selectinload(Material.category),
                selectinload(Material.variants),
            )
        )
        if material is None or not self._is_public_searchable(material):
            self.session.flush()
            return
        self._insert_material(material)
        self.session.flush()

    def rebuild_index(self) -> None:
        self._ensure_index()
        self.session.execute(text("DELETE FROM search_index"))
        materials = self.session.scalars(
            select(Material)
            .join(Material.topic)
            .where(
                Material.status == MaterialStatus.ACTIVE,
                Material.is_official.is_(True),
                Topic.is_public.is_(True),
            )
            .options(
                selectinload(Material.topic),
                selectinload(Material.category),
                selectinload(Material.variants),
            )
        )
        for material in materials:
            self._insert_material(material)
        self.session.flush()

    def record_not_helpful(
        self,
        *,
        original_query: str,
        material: Material,
        similar_material_ids: list[int],
    ) -> None:
        normalized_query = normalize_text(original_query)
        self._record_problem_query(
            original_query=original_query,
            normalized_query=normalized_query,
            category_id=material.category_id,
            shown_material_id=material.id,
            similar_material_ids=similar_material_ids,
            match_level="not_helpful",
        )

    def approve_candidate(self, candidate_id: int) -> DictionaryCandidate | None:
        repo = DictionaryCandidateRepository(self.session)
        candidate = repo.approve(candidate_id)
        if candidate is None:
            return None
        if candidate.candidate_type == DictionaryCandidateType.CATEGORY:
            self._approve_category_candidate(candidate)
        elif candidate.candidate_type == DictionaryCandidateType.QUESTION_VARIANT and candidate.material_id:
            variant = self.session.scalar(
                select(QuestionVariant).where(
                    QuestionVariant.material_id == candidate.material_id,
                    QuestionVariant.normalized_text == candidate.normalized_text,
                )
            )
            if variant is None:
                variant = QuestionVariant(
                    material_id=candidate.material_id,
                    text=candidate.text,
                    normalized_text=candidate.normalized_text,
                    is_confirmed=True,
                    created_from_problem_query_id=candidate.problem_query_id,
                )
                self.session.add(variant)
            else:
                variant.is_confirmed = True
            self.reindex_material(candidate.material_id)
        elif candidate.category_id is not None:
            self._reindex_category(candidate.category_id)
        return candidate

    def reject_candidate(self, candidate_id: int) -> DictionaryCandidate | None:
        return DictionaryCandidateRepository(self.session).reject(candidate_id)

    def _approve_category_candidate(self, candidate: DictionaryCandidate) -> None:
        taxonomy = TaxonomyRepository(self.session)
        if candidate.category_id is not None:
            category = self.session.get(Category, candidate.category_id)
            if category is not None:
                category.is_confirmed = True
                category.is_public = True
                self.session.flush()
            return

        topic = taxonomy.get_topic_by_slug("housing")
        if topic is None:
            return
        slug = f"category-{candidate.id}"
        category = taxonomy.get_category_by_slug(topic_id=topic.id, slug=slug)
        if category is None:
            category = taxonomy.create_category(
                topic_id=topic.id,
                slug=slug,
                name=candidate.text.strip() or f"Категория {candidate.id}",
                is_public=True,
                is_confirmed=True,
                sort_order=100,
            )
        else:
            category.is_public = True
            category.is_confirmed = True
        candidate.category_id = category.id
        self.session.flush()

    def _ensure_index(self) -> None:
        self.session.execute(
            text(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5(
                    material_id UNINDEXED,
                    normalized_text,
                    public_text,
                    category_text,
                    tokenize='unicode61 remove_diacritics 2'
                )
                """
            )
        )

    def _insert_material(self, material: Material) -> None:
        category_text = ""
        if material.category is not None:
            approved_candidates = self.session.scalars(
                select(DictionaryCandidate).where(
                    DictionaryCandidate.category_id == material.category_id,
                    DictionaryCandidate.status == DictionaryCandidateStatus.APPROVED,
                    DictionaryCandidate.candidate_type.in_(
                        [DictionaryCandidateType.MARKER, DictionaryCandidateType.SYNONYM]
                    ),
                )
            )
            category_text = " ".join(
                [
                    material.category.name,
                    material.category.slug,
                    category_marker_text(material.category.slug),
                    *(candidate.normalized_text for candidate in approved_candidates),
                ]
            )
        confirmed_variants = " ".join(
            variant.normalized_text or normalize_text(variant.text)
            for variant in self.session.scalars(
                select(QuestionVariant).where(
                    QuestionVariant.material_id == material.id,
                    QuestionVariant.is_confirmed.is_(True),
                )
            )
        )
        normalized_text = normalize_text(
            " ".join(
                [
                    material.public_text,
                    material.topic.name,
                    category_text,
                    confirmed_variants,
                ]
            )
        )
        self.session.execute(
            text(
                """
                INSERT INTO search_index(material_id, normalized_text, public_text, category_text)
                VALUES (:material_id, :normalized_text, :public_text, :category_text)
                """
            ),
            {
                "material_id": material.id,
                "normalized_text": normalized_text,
                "public_text": material.public_text,
                "category_text": normalize_text(category_text),
            },
        )

    def _fts_rows(
        self,
        normalized_query: str,
        *,
        category_ids: list[int] | None,
        limit: int,
    ) -> list[dict[str, object]]:
        query_tokens = tokens(normalized_query)
        if not query_tokens:
            return []
        match_query = " OR ".join(query_tokens)
        sql = """
            SELECT material_id, bm25(search_index) AS rank
            FROM search_index
            WHERE search_index MATCH :match_query
            ORDER BY rank
            LIMIT :limit
        """
        rows = self.session.execute(
            text(sql),
            {"match_query": match_query, "limit": limit * 3},
        ).mappings()
        material_ids = [int(row["material_id"]) for row in rows]
        if not material_ids:
            return []
        materials = self._load_materials(material_ids)
        filtered_rows: list[dict[str, object]] = []
        query_terms = set(query_tokens)
        for material_id in material_ids:
            material = materials.get(material_id)
            if material is None:
                continue
            if category_ids is not None and material.category_id not in category_ids:
                continue
            indexed_text = normalize_text(
                " ".join(
                    [
                        material.public_text,
                        material.topic.name,
                        material.category.name if material.category else "",
                    ]
                )
            )
            overlap = len(query_terms & set(indexed_text.split()))
            score = overlap / max(len(query_terms), 1)
            filtered_rows.append({"material_id": material_id, "score": score})
            if len(filtered_rows) >= limit:
                break
        return filtered_rows

    def _load_materials(self, material_ids: list[int]) -> dict[int, Material]:
        if not material_ids:
            return {}
        materials = self.session.scalars(
            select(Material)
            .where(Material.id.in_(material_ids))
            .options(
                selectinload(Material.source),
                selectinload(Material.topic),
                selectinload(Material.category).selectinload(Category.topic),
            )
        )
        material_map = {material.id: material for material in materials if self._is_public_searchable(material)}
        return {material_id: material_map[material_id] for material_id in material_ids if material_id in material_map}

    def _load_recent_public(self, *, category_ids: list[int] | None, limit: int) -> list[Material]:
        statement = (
            select(Material)
            .join(Material.topic)
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
        if category_ids is not None:
            statement = statement.where(Material.category_id.in_(category_ids))
        return list(self.session.scalars(statement))

    def _record_problem_query(
        self,
        *,
        original_query: str,
        normalized_query: str,
        category_id: int | None,
        shown_material_id: int | None,
        similar_material_ids: list[int],
        match_level: str,
    ) -> None:
        anonymized = anonymize_text(original_query)
        safe_text = anonymized.text if anonymized.text.strip() else "[пустой поисковый запрос]"
        problem_query = ProblemQueryRepository(self.session).create(
            anonymized_text=safe_text,
            normalized_text=normalized_query,
            channel=ProblemQueryChannel.WEBSITE,
            shown_material_id=shown_material_id,
            category_id=category_id,
            similar_material_ids=similar_material_ids,
            user_action=ProblemQueryAction.REPHRASE,
            match_level=match_level,
            selection_reason="Поисковая выдача была неуверенной." if match_level != "not_helpful" else "Пользователь нажал кнопку 'Ответ не подошел' на публичной карточке.",
        )
        candidate_text = normalized_query or safe_text
        candidate_category_id = category_id
        if candidate_category_id is None:
            guessed_slug = guess_category_slug(original_query)
            if guessed_slug is not None:
                topic = TaxonomyRepository(self.session).get_topic_by_slug("housing")
                category = (
                    TaxonomyRepository(self.session).get_category(topic_id=topic.id, slug=guessed_slug)
                    if topic is not None
                    else None
                )
                candidate_category_id = category.id if category is not None else None
        if candidate_text.strip():
            DictionaryCandidateRepository(self.session).create_or_increment(
                text=safe_text,
                normalized_text=normalized_query or normalize_text(safe_text),
                candidate_type=DictionaryCandidateType.QUESTION_VARIANT if shown_material_id else DictionaryCandidateType.MARKER,
                source=DictionaryCandidateSource.SEARCH,
                category_id=candidate_category_id,
                material_id=shown_material_id,
                problem_query_id=problem_query.id,
            )

    def _match_level(self, *, items: list[SearchItem], normalized_query: str) -> str:
        if not items:
            return "none"
        best_score = items[0].score
        if best_score >= 0.75:
            return "high"
        if best_score >= 0.4:
            return "medium"
        return "low"

    def _is_public_searchable(self, material: Material) -> bool:
        return (
            material.status == MaterialStatus.ACTIVE
            and material.is_official is True
            and material.topic is not None
            and material.topic.is_public is True
        )

    def _reindex_category(self, category_id: int) -> None:
        material_ids = self.session.scalars(
            select(Material.id).where(Material.category_id == category_id)
        )
        for material_id in material_ids:
            self.reindex_material(material_id)
