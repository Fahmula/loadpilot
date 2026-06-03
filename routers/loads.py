import pypdf
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
from typing import List, Optional

import crud
import models
import schemas
import services
from database import get_db

router = APIRouter(
    prefix="/api/loads",
    tags=["Loads"]
)

@router.post("/", response_model=schemas.LoadResponse)
def create_load(load: schemas.LoadCreate, db: Session = Depends(get_db)):
    """Create a new load."""
    return crud.create_load(db=db, load=load)

@router.get("/", response_model=List[schemas.LoadResponse])
def read_loads(skip: int = 0, limit: int = 100, status: Optional[models.LoadStatus] = None, db: Session = Depends(get_db)):
    """Get all loads. Optional: filter by status (e.g., ?status=Active)"""
    loads = crud.get_loads(db, skip=skip, limit=limit, status=status)
    return loads

@router.get("/{load_id}", response_model=schemas.LoadResponse)
def read_load(load_id: int, db: Session = Depends(get_db)):
    """Get a specific load by ID."""
    db_load = crud.get_load(db, load_id=load_id)
    if db_load is None:
        raise HTTPException(status_code=404, detail="Load not found")
    return db_load

@router.put("/{load_id}", response_model=schemas.LoadResponse)
def update_load(load_id: int, load_update: schemas.LoadUpdate, db: Session = Depends(get_db)):
    """Update a load (e.g., change status to Delivered)."""
    db_load = crud.update_load(db, load_id=load_id, load_update=load_update)
    if db_load is None:
        raise HTTPException(status_code=404, detail="Load not found")
    return db_load

@router.delete("/{load_id}")
def delete_load(load_id: int, db: Session = Depends(get_db)):
    """Delete a load."""
    success = crud.delete_load(db, load_id=load_id)
    if not success:
        raise HTTPException(status_code=404, detail="Load not found")
    return {"message": "Load deleted successfully"}

@router.post("/parse-rate-con/")
async def parse_rate_con(file: UploadFile = File(...)):
    """Accepts a PDF Rate Con, extracts text, and uses Gemini to parse it."""
    try:
        print(f"Received file: {file.filename}, content type: {file.content_type}")  # Debugging line to confirm file upload
        extracted_text = ""
        
        # Read the PDF file directly from memory
        pdf_reader = pypdf.PdfReader(file.file)
        for page in pdf_reader.pages:
            extracted_text += page.extract_text() + "\n"
            
        # Send text to our Gemini AI service
        parsed_data = services.parse_rate_con_with_ai(extracted_text)
        
        if not parsed_data:
            from fastapi import HTTPException
            raise HTTPException(status_code=500, detail="Gemini failed to parse the document.")
            
        return {"status": "success", "data": parsed_data}
        
    except Exception as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail=str(e))