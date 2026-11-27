import logging
import re

import pytest
from fastapi.testclient import TestClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

import app as app_module
from loadpilot import auth
from loadpilot.auth import hash_password, is_public_path, safe_next_path, verify_password
from loadpilot.cli import reset_password

from .conftest import TEST_PASSWORD, TEST_USERNAME


def login(client, username=TEST_USERNAME, password=TEST_PASSWORD, next_path="/", headers=None):
    return client.post(
        "/login",
        data={"username": username, "password": password, "next": next_path},
        headers=headers,
        follow_redirects=False,
    )


def change_password(client, current, new, confirm=None):
    return client.post(
        "/settings/password",
        data={"current_password": current, "new_password": new, "confirm_password": confirm or new},
    )


# -----------------------------------------
# Password hashing and helpers
# -----------------------------------------
def test_password_hash_round_trip():
    stored = hash_password("s3cret-pass")

    assert stored.startswith("scrypt$")
    assert verify_password("s3cret-pass", stored)
    assert not verify_password("wrong-pass", stored)
    assert hash_password("s3cret-pass") != stored  # salted


def test_verify_rejects_malformed_hash():
    assert not verify_password("anything", "not-a-hash")
    assert not verify_password("anything", "bcrypt$1$2$3$4$5")


@pytest.mark.parametrize(
    ("next_path", "expected"),
    [
        ("/loads?search=acme", "/loads?search=acme"),
        ("//evil.example", "/"),
        ("https://evil.example", "/"),
        ("/\\evil.example", "/"),
        ("", "/"),
        (None, "/"),
    ],
)
def test_safe_next_path(next_path, expected):
    assert safe_next_path(next_path) == expected


def test_public_paths():
    assert is_public_path("/login")
    assert is_public_path("/static/icon-192.png")
    assert not is_public_path("/static/invoices/invoice_INV-1001.pdf")
    assert not is_public_path("/static/bols/bol_1.png")
    assert not is_public_path("/static/ratecons/ratecon_1_abc123.pdf")
    assert not is_public_path("/")


# -----------------------------------------
# First-run setup
# -----------------------------------------
def test_first_visit_goes_to_setup(anon_client):
    response = anon_client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/setup")
    assert anon_client.get("/login", follow_redirects=False).headers["location"] == "/setup"


def test_setup_validates_input(anon_client):
    short = anon_client.post("/setup", data={"username": "driver", "password": "short", "confirm_password": "short"})
    mismatch = anon_client.post(
        "/setup", data={"username": "driver", "password": "long-enough-1", "confirm_password": "long-enough-2"}
    )

    assert short.status_code == 400
    assert "at least 8 characters" in short.text
    assert mismatch.status_code == 400
    assert "don&#39;t match" in mismatch.text


def test_setup_creates_account_and_signs_in(client):
    assert client.get("/", follow_redirects=False).status_code == 200


def test_setup_is_closed_once_an_account_exists(client, anon_client):
    client.cookies.clear()

    page = client.get("/setup", follow_redirects=False)
    second = client.post(
        "/setup",
        data={"username": "intruder", "password": "intruder-pass", "confirm_password": "intruder-pass"},
        follow_redirects=False,
    )

    assert page.headers["location"] == "/login"
    assert second.headers["location"] == "/login"
    assert login(client, "intruder", "intruder-pass").status_code == 400


# -----------------------------------------
# Login / logout
# -----------------------------------------
def test_protected_routes_require_login(client):
    client.cookies.clear()

    page = client.get("/loads?search=acme", follow_redirects=False)
    htmx = client.get("/loads", headers={"HX-Request": "true"}, follow_redirects=False)

    assert page.status_code == 303
    assert page.headers["location"] == "/login?next=/loads%3Fsearch%3Dacme"
    assert htmx.headers["HX-Redirect"] == "/login"
    assert client.get("/api/loads/").status_code == 401
    assert client.get("/static/invoices/invoice_INV-1001.pdf", follow_redirects=False).status_code == 303
    assert client.post("/delete-load-web/1", follow_redirects=False).status_code == 303


# -----------------------------------------
# API token, for other apps
# -----------------------------------------
def create_api_token(client) -> str:
    html = client.post("/settings/api-token").text
    assert "It won't be shown again" in html
    return re.search(r'value="(lp_[^"]+)"', html).group(1)


def other_app(token: str | None) -> TestClient:
    """A bank feed or bookkeeping script: no login, only the token (when it has one)."""
    other = TestClient(app_module.app)
    if token:
        other.headers["Authorization"] = f"Bearer {token}"
    return other


INVOICED_LOAD = {
    "broker_name": "Acme Logistics",
    "load_reference": "PO-1",
    "rate_amount": 2500,
    "pickup_location": "Dallas, TX",
    "pickup_date": "2026-11-20",
    "delivery_location": "Atlanta, GA",
    "delivery_date": "2026-11-22",
    "status": "Invoiced",
    "invoice_number": "INV-1001",
    "invoiced_at": "2026-11-23T09:00:00",
}


def test_api_token_lets_another_app_record_payments(client):
    bank_feed = other_app(create_api_token(client))

    load = bank_feed.post("/api/loads/", json=INVOICED_LOAD)
    payment = bank_feed.post("/api/payments/", json={"invoice_number": "INV-1001", "amount": 2450})

    assert load.status_code == 200
    assert payment.status_code == 200
    assert payment.json()["status"] == "Paid"
    assert bank_feed.get("/api/loads/").json()[0]["amount_paid"] == 2450
    assert other_app(None).get("/api/loads/").status_code == 401
    assert other_app("lp_wrong").get("/api/loads/").status_code == 401
    assert other_app("").get("/api/loads/", headers={"Authorization": "Basic abc"}).status_code == 401


def test_api_token_never_opens_the_pages_or_settings(client):
    bank_feed = other_app(create_api_token(client))

    assert bank_feed.get("/", follow_redirects=False).headers["location"] == "/login?next=/"
    assert bank_feed.get("/static/invoices/invoice_INV-1001.pdf", follow_redirects=False).status_code == 303
    # It can't make itself a new token, or revoke the owner's
    assert bank_feed.post("/settings/api-token", follow_redirects=False).status_code == 303
    assert bank_feed.post("/settings/api-token/revoke", follow_redirects=False).status_code == 303
    assert bank_feed.get("/api/loads/").status_code == 200


async def test_only_a_hash_of_the_token_is_kept(client, db):
    token = create_api_token(client)

    settings = await db.get_all_settings()
    page = client.get("/settings").text

    assert token not in str(settings)
    assert settings["api_token_hash"] == auth.hash_api_token(token)
    assert token not in page
    assert f'Token ending in <span class="font-mono">…{token[-4:]}</span>' in page


def test_replacing_or_revoking_the_token_cuts_off_the_old_one(client):
    first = create_api_token(client)
    second = create_api_token(client)  # Replace

    assert other_app(first).get("/api/loads/").status_code == 401
    assert other_app(second).get("/api/loads/").status_code == 200

    revoked = client.post("/settings/api-token/revoke").text

    assert other_app(second).get("/api/loads/").status_code == 401
    assert "Create API Token" in revoked
    assert "Create API Token" in client.get("/settings").text


def test_public_assets_stay_open(client):
    client.cookies.clear()

    for path in ("/health", "/manifest.json", "/sw.js", "/static/icon-192.png", "/login"):
        assert client.get(path, follow_redirects=False).status_code == 200, path


def test_login_with_correct_password_redirects_to_next(client):
    client.cookies.clear()

    response = login(client, next_path="/analytics")

    assert response.status_code == 303
    assert response.headers["location"] == "/analytics"
    assert client.get("/analytics", follow_redirects=False).status_code == 200


def test_login_username_is_case_insensitive(client):
    client.cookies.clear()

    assert login(client, username="DRIVER").status_code == 303


def test_login_rejects_bad_credentials(client):
    client.cookies.clear()

    wrong_password = login(client, password="not-the-password")
    unknown_user = login(client, username="nobody")

    assert wrong_password.status_code == 400
    assert "Incorrect username or password" in wrong_password.text
    assert unknown_user.status_code == 400
    assert client.get("/", follow_redirects=False).status_code == 303


def test_login_open_redirect_is_blocked(client):
    client.cookies.clear()

    assert login(client, next_path="//evil.example").headers["location"] == "/"


def test_login_is_throttled_after_repeated_failures(client):
    client.cookies.clear()

    for _ in range(5):
        login(client, password="wrong-password")
    blocked = login(client)

    assert blocked.status_code == 429
    assert "Too many failed attempts" in blocked.text


# -----------------------------------------
# Behind a reverse proxy (Caddy)
# -----------------------------------------
CADDY = ("172.18.0.5", 50000)  # Caddy's address on the Docker network


def behind_caddy(forwarded_allow_ips: str) -> TestClient:
    """LoadPilot as uvicorn serves it behind Caddy, which connects from its Docker address and passes each
    visitor's address and scheme in X-Forwarded-* headers. uvicorn applies them from FORWARDED_ALLOW_IPS only."""
    return TestClient(ProxyHeadersMiddleware(app_module.app, trusted_hosts=forwarded_allow_ips), client=CADDY)


def visitor(address: str) -> dict:
    return {"X-Forwarded-For": address, "X-Forwarded-Proto": "https"}


@pytest.fixture
def app_log(caplog):
    """Records LoadPilot's log, whose loggers don't propagate to pytest's root handler."""
    logger = logging.getLogger("loadpilot")
    logger.addHandler(caplog.handler)
    yield caplog
    logger.removeHandler(caplog.handler)


def test_session_cookie_is_secure_over_https(db_path):
    with TestClient(app_module.app, base_url="https://loadpilot.example") as https_client:
        response = https_client.post(
            "/setup",
            data={"username": TEST_USERNAME, "password": TEST_PASSWORD, "confirm_password": TEST_PASSWORD},
            follow_redirects=False,
        )

        assert "secure" in response.headers["set-cookie"].lower()
        assert https_client.get("/", follow_redirects=False).status_code == 200  # and it comes back over HTTPS


def test_session_cookie_still_works_over_plain_http(client):
    client.cookies.clear()

    response = login(client)  # e.g. http://192.168.1.20:8000 on the home network

    assert "secure" not in response.headers["set-cookie"].lower()
    assert client.get("/", follow_redirects=False).status_code == 200


def test_behind_trusted_caddy_failed_logins_only_block_that_visitor(client, app_log):
    with behind_caddy(forwarded_allow_ips="172.16.0.0/12") as caddy:
        for _ in range(5):
            login(caddy, password="wrong-password", headers=visitor("203.0.113.7"))

        assert login(caddy, headers=visitor("203.0.113.7")).status_code == 429
        owner = login(caddy, headers=visitor("198.51.100.20"))

    assert owner.status_code == 303  # someone else guessing doesn't lock the owner out
    assert "secure" in owner.headers["set-cookie"].lower()  # Caddy's HTTPS reaches the cookie
    assert "FORWARDED_ALLOW_IPS" not in app_log.text


def test_behind_untrusted_caddy_everyone_shares_one_limit_and_it_is_logged(client, app_log, monkeypatch):
    monkeypatch.setattr(auth, "_untrusted_proxy_logged", False)

    with behind_caddy(forwarded_allow_ips="127.0.0.1,::1") as caddy:  # uvicorn's default
        for _ in range(5):
            login(caddy, password="wrong-password", headers=visitor("203.0.113.7"))
        owner = login(caddy, headers=visitor("198.51.100.20"))

    assert owner.status_code == 429
    warnings = [record.getMessage() for record in app_log.records if "FORWARDED_ALLOW_IPS" in record.getMessage()]
    assert len(warnings) == 1  # once, not on every request
    assert "proxy at 172.18.0.5" in warnings[0]


def test_logout_ends_session(client):
    response = client.post("/logout", follow_redirects=False)

    assert response.headers["location"] == "/login"
    assert client.get("/", follow_redirects=False).status_code == 303


# -----------------------------------------
# Changing and resetting the password
# -----------------------------------------
def test_change_password_requires_current_password(client):
    response = change_password(client, "wrong-current", "brand-new-pass")

    assert "Current password is incorrect" in response.text


def test_change_password_validates_new_password(client):
    assert "at least 8 characters" in change_password(client, TEST_PASSWORD, "short").text
    assert "match" in change_password(client, TEST_PASSWORD, "brand-new-pass", "different-pass").text


def test_change_password_signs_out_other_devices(client, db_path):
    with TestClient(app_module.app) as other_device:
        login(other_device)
        assert other_device.get("/", follow_redirects=False).status_code == 200

        response = change_password(client, TEST_PASSWORD, "brand-new-pass")

        assert "Password updated" in response.text
        assert client.get("/", follow_redirects=False).status_code == 200
        assert other_device.get("/", follow_redirects=False).status_code == 303
        assert login(other_device).status_code == 400
        assert login(other_device, password="brand-new-pass").status_code == 303


async def test_cli_reset_password(db):
    with pytest.raises(SystemExit, match="No account"):
        await reset_password(db, "new-password-1")

    await db.create_user("driver", hash_password(TEST_PASSWORD))

    assert await reset_password(db, "new-password-1") == "driver"
    user = await db.get_user_by_username("driver")
    assert verify_password("new-password-1", user.password_hash)
    with pytest.raises(SystemExit, match="at least 8"):
        await reset_password(db, "short")
    with pytest.raises(SystemExit, match="No account named"):
        await reset_password(db, "new-password-2", username="someone-else")
