from fastapi import APIRouter, Request, Depends, UploadFile, File, Form
from fastapi.templating import Jinja2Templates
from fastapi.responses import Response
from httpx import request
from sqlalchemy.orm import Session
from datetime import datetime

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
        notes=notes,
        status=models.LoadStatus.ACTIVE
    )
    
    # Save to database
    crud.create_load(db=db, load=load_in)
    
    # HTMX Magic: This header tells the browser to instantly refresh the page 
    # so the new load shows up on the dashboard!
    return Response(headers={"HX-Refresh": "true"})