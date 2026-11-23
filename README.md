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
| `shop/faults.py` | The fault switches |
| `.github/workflows/e2e.yml` | Runs the suite in GitHub Actions, clean or with faults on |

## Fault switches

Each switch maps to one failure category, so the label of the resulting failure is known.

| Switch | Category | What breaks | Tests that fail |
| --- | --- | --- | --- |
| `BUG_SORT_PRICE=1` | product_bug | Price sort compares text, so "$9.99" sorts after "$79.00" | 2 price-sort tests |
| `BUG_TAX_ROUNDING=1` | product_bug | Tax is cut off but the total is rounded, so item + tax ≠ total | order-summary totals |
| `BUG_CART_REMOVE=1` | product_bug | Removing a cart item does nothing on the server | remove-item |
| `BUG_CHECKOUT_VALIDATION=1` | product_bug | Checkout accepts empty fields | required-fields check |
| `FLAKY_API_500_PCT=40` | flaky | That share of add/remove-to-cart calls return HTTP 500 | different cart tests each run |
| `FLAKY_PAGES=inventory FLAKY_PCT=30` | flaky | That share of page loads take 35 s (`FLAKY_DELAY_MS`), past Playwright's 30 s wait | different tests each run |
| `TAX_RATE` unset | environment | Checkout overview crashes on missing config | 3 checkout tests |
| Database stopped or wrong `DATABASE_URL` | environment | Everything after login fails; login still works | 14 tests |

Test-side breakage (`test_bug`: a renamed locator, a stale expected value) and runner problems
(`infra`: job timeout, memory cap) are made by changing the suite or the workflow, not the shop.

Flaky switches are random by design: a run may pass. Run them more than once.

All of the above was checked locally: the clean suite passes 3 runs in a row (and with 4
parallel workers), and each switch fails exactly the tests listed.

## Run it in GitHub Actions

Pushes run the suite clean; it must pass. To break the shop, open **Actions → e2e → Run
workflow** and fill in `faults` (for example `BUG_TAX_ROUNDING=1`), tick `unset_tax_rate` or
`break_database`, or name a test patch in `suite_patch`.
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

Each case goes to `cases/case-NNNN/` with `junit.xml`, `test_log.txt` (the test step's log
only), `shop.log` and `label.json`. Folder names are neutral and the files the agent reads are
checked so that nothing names the injected fault; only `label.json` holds the answer.

## Run it locally

Needs Python 3.11+ and a Postgres. With Docker: `docker compose up -d` starts one on port 5433.

```sh
python -m venv .venv && . .venv/bin/activate
pip install -r shop/requirements.txt ./suite
python -m playwright install chromium

export DATABASE_URL=postgresql://lab:lab@localhost:5433/labshop TAX_RATE=0.08
uvicorn shop.app:app --port 8000 &          # add fault switches in front, e.g. BUG_SORT_PRICE=1

cd suite && pytest --junitxml=reports/junit.xml
```

Lab accounts: `standard_user` and `locked_out_user`, password `lab_password`. These are
lab-only and protect nothing.
