import asyncio
import logging

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from loadpilot.auth import (
    DUMMY_PASSWORD_HASH,
    MIN_PASSWORD_LENGTH,
    get_current_user,
    hash_password,
    log_in,
    safe_next_path,
    verify_password,
)
from loadpilot.templating import alert_response, templates

logger = logging.getLogger("loadpilot.routes.auth")
router = APIRouter(tags=["Auth"])


def validate_new_password(password: str, confirm_password: str) -> str | None:
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
    if password != confirm_password:
        return "Passwords don't match."
    return None


# -----------------------------------------
# FIRST-RUN ACCOUNT SETUP
# -----------------------------------------
@router.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request):
    if await request.app.state.db.has_users():
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(request=request, name="setup.html", context={"error": None, "username": ""})


@router.post("/setup")
async def create_account(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
):
    db = request.app.state.db
    # The account can only be created once; after that, /setup is closed
    if await db.has_users():
        return RedirectResponse("/login", status_code=303)

    username = username.strip()
    error = "Username is required." if not username else validate_new_password(password, confirm_password)
    if error:
        return templates.TemplateResponse(
            request=request, name="setup.html", context={"error": error, "username": username}, status_code=400
        )

    password_hash = await asyncio.to_thread(hash_password, password)
    user = await db.create_user(username, password_hash)
    log_in(request, user)
    logger.info(f"Created account '{username}'.")
    return RedirectResponse("/", status_code=303)


# -----------------------------------------
# LOGIN / LOGOUT
# -----------------------------------------
@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: str = "/"):
    if not await request.app.state.db.has_users():
        return RedirectResponse("/setup", status_code=303)
    if await get_current_user(request):
        return RedirectResponse(safe_next_path(next), status_code=303)
    return templates.TemplateResponse(
        request=request, name="login.html", context={"error": None, "username": "", "next": safe_next_path(next)}
    )


@router.post("/login")
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
):
    throttle = request.app.state.login_throttle
    client = request.client.host if request.client else "unknown"
    context = {"username": username, "next": safe_next_path(next)}

    if throttle.is_blocked(client):
        logger.warning(f"Blocked login attempt from {client} after repeated failures.")
        context["error"] = "Too many failed attempts. Try again in 15 minutes."
        return templates.TemplateResponse(request=request, name="login.html", context=context, status_code=429)

    user = await request.app.state.db.get_user_by_username(username.strip())
    stored_hash = user.password_hash if user else DUMMY_PASSWORD_HASH
    password_ok = await asyncio.to_thread(verify_password, password, stored_hash)

    if not (user and password_ok):
        throttle.record_failure(client)
        logger.warning(f"Failed login for '{username}' from {client}.")
        context["error"] = "Incorrect username or password."
        return templates.TemplateResponse(request=request, name="login.html", context=context, status_code=400)

    throttle.reset(client)
    log_in(request, user)
    return RedirectResponse(safe_next_path(next), status_code=303)


@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


# -----------------------------------------
# CHANGE PASSWORD (from Settings)
# -----------------------------------------
@router.post("/settings/password")
async def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
):
    user = request.state.user
    if not await asyncio.to_thread(verify_password, current_password, user.password_hash):
        return alert_response("Current password is incorrect.")

    error = validate_new_password(new_password, confirm_password)
    if error:
        return alert_response(error)

    password_hash = await asyncio.to_thread(hash_password, new_password)
    await request.app.state.db.set_password_hash(user.id, password_hash)
    # Re-issue this session for the new password; every other device is signed out
    log_in(request, user.model_copy(update={"password_hash": password_hash}))
    logger.info(f"Password changed for '{user.username}'.")
    return alert_response("Password updated. Other devices were signed out.", "success")
