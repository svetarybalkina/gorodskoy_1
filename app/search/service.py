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
from app.db.models import Category, DictionaryCandidate, Material, MaterialLink, MaterialRecommendation, QuestionVariant, Topic
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
STATE_PATTERNS: dict[str, tuple[str, ...]] = {
    "maintenance_condition": ("гряз", "уборк", "неубран", "санитар", "мусор", "воняет", "запах"),
    "surface_clearing": ("очист", "убрат", "подмест", "помы", "мыть"),
    "weather_access": ("снег", "налед", "гололед", "сугроб", "противогололед", "подсып"),
    "temperature_comfort": ("температур", "холод", "жарк", "отоплен", "батаре"),
    "repair_work": ("ремонт", "почин", "неисправ", "сломан", "замен", "восстанов"),
    "service_outage": ("отключ", "отсутств", "нет ", "не работает", "порыв", "утеч"),
    "complaint_control": ("жалоб", "бездейств", "претенз", "нарушен", "не реагир"),
    "schedule_information": ("график", "когда", "срок", "ознаком", "планиру", "будет"),
    "danger_emergency": ("авар", "срочн", "опасн", "агрессивн"),
}
ORTHOGONAL_STATE_PENALTY = 0.45
RELATED_STATE_ALIGNMENT: dict[tuple[str, str], float] = {
    ("maintenance_condition", "surface_clearing"): 0.45,
    ("surface_clearing", "maintenance_condition"): 0.6,
    ("service_outage", "repair_work"): 0.35,
    ("repair_work", "service_outage"): 0.35,
}
GENERIC_RESPONSE_PATTERNS = (
    "направлен специалист",
    "направлено специалист",
    "вернемся с ответ",
    "для детальной проработки",
    "для предоставления информации",
    "уточните адресный ориентир",
)


@dataclass(frozen=True)
class SearchItem:
    material: Material
    score: float
    snippet: str
    signals: SearchSignals


@dataclass(frozen=True)
class SearchSignals:
    public_overlap: float
    question_overlap: float
    recommendation_overlap: float
    phrase_overlap: float
    strict_question_match: bool
    state_alignment: float
    category_alignment: float
    category_evidence: float
    state_penalty: float
    generic_penalty: float
    need_multiplier: float
    weak_signal_only: bool
    final_score: float


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
    has_strict_question_match: bool = False
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
                        signals=SearchSignals(
                            public_overlap=0.0,
                            question_overlap=0.0,
                            recommendation_overlap=0.0,
                            phrase_overlap=0.0,
                            strict_question_match=False,
                            state_alignment=0.0,
                            category_alignment=0.0,
                            category_evidence=1.0 if selected_category is None else float(self._has_category_evidence(material, selected_category)),
                            state_penalty=1.0,
                            generic_penalty=1.0,
                            need_multiplier=1.0,
                            weak_signal_only=False,
                            final_score=0.0,
                        ),
                    )
                    for material in materials
                ],
                recommendations=[],
                match_level="none" if not materials else "medium",
                normalized_query="",
                has_strict_question_match=False,
                problem_query_saved=False,
            )

        rows = self._fts_rows(
            normalized_query,
            raw_query=query,
            query_need=query_need,
            category_ids=category_ids,
            selected_category=selected_category,
            limit=limit,
        )
        if not rows:
            self.rebuild_index()
            rows = self._fts_rows(
                normalized_query,
                raw_query=query,
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
                signals=row["signals"],
            )
            for row, material_id in zip(rows, material_ids, strict=False)
            if material_id in materials_by_id
        ]
        match_level = self._match_level(items=items, normalized_query=normalized_query)
        has_strict_question_match = self._has_strict_question_match(query=query, items=items)
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
            has_strict_question_match=has_strict_question_match,
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
                selectinload(Material.question_links).selectinload(MaterialLink.question),
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
                selectinload(Material.question_links).selectinload(MaterialLink.question),
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
        linked_questions_text = self._linked_questions_text(material)
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
        raw_query: str,
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
        query_phrases = self._query_phrases(query_tokens)
        query_states = self._extract_states(raw_query)
        for material_id in material_ids:
            material = materials.get(material_id)
            if material is None:
                continue
            if category_ids is not None and material.category_id not in category_ids:
                continue
            if selected_category is not None and not self._has_category_evidence(material, selected_category):
                continue
            signals = self._score_material(
                material=material,
                raw_query=raw_query,
                query_terms=query_terms,
                query_phrases=query_phrases,
                query_states=query_states,
                query_need=query_need,
            )
            filtered_rows.append({"material_id": material_id, "score": signals.final_score, "signals": signals})
        filtered_rows.sort(
            key=lambda row: (
                -float(row["score"]),
                -row["signals"].question_overlap,
                -row["signals"].state_alignment,
                -row["signals"].phrase_overlap,
                -row["signals"].public_overlap,
                material_ids.index(int(row["material_id"])),
            )
        )
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
                selectinload(Material.question_links).selectinload(MaterialLink.question),
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
                selectinload(Material.question_links).selectinload(MaterialLink.question),
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
        best = items[0]
        best_signals = best.signals
        query_term_count = len(tokens(normalized_query))
        if best_signals.weak_signal_only:
            return "low"
        if query_term_count <= 1:
            if best.score >= 0.55 and max(best_signals.public_overlap, best_signals.question_overlap) >= 1.0:
                return "high"
            if best.score >= 0.3:
                return "medium"
            return "low"
        if best_signals.strict_question_match and best.score >= 0.5:
            return "high"
        if (
            best.score >= 0.85
            and best_signals.public_overlap >= 0.45
            and max(
                best_signals.phrase_overlap,
                best_signals.question_overlap,
                best_signals.recommendation_overlap,
            )
            >= 0.35
        ):
            return "high"
        if best.score >= 0.2 and max(
            best_signals.public_overlap,
            best_signals.question_overlap,
            best_signals.recommendation_overlap,
            best_signals.state_alignment,
            best_signals.category_alignment,
        ) >= 0.25:
            return "medium"
        return "low"

    def _has_strict_question_match(self, *, query: str, items: list[SearchItem]) -> bool:
        exact_query = self._strict_query_text(query)
        if not exact_query:
            return False
        for item in items:
            for link in item.material.question_links:
                question = link.question
                if question is None:
                    continue
                if self._queries_match_humanly(self._strict_query_text(question.anonymized_text), exact_query):
                    return True
        return False

    def _strict_query_text(self, text_value: str) -> str:
        compact = re.sub(r"[^\w\s]", " ", text_value.lower().replace("ё", "е"), flags=re.UNICODE)
        return " ".join(compact.split())

    def _queries_match_humanly(self, left: str, right: str) -> bool:
        if left == right:
            return True
        left_tokens = left.split()
        right_tokens = right.split()
        if len(left_tokens) != len(right_tokens):
            return False

        typo_tokens = 0
        total_distance = 0
        for left_token, right_token in zip(left_tokens, right_tokens, strict=False):
            if left_token == right_token:
                continue
            distance = self._levenshtein_distance(left_token, right_token)
            max_distance = 1 if max(len(left_token), len(right_token)) <= 4 else 2
            if distance > max_distance:
                return False
            typo_tokens += 1
            total_distance += distance

        if typo_tokens == 0:
            return True
        return typo_tokens <= 2 and total_distance <= 3

    def _levenshtein_distance(self, left: str, right: str) -> int:
        if left == right:
            return 0
        if not left:
            return len(right)
        if not right:
            return len(left)
        previous = list(range(len(right) + 1))
        for left_index, left_char in enumerate(left, start=1):
            current = [left_index]
            for right_index, right_char in enumerate(right, start=1):
                insertion = current[right_index - 1] + 1
                deletion = previous[right_index] + 1
                substitution = previous[right_index - 1] + (left_char != right_char)
                current.append(min(insertion, deletion, substitution))
            previous = current
        return previous[-1]

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
        query_states = self._extract_states(normalized_query)
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
                recommendation_states = self._extract_states(" ".join([recommendation.text, recommendation.normalized_text]))
                state_alignment, state_penalty = self._state_alignment(
                    query_states=query_states,
                    material_states=recommendation_states,
                )
                if query_states and state_alignment == 0.0:
                    continue
                score = (overlap / max(len(query_terms), 1)) * action_score_multiplier(
                    query_need,
                    recommendation.action_kind,
                )
                score *= state_alignment if state_alignment > 0.0 else 1.0
                score *= state_penalty
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
            overlap = self._overlap_ratio(query_terms, set(tokens(recommendation.normalized_text)))
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

    def _linked_questions_text(self, material: Material) -> str:
        return " ".join(
            question.normalized_text
            for link in material.question_links
            if (question := link.question) is not None and question.normalized_text
        )

    def _material_recommendation_text(self, material: Material) -> str:
        return " ".join(
            recommendation.normalized_text
            for recommendation in material.recommendations
            if recommendation.confidence >= MIN_CONFIDENCE_FOR_PUBLIC
        )

    def _score_material(
        self,
        *,
        material: Material,
        raw_query: str,
        query_terms: set[str],
        query_phrases: set[str],
        query_states: dict[str, int],
        query_need: QueryNeed,
    ) -> SearchSignals:
        public_terms = set(tokens(material.public_text))
        question_text = self._linked_questions_text(material)
        question_terms = set(question_text.split())
        recommendation_text = self._material_recommendation_text(material)
        strict_question_match = self._material_has_human_question_match(material, raw_query)
        material_states = self._extract_states(" ".join([material.public_text, recommendation_text, question_text]))

        public_overlap = self._overlap_ratio(query_terms, public_terms)
        question_overlap = self._overlap_ratio(query_terms, question_terms)
        recommendation_overlap = self._recommendation_overlap(material, query_terms, query_need)
        phrase_overlap = self._phrase_overlap(
            query_phrases=query_phrases,
            haystacks=(
                normalize_text(material.public_text),
                question_text,
                recommendation_text,
            ),
        )
        state_alignment, state_penalty = self._state_alignment(
            query_states=query_states,
            material_states=material_states,
        )
        category_alignment = self._category_alignment(material, query_need)
        category_evidence = self._category_evidence_score(material, query_need)
        generic_penalty = self._generic_penalty(material, public_overlap, phrase_overlap, question_overlap)
        need_multiplier = self._material_need_multiplier(material, query_need)
        weak_signal_only = (
            public_overlap <= 0.34
            and question_overlap == 0.0
            and recommendation_overlap == 0.0
            and phrase_overlap == 0.0
        )
        base_score = (
            public_overlap * 0.45
            + question_overlap * 0.35
            + recommendation_overlap * 0.2
            + phrase_overlap * 0.25
            + (0.45 if strict_question_match else 0.0)
            + state_alignment * 0.35
            + category_alignment * 0.12
            + category_evidence * 0.12
        )
        final_score = max(base_score * state_penalty * generic_penalty * need_multiplier, 0.0)
        if weak_signal_only and category_alignment == 0.0:
            final_score *= 0.6
        return SearchSignals(
            public_overlap=public_overlap,
            question_overlap=question_overlap,
            recommendation_overlap=recommendation_overlap,
            phrase_overlap=phrase_overlap,
            strict_question_match=strict_question_match,
            state_alignment=state_alignment,
            category_alignment=category_alignment,
            category_evidence=category_evidence,
            state_penalty=state_penalty,
            generic_penalty=generic_penalty,
            need_multiplier=need_multiplier,
            weak_signal_only=weak_signal_only,
            final_score=final_score,
        )

    def _overlap_ratio(self, query_terms: set[str], material_terms: set[str]) -> float:
        if not query_terms or not material_terms:
            return 0.0
        return len(query_terms & material_terms) / len(query_terms)

    def _query_phrases(self, query_tokens: list[str]) -> set[str]:
        phrases: set[str] = set()
        for size in (2, 3):
            if len(query_tokens) < size:
                continue
            for index in range(len(query_tokens) - size + 1):
                phrases.add(" ".join(query_tokens[index : index + size]))
        return phrases

    def _extract_states(self, text_value: str) -> dict[str, int]:
        lowered = f" {text_value.lower().replace('ё', 'е')} "
        states: dict[str, int] = {}
        for state, patterns in STATE_PATTERNS.items():
            hits = sum(lowered.count(pattern) for pattern in patterns)
            if hits > 0:
                states[state] = hits
        return states

    def _state_alignment(
        self,
        *,
        query_states: dict[str, int],
        material_states: dict[str, int],
    ) -> tuple[float, float]:
        if not query_states:
            return 0.0, 1.0
        matched_scores = [
            min(1.0, material_states[state] / max(query_states[state], 1))
            for state in query_states
            if state in material_states
        ]
        related_scores = [
            RELATED_STATE_ALIGNMENT[(query_state, material_state)]
            for query_state in query_states
            for material_state in material_states
            if (query_state, material_state) in RELATED_STATE_ALIGNMENT
        ]
        alignment = max([*matched_scores, *related_scores], default=0.0)
        penalty = 1.0
        if matched_scores:
            return alignment, penalty
        if alignment == 0.0:
            penalty *= ORTHOGONAL_STATE_PENALTY if material_states else 0.7
        elif related_scores and any(state == "weather_access" for state in material_states):
            penalty *= 0.75
        return alignment, penalty

    def _phrase_overlap(self, *, query_phrases: set[str], haystacks: tuple[str, ...]) -> float:
        if not query_phrases:
            return 0.0
        matched = 0
        for phrase in query_phrases:
            if any(phrase in haystack for haystack in haystacks if haystack):
                matched += 1
        return matched / len(query_phrases)

    def _category_alignment(self, material: Material, query_need: QueryNeed) -> float:
        if query_need.category_slug is None or material.category is None:
            return 0.0
        if material.category.slug == query_need.category_slug:
            return 1.0
        if query_need.category_slug == "animals" and material.category.slug in {"stray_dogs", "animal_capture", "aggressive_animals", "shelters", "pet_rules"}:
            return 0.9
        return 0.0

    def _category_evidence_score(self, material: Material, query_need: QueryNeed) -> float:
        if query_need.category_slug is None:
            return 0.0
        category_terms = REQUIRED_MARKERS.get(query_need.category_slug, set())
        if not category_terms:
            return 0.0
        material_terms = set(tokens(material.public_text))
        if material.category is not None and material.category.slug == query_need.category_slug and category_terms & material_terms:
            return 1.0
        if category_terms & material_terms:
            return 0.6
        return 0.0

    def _material_has_human_question_match(self, material: Material, query: str) -> bool:
        exact_query = self._strict_query_text(query)
        if not exact_query:
            return False
        for link in material.question_links:
            question = link.question
            if question is None:
                continue
            if self._queries_match_humanly(self._strict_query_text(question.anonymized_text), exact_query):
                return True
        return False

    def _generic_penalty(
        self,
        material: Material,
        public_overlap: float,
        phrase_overlap: float,
        question_overlap: float,
    ) -> float:
        normalized_public = normalize_text(material.public_text)
        if any(pattern in normalized_public for pattern in GENERIC_RESPONSE_PATTERNS):
            if max(public_overlap, phrase_overlap, question_overlap) < 0.5:
                return 0.35
            return 0.6
        if material.category is not None and material.category.slug == "other" and max(public_overlap, phrase_overlap, question_overlap) < 0.75:
            return 0.45
        return 1.0

    def _reindex_category(self, category_id: int) -> None:
        material_ids = self.session.scalars(
            select(Material.id).where(Material.category_id == category_id)
        )
        for material_id in material_ids:
            self.reindex_material(material_id)
