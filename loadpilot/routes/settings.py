import asyncio
import re
from datetime import date
from typing import Literal

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from loadpilot.ai import PROVIDERS, AIError, build_prompt, list_models, load_ai_config, suggest_model
from loadpilot.auth import hash_api_token, new_api_token
from loadpilot.templating import templates

router = APIRouter(prefix="/settings", tags=["Settings"])

Section = Literal["company", "ai", "bank"]

# Which settings keys the simple cards edit (the AI card has its own handler)
SECTION_FIELDS = {
    "company": (
        "company_name",
        "street_address",
        "city_state_zip",
        "mc_number",
        "dot_number",
        "company_phone",
        "company_email",
    ),
    "bank": ("payment_terms_days", "bank_name", "account_number", "routing_number"),
}
TOGGLES = frozenset({"show_address_on_invoice", "show_bank_on_invoice"})

# Carriers type these as "MC-123456" or "USDOT 1234567"; keep just the number, the invoice adds the label
ID_PREFIXES = {
    "mc_number": re.compile(r"^MC\s*[#:-]?\s*", re.I),
    "dot_number": re.compile(r"^(US)?\s*DOT\s*[#:-]?\s*", re.I),
}


async def section_context(request: Request, section: str, editing: bool, provider: str | None = None) -> dict:
    db = request.app.state.db
    return {
        "section": section,
        "settings": await db.get_all_settings(),
        "editing": editing,
        "ai": await load_ai_config(db, provider),
        "providers": PROVIDERS,
        "builtin_prompt": build_prompt(),
    }


async def render_section(request: Request, section: str, editing: bool, provider: str | None = None):
    context = await section_context(request, section, editing, provider)
    return templates.TemplateResponse(request=request, name="partials/settings_section.html", context=context)


@router.get("", response_class=HTMLResponse)
async def settings_page(request: Request):
    context = await section_context(request, "company", editing=False)
    context["token"] = api_token_on_file(context["settings"])
    context["export_years"] = await request.app.state.db.get_load_years()
    return templates.TemplateResponse(request=request, name="settings.html", context=context)


# -----------------------------------------
# API TOKEN
# -----------------------------------------
def api_token_on_file(settings: dict) -> dict | None:
    """What the API Access card shows about the current token: never the token, which isn't stored."""
    if not settings.get("api_token_hash"):
        return None
    created = settings.get("api_token_created")
    return {"hint": settings.get("api_token_hint", ""), "created": date.fromisoformat(created) if created else None}


def render_api_token(request: Request, token: dict | None, new_token: str | None = None):
    context = {"token": token, "new_token": new_token}
    return templates.TemplateResponse(request=request, name="partials/api_token.html", context=context)


@router.post("/api-token", response_class=HTMLResponse)
async def create_api_token(request: Request):
    """Creates the API token, replacing any earlier one, and shows it this once."""
    db = request.app.state.db
    token = new_api_token()
    await db.save_setting("api_token_hash", hash_api_token(token))
    await db.save_setting("api_token_hint", "…" + token[-4:])
    await db.save_setting("api_token_created", date.today().isoformat())
    return render_api_token(request, api_token_on_file(await db.get_all_settings()), new_token=token)


@router.post("/api-token/revoke", response_class=HTMLResponse)
async def revoke_api_token(request: Request):
    db = request.app.state.db
    for key in ("api_token_hash", "api_token_hint", "api_token_created"):
        await db.save_setting(key, "")
    return render_api_token(request, None)


@router.get("/section/{section}", response_class=HTMLResponse)
async def settings_section(request: Request, section: Section, edit: bool = False, provider: str | None = None):
    return await render_section(request, section, editing=edit, provider=provider)


@router.post("/section/ai", response_class=HTMLResponse)
async def save_ai_settings(
    request: Request,
    provider: str = Form(...),
    api_key: str = Form(""),
    model: str = Form(""),
    extra_instructions: str = Form(""),
):
    if provider not in PROVIDERS:
        raise HTTPException(status_code=422, detail="Unknown AI provider")
    db = request.app.state.db
    await db.save_setting("ai_provider", provider)
    # A blank key field means "keep the current key", since a saved key is never shown again
    if api_key.strip():
        await db.save_setting(f"ai_api_key_{provider}", api_key.strip())
    await db.save_setting(f"ai_model_{provider}", model.strip())
    await db.save_setting("ai_extra_instructions", extra_instructions.strip())
    return await render_section(request, "ai", editing=False)


@router.post("/section/{section}", response_class=HTMLResponse)
async def save_settings_section(request: Request, section: Literal["company", "bank"]):
    db = request.app.state.db
    form = await request.form()
    for key in SECTION_FIELDS[section]:
        value = str(form.get(key, "")).strip()
        if key in ID_PREFIXES:
            value = ID_PREFIXES[key].sub("", value)
        await db.save_setting(key, value)
    return await render_section(request, section, editing=False)


@router.post("/ai/models", response_class=HTMLResponse)
async def ai_models(request: Request, provider: str = Form(...), api_key: str = Form("")):
    """Model picker for the AI card, loaded live with the typed key or the saved one."""
    if provider not in PROVIDERS:
        raise HTTPException(status_code=422, detail="Unknown AI provider")
    ai = await load_ai_config(request.app.state.db, provider)
    context = {"ai": ai, "models": [], "suggested": None, "selected": ai.model, "error": None}
    try:
        models = await asyncio.to_thread(list_models, ai.provider, api_key.strip() or ai.api_key)
        context["models"] = models
        context["suggested"] = suggest_model(ai.provider, models)
        if ai.model not in {m.id for m in models}:
            context["selected"] = context["suggested"]
    except AIError as e:
        context["error"] = str(e)
    return templates.TemplateResponse(request=request, name="partials/ai_models.html", context=context)


@router.post("/toggle/{key}")
async def save_toggle(request: Request, key: str):
    if key not in TOGGLES:
        raise HTTPException(status_code=404, detail="Unknown setting")
    form = await request.form()
    await request.app.state.db.save_setting(key, "true" if form.get("enabled") == "true" else "false")
    return Response(status_code=204)
