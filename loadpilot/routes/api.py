import asyncio
from datetime import date

import aiosqlite
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from loadpilot.documents import remove_load_documents
from loadpilot.models import Load, LoadStatus, LoadUpdate

router = APIRouter(prefix="/api/loads", tags=["Loads"])
payments_router = APIRouter(prefix="/api/payments", tags=["Payments"])


class Payment(BaseModel):
    """A broker payment reported by another app (bank feed, bookkeeping), matched to its invoice."""

    invoice_number: str
    amount: float = Field(gt=0)
    paid_at: date | None = None  # defaults to today


@router.post("/", response_model=Load)
async def create_load(load: Load, request: Request):
    db = request.app.state.db
    return await db.add_load(load)


@router.get("/", response_model=list[Load])
async def read_loads(request: Request, skip: int = 0, limit: int = 100, status: LoadStatus | None = None):
    db = request.app.state.db
    return await db.get_loads(skip=skip, limit=limit, status=status)


@router.get("/{load_id}", response_model=Load)
async def read_load(load_id: int, request: Request):
    db = request.app.state.db
    db_load = await db.get_load(load_id)
    if not db_load:
        raise HTTPException(status_code=404, detail="Load not found")
    return db_load


@router.put("/{load_id}", response_model=Load)
async def update_load(load_id: int, load_update: LoadUpdate, request: Request):
    db = request.app.state.db
    try:
        db_load = await db.update_load(load_id, load_update.model_dump(exclude_unset=True))
    except aiosqlite.IntegrityError as e:
        raise HTTPException(status_code=422, detail=f"Invalid load update: {e}") from e
    if not db_load:
        raise HTTPException(status_code=404, detail="Load not found")
    return db_load


@router.delete("/{load_id}")
async def delete_load(load_id: int, request: Request):
    db = request.app.state.db
    load = await db.get_load(load_id)
    if not load or not await db.delete_load(load_id):
        raise HTTPException(status_code=404, detail="Load not found")
    await asyncio.to_thread(remove_load_documents, load)
    return {"message": "Load deleted successfully"}


@payments_router.post("/", response_model=Load)
async def record_payment(payment: Payment, request: Request):
    db = request.app.state.db
    load = await db.get_load_by_invoice_number(payment.invoice_number)
    if not load:
        raise HTTPException(status_code=404, detail="No load has that invoice number")
    if load.status != LoadStatus.INVOICED:
        raise HTTPException(status_code=409, detail=f"Load is {load.status.value}, not awaiting payment")
    update = {"status": LoadStatus.PAID, "paid_at": payment.paid_at or date.today(), "amount_paid": payment.amount}
    return await db.update_load(load.id, update)
