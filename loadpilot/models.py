from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class LoadStatus(StrEnum):
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
    actual_delivery_date: date | None = None
    loaded_miles: int = 0
    status: LoadStatus = LoadStatus.DRAFT
    notes: str | None = None
    rate_con_pdf_path: str | None = None
    bol_pdf_path: str | None = None
    invoice_pdf_path: str | None = None
    created_at: datetime | None = None
    invoiced_at: datetime | None = None


class User(BaseModel):
    id: int
    username: str
    password_hash: str


class LoadUpdate(BaseModel):
    """Partial update for a load; only fields the client sends are written."""

    model_config = ConfigDict(extra="forbid")

    invoice_number: str | None = None
    broker_name: str | None = None
    broker_email: str | None = None
    load_reference: str | None = None
    rate_amount: float | None = None
    pickup_location: str | None = None
    pickup_date: date | None = None
    delivery_location: str | None = None
    delivery_date: date | None = None
    actual_delivery_date: date | None = None
    loaded_miles: int | None = None
    status: LoadStatus | None = None
    notes: str | None = None
