import asyncio
import logging
import uuid
from datetime import date, datetime
from pathlib import Path

from dotenv import load_dotenv
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from weasyprint import HTML

from loadpilot.ai import AIError, extract_rate_con, list_models, load_ai_config, suggest_model
from loadpilot.analytics import build_analytics, due_date, payment_terms, receivables
from loadpilot.brokers import apply_known_broker, known_brokers
from loadpilot.config import BOL_DIR, INVOICE_DIR
from loadpilot.documents import (
    UnreadableDocument,
    as_pdf,
    claim_pending,
    remove_document,
    remove_load_documents,
    save_for_load,
    save_pending,
    with_document,
)
from loadpilot.geo import estimate_road_miles, nearest_place
from loadpilot.models import Charge, Load, LoadStatus, Stop
from loadpilot.templating import STATUS_STYLES, alert_response, templates

logger = logging.getLogger("loadpilot.routes.ui")
router = APIRouter(tags=["UI Views"])

load_dotenv()


async def get_load_or_404(db, load_id: int) -> Load:
    load = await db.get_load(load_id)
    if not load:
        raise HTTPException(status_code=404, detail="Load not found")
    return load


async def form_context(db, data) -> dict:
    """What the load form needs besides the load itself: broker autofill and the deadhead fallback start."""
    brokers = known_brokers(await db.get_broker_contacts())
    return {
        "data": data,
        "brokers": {key: {"name": b.name, "email": b.email or ""} for key, b in brokers.items()},
        "last_delivery": await last_delivery(db),
    }


async def last_delivery(db) -> str:
    """Where the newest load delivers: the usual start of the next load's deadhead."""
    recent = await db.get_recent_loads(limit=1)
    return recent[0].delivery_location if recent else ""


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    db = request.app.state.db
    settings = await db.get_all_settings()
    terms = payment_terms(settings)

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "company_name": settings.get("company_name") or request.state.user.username,
            "counts": await db.count_loads_by_status(),
            "month": build_analytics(await db.get_earned_loads(), "month")["current"],
            "recent_loads": await db.get_recent_loads(limit=5),
            "awaiting": receivables(await db.get_unpaid_invoices(), terms),
            "terms": terms,
        },
    )


@router.get("/loads", response_class=HTMLResponse)
async def loads_page(request: Request, search: str | None = None, status: LoadStatus | None = None):
    db = request.app.state.db
    loads = await db.get_loads(limit=100, status=status, search_query=search)
    counts = await db.count_loads_by_status()

    # Drafts only get a tab once there are some
    tabs = [(None, "All", sum(counts.values()))] + [
        (tab_status, STATUS_STYLES[tab_status][1], count)
        for tab_status, count in counts.items()
        if count or tab_status != LoadStatus.DRAFT
    ]
    terms = payment_terms(await db.get_all_settings())
    context = {"loads": loads, "search": search or "", "status": status, "tabs": tabs, "terms": terms}

    # HTMX tab/search requests only need the tabs + list
    if request.headers.get("HX-Request") == "true":
        return templates.TemplateResponse(request=request, name="partials/loads_results.html", context=context)
    return templates.TemplateResponse(request=request, name="loads.html", context=context)


@router.get("/loads/new", response_class=HTMLResponse)
async def new_load_page(request: Request):
    ai = await load_ai_config(request.app.state.db)
    return templates.TemplateResponse(request=request, name="load_new.html", context={"ai": ai})


@router.get("/loads/{load_id}", response_class=HTMLResponse)
async def load_detail_page(request: Request, load_id: int):
    db = request.app.state.db
    load = await get_load_or_404(db, load_id)
    context = {"load": load, "terms": payment_terms(await db.get_all_settings()), "today": date.today()}
    return templates.TemplateResponse(request=request, name="load_detail.html", context=context)


@router.post("/parse-rate-con-html/")
async def parse_rate_con_html(request: Request, files: list[UploadFile] = File(...)):
    ai = await load_ai_config(request.app.state.db)
    try:
        pages = [(await file.read(), file.content_type or "application/octet-stream", file.filename) for file in files]

        # No model chosen yet (e.g. key only set by environment variable): use the suggested one
        model = ai.model
        if not model and ai.api_key:
            model = suggest_model(ai.provider, await asyncio.to_thread(list_models, ai.provider, ai.api_key)) or ""

        parsed_data = await asyncio.to_thread(extract_rate_con, ai, model, pages)
        rate_con_file = await asyncio.to_thread(save_pending, pages)
    except AIError as e:
        return alert_response(str(e), status_code=400)
    except Exception as e:
        logger.error(f"Error parsing document: {e}", exc_info=True)
        return alert_response(f"Internal processing error: {e}", status_code=500)

    db = request.app.state.db
    parsed_data = apply_known_broker(parsed_data, known_brokers(await db.get_broker_contacts()))
    context = {**await form_context(db, parsed_data), "rate_con_file": rate_con_file}
    return templates.TemplateResponse(request=request, name="partials/load_form.html", context=context)


@router.get("/manual-load-form/", response_class=HTMLResponse)
async def manual_load_form(request: Request):
    # Provides structure so templates/partials/load_form.html renders safely with empty values
    empty_data = {
        "broker_name": "",
        "broker_email": "",
        "rate_amount": "",
        "loaded_miles": 0,
        "empty_miles": 0,
        "deadhead_origin": "",
        "load_reference": "",
        "pickup_location": "",
        "pickup_date": "",
        "delivery_location": "",
        "delivery_date": "",
        "stops": [],
        "notes": "",
    }
    context = await form_context(request.app.state.db, empty_data)
    return templates.TemplateResponse(request=request, name="partials/load_form.html", context=context)


@router.get("/geo/nearest")
async def geo_nearest(lat: float, lon: float):
    place = nearest_place(lat, lon)
    if not place:
        raise HTTPException(status_code=404, detail="No known city near this location")
    return {"location": place.label}


@router.get("/geo/estimate")
async def geo_estimate(origin: str = "", destination: str = ""):
    return {"miles": estimate_road_miles(origin, destination)}


def load_form(
    broker_name: str = Form(...),
    broker_email: str = Form(""),
    rate_amount: float = Form(...),
    load_reference: str = Form(...),
    pickup_location: str = Form(...),
    pickup_date: date = Form(...),
    delivery_location: str = Form(...),
    delivery_date: date = Form(...),
    loaded_miles: int = Form(...),
    empty_miles: int = Form(0),
    deadhead_origin: str = Form(""),
    notes: str = Form(""),
    stop_kind: list[str] = Form([]),
    stop_location: list[str] = Form([]),
    stop_date: list[str] = Form([]),
) -> dict:
    """The load fields shared by the add and edit forms (templates/partials/load_form.html)."""
    # Each extra stop is a row of three same-named inputs; rows left without a city are dropped
    try:
        stops = [
            Stop(kind=kind, location=location.strip(), stop_date=day or None)
            for kind, location, day in zip(stop_kind, stop_location, stop_date, strict=True)
            if location.strip()
        ]
    except ValueError as e:  # a mismatched row, unknown stop type or bad date
        raise HTTPException(status_code=422, detail="Check the extra stops") from e
    return {
        "broker_name": broker_name,
        "broker_email": broker_email.strip() or None,
        "rate_amount": rate_amount,
        "load_reference": load_reference,
        "pickup_location": pickup_location,
        "pickup_date": pickup_date,
        "delivery_location": delivery_location,
        "delivery_date": delivery_date,
        "loaded_miles": loaded_miles,
        "empty_miles": empty_miles,
        "deadhead_origin": deadhead_origin.strip() or None,
        "notes": notes or None,
        "stops": stops,
    }


@router.post("/create-load-web/")
async def create_load_web(request: Request, fields: dict = Depends(load_form), rate_con_file: str = Form("")):
    db = request.app.state.db
    load = await db.add_load(Load(**fields, status=LoadStatus.ACTIVE))

    rate_con_path = await asyncio.to_thread(claim_pending, rate_con_file, load.id)
    if rate_con_path:
        await db.update_load(load.id, {"rate_con_pdf_path": rate_con_path})
    return Response(headers={"HX-Redirect": f"/loads/{load.id}"})


@router.get("/loads/{load_id}/edit", response_class=HTMLResponse)
async def edit_load_page(request: Request, load_id: int):
    db = request.app.state.db
    load = await get_load_or_404(db, load_id)
    context = {**await form_context(db, load), "load": load}
    return templates.TemplateResponse(request=request, name="load_edit.html", context=context)


@router.post("/loads/{load_id}/edit")
async def edit_load(
    request: Request, load_id: int, fields: dict = Depends(load_form), actual_delivery_date: date | None = Form(None)
):
    db = request.app.state.db
    load = await get_load_or_404(db, load_id)
    # Only delivered loads have a delivery day to correct; it decides which period earnings count toward
    if load.actual_delivery_date and actual_delivery_date:
        fields["actual_delivery_date"] = actual_delivery_date
    await db.update_load(load_id, fields)
    return Response(headers={"HX-Redirect": f"/loads/{load_id}"})


@router.post("/loads/{load_id}/rate-con")
async def attach_rate_con(request: Request, load_id: int, rate_con: UploadFile = File(...)):
    db = request.app.state.db
    load = await get_load_or_404(db, load_id)
    page = (await rate_con.read(), rate_con.content_type or "application/octet-stream", rate_con.filename)
    path = await asyncio.to_thread(save_for_load, [page], load_id)
    if not path:
        raise HTTPException(status_code=400, detail="Upload a PDF or a photo of the rate con")
    await db.update_load(load_id, {"rate_con_pdf_path": path})
    # A replaced rate con is no longer linked from anywhere
    await asyncio.to_thread(remove_document, load.rate_con_pdf_path)
    return RedirectResponse(url=f"/loads/{load_id}", status_code=303)


@router.post("/update-load-status/{load_id}/{new_status}")
async def update_load_status(request: Request, load_id: int, new_status: str):
    """Marks a load delivered today. Invoicing and payment have their own routes: each needs more than a status."""
    if new_status != LoadStatus.DELIVERED:
        raise HTTPException(status_code=422, detail="Only Delivered can be set here")
    db = request.app.state.db
    load = await get_load_or_404(db, load_id)
    # A page left open from before the load moved on mustn't move it back or reset its delivery day.
    # htmx reloads on HX-Refresh whatever the status code, so the stale page then shows where the load stands.
    if load.status not in (LoadStatus.DRAFT, LoadStatus.ACTIVE):
        return Response(status_code=409, headers={"HX-Refresh": "true"})
    await db.update_load(load_id, {"status": LoadStatus.DELIVERED, "actual_delivery_date": date.today()})
    return Response(headers={"HX-Refresh": "true"})


async def write_invoice(load: Load, settings: dict) -> str:
    """Renders the invoice PDF for load.invoice_number, dated load.invoiced_at; returns its web path.

    The signed BOL, when the load has one, follows as the last pages: brokers want the proof of delivery
    in the same file as the invoice. Raises UnreadableDocument, before writing anything, if it can't be added.
    """
    terms = payment_terms(settings)
    html_content = templates.get_template("invoice_template.html").render(
        {
            "load": load,
            "settings": settings,
            "invoice_number": load.invoice_number,
            "invoice_date": load.invoiced_at.strftime("%b %d, %Y"),
            "terms": terms,
            "due_date": due_date(load, terms).strftime("%b %d, %Y"),
        }
    )
    pdf = await asyncio.to_thread(HTML(string=html_content).write_pdf)
    if load.bol_pdf_path:
        pdf = await asyncio.to_thread(with_document, pdf, BOL_DIR / Path(load.bol_pdf_path).name)
    pdf_filename = f"invoice_{load.invoice_number}.pdf"
    (INVOICE_DIR / pdf_filename).write_bytes(pdf)
    return f"static/invoices/{pdf_filename}"


@router.post("/generate-invoice/{load_id}")
async def generate_invoice(request: Request, load_id: int, bol_file: UploadFile = File(...)):
    db = request.app.state.db
    load = await get_load_or_404(db, load_id)
    # A resubmitted form would otherwise re-invoice the load, putting a paid one back to Invoiced
    if load.status != LoadStatus.DELIVERED:
        raise HTTPException(status_code=409, detail="Only a delivered load can be invoiced")
    settings = await db.get_all_settings()

    # Kept as a PDF, the BOL opens in any browser (a phone's HEIC photo wouldn't) and matches the invoice's pages
    bol_filename = f"bol_{load_id}_{uuid.uuid4().hex[:6]}.pdf"
    invoiced = {
        "invoice_number": f"INV-{1000 + load.id}",
        "invoiced_at": datetime.now().replace(microsecond=0),
        "bol_pdf_path": f"static/bols/{bol_filename}",
    }
    # The BOL is the proof of delivery the broker pays against, so no BOL means no invoice
    try:
        (BOL_DIR / bol_filename).write_bytes(await asyncio.to_thread(as_pdf, await bol_file.read()))
        invoice_web_path = await write_invoice(load.model_copy(update=invoiced), settings)
    except UnreadableDocument as e:
        (BOL_DIR / bol_filename).unlink(missing_ok=True)
        logger.warning(f"Refused BOL {bol_file.filename!r} for load {load_id}: {e}")
        return RedirectResponse(url=f"/loads/{load_id}?bol_error=1#invoice", status_code=303)

    await db.update_load(
        load_id, {**invoiced, "status": LoadStatus.INVOICED.value, "invoice_pdf_path": invoice_web_path}
    )

    return RedirectResponse(url=f"/loads/{load_id}", status_code=303)


@router.post("/loads/{load_id}/rebuild-invoice")
async def rebuild_invoice(request: Request, load_id: int):
    """Re-renders an existing invoice after the load or its charges changed; same number and date."""
    db = request.app.state.db
    load = await get_load_or_404(db, load_id)
    if not load.invoice_number:
        raise HTTPException(status_code=409, detail="This load has no invoice yet")
    # Invoices from before invoiced_at was recorded are dated today
    load = load.model_copy(update={"invoiced_at": load.invoiced_at or datetime.now().replace(microsecond=0)})
    settings = await db.get_all_settings()
    rebuilt = "rebuilt=1"
    try:
        invoice_web_path = await write_invoice(load, settings)
    except UnreadableDocument as e:
        # A BOL saved before uploads were checked, or one that went missing: rebuild anyway and say so
        logger.warning(f"Rebuilt invoice {load.invoice_number} without its BOL: {e}")
        invoice_web_path = await write_invoice(load.model_copy(update={"bol_pdf_path": None}), settings)
        rebuilt += "&bol_missing=1"
    await db.update_load(load_id, {"invoice_pdf_path": invoice_web_path})
    return RedirectResponse(url=f"/loads/{load_id}?{rebuilt}#payment", status_code=303)


@router.post("/loads/{load_id}/charges")
async def add_charge(request: Request, load_id: int, description: str = Form(...), amount: float = Form(..., gt=0)):
    db = request.app.state.db
    load = await get_load_or_404(db, load_id)
    if load.status == LoadStatus.PAID:
        raise HTTPException(status_code=409, detail="This load is already paid")
    description = description.strip()
    if not description:
        raise HTTPException(status_code=422, detail="Describe the charge, e.g. Detention")
    charges = [*load.charges, Charge(description=description, amount=amount)]
    await db.update_load(load_id, {"charges": charges})
    return RedirectResponse(url=f"/loads/{load_id}#charges", status_code=303)


@router.post("/loads/{load_id}/charges/{index}/delete")
async def delete_charge(request: Request, load_id: int, index: int):
    db = request.app.state.db
    load = await get_load_or_404(db, load_id)
    if load.status == LoadStatus.PAID:
        raise HTTPException(status_code=409, detail="This load is already paid")
    if not 0 <= index < len(load.charges):
        raise HTTPException(status_code=404, detail="Charge not found")
    charges = [charge for i, charge in enumerate(load.charges) if i != index]
    await db.update_load(load_id, {"charges": charges})
    return RedirectResponse(url=f"/loads/{load_id}#charges", status_code=303)


@router.post("/loads/{load_id}/paid")
async def mark_paid(request: Request, load_id: int, paid_at: date = Form(...), amount_paid: float = Form(..., gt=0)):
    db = request.app.state.db
    load = await get_load_or_404(db, load_id)
    if load.status != LoadStatus.INVOICED:
        raise HTTPException(status_code=409, detail="Only an invoiced load can be marked paid")
    await db.update_load(load_id, {"status": LoadStatus.PAID, "paid_at": paid_at, "amount_paid": amount_paid})
    return RedirectResponse(url=f"/loads/{load_id}", status_code=303)


@router.post("/loads/{load_id}/unpaid")
async def mark_unpaid(request: Request, load_id: int):
    db = request.app.state.db
    load = await get_load_or_404(db, load_id)
    if load.status == LoadStatus.PAID:
        await db.update_load(load_id, {"status": LoadStatus.INVOICED, "paid_at": None, "amount_paid": None})
    return RedirectResponse(url=f"/loads/{load_id}", status_code=303)


@router.get("/analytics", response_class=HTMLResponse)
async def analytics_page(request: Request, period: str = "month"):
    loads = await request.app.state.db.get_earned_loads()
    analytics = build_analytics(loads, period)
    return templates.TemplateResponse(request=request, name="analytics.html", context={"analytics": analytics})


@router.post("/delete-load-web/{load_id}")
async def delete_load_web(request: Request, load_id: int):
    db = request.app.state.db
    load = await db.get_load(load_id)
    if load and await db.delete_load(load_id):
        await asyncio.to_thread(remove_load_documents, load)
    return Response(headers={"HX-Redirect": "/loads"})
