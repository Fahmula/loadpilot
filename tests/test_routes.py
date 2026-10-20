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
    for path in ("/", "/loads", "/loads/new", "/settings", "/analytics", "/analytics?period=all"):
        assert client.get(path).status_code == 200, path


def test_dashboard_shows_status_value_not_enum_name(client, db_path):
    seed(db_path)

    body = client.get("/").text

    assert "LoadStatus." not in body
    assert "Active" in body
    assert "Current Load" in body


def test_create_load_from_form_keeps_broker_email(client):
    response = client.post("/create-load-web/", data=LOAD_FORM)

    assert response.status_code == 200
    load = client.get("/api/loads/").json()[0]
    assert response.headers["HX-Redirect"] == f"/loads/{load['id']}"
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
    assert response.headers["location"] == f"/loads/{load.id}"
    invoiced = client.get(f"/api/loads/{load.id}").json()
    assert invoiced["invoiced_at"] is not None
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


# -----------------------------------------
# Redesigned pages
# -----------------------------------------
def test_load_detail_shows_next_step_for_each_status(client, db_path):
    active = seed(db_path, status=LoadStatus.ACTIVE)
    delivered = seed(db_path, status=LoadStatus.DELIVERED, actual_delivery_date=date(2026, 10, 3))
    invoiced = seed(
        db_path, status=LoadStatus.INVOICED, invoice_number="INV-1003", invoice_pdf_path="static/invoices/x.pdf"
    )

    assert f"/update-load-status/{active.id}/Delivered" in client.get(f"/loads/{active.id}").text
    assert f"/generate-invoice/{delivered.id}" in client.get(f"/loads/{delivered.id}").text
    invoiced_page = client.get(f"/loads/{invoiced.id}").text
    assert "Invoice INV-1003" in invoiced_page
    assert "/static/invoices/x.pdf" in invoiced_page


def test_load_detail_missing_load_is_404(client):
    assert client.get("/loads/999").status_code == 404


def test_loads_tabs_filter_by_status(client, db_path):
    seed(db_path, load_reference="ACTIVE-1", status=LoadStatus.ACTIVE)
    seed(db_path, load_reference="DONE-1", status=LoadStatus.INVOICED)

    page = client.get("/loads?status=Active").text
    partial = client.get("/loads?status=Invoiced", headers={"HX-Request": "true"}).text

    assert "#ACTIVE-1" in page and "#DONE-1" not in page
    assert "#DONE-1" in partial and "#ACTIVE-1" not in partial
    assert "<html" not in partial


def test_loads_search(client, db_path):
    seed(db_path, broker_name="Acme Logistics", load_reference="A-1")
    seed(db_path, broker_name="Blue Freight", load_reference="B-1")

    body = client.get("/loads?search=blue", headers={"HX-Request": "true"}).text

    assert "#B-1" in body and "#A-1" not in body


def test_delete_load_redirects_to_loads(client, db_path):
    load = seed(db_path)

    response = client.post(f"/delete-load-web/{load.id}")

    assert response.headers["HX-Redirect"] == "/loads"
    assert client.get(f"/api/loads/{load.id}").status_code == 404


def test_dashboard_lists_loads_ready_to_invoice_and_activity(client, db_path):
    delivered = seed(db_path, status=LoadStatus.DELIVERED, delivery_location="Smiley, TX")

    body = client.get("/").text

    assert "Ready to Invoice" in body
    assert f"/loads/{delivered.id}#invoice" in body
    assert "Load Added" in body


def test_new_load_page_warns_without_api_key(client, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    assert "Add your Gemini API key" in client.get("/loads/new").text


def test_analytics_period_picker(client, db_path):
    seed(db_path, status=LoadStatus.DELIVERED, actual_delivery_date=date.today(), rate_amount=1234.0)

    for period, label in (("week", "This Week"), ("month", "This Month"), ("year", "This Year"), ("all", "All Time")):
        body = client.get(f"/analytics?period={period}").text
        assert label in body
        assert "$1,234.00" in body


# -----------------------------------------
# Settings cards
# -----------------------------------------
def test_settings_section_edit_and_save(client):
    edit = client.get("/settings/section/company?edit=true").text
    saved = client.post(
        "/settings/section/company",
        data={"company_name": "Fahmula Trucking LLC", "street_address": "491 Arctic St", "city_state_zip": "CT"},
    ).text

    assert 'name="company_name"' in edit
    assert "Fahmula Trucking LLC" in saved
    assert 'name="company_name"' not in saved
    assert "Fahmula Trucking LLC" in client.get("/settings").text


def test_settings_masks_secrets(client):
    client.post("/settings/section/bank", data={"bank_name": "Sikorsky CU", "account_number": "9876541234"})
    client.post("/settings/section/ai", data={"gemini_api_key": "AIzaSecretKeyAbcd"})

    body = client.get("/settings").text

    assert "•••• 1234" in body and "9876541234" not in body
    assert "•••• Abcd" in body and "AIzaSecretKeyAbcd" not in body


def test_blank_api_key_keeps_existing_key(client, db_path):
    client.post("/settings/section/ai", data={"gemini_api_key": "AIzaSecretKeyAbcd"})
    client.post("/settings/section/ai", data={"gemini_api_key": "  "})

    db = DatabaseManager(db_path=str(db_path))
    assert asyncio.run(db.get_setting("gemini_api_key")) == "AIzaSecretKeyAbcd"


def test_settings_toggles(client, db_path):
    db = DatabaseManager(db_path=str(db_path))

    assert client.post("/settings/toggle/show_bank_on_invoice", data={"enabled": "true"}).status_code == 204
    assert asyncio.run(db.get_all_settings())["show_bank_on_invoice"] is True
    client.post("/settings/toggle/show_bank_on_invoice")
    assert asyncio.run(db.get_all_settings())["show_bank_on_invoice"] is False
    assert client.post("/settings/toggle/gemini_api_key", data={"enabled": "true"}).status_code == 404


def test_unknown_settings_section_is_rejected(client):
    assert client.get("/settings/section/password").status_code == 422
