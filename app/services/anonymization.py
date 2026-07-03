from __future__ import annotations

import re
from dataclasses import dataclass


PHONE_REPLACEMENT = "[телефон скрыт]"
EMAIL_REPLACEMENT = "[email скрыт]"
APARTMENT_REPLACEMENT = "[квартира скрыта]"
ADDRESS_REPLACEMENT = "[адрес скрыт]"
APPEAL_REPLACEMENT = "[номер обращения скрыт]"
PROFILE_REPLACEMENT = "[ссылка на профиль скрыта]"
SALUTATION_ADDRESSEE_REPLACEMENT = ""
OLD_SALUTATION_ADDRESSEE_REPLACEMENT = "[персональное обращение скрыто]"


@dataclass(frozen=True)
class RedactionMatch:
    redaction_type: str
    original_fragment: str
    replacement: str
    start: int
    end: int
    is_confirmed: bool = True
    needs_review: bool = False
    review_code: str | None = None


@dataclass(frozen=True)
class PersonNameCandidate:
    detected_name: str
    context: str
    start: int
    end: int


@dataclass(frozen=True)
class ReviewCase:
    code: str
    description: str
    fragment: str


@dataclass(frozen=True)
class AnonymizationResult:
    text: str
    redactions: list[RedactionMatch]
    person_names: list[PersonNameCandidate]
    review_cases: list[ReviewCase]

    @property
    def needs_review(self) -> bool:
        return bool(self.person_names or self.review_cases)

    @property
    def has_personal_data(self) -> bool:
        return bool(self.redactions or self.person_names or self.review_cases)


PHONE_PATTERN = re.compile(
    r"(?<!\d)(?:\+7|8)[\s\-.(]*\d{3}[\s\-.)]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}(?!\d)"
)
EMAIL_PATTERN = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+(?![\w.+-])", re.IGNORECASE)
APARTMENT_PATTERN = re.compile(r"\b(?:кв\.?|квартира|квартиры|квартиру)\s*№?\s*\d+[А-ЯA-Z]?\b", re.IGNORECASE)
ADDRESS_PATTERN = re.compile(
    r"\b(?:г\.\s*[А-ЯЁа-яёA-Za-z .-]+,\s*)?"
    r"(?:ул\.?|улица|проспект|пр-т|пер\.?|переулок|бульвар|шоссе|площадь)\s+"
    r"[А-ЯЁа-яёA-Za-z0-9 .-]{2,80}?"
    r"(?:,\s*|\s+)(?:д\.?|дом)\s*№?\s*\d+[А-ЯЁа-яёA-Za-z0-9/-]*"
    r"(?:,\s*|\s+)?(?:(?:к\.?|корп\.?|корпус|стр\.?|строение)\s*№?\s*\d+[А-ЯЁа-яёA-Za-z0-9/-]*)?"
    r"(?:,\s*|\s+)?(?:(?:кв\.?|квартира)\s*№?\s*\d+[А-ЯA-Z]?)?",
    re.IGNORECASE,
)
APPEAL_PATTERN = re.compile(
    r"\b(?:номер\s+)?(?:обращени[еяю]|заявк[аи]|жалоб[аы]|регистрационный\s+номер)\s*"
    r"(?:№|N|Nº|#)?\s*[А-ЯЁA-Z0-9][А-ЯЁA-Z0-9/-]{3,}\b",
    re.IGNORECASE,
)
PROFILE_URL_PATTERN = re.compile(
    r"(?<![\w/@])(?:https?://)?(?:www\.)?"
    r"(?:t\.me|telegram\.me|vk\.com|vkontakte\.ru|ok\.ru|odnoklassniki\.ru)/"
    r"[A-Za-z0-9_.-]{3,}(?:/[A-Za-z0-9_.?=&%+-]*)?",
    re.IGNORECASE,
)
BARE_USERNAME_PATTERN = re.compile(r"(?<![\w.+-])@[A-Za-z0-9_][A-Za-z0-9_.-]{2,}(?![\w.-])")
SALUTATION_ADDRESSEE_PATTERN = re.compile(
    r"^(?P<leading>\s*)"
    r"(?P<addressee>(?=[\s\S]{1,160}?Здравствуйте)(?=[\s\S]{1,160},)[\s\S]{1,160}?)"
    r"(?P<greeting>Здравствуйте)",
    re.IGNORECASE,
)
FULL_NAME_PATTERN = re.compile(
    r"\b[А-ЯЁ][а-яё]{2,}\s+[А-ЯЁ][а-яё]{2,}\s+"
    r"[А-ЯЁ][а-яё]{2,}(?:вич|вича|вичем|вичу|вна|вны|вне|вну|вной|ична|ичны|ичне|ичну|ичной)\b"
)
INITIALS_NAME_PATTERN = re.compile(r"\b[А-ЯЁ][а-яё]{2,}\s+[А-ЯЁ]\.\s*[А-ЯЁ]\.")

OFFICIAL_CONTEXT_MARKERS = {
    "администрац",
    "комитет",
    "департамент",
    "отдел",
    "мфц",
    "диспетчер",
    "аварийн",
    "управляющ",
    "ук ",
    "жэк",
    "ресурсоснаб",
    "горячая линия",
    "единая справоч",
    "приемн",
    "официальн",
    "служб",
    "организац",
    "ведомств",
}


def anonymize_text(text: str) -> AnonymizationResult:
    matches: list[RedactionMatch] = []
    review_cases: list[ReviewCase] = []

    salutation_match = SALUTATION_ADDRESSEE_PATTERN.search(text)
    if salutation_match and not _is_redacted_salutation_addressee(salutation_match.group("addressee")):
        matches.append(
            RedactionMatch(
                redaction_type="salutation_addressee",
                original_fragment=(salutation_match.group("leading") + salutation_match.group("addressee")).strip(),
                replacement=SALUTATION_ADDRESSEE_REPLACEMENT,
                start=salutation_match.start("leading"),
                end=salutation_match.end("addressee"),
            )
        )

    for pattern, redaction_type, replacement in (
        (ADDRESS_PATTERN, "address", ADDRESS_REPLACEMENT),
        (APARTMENT_PATTERN, "apartment", APARTMENT_REPLACEMENT),
        (APPEAL_PATTERN, "appeal_number", APPEAL_REPLACEMENT),
        (PROFILE_URL_PATTERN, "personal_profile", PROFILE_REPLACEMENT),
        (BARE_USERNAME_PATTERN, "personal_profile", PROFILE_REPLACEMENT),
        (EMAIL_PATTERN, "email", EMAIL_REPLACEMENT),
        (PHONE_PATTERN, "phone", PHONE_REPLACEMENT),
    ):
        for match in pattern.finditer(text):
            original = match.group(0)
            if _has_official_context(text, match.start(), match.end()):
                continue
            if any(match.start() < item.end and match.end() > item.start for item in matches):
                continue
            needs_review = redaction_type in {"phone", "email", "address", "personal_profile"} and not _has_private_context(
                text, match.start(), match.end()
            )
            matches.append(
                RedactionMatch(
                    redaction_type=redaction_type,
                    original_fragment=original,
                    replacement=replacement,
                    start=match.start(),
                    end=match.end(),
                    needs_review=needs_review,
                    review_code="ambiguous_contact" if needs_review else None,
                )
            )
            if needs_review:
                review_cases.append(
                    ReviewCase(
                        code="ambiguous_contact",
                        description="Контактные данные или ссылка скрыты автоматически, но требуют проверки администратора.",
                        fragment=original,
                    )
                )

    redactions = _drop_overlapping(matches)
    anonymized = _apply_redactions(text, redactions)
    person_names = _find_person_names(text)
    return AnonymizationResult(
        text=anonymized,
        redactions=redactions,
        person_names=person_names,
        review_cases=_unique_review_cases(review_cases),
    )


def has_unredacted_salutation_addressee(text: str) -> bool:
    match = SALUTATION_ADDRESSEE_PATTERN.search(text)
    if match is None:
        return False
    return not _is_redacted_salutation_addressee(match.group("addressee"))


def _drop_overlapping(matches: list[RedactionMatch]) -> list[RedactionMatch]:
    result: list[RedactionMatch] = []
    occupied: list[tuple[int, int]] = []
    for match in sorted(matches, key=lambda item: (item.start, -(item.end - item.start))):
        if any(match.start < end and match.end > start for start, end in occupied):
            continue
        result.append(match)
        occupied.append((match.start, match.end))
    return result


def _apply_redactions(text: str, redactions: list[RedactionMatch]) -> str:
    updated = text
    for match in sorted(redactions, key=lambda item: item.start, reverse=True):
        updated = updated[: match.start] + match.replacement + updated[match.end :]
    return updated


def _find_person_names(text: str) -> list[PersonNameCandidate]:
    candidates: list[PersonNameCandidate] = []
    seen: set[str] = set()
    for pattern in (FULL_NAME_PATTERN, INITIALS_NAME_PATTERN):
        for match in pattern.finditer(text):
            detected_name = match.group(0)
            if detected_name in seen:
                continue
            seen.add(detected_name)
            candidates.append(
                PersonNameCandidate(
                    detected_name=detected_name,
                    context=_context(text, match.start(), match.end(), radius=70),
                    start=match.start(),
                    end=match.end(),
                )
            )
    return candidates


def _is_redacted_salutation_addressee(value: str) -> bool:
    normalized = re.sub(r"[\s,!?).]+$", "", value.strip())
    return normalized == OLD_SALUTATION_ADDRESSEE_REPLACEMENT


def _has_official_context(text: str, start: int, end: int) -> bool:
    context = text[max(0, start - 100) : end].lower()
    return any(marker in context for marker in OFFICIAL_CONTEXT_MARKERS)


def _has_private_context(text: str, start: int, end: int) -> bool:
    context = _context(text, start, end).lower()
    private_markers = (
        "мой",
        "моя",
        "мою",
        "моего",
        "личн",
        "профил",
        "страниц",
        "аккаунт",
        "жител",
        "собственник",
        "заявител",
        "контакт",
        "квартира",
        "кв.",
        "домофон",
    )
    return any(marker in context for marker in private_markers)


def _context(text: str, start: int, end: int, *, radius: int = 90) -> str:
    return text[max(0, start - radius) : min(len(text), end + radius)]


def _unique_review_cases(review_cases: list[ReviewCase]) -> list[ReviewCase]:
    result: list[ReviewCase] = []
    seen: set[tuple[str, str]] = set()
    for item in review_cases:
        key = (item.code, item.fragment)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result
