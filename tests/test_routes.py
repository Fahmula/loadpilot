import asyncio
from datetime import date

from loadpilot.config import BOL_DIR, INVOICE_DIR
from loadpilot.database import DatabaseManager
from loadpilot.models import LoadStatus
from loadpilot.routes import ui

from .conftest import make_load

LOAD_FORM = {
    "broker_name": "Acme Logistics",
    "broker_email": "dispatch@acme.example",
    "rate_amount": "2500",
    "load_reference": "PO-1234",
    "pickup_location": "Dallas, TX",
    "pickup_date": "2026-10-01",
    "delivery_location": "Atlanta, GA",
    "delivery_date": "2026-10-03",
    "loaded_miles": "1000",
    "notes": "PU# 5521",
}


def seed(db_path, **overrides):
    """Inserts a load directly through the database layer and returns it."""
    return asyncio.run(DatabaseManager(db_path=str(db_path)).add_load(make_load(**overrides)))


def test_health_reports_version(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["version"] == "dev"


def test_pwa_assets_are_served(client):
    assert client.get("/manifest.json").json()["name"] == "LoadPilot"
    assert client.get("/sw.js").status_code == 200
    assert client.get("/static/icon-192.png").headers["content-type"] == "image/png"


def test_pages_render(client):
    for path in ("/", "/loads", "/settings", "/analytics"):
        assert client.get(path).status_code == 200, path


def test_dashboard_shows_status_value_not_enum_name(client, db_path):
    seed(db_path)

    body = client.get("/").text

    assert "LoadStatus." not in body
    assert ">Active<" in body


def test_create_load_from_form_keeps_broker_email(client):
    response = client.post("/create-load-web/", data=LOAD_FORM)

    assert response.status_code == 200
    load = client.get("/api/loads/").json()[0]
    assert load["broker_email"] == "dispatch@acme.example"
    assert load["status"] == "Active"
    assert load["notes"] == "PU# 5521"


def test_mark_delivered_sets_actual_delivery_date(client, db_path):
    load = seed(db_path)

    client.post(f"/update-load-status/{load.id}/Delivered")

    updated = client.get(f"/api/loads/{load.id}").json()
    assert updated["status"] == "Delivered"
    assert updated["actual_delivery_date"] == date.today().isoformat()


def test_api_update_rejects_unknown_fields(client, db_path):
    load = seed(db_path)

    response = client.put(f"/api/loads/{load.id}", json={"rate_amount = 0, broker_name": "pwned"})

    assert response.status_code == 422
    assert client.get(f"/api/loads/{load.id}").json()["broker_name"] == "Acme Logistics"


def test_api_update_rejects_null_required_field(client, db_path):
    load = seed(db_path)

    response = client.put(f"/api/loads/{load.id}", json={"broker_name": None})

    assert response.status_code == 422


def test_api_update_partial(client, db_path):
    load = seed(db_path)

    response = client.put(f"/api/loads/{load.id}", json={"rate_amount": 2750.5})

    assert response.status_code == 200
    assert response.json()["rate_amount"] == 2750.5
    assert response.json()["broker_name"] == "Acme Logistics"


def test_api_missing_load_returns_404(client):
    assert client.get("/api/loads/999").status_code == 404
    assert client.put("/api/loads/999", json={"notes": "x"}).status_code == 404
    assert client.delete("/api/loads/999").status_code == 404


def test_generate_invoice_for_missing_load_returns_404(client):
    response = client.post("/generate-invoice/999", files={"bol_file": ("bol.pdf", b"%PDF-1.4", "application/pdf")})

    assert response.status_code == 404


def test_generate_invoice(client, db_path):
    load = seed(db_path, status=LoadStatus.DELIVERED)

    response = client.post(
        f"/generate-invoice/{load.id}",
        files={"bol_file": ("../../escape.pdf", b"%PDF-1.4", "application/pdf")},
        follow_redirects=False,
    )

    assert response.status_code == 303
    invoiced = client.get(f"/api/loads/{load.id}").json()
    assert invoiced["status"] == "Invoiced"
    assert invoiced["invoice_number"] == f"INV-{1000 + load.id}"
    assert invoiced["invoice_pdf_path"] == f"static/invoices/invoice_INV-{1000 + load.id}.pdf"
    assert invoiced["rate_con_pdf_path"] is None
    assert (INVOICE_DIR / f"invoice_INV-{1000 + load.id}.pdf").read_bytes().startswith(b"%PDF")
    # The uploaded BOL stays inside the BOL directory despite the "../" in its name
    bol_path = (BOL_DIR / invoiced["bol_pdf_path"].removeprefix("static/bols/")).resolve()
    assert bol_path.parent == BOL_DIR.resolve()
    assert client.get("/" + invoiced["invoice_pdf_path"]).status_code == 200


def test_parse_rate_con_prefills_form(client, monkeypatch):
    extracted = {**LOAD_FORM, "rate_amount": 2500.0, "loaded_miles": 1000}
    monkeypatch.setattr(ui, "parse_rate_con_with_ai", lambda files, key: extracted)

    response = client.post("/parse-rate-con-html/", files={"files": ("ratecon.png", b"img", "image/png")})

    assert response.status_code == 200
    assert 'value="dispatch@acme.example"' in response.text
    assert "PU# 5521" in response.text


def test_parse_rate_con_failure_shows_error(client, monkeypatch):
    monkeypatch.setattr(ui, "parse_rate_con_with_ai", lambda files, key: None)

    response = client.post("/parse-rate-con-html/", files={"files": ("ratecon.png", b"img", "image/png")})

    assert response.status_code == 400
    assert "AI extraction failed" in response.text


def test_parse_rate_con_error_is_html_escaped(client, monkeypatch):
    def explode(files, key):
        raise RuntimeError("<script>alert(1)</script>")

    monkeypatch.setattr(ui, "parse_rate_con_with_ai", explode)

    response = client.post("/parse-rate-con-html/", files={"files": ("ratecon.png", b"img", "image/png")})

    assert response.status_code == 500
    assert "<script>" not in response.text
    assert "&lt;script&gt;" in response.text
