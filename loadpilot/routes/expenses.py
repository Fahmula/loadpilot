"""Expenses: fuel, repairs, insurance and every other cost, so Analytics can show profit and not just revenue."""

import csv
import io
from datetime import date
from itertools import groupby

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from loadpilot.models import Expense, ExpenseCategory
from loadpilot.templating import templates

router = APIRouter(prefix="/expenses", tags=["Expenses"])

PAGE_SIZE = 100  # the newest expenses shown; the CSV has them all

CATEGORY_ICONS = {
    ExpenseCategory.FUEL: "fuel",
    ExpenseCategory.MAINTENANCE: "wrench",
    ExpenseCategory.TOLLS: "road",
    ExpenseCategory.INSURANCE: "shield",
    ExpenseCategory.TRUCK_PAYMENT: "truck",
    ExpenseCategory.PERMITS: "file-text",
    ExpenseCategory.PARKING: "square-parking",
    ExpenseCategory.OTHER: "receipt",
}

# A spreadsheet runs a cell starting with one of these as a formula; notes are kept as text
FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")


@router.get("", response_class=HTMLResponse)
async def expenses_page(request: Request):
    db = request.app.state.db
    expenses = await db.get_expenses(limit=PAGE_SIZE)
    months = [
        {"month": month, "expenses": items, "total": sum(expense.amount for expense in items)}
        for month, group in groupby(expenses, key=lambda expense: expense.spent_on.replace(day=1))
        for items in [list(group)]
    ]
    context = {
        "months": months,
        "categories": list(ExpenseCategory),
        "category_icons": CATEGORY_ICONS,
        "today": date.today(),
        "hidden": await db.count_expenses() - len(expenses),
        "page_size": PAGE_SIZE,
    }
    return templates.TemplateResponse(request=request, name="expenses.html", context=context)


@router.post("")
async def add_expense(
    request: Request,
    spent_on: date = Form(...),
    category: ExpenseCategory = Form(...),
    amount: float = Form(..., gt=0),
    note: str = Form(""),
):
    expense = Expense(spent_on=spent_on, category=category, amount=amount, note=note.strip() or None)
    await request.app.state.db.add_expense(expense)
    return RedirectResponse(url="/expenses", status_code=303)


@router.post("/{expense_id}/delete")
async def delete_expense(request: Request, expense_id: int):
    if not await request.app.state.db.delete_expense(expense_id):
        raise HTTPException(status_code=404, detail="Expense not found")
    return RedirectResponse(url="/expenses", status_code=303)


@router.get("/export.csv")
async def export_expenses(request: Request):
    """Every expense, oldest first, for the accountant."""
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["Date", "Category", "Amount", "Note"])
    for expense in reversed(await request.app.state.db.get_expenses()):
        note = expense.note or ""
        writer.writerow(
            [
                expense.spent_on,
                expense.category.value,
                f"{expense.amount:.2f}",
                ("'" if note.startswith(FORMULA_STARTS) else "") + note,
            ]
        )
    return Response(
        "﻿" + out.getvalue(),  # the byte order mark makes Excel read it as UTF-8
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="loadpilot-expenses.csv"'},
    )
