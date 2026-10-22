import asyncio
import re
from datetime import date

from loadpilot.ai import AIError, Model
from loadpilot.config import BOL_DIR, INVOICE_DIR, RATECON_DIR
from loadpilot.database import DatabaseManager
from loadpilot.models import LoadStatus
from loadpilot.routes import settings as settings_routes
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


def test_create_load_saves_deadhead(client):
    client.post("/create-load-web/", data={**LOAD_FORM, "empty_miles": "120", "deadhead_origin": " Waco, TX "})

    load = client.get("/api/loads/").json()[0]
    assert load["empty_miles"] == 120
    assert load["deadhead_origin"] == "Waco, TX"


def test_create_load_without_deadhead_defaults_to_zero(client):
    client.post("/create-load-web/", data=LOAD_FORM)

    load = client.get("/api/loads/").json()[0]
    assert load["empty_miles"] == 0
    assert load["deadhead_origin"] is None


def test_manual_form_offers_last_delivery_as_deadhead_start(client, db_path):
    seed(db_path, delivery_location="Memphis, TN")

    html = client.get("/manual-load-form/").text

    assert 'name="deadhead_origin"' in html
    assert 'name="empty_miles"' in html
    assert 'data-last-delivery="Memphis, TN"' in html


def test_geo_nearest(client):
    assert client.get("/geo/nearest", params={"lat": 32.7767, "lon": -96.797}).json() == {"location": "Dallas, TX"}
    assert client.get("/geo/nearest", params={"lat": 30, "lon": -40}).status_code == 404


def test_geo_estimate(client):
    miles = client.get("/geo/estimate", params={"origin": "Dallas, TX", "destination": "Houston, TX"}).json()["miles"]
    assert 240 <= miles <= 300
    unknown = client.get("/geo/estimate", params={"origin": "Nowhere, ZZ", "destination": "Houston, TX"}).json()
    assert unknown == {"miles": None}


def test_geo_requires_login(anon_client):
    response = anon_client.get("/geo/estimate", follow_redirects=False)
    assert response.status_code in (303, 401)


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


def configure_ai(client, provider="gemini", api_key="test-key-1234", model="test-model"):
    client.post("/settings/section/ai", data={"provider": provider, "api_key": api_key, "model": model})


def test_parse_rate_con_prefills_form(client, monkeypatch):
    configure_ai(client)
    extracted = {**LOAD_FORM, "rate_amount": 2500.0, "loaded_miles": 1000}
    calls = []
    monkeypatch.setattr(ui, "extract_rate_con", lambda ai, model, pages: calls.append((ai, model, pages)) or extracted)

    response = client.post("/parse-rate-con-html/", files={"files": ("ratecon.png", b"img", "image/png")})

    assert response.status_code == 200
    assert 'value="dispatch@acme.example"' in response.text
    assert "PU# 5521" in response.text
    ai, model, pages = calls[0]
    assert (ai.provider.key, ai.api_key, model) == ("gemini", "test-key-1234", "test-model")
    assert pages == [(b"img", "image/png", "ratecon.png")]


def test_scanned_rate_con_is_saved_with_the_load(client, monkeypatch):
    configure_ai(client)
    monkeypatch.setattr(ui, "extract_rate_con", lambda ai, model, pages: LOAD_FORM)

    form = client.post("/parse-rate-con-html/", files={"files": ("ratecon.pdf", b"%PDF-1.4 rc", "application/pdf")})
    pending = re.search(r'name="rate_con_file" value="([^"]+)"', form.text).group(1)
    client.post("/create-load-web/", data={**LOAD_FORM, "rate_con_file": pending})

    load = client.get("/api/loads/").json()[0]
    assert load["rate_con_pdf_path"].startswith(f"static/ratecons/ratecon_{load['id']}_")
    assert client.get("/" + load["rate_con_pdf_path"]).content == b"%PDF-1.4 rc"
    assert "Rate Con" in client.get(f"/loads/{load['id']}").text


def test_create_load_ignores_a_bad_rate_con_reference(client):
    client.post("/create-load-web/", data={**LOAD_FORM, "rate_con_file": "../loadpilot.db"})

    assert client.get("/api/loads/").json()[0]["rate_con_pdf_path"] is None


def test_attach_rate_con_to_an_existing_load(client, db_path):
    load = seed(db_path)
    assert "No rate con on file" in client.get(f"/loads/{load.id}").text

    response = client.post(
        f"/loads/{load.id}/rate-con",
        files={"rate_con": ("../../rc.pdf", b"%PDF-1.4", "application/pdf")},
        follow_redirects=False,
    )

    assert response.status_code == 303
    path = client.get(f"/api/loads/{load.id}").json()["rate_con_pdf_path"]
    assert (RATECON_DIR / path.removeprefix("static/ratecons/")).resolve().parent == RATECON_DIR.resolve()


def test_attach_rate_con_rejects_other_file_types(client, db_path):
    load = seed(db_path)

    exe = {"rate_con": ("x.exe", b"MZ", "application/x-msdownload")}
    pdf = {"rate_con": ("a.pdf", b"%PDF", "application/pdf")}

    assert client.post(f"/loads/{load.id}/rate-con", files=exe).status_code == 400
    assert client.post("/loads/999/rate-con", files=pdf).status_code == 404


def test_edit_page_is_prefilled(client, db_path):
    load = seed(db_path, empty_miles=40, deadhead_origin="Waco, TX")

    html = client.get(f"/loads/{load.id}/edit").text

    assert f'hx-post="/loads/{load.id}/edit"' in html
    assert 'value="Acme Logistics"' in html
    assert 'value="2026-10-01"' in html
    assert 'value="Waco, TX"' in html
    assert "Save Changes" in html
    assert "data-auto-locate" not in html  # GPS describes now, not when this load ran
    assert 'name="actual_delivery_date"' not in html


def test_edit_load_updates_fields_and_keeps_the_rest(client, db_path):
    load = seed(db_path, status=LoadStatus.INVOICED, invoice_number="INV-1001", actual_delivery_date=date(2026, 10, 3))

    response = client.post(
        f"/loads/{load.id}/edit",
        data={**LOAD_FORM, "rate_amount": "2650", "broker_email": "", "actual_delivery_date": "2026-10-04"},
    )

    assert response.headers["HX-Redirect"] == f"/loads/{load.id}"
    edited = client.get(f"/api/loads/{load.id}").json()
    assert edited["rate_amount"] == 2650
    assert edited["broker_email"] is None
    assert edited["actual_delivery_date"] == "2026-10-04"
    assert edited["status"] == "Invoiced"
    assert edited["invoice_number"] == "INV-1001"


def test_edit_cannot_add_a_delivery_day_to_an_undelivered_load(client, db_path):
    load = seed(db_path)

    client.post(f"/loads/{load.id}/edit", data={**LOAD_FORM, "actual_delivery_date": "2026-10-04"})

    assert client.get(f"/api/loads/{load.id}").json()["actual_delivery_date"] is None


def test_edit_missing_load_is_404(client):
    assert client.get("/loads/999/edit").status_code == 404
    assert client.post("/loads/999/edit", data=LOAD_FORM).status_code == 404


def test_parse_rate_con_uses_suggested_model_when_none_chosen(client, monkeypatch):
    configure_ai(client, model="")
    monkeypatch.setattr(ui, "list_models", lambda provider, key: [Model("gemini-9-flash", "Gemini 9 Flash")])
    models_used = []
    monkeypatch.setattr(ui, "extract_rate_con", lambda ai, model, pages: models_used.append(model) or LOAD_FORM)

    client.post("/parse-rate-con-html/", files={"files": ("ratecon.png", b"img", "image/png")})

    assert models_used == ["gemini-9-flash"]


def test_parse_rate_con_failure_shows_error(client, monkeypatch):
    configure_ai(client)

    def fail(ai, model, pages):
        raise AIError("Google Gemini rejected the API key. Check it in Settings.")

    monkeypatch.setattr(ui, "extract_rate_con", fail)

    response = client.post("/parse-rate-con-html/", files={"files": ("ratecon.png", b"img", "image/png")})

    assert response.status_code == 400
    assert "rejected the API key" in response.text


def test_parse_rate_con_error_is_html_escaped(client, monkeypatch):
    configure_ai(client)

    def explode(ai, model, pages):
        raise RuntimeError("<script>alert(1)</script>")

    monkeypatch.setattr(ui, "extract_rate_con", explode)

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

    assert "Add your Google Gemini API key" in client.get("/loads/new").text
    configure_ai(client)
    assert "Add your Google Gemini API key" not in client.get("/loads/new").text


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
    configure_ai(client, api_key="AIzaSecretKeyAbcd")

    body = client.get("/settings").text

    assert "•••• 1234" in body and "9876541234" not in body
    assert "•••• Abcd" in body and "AIzaSecretKeyAbcd" not in body


def test_blank_api_key_keeps_existing_key(client, db_path):
    configure_ai(client, api_key="AIzaSecretKeyAbcd")
    configure_ai(client, api_key="  ")

    db = DatabaseManager(db_path=str(db_path))
    assert asyncio.run(db.get_setting("ai_api_key_gemini")) == "AIzaSecretKeyAbcd"


def test_settings_toggles(client, db_path):
    db = DatabaseManager(db_path=str(db_path))

    assert client.post("/settings/toggle/show_bank_on_invoice", data={"enabled": "true"}).status_code == 204
    assert asyncio.run(db.get_all_settings())["show_bank_on_invoice"] is True
    client.post("/settings/toggle/show_bank_on_invoice")
    assert asyncio.run(db.get_all_settings())["show_bank_on_invoice"] is False
    assert client.post("/settings/toggle/gemini_api_key", data={"enabled": "true"}).status_code == 404


def test_unknown_settings_section_is_rejected(client):
    assert client.get("/settings/section/password").status_code == 422


# -----------------------------------------
# AI provider settings
# -----------------------------------------
def test_each_provider_keeps_its_own_key_and_model(client, db_path):
    configure_ai(client, "gemini", "gemini-key-1111", "gemini-9-flash")
    configure_ai(client, "openrouter", "or-key-2222", "google/gemini-9-flash")

    body = client.get("/settings").text
    assert "OpenRouter" in body and "google/gemini-9-flash" in body and "•••• 2222" in body

    db = DatabaseManager(db_path=str(db_path))
    settings = asyncio.run(db.get_all_settings())
    assert settings["ai_provider"] == "openrouter"
    assert settings["ai_api_key_gemini"] == "gemini-key-1111"
    assert settings["ai_model_gemini"] == "gemini-9-flash"


def test_switching_provider_in_edit_mode(client):
    configure_ai(client, "gemini", "gemini-key-1111")

    body = client.get("/settings/section/ai?edit=true&provider=openai").text

    assert "OpenAI API key" in body
    assert "Paste your OpenAI API key" in body
    assert "platform.openai.com/api-keys" in body


def test_env_key_is_used_when_nothing_saved(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-9999")

    body = client.get("/settings/section/ai?edit=true&provider=openai").text

    assert "Using OPENAI_API_KEY" in body
    assert "sk-env-9999" not in body


def test_unknown_provider_is_rejected(client):
    assert client.post("/settings/section/ai", data={"provider": "skynet"}).status_code == 422
    assert client.post("/settings/ai/models", data={"provider": "skynet"}).status_code == 422


def test_model_picker_suggests_a_default(client, monkeypatch):
    seen_keys = []
    models = [Model("gemini-8-flash", "8"), Model("gemini-9-flash", "9"), Model("gemini-9-pro", "pro")]
    monkeypatch.setattr(settings_routes, "list_models", lambda provider, key: seen_keys.append(key) or models)

    body = client.post("/settings/ai/models", data={"provider": "gemini", "api_key": "typed-key"}).text

    assert seen_keys == ["typed-key"]
    assert 'label="Recommended"' in body
    assert '<option value="gemini-9-flash" selected>' in body
    assert "3 available" in body


def test_model_picker_keeps_saved_model_selected(client, monkeypatch):
    configure_ai(client, model="gemini-8-flash")
    models = [Model("gemini-8-flash", "8"), Model("gemini-9-flash", "9")]
    monkeypatch.setattr(settings_routes, "list_models", lambda provider, key: models)

    body = client.post("/settings/ai/models", data={"provider": "gemini"}).text

    assert '<option value="gemini-8-flash" selected>' in body


def test_model_picker_error_falls_back_to_typing_a_model(client, monkeypatch):
    configure_ai(client, model="my-model")

    def fail(provider, key):
        raise AIError("Google Gemini rejected the API key. Check it in Settings.")

    monkeypatch.setattr(settings_routes, "list_models", fail)

    body = client.post("/settings/ai/models", data={"provider": "gemini"}).text

    assert "rejected the API key" in body
    assert 'name="model"' in body and 'value="my-model"' in body


def test_extra_instructions_are_saved(client):
    client.post(
        "/settings/section/ai",
        data={"provider": "gemini", "extra_instructions": "Pickup numbers are labeled Shipper Ref."},
    )

    assert "Custom instructions on" in client.get("/settings").text
    assert "Pickup numbers are labeled Shipper Ref." in client.get("/settings/section/ai?edit=true").text


def test_model_picker_only_replaces_itself(client):
    """The picker sits inside the card's form; without its own hx-target it inherits the form's
    and the model list replaces the whole card (provider, key and Advanced disappear)."""
    configure_ai(client)

    body = client.get("/settings/section/ai?edit=true").text

    picker = re.search(r'<div id="ai-models"[^>]*>', body).group(0)
    assert 'hx-target="this"' in picker
    assert 'hx-swap="innerHTML"' in picker
