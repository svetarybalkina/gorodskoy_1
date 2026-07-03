from __future__ import annotations

import re
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
from app.db.models import Category, DictionaryCandidate, Material, MaterialRecommendation, QuestionVariant, Topic
from app.db.repositories import (
    DictionaryCandidateRepository,
    ProblemQueryRepository,
    TaxonomyRepository,
)
from app.search.normalization import category_marker_text, guess_category_slug, normalize_text, tokens
from app.search.intent import action_score_multiplier, classify_query_need, is_action_useful_for_need, QueryNeed
from app.services.anonymization import anonymize_text
from app.services.classification import REQUIRED_MARKERS
from app.services.recommendations import MIN_CONFIDENCE_FOR_PUBLIC, RecommendationExtractionService


MATCH_LEVELS = {"high", "medium", "low", "none", "not_helpful"}
SNIPPET_LENGTH = 320


@dataclass(frozen=True)
class SearchItem:
    material: Material
    score: float
    snippet: str


@dataclass(frozen=True)
class RecommendationSearchItem:
    recommendation: MaterialRecommendation
    material: Material
    score: float


@dataclass(frozen=True)
class SearchResponse:
    items: list[SearchItem]
    recommendations: list[RecommendationSearchItem]
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
        query_need = classify_query_need(query)
        if not normalized_query:
            materials = self._load_recent_public(category_ids=category_ids, limit=limit * 3)
            if selected_category is not None:
                materials = [
                    material
                    for material in materials
                    if self._has_category_evidence(material, selected_category)
                ]
            materials = materials[:limit]
            snippet_query = self._category_snippet_query(selected_category)
            return SearchResponse(
                items=[
                    SearchItem(
                        material=material,
                        score=0.0,
                        snippet=self._snippet_for_material(material, normalized_query=snippet_query),
                    )
                    for material in materials
                ],
                recommendations=[],
                match_level="none" if not materials else "medium",
                normalized_query="",
                problem_query_saved=False,
            )

        rows = self._fts_rows(
            normalized_query,
            query_need=query_need,
            category_ids=category_ids,
            selected_category=selected_category,
            limit=limit,
        )
        if not rows:
            self.rebuild_index()
            rows = self._fts_rows(
                normalized_query,
                query_need=query_need,
                category_ids=category_ids,
                selected_category=selected_category,
                limit=limit,
            )
        material_ids = [int(row["material_id"]) for row in rows]
        materials_by_id = self._load_materials(material_ids)
        items = [
            SearchItem(
                material=materials_by_id[material_id],
                score=float(row["score"]),
                snippet=self._snippet_for_material(
                    materials_by_id[material_id],
                    normalized_query=normalized_query,
                ),
            )
            for row, material_id in zip(rows, material_ids, strict=False)
            if material_id in materials_by_id
        ]
        match_level = self._match_level(items=items, normalized_query=normalized_query)
        recommendations = self._recommendations_for_items(
            items=items,
            normalized_query=normalized_query,
            query_need=query_need,
        )
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
            recommendations=recommendations,
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
                selectinload(Material.question_links),
            )
        )
        if material is None or not self._is_public_searchable(material):
            if material is not None:
                RecommendationExtractionService(self.session).refresh_material(material.id)
            self.session.flush()
            return
        recommendations = RecommendationExtractionService(self.session).refresh_material(material.id)
        self._insert_material(material, recommendations_text=" ".join(item.normalized_text for item in recommendations))
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
                selectinload(Material.question_links),
            )
        )
        for material in materials:
            recommendations = RecommendationExtractionService(self.session).refresh_material(material.id)
            self._insert_material(material, recommendations_text=" ".join(item.normalized_text for item in recommendations))
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
                    recommendation_text,
                    question_text,
                    tokenize='unicode61 remove_diacritics 2'
                )
                """
            )
        )
        columns = {
            str(row["name"])
            for row in self.session.execute(text("PRAGMA table_info(search_index)")).mappings()
        }
        if "recommendation_text" not in columns or "question_text" not in columns:
            self.session.execute(text("DROP TABLE search_index"))
            self.session.execute(
                text(
                    """
                    CREATE VIRTUAL TABLE search_index USING fts5(
                        material_id UNINDEXED,
                        normalized_text,
                        public_text,
                        category_text,
                        recommendation_text,
                        question_text,
                        tokenize='unicode61 remove_diacritics 2'
                    )
                    """
                )
            )

    def _insert_material(self, material: Material, *, recommendations_text: str = "") -> None:
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
        linked_questions_text = self._linked_questions_text(material.id)
        normalized_text = normalize_text(
            " ".join(
                [
                    material.public_text,
                    material.topic.name,
                    category_text,
                    confirmed_variants,
                    recommendations_text,
                    linked_questions_text,
                ]
            )
        )
        self.session.execute(
            text(
                """
                INSERT INTO search_index(material_id, normalized_text, public_text, category_text, recommendation_text, question_text)
                VALUES (:material_id, :normalized_text, :public_text, :category_text, :recommendation_text, :question_text)
                """
            ),
            {
                "material_id": material.id,
                "normalized_text": normalized_text,
                "public_text": material.public_text,
                "category_text": normalize_text(category_text),
                "recommendation_text": recommendations_text,
                "question_text": linked_questions_text,
            },
        )

    def _fts_rows(
        self,
        normalized_query: str,
        *,
        query_need: QueryNeed,
        category_ids: list[int] | None,
        selected_category: Category | None,
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
            if selected_category is not None and not self._has_category_evidence(material, selected_category):
                continue
            indexed_text = normalize_text(
                " ".join(
                    [
                        material.public_text,
                        material.topic.name,
                        material.category.name if material.category else "",
                        " ".join(
                            recommendation.normalized_text
                            for recommendation in material.recommendations
                            if recommendation.confidence >= MIN_CONFIDENCE_FOR_PUBLIC
                        ),
                        self._linked_questions_text(material.id),
                    ]
                )
            )
            overlap = len(query_terms & set(indexed_text.split()))
            recommendation_overlap = self._recommendation_overlap(material, query_terms, query_need)
            question_overlap = len(query_terms & set(self._linked_questions_text(material.id).split()))
            score = (overlap + recommendation_overlap + question_overlap * 2.0) / max(len(query_terms), 1)
            if query_need.category_slug is not None and material.category is not None:
                if material.category.slug == query_need.category_slug:
                    score += 0.25
            score *= self._material_need_multiplier(material, query_need)
            filtered_rows.append({"material_id": material_id, "score": score})
        filtered_rows.sort(key=lambda row: (-float(row["score"]), material_ids.index(int(row["material_id"]))))
        return filtered_rows[:limit]

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
                selectinload(Material.recommendations),
                selectinload(Material.question_links),
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
                selectinload(Material.recommendations),
                selectinload(Material.question_links),
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

    def _snippet_for_material(self, material: Material, *, normalized_query: str) -> str:
        text_value = material.public_text.strip()
        if not text_value:
            return ""
        query_terms = set(tokens(normalized_query))
        if not query_terms:
            return self._truncate_snippet(text_value)

        best_span: tuple[int, int] | None = None
        best_score = 0
        for match in re.finditer(r"[^.!?\n]+[.!?]?", text_value):
            sentence = match.group(0)
            sentence_terms = set(tokens(sentence))
            score = len(query_terms & sentence_terms)
            if score > best_score:
                best_score = score
                best_span = match.span()

        if best_span is None or best_score == 0:
            return self._truncate_snippet(text_value)

        sentence_start, sentence_end = best_span
        match_position = self._first_matching_token_position(
            text_value[sentence_start:sentence_end],
            query_terms,
        )
        center = sentence_start + (match_position if match_position is not None else 0)
        return self._window_snippet(text_value, center, minimum_start=sentence_start)

    def _first_matching_token_position(self, text_value: str, query_terms: set[str]) -> int | None:
        for match in re.finditer(r"\w+", text_value, flags=re.UNICODE):
            if query_terms & set(tokens(match.group(0))):
                return match.start()
        return None

    def _truncate_snippet(self, text_value: str) -> str:
        if len(text_value) <= SNIPPET_LENGTH:
            return text_value
        return text_value[:SNIPPET_LENGTH].rstrip() + "..."

    def _window_snippet(self, text_value: str, center: int, *, minimum_start: int | None = None) -> str:
        if len(text_value) <= SNIPPET_LENGTH:
            return text_value
        half_window = SNIPPET_LENGTH // 2
        start = max(center - half_window, 0)
        if minimum_start is not None:
            start = max(start, minimum_start)
        end = min(start + SNIPPET_LENGTH, len(text_value))
        start = max(end - SNIPPET_LENGTH, 0)
        if minimum_start is not None:
            start = max(start, minimum_start)
            end = min(start + SNIPPET_LENGTH, len(text_value))

        if start > 0:
            whitespace = text_value.find(" ", start)
            if whitespace != -1 and whitespace < center:
                start = whitespace + 1
        if end < len(text_value):
            whitespace = text_value.rfind(" ", start, end)
            if whitespace != -1 and whitespace > center:
                end = whitespace

        snippet = text_value[start:end].strip()
        if start > 0:
            snippet = "..." + snippet
        if end < len(text_value):
            snippet += "..."
        return snippet

    def _category_snippet_query(self, category: Category | None) -> str:
        if category is None:
            return ""
        return normalize_text(" ".join([category.name, category_marker_text(category.slug)]))

    def _has_category_evidence(self, material: Material, category: Category) -> bool:
        if category.slug == "other":
            return True
        category_terms = REQUIRED_MARKERS.get(category.slug, set())
        if not category_terms:
            return True
        material_terms = set(tokens(material.public_text))
        return bool(category_terms & material_terms)

    def _is_public_searchable(self, material: Material) -> bool:
        return (
            material.status == MaterialStatus.ACTIVE
            and material.is_official is True
            and material.topic is not None
            and material.topic.is_public is True
        )

    def _recommendations_for_items(
        self,
        *,
        items: list[SearchItem],
        normalized_query: str,
        query_need: QueryNeed,
        limit: int = 3,
    ) -> list[RecommendationSearchItem]:
        if not items or not normalized_query:
            return []
        query_terms = set(tokens(normalized_query))
        if not query_terms:
            return []
        results: list[RecommendationSearchItem] = []
        for item in items:
            for recommendation in item.material.recommendations:
                if recommendation.confidence < MIN_CONFIDENCE_FOR_PUBLIC:
                    continue
                if not is_action_useful_for_need(query_need, recommendation.action_kind):
                    continue
                recommendation_terms = set(tokens(recommendation.normalized_text))
                overlap = len(query_terms & recommendation_terms)
                if overlap == 0:
                    continue
                score = (overlap / max(len(query_terms), 1)) * action_score_multiplier(
                    query_need,
                    recommendation.action_kind,
                )
                results.append(
                    RecommendationSearchItem(
                        recommendation=recommendation,
                        material=item.material,
                        score=score,
                    )
                )
        results.sort(
            key=lambda result: (
                -result.score,
                -result.recommendation.confidence,
                result.material.published_at,
                result.recommendation.sort_order,
            )
        )
        return results[:limit]

    def _recommendation_overlap(self, material: Material, query_terms: set[str], query_need: QueryNeed) -> float:
        best_overlap = 0.0
        for recommendation in material.recommendations:
            if recommendation.confidence < MIN_CONFIDENCE_FOR_PUBLIC:
                continue
            overlap = len(query_terms & set(tokens(recommendation.normalized_text)))
            weighted = overlap * action_score_multiplier(query_need, recommendation.action_kind)
            best_overlap = max(best_overlap, weighted)
        return best_overlap

    def _material_need_multiplier(self, material: Material, query_need: QueryNeed) -> float:
        if query_need.intent.value != "complaint_escalation" or query_need.category_slug != "management_company":
            return 1.0
        action_kinds = {recommendation.action_kind for recommendation in material.recommendations}
        if "oversight" in action_kinds:
            return 1.8
        if "self_service" in action_kinds:
            return 0.3
        return 0.7

    def _linked_questions_text(self, material_id: int) -> str:
        from app.db.models import MaterialLink, ResidentQuestion

        questions = self.session.scalars(
            select(ResidentQuestion.normalized_text)
            .join(MaterialLink, MaterialLink.question_id == ResidentQuestion.id)
            .where(MaterialLink.material_id == material_id)
        )
        return " ".join(question for question in questions if question)

    def _reindex_category(self, category_id: int) -> None:
        material_ids = self.session.scalars(
            select(Material.id).where(Material.category_id == category_id)
        )
        for material_id in material_ids:
            self.reindex_material(material_id)
