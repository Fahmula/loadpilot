import base64
import hashlib
import hmac
import logging
import os
import secrets
import time
from collections import defaultdict, deque
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

from loadpilot.config import DATA_DIR
from loadpilot.models import User

logger = logging.getLogger("loadpilot.auth")

MIN_PASSWORD_LENGTH = 8
SESSION_MAX_AGE = 30 * 24 * 60 * 60  # stay signed in on the phone for a month

# scrypt cost parameters (~16 MB of memory per hash)
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 1

# Pages and assets reachable without logging in. Rate cons, BOLs and invoices live under
# /static too, but they hold customer and banking details, so they stay private.
PUBLIC_PATHS = frozenset({"/login", "/setup", "/health", "/manifest.json", "/sw.js"})
PRIVATE_STATIC_PREFIXES = ("/static/bols/", "/static/ratecons/", "/static/invoices/")


# -----------------------------------------
# PASSWORDS
# -----------------------------------------
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P)
    encoded_salt = base64.b64encode(salt).decode()
    encoded_digest = base64.b64encode(digest).decode()
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${encoded_salt}${encoded_digest}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, n, r, p, encoded_salt, encoded_digest = stored_hash.split("$")
    except ValueError:
        return False
    if algorithm != "scrypt":
        return False
    digest = hashlib.scrypt(password.encode(), salt=base64.b64decode(encoded_salt), n=int(n), r=int(r), p=int(p))
    return hmac.compare_digest(digest, base64.b64decode(encoded_digest))


# Compared against when a username doesn't exist, so a wrong username takes as long as a wrong password
DUMMY_PASSWORD_HASH = hash_password(secrets.token_urlsafe(16))


def session_fingerprint(password_hash: str) -> str:
    """Ties a session to the current password, so changing it signs out every other device."""
    return hashlib.sha256(password_hash.encode()).hexdigest()[:16]


def load_secret_key() -> str:
    """Returns the key that signs session cookies, generating and saving one on first start."""
    if env_key := os.getenv("LOADPILOT_SECRET_KEY"):
        return env_key

    key_file = DATA_DIR / "secret_key"
    if key_file.exists():
        return key_file.read_text().strip()

    key = secrets.token_urlsafe(48)
    key_file.write_text(key)
    key_file.chmod(0o600)
    logger.info("Generated a new session secret key.")
    return key


# -----------------------------------------
# SESSIONS
# -----------------------------------------
def log_in(request: Request, user: User):
    request.session.clear()
    request.session["user_id"] = user.id
    request.session["fingerprint"] = session_fingerprint(user.password_hash)


async def get_current_user(request: Request) -> User | None:
    user_id = request.session.get("user_id")
    if user_id is None:
        return None

    user = await request.app.state.db.get_user(user_id)
    fingerprint = request.session.get("fingerprint", "")
    if user and hmac.compare_digest(fingerprint, session_fingerprint(user.password_hash)):
        return user

    request.session.clear()
    return None


class HttpsSessionMiddleware:
    """Starlette's session cookie, marked Secure whenever the request came over HTTPS.

    A Secure cookie never travels over plain HTTP, e.g. when a link or a typed address starts with http://
    on public Wi-Fi. Deciding per request keeps plain-HTTP access on the home network working, where a
    Secure cookie would never come back. Behind a proxy, HTTPS shows through X-Forwarded-Proto once
    FORWARDED_ALLOW_IPS trusts the proxy (see warn_if_proxy_untrusted).
    """

    def __init__(self, app: ASGIApp, **options):
        self.plain = SessionMiddleware(app, **options)
        self.secure = SessionMiddleware(app, **options, https_only=True)

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        session = self.secure if scope.get("scheme") in ("https", "wss") else self.plain
        await session(scope, receive, send)


_untrusted_proxy_logged = False


def warn_if_proxy_untrusted(request: Request):
    """Logs once when a reverse proxy's X-Forwarded-For header is being ignored.

    uvicorn only applies the header from addresses in FORWARDED_ALLOW_IPS (localhost unless set). Behind any
    other proxy every visitor shares the proxy's address, so one person's failed logins lock out everyone,
    and HTTPS visits look like plain HTTP.
    """
    global _untrusted_proxy_logged
    forwarded_for = request.headers.get("x-forwarded-for")
    if _untrusted_proxy_logged or not forwarded_for or not request.client:
        return
    proxy = request.client.host
    # Once the proxy is trusted, uvicorn has already replaced the client address with one from the header
    if proxy in (address.strip() for address in forwarded_for.split(",")):
        return
    _untrusted_proxy_logged = True
    logger.warning(
        f"Requests are coming through a proxy at {proxy} that LoadPilot doesn't trust, so every visitor looks "
        f"like {proxy}: failed logins from anyone count toward the same limit, and HTTPS visits look like HTTP. "
        f"If {proxy} is your reverse proxy, set FORWARDED_ALLOW_IPS on the container to its address or network "
        "(or * if nothing but the proxy can reach LoadPilot)."
    )


# -----------------------------------------
# API TOKEN (for other apps, e.g. a bank feed recording payments)
# -----------------------------------------
def new_api_token() -> str:
    # The lp_ prefix makes a leaked token recognisable, e.g. to secret scanners
    return "lp_" + secrets.token_urlsafe(32)


def hash_api_token(token: str) -> str:
    """Only this is stored, so a copy of the database can't call the API. A plain hash suffices for 256
    random bits; slow hashing is for guessable passwords."""
    return hashlib.sha256(token.encode()).hexdigest()


async def has_valid_api_token(request: Request) -> bool:
    """Whether the request carries the API token from Settings, as `Authorization: Bearer <token>`."""
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return False
    stored = await request.app.state.db.get_setting("api_token_hash")
    return bool(stored) and hmac.compare_digest(stored, hash_api_token(token.strip()))


def safe_next_path(next_path: str | None) -> str:
    """Only allow redirects back into this app, never to another site."""
    if next_path and next_path.startswith("/") and not next_path.startswith("//") and "\\" not in next_path:
        return next_path
    return "/"


class LoginThrottle:
    """Blocks a client after too many failed logins in a short window."""

    def __init__(self, max_failures: int = 5, window_seconds: int = 15 * 60):
        self.max_failures = max_failures
        self.window_seconds = window_seconds
        self._failures: dict[str, deque[float]] = defaultdict(deque)

    def _recent(self, client: str) -> deque[float]:
        failures = self._failures[client]
        cutoff = time.monotonic() - self.window_seconds
        while failures and failures[0] < cutoff:
            failures.popleft()
        return failures

    def is_blocked(self, client: str) -> bool:
        return len(self._recent(client)) >= self.max_failures

    def record_failure(self, client: str):
        self._recent(client).append(time.monotonic())

    def reset(self, client: str):
        self._failures.pop(client, None)


def is_public_path(path: str) -> bool:
    if path in PUBLIC_PATHS:
        return True
    return path.startswith("/static/") and not path.startswith(PRIVATE_STATIC_PREFIXES)


class AuthMiddleware(BaseHTTPMiddleware):
    """Requires a logged-in session for everything except the public paths above."""

    async def dispatch(self, request: Request, call_next) -> Response:
        warn_if_proxy_untrusted(request)
        path = request.url.path
        if is_public_path(path):
            return await call_next(request)

        user = await get_current_user(request)
        if user:
            request.state.user = user
            return await call_next(request)
        # Other apps sign in with the API token instead; it opens the API only, never the pages or Settings
        if path.startswith("/api/") and await has_valid_api_token(request):
            return await call_next(request)

        target = "/login" if await request.app.state.db.has_users() else "/setup"
        if path.startswith("/api/"):
            return JSONResponse({"detail": "Not authenticated"}, status_code=401)
        if request.headers.get("HX-Request") == "true":
            # HTMX swaps responses into the page; tell it to navigate instead
            return Response(headers={"HX-Redirect": target})

        next_path = path + (f"?{request.url.query}" if request.url.query else "")
        return RedirectResponse(f"{target}?next={quote(next_path)}", status_code=303)
