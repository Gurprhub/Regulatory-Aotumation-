# Regulatory Automation

Regulatory compliance management for agrochemical companies — track registrations,
state sale permissions, licences and label approvals in one place.

Four registers, one renewal queue. Every certificate carries a validity window;
the application derives each item's compliance state from those dates on every
read, merges all four registers into a single list ordered by urgency, and shows
it on a dashboard you can filter and export.

## What it tracks

| Register | What it holds |
| --- | --- |
| **Products** | Technical grades and formulations — active ingredient, concentration, CAS number, category, formulation code. |
| **Registrations** | CIB&RC certificates, by section of the Insecticides Act (9(3), 9(3B), 9(4), import variants) and purpose. |
| **State sale permissions** | Permission to sell a registered product in a given state or union territory. |
| **Licences** | State licences to manufacture, sell, stock or store, with the products each one covers. |
| **Label approvals** | Approved label and leaflet versions, with languages. |

## Compliance states

Each item's state is computed from its dates and status, never stored, so a
record cannot go stale because nobody re-saved it:

| State | Meaning |
| --- | --- |
| `valid` | In force, and more than `WARNING_DAYS` from expiry (or with no expiry at all). |
| `expiring_soon` | Expires within `WARNING_DAYS` (default 90). |
| `critical` | Expires within `CRITICAL_DAYS` (default 30). |
| `expired` | Past its `valid_until` date. |
| `not_yet_effective` | `valid_from` is still in the future. |
| `non_compliant` | Suspended, cancelled or surrendered — unusable whatever its dates say. |

Two deliberate rules:

* **`under_renewal` does not extend validity.** Filing a renewal does not keep a
  certificate in force, so an item under renewal still reports as expiring or
  expired on its own dates. The status is recorded, but it never masks the risk.
* **Suspension outranks the calendar.** A suspended permission is `non_compliant`
  even if it has years left to run, and it stays at the top of the renewal queue.

## Getting started

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python -m scripts.seed          # optional: a sample portfolio spanning every state
uvicorn app.main:app --reload
```

Then open <http://localhost:8000> for the dashboard, or
<http://localhost:8000/docs> for the interactive API reference.

The seed script refuses to run against a database that already holds products;
pass `--reset` to drop everything and start again.

## Configuration

Read from the environment:

| Variable | Default | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | `sqlite:///./regulatory.db` | Any SQLAlchemy URL. SQLite needs no setup; point it at PostgreSQL for shared use. |
| `CRITICAL_DAYS` | `30` | Items within this many days of expiry are `critical`. |
| `WARNING_DAYS` | `90` | Items within this many days are `expiring_soon`; also the default alert horizon. |

`WARNING_DAYS` must be greater than or equal to `CRITICAL_DAYS`; the application
refuses to start otherwise.

## API

All payloads are JSON. Unknown fields are rejected rather than silently dropped,
so a misspelt date field fails loudly instead of going unrecorded.

### Registers

Each register supports the same five operations:

```
GET    /api/{register}            list, with filters and pagination
POST   /api/{register}            create
GET    /api/{register}/{id}       read one
PATCH  /api/{register}/{id}       partial update
DELETE /api/{register}/{id}       delete
```

where `{register}` is `products`, `registrations`, `sale-permissions`,
`licences` or `label-approvals`.

Every read of a dated register includes the derived `compliance_state` and
`days_remaining` alongside the stored fields.

Common list filters: `status`, `compliance_state` (repeatable),
`expiring_within=<days>`, `limit`, `offset`. Registers add their own —
`state` and `product_id` where they apply, `q` for product search,
`section`/`purpose` for registrations, `licence_type` for licences.

```bash
# Registrations that lapse inside 45 days
curl 'localhost:8000/api/registrations?expiring_within=45'

# Everything suspended or cancelled in Punjab
curl 'localhost:8000/api/sale-permissions?state=Punjab&compliance_state=non_compliant'
```

### Dashboard and alerts

```
GET /api/dashboard        counts per register and per state, plus the renewal queue
GET /api/alerts           the unified queue: every register, most urgent first
GET /api/alerts.csv       the same queue as a CSV download
GET /api/reference        controlled vocabularies (states, sections, licence types…)
GET /health               liveness, echoing the configured windows
```

`/api/alerts` and `/api/alerts.csv` take `within_days` (defaults to
`WARNING_DAYS`), plus repeatable `register` and `state` filters.

```bash
curl 'localhost:8000/api/alerts?within_days=30&register=licence'
curl -o queue.csv 'localhost:8000/api/alerts.csv?within_days=60'
```

Items that are already expired or non-compliant always appear in the queue
regardless of the horizon — a lapsed licence does not stop mattering because you
asked about the next 14 days.

## Data model notes

* State and union territory names are normalised against a fixed list, so
  `punjab`, `Punjab ` and `PUNJAB` all store as `Punjab`, and an unknown state is
  rejected at the edge.
* Uniqueness is scoped the way the regime is: a registration number is unique
  nationally; a sale permission number is unique within its state; a licence
  number is unique within its state; a label approval is unique per number *and*
  version, so successive versions can coexist.
* Deleting a product cascades to its registrations, sale permissions and label
  approvals. The UI warns before doing it.
* Licences link to the products they cover (many-to-many), so
  `/api/licences?product_id=3` answers "what may we still make and sell?".

## Development

```bash
pip install -r requirements-dev.txt
pytest
```

The suite covers the compliance engine's boundaries (including the exact
threshold days), every register's CRUD and validation rules, uniqueness scoping,
cascade behaviour, the dashboard aggregation and the CSV export.

### Layout

```
app/
  compliance.py   the engine: dates + status -> state, and the unified queue
  reference.py    controlled vocabularies (states, sections, licence types)
  models.py       ORM models; the validity envelope is a shared mixin
  schemas.py      request/response schemas; reads carry the derived verdict
  services.py     persistence helpers and cross-register aggregation
  routers/        one module per register, plus the dashboard
  static/         the bundled dashboard UI (no build step)
scripts/seed.py   sample portfolio, dated relative to today
tests/
```
