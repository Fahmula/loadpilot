import asyncio
import logging
import shutil
import uuid
from datetime import date, datetime
from pathlib import Path

from dotenv import load_dotenv
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from weasyprint import HTML

from loadpilot.ai import AIError, extract_rate_con, list_models, load_ai_config, suggest_model
from loadpilot.analytics import build_analytics, recent_activity
from loadpilot.config import BOL_DIR, INVOICE_DIR
from loadpilot.geo import estimate_road_miles, nearest_place
from loadpilot.models import Load, LoadStatus
from loadpilot.templating import STATUS_STYLES, alert_response, templates

logger = logging.getLogger("loadpilot.routes.ui")
router = APIRouter(tags=["UI Views"])

load_dotenv()


async def get_load_or_404(db, load_id: int) -> Load:
    load = await db.get_load(load_id)
    if not load:
        raise HTTPException(status_code=404, detail="Load not found")
    return load


async def last_delivery(db) -> str:
    """Where the newest load delivers: the usual start of the next load's deadhead."""
    recent = await db.get_recent_loads(limit=1)
    return recent[0].delivery_location if recent else ""


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    db = request.app.state.db
    # The current load is the active one picked up soonest; others are booked ahead
    active_loads = sorted(await db.get_loads(status=LoadStatus.ACTIVE), key=lambda load: load.pickup_date)
    needs_invoice = await db.get_loads(limit=10, status=LoadStatus.DELIVERED)
    activity = recent_activity(await db.get_recent_loads(limit=30))

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "current_load": active_loads[0] if active_loads else None,
            "more_active": max(len(active_loads) - 1, 0),
            "needs_invoice": needs_invoice,
            "activity": activity,
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
    context = {"loads": loads, "search": search or "", "status": status, "tabs": tabs}

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
    load = await get_load_or_404(request.app.state.db, load_id)
    return templates.TemplateResponse(request=request, name="load_detail.html", context={"load": load})


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
    except AIError as e:
        return alert_response(str(e), status_code=400)
    except Exception as e:
        logger.error(f"Error parsing document: {e}", exc_info=True)
        return alert_response(f"Internal processing error: {e}", status_code=500)

    context = {"data": parsed_data, "last_delivery": await last_delivery(request.app.state.db)}
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
        "notes": "",
    }
    context = {"data": empty_data, "last_delivery": await last_delivery(request.app.state.db)}
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


@router.post("/create-load-web/")
async def create_load_web(
    request: Request,
    broker_name: str = Form(...),
    broker_email: str = Form(""),
    rate_amount: float = Form(...),
    load_reference: str = Form(...),
    pickup_location: str = Form(...),
    pickup_date: str = Form(...),
    delivery_location: str = Form(...),
    delivery_date: str = Form(...),
    loaded_miles: int = Form(...),
    empty_miles: int = Form(0),
    deadhead_origin: str = Form(""),
    notes: str = Form(None),
):
    db = request.app.state.db
    p_date = datetime.strptime(pickup_date, "%Y-%m-%d").date()
    d_date = datetime.strptime(delivery_date, "%Y-%m-%d").date()

    load = Load(
        broker_name=broker_name,
        broker_email=broker_email.strip() or None,
        rate_amount=rate_amount,
        load_reference=load_reference,
        pickup_location=pickup_location,
        pickup_date=p_date,
        delivery_location=delivery_location,
        delivery_date=d_date,
        loaded_miles=loaded_miles,
        empty_miles=empty_miles,
        deadhead_origin=deadhead_origin.strip() or None,
        notes=notes,
        status=LoadStatus.ACTIVE,
    )

    load = await db.add_load(load)
    return Response(headers={"HX-Redirect": f"/loads/{load.id}"})


@router.post("/update-load-status/{load_id}/{new_status}")
async def update_load_status(request: Request, load_id: int, new_status: str):
    db = request.app.state.db
    status_map = {
        "Delivered": LoadStatus.DELIVERED,
        "Invoiced": LoadStatus.INVOICED,
    }

    if new_status in status_map:
        update_data = {"status": status_map[new_status].value}

        if status_map[new_status] == LoadStatus.DELIVERED:
            update_data["actual_delivery_date"] = date.today().isoformat()

        await db.update_load(load_id, update_data)

    return Response(headers={"HX-Refresh": "true"})


@router.post("/generate-invoice/{load_id}")
async def generate_invoice(request: Request, load_id: int, bol_file: UploadFile = File(...)):
    db = request.app.state.db
    load = await get_load_or_404(db, load_id)
    settings = await db.get_all_settings()

    # Keep only the base name so a crafted filename can't escape the BOL directory
    original_name = Path(bol_file.filename or "bol").name
    bol_filename = f"bol_{load_id}_{uuid.uuid4().hex[:6]}_{original_name}"
    bol_physical_path = BOL_DIR / bol_filename

    with open(str(bol_physical_path), "wb") as buffer:
        await asyncio.to_thread(shutil.copyfileobj, bol_file.file, buffer)

    invoice_num = f"INV-{1000 + load.id}"

    html_content = templates.get_template("invoice_template.html").render(
        {
            "load": load,
            "settings": settings,
            "invoice_number": invoice_num,
            "date_today": date.today().strftime("%b %d, %Y"),
        }
    )

    pdf_filename = f"invoice_{invoice_num}.pdf"
    pdf_physical_path = INVOICE_DIR / pdf_filename

    await asyncio.to_thread(HTML(string=html_content).write_pdf, str(pdf_physical_path))

    bol_web_path = f"static/bols/{bol_filename}"
    invoice_web_path = f"static/invoices/{pdf_filename}"

    await db.update_load(
        load_id,
        {
            "status": LoadStatus.INVOICED.value,
            "invoice_number": invoice_num,
            "bol_pdf_path": bol_web_path,
            "invoice_pdf_path": invoice_web_path,
            "invoiced_at": datetime.now().replace(microsecond=0),
        },
    )

    return RedirectResponse(url=f"/loads/{load_id}", status_code=303)


@router.get("/analytics", response_class=HTMLResponse)
async def analytics_page(request: Request, period: str = "month"):
    loads = await request.app.state.db.get_earned_loads()
    analytics = build_analytics(loads, period)
    return templates.TemplateResponse(request=request, name="analytics.html", context={"analytics": analytics})


@router.post("/delete-load-web/{load_id}")
async def delete_load_web(request: Request, load_id: int):
    db = request.app.state.db
    await db.delete_load(load_id)
    return Response(headers={"HX-Redirect": "/loads"})
