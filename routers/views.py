import os
import shutil
import uuid
from datetime import datetime, date
from fastapi import APIRouter, Request, Depends, UploadFile, File, Form
from fastapi.templating import Jinja2Templates
from fastapi.responses import Response, RedirectResponse
from httpx import request
from sqlalchemy.orm import Session
from weasyprint import HTML

import crud
import models
import services
import schemas
from database import get_db

router = APIRouter(tags=["Views"])

# Point FastAPI to your templates folder
templates = Jinja2Templates(directory="templates")

@router.get("/")
def dashboard(request: Request, db: Session = Depends(get_db)):
    """Render the main dashboard."""
    # Get all active loads for the top card
    active_loads = crud.get_loads(db, limit=5, status=models.LoadStatus.ACTIVE)
    
    # Get delivered and invoiced loads for the bottom section
    delivered = crud.get_loads(db, limit=10, status=models.LoadStatus.DELIVERED)
    invoiced = crud.get_loads(db, limit=10, status=models.LoadStatus.INVOICED)
    
    recent_loads = delivered + invoiced

    # Pass the data into the HTML template
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html", 
        context={
            "active_loads": active_loads,
            "recent_loads": recent_loads
        }
    )

@router.post("/parse-rate-con-html/")
async def parse_rate_con_html(request: Request, file: UploadFile = File(...)):
    """HTMX endpoint: Takes raw file (PDF/Image), asks Gemini Vision, returns HTML form."""
    try:
        # Read the raw file bytes
        file_bytes = await file.read()
        
        # Get the file type (e.g., 'application/pdf', 'image/jpeg', 'image/png')
        mime_type = file.content_type 
        
        # Let Gemini's Vision do the heavy lifting
        parsed_data = services.parse_rate_con_with_ai(file_bytes, mime_type)
        
        if not parsed_data:
            return Response("<div class='alert alert-danger'>AI failed to parse the document.</div>", status_code=400)
            
        return templates.TemplateResponse(
            request=request, 
            name="partials/load_form.html", 
            context={"data": parsed_data}
        )
    except Exception as e:
        return Response(f"<div class='alert alert-danger'>Error: {str(e)}</div>", status_code=500)


@router.post("/create-load-web/")
def create_load_web(
    db: Session = Depends(get_db),
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
    """HTMX endpoint: Saves the form data and refreshes the dashboard."""
    
    # Convert string dates to Python date objects
    p_date = datetime.strptime(pickup_date, "%Y-%m-%d").date()
    d_date = datetime.strptime(delivery_date, "%Y-%m-%d").date()
    
    # Create the Pydantic schema
    load_in = schemas.LoadCreate(
        broker_name=broker_name,
        rate_amount=rate_amount,
        load_reference=load_reference,
        pickup_location=pickup_location,
        pickup_date=p_date,
        delivery_location=delivery_location,
        delivery_date=d_date,
        loaded_miles=loaded_miles,
        notes=notes,
        status=models.LoadStatus.ACTIVE
    )
    
    # Save to database
    crud.create_load(db=db, load=load_in)
    
    # HTMX Magic: This header tells the browser to instantly refresh the page 
    # so the new load shows up on the dashboard!
    return Response(headers={"HX-Refresh": "true"})

@router.post("/update-load-status/{load_id}/{new_status}")
def update_load_status(load_id: int, new_status: str, db: Session = Depends(get_db)):
    """HTMX endpoint: Updates the status of a load and refreshes the page."""
    
    # Map the string from the URL to our actual LoadStatus Enum
    status_map = {
        "Delivered": models.LoadStatus.DELIVERED,
        "Invoiced": models.LoadStatus.INVOICED,
    }
    
    if new_status in status_map:
        update_data = schemas.LoadUpdate(status=status_map[new_status])
        crud.update_load(db=db, load_id=load_id, load_update=update_data)
        
    # HX-Refresh tells the browser to reload the dashboard instantly
    return Response(headers={"HX-Refresh": "true"})

@router.get("/settings")
def settings_page(request: Request, db: Session = Depends(get_db)):
    settings = crud.get_settings(db)
    return templates.TemplateResponse(
        request=request, 
        name="settings.html", 
        context={"settings": settings}
    )

@router.post("/settings/save")
def save_settings(
    db: Session = Depends(get_db),
    company_name: str = Form(""),
    street_address: str = Form(""),
    city_state_zip: str = Form(""),
    show_address_on_invoice: bool = Form(False),
    bank_name: str = Form(""),
    account_number: str = Form(""),
    routing_number: str = Form(""),
    show_bank_on_invoice: bool = Form(False)
):
    """HTMX endpoint to save settings and show a success message."""
    settings_data = schemas.SettingsUpdate(
        company_name=company_name,
        street_address=street_address,
        city_state_zip=city_state_zip,
        show_address_on_invoice=show_address_on_invoice,
        bank_name=bank_name,
        account_number=account_number,
        routing_number=routing_number,
        show_bank_on_invoice=show_bank_on_invoice
    )
    crud.update_settings(db, settings_data)
    
    # Return a green success alert that HTMX will swap into the page
    return Response("""
        <div class="alert alert-success alert-dismissible fade show mt-3" role="alert">
            <i class="bi bi-check-circle"></i> Settings saved successfully!
            <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
        </div>
    """)

@router.post("/generate-invoice/{load_id}")
async def generate_invoice(load_id: int, db: Session = Depends(get_db), bol_file: UploadFile = File(...)):
    """Uploads BOL, generates PDF invoice, and marks load as Invoiced."""
    
    load = crud.get_load(db, load_id)
    settings = crud.get_settings(db)
    
    # 1. Save the uploaded BOL image/PDF
    os.makedirs("static/bols", exist_ok=True)
    bol_filename = f"bol_{load_id}_{uuid.uuid4().hex[:6]}_{bol_file.filename}"
    bol_path = f"static/bols/{bol_filename}"
    
    with open(bol_path, "wb") as buffer:
        shutil.copyfileobj(bol_file.file, buffer)
        
    # 2. Generate Invoice Number (e.g., INV-1005)
    invoice_num = f"INV-{1000 + load.id}"
    
    # 3. Render the HTML Template to a string
    html_content = templates.get_template("invoice_template.html").render({
        "load": load,
        "settings": settings,
        "invoice_number": invoice_num,
        "date_today": date.today().strftime("%b %d, %Y")
    })
    
    # 4. Convert HTML to PDF using WeasyPrint
    os.makedirs("static/invoices", exist_ok=True)
    pdf_filename = f"invoice_{invoice_num}.pdf"
    pdf_path = f"static/invoices/{pdf_filename}"
    
    HTML(string=html_content).write_pdf(pdf_path)
    
    # 5. Update Database Load Record
    update_data = schemas.LoadUpdate(
        status=models.LoadStatus.INVOICED,
        invoice_number=invoice_num,
        bol_pdf_path=bol_path,
        rate_con_pdf_path=pdf_path # Reusing this column to store the invoice path for now
    )
    crud.update_load(db, load_id, update_data)
    
    return RedirectResponse(url="/", status_code=303)