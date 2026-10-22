from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class LoadStatus(StrEnum):
    DRAFT = "Draft"
    ACTIVE = "Active"
    DELIVERED = "Delivered"
    INVOICED = "Invoiced"
    PAID = "Paid"


class StopKind(StrEnum):
    PICKUP = "pickup"
    DELIVERY = "delivery"


class Stop(BaseModel):
    """A stop between the first pickup and the final delivery of a multi-stop load."""

    kind: StopKind
    location: str = Field(min_length=1)
    stop_date: date | None = None


class Charge(BaseModel):
    """An extra charge billed on top of the rate, e.g. detention, layover, a lumper fee or TONU."""

    description: str = Field(min_length=1)
    amount: float = Field(gt=0)


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
    empty_miles: int = 0
    deadhead_origin: str | None = None
    status: LoadStatus = LoadStatus.DRAFT
    notes: str | None = None
    rate_con_pdf_path: str | None = None
    bol_pdf_path: str | None = None
    invoice_pdf_path: str | None = None
    created_at: datetime | None = None
    invoiced_at: datetime | None = None
    paid_at: date | None = None
    amount_paid: float | None = None
    charges: list[Charge] = []
    stops: list[Stop] = []

    @property
    def total_amount(self) -> float:
        """The rate plus extra charges: what the invoice bills and the broker owes."""
        return self.rate_amount + sum(charge.amount for charge in self.charges)

    @property
    def route_stops(self) -> list[tuple[StopKind, str, date | None]]:
        """Every stop in route order, first pickup to final delivery, as (kind, location, date)."""
        return [
            (StopKind.PICKUP, self.pickup_location, self.pickup_date),
            *((stop.kind, stop.location, stop.stop_date) for stop in self.stops),
            (StopKind.DELIVERY, self.delivery_location, self.actual_delivery_date or self.delivery_date),
        ]

    @property
    def total_miles(self) -> int:
        """Loaded plus empty (deadhead) miles: everything the truck drove for this load."""
        return (self.loaded_miles or 0) + (self.empty_miles or 0)


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
    empty_miles: int | None = None
    deadhead_origin: str | None = None
    status: LoadStatus | None = None
    notes: str | None = None
    paid_at: date | None = None
    amount_paid: float | None = None
    charges: list[Charge] | None = None
    stops: list[Stop] | None = None
