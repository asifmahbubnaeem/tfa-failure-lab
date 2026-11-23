"""Fault switches: environment variables that break Lab Shop on purpose.

Every switch is read per request, so one running shop can be flipped between scenarios by
restarting it with different variables. Each switch maps to exactly one failure category, so a
test failure produced with a switch on has a label known for certain.

Product bugs (category: product_bug)
  BUG_SORT_PRICE=1         price sort compares the text "$9.99" > "$79.00" instead of numbers
  BUG_TAX_ROUNDING=1       tax is cut off to the cent, total is rounded: item + tax != total
  BUG_CART_REMOVE=1        removing an item from the cart does nothing on the server
  BUG_CHECKOUT_VALIDATION=1  the checkout form accepts empty fields

Timing problems (category: flaky)
  FLAKY_PCT=30             percent of requests to the pages below that get slowed down
  FLAKY_DELAY_MS=35000     how long a slowed-down request waits. Keep it above 30000: Playwright
                           waits up to 30 s for a page, so shorter delays are absorbed silently
  FLAKY_PAGES=inventory    comma-separated: inventory, cart, checkout, api
  FLAKY_API_500_PCT=20     percent of add/remove-to-cart calls that fail with HTTP 500

Environment problems (category: environment): no switch needed
  - unset TAX_RATE            -> checkout overview fails with a KeyError
  - stop Postgres / bad DATABASE_URL -> every page after login fails
"""

import os
import random
import time


def on(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def maybe_slow_down(page: str) -> None:
    """Sleep FLAKY_DELAY_MS on FLAKY_PCT percent of requests to the listed pages."""
    pages = {p.strip() for p in os.environ.get("FLAKY_PAGES", "").split(",") if p.strip()}
    if page not in pages:
        return
    if random.uniform(0, 100) < _int("FLAKY_PCT", 0):
        time.sleep(_int("FLAKY_DELAY_MS", 35000) / 1000)


def api_should_fail() -> bool:
    return random.uniform(0, 100) < _int("FLAKY_API_500_PCT", 0)
