from __future__ import annotations

from datetime import datetime
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

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
from app.db.enums import MaterialStatus, MaterialType
from app.db.repositories import AdminNoteRepository, MaterialRepository, TaxonomyRepository
from app.db.session import get_db_session

router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory="app/templates")


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


templates.env.filters["date_ru"] = date_ru
templates.env.filters["material_type_label"] = material_type_label
templates.env.filters["status_label"] = status_label


def admin_template_context(request: Request, settings: Settings, admin_user: str | None = None) -> dict:
    return {
        "request": request,
        "service_name": settings.service_name,
        "admin_user": admin_user,
        "csrf_token": ensure_csrf_token(request),
        "default_password_warning": settings.admin_password == "change_me",
        "statuses": list(MaterialStatus),
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
    return templates.TemplateResponse(
        request,
        "admin/material.html",
        {
            **admin_template_context(request, settings, admin_user),
            "material": material,
            "categories": categories,
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
    db.commit()
    return RedirectResponse(f"/admin/materials/{material_id}", status_code=status.HTTP_303_SEE_OTHER)


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
