"""Lab Shop: a small shop whose failures we control.

Routes match the e2e suite's config: / (login), /inventory.html, /cart.html,
/checkout-step-one.html, /checkout-step-two.html, /checkout-complete.html.
See faults.py for the switches that break it on purpose.

Run locally:
    DATABASE_URL=postgresql://lab:lab@localhost:5432/labshop TAX_RATE=0.08 \
        uvicorn shop.app:app --port 8000
"""

import logging
import os
import time
import uuid
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from shop import db, faults

log = logging.getLogger("labshop")
HERE = Path(__file__).parent
CENT = Decimal("0.01")

# Lab-only accounts. Not real credentials.
USERS = {
    "standard_user": {"password": "lab_password", "locked": False},
    "locked_out_user": {"password": "lab_password", "locked": True},
}

app = FastAPI(title="Lab Shop", docs_url=None, redoc_url=None)
app.add_middleware(SessionMiddleware, secret_key=os.environ.get("SHOP_SECRET", "lab-shop-not-a-secret"))
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")


@app.on_event("startup")
def startup() -> None:
    # Keep trying briefly, but never refuse to start: without a database the pages that need it
    # fail, while the shop itself stays up.
    for attempt in range(10):
        try:
            db.init()
            log.info("database ready")
            return
        except Exception as e:  # noqa: BLE001
            log.warning("database not ready (attempt %d): %s", attempt + 1, e)
            time.sleep(1)
    log.error("starting without a database")


def user_of(request: Request) -> str | None:
    return request.session.get("user")


def cart_of(request: Request) -> str:
    return request.session["cart"]


def to_login() -> RedirectResponse:
    return RedirectResponse("/", status_code=303)


def money(d: Decimal) -> str:
    return f"${d.quantize(CENT)}"


def totals(items: list[dict]) -> tuple[Decimal, Decimal, Decimal]:
    rate = Decimal(os.environ["TAX_RATE"])
    subtotal = sum((i["price"] for i in items), Decimal("0"))
    if faults.on("BUG_TAX_ROUNDING"):
        tax = (subtotal * rate).quantize(CENT, rounding=ROUND_DOWN)
        total = (subtotal * (1 + rate)).quantize(CENT, rounding=ROUND_HALF_UP)
    else:
        tax = (subtotal * rate).quantize(CENT, rounding=ROUND_HALF_UP)
        total = subtotal + tax
    return subtotal, tax, total


@app.get("/healthz")
def healthz():
    try:
        db.ping()
        return PlainTextResponse("ok")
    except Exception as e:  # noqa: BLE001
        return PlainTextResponse(f"database unavailable: {e}", status_code=503)


@app.get("/", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None})


@app.post("/", response_class=HTMLResponse)
def login(request: Request, username: str = Form(""), password: str = Form("")):
    error = None
    account = USERS.get(username)
    if not username:
        error = "Error: Username is required."
    elif not password:
        error = "Error: Password is required."
    elif not account or account["password"] != password:
        error = "Error: Invalid username or password."
    elif account["locked"]:
        error = "Error: This account is locked out."
    if error:
        return templates.TemplateResponse(request, "login.html", {"error": error, "username": username})
    request.session["user"] = username
    # A fresh cart per login, like a shop that keeps the cart in the browser session. Tests that
    # share an account (and parallel test workers) therefore never see each other's items.
    request.session["cart"] = uuid.uuid4().hex
    return RedirectResponse("/inventory.html", status_code=303)


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return to_login()


@app.get("/inventory.html", response_class=HTMLResponse)
def inventory(request: Request):
    user = user_of(request)
    if not user:
        return to_login()
    faults.maybe_slow_down("inventory")
    in_cart = {i["slug"] for i in db.cart(cart_of(request))}
    return templates.TemplateResponse(
        request,
        "inventory.html",
        {
            "products": db.products(),
            "in_cart": in_cart,
            "cart_count": len(in_cart),
            "money": money,
            "price_sort": "text" if faults.on("BUG_SORT_PRICE") else "number",
        },
    )


def cart_json(cart_id: str) -> dict:
    items = db.cart(cart_id)
    return {
        "count": len(items),
        "items": [{"slug": i["slug"], "name": i["name"], "price": money(i["price"])} for i in items],
    }


@app.post("/api/cart/{slug}")
def api_add(request: Request, slug: str):
    user = user_of(request)
    if not user:
        return JSONResponse({"error": "not logged in"}, status_code=401)
    faults.maybe_slow_down("api")
    if faults.api_should_fail():
        return JSONResponse({"error": "cart service error"}, status_code=500)
    db.add_to_cart(cart_of(request), slug)
    return cart_json(cart_of(request))


@app.delete("/api/cart/{slug}")
def api_remove(request: Request, slug: str):
    user = user_of(request)
    if not user:
        return JSONResponse({"error": "not logged in"}, status_code=401)
    faults.maybe_slow_down("api")
    if faults.api_should_fail():
        return JSONResponse({"error": "cart service error"}, status_code=500)
    if not faults.on("BUG_CART_REMOVE"):
        db.remove_from_cart(cart_of(request), slug)
    return cart_json(cart_of(request))


@app.get("/cart.html", response_class=HTMLResponse)
def cart_page(request: Request):
    user = user_of(request)
    if not user:
        return to_login()
    faults.maybe_slow_down("cart")
    items = db.cart(cart_of(request))
    return templates.TemplateResponse(
        request, "cart.html", {"items": items, "cart_count": len(items), "money": money}
    )


@app.get("/checkout-step-one.html", response_class=HTMLResponse)
def checkout_one(request: Request):
    user = user_of(request)
    if not user:
        return to_login()
    count = len(db.cart(cart_of(request)))
    return templates.TemplateResponse(request, "checkout_one.html", {"error": None, "cart_count": count})


@app.post("/checkout-step-one.html", response_class=HTMLResponse)
def checkout_one_submit(
    request: Request, firstName: str = Form(""), lastName: str = Form(""), postalCode: str = Form("")
):
    user = user_of(request)
    if not user:
        return to_login()
    error = None
    if not faults.on("BUG_CHECKOUT_VALIDATION"):
        if not firstName.strip():
            error = "Error: First name is required."
        elif not lastName.strip():
            error = "Error: Last name is required."
        elif not postalCode.strip():
            error = "Error: Postal code is required."
    if error:
        count = len(db.cart(cart_of(request)))
        return templates.TemplateResponse(
            request,
            "checkout_one.html",
            {"error": error, "cart_count": count, "first": firstName, "last": lastName, "postal": postalCode},
        )
    request.session["shipping"] = {"first": firstName, "last": lastName, "postal": postalCode}
    return RedirectResponse("/checkout-step-two.html", status_code=303)


@app.get("/checkout-step-two.html", response_class=HTMLResponse)
def checkout_two(request: Request):
    user = user_of(request)
    if not user:
        return to_login()
    faults.maybe_slow_down("checkout")
    items = db.cart(cart_of(request))
    subtotal, tax, total = totals(items)
    return templates.TemplateResponse(
        request,
        "checkout_two.html",
        {
            "items": items,
            "cart_count": len(items),
            "money": money,
            "subtotal": subtotal,
            "tax": tax,
            "total": total,
        },
    )


@app.post("/checkout-step-two.html")
def checkout_finish(request: Request):
    user = user_of(request)
    if not user:
        return to_login()
    faults.maybe_slow_down("checkout")
    items = db.cart(cart_of(request))
    subtotal, tax, total = totals(items)
    order_id = db.create_order(user, subtotal, tax, total)
    db.clear_cart(cart_of(request))
    request.session["last_order"] = order_id
    return RedirectResponse("/checkout-complete.html", status_code=303)


@app.get("/checkout-complete.html", response_class=HTMLResponse)
def checkout_complete(request: Request):
    user = user_of(request)
    if not user:
        return to_login()
    return templates.TemplateResponse(
        request, "complete.html", {"order_id": request.session.get("last_order"), "cart_count": 0}
    )
