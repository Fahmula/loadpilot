import os
import uuid
import shutil
import asyncio
import logging
from datetime import datetime, date
from fastapi import APIRouter, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from weasyprint import HTML
from pathlib import Path
from dotenv import load_dotenv

from loadpilot.models import Load, LoadStatus
from loadpilot.services import parse_rate_con_with_ai

logger = logging.getLogger("loadpilot.routes.ui")
router = APIRouter(tags=["UI Views"])
templates = Jinja2Templates(directory="templates")

load_dotenv()

@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    db = request.app.state.db
    active_loads = await db.get_loads(limit=5, status=LoadStatus.ACTIVE)
    pending_loads = await db.get_loads(limit=10, status=LoadStatus.DELIVERED)
    history_loads = await db.get_loads(limit=10, status=LoadStatus.INVOICED)
    
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "active_loads": active_loads,
            "pending_loads": pending_loads,
            "history_loads": history_loads
        }
    )

@router.get("/loads", response_class=HTMLResponse)
async def loads_page(request: Request, search: str | None = None):
    db = request.app.state.db
    loads = await db.get_loads(limit=100, search_query=search)
    
    # Check if this is an HTMX search request
    if request.headers.get("HX-Request") == "true":
        return templates.TemplateResponse(
            request=request,
            name="partials/loads_list.html",
            context={"loads": loads}
        )
        
    return templates.TemplateResponse(
        request=request,
        name="loads.html",
        context={"loads": loads, "search": search or ""}
    )

@router.post("/parse-rate-con-html/")
async def parse_rate_con_html(request: Request, file: UploadFile = File(...)):
    db = request.app.state.db
    try:
        file_bytes = await file.read()
        mime_type = file.content_type or "application/octet-stream"
        
        # Load API keys from DB settings or environment
        api_key = await db.get_setting("gemini_api_key", os.getenv("GEMINI_API_KEY", ""))
        
        # Run Gemini blocking network call inside a thread pool
        parsed_data = await asyncio.to_thread(
            parse_rate_con_with_ai, file_bytes, mime_type, api_key
        )
        
        if not parsed_data:
            return Response("<div class='alert alert-danger'>AI extraction failed. Please try again.</div>", status_code=400)
            
        return templates.TemplateResponse(
            request=request, 
            name="partials/load_form.html", 
            context={"data": parsed_data}
        )
    except Exception as e:
        logger.error(f"Error parsing document: {e}", exc_info=True)
        return Response(f"<div class='alert alert-danger'>Internal Processing Error: {str(e)}</div>", status_code=500)

@router.post("/create-load-web/")
async def create_load_web(
    request: Request,
    broker_name: str = Form(...),
    rate_amount: float = Form(...),
    load_reference: str = Form(...),
    pickup_location: str = Form(...),
    pickup_date: str = Form(...),
    delivery_location: str = Form(...),
    delivery_date: str = Form(...),
    loaded_miles: int = Form(...),
    notes: str = Form(None)
):
    db = request.app.state.db
    p_date = datetime.strptime(pickup_date, "%Y-%m-%d").date()
    d_date = datetime.strptime(delivery_date, "%Y-%m-%d").date()
    
    load = Load(
        broker_name=broker_name,
        rate_amount=rate_amount,
        load_reference=load_reference,
        pickup_location=pickup_location,
        pickup_date=p_date,
        delivery_location=delivery_location,
        delivery_date=d_date,
        loaded_miles=loaded_miles,
        notes=notes,
        status=LoadStatus.ACTIVE
    )
    
    await db.add_load(load)
    return Response(headers={"HX-Refresh": "true"})

@router.post("/update-load-status/{load_id}/{new_status}")
async def update_load_status(request: Request, load_id: int, new_status: str):
    db = request.app.state.db
    status_map = {
        "Delivered": LoadStatus.DELIVERED,
        "Invoiced": LoadStatus.INVOICED,
    }
    
    if new_status in status_map:
        await db.update_load(load_id, {"status": status_map[new_status].value})
        
    return Response(headers={"HX-Refresh": "true"})

@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    db = request.app.state.db
    settings = await db.get_all_settings()
    return templates.TemplateResponse(
        request=request, 
        name="settings.html", 
        context={"settings": settings}
    )

@router.post("/settings/save")
async def save_settings(
    request: Request,
    company_name: str = Form(""),
    street_address: str = Form(""),
    city_state_zip: str = Form(""),
    show_address_on_invoice: bool = Form(False),
    bank_name: str = Form(""),
    account_number: str = Form(""),
    routing_number: str = Form(""),
    show_bank_on_invoice: bool = Form(False),
    gemini_api_key: str = Form("")
):
    db = request.app.state.db
    
    await db.save_setting("company_name", company_name)
    await db.save_setting("street_address", street_address)
    await db.save_setting("city_state_zip", city_state_zip)
    await db.save_setting("show_address_on_invoice", "true" if show_address_on_invoice else "false")
    await db.save_setting("bank_name", bank_name)
    await db.save_setting("account_number", account_number)
    await db.save_setting("routing_number", routing_number)
    await db.save_setting("show_bank_on_invoice", "true" if show_bank_on_invoice else "false")
    
    if gemini_api_key.strip():
         await db.save_setting("gemini_api_key", gemini_api_key)
    
    return Response("""
        <div class="alert alert-success alert-dismissible fade show mt-3" role="alert">
            <i class="bi bi-check-circle-fill"></i> Settings saved successfully!
            <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
        </div>
    """)

@router.post("/generate-invoice/{load_id}")
async def generate_invoice(request: Request, load_id: int, bol_file: UploadFile = File(...)):
    db = request.app.state.db
    load = await db.get_load(load_id)
    settings = await db.get_all_settings()
    
    os.makedirs("static/bols", exist_ok=True)
    bol_filename = f"bol_{load_id}_{uuid.uuid4().hex[:6]}_{bol_file.filename}"
    bol_path = f"static/bols/{bol_filename}"
    
    with open(bol_path, "wb") as buffer:
        await asyncio.to_thread(shutil.copyfileobj, bol_file.file, buffer)
        
    invoice_num = f"INV-{1000 + load.id}"
    
    html_content = templates.get_template("invoice_template.html").render({
        "load": load,
        "settings": settings,
        "invoice_number": invoice_num,
        "date_today": date.today().strftime("%b %d, %Y")
    })
    
    os.makedirs("static/invoices", exist_ok=True)
    pdf_filename = f"invoice_{invoice_num}.pdf"
    pdf_path = f"static/invoices/{pdf_filename}"
    
    await asyncio.to_thread(HTML(string=html_content).write_pdf, pdf_path)
    
    await db.update_load(load_id, {
        "status": LoadStatus.INVOICED.value,
        "invoice_number": invoice_num,
        "bol_pdf_path": bol_path,
        "rate_con_pdf_path": pdf_path
    })
    
    return RedirectResponse(url="/", status_code=303)