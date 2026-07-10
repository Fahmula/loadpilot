from enum import Enum
from datetime import date
from pydantic import BaseModel

class LoadStatus(str, Enum):
    DRAFT = "Draft"
    ACTIVE = "Active"
    DELIVERED = "Delivered"
    INVOICED = "Invoiced"

class Load(BaseModel):
    id: int | None = None
    invoice_number: str | None = None
    broker_name: str
    broker_email: str | None = None
    load_reference: str
    rate_amount: float
    pickup_location: str
    pickup_date: date
    delivery_location: str
    delivery_date: date
    loaded_miles: int = 0
    status: LoadStatus = LoadStatus.DRAFT
    notes: str | None = None
    rate_con_pdf_path: str | None = None
    bol_pdf_path: str | None = None