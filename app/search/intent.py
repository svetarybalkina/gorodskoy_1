from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.search.normalization import normalize_text


class QueryIntent(StrEnum):
    COMPLAINT_ESCALATION = "complaint_escalation"
    SERVICE_REQUEST = "service_request"
    EMERGENCY = "emergency"
    BILLING = "billing"
    INFORMATION = "information"
    GENERAL = "general"


class ActionKind(StrEnum):
    SELF_SERVICE = "self_service"
    OVERSIGHT = "oversight"
    EMERGENCY_CONTACT = "emergency_contact"
    OPERATOR_CONTACT = "operator_contact"
    INFO_LINK = "info_link"
    GENERAL_ACTION = "general_action"


@dataclass(frozen=True)
class QueryNeed:
    intent: QueryIntent
    category_slug: str | None
    normalized_query: str


def classify_query_need(query: str) -> QueryNeed:
    normalized = normalize_text(query)
    terms = set(normalized.split())
    raw = query.lower().replace("ё", "е")

    category_slug = _category_slug(raw=raw, terms=terms)
    intent = QueryIntent.GENERAL
    if _contains_any(raw, ("авар", "порыв", "опасн", "срочн", "экстренн")):
        intent = QueryIntent.EMERGENCY
    elif _contains_any(raw, ("жалоб", "жаловаться", "пожаловаться", "претенз", "бездейств", "не реагир")):
        intent = QueryIntent.COMPLAINT_ESCALATION
    elif terms & {"квитанция", "начисление", "перерасчет", "оплата", "счет"}:
        intent = QueryIntent.BILLING
    elif _contains_any(raw, ("график", "когда", "почему", "где посмотреть", "ознакомиться")):
        intent = QueryIntent.INFORMATION
    elif _contains_any(raw, ("куда обратиться", "обратиться", "заявк", "почин", "убрать")) or terms & {
        "отопление",
        "вода",
        "мусор",
        "подъезд",
    }:
        intent = QueryIntent.SERVICE_REQUEST

    return QueryNeed(intent=intent, category_slug=category_slug, normalized_query=normalized)


def classify_action_kind(text: str) -> ActionKind:
    raw = text.lower().replace("ё", "е")
    if _contains_any(raw, ("еддс", "аварийн", "аварийно-диспетчерск")):
        return ActionKind.EMERGENCY_CONTACT
    if _contains_any(
        raw,
        (
            "жилищн",
            "госжилинспекц",
            "министерств",
            "управлени",
            "департамент",
            "прокуратур",
            "контрольн",
            "надзорн",
        ),
    ):
        return ActionKind.OVERSIGHT
    if _contains_any(raw, ("региональн", "ресурсоснабжающ", "водоканал", "теплосет", "оператор")):
        return ActionKind.OPERATOR_CONTACT
    if _contains_any(raw, ("управляющ", " ук ", "диспетчерск", "обслуживающ")):
        return ActionKind.SELF_SERVICE
    if _contains_any(raw, ("сайт", "график", "документ", "ссылка", "ознакомиться")):
        return ActionKind.INFO_LINK
    return ActionKind.GENERAL_ACTION


def action_score_multiplier(need: QueryNeed, action_kind: str | None) -> float:
    try:
        kind = ActionKind(action_kind or ActionKind.GENERAL_ACTION)
    except ValueError:
        kind = ActionKind.GENERAL_ACTION

    if need.intent == QueryIntent.COMPLAINT_ESCALATION:
        if kind == ActionKind.OVERSIGHT:
            return 2.3
        if kind == ActionKind.SELF_SERVICE:
            return 0.05
    if need.intent == QueryIntent.EMERGENCY:
        if kind == ActionKind.EMERGENCY_CONTACT:
            return 2.2
        if kind == ActionKind.INFO_LINK:
            return 0.4
    if need.intent == QueryIntent.INFORMATION:
        if kind == ActionKind.INFO_LINK:
            return 1.8
    if need.intent == QueryIntent.BILLING:
        if kind in {ActionKind.OPERATOR_CONTACT, ActionKind.OVERSIGHT}:
            return 1.5
    if need.intent == QueryIntent.SERVICE_REQUEST:
        if kind in {ActionKind.SELF_SERVICE, ActionKind.EMERGENCY_CONTACT, ActionKind.OPERATOR_CONTACT}:
            return 1.35
    return 1.0


def is_action_useful_for_need(need: QueryNeed, action_kind: str | None) -> bool:
    try:
        kind = ActionKind(action_kind or ActionKind.GENERAL_ACTION)
    except ValueError:
        kind = ActionKind.GENERAL_ACTION
    if need.intent == QueryIntent.COMPLAINT_ESCALATION:
        return kind == ActionKind.OVERSIGHT
    if need.intent == QueryIntent.EMERGENCY:
        return kind == ActionKind.EMERGENCY_CONTACT
    return True


def _category_slug(*, raw: str, terms: set[str]) -> str | None:
    if terms & {"ук", "жэк", "тсж", "управляющий"} or "управляющ" in raw:
        return "management_company"
    if terms & {"отопление", "батарея", "тепло"}:
        return "heating"
    if terms & {"вода", "водоснабжение", "канализация"}:
        return "water"
    if terms & {"мусор", "отход", "контейнер", "тко"}:
        return "waste"
    if terms & {"квитанция", "начисление", "перерасчет", "оплата"}:
        return "bills"
    if terms & {"подъезд", "лифт", "домофон"}:
        return "entrance"
    if terms & {"двор", "территория", "площадка"}:
        return "yard"
    if terms & {"собака", "животное", "отлов"}:
        return "animals"
    return None


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)
