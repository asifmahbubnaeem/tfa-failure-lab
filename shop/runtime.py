"""Lab-only runtime hooks: intermittent slowness and server errors, switched on by environment
variables. They live here, as middleware, so the shop's own code stays free of them: this file is
left out of the source snapshot an analyzer may read.

  FLAKY_PAGES=inventory     comma-separated: inventory, cart, checkout, api
  FLAKY_PCT=30              percent of requests to those pages that are slowed down
  FLAKY_DELAY_MS=35000      how long a slowed request waits. Keep it above 30000: Playwright waits up
                            to 30 s for a page, so shorter delays are absorbed silently
  FLAKY_API_500_PCT=20      percent of add/remove-to-cart calls answered with HTTP 500

Product bugs and test bugs are not switches: they are code changes in scenarios/patches/.
"""

import asyncio
import os
import random

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

PAGE_PREFIXES = {
    "inventory": "/inventory.html",
    "cart": "/cart.html",
    "checkout": "/checkout-step-two.html",
    "api": "/api/cart/",
}


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


class _Hooks(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        path = request.url.path
        pages = {p.strip() for p in os.environ.get("FLAKY_PAGES", "").split(",") if p.strip()}
        if any(path.startswith(PAGE_PREFIXES[p]) for p in pages if p in PAGE_PREFIXES):
            if random.uniform(0, 100) < _int("FLAKY_PCT", 0):
                await asyncio.sleep(_int("FLAKY_DELAY_MS", 35000) / 1000)
        if path.startswith("/api/cart/") and request.method in ("POST", "DELETE"):
            if random.uniform(0, 100) < _int("FLAKY_API_500_PCT", 0):
                return JSONResponse({"error": "cart service error"}, status_code=500)
        return await call_next(request)


def install(app) -> None:
    app.add_middleware(_Hooks)
