import os
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from loadpilot.templating import templates

router = APIRouter(prefix="/settings", tags=["Settings"])

Section = Literal["company", "ai", "bank"]

# Which settings keys each card edits
SECTION_FIELDS = {
    "company": ("company_name", "street_address", "city_state_zip"),
    "ai": ("gemini_api_key",),
    "bank": ("bank_name", "account_number", "routing_number"),
}
TOGGLES = frozenset({"show_address_on_invoice", "show_bank_on_invoice"})


async def render_section(request: Request, section: str, editing: bool) -> HTMLResponse:
    settings = await request.app.state.db.get_all_settings()
    return templates.TemplateResponse(
        request=request,
        name="partials/settings_section.html",
        context={
            "section": section,
            "settings": settings,
            "editing": editing,
            "env_ai_key": bool(os.getenv("GEMINI_API_KEY")),
        },
    )


@router.get("", response_class=HTMLResponse)
async def settings_page(request: Request):
    settings = await request.app.state.db.get_all_settings()
    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={"settings": settings, "env_ai_key": bool(os.getenv("GEMINI_API_KEY"))},
    )


@router.get("/section/{section}", response_class=HTMLResponse)
async def settings_section(request: Request, section: Section, edit: bool = False):
    return await render_section(request, section, editing=edit)


@router.post("/section/{section}", response_class=HTMLResponse)
async def save_settings_section(request: Request, section: Section):
    db = request.app.state.db
    form = await request.form()
    for key in SECTION_FIELDS[section]:
        value = str(form.get(key, "")).strip()
        # A blank API key field means "keep the current key", since the saved key is never shown
        if key == "gemini_api_key" and not value:
            continue
        await db.save_setting(key, value)
    return await render_section(request, section, editing=False)


@router.post("/toggle/{key}")
async def save_toggle(request: Request, key: str):
    if key not in TOGGLES:
        raise HTTPException(status_code=404, detail="Unknown setting")
    form = await request.form()
    await request.app.state.db.save_setting(key, "true" if form.get("enabled") == "true" else "false")
    return Response(status_code=204)
