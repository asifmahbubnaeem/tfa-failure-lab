# tfa-failure-lab

A small online shop, an end-to-end test suite for it, and switches that break the shop on
purpose. It produces **labelled CI test failures**: because we choose what breaks, we know for
certain why each test failed. Those failures are the eval set for
[test-failure-analyzer](https://github.com/asifmahbubnaeem/test-failure-analyzer), an AI agent
that reads a failed CI run and explains it.

| Part | What it is |
| --- | --- |
| `shop/` | **Lab Shop**: FastAPI + Postgres. Login, products, cart, checkout. |
| `suite/` | Playwright + pytest e2e suite (21 tests), adapted from [ecommerce-automation-test-playwright-pytest](https://github.com/asifmahbubnaeem/ecommerce-automation-test-playwright-pytest) |
| `scenarios/` | Scenario list, patches, and the case collector |
| `shop/runtime.py` | Switches for intermittent problems |
| `.github/workflows/e2e.yml` | Runs the suite in GitHub Actions, clean or with faults on |

## Scenarios

Each scenario breaks one thing in a known way, so the label of the resulting failure is known.
Bugs are real code changes (patches in `scenarios/patches/`); intermittent and environment
problems are switches.

| Scenario | Category | What breaks | Tests that fail |
| --- | --- | --- | --- |
| `pb-sort-price` | product_bug | `shop.js` sorts by the price text, so "$9.99" sorts after "$79.00" | 2 price-sort tests |
| `pb-tax-rounding` | product_bug | Tax rounded down, total rounded separately: item + tax ≠ total | order-summary totals |
| `pb-cart-remove` | product_bug | The DELETE looks the product up by name instead of slug | remove-item |
| `pb-checkout-validation` | product_bug | Validation checks for `None` instead of empty strings | required-fields check |
| `tb-checkout-locator` | test_bug | Page object uses a locator that doesn't exist | 4 checkout tests |
| `tb-confirmation-text` | test_bug | Test expects text the page never shows | confirmation test |
| `tb-sort-direction` | test_bug | Z-to-A test asserts A-to-Z order | 1 sort test |
| `tb-password-typo` | test_bug | Typo in the test user's password | 15 tests |
| `FLAKY_API_500_PCT=40` | flaky | That share of add/remove-to-cart calls return HTTP 500 | different cart tests each run |
| `FLAKY_PAGES=inventory FLAKY_PCT=25` | flaky | That share of page loads take 35 s, past Playwright's 30 s wait | different tests each run |
| `TAX_RATE` unset | environment | Checkout overview crashes on missing config | 3 checkout tests |
| Database unreachable | environment | Everything after login fails; login still works | 14 tests |

The flaky switches are middleware in `shop/runtime.py`, so the shop's own code stays clean.
They are random by design: a run may pass.

Every case also gets one harmless change (`noise-*` patches: a style tweak, a footer, a test
helper docstring, a comment in `docker-compose.yml`), so every case has a diff and "nothing changed" never gives the
answer away.

All of this was checked locally: the clean suite passes 6 restarts in a row with 4 parallel
workers, each bug patch fails exactly the tests listed, and each harmless change fails none.
Getting there meant fixing two races that were hiding in the suite and the shop: page objects
read the next page before it had loaded, and the sort menu could be used before its script ran.

## Run it in GitHub Actions

Pushes run the suite clean; it must pass. To break the shop, open **Actions → e2e → Run
workflow** and list patches in `patches` (for example `pb-tax-rounding noise-css`), fill in
`faults` (for example `FLAKY_API_500_PCT=40`), or tick `unset_tax_rate` or `break_database`.
When tests fail, the failed ones are rerun once, and both reports are kept.
Reports (JUnit XML, HTML, screenshots) and the shop log are uploaded as the `e2e-reports`
artifact.

## Collect labelled eval cases

`scenarios/scenarios.toml` lists 12 scenarios: 4 product bugs, 4 test bugs (patches in
`scenarios/patches/`), 2 flaky modes and 2 environment faults. `scenarios/collect.py` runs them
in GitHub Actions through the GitHub CLI and saves each failed run as a case:

```sh
python3 scenarios/collect.py --list
python3 scenarios/collect.py --only pb-tax-rounding
python3 scenarios/collect.py --all --runs 2
```

Each case goes to `cases/case-NNNN/`:

| File | What |
| --- | --- |
| `junit.xml` | The test report |
| `rerun.xml` | The report from rerunning only the failed tests once |
| `test_log.txt` | The test step's log, nothing else |
| `shop.log` | The application's log |
| `diff.patch` | What changed in this run, like a commit's diff |
| `source/` | The test code and shop code as tested |
| `label.json` | Category and root cause: the answer, never shown to the analyzer |

Folder names are neutral, and every file an analyzer reads is checked so that nothing names the
injected fault.

## Run it locally

Needs Python 3.11+ and a Postgres. With Docker: `docker compose up -d` starts one on port 5433.

```sh
python -m venv .venv && . .venv/bin/activate
pip install -r shop/requirements.txt ./suite
python -m playwright install chromium

export DATABASE_URL=postgresql://lab:lab@localhost:5433/labshop TAX_RATE=0.08
uvicorn shop.app:app --port 8000 &          # flaky switches go in front, e.g. FLAKY_API_500_PCT=40

cd suite && pytest --junitxml=reports/junit.xml
```

Lab accounts: `standard_user` and `locked_out_user`, password `lab_password`. These are
lab-only and protect nothing.
