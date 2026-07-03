from __future__ import annotations

from dataclasses import dataclass

from app.search.normalization import normalize_text


@dataclass(frozen=True)
class MaterialClassification:
    topic_slug: str
    category_slug: str | None
    confidence: int
    matched_group: str


HOUSING_TOPIC = "housing"
TRANSPORT_TOPIC = "transport"
IMPROVEMENT_TOPIC = "improvement"


CATEGORY_ORDER = {
    "animals": 90,
    "heating": 80,
    "water": 80,
    "waste": 75,
    "entrance": 70,
    "bills": 65,
    "management_company": 60,
    "yard": 50,
}

CATEGORY_TIE_PRIORITY = {
    "heating": 80,
    "water": 80,
    "waste": 75,
    "entrance": 70,
    "bills": 65,
    "management_company": 60,
    "yard": 55,
    "animals": 50,
}

MARKERS: dict[str, set[str]] = {
    "transport": {
        "автобус",
        "водитель",
        "маршрут",
        "перевозка",
        "перевозчик",
        "проезд",
        "транспорт",
        "транспортный",
        "электробус",
    },
    "improvement": {
        "асфальт",
        "дорога",
        "дорожный",
        "ливневка",
        "пешеходный",
        "переезд",
        "светофор",
        "тротуар",
        "яма",
    },
    "heating": {
        "батарея",
        "котельная",
        "отопление",
        "радиатор",
        "тепло",
        "теплоснабжение",
    },
    "water": {
        "водоотведение",
        "водоканал",
        "водоснабжение",
        "вода",
        "гвс",
        "горячий",
        "канализация",
        "утечка",
        "хвс",
        "холодный",
    },
    "waste": {
        "вывоз",
        "контейнер",
        "мусор",
        "отход",
        "свалка",
        "тбо",
        "тко",
        "урна",
    },
    "entrance": {
        "домофон",
        "лестница",
        "лифт",
        "подъезд",
    },
    "yard": {
        "двор",
        "детский",
        "парковка",
        "площадка",
        "территория",
    },
    "management_company": {
        "диспетчерская",
        "жэк",
        "тсж",
        "ук",
        "управляющий",
    },
    "bills": {
        "квитанция",
        "начисление",
        "оплата",
        "платеж",
        "счет",
    },
    "animals": {
        "агрессивный",
        "безнадзорный",
        "животное",
        "кошка",
        "отлов",
        "собака",
    },
}

REQUIRED_MARKERS: dict[str, set[str]] = {
    "transport": MARKERS["transport"],
    "improvement": MARKERS["improvement"],
    "heating": {
        "батарея",
        "котельная",
        "отопление",
        "радиатор",
        "теплоснабжение",
    },
    "water": {
        "водоотведение",
        "водоканал",
        "водоснабжение",
        "вода",
        "гвс",
        "канализация",
        "утечка",
        "хвс",
    },
    "waste": {
        "контейнер",
        "мусор",
        "отход",
        "свалка",
        "тбо",
        "тко",
        "урна",
    },
    "entrance": MARKERS["entrance"],
    "yard": {
        "двор",
        "парковка",
        "площадка",
        "территория",
    },
    "management_company": MARKERS["management_company"],
    "bills": MARKERS["bills"],
    "animals": {
        "безнадзорный",
        "животное",
        "кошка",
        "отлов",
        "собака",
    },
}

SUPPORT_MARKERS: dict[str, set[str]] = {
    group: MARKERS[group] - REQUIRED_MARKERS.get(group, set())
    for group in MARKERS
}


PHRASES: dict[str, tuple[str, ...]] = {
    "transport": (
        "управление транспорт",
        "управление транспортный",
        "пассажирский перевозка",
        "общественный транспорт",
    ),
    "improvement": (
        "ремонт дорога",
        "автомобильный дорога",
        "асфальтовый покрытие",
    ),
    "heating": (
        "отопительный сезон",
        "модульный котельная",
        "тепловой сеть",
    ),
    "water": (
        "горячий вода",
        "холодный вода",
        "порыв водопровод",
    ),
    "waste": (
        "вывоз мусор",
        "мусорный контейнер",
        "твердый коммунальный отход",
    ),
    "management_company": (
        "управляющий компания",
    ),
}


ROAD_CONTEXT = {
    "автомобильный",
    "асфальт",
    "дорога",
    "дорожный",
    "покрытие",
    "ул",
    "улица",
    "тротуар",
}


def classify_material_text(text: str) -> MaterialClassification:
    normalized = normalize_text(text)
    token_set = set(normalized.split())
    if not token_set:
        return MaterialClassification(HOUSING_TOPIC, "other", 0, "empty")

    topic_scores = {
        TRANSPORT_TOPIC: _score("transport", normalized, token_set),
        IMPROVEMENT_TOPIC: _score("improvement", normalized, token_set),
    }
    category_scores = {
        category_slug: _score(category_slug, normalized, token_set)
        for category_slug in CATEGORY_ORDER
    }

    if _is_kotelnaya_street_context(token_set):
        category_scores["heating"] = 0

    best_topic, best_topic_score = max(topic_scores.items(), key=lambda item: item[1])
    best_category, best_category_score = max(
        category_scores.items(),
        key=lambda item: (item[1], CATEGORY_TIE_PRIORITY.get(item[0], 0)),
    )

    if best_topic_score > 0 and _wins_topic(best_topic, best_topic_score, best_category_score):
        return MaterialClassification(
            topic_slug=best_topic,
            category_slug=None,
            confidence=min(best_topic_score, 100),
            matched_group=best_topic,
        )

    if best_category_score > 0:
        return MaterialClassification(
            topic_slug=HOUSING_TOPIC,
            category_slug=best_category,
            confidence=min(best_category_score, 100),
            matched_group=best_category,
        )

    return MaterialClassification(HOUSING_TOPIC, "other", 0, "other")


def _score(group: str, normalized: str, token_set: set[str]) -> int:
    score = 0
    required_hits = token_set & REQUIRED_MARKERS.get(group, set())
    phrase_hits = 0
    for phrase in PHRASES.get(group, ()):
        if phrase in normalized:
            phrase_hits += 1
    if not required_hits and not phrase_hits:
        return 0
    score += len(required_hits) * 20
    score += len(token_set & SUPPORT_MARKERS.get(group, set())) * 5
    score += phrase_hits * 30
    return score


def _wins_topic(topic_slug: str, topic_score: int, best_category_score: int) -> bool:
    if topic_slug == TRANSPORT_TOPIC:
        return topic_score >= best_category_score
    if topic_slug == IMPROVEMENT_TOPIC:
        return topic_score >= best_category_score
    return False


def _is_kotelnaya_street_context(token_set: set[str]) -> bool:
    return "котельная" in token_set and bool(token_set & ROAD_CONTEXT)
