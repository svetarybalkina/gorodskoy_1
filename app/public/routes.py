from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.enums import MaterialType
from app.db.models import Category, Material
from app.db.repositories import MaterialRepository, TaxonomyRepository
from app.db.session import get_db_session
from app.search import SearchService
from app.services.recommendations import RecommendationExtractionService


router = APIRouter(tags=["public"])
templates = Jinja2Templates(directory="app/templates")


SAFE_PROBLEM_QUERY_TEXT = "[текст запроса не сохранен; зафиксирована только отметка о неподходящем материале]"
DETAILED_ANIMAL_CATEGORY_SLUGS = {
    "stray_dogs",
    "animal_capture",
    "aggressive_animals",
    "shelters",
    "pet_rules",
}


@dataclass(frozen=True)
class LegalPage:
    slug: str
    title: str
    lead: str
    sections: tuple[tuple[str, str], ...]


LEGAL_PAGES: dict[str, LegalPage] = {
    "terms": LegalPage(
        slug="terms",
        title="Пользовательское соглашение",
        lead="Настоящее соглашение описывает условия использования информационно-поискового сервиса справочного характера.",
        sections=(
            (
                "1. Общие положения",
                "[НАЗВАНИЕ СЕРВИСА] является информационно-поисковым сервисом справочного характера. "
                "Сервис помогает находить официально опубликованные материалы по ЖКХ, "
                "но не является официальным ресурсом администрации и не принимает обращения граждан.",
            ),
            (
                "2. Оператор сервиса",
                "Оператором сервиса является [ВЛАДЕЛЕЦ / ОПЕРАТОР СЕРВИСА]. Домен сервиса: [ДОМЕН СЕРВИСА]. "
                "Контакты для обращений: [EMAIL ДЛЯ ОБРАЩЕНИЙ], [КОНТАКТЫ ОПЕРАТОРА].",
            ),
            (
                "3. Характер информации",
                "Материалы показываются как справочная выдача по смысловому сходству с запросом. "
                "Сервис не формирует новые ответы от имени администрации, не оказывает юридические услуги "
                "и не гарантирует применимость материала к конкретной жизненной ситуации.",
            ),
            (
                "4. Ограничения использования",
                "Пользователь обязуется не пытаться получить доступ к закрытым разделам, не нарушать работу сервиса "
                "и не размещать через формы обратной связи запрещенную законом информацию.",
            ),
        ),
    ),
    "privacy": LegalPage(
        slug="privacy",
        title="Политика конфиденциальности и обработки персональных данных",
        lead="Политика описывает, какие данные может обрабатывать сервис и зачем это нужно для работы MVP.",
        sections=(
            (
                "1. Оператор и контакты",
                "Оператор обработки данных: [ВЛАДЕЛЕЦ / ОПЕРАТОР СЕРВИСА]. Контакты оператора: "
                "[КОНТАКТЫ ОПЕРАТОРА]. Email для обращений: [EMAIL ДЛЯ ОБРАЩЕНИЙ].",
            ),
            (
                "2. Категории данных",
                "Сервис может технически обрабатывать поисковый запрос, сведения о выбранной категории, "
                "служебные данные HTTP-запроса и cookies, если они нужны для работы сайта и аналитики.",
            ),
            (
                "3. Принципы обработки",
                "Кнопка 'Ответ не подошел' не сохраняет сырой текст запроса. "
                "В проблемный запрос записывается только безопасная служебная отметка.",
            ),
            (
                "4. Правовые основания",
                "Обработка выполняется в рамках законодательства РФ, включая 152-ФЗ, 149-ФЗ, 8-ФЗ и 59-ФЗ, "
                "с учетом справочного характера сервиса.",
            ),
        ),
    ),
    "personal-data-consent": LegalPage(
        slug="personal-data-consent",
        title="Согласие на обработку персональных данных",
        lead="Текст согласия применяется к действиям пользователя на сайте, если такие действия требуют обработки данных.",
        sections=(
            (
                "1. Согласие пользователя",
                "Используя [НАЗВАНИЕ СЕРВИСА] на домене [ДОМЕН СЕРВИСА], пользователь выражает согласие "
                "на обработку данных оператором [ВЛАДЕЛЕЦ / ОПЕРАТОР СЕРВИСА] в целях работы сервиса.",
            ),
            (
                "2. Состав действий",
                "Обработка может включать сбор, запись, систематизацию, хранение, уточнение, обезличивание, "
                "блокирование и удаление данных в пределах, необходимых для работы MVP.",
            ),
            (
                "3. Ограничение сохранения запросов",
                "При нажатии 'Ответ не подошел' сервис не сохраняет сырой текст проблемного пользовательского запроса. "
                "Для анализа качества выдачи используется безопасная служебная запись.",
            ),
            (
                "4. Отзыв согласия",
                "Пользователь может направить обращение об отзыве согласия на [EMAIL ДЛЯ ОБРАЩЕНИЙ].",
            ),
        ),
    ),
    "disclaimer": LegalPage(
        slug="disclaimer",
        title="Дисклеймер о справочном характере сервиса",
        lead="Этот документ описывает ограничения информационно-поискового сервиса справочного характера.",
        sections=(
            (
                "1. Неофициальный статус",
                "[НАЗВАНИЕ СЕРВИСА] не является официальным ресурсом администрации, органа власти или муниципальной службы.",
            ),
            (
                "2. Справочная выдача",
                "Выдача строится по смысловому сходству с запросом и может быть неточной. "
                "Официальный текст материала не редактируется сервисом, кроме точечного скрытия подтвержденных персональных данных.",
            ),
            (
                "3. Обращения граждан",
                "Сервис не принимает обращения граждан по 59-ФЗ. Для официального обращения нужно использовать "
                "официальные каналы соответствующего органа или организации.",
            ),
        ),
    ),
    "cookies": LegalPage(
        slug="cookies",
        title="Согласие на cookies и аналитику",
        lead="Этот документ фиксирует рабочие условия использования cookies и аналитики в MVP.",
        sections=(
            (
                "1. Использование cookies",
                "[НАЗВАНИЕ СЕРВИСА] может использовать технические cookies для работы сайта на домене [ДОМЕН СЕРВИСА].",
            ),
            (
                "2. Аналитика",
                "В будущем сервис может использовать обезличенную аналитику посещений, если это не противоречит "
                "политике конфиденциальности и настройкам оператора.",
            ),
            (
                "3. Отказ",
                "Пользователь может ограничить cookies средствами браузера. Некоторые функции сайта после этого могут работать иначе.",
            ),
        ),
    ),
    "moderation": LegalPage(
        slug="moderation",
        title="Политика модерации и удаления запрещенной информации",
        lead="Политика описывает подход к удалению запрещенной информации и спорных материалов.",
        sections=(
            (
                "1. Запрещенная информация",
                "Сервис не предназначен для публикации пользовательского контента. Если запрещенная информация обнаружена "
                "в материалах или служебных данных, оператор принимает меры по ограничению доступа или удалению.",
            ),
            (
                "2. Персональные данные",
                "Очевидные персональные данные должны обезличиваться до публичной выдачи. Спорные случаи направляются "
                "на ручную проверку администратору.",
            ),
            (
                "3. Обращения об удалении",
                "Сообщения о запрещенной информации и запросы на удаление можно направлять на [EMAIL ДЛЯ ОБРАЩЕНИЙ] "
                "или через [КОНТАКТЫ ОПЕРАТОРА].",
            ),
        ),
    ),
}


POPULAR_QUERIES = [
    "Почему нет отопления?",
    "Что делать, если нет горячей воды?",
    "Куда жаловаться на управляющую компанию?",
    "Куда обращаться по начислениям в квитанции?",
    "Что делать, если грязно в подъезде?",
    "Куда обращаться по дворовой территории?",
    "Куда обращаться по бродячим собакам?",
    "Что делать, если во дворе агрессивная собака?",
]


def material_type_label(material_type: MaterialType) -> str:
    return {
        MaterialType.OFFICIAL_ANSWER: "Официальный ответ",
        MaterialType.OFFICIAL_POST: "Официальная публикация",
    }[material_type]


def format_date(value: datetime) -> str:
    return value.strftime("%d.%m.%Y")


def public_category_name(category: Category) -> str:
    if category.slug in DETAILED_ANIMAL_CATEGORY_SLUGS:
        return "Животные"
    return category.name


def public_material_category_name(material: Material) -> str:
    if material.category is None:
        return material.topic.name
    return public_category_name(material.category)


templates.env.filters["material_type_label"] = material_type_label
templates.env.filters["date_ru"] = format_date
templates.env.filters["public_category_name"] = public_category_name
templates.env.filters["public_material_category_name"] = public_material_category_name


def public_context(settings: Settings) -> dict[str, object]:
    return {
        "service_name": settings.service_name,
        "legal_pages": LEGAL_PAGES.values(),
    }


@router.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    db: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    taxonomy = TaxonomyRepository(db)
    return templates.TemplateResponse(
        request,
        "public/index.html",
        {
            **public_context(settings),
            "topics": taxonomy.list_public_topics(),
            "categories": taxonomy.list_public_categories(),
            "popular_queries": POPULAR_QUERIES,
        },
    )


@router.get("/search", response_class=HTMLResponse)
async def search(
    request: Request,
    q: str = "",
    category_id: int | None = None,
    db: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    taxonomy = TaxonomyRepository(db)
    selected_category = (
        taxonomy.get_public_category_by_id(category_id) if category_id is not None else None
    )
    if category_id is not None and selected_category is None:
        raise HTTPException(status_code=404, detail="Category not found")

    search_response = SearchService(db).search_public(query=q, category_id=category_id)
    if search_response.problem_query_saved:
        db.commit()
    return templates.TemplateResponse(
        request,
        "public/search.html",
        {
            **public_context(settings),
            "query": q.strip(),
            "categories": taxonomy.list_public_categories(),
            "selected_category": selected_category,
            "search_items": search_response.items,
            "materials": search_response.materials,
            "recommendations": search_response.recommendations,
            "match_level": search_response.match_level,
            "has_strict_question_match": search_response.has_strict_question_match,
            "problem_query_saved": search_response.problem_query_saved,
        },
    )


@router.get("/materials/{material_id}", response_class=HTMLResponse)
async def material_detail(
    request: Request,
    material_id: int,
    db: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    materials = MaterialRepository(db)
    material = materials.get_public_material(material_id)
    if material is None:
        raise HTTPException(status_code=404, detail="Material not found")

    similar_materials = materials.list_similar_public(material)
    recommendations = RecommendationExtractionService(db).list_for_material(material.id)
    return templates.TemplateResponse(
        request,
        "public/material.html",
        {
            **public_context(settings),
            "material": material,
            "recommendations": recommendations,
            "similar_materials": similar_materials,
            "query": request.query_params.get("q", "").strip(),
        },
    )


@router.post("/materials/{material_id}/not-helpful", response_class=HTMLResponse)
async def mark_not_helpful(
    request: Request,
    material_id: int,
    db: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    materials = MaterialRepository(db)
    material = materials.get_public_material(material_id)
    if material is None:
        raise HTTPException(status_code=404, detail="Material not found")

    form_data = parse_qs((await request.body()).decode("utf-8"))
    similar_material_ids = form_data.get("similar_material_ids", [""])[0]
    parsed_similar_ids = [
        int(value)
        for value in similar_material_ids.split(",")
        if value.strip().isdigit()
    ]
    original_query = form_data.get("query", [""])[0]
    SearchService(db).record_not_helpful(
        original_query=original_query or SAFE_PROBLEM_QUERY_TEXT,
        material=material,
        similar_material_ids=parsed_similar_ids,
    )
    db.commit()

    return templates.TemplateResponse(
        request,
        "public/not_helpful.html",
        {
            **public_context(settings),
            "material": material,
        },
    )


@router.get("/legal/{slug}", response_class=HTMLResponse)
async def legal_page(
    request: Request,
    slug: str,
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    page = LEGAL_PAGES.get(slug)
    if page is None:
        raise HTTPException(status_code=404, detail="Legal page not found")
    return templates.TemplateResponse(
        request,
        "legal/page.html",
        {
            **public_context(settings),
            "page": page,
        },
    )
