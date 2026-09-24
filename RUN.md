# AletheiaTelos DealFlow Engine — RUN.md

## What this is

A lean, deterministic deal acquisition and underwriting workflow:

PUBLIC LANDING PAGE → DEAL INTAKE → VALIDATION → CANONICAL DEAL RECORD →
UNDERWRITING → USER RESULTS → EXCEL ARTIFACT → INTERNAL DEAL PIPELINE →
DEAL REVIEW

Analytical and administrative only. It never executes trades or purchases,
never moves capital, never touches brokerage accounts, and never makes
investment decisions. Humans decide; the engine calculates.

## Install

Python 3.12. From the repo root:

```bash
pip install --break-system-packages flask
# openpyxl is already installed; the stdlib sqlite3 needs nothing.
```

(On systems without the PEP 668 restriction, plain `pip install flask` works.)

## Run the server

```bash
cd ~/workspace/ALETHEIATELOSDEALFLOWENGINE
python3 app.py
```

Open http://127.0.0.1:5000 — responsive on iPhone, iPad, tablet, desktop.
The SQLite database (`dealflow.db`) and generated workbooks (`artifacts/`)
are created in the repo directory on first use and are git-ignored.

## Restart-safe seed data

The Render free tier wipes `dealflow.db` on every restart. `seed.py` bakes
the 43 curated nationwide listings (LoopNet research, 2026-09-24) into the
app: `wsgi.py` (production) and `python3 app.py` (dev) call
`seed.seed_database()` on every boot, restoring the pipeline — including the
4 home-run deals triaged to PURSUE. Seeding is idempotent: existing deals
(matched by name + location or listing URL) are never duplicated, and listed
cap rates are recorded as evidence in the notes only, never used to derive
NOI. Manual reseed: `python3 seed.py`.

## Persistent database (PostgreSQL)

The seed above makes the *baseline* restart-safe, but anything changed
through the site after boot (new deals, status changes, DD work, uploads)
still lives in the ephemeral SQLite file. For true persistence, set the
`DATABASE_URL` environment variable to a PostgreSQL connection string:

```bash
DATABASE_URL=postgresql://user:password@host:5432/dbname python3 app.py
```

When `DATABASE_URL` is set, `records.py` uses PostgreSQL (via `psycopg`,
already in `requirements.txt`) instead of SQLite — same tables, same API,
same seed-on-boot behavior. Without it, everything works exactly as before
on local SQLite, which is also what the test suite uses.

On Render: create a PostgreSQL database (Render Postgres, Neon, and
Supabase all work — any provider that gives you a `postgresql://` URL),
then add `DATABASE_URL` as an environment variable on the
`aletheiatelos-dealflow` service in the Render dashboard. Never commit the
connection string; `render.yaml` declares the key with `sync: false` so the
dashboard holds the secret. Redeploy after setting it; on boot the app
creates the tables and seeds the 43 baseline deals into Postgres.

Still ephemeral after this change: uploaded DD *files* (`artifacts/dd/`)
and generated Excel workbooks (`artifacts/`). Their *metadata* is in the
database; the files themselves need object storage (S3/R2) as a later step.

## Run the tests

```bash
cd ~/workspace/ALETHEIATELOSDEALFLOWENGINE
python3 -m unittest discover -s tests -t .
```

Covers the README's test list: deal record (unique IDs, inputs preserved,
missing explicit), underwriting (direct/derived/missing NOI, missing financing,
invalid inputs, zero values, negative values, determinism), submission (valid,
incomplete optional, invalid required, persistence), Excel (generated, values
match record, calculations match app), pipeline (appears, detail opens, status
persists), and the web loop end to end.

## Import deals from JSON

```bash
python3 scripts/import_deals.py --file deals.json
```

`deals.json` is a JSON array of deal dicts with any of these keys:

```
name, asset_type, location, purchase_price, noi, egi, operating_expenses,
scheduled_rent_annual, vacancy_loss_annual, other_income_annual,
occupancy, ltv, interest_rate, amortization_years, hold_period,
exit_cap_rate, closing_costs, source,
contact_name, contact_email, contact_phone, source_url, notes
```

Every deal goes through the exact same path as a web submission —
validation → canonical record → deterministic underwriting → stored.
Invalid deals are reported and skipped.

## Key behaviors

- **NOI precedence:** explicit NOI wins. If NOI is absent but EGI *and*
  operating expenses are both supplied, NOI = EGI − operating expenses.
- **EGI derivation:** explicit EGI wins. If EGI is absent but annual
  scheduled rent, vacancy / collection loss, *and* other income are all
  supplied, EGI = scheduled rent − vacancy loss + other income. Either path
  feeds the unchanged NOI rule, so a deal with only rent detail still flows
  through the full chain: rent detail → EGI → NOI → cap rate → ….
- **Missing stays missing:** a metric that can't be computed is reported
  `unavailable` (or `unknown` when not applicable, e.g. DSCR with no debt) —
  never a silent zero, never an assumed all-cash deal.
- **Provenance:** every derived metric records its status and the exact
  inputs that produced it, in the app, the record, and the Excel workbook.
- **Deal IDs:** generated by the app as `AT-2026-000001`, … — never derived
  from property data.
- **Statuses:** `NEW → REVIEWING → PURSUE / HOLD / PASS`, changed by a human
  on the deal detail page. Reopening to REVIEWING is allowed.
- **Excel:** generated from the canonical record; an artifact, never a second
  source of truth.
- **Hold period** is treated in whole years for IRR / equity multiple; exit
  value uses current NOI with no growth invented.

## Due diligence

Every deal is seeded with a standard 19-item CRE due-diligence checklist
(Legal / Title, Financial, Physical / Environmental, Insurance / Compliance,
Association, Financing). On the deal detail page each item has:

- a human-controlled status (`not_started → requested → received →
  in_review → cleared`, plus `waived` and `issue` — any transition allowed),
- editable notes,
- file upload (stored under `artifacts/dd/<deal_id>/<item_key>/`, 50 MB max
  per file) with download links.

The detail page shows a progress summary ("Due diligence: n of 19
cleared"), the pipeline shows an `n/m` indicator per deal, and the Excel
workbook includes a Due Diligence sheet. Existing deals are backfilled with
the checklist on first run after upgrade (`records.backfill_dd_checklist()`),
without touching items already worked.

## Layout

```
app.py               Flask app: landing, intake, results, pipeline, detail, Excel,
                     DD status/notes/upload/download routes
validation.py        intake validation (required-missing / optional-missing / invalid / valid)
underwriting.py      pure deterministic calculation layer: calculate(inputs)
records.py           canonical deal record persistence (SQLite by default;
                     PostgreSQL when DATABASE_URL is set: deals, dd_items,
                     dd_documents tables; DD checklist seeding + backfill)
excel_gen.py         standardized Excel workbook generator (artifact only)
scripts/import_deals.py  JSON import through the real submission path
templates/           responsive UI (large touch-friendly controls)
tests/               full test suite (unittest)
```
