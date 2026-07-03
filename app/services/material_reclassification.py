from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.enums import MaterialStatus
from app.db.models import Category, Material, Topic
from app.db.repositories import TaxonomyRepository
from app.search import SearchService
from app.services.classification import HOUSING_TOPIC, classify_material_text


@dataclass(frozen=True)
class ReclassificationTransition:
    current_topic: str
    current_category: str | None
    target_topic: str
    target_category: str | None
    count: int


@dataclass(frozen=True)
class ReclassificationResult:
    scanned: int
    would_change: int
    changed: int
    transitions: list[ReclassificationTransition]


class MaterialReclassificationService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def preview(
        self,
        *,
        status: MaterialStatus | None = None,
        batch_id: int | None = None,
        limit: int | None = None,
    ) -> ReclassificationResult:
        return self._run(status=status, batch_id=batch_id, limit=limit, execute=False)

    def execute(
        self,
        *,
        status: MaterialStatus | None = None,
        batch_id: int | None = None,
        limit: int | None = None,
    ) -> ReclassificationResult:
        result = self._run(status=status, batch_id=batch_id, limit=limit, execute=True)
        SearchService(self.session).rebuild_index()
        self.session.flush()
        return result

    def _run(
        self,
        *,
        status: MaterialStatus | None,
        batch_id: int | None,
        limit: int | None,
        execute: bool,
    ) -> ReclassificationResult:
        taxonomy = TaxonomyRepository(self.session)
        topic_by_slug = {topic.slug: topic for topic in self.session.scalars(select(Topic))}
        category_by_key = {
            (category.topic_id, category.slug): category
            for category in self.session.scalars(select(Category))
        }
        transition_counts: Counter[tuple[str, str | None, str, str | None]] = Counter()
        scanned = 0
        changed = 0

        for material in self._materials(status=status, batch_id=batch_id, limit=limit):
            scanned += 1
            classification = classify_material_text(material.public_text or material.original_text or "")
            target_topic = topic_by_slug.get(classification.topic_slug)
            if target_topic is None:
                continue

            target_category = None
            target_category_slug = None
            if classification.topic_slug == HOUSING_TOPIC:
                target_category_slug = classification.category_slug or "other"
                target_category = category_by_key.get((target_topic.id, target_category_slug))
                if target_category is None and target_category_slug == "other":
                    target_category = taxonomy.get_category(topic_id=target_topic.id, slug="other")
                    if target_category is not None:
                        category_by_key[(target_topic.id, target_category.slug)] = target_category
                    else:
                        target_category_slug = None

            current_topic_slug = material.topic.slug if material.topic is not None else ""
            current_category_slug = material.category.slug if material.category is not None else None
            if target_category is not None:
                target_category_slug = target_category.slug
            if material.topic_id == target_topic.id and material.category_id == (target_category.id if target_category else None):
                continue

            transition_counts[
                (
                    current_topic_slug,
                    current_category_slug,
                    target_topic.slug,
                    target_category_slug,
                )
            ] += 1
            changed += 1
            if execute:
                material.topic_id = target_topic.id
                material.category_id = target_category.id if target_category else None

        transitions = [
            ReclassificationTransition(
                current_topic=current_topic,
                current_category=current_category,
                target_topic=target_topic,
                target_category=target_category,
                count=count,
            )
            for (current_topic, current_category, target_topic, target_category), count in transition_counts.items()
        ]
        transitions.sort(key=lambda item: (-item.count, item.current_topic, item.current_category or ""))
        return ReclassificationResult(
            scanned=scanned,
            would_change=changed,
            changed=changed if execute else 0,
            transitions=transitions,
        )

    def _materials(
        self,
        *,
        status: MaterialStatus | None,
        batch_id: int | None,
        limit: int | None,
    ):
        statement = (
            select(Material)
            .options(
                selectinload(Material.topic),
                selectinload(Material.category),
            )
            .order_by(Material.id)
        )
        if status is not None:
            statement = statement.where(Material.status == status)
        if batch_id is not None:
            statement = statement.where(Material.import_batch_id == batch_id)
        if limit is not None:
            statement = statement.limit(limit)
        return self.session.scalars(statement)
