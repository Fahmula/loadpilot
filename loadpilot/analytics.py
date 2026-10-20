"""Earnings analytics and the dashboard's activity feed, computed from loads in Python."""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from loadpilot.models import Load

PERIODS = {"week": "This Week", "month": "This Month", "year": "This Year", "all": "All Time"}
PREVIOUS_LABELS = {"week": "last week", "month": "last month", "year": "last year"}
TREND_MONTHS = 6
TOP_BROKERS = 5


@dataclass
class Totals:
    revenue: float = 0.0
    miles: int = 0
    loads: int = 0

    @property
    def rpm(self) -> float:
        return self.revenue / self.miles if self.miles else 0.0

    def add(self, load: Load):
        self.revenue += load.rate_amount
        self.miles += load.loaded_miles or 0
        self.loads += 1


def earned_on(load: Load) -> date:
    """The day a load counts toward earnings: when it was actually delivered."""
    return load.actual_delivery_date or load.delivery_date


def period_start(period: str, today: date) -> date | None:
    if period == "week":
        return today - timedelta(days=today.weekday())
    if period == "month":
        return today.replace(day=1)
    if period == "year":
        return today.replace(month=1, day=1)
    return None


def previous_window(period: str, start: date, today: date) -> tuple[date, date]:
    """The same stretch of the previous period, e.g. Sep 1-20 when today is Oct 20."""
    if period == "week":
        previous_start = start - timedelta(days=7)
    elif period == "month":
        previous_start = (start - timedelta(days=1)).replace(day=1)
    else:
        previous_start = start.replace(year=start.year - 1)
    previous_end = min(previous_start + (today - start), start - timedelta(days=1))
    return previous_start, previous_end


def percent_change(current: float, previous: float) -> float | None:
    if not previous:
        return None
    return (current - previous) / previous * 100


def month_starts(today: date, count: int) -> list[date]:
    starts = [today.replace(day=1)]
    while len(starts) < count:
        starts.insert(0, (starts[0] - timedelta(days=1)).replace(day=1))
    return starts


def build_analytics(loads: list[Load], period: str = "month", today: date | None = None) -> dict:
    today = today or date.today()
    if period not in PERIODS:
        period = "month"

    start = period_start(period, today)
    current, previous = Totals(), Totals()
    brokers: dict[str, Totals] = {}
    previous_start = previous_end = None
    if start:
        previous_start, previous_end = previous_window(period, start, today)

    for load in loads:
        day = earned_on(load)
        if start is None or start <= day <= today:
            current.add(load)
            brokers.setdefault(load.broker_name, Totals()).add(load)
        elif previous_start and previous_start <= day <= previous_end:
            previous.add(load)

    # Revenue per calendar month for the chart, oldest first
    starts = month_starts(today, TREND_MONTHS)
    trend = []
    for month_start in starts:
        revenue = sum(
            load.rate_amount
            for load in loads
            if (earned_on(load).year, earned_on(load).month) == (month_start.year, month_start.month)
        )
        trend.append({"label": month_start.strftime("%b"), "month": month_start, "revenue": revenue})
    peak = max((month["revenue"] for month in trend), default=0)
    for month in trend:
        month["percent"] = round(month["revenue"] / peak * 100) if peak else 0

    top_brokers = sorted(brokers.items(), key=lambda item: item[1].revenue, reverse=True)[:TOP_BROKERS]
    has_comparison = start is not None

    return {
        "period": period,
        "period_label": PERIODS[period],
        "periods": PERIODS,
        "compare_label": PREVIOUS_LABELS.get(period),
        "current": current,
        "changes": {
            "revenue": percent_change(current.revenue, previous.revenue) if has_comparison else None,
            "miles": percent_change(current.miles, previous.miles) if has_comparison else None,
            "rpm": percent_change(current.rpm, previous.rpm) if has_comparison else None,
            "loads": percent_change(current.loads, previous.loads) if has_comparison else None,
        },
        "trend": trend,
        "trend_peak": peak,
        "brokers": [{"name": name, "totals": totals} for name, totals in top_brokers],
    }


def recent_activity(loads: list[Load], limit: int = 5) -> list[dict]:
    """Newest-first events (added, delivered, invoiced) for the dashboard."""
    events = []
    for load in loads:
        if load.invoiced_at:
            events.append({"kind": "invoiced", "at": load.invoiced_at, "load": load})
        if load.actual_delivery_date:
            events.append(
                {"kind": "delivered", "at": datetime.combine(load.actual_delivery_date, time()), "load": load}
            )
        if load.created_at:
            events.append({"kind": "added", "at": load.created_at, "load": load})
    events.sort(key=lambda event: event["at"], reverse=True)
    return events[:limit]
