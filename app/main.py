"""FastAPI application: REST API plus the bundled dashboard UI."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.config import settings
from app.database import init_db
from app.bootstrap import bootstrap_admin
from app.routers import (
    audit,
    auth,
    dashboard,
    label_approvals,
    licences,
    products,
    registrations,
    sale_permissions,
    tokens,
    users,
)

STATIC_DIR = Path(__file__).parent / "static"

DESCRIPTION = """
Track agrochemical regulatory compliance in one place:

* **Registrations** — CIB&RC certificates held per product, by section of the
  Insecticides Act.
* **State sale permissions** — permission to sell a registered product in a
  given state or union territory.
* **Licences** — state licences to manufacture, sell, stock or store.
* **Label approvals** — approved label and leaflet versions.

Every record carries a derived `compliance_state` and `days_remaining`, computed
from its dates on each read, and `/api/alerts` merges all four registers into a
single renewal queue ordered by urgency.

## Access

Every endpoint below requires a signed-in account. Authenticate either with a
session cookie (`POST /api/auth/login`, used by the dashboard) or with a bearer
token minted at `POST /api/tokens`:

    Authorization: Bearer rat_...

Roles are cumulative: **viewer** reads every register, **editor** also creates,
amends and deletes records, **admin** also manages accounts.
"""


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    bootstrap_admin()
    yield


app = FastAPI(
    title="Regulatory Automation",
    description=DESCRIPTION,
    version=__version__,
    lifespan=lifespan,
)

for router in (
    auth.router,
    users.router,
    audit.router,
    tokens.router,
    products.router,
    registrations.router,
    sale_permissions.router,
    licences.router,
    label_approvals.router,
    dashboard.router,
):
    app.include_router(router)


@app.get("/health", tags=["meta"])
def health() -> dict[str, object]:
    """Liveness probe, also echoing the configured alert windows."""
    return {
        "status": "ok",
        "version": __version__,
        "critical_days": settings.critical_days,
        "warning_days": settings.warning_days,
    }


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    """The dashboard shell.

    The page itself is public; it holds no data. Everything it renders comes
    from the API, which redirects the browser here to /login on a 401.
    """
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/login", include_in_schema=False)
def login_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "login.html")
