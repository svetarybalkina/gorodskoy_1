from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, select

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.db.models import Category, ProblemQuery, ResidentQuestion, Topic
from app.db.repositories import TaxonomyRepository
from app.db.session import SessionLocal
from app.search.normalization import tokens
from app.search.service import SearchResponse, SearchService


CONTROL_QUERIES: tuple[tuple[str, str], ...] = (
    ("entrance", "Грязно в подъезде"),
    ("entrance", "Сломан домофон"),
    ("yard", "Яма во дворе"),
    ("water", "Нет горячей воды"),
    ("water", "Затопило подвал"),
    ("waste", "Не вывозят мусор"),
    ("waste", "Воняет мусором у дома"),
    ("management_company", "Куда жаловаться на управляющую компанию?"),
    ("bills", "Неправильные начисления в квитанции"),
    ("animals", "Во дворе агрессивная собака"),
)


@dataclass(frozen=True)
class QualityCase:
    source: str
    category_slug: str | None
    query: str


@dataclass(frozen=True)
class QualityResult:
    case: QualityCase
    match_level: str
    top_category_slug: str | None
    score: float
    error_class: str
    reasons: tuple[str, ...]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Analyze search relevance quality on control queries, problem queries, and resident questions."
    )
    parser.add_argument(
        "--problem-limit",
        type=int,
        default=20,
        help="How many recent problem queries to analyze.",
    )
    parser.add_argument(
        "--resident-limit",
        type=int,
        default=20,
        help="How many grouped resident question formulations to analyze.",
    )
    args = parser.parse_args()

    with SessionLocal() as session:
        service = SearchService(session)
        taxonomy = TaxonomyRepository(session)
        categories = _public_housing_categories(session)

        cases = list(_control_cases())
        cases.extend(_problem_query_cases(session, limit=args.problem_limit))
        cases.extend(
            _resident_question_cases(
                session,
                categories=categories,
                limit=args.resident_limit,
            )
        )

        results = [
            _evaluate_case(service=service, taxonomy=taxonomy, case=case)
            for case in cases
        ]

    _print_report(results)
    return 0


def _public_housing_categories(session) -> dict[int, Category]:
    rows = session.scalars(
        select(Category)
        .join(Category.topic)
        .where(
            Topic.slug == "housing",
            Category.is_public.is_(True),
            Category.is_confirmed.is_(True),
        )
    )
    return {category.id: category for category in rows}


def _control_cases() -> list[QualityCase]:
    return [
        QualityCase(source="control", category_slug=category_slug, query=query)
        for category_slug, query in CONTROL_QUERIES
    ]


def _problem_query_cases(session, *, limit: int) -> list[QualityCase]:
    rows = session.scalars(
        select(ProblemQuery)
        .order_by(ProblemQuery.created_at.desc(), ProblemQuery.id.desc())
        .limit(limit)
    )
    return [
        QualityCase(
            source="problem_queries",
            category_slug=None,
            query=row.anonymized_text,
        )
        for row in rows
        if row.anonymized_text.strip()
    ]


def _resident_question_cases(
    session,
    *,
    categories: dict[int, Category],
    limit: int,
) -> list[QualityCase]:
    rows = session.execute(
        select(
            ResidentQuestion.category_id,
            ResidentQuestion.anonymized_text,
            func.count(ResidentQuestion.id).label("occurrences"),
        )
        .where(ResidentQuestion.anonymized_text != "")
        .group_by(ResidentQuestion.category_id, ResidentQuestion.anonymized_text)
        .order_by(func.count(ResidentQuestion.id).desc(), ResidentQuestion.id.desc())
        .limit(limit)
    )
    cases: list[QualityCase] = []
    for category_id, query, _occurrences in rows:
        category = categories.get(category_id) if category_id is not None else None
        if not query or len(tokens(query)) == 0:
            continue
        cases.append(
            QualityCase(
                source="resident_questions",
                category_slug=category.slug if category is not None else None,
                query=query,
            )
        )
    return cases


def _evaluate_case(
    *,
    service: SearchService,
    taxonomy: TaxonomyRepository,
    case: QualityCase,
) -> QualityResult:
    category_id = None
    if case.category_slug is not None:
        topic = taxonomy.get_topic_by_slug("housing")
        category = taxonomy.get_category(topic_id=topic.id, slug=case.category_slug) if topic is not None else None
        category_id = category.id if category is not None else None

    response = service.search_public(
        query=case.query,
        category_id=category_id,
        record_problem_query=False,
    )
    top_item = response.items[0] if response.items else None
    top_category_slug = top_item.material.category.slug if top_item and top_item.material.category else None
    score = top_item.score if top_item else 0.0
    error_class, reasons = _classify_issue(case=case, response=response)
    return QualityResult(
        case=case,
        match_level=response.match_level,
        top_category_slug=top_category_slug,
        score=score,
        error_class=error_class,
        reasons=reasons,
    )


def _classify_issue(*, case: QualityCase, response: SearchResponse) -> tuple[str, tuple[str, ...]]:
    if not response.items:
        return "no_results", ("Поиск не вернул материалов.",)

    item = response.items[0]
    signals = item.signals
    query_token_count = len(tokens(case.query))
    reasons: list[str] = []

    if query_token_count <= 1:
        reasons.append("Запрос слишком короткий и держится на одном слабом маркере.")
        return "weak_single_word", tuple(reasons)

    if signals.phrase_overlap == 0.0 and query_token_count >= 2:
        reasons.append("Устойчивая фраза запроса не подтверждается текстом материала, вопросами или рекомендациями.")
        if signals.public_overlap < 0.34:
            return "underweighted_phrase", tuple(reasons)

    if signals.question_overlap < 0.2 and signals.public_overlap >= 0.34:
        reasons.append("Связанные вопросы жителей почти не участвуют в подтверждении результата.")
        if case.source != "control":
            return "weak_question_signal", tuple(reasons)

    if signals.recommendation_overlap > signals.public_overlap + 0.25:
        reasons.append("Рекомендации влияют на результат сильнее, чем официальный текст.")
        return "signal_imbalance", tuple(reasons)

    if signals.public_overlap > signals.question_overlap + 0.5 and signals.question_overlap == 0:
        reasons.append("Результат держится почти только на официальном тексте без поддержки связанными вопросами.")
        if case.source == "resident_questions":
            return "weak_question_signal", tuple(reasons)

    if signals.generic_penalty < 1.0 or item.material.category and item.material.category.slug == "other":
        reasons.append("В топе общий или служебный материал без достаточного тематического доказательства.")
        return "broad_household_context", tuple(reasons)

    if response.match_level in {"low", "none"}:
        reasons.append("Сигналов недостаточно для уверенного совпадения.")
        return "low_confidence", tuple(reasons)

    return "ok", ("Существенных признаков класса ошибки не обнаружено.",)


def _print_report(results: list[QualityResult]) -> None:
    counter = Counter(result.error_class for result in results)
    grouped: dict[str, list[QualityResult]] = defaultdict(list)
    for result in results:
        grouped[result.error_class].append(result)

    print("Search quality analysis")
    print(f"  analyzed_queries: {len(results)}")
    print("  by_error_class:")
    for error_class, count in counter.most_common():
        print(f"    - {error_class}: {count}")

    print()
    for error_class, items in sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0])):
        print(f"## {error_class}")
        for result in items[:8]:
            print(
                "  - "
                f"[{result.case.source}] "
                f"query={result.case.query!r}; "
                f"category={result.case.category_slug or '-'}; "
                f"match={result.match_level}; "
                f"top_category={result.top_category_slug or '-'}; "
                f"score={result.score:.3f}"
            )
            for reason in result.reasons:
                print(f"      {reason}")
        print()


if __name__ == "__main__":
    raise SystemExit(main())
