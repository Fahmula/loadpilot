"""Sending email through the driver's own mail account (SMTP), e.g. an invoice to the broker's billing desk."""

import logging
import re
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr

logger = logging.getLogger("loadpilot.mailer")

TIMEOUT = 30  # seconds
ADDRESS = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")


class MailError(Exception):
    """A failure whose message is safe and useful to show the driver."""


@dataclass(frozen=True)
class MailConfig:
    host: str
    port: int
    username: str
    password: str
    sender: str  # the From address
    sender_name: str = ""


def mail_config(settings: dict) -> MailConfig | None:
    """The SMTP account from Settings > Invoice Email, or None until a server and From address are set."""
    host = str(settings.get("smtp_host") or "").strip()
    username = str(settings.get("smtp_username") or "").strip()
    sender = str(settings.get("smtp_from") or "").strip() or username
    if not host or not ADDRESS.match(sender):
        return None
    try:
        port = int(settings.get("smtp_port") or 587)
    except ValueError:
        port = 587
    return MailConfig(
        host=host,
        port=port,
        username=username,
        password=str(settings.get("smtp_password") or ""),
        sender=sender,
        sender_name=str(settings.get("company_name") or ""),
    )


def is_address(value: str) -> bool:
    return bool(ADDRESS.match(value.strip()))


def build_message(
    config: MailConfig,
    to: str,
    subject: str,
    body: str,
    attachments: list[tuple[str, bytes]] | None = None,
    reply_to: str | None = None,
) -> EmailMessage:
    message = EmailMessage()
    message["From"] = formataddr((config.sender_name, config.sender)) if config.sender_name else config.sender
    message["To"] = to
    message["Subject"] = subject
    if reply_to and reply_to != config.sender:
        message["Reply-To"] = reply_to
    message.set_content(body)
    for filename, content in attachments or []:
        message.add_attachment(content, maintype="application", subtype="pdf", filename=filename)
    return message


def send(config: MailConfig, message: EmailMessage):
    """Sends through the configured server, always encrypted when it signs in. Raises MailError."""
    where = f"{config.host}:{config.port}"
    try:
        if config.port == 465:  # TLS from the first byte
            smtp = smtplib.SMTP_SSL(config.host, config.port, timeout=TIMEOUT, context=ssl.create_default_context())
        else:
            smtp = smtplib.SMTP(config.host, config.port, timeout=TIMEOUT)
        with smtp:
            if config.port != 465:
                smtp.ehlo()
                if smtp.has_extn("starttls"):
                    smtp.starttls(context=ssl.create_default_context())
                    smtp.ehlo()
                elif config.username:
                    raise MailError(f"{where} doesn't offer encryption, so LoadPilot won't send your password to it.")
            if config.username:
                smtp.login(config.username, config.password)
            smtp.send_message(message)
    except MailError:
        raise
    except smtplib.SMTPAuthenticationError as e:
        raise MailError(
            "The email server rejected the username or password. Gmail needs an app password here, not your "
            "normal one, and many other providers do too."
        ) from e
    except smtplib.SMTPRecipientsRefused as e:
        raise MailError(f"The email server refused to send to {', '.join(e.recipients)}.") from e
    except smtplib.SMTPException as e:
        logger.warning(f"Sending email through {where} failed: {e}")
        raise MailError(f"The email server at {where} returned an error: {e}"[:300]) from e
    except ssl.SSLError as e:
        raise MailError(f"Couldn't set up encryption with {where}. Check the port: 587 or 465.") from e
    except OSError as e:  # refused, timed out, unknown host
        raise MailError(f"Couldn't reach {where}. Check the server name and port.") from e
    logger.info(f"Emailed '{message['Subject']}' to {message['To']}.")
