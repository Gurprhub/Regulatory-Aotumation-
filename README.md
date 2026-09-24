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
python -m scripts.create_admin  # the first account — prompts for a password
uvicorn app.main:app --reload
```

Then open <http://localhost:8000> for the dashboard, or
<http://localhost:8000/docs> for the interactive API reference.

The seed script refuses to run against a database that already holds products;
pass `--reset` to drop everything and start again.

Every endpoint needs an account, so create the first administrator before you
start. In a container where prompting is awkward, set `BOOTSTRAP_ADMIN_EMAIL`
and `BOOTSTRAP_ADMIN_PASSWORD` instead: they create an administrator on startup,
but only while the database has no accounts at all, so they cannot silently
resurrect an account later.

## Configuration

Read from the environment:

| Variable | Default | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | `sqlite:///./regulatory.db` | Any SQLAlchemy URL. SQLite needs no setup; point it at PostgreSQL for shared use. |
| `CRITICAL_DAYS` | `30` | Items within this many days of expiry are `critical`. |
| `WARNING_DAYS` | `90` | Items within this many days are `expiring_soon`; also the default alert horizon. |

| `SESSION_HOURS` | `12` | How long a browser session lasts. |
| `SESSION_COOKIE_SECURE` | `false` | Send the session cookie over HTTPS only. **Turn this on in production.** It defaults to off so the app still works over plain HTTP on localhost. |
| `MAX_FAILED_LOGINS` | `10` | Consecutive failures before an account is locked. |
| `LOCKOUT_MINUTES` | `15` | How long that lock lasts. |
| `BOOTSTRAP_ADMIN_EMAIL` | — | First-run administrator, created only while no accounts exist. |
| `BOOTSTRAP_ADMIN_PASSWORD` | — | Its password; must meet the 12-character minimum. |

`WARNING_DAYS` must be greater than or equal to `CRITICAL_DAYS`; the application
refuses to start otherwise.

## Access control

Every endpoint except `POST /api/auth/login`, `/health` and the sign-in page
requires an account. Roles are cumulative:

| Role | May |
| --- | --- |
| `viewer` | Read every register, the dashboard, the alert queue and the CSV export. |
| `editor` | Everything a viewer may, plus create, amend and delete records. |
| `admin` | Everything an editor may, plus manage accounts. |

Two ways to authenticate:

* **Session cookie** — `POST /api/auth/login` with an email and password. The
  cookie is `HttpOnly` and `SameSite=Lax`. The dashboard uses this.
* **Bearer token** — mint one at `POST /api/tokens` for a script or an
  integration, then send `Authorization: Bearer rat_…`. A token acts as its
  owner: it carries that account's role and stops working the moment the
  account is deactivated.

```bash
# Sign in and keep the cookie
curl -c jar.txt -X POST localhost:8000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"you@example.com","password":"…"}'

# Mint a token for an integration, then use it
curl -b jar.txt -X POST localhost:8000/api/tokens \
  -H 'Content-Type: application/json' -d '{"name":"ERP sync","expires_in_days":365}'
curl -H 'Authorization: Bearer rat_…' localhost:8000/api/alerts
```

The token's plaintext appears once, in the response that creates it. Only a
SHA-256 digest is stored, so it cannot be recovered — mint a new one instead.

### How credentials are held

* Passwords are hashed with **scrypt** (N=2¹⁵, r=8, p=1, per-password salt),
  using the standard library. Parameters are stored with each hash, so they can
  be raised later without invalidating existing passwords, and a password is
  re-hashed on next sign-in when its parameters are behind.
* Session identifiers and API tokens are 256-bit random secrets; only their
  SHA-256 digests are stored, and comparisons are constant-time.
* Minimum password length is 12 characters. There are no composition rules —
  they push people towards predictable substitutions without adding entropy.
* Sign-in failures are counted per account and lock it temporarily after
  `MAX_FAILED_LOGINS`. A wrong password, an unknown address, a deactivated
  account and a locked one all return the same 401, so an unauthenticated
  caller learns nothing about who has an account.
* Changing a password, resetting one as an admin, or deactivating an account
  revokes that account's sessions immediately rather than at expiry.

Signing out deletes the session server-side, so a copy of the cookie taken
beforehand is worthless afterwards.

## Audit trail

Every change to a register or an account is recorded: who made it, when, and
what the record said before. Capture hangs off SQLAlchemy's flush rather than
living in each endpoint, so a change is recorded because it reached the
database — not because someone remembered to log it. An endpoint added later is
audited without being told to, and a change made from a script or the shell is
recorded on the same terms as one made from the dashboard.

The event is written in the same transaction as the change it describes, so the
two commit or roll back together. There is no window in which a record is
amended but the trail does not say so, and a rejected change leaves no trace.

```
GET /api/audit       the trail, most recent first
GET /api/audit.csv   the same, as a CSV for an auditor
```

Filters: `entity_type`, `entity_id` (together these give one record's full
history), `actor_email`, `action` (`create`, `update`, `delete`), `since`,
`until`, `limit`, `offset`.

```bash
# Everything that happened to registration 12, including its creation
curl 'localhost:8000/api/audit?entity_type=registration&entity_id=12'

# Everything one person changed this month
curl 'localhost:8000/api/audit?actor_email=someone@example.com&since=2026-09-01'
```

An event reads correctly years later, on purpose:

* The actor's name and address and the record's label are **copied into the
  event**, not referenced. Deleting an account does not take its history with
  it, and a deleted record's entry still says which product and state it
  concerned.
* Updates carry both sides — `{"valid_until": {"from": "2026-10-15", "to":
  "2027-03-31"}}`. The previous value is read from the database during the
  flush, while the row still holds it, so it is recorded whether or not the
  application happened to have the old value in memory.
* Deleting a product records an event for **each** dependent registration, sale
  permission and label approval, not just for the product. This is why those
  relationships do not use `passive_deletes`: letting the database cascade
  silently would cost one query less and lose several compliance records from
  the trail.
* Secrets are never written down. A password or token change is recorded as
  having happened, with both values shown as `[redacted]`.
* Bookkeeping the application maintains by itself — `last_login_at`, failed
  sign-in counts, token `last_used_at` — is not recorded. Without that, signing
  in would append an event every time.

The trail is append-only: no endpoint amends or deletes an event, and a test
asserts that no route under `/api/audit` accepts anything but `GET`. Enforcing
that at the database level (a trigger, or revoking `UPDATE`/`DELETE` on the
table from the application's role) is worth doing if the trail must stand up to
a determined insider rather than to accident.

Anyone signed in may read the register trail — that is the point of keeping
one. Events about accounts and API tokens are restricted to admins.


## API

All payloads are JSON and every endpoint needs an account (see
[Access control](#access-control)). Unknown fields are rejected rather than
silently dropped, so a misspelt date field fails loudly instead of going
unrecorded.

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

### Accounts

```
POST   /api/auth/login             sign in, returns a session cookie
POST   /api/auth/logout            end the session (server-side, not just the cookie)
GET    /api/auth/me                the signed-in account
POST   /api/auth/change-password   change your own password
GET    /api/users                  list accounts             (admin)
POST   /api/users                  create an account         (admin)
PATCH  /api/users/{id}             amend, reset or disable   (admin)
DELETE /api/users/{id}             delete outright           (admin)
GET    /api/tokens                 your API tokens
POST   /api/tokens                 mint one (plaintext shown once)
DELETE /api/tokens/{id}            revoke one
```

Changes to accounts are recorded in the [audit trail](#audit-trail) like any
other change.

Deactivating an account (`PATCH {"is_active": false}`) is preferred over
deleting it: the register keeps referring to accounts that acted on it, and
reactivation is a single flag. The last active administrator cannot be demoted,
deactivated or deleted, so the system can never be left with no way in.

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
  approvals. The UI warns before doing it, and the audit trail records each
  record that went.
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

It also covers access control: that no `/api/` route answers an unauthenticated
caller — asserted by walking the route table, so a new endpoint added without
protection fails the suite — that each role is held to its own permissions, and
that sessions and tokens actually die when revoked, expired or deactivated.

The audit tests lean on the cases where a naive implementation quietly loses the
answer it exists to give: a record amended in a later request (is the previous
value still recorded?), a cascading delete (is each removed record recorded?), a
deleted account (does its history survive?), and a rejected change (does it
leave nothing behind?).

### Layout

```
app/
  audit.py        the trail: captures every change at flush time
  auth.py         who the caller is, and what their role permits
  security.py     password hashing, session ids and API token generation
  bootstrap.py    first-run administrator from the environment
  compliance.py   the engine: dates + status -> state, and the unified queue
  reference.py    controlled vocabularies (states, sections, licence types)
  models.py       ORM models; the validity envelope is a shared mixin
  schemas.py      request/response schemas; reads carry the derived verdict
  services.py     persistence helpers and cross-register aggregation
  routers/        one module per register, plus the dashboard
  static/         the bundled dashboard UI (no build step)
scripts/
  seed.py         sample portfolio, dated relative to today
  create_admin.py create or reset an administrator, interactively
tests/
```
