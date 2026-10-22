from datetime import date, datetime
from pathlib import Path

from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape

from loadpilot import __version__
from loadpilot.analytics import days_overdue, due_date
from loadpilot.icons import ICONS
from loadpilot.models import LoadStatus

templates = Jinja2Templates(directory=Path(__file__).resolve().parents[1] / "templates")

# Badge colour and label for each load status, shared by every page
STATUS_STYLES = {
    LoadStatus.DRAFT: ("badge-neutral", "Draft"),
    LoadStatus.ACTIVE: ("badge-warning", "Active"),
    LoadStatus.DELIVERED: ("badge-success", "Delivered"),
    LoadStatus.INVOICED: ("badge-info", "Invoiced"),
    LoadStatus.PAID: ("badge-secondary", "Paid"),
}

ALERT_STYLES = {
    "success": ("alert-success", "circle-check"),
    "error": ("alert-error", "circle-alert"),
    "warning": ("alert-warning", "circle-alert"),
    "info": ("alert-info", "info"),
}


def icon(name: str, class_: str = "size-5") -> Markup:
    """Inline Lucide SVG icon; raises KeyError for names missing from scripts/generate_icons.py."""
    # Safe: the class is escaped and the SVG markup comes from the generated icons module, never user input
    return Markup(  # noqa: S704
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        f'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="{escape(class_)}" '
        f'aria-hidden="true">{ICONS[name]}</svg>'
    )


def alert_response(message: str, kind: str = "error", status_code: int = 200) -> HTMLResponse:
    """A small daisyUI alert for HTMX to swap into the page. The message is escaped."""
    style, icon_name = ALERT_STYLES[kind]
    return HTMLResponse(
        f'<div role="alert" class="alert alert-soft {style} mt-3 text-sm">{icon(icon_name)}'
        f"<span>{escape(message)}</span></div>",
        status_code=status_code,
    )


def last4(value: str | None) -> str:
    """Masks a secret down to its last four characters, e.g. •••• 1234."""
    return f"•••• {value[-4:]}" if value else ""


def money(value: float | None, cents: bool = True) -> str:
    return f"${value or 0:,.2f}" if cents else f"${value or 0:,.0f}"


def miles(value: int | None) -> str:
    return f"{value or 0:,} mi"


def rpm(rate: float, loaded_miles: int | None) -> str:
    return f"${rate / loaded_miles:.2f}/mi" if loaded_miles else "—"


def short_date(value: date | datetime | None, with_year: bool = True) -> str:
    if not value:
        return "—"
    return value.strftime("%b %-d, %Y" if with_year else "%b %-d")


templates.env.globals.update(icon=icon, app_version=__version__, status_styles=STATUS_STYLES)
templates.env.filters.update(money=money, miles=miles, short_date=short_date, last4=last4)
templates.env.globals.update(rpm=rpm, due_date=due_date, days_overdue=days_overdue)
