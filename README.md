# JSW C&F Operations App

A production-structured web app for running a JSW Cement Clearing & Forwarding
operation across **multiple godowns** (Manesar and Daultabad, extensible to
more): stock, GRN, dispatch, billing, dealer-wise advance/hold reconciliation,
transporter freight, and a protected commission/claim page.

Built with **FastAPI + SQLAlchemy + SQLite (Postgres-ready) + server-rendered
Jinja2/Bootstrap pages**. No separate frontend build step — one Python process
serves the whole thing.

## What's in here, mapped to your requirements

| # | Requirement | Where |
|---|---|---|
| 1 | Opening stock, multi-product, add new product | `/stock` (opening) + `/products` (admin, add products anytime) |
| 2 | GRN entry after SAP → auto stock update | `/import` (SAP Material In export) — **the only way GRN rows are created**; `/grn` is a read-only list. Stock is *computed*, not a stored running number, so it can never silently drift |
| 3 | Dispatch entry | `/dispatch` |
| 4 | Billing entry from invoice | `/import` (SAP Sale export) — **the only way Billing rows are created**; `/billing` is a read-only list |
| 5 | Dealer-wise & product-wise advance/hold, auto-adjusting | `/dealer-report` — see "How advance/hold works" below |
| 6 | Truck-wise & transporter-wise freight, by district/pincode | `/freight` — rate cards are **per godown**, managed under `/products` (add, edit, delete, and an "All godowns" comparison view) |
| 7 | Protected commission + secondary freight claim, different rate than what's paid to transporter | `/claims` — **admin role only**, not shown to staff users, and not shown on the public `/freight` page |

## GRN and Billing are SAP-only

There is deliberately **no manual entry form** for GRN or Billing, and no
`/grn/add` or `/billing/add` endpoint at all. Both are created exclusively by
importing the SAP exports on `/import`:

- **Material In export → GRN**, keyed on Material Document.
- **Sale export → Billing**, keyed on Invoice No.

Why: those keys are what make a re-upload safe, and they only exist if the
row exists in SAP. Allowing a hand-typed GRN or invoice alongside them would
mean a receipt could sit in this app that SAP has never heard of, a truck
could be counted twice under two different spellings, and the dealer
Advance/Hold balance would stop being a reconciliation against SAP.

`/grn` and `/billing` remain as read-only, filterable registers, each showing
when the last import of its type ran and linking straight to the Import page.
**Dispatch is still entered by hand** (`/dispatch`) — that's the one event
that happens at your godown rather than in SAP.

**Also included:**

- **Backup & CSV export** (`/backup`, admin only) — one-click download of the
  entire database, plus CSV exports of GRN/Dispatch/Billing individually.
  Do this before every update and on a regular schedule; it's the only copy
  of your operational records.
- **Filters on GRN, Dispatch, and Billing** — by date range, product,
  dealer, transporter, and vehicle/invoice number, so these lists stay
  usable once you have months of data instead of a handful of rows.
- **Multi-godown support** — see the dedicated section below.

**Beyond what you asked for**, because they matter for a real operation:

- **Dealer and Transporter master lists** (not just free text) — this stops
  "Sharma Traders" vs "Sharma Trader's" from silently splitting your
  dealer-wise report into two rows. New names typed on the Dispatch/Billing
  forms are added to the master automatically.
- **User accounts with roles** (admin/staff) instead of one shared login —
  your godown staff can enter GRN/dispatch/billing without ever seeing the
  commission or company freight-claim numbers.
- **Duplicate GRN detection** on SAP GRN No., so the same truck can't get
  double-entered by mistake.
- **Missing-rate flags** on the freight report, so a truck to a district you
  haven't priced yet is visibly called out instead of silently costed at ₹0.

## Bag size is per product (Microfine is a 20 kg bag)

Everything is stored in **bags** internally, and every crossing between bags
and tonnes uses the bag size of *that product*:

- Cement is a 50 kg bag = 0.05 MT (20 bags to the tonne). This is the global
  `bag_weight_mt` setting and stays the default.
- **JSW Microfine is a 20 kg bag = 0.02 MT, so 1 MT is 50 bags**, not 20.

Set it per product under **Masters -> Products** — enter the bag size in kg and
the row shows what 1 MT works out to. Leave a product alone and it uses the
50 kg default, so this only ever corrects the products that need it.

The bag size drives three things, all from one place
(`crud.product_bag_weight_mt`), so a product can't be right in one report and
wrong in another:

1. **Stock in MT** on the Stock page and the dashboard total.
2. **The MT->bags conversion on SAP import** — SAP reports quantity in MT, so a
   1 MT Microfine invoice line imports as 50 bags.
3. **The weight a truck is rated on for freight**, which is charged per MT —
   400 bags of Microfine is 8 MT, where 400 bags of cement is 20 MT. Getting
   this wrong would over-claim a Microfine truck by 2.5x.

Commission is Rs/bag, so it is unaffected by bag size.

On an existing database the column is added automatically on the next start,
with every product left on the 50 kg default except Microfine, which is set to
20 kg. **Any Microfine rows imported before this change carry the old 50 kg
conversion** — if you have some, delete those GRN/billing rows and re-import
the file, since the importer skips rows it has already seen.

## How the advance/hold logic works (requirement 5)

For every (dealer, product) pair:

```
balance = total billed (bags) − total dispatched (bags)
```

- `balance > 0` → **Advance** (you've billed the dealer ahead of dispatch — owed to them)
- `balance < 0` → **Hold** (you've dispatched ahead of billing — owed to you/JSW)
- `balance = 0` → **Settled**

Because it's a running net balance recomputed from every transaction, a later
matching entry on either side (a delayed dispatch against an advance, or a
delayed invoice against a hold) automatically nets the balance down — there's
no separate "adjustment" step to run.

## Importing from SAP (Sale & Material In exports)

Instead of retyping GRN and Billing entries by hand, download the **Sale**
and **Material In** reports from SAP and upload them under **Import from
SAP** in the nav bar.

- **Sale export → Billing.** Maps Invoice No., Invoice Date, Sold-To Party,
  Product, Qty, and Total Value. SAP reports Qty in **metric tonnes**, so
  it's converted to bags using **that product's** bag size (50 kg for cement,
  20 kg for Microfine — see "Bag size is per product" above), the same
  conversion the rest of the app uses everywhere.
- **Material In export → GRN.** Maps Material Document, Posting Date, Truck
  No., Product, Qty, and Supplying Plant. Rows still showing **"Stock in
  Transit"** (i.e. not yet actually received) are skipped automatically —
  only rows with Status = "Inward" and a real Material Document get posted
  as GRN. Re-upload the same (or a newer) file later and those rows will be
  picked up once they've actually arrived.
- **Re-uploads are safe.** Billing rows are matched by Invoice No. and GRN
  rows by Material Document — anything already imported is silently
  skipped, so it's fine to upload an export that overlaps with a previous
  one (e.g. a weekly file that repeats yesterday's rows).
- **New dealers, transporters, and SAP product codes are handled
  automatically where possible.** A dealer or transporter name seen for the
  first time is added to the master list on the fly (matched by SAP code
  going forward, so a spelling variation later doesn't create a duplicate).
  A **product code** SAP uses that doesn't match anything in your product
  list gets flagged on the Import page instead of guessed at — map it to an
  existing product, create a new one for it, or mark it "ignore" (useful
  for codes like raw-material inputs that aren't a cement product you
  stock), then re-upload the file to pull those rows in.

## Multi-godown support (Manesar + Daultabad)

Every stock/GRN/dispatch/billing/freight-rate record belongs to a specific
**godown** (storage location). Manesar and Daultabad are completely
independent of each other — separate stock, separate GRN/dispatch/billing
logs, separate freight rate cards — while dealers, transporters, and
products are shared across both (the same dealer can buy from either
location; a product exists company-wide).

- **The navbar has a Godown switcher.** Whichever godown is selected there
  is where new GRN/Dispatch/Billing entries get recorded, and what the
  Stock/GRN/Dispatch/Billing pages show. Switch it before you start
  entering a day's data for a given location.
- **Dealer Advance/Hold, Freight, and Claims** default to showing the
  active godown, but each has an **"All Godowns"** option for combined
  totals — useful for a dealer's total exposure or your total monthly
  claim across both locations.
- **Freight rate cards are godown-specific.** The same district can (and
  usually will) have a different rate from each godown, since the
  distance differs. Manage them under Masters → Freight Rate Card: pick
  which godown's card you're looking at from the dropdown in that section
  (or "All godowns (compare)" to see every location's rates side by side).
  The godown is chosen on the add-rate form itself, so a rate can't land on
  the wrong location just because the navbar switcher was left elsewhere.
  Rates can be **edited in place and deleted** — a rate that changes is
  corrected on its existing row rather than needing a duplicate. Each row
  also shows the per-MT margin between what you pay the transporter and
  what you claim from JSW.
- **Adding a third godown later** is just Masters → Godowns → Add — no
  code changes needed, it immediately gets its own independent stock/GRN/
  dispatch/billing/freight scope like Manesar and Daultabad do.

**If you're upgrading from a single-godown version of this app**, the
migration is automatic and safe: on first startup after upgrading, every
existing GRN/Dispatch/Billing/OpeningStock/FreightRateCard row is
automatically tagged as belonging to "Manesar Godown" (a new "Daultabad
Godown" is created alongside it, empty and ready to use). This was tested
against a full simulated copy of a real single-godown database — existing
data, stock calculations, and login credentials all carry over unchanged.
**Back up your database before upgrading anyway** (see Backup & Export
above) — that's just good practice for any schema change, automatic or not.

## Running it locally (no Docker)

**The short version — one command:**

```bash
cd ~/Desktop/jsw_cf_app
./run.sh              # or ./run.sh 8080 to use a different port
```

`run.sh` finds a Python 3.10+, creates `venv/` and installs the pinned
requirements on first run (a minute or so; instant on later runs), creates
`.env` from the example if it's missing, starts the server on
http://127.0.0.1:8000 and opens it in your browser. Ctrl+C stops it.

**The manual version**, if you'd rather drive it yourself. Requires Python 3.11+.

```bash
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env: set a real SECRET_KEY (see the comment in the file for how to
# generate one) and change ADMIN_PASSWORD.

uvicorn app.main:app --reload --port 8000
```

Open http://127.0.0.1:8000, log in with the admin username/password from
`.env`, and change that password immediately from the Masters page (add a
new admin user for yourself, then disable the seed `admin` account, or just
treat `.env`'s password as final if you're the only user).

## Moving off Docker to the local run

If you've been running this under Docker, its database lives in the named
volume `jsw_cf_data` at `/app/data/jsw_cf.db`. The local run can't see that
volume, so the data has to be copied across once — and the container has to be
retired, or it keeps holding port 8000 and keeps serving whatever code its
image was built from (which is how you end up looking at an old version of the
app while the updated code sits unused on disk).

```bash
./import_from_docker.sh                    # look only: lists containers/volumes, changes nothing
./import_from_docker.sh --import --retire  # copy the data across, then stop + remove the container
```

What `--import --retire` does:

- snapshots the database *inside* the container if it's still running, so you
  never copy a half-written file;
- backs up whatever is already in `./data/` and keeps an untouched copy of the
  import alongside it;
- prints the row counts that came across, and whether the schema still needs
  the single-godown → multi-godown migration (which runs on the next start and
  tags existing rows to Manesar Godown);
- clears the container's restart policy, stops it and removes it, so it can't
  reappear after a reboot;
- **leaves the Docker volume alone.** Your data stays in there as a fallback
  until you delete it yourself with `docker volume rm jsw_cf_data`.

The users table comes across with the data, so log in with the credentials you
used under Docker — `ADMIN_PASSWORD` in `.env` only applies to a database with
no users in it yet.

## Running it with Docker

Kept here for reference — the setup in use is the local `./run.sh` one above.
If you go back to Docker, remember the image bakes in a copy of `app/`, so
code changes need `docker compose up -d --build`, not just a restart.

```bash
cp .env.example .env
# edit .env as above

docker compose up -d --build
```

The app will be on port 8000. Data persists in the `jsw_cf_data` Docker
volume even if the container restarts or gets rebuilt.

To go to Postgres instead of SQLite (only worth it if several people will
hit this concurrently, or you want off-box backups): uncomment the `db`
service in `docker-compose.yml` and change `DATABASE_URL` in `.env` to
`postgresql+psycopg2://jsw_cf:<password>@db:5432/jsw_cf`. You'll also need
to add `psycopg2-binary` to `requirements.txt`. No application code changes
are needed — SQLAlchemy handles both databases identically.

## Deploying on a plain VPS without Docker

1. Copy this folder to the server, e.g. `/opt/jsw_cf_app`.
2. `python -m venv venv && venv/bin/pip install -r requirements.txt`
3. Set up `.env` as above.
4. Copy `deploy/jsw_cf_app.service` to `/etc/systemd/system/`, adjust the
   `User`/`Group`/paths, then:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable --now jsw_cf_app
   ```
5. Put nginx in front of it using `deploy/nginx.conf.example` as a starting
   point, then get HTTPS with `certbot --nginx`.

## Troubleshooting

**The app is showing an old screen — e.g. a "Save GRN" form that shouldn't
exist any more.** You're almost certainly looking at the Docker container, not
the local run. The Docker image bakes in a copy of `app/` at build time, so a
container built before a code change keeps serving the old templates no matter
what's on disk, and it holds port 8000 so http://127.0.0.1:8000 reaches it
rather than `./run.sh`. Check with `docker ps`; retire it with
`./import_from_docker.sh --import --retire` (see "Moving off Docker" above), or
rebuild it with `docker compose up -d --build` if you're staying on Docker.


**"Internal Server Error" right after `docker compose up -d --build`, on a
fresh install.** This was a real bug in an earlier version of this Dockerfile
(now fixed) — it ran 2 gunicorn workers, which could race to create the
database tables on first startup and crash. If you're on the current
Dockerfile this shouldn't happen, but if it does: `docker compose down -v`
(the `-v` wipes the database volume — only do this if you don't have real
data in it yet) then `docker compose up -d --build` for a clean slate, and
check `docker compose logs --tail=100` for the actual error if it persists.

## Security notes — read before exposing this beyond localhost

- **Change `SECRET_KEY`** in `.env` to a real random value. The app logs a
  warning on startup if you haven't.
- **Change the admin password** immediately after first login.
- The `/claims` page is gated by the `admin` role in the app, which is
  adequate once the app itself sits behind HTTPS. It is *not* a substitute
  for HTTPS — don't run this over plain HTTP on the open internet.
- Back up the `data/jsw_cf.db` file (or your Postgres database) regularly —
  it's the only copy of your operational records.

## Running the tests

```bash
pip install pytest
pytest tests/ -v
```

32 tests in `test_app.py` cover login/role enforcement, stock computation,
duplicate-safe GRN re-import, that the manual GRN/Billing endpoints are gone
and their pages carry no entry form, the advance/hold engine's Advance →
Settled and Hold → Settled transitions, freight rate lookup, that the company
claim rate never leaks onto the public Freight page, edit/delete on dispatch,
the GRN/Dispatch/Billing filters (including the blank-field edge case that
once caused a 422), and the backup/CSV export endpoints. Tests needing GRN or
Billing rows build a small Sale / Material In workbook in memory and upload it
through the real import endpoint (helpers in `tests/conftest.py`), so they run
against the same code path production uses.

8 tests in `test_sap_import.py` run the real SAP import logic against the
actual Sale/Material-In export files you provided, checking MT→bags
conversion, duplicate-safe re-import, and correct handling of "Stock in
Transit" and unmapped product code rows.

9 tests in `test_bag_weight.py` cover per-product bag size end to end: a 20 kg
product converts MT->bags correctly on both SAP imports, stock in MT uses the
product's own bag size, a Microfine truck is weighed at 8 MT where a cement
truck of the same bag count is 20 MT, cement still falls back to the 50 kg
default, and only admins can change a bag size.

4 tests in `test_migration.py` verify the pre-godown → multi-godown schema
migration against a simulated copy of a real single-godown database: existing
data survives, gets correctly tagged to Manesar, and the constraint rebuild
(needed so the same product/district can exist independently at a second
godown) actually works. Also checked for idempotency and a no-op on a fresh
database.

15 tests in `test_godown.py` cover the multi-godown feature directly: both
godowns exist after seed, the godown switcher works, GRN/stock/freight-rates
are correctly isolated between godowns, a rate can be added for a godown other
than the active one, the per-godown and "All godowns" rate views show the right
rows, rates can be edited and deleted without moving godown, one district
priced from two godowns costs each dispatch against its own godown's rate, the
dealer-report/freight "All Godowns" combined view works, and Masters won't let
you disable the last remaining active godown.

## Project layout

```
app/
  main.py          FastAPI app assembly, session middleware, error handlers
  config.py        Settings loaded from .env
  database.py      SQLAlchemy engine/session
  models.py        ORM models (Godown, Product, Dealer, Transporter, GRN,
                    Dispatch, Billing, FreightRateCard, User, Setting)
  migrations.py    One-time, idempotent schema migration: adds godown_id to
                    an existing pre-godown database without losing data
  godown_context.py  Resolves/switches the "active godown" stored in the
                    user's session; used by every godown-scoped page
  templating.py    Shared Jinja2Templates instance — auto-injects the
                    godown-switcher context into every page
  auth.py          Password hashing, session login, role-check dependencies
  crud.py          All business logic: stock calc, advance/hold engine,
                    freight lookup, commission/claim report — all godown-scoped
  sap_import.py    Parses SAP Sale/Material-In exports and imports them as
                    Billing/GRN rows (MT→bags conversion, duplicate-safe,
                    unmapped product code detection, tagged to the active godown)
  seed.py          Runs migrations, creates tables, seeds default products,
                    known SAP product-code mappings, both godowns, and the
                    first admin user
  routers/         One file per feature area (grn.py, dispatch.py, ...).
                    grn.py and billing.py are read-only views; those rows are
                    written only by sap_import.py via sap_import_router.py
  templates/       Jinja2 + Bootstrap 5 pages
  static/          Small CSS override file
tests/
  test_app.py      End-to-end tests using FastAPI's TestClient (no server needed)
  test_sap_import.py  Same, run against real SAP export files in tests/fixtures/
  test_migration.py   Verifies the pre-godown → multi-godown migration against
                    a simulated real database, including idempotency
  test_godown.py   Multi-godown isolation, switching, and combined-view tests
  test_bag_weight.py  Per-product bag size (50 kg cement vs 20 kg Microfine)
  conftest.py      Shared fixtures, incl. in-memory Sale/Material-In workbook
                    builders used to create GRN/Billing rows through the import
  fixtures/        Sample Sale/Material-In export files used by the tests above
deploy/
  jsw_cf_app.service     systemd unit
  nginx.conf.example     reverse proxy config
Dockerfile
docker-compose.yml
requirements.txt   Pinned to the exact versions this was built and tested against
.env.example
```

## Suggested next additions

Roughly in the order they'd pay off for a one-godown C&F operation:

1. **Export to Excel/PDF** — a "Download as Excel" button on the dealer
   report and monthly claims report, for sharing with JSW or your dealers
   directly (they're already used to seeing statements in that format).
2. **Low-stock alerts** — a threshold per product that flags on the
   dashboard when current stock drops below it, so you reorder before you
   run out.
3. **E-way bill number + validity tracking** on dispatch, with an alert
   before expiry.
4. **Monthly PDF commission bill generation** — auto-fill your actual
   billing format to JSW from the Claims page data, instead of retyping it.
5. **Audit trail** — you already log `created_by` on every GRN/dispatch/
   billing row; a simple "history" view per record (who entered what, when)
   is a small addition on top of that and pays off the first time there's a
   dispute.
6. **Mobile-friendly dispatch entry** — the Bootstrap layout already resizes
   reasonably on a phone, but a stripped-down single-column entry page would
   make it realistic for godown staff to log a dispatch from the yard on
   their phone as the truck leaves, rather than at a desk later.
7. **Godown-specific commission rates** — freight rates are already per
   godown, but the commission rate on `/claims` is still one global setting;
   if JSW's C&F agreement pays a different rate per location, this needs to
   become a per-godown setting (small change: move it from `Setting` into a
   column on `Godown`).
8. **Automated backups** — the manual "Download Full Backup" button on
   `/backup` covers on-demand safety, but a daily cron job that hits that
   same endpoint (or copies `data/jsw_cf.db` directly) to cloud storage
   would remove the need to remember. Losing this database is losing your
   operational records.
9. **Rate-limiting / lockout on login** — a basic protection against
   password-guessing if this is ever exposed to the open internet.
10. **Two-factor auth for the admin role**, given `/claims` holds your
    actual margin data — worth it once this isn't just you using the app.
