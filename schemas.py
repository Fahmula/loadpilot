# schemas.py
from pydantic import BaseModel
from typing import Optional
from datetime import date
from models import LoadStatus

# Base fields that are common across creation, reading, and updating
class LoadBase(BaseModel):
    broker_name: str
    broker_email: Optional[str] = None
    load_reference: str
    rate_amount: float
    pickup_location: str
    pickup_date: date
    delivery_location: str
    loaded_miles: int = 0 
    delivery_date: date
    status: LoadStatus = LoadStatus.DRAFT
    notes: Optional[str] = None

# Schema for creating a new load (inherits everything from LoadBase)
class LoadCreate(LoadBase):
    pass

class LoadUpdate(BaseModel):
    broker_name: Optional[str] = None
    broker_email: Optional[str] = None
    load_reference: Optional[str] = None
    rate_amount: Optional[float] = None
    pickup_location: Optional[str] = None
    pickup_date: Optional[date] = None
    delivery_location: Optional[str] = None
    delivery_date: Optional[date] = None
    status: Optional[LoadStatus] = None
    notes: Optional[str] = None
    rate_con_pdf_path: Optional[str] = None
    bol_pdf_path: Optional[str] = None

# Schema for what the API returns (includes the DB ID)
class LoadResponse(LoadBase):
    id: int
    rate_con_pdf_path: Optional[str] = None
    bol_pdf_path: Optional[str] = None

    class Config:
        from_attributes = True  # This allows Pydantic to read SQLAlchemy models
        
# Schema for updating company settings        
class SettingsUpdate(BaseModel):
    company_name: Optional[str] = None
    street_address: Optional[str] = None
    city_state_zip: Optional[str] = None
    show_address_on_invoice: bool = True
    bank_name: Optional[str] = None
    account_number: Optional[str] = None
    routing_number: Optional[str] = None
    show_bank_on_invoice: bool = False