import asyncio
import smtplib
from datetime import date, datetime

import pytest

from loadpilot import mailer
from loadpilot.config import INVOICE_DIR
from loadpilot.database import DatabaseManager
from loadpilot.mailer import MailConfig, MailError, build_message, mail_config, send
from loadpilot.models import LoadStatus

from .conftest import make_load


class FakeSMTP:
    """Stands in for the mail server: records what LoadPilot does, and fails when told to."""

    servers: list[FakeSMTP] = []
    offers_starttls = True
    fail_with: Exception | None = None

    def __init__(self, host, port, timeout=None, context=None):
        if isinstance(FakeSMTP.fail_with, OSError):
            raise FakeSMTP.fail_with
        self.address, self.steps, self.sent = (host, port), [], []
        FakeSMTP.servers.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def ehlo(self):
        pass

    def has_extn(self, name):
        return name == "starttls" and FakeSMTP.offers_starttls

    def starttls(self, context=None):
        self.steps.append("starttls")

    def login(self, username, password):
        if isinstance(FakeSMTP.fail_with, smtplib.SMTPAuthenticationError):
            raise FakeSMTP.fail_with
        self.steps.append(f"login {username}")

    def send_message(self, message):
        self.sent.append(message)


class FakeSMTPSSL(FakeSMTP):
    pass


@pytest.fixture
def smtp(monkeypatch):
    FakeSMTP.servers, FakeSMTP.offers_starttls, FakeSMTP.fail_with = [], True, None
    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(mailer.smtplib, "SMTP_SSL", FakeSMTPSSL)
    return FakeSMTP


ACCOUNT = MailConfig(
    host="smtp.example.com",
    port=587,
    username="me@example.com",
    password="app-pass",
    sender="me@example.com",
    sender_name="Fahmula Trucking",
)


# -----------------------------------------
# The mail module
# -----------------------------------------
def test_mail_config_needs_a_server_and_from_address():
    assert mail_config({}) is None
    assert mail_config({"smtp_host": "smtp.gmail.com"}) is None  # nothing to send from
    config = mail_config({"smtp_host": "smtp.gmail.com", "smtp_username": "me@gmail.com", "smtp_port": "abc"})
    assert (config.sender, config.port) == ("me@gmail.com", 587)  # From defaults to the username
    assert mail_config({"smtp_host": "h", "smtp_username": "u", "smtp_from": "billing@x.com"}).sender == "billing@x.com"


def test_send_encrypts_before_signing_in(smtp):
    send(ACCOUNT, build_message(ACCOUNT, "ap@broker.com", "Hi", "Body"))

    server = smtp.servers[0]
    assert server.address == ("smtp.example.com", 587)
    assert server.steps == ["starttls", "login me@example.com"]
    assert server.sent[0]["To"] == "ap@broker.com"
    assert server.sent[0]["From"] == "Fahmula Trucking <me@example.com>"


def test_port_465_is_tls_from_the_start(smtp):
    config = MailConfig(**{**ACCOUNT.__dict__, "port": 465})

    send(config, build_message(config, "ap@broker.com", "Hi", "Body"))

    assert isinstance(smtp.servers[0], FakeSMTPSSL)
    assert smtp.servers[0].steps == ["login me@example.com"]


def test_password_never_goes_to_a_server_without_encryption(smtp):
    smtp.offers_starttls = False

    with pytest.raises(MailError, match="doesn't offer encryption"):
        send(ACCOUNT, build_message(ACCOUNT, "ap@broker.com", "Hi", "Body"))
    assert smtp.servers[0].sent == []


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        (smtplib.SMTPAuthenticationError(535, b"bad credentials"), "app password"),
        (ConnectionRefusedError(), "Couldn't reach smtp.example.com:587"),
        (TimeoutError(), "Couldn't reach"),
    ],
)
def test_send_failures_are_explained(smtp, failure, message):
    smtp.fail_with = failure

    with pytest.raises(MailError, match=message):
        send(ACCOUNT, build_message(ACCOUNT, "ap@broker.com", "Hi", "Body"))


# -----------------------------------------
# Settings > Invoice Email
# -----------------------------------------
EMAIL_SETTINGS = {
    "smtp_host": "smtp.example.com",
    "smtp_port": "587",
    "smtp_username": "me@example.com",
    "smtp_password": "app-pass",
    "smtp_from": "",
}


def test_email_settings_keep_the_saved_password(client, db):
    client.post("/settings/section/email", data=EMAIL_SETTINGS)
    card = client.post("/settings/section/email", data={**EMAIL_SETTINGS, "smtp_password": ""}).text

    assert asyncio.run(db.get_setting("smtp_password")) == "app-pass"
    assert "app-pass" not in card and "app-pass" not in client.get("/settings").text
    assert "me@example.com" in card and "smtp.example.com:587" in card


def test_test_email_goes_to_the_from_address(client, smtp):
    assert "Add an email server" in client.post("/settings/email/test").text

    client.post("/settings/section/email", data=EMAIL_SETTINGS)
    result = client.post("/settings/email/test").text

    assert "Test email sent to me@example.com" in result
    assert smtp.servers[-1].sent[0]["To"] == "me@example.com"


# -----------------------------------------
# Emailing an invoice from the load page
# -----------------------------------------
def invoiced_load(db_path, **overrides):
    pdf = INVOICE_DIR / "invoice_INV-2001.pdf"
    pdf.write_bytes(b"%PDF-1.4 invoice with its BOL")
    fields = {
        "status": LoadStatus.INVOICED,
        "invoice_number": "INV-2001",
        "invoiced_at": datetime(2026, 11, 23, 9, 0),
        "invoice_pdf_path": "static/invoices/invoice_INV-2001.pdf",
        "bol_pdf_path": "static/bols/bol_1_abc123.pdf",
        "actual_delivery_date": date(2026, 11, 22),
        "charges": [{"description": "Detention", "amount": 150.0}],
        **overrides,
    }
    return asyncio.run(DatabaseManager(str(db_path)).add_load(make_load(**fields)))


def test_invoice_is_emailed_to_the_broker(client, db_path, smtp):
    client.post(
        "/settings/section/company",
        data={"company_name": "Fahmula Trucking", "mc_number": "123456", "company_email": "billing@fahmula.example"},
    )
    client.post("/settings/section/email", data=EMAIL_SETTINGS)
    load = invoiced_load(db_path)
    assert 'value="dispatch@acme.example"' in client.get(f"/loads/{load.id}").text  # prefilled

    result = client.post(f"/loads/{load.id}/email-invoice", data={"to": "ap@acme.example"}).text

    assert "Invoice INV-2001 emailed to ap@acme.example" in result
    message = smtp.servers[-1].sent[0]
    assert message["To"] == "ap@acme.example"
    assert message["Subject"] == "Invoice INV-2001 · Load PO-1234 · Fahmula Trucking"
    assert message["Reply-To"] == "billing@fahmula.example"
    body = message.get_body().get_content()
    assert "load PO-1234, Dallas, TX to Atlanta, GA, delivered Nov 22, 2026. The signed BOL is included." in body
    assert "Amount due: $2,650.00, due Dec 23, 2026 (net 30)." in body
    assert "MC# 123456" in body
    (attachment,) = message.iter_attachments()
    assert attachment.get_filename() == "Invoice-INV-2001.pdf"
    assert attachment.get_content() == b"%PDF-1.4 invoice with its BOL"

    page = client.get(f"/loads/{load.id}").text
    assert "Emailed to ap@acme.example on" in page
    assert "Resend" in page


def test_a_typed_address_is_remembered_for_a_broker_without_one(client, db_path, smtp):
    client.post("/settings/section/email", data=EMAIL_SETTINGS)
    load = invoiced_load(db_path, broker_email=None)

    client.post(f"/loads/{load.id}/email-invoice", data={"to": "ap@acme.example"})

    assert client.get(f"/api/loads/{load.id}").json()["broker_email"] == "ap@acme.example"


def test_failed_or_refused_sends_change_nothing(client, db_path, smtp):
    load = invoiced_load(db_path)
    page = client.get(f"/loads/{load.id}").text
    assert 'Set up <a href="/settings" class="link">Invoice Email</a>' in page  # no form until it's set up
    assert "Set up Invoice Email" in client.post(f"/loads/{load.id}/email-invoice", data={"to": "a@b.co"}).text

    client.post("/settings/section/email", data=EMAIL_SETTINGS)
    bad = client.post(f"/loads/{load.id}/email-invoice", data={"to": "ap@acme.example, boss@acme.example"}).text
    smtp.fail_with = smtplib.SMTPAuthenticationError(535, b"bad credentials")
    failed = client.post(f"/loads/{load.id}/email-invoice", data={"to": "ap@acme.example"}).text

    assert "Enter one email address" in bad
    assert "app password" in failed
    assert all(not server.sent for server in smtp.servers)
    assert client.get(f"/api/loads/{load.id}").json()["invoice_emailed_at"] is None
