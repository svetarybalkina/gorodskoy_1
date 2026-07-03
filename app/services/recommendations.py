from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from app.db.enums import MaterialStatus, RecommendationType
from app.db.models import Material, MaterialRecommendation, Topic
from app.search.intent import classify_action_kind


SENTENCE_RE = re.compile(r"[^.!?\n\r]+(?:[.!?]+|$)", re.MULTILINE)

ACTION_RE = re.compile(
    r"\b("
    r"обрат(?:иться|итесь|иться можно|итесь пожалуйста)|"
    r"сообщ(?:ить|ите)|"
    r"позвон(?:ить|ите)|"
    r"направ(?:ить|ьте)|"
    r"остав(?:ить|ьте)\s+заявк[ауи]|"
    r"подать\s+заявк[ауи]|"
    r"заявк[ауи]\s+принима(?:ет|ются)|"
    r"жалоб[ау]\s+можно\s+направить|"
    r"следует\s+обратиться|"
    r"необходимо\s+обратиться"
    r")\b",
    re.IGNORECASE,
)

ORGANIZATION_RE = re.compile(
    r"\b("
    r"диспетчерск(?:ая|ую|ой)\s+служб[ауеы]?|"
    r"аварийн(?:ая|ую|ой)\s+служб[ауеы]?|"
    r"еддс|"
    r"управляющ(?:ая|ую|ей)\s+компани[яюи]|"
    r"\bук\b|"
    r"жилищн(?:ая|ую|ой)\s+инспекци[яюи]|"
    r"ресурсоснабжающ(?:ая|ую|ей)\s+организаци[яюи]|"
    r"региональн(?:ый|ому|ого)\s+оператор[ау]?|"
    r"служб[ауеы]?\s+отлова|"
    r"администраци[яюи]|"
    r"горяч(?:ая|ую|ей)\s+лини[яюи]|"
    r"контактн(?:ый|ому|ого)\s+центр[ау]?"
    r")\b",
    re.IGNORECASE,
)

DEADLINE_RE = re.compile(
    r"\b(срок|в течение|до \d{1,2}[.\s][а-яa-z0-9]+|\d+\s+(?:дн|час|сут)|после завершения|до окончания)\b",
    re.IGNORECASE,
)
CONDITION_RE = re.compile(r"\b(если|при|после|в случае|для этого|для получения|при наличии)\b", re.IGNORECASE)
RESTRICTION_RE = re.compile(r"\b(нельзя|не допускается|только|запрещено|при наличии|без .* не)\b", re.IGNORECASE)

MIN_CONFIDENCE_FOR_PUBLIC = 70


@dataclass(frozen=True)
class ExtractedRecommendation:
    recommendation_type: RecommendationType
    text: str
    normalized_text: str
    source_fragment: str
    action_kind: str
    confidence: int
    sort_order: int


@dataclass(frozen=True)
class RecommendationRebuildResult:
    scanned: int
    would_change: int
    changed: int
    recommendations: int


class RecommendationExtractionService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def extract_from_text(self, text: str) -> list[ExtractedRecommendation]:
        recommendations: list[ExtractedRecommendation] = []
        seen: set[str] = set()
        for sentence in _sentences(text):
            item = self._extract_sentence(sentence, len(recommendations))
            if item is None or item.normalized_text in seen:
                continue
            seen.add(item.normalized_text)
            recommendations.append(item)
            if len(recommendations) >= 5:
                break
        return recommendations

    def refresh_material(self, material_id: int) -> list[MaterialRecommendation]:
        material = self.session.get(Material, material_id)
        self.session.execute(delete(MaterialRecommendation).where(MaterialRecommendation.material_id == material_id))
        if material is None:
            self.session.flush()
            return []
        extracted = self.extract_from_text(material.public_text)
        rows = [
            MaterialRecommendation(
                material_id=material.id,
                recommendation_type=item.recommendation_type,
                text=item.text,
                normalized_text=item.normalized_text,
                source_fragment=item.source_fragment,
                action_kind=item.action_kind,
                confidence=item.confidence,
                sort_order=item.sort_order,
            )
            for item in extracted
        ]
        self.session.add_all(rows)
        self.session.flush()
        return rows

    def rebuild(
        self,
        *,
        status: MaterialStatus | None = None,
        public_only: bool = False,
        execute: bool = False,
        limit: int | None = None,
    ) -> RecommendationRebuildResult:
        scanned = 0
        would_change = 0
        recommendations = 0
        for material in self._materials(status=status, public_only=public_only, limit=limit):
            scanned += 1
            extracted = self.extract_from_text(material.public_text)
            recommendations += len(extracted)
            current = sorted(item.normalized_text for item in material.recommendations)
            target = sorted(item.normalized_text for item in extracted)
            if current != target:
                would_change += 1
            if execute:
                self.refresh_material(material.id)
        return RecommendationRebuildResult(
            scanned=scanned,
            would_change=would_change,
            changed=would_change if execute else 0,
            recommendations=recommendations,
        )

    def list_for_material(self, material_id: int, *, limit: int = 5) -> list[MaterialRecommendation]:
        rows = list(
            self.session.scalars(
                select(MaterialRecommendation)
                .where(
                    MaterialRecommendation.material_id == material_id,
                    MaterialRecommendation.confidence >= MIN_CONFIDENCE_FOR_PUBLIC,
                )
                .order_by(MaterialRecommendation.sort_order, MaterialRecommendation.id)
                .limit(limit)
            )
        )
        if rows:
            return rows
        self.refresh_material(material_id)
        return list(
            self.session.scalars(
                select(MaterialRecommendation)
                .where(
                    MaterialRecommendation.material_id == material_id,
                    MaterialRecommendation.confidence >= MIN_CONFIDENCE_FOR_PUBLIC,
                )
                .order_by(MaterialRecommendation.sort_order, MaterialRecommendation.id)
                .limit(limit)
            )
        )

    def _extract_sentence(self, sentence: str, index: int) -> ExtractedRecommendation | None:
        compact = _compact(sentence)
        if len(compact) < 24 or len(compact) > 420:
            return None
        has_action = ACTION_RE.search(compact) is not None
        has_org = ORGANIZATION_RE.search(compact) is not None
        has_deadline = DEADLINE_RE.search(compact) is not None
        has_condition = CONDITION_RE.search(compact) is not None
        has_restriction = RESTRICTION_RE.search(compact) is not None
        if not has_action and not ((has_deadline or has_condition or has_restriction) and has_org):
            return None
        if has_action and not (has_org or has_deadline or has_condition):
            return None

        recommendation_type = RecommendationType.NEXT_STEP
        confidence = 70
        if has_action and has_org:
            recommendation_type = RecommendationType.CONTACT
            confidence = 90
        elif has_deadline:
            recommendation_type = RecommendationType.DEADLINE
            confidence = 80
        elif has_restriction:
            recommendation_type = RecommendationType.RESTRICTION
            confidence = 75
        elif has_condition:
            recommendation_type = RecommendationType.CONDITION
            confidence = 75

        normalized = _normalize_text(compact)
        if len(_tokens(normalized)) < 3:
            return None
        return ExtractedRecommendation(
            recommendation_type=recommendation_type,
            text=compact,
            normalized_text=normalized,
            source_fragment=compact,
            action_kind=classify_action_kind(compact).value,
            confidence=confidence,
            sort_order=index,
        )

    def _materials(
        self,
        *,
        status: MaterialStatus | None,
        public_only: bool,
        limit: int | None,
    ):
        statement = (
            select(Material)
            .options(selectinload(Material.recommendations), selectinload(Material.topic))
            .order_by(Material.id)
        )
        if status is not None:
            statement = statement.where(Material.status == status)
        if public_only:
            statement = statement.join(Material.topic).where(
                Material.status == MaterialStatus.ACTIVE,
                Material.is_official.is_(True),
                Topic.is_public.is_(True),
            )
        if limit is not None:
            statement = statement.limit(limit)
        return self.session.scalars(statement)


def _sentences(text: str) -> list[str]:
    return [_compact(match.group(0)) for match in SENTENCE_RE.finditer(text or "") if _compact(match.group(0))]


def _compact(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _normalize_text(text: str) -> str:
    from app.search.normalization import normalize_text

    return normalize_text(text)


def _tokens(text: str) -> list[str]:
    from app.search.normalization import tokens

    return tokens(text)
