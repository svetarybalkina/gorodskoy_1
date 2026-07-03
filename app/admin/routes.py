from __future__ import annotations

from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile as StarletteUploadFile

from app.admin.auth import (
    CurrentAdmin,
    ensure_csrf_token,
    login_admin,
    logout_admin,
    redirect_to_login,
    validate_csrf_token,
    verify_admin_credentials,
)
from app.core.config import Settings, get_settings
from app.db.enums import (
    DictionaryCandidateSource,
    DictionaryCandidateStatus,
    DictionaryCandidateType,
    ImportStatus,
    MaterialStatus,
    MaterialType,
    SourceKind,
)
from app.db.repositories import (
    AdminNoteRepository,
    DictionaryCandidateRepository,
    ImportRepository,
    MaterialRepository,
    ProblemQueryRepository,
    ReviewRepository,
    TaxonomyRepository,
)
from app.db.session import get_db_session
from app.importers.telegram_json import TelegramImportError, TelegramJsonImporter
from app.search import SearchService
from app.search.normalization import normalize_text
from app.services.anonymization import has_unredacted_salutation_addressee
from app.services.material_deletion import MaterialDeletionService
from app.services.person_reviews import PersonNameReviewService
from app.services.reprocessing import MaterialReprocessingService

router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "templates"))


def date_ru(value: datetime) -> str:
    return value.strftime("%d.%m.%Y")


def material_type_label(material_type: MaterialType) -> str:
    return {
        MaterialType.OFFICIAL_ANSWER: "Официальный ответ",
        MaterialType.OFFICIAL_POST: "Официальный пост",
    }[material_type]


def status_label(status_value: MaterialStatus | str) -> str:
    status_item = MaterialStatus(status_value)
    return {
        MaterialStatus.DRAFT: "Черновик",
        MaterialStatus.ACTIVE: "Опубликовано",
        MaterialStatus.NEEDS_REVIEW: "На проверке",
        MaterialStatus.ARCHIVED: "Архив",
        MaterialStatus.HIDDEN: "Скрыто",
        MaterialStatus.DUPLICATE: "Дубль",
        MaterialStatus.PENDING_DELETE: "Ожидает удаления",
    }[status_item]


def import_status_label(status_value: ImportStatus | str) -> str:
    status_item = ImportStatus(status_value)
    return {
        ImportStatus.PENDING: "Ожидает обработки",
        ImportStatus.PROCESSING: "Обрабатывается",
        ImportStatus.COMPLETED: "Завершен",
        ImportStatus.COMPLETED_WITH_ERRORS: "Завершен с ошибками",
        ImportStatus.FAILED: "Ошибка",
    }[status_item]


def source_kind_label(kind_value: SourceKind | str) -> str:
    kind_item = SourceKind(kind_value)
    return {
        SourceKind.OFFICIAL_BOT: "Официальный бот",
        SourceKind.OFFICIAL_CHANNEL: "Официальный канал",
        SourceKind.WEBSITE: "Сайт",
        SourceKind.TELEGRAM_BOT: "Telegram-бот",
    }[kind_item]


def dictionary_candidate_status_label(status_value: DictionaryCandidateStatus | str) -> str:
    status_item = DictionaryCandidateStatus(status_value)
    return {
        DictionaryCandidateStatus.PENDING: "Ожидает решения",
        DictionaryCandidateStatus.APPROVED: "Подтверждено",
        DictionaryCandidateStatus.REJECTED: "Отклонено",
    }[status_item]


def dictionary_candidate_type_label(type_value: DictionaryCandidateType | str) -> str:
    type_item = DictionaryCandidateType(type_value)
    return {
        DictionaryCandidateType.MARKER: "Маркер",
        DictionaryCandidateType.SYNONYM: "Синоним",
        DictionaryCandidateType.QUESTION_VARIANT: "Вариант формулировки",
        DictionaryCandidateType.CATEGORY: "Категория",
    }[type_item]


def dictionary_candidate_source_label(source_value: DictionaryCandidateSource | str) -> str:
    source_item = DictionaryCandidateSource(source_value)
    return {
        DictionaryCandidateSource.SEARCH: "Поиск",
        DictionaryCandidateSource.IMPORT: "Импорт",
    }[source_item]


templates.env.filters["date_ru"] = date_ru
templates.env.filters["material_type_label"] = material_type_label
templates.env.filters["status_label"] = status_label
templates.env.filters["import_status_label"] = import_status_label
templates.env.filters["source_kind_label"] = source_kind_label
templates.env.filters["dictionary_candidate_status_label"] = dictionary_candidate_status_label
templates.env.filters["dictionary_candidate_type_label"] = dictionary_candidate_type_label
templates.env.filters["dictionary_candidate_source_label"] = dictionary_candidate_source_label


def admin_template_context(request: Request, settings: Settings, admin_user: str | None = None) -> dict:
    return {
        "request": request,
        "service_name": settings.service_name,
        "admin_user": admin_user,
        "csrf_token": ensure_csrf_token(request),
        "default_password_warning": settings.admin_password == "change_me",
            "statuses": list(MaterialStatus),
            "candidate_statuses": list(DictionaryCandidateStatus),
        }


def parse_status(value: str | None) -> MaterialStatus | None:
    if not value:
        return None
    try:
        return MaterialStatus(value)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown status") from exc


def parse_optional_int(value: str | None, *, field_name: str) -> int | None:
    if not value:
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid {field_name}") from exc


def parse_candidate_status(value: str | None) -> DictionaryCandidateStatus | None:
    if not value:
        return None
    try:
        return DictionaryCandidateStatus(value)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown candidate status") from exc


def parse_bool_form(value: str | None) -> bool:
    return value in {"1", "true", "on", "yes"}


async def read_urlencoded_form(request: Request) -> dict[str, str]:
    body = (await request.body()).decode("utf-8")
    parsed = parse_qs(body, keep_blank_values=True)
    return {key: values[-1] if values else "" for key, values in parsed.items()}


@router.get("", response_class=HTMLResponse)
async def admin_index(request: Request) -> RedirectResponse:
    if request.session.get("admin_user"):
        return RedirectResponse("/admin/materials", status_code=status.HTTP_303_SEE_OTHER)
    return redirect_to_login()


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, settings: Settings = Depends(get_settings)) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "admin/login.html",
        {
            **admin_template_context(request, settings),
            "error": None,
        },
    )


@router.post("/login", response_class=HTMLResponse)
async def login_submit(request: Request, settings: Settings = Depends(get_settings)) -> Response:
    form = await read_urlencoded_form(request)
    username = form.get("username", "")
    password = form.get("password", "")
    if not verify_admin_credentials(username, password, settings):
        return templates.TemplateResponse(
            request,
            "admin/login.html",
            {
                **admin_template_context(request, settings),
                "error": "Неверный логин или пароль.",
            },
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    login_admin(request, username)
    return RedirectResponse("/admin/materials", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/logout")
async def logout_submit(request: Request) -> RedirectResponse:
    form = await read_urlencoded_form(request)
    validate_csrf_token(request, form.get("csrf_token"))
    logout_admin(request)
    return RedirectResponse("/admin/login", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/materials", response_class=HTMLResponse)
async def materials_list(
    request: Request,
    admin_user: CurrentAdmin,
    db: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    status_filter = parse_status(request.query_params.get("status"))
    category_id = parse_optional_int(request.query_params.get("category_id"), field_name="category_id")
    topic_id = parse_optional_int(request.query_params.get("topic_id"), field_name="topic_id")
    taxonomy = TaxonomyRepository(db)
    materials = MaterialRepository(db).list_admin(
        status=status_filter,
        category_id=category_id,
        topic_id=topic_id,
    )
    return templates.TemplateResponse(
        request,
        "admin/materials.html",
        {
            **admin_template_context(request, settings, admin_user),
            "materials": materials,
            "topics": taxonomy.list_admin_topics(),
            "categories": taxonomy.list_admin_categories(topic_id=topic_id),
            "selected_status": status_filter.value if status_filter else "",
            "selected_category_id": category_id,
            "selected_topic_id": topic_id,
        },
    )


@router.get("/imports", response_class=HTMLResponse)
async def imports_list(
    request: Request,
    admin_user: CurrentAdmin,
    db: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "admin/imports.html",
        {
            **admin_template_context(request, settings, admin_user),
            "batches": ImportRepository(db).list_batches(),
            "source_configured": bool(settings.official_telegram_source_id.strip()),
            "official_source_id": settings.official_telegram_source_id,
            "official_source_name": settings.official_telegram_source_name,
            "official_source_kind": settings.official_telegram_source_kind,
            "error": None,
        },
    )


@router.get("/reviews", response_class=HTMLResponse)
async def reviews_list(
    request: Request,
    admin_user: CurrentAdmin,
    db: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "admin/reviews.html",
        {
            **admin_template_context(request, settings, admin_user),
            "materials": MaterialRepository(db).list_needs_review(),
            "person_name_reviews": ReviewRepository(db).list_pending_person_name_reviews(),
        },
    )


@router.get("/search-quality", response_class=HTMLResponse)
async def search_quality(
    request: Request,
    admin_user: CurrentAdmin,
    db: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    candidate_status = parse_candidate_status(request.query_params.get("candidate_status"))
    return templates.TemplateResponse(
        request,
        "admin/search_quality.html",
        {
            **admin_template_context(request, settings, admin_user),
            "problem_queries": ProblemQueryRepository(db).list_recent(),
            "candidates": DictionaryCandidateRepository(db).list_admin(status=candidate_status),
            "selected_candidate_status": candidate_status.value if candidate_status else "",
        },
    )


@router.get("/categories", response_class=HTMLResponse)
async def categories_list(
    request: Request,
    admin_user: CurrentAdmin,
    db: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    taxonomy = TaxonomyRepository(db)
    topic_id = parse_optional_int(request.query_params.get("topic_id"), field_name="topic_id")
    return templates.TemplateResponse(
        request,
        "admin/categories.html",
        {
            **admin_template_context(request, settings, admin_user),
            "topics": taxonomy.list_admin_topics(),
            "categories": taxonomy.list_admin_categories(topic_id=topic_id),
            "selected_topic_id": topic_id,
        },
    )


@router.post("/imports", response_class=HTMLResponse)
async def imports_upload(
    request: Request,
    admin_user: CurrentAdmin,
    db: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> Response:
    form = await request.form()
    validate_csrf_token(request, str(form.get("csrf_token", "")))
    uploaded_file = form.get("file")
    if not isinstance(uploaded_file, (UploadFile, StarletteUploadFile)):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="JSON file is required")
    content = await uploaded_file.read()
    if not content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="JSON file is empty")
    try:
        result = TelegramJsonImporter(session=db, settings=settings).import_bytes(
            filename=uploaded_file.filename or "telegram-export.json",
            content=content,
        )
    except TelegramImportError as exc:
        db.rollback()
        return templates.TemplateResponse(
            request,
            "admin/imports.html",
            {
                **admin_template_context(request, settings, admin_user),
                "batches": ImportRepository(db).list_batches(),
                "source_configured": bool(settings.official_telegram_source_id.strip()),
                "official_source_id": settings.official_telegram_source_id,
                "official_source_name": settings.official_telegram_source_name,
                "official_source_kind": settings.official_telegram_source_kind,
                "error": str(exc),
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    db.commit()
    return RedirectResponse(f"/admin/imports/{result.batch_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/imports/{batch_id}", response_class=HTMLResponse)
async def import_detail(
    request: Request,
    batch_id: int,
    admin_user: CurrentAdmin,
    db: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    import_repo = ImportRepository(db)
    batch = import_repo.get_batch(batch_id)
    report = import_repo.get_report_for_batch(batch_id)
    if batch is None or report is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return templates.TemplateResponse(
        request,
        "admin/import_detail.html",
        {
            **admin_template_context(request, settings, admin_user),
            "batch": batch,
            "report": report,
            "summary": report.summary,
            "errors": report.errors,
            "reprocess_result": request.query_params.get("reprocess_result"),
        },
    )


@router.get("/imports/{batch_id}/report/download")
async def import_report_download(
    batch_id: int,
    admin_user: CurrentAdmin,
    db: Session = Depends(get_db_session),
) -> FileResponse:
    report = ImportRepository(db).get_report_for_batch(batch_id)
    if report is None or not report.report_file_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    report_path = Path(report.report_file_path)
    if not report_path.exists() or not report_path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return FileResponse(
        report_path,
        media_type="application/json",
        filename=report_path.name,
    )


@router.get("/materials/{material_id}", response_class=HTMLResponse)
async def material_detail(
    request: Request,
    material_id: int,
    admin_user: CurrentAdmin,
    db: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    material = MaterialRepository(db).get_admin_material(material_id)
    if material is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    categories = TaxonomyRepository(db).list_admin_categories(topic_id=material.topic_id)
    delete_preview = MaterialDeletionService(db).preview(material_id)
    return templates.TemplateResponse(
        request,
        "admin/material.html",
        {
            **admin_template_context(request, settings, admin_user),
            "material": material,
            "categories": categories,
            "delete_preview": delete_preview,
            "reprocess_result": request.query_params.get("reprocess_result"),
        },
    )


@router.post("/materials/{material_id}/status")
async def update_material_status(
    request: Request,
    material_id: int,
    admin_user: CurrentAdmin,
    db: Session = Depends(get_db_session),
) -> RedirectResponse:
    form = await read_urlencoded_form(request)
    validate_csrf_token(request, form.get("csrf_token"))
    new_status = parse_status(form.get("status"))
    if new_status is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Status is required")
    material = MaterialRepository(db).update_status(material_id, new_status)
    if material is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    if new_status == MaterialStatus.ACTIVE:
        has_pending_person_reviews = any(review.status.value == "pending" for review in material.person_name_reviews)
        if material.needs_person_name_review or has_pending_person_reviews:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Material has unresolved person name review",
            )
        if has_unredacted_salutation_addressee(material.public_text):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Material has unredacted personal salutation addressee",
            )
    SearchService(db).reindex_material(material_id)
    db.commit()
    return RedirectResponse(f"/admin/materials/{material_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/materials/{material_id}/category")
async def update_material_category(
    request: Request,
    material_id: int,
    admin_user: CurrentAdmin,
    db: Session = Depends(get_db_session),
) -> RedirectResponse:
    form = await read_urlencoded_form(request)
    validate_csrf_token(request, form.get("csrf_token"))
    category_id = parse_optional_int(form.get("category_id"), field_name="category_id")
    try:
        material = MaterialRepository(db).update_category(material_id, category_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if material is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    SearchService(db).reindex_material(material_id)
    db.commit()
    return RedirectResponse(f"/admin/materials/{material_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/materials/{material_id}/reprocess")
async def reprocess_material(
    request: Request,
    material_id: int,
    admin_user: CurrentAdmin,
    db: Session = Depends(get_db_session),
) -> RedirectResponse:
    form = await read_urlencoded_form(request)
    validate_csrf_token(request, form.get("csrf_token"))
    result = MaterialReprocessingService(db).reprocess_material(material_id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    db.commit()
    return RedirectResponse(
        f"/admin/materials/{material_id}?reprocess_result={result.redactions}-{result.person_name_reviews}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/imports/{batch_id}/reprocess")
async def reprocess_import_batch(
    request: Request,
    batch_id: int,
    admin_user: CurrentAdmin,
    db: Session = Depends(get_db_session),
) -> RedirectResponse:
    form = await read_urlencoded_form(request)
    validate_csrf_token(request, form.get("csrf_token"))
    if ImportRepository(db).get_batch(batch_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    result = MaterialReprocessingService(db).reprocess_import_batch(batch_id)
    db.commit()
    return RedirectResponse(
        f"/admin/imports/{batch_id}?reprocess_result={result.processed}-{result.needs_review}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/materials/{material_id}/delete/mark")
async def mark_material_pending_delete(
    request: Request,
    material_id: int,
    admin_user: CurrentAdmin,
    db: Session = Depends(get_db_session),
) -> RedirectResponse:
    form = await read_urlencoded_form(request)
    validate_csrf_token(request, form.get("csrf_token"))
    material = MaterialDeletionService(db).mark_pending_delete(material_id)
    if material is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    db.commit()
    return RedirectResponse(f"/admin/materials/{material_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/materials/{material_id}/delete/final")
async def delete_material_permanently(
    request: Request,
    material_id: int,
    admin_user: CurrentAdmin,
    db: Session = Depends(get_db_session),
) -> RedirectResponse:
    form = await read_urlencoded_form(request)
    validate_csrf_token(request, form.get("csrf_token"))
    if form.get("confirmation", "").strip() != f"УДАЛИТЬ #{material_id}":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid delete confirmation")
    try:
        deleted = MaterialDeletionService(db).delete_permanently(material_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    db.commit()
    return RedirectResponse("/admin/materials", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/person-name-reviews/{review_id}/approve")
async def approve_person_name(
    request: Request,
    review_id: int,
    admin_user: CurrentAdmin,
    db: Session = Depends(get_db_session),
) -> RedirectResponse:
    form = await read_urlencoded_form(request)
    validate_csrf_token(request, form.get("csrf_token"))
    review = PersonNameReviewService(db).approve_public(review_id)
    if review is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    material_id = review.material_id
    db.commit()
    return RedirectResponse(f"/admin/materials/{material_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/person-name-reviews/{review_id}/redact")
async def redact_person_name(
    request: Request,
    review_id: int,
    admin_user: CurrentAdmin,
    db: Session = Depends(get_db_session),
) -> RedirectResponse:
    form = await read_urlencoded_form(request)
    validate_csrf_token(request, form.get("csrf_token"))
    review = PersonNameReviewService(db).redact_name(review_id)
    if review is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    material_id = review.material_id
    db.commit()
    return RedirectResponse(f"/admin/materials/{material_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/person-name-reviews/{review_id}/hide-material")
async def hide_material_after_person_name_review(
    request: Request,
    review_id: int,
    admin_user: CurrentAdmin,
    db: Session = Depends(get_db_session),
) -> RedirectResponse:
    form = await read_urlencoded_form(request)
    validate_csrf_token(request, form.get("csrf_token"))
    review = PersonNameReviewService(db).hide_material(review_id)
    if review is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    material_id = review.material_id
    db.commit()
    return RedirectResponse(f"/admin/materials/{material_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/search-quality/candidates/{candidate_id}/approve")
async def approve_dictionary_candidate(
    request: Request,
    candidate_id: int,
    admin_user: CurrentAdmin,
    db: Session = Depends(get_db_session),
) -> RedirectResponse:
    form = await read_urlencoded_form(request)
    validate_csrf_token(request, form.get("csrf_token"))
    candidate = SearchService(db).approve_candidate(candidate_id)
    if candidate is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    db.commit()
    return RedirectResponse("/admin/search-quality", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/search-quality/candidates/{candidate_id}/reject")
async def reject_dictionary_candidate(
    request: Request,
    candidate_id: int,
    admin_user: CurrentAdmin,
    db: Session = Depends(get_db_session),
) -> RedirectResponse:
    form = await read_urlencoded_form(request)
    validate_csrf_token(request, form.get("csrf_token"))
    candidate = SearchService(db).reject_candidate(candidate_id)
    if candidate is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    db.commit()
    return RedirectResponse("/admin/search-quality", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/search-quality/candidates/category")
async def create_category_candidate(
    request: Request,
    admin_user: CurrentAdmin,
    db: Session = Depends(get_db_session),
) -> RedirectResponse:
    form = await read_urlencoded_form(request)
    validate_csrf_token(request, form.get("csrf_token"))
    text = form.get("text", "").strip()
    if not text:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Candidate text is required")
    DictionaryCandidateRepository(db).create_or_increment(
        text=text,
        normalized_text=normalize_text(text) or text.lower(),
        candidate_type=DictionaryCandidateType.CATEGORY,
        source=DictionaryCandidateSource.SEARCH,
    )
    db.commit()
    return RedirectResponse("/admin/search-quality", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/categories")
async def create_category(
    request: Request,
    admin_user: CurrentAdmin,
    db: Session = Depends(get_db_session),
) -> RedirectResponse:
    form = await read_urlencoded_form(request)
    validate_csrf_token(request, form.get("csrf_token"))
    topic_id = parse_optional_int(form.get("topic_id"), field_name="topic_id")
    slug = form.get("slug", "").strip().lower()
    name = form.get("name", "").strip()
    sort_order = parse_optional_int(form.get("sort_order"), field_name="sort_order") or 100
    if topic_id is None or not slug or not name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Topic, slug and name are required")
    taxonomy = TaxonomyRepository(db)
    if taxonomy.get_category_by_slug(topic_id=topic_id, slug=slug) is not None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Category slug already exists")
    taxonomy.create_category(
        topic_id=topic_id,
        slug=slug,
        name=name,
        is_public=parse_bool_form(form.get("is_public")),
        is_confirmed=parse_bool_form(form.get("is_confirmed")),
        sort_order=sort_order,
    )
    db.commit()
    return RedirectResponse("/admin/categories", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/categories/{category_id}")
async def update_category(
    request: Request,
    category_id: int,
    admin_user: CurrentAdmin,
    db: Session = Depends(get_db_session),
) -> RedirectResponse:
    form = await read_urlencoded_form(request)
    validate_csrf_token(request, form.get("csrf_token"))
    name = form.get("name", "").strip()
    sort_order = parse_optional_int(form.get("sort_order"), field_name="sort_order") or 100
    if not name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Category name is required")
    category = TaxonomyRepository(db).update_category(
        category_id,
        name=name,
        is_public=parse_bool_form(form.get("is_public")),
        is_confirmed=parse_bool_form(form.get("is_confirmed")),
        sort_order=sort_order,
    )
    if category is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    SearchService(db).rebuild_index()
    db.commit()
    return RedirectResponse("/admin/categories", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/materials/{material_id}/notes")
async def add_material_note(
    request: Request,
    material_id: int,
    admin_user: CurrentAdmin,
    db: Session = Depends(get_db_session),
) -> RedirectResponse:
    form = await read_urlencoded_form(request)
    validate_csrf_token(request, form.get("csrf_token"))
    body = form.get("body", "").strip()
    if not body:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Note body is required")
    if MaterialRepository(db).get_admin_material(material_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    AdminNoteRepository(db).create(material_id=material_id, body=body, author=admin_user)
    db.commit()
    return RedirectResponse(f"/admin/materials/{material_id}", status_code=status.HTTP_303_SEE_OTHER)
