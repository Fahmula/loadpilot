from datetime import date, datetime

import pytest

from loadpilot.analytics import (
    build_analytics,
    days_overdue,
    due_date,
    payment_terms,
    previous_window,
    receivables,
)
from loadpilot.models import Charge, LoadStatus

from .conftest import make_load

TODAY = date(2026, 10, 20)  # a Tuesday


def delivered(day: date, rate: float = 1000.0, miles: int = 500, broker: str = "Acme Logistics", empty: int = 0):
    return make_load(
        status=LoadStatus.DELIVERED,
        actual_delivery_date=day,
        rate_amount=rate,
        loaded_miles=miles,
        empty_miles=empty,
        broker_name=broker,
    )


@pytest.mark.parametrize(
    ("period", "start", "expected"),
    [
        ("week", date(2026, 10, 19), (date(2026, 10, 12), date(2026, 10, 13))),
        ("month", date(2026, 10, 1), (date(2026, 9, 1), date(2026, 9, 20))),
        ("year", date(2026, 1, 1), (date(2025, 1, 1), date(2025, 10, 20))),
    ],
)
def test_previous_window_is_the_same_stretch_of_last_period(period, start, expected):
    assert previous_window(period, start, TODAY) == expected


def test_previous_window_is_clamped_to_a_shorter_month():
    # Mar 31 compares against all of February, not a date in March
    assert previous_window("month", date(2026, 3, 1), date(2026, 3, 31)) == (date(2026, 2, 1), date(2026, 2, 28))


def test_month_totals_and_change():
    loads = [
        delivered(date(2026, 10, 2), rate=3000, miles=1000),
        delivered(date(2026, 10, 15), rate=1000, miles=500, broker="Blue Freight"),
        delivered(date(2026, 9, 10), rate=2000, miles=1000),  # inside Sep 1-20: counts as previous
        delivered(date(2026, 9, 25), rate=9999, miles=1),  # after Sep 20: not comparable
    ]

    a = build_analytics(loads, "month", TODAY)

    assert a["current"].revenue == 4000
    assert a["current"].miles == 1500
    assert a["current"].loads == 2
    assert a["current"].rpm == pytest.approx(4000 / 1500)
    assert a["changes"]["revenue"] == pytest.approx(100.0)
    assert a["changes"]["loads"] == pytest.approx(100.0)
    assert [b["name"] for b in a["brokers"]] == ["Acme Logistics", "Blue Freight"]
    assert a["compare_label"] == "last month"


def test_change_is_none_without_previous_data_or_for_all_time():
    loads = [delivered(date(2026, 10, 2))]

    assert build_analytics(loads, "month", TODAY)["changes"]["revenue"] is None
    assert build_analytics(loads, "all", TODAY)["changes"]["revenue"] is None


def test_all_time_includes_everything():
    loads = [delivered(date(2024, 1, 5)), delivered(date(2026, 10, 2))]

    assert build_analytics(loads, "all", TODAY)["current"].loads == 2


def test_unknown_period_falls_back_to_month():
    assert build_analytics([], "decade", TODAY)["period"] == "month"


def test_rpm_with_no_miles_is_zero():
    assert build_analytics([delivered(date(2026, 10, 2), miles=0)], "month", TODAY)["current"].rpm == 0.0


def test_trend_covers_last_six_months_scaled_to_peak():
    loads = [delivered(date(2026, 10, 2), rate=2000), delivered(date(2026, 8, 3), rate=1000)]

    trend = build_analytics(loads, "month", TODAY)["trend"]

    assert [m["label"] for m in trend] == ["May", "Jun", "Jul", "Aug", "Sep", "Oct"]
    assert [m["percent"] for m in trend] == [0, 0, 0, 50, 0, 100]


def test_all_in_rpm_counts_empty_miles():
    loads = [
        delivered(date(2026, 10, 2), rate=3000, miles=1000, empty=200),
        delivered(date(2026, 10, 15), rate=1000, miles=500, empty=300),
    ]

    current = build_analytics(loads, "month", TODAY)["current"]

    assert current.rpm == pytest.approx(4000 / 1500)
    assert current.empty_miles == 500
    assert current.all_in_rpm == pytest.approx(2.0)
    assert current.deadhead_pct == pytest.approx(25.0)


def test_all_in_rpm_without_miles_is_zero():
    current = build_analytics([], "month", TODAY)["current"]

    assert current.all_in_rpm == 0.0
    assert current.deadhead_pct == 0.0


def invoiced(day: date, rate: float = 1000.0):
    return make_load(
        status=LoadStatus.INVOICED, invoiced_at=datetime.combine(day, datetime.min.time()), rate_amount=rate
    )


@pytest.mark.parametrize(
    ("settings", "terms"),
    [({}, 30), ({"payment_terms_days": "15"}, 15), ({"payment_terms_days": ""}, 30), ({"payment_terms_days": "x"}, 30)],
)
def test_payment_terms(settings, terms):
    assert payment_terms(settings) == terms


def test_invoice_is_overdue_after_terms():
    load = invoiced(date(2026, 9, 10))

    assert due_date(load, 30) == date(2026, 10, 10)
    assert days_overdue(load, 30, TODAY) == 10
    assert days_overdue(load, 45, TODAY) == 0


def test_paid_or_undated_invoices_are_never_overdue():
    paid = invoiced(date(2026, 1, 1)).model_copy(update={"status": LoadStatus.PAID})
    legacy = make_load(status=LoadStatus.INVOICED)  # invoiced before invoiced_at was recorded

    assert days_overdue(paid, 30, TODAY) == 0
    assert days_overdue(legacy, 30, TODAY) == 0
    assert due_date(legacy, 30) is None


def test_receivables_totals_and_overdue():
    unpaid = [invoiced(date(2026, 9, 1), rate=2000), invoiced(date(2026, 10, 15), rate=500)]

    owed = receivables(unpaid, 30, TODAY)

    assert owed["total"] == 2500
    assert (owed["overdue_count"], owed["overdue_total"]) == (1, 2000)


def test_revenue_includes_extra_charges():
    load = delivered(date(2026, 10, 2), rate=1000, miles=500).model_copy(
        update={"charges": [Charge(description="Detention", amount=200.0)]}
    )

    current = build_analytics([load], "month", TODAY)["current"]

    assert current.revenue == 1200
    assert receivables([load], 30, TODAY)["total"] == 1200
