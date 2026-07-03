from __future__ import annotations

import re
from functools import lru_cache

try:
    import pymorphy3
except ImportError:  # pragma: no cover - used only before dependencies are installed
    pymorphy3 = None


TOKEN_RE = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)

CATEGORY_MARKERS: dict[str, set[str]] = {
    "heating": {"отопление", "батарея", "тепло", "теплоснабжение", "котельная"},
    "water": {"вода", "горячий вода", "холодный вода", "гвс", "хвс", "канализация"},
    "entrance": {"подъезд", "лестница", "лифт", "домофон"},
    "yard": {"двор", "парковка", "площадка", "территория"},
    "waste": {"мусор", "отход", "тко", "тбо", "контейнер", "свалка", "урна"},
    "management_company": {"управляющий компания", "ук", "жэк", "диспетчерская"},
    "bills": {"квитанция", "начисление", "платеж", "оплата"},
    "animals": {"животное", "собака", "кошка", "отлов", "безнадзорный", "агрессивный"},
    "other": {"другое", "вопрос", "обращение"},
}

SYNONYMS: dict[str, str] = {
    "жкх": "жилищный коммунальный хозяйство",
    "ук": "управляющий компания",
    "жэк": "управляющий компания",
    "гвс": "горячий вода",
    "хвс": "холодный вода",
    "платежка": "квитанция",
    "платёжка": "квитанция",
    "собаки": "собака",
}

STOP_WORDS = {
    "а",
    "в",
    "во",
    "и",
    "или",
    "к",
    "ко",
    "на",
    "не",
    "нет",
    "по",
    "с",
    "со",
    "у",
    "что",
    "если",
    "куда",
    "как",
    "когда",
    "почему",
    "делать",
    "сделать",
}


@lru_cache(maxsize=1)
def _morph():
    if pymorphy3 is None:
        return None
    return pymorphy3.MorphAnalyzer()


@lru_cache(maxsize=20000)
def normalize_token(token: str) -> str:
    lowered = token.lower().replace("ё", "е")
    if lowered in SYNONYMS:
        return SYNONYMS[lowered]
    morph = _morph()
    if morph is None:
        return _fallback_stem(lowered)
    return morph.parse(lowered)[0].normal_form.replace("ё", "е")


def normalize_text(text: str) -> str:
    normalized_tokens: list[str] = []
    for token in TOKEN_RE.findall(text.lower().replace("ё", "е")):
        if token in STOP_WORDS:
            continue
        normalized = normalize_token(token)
        normalized_tokens.extend(part for part in normalized.split() if part and part not in STOP_WORDS)
    return " ".join(normalized_tokens)


def tokens(text: str) -> list[str]:
    return [token for token in normalize_text(text).split() if token]


def category_marker_text(category_slug: str) -> str:
    markers = CATEGORY_MARKERS.get(category_slug, set())
    return normalize_text(" ".join(sorted(markers)))


def guess_category_slug(text: str) -> str | None:
    normalized = normalize_text(text)
    if not normalized:
        return None
    normalized_terms = set(normalized.split())
    best_slug: str | None = None
    best_score = 0
    for slug, markers in CATEGORY_MARKERS.items():
        marker_terms = set(normalize_text(" ".join(markers)).split())
        score = len(normalized_terms & marker_terms)
        if score > best_score:
            best_slug = slug
            best_score = score
    return best_slug if best_score else None


def _fallback_stem(token: str) -> str:
    for suffix in (
        "иями",
        "ями",
        "ами",
        "ого",
        "ему",
        "ыми",
        "ими",
        "ой",
        "ая",
        "ое",
        "ые",
        "ий",
        "ый",
        "ом",
        "ем",
        "ах",
        "ях",
        "ам",
        "ям",
        "ов",
        "ев",
        "ей",
        "ой",
        "а",
        "я",
        "ы",
        "и",
        "е",
        "у",
        "ю",
    ):
        if len(token) > len(suffix) + 3 and token.endswith(suffix):
            return token[: -len(suffix)]
    return token
