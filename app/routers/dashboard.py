"""Dashboard, renewal alert feed, CSV export and reference vocabularies."""

from __future__ import annotations

import csv
import io
from datetime import date

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app import audit, auth, compliance, schemas, services
from app.config import settings
from app.database import get_session
from app.reference import (
    INDIAN_STATES,
    ComplianceState,
    ComplianceStatus,
    FormulationType,
    LicenceType,
    ProductCategory,
    RegistrationPurpose,
    RegistrationSection,
)

router = APIRouter(
    prefix="/api",
    tags=["dashboard"],
    dependencies=[Depends(auth.require_viewer)],
)

CSV_COLUMNS = (
    "register",
    "reference_number",
    "title",
    "product_name",
    "state",
    "valid_from",
    "valid_until",
    "days_remaining",
    "status",
    "compliance_state",
)


@router.get("/dashboard", response_model=schemas.DashboardSummary)
def dashboard(
    session: Session = Depends(get_session),
    upcoming_limit: int = Query(default=10, ge=1, le=100),
) -> dict:
    """Headline counts per register and compliance state, plus the renewal queue."""
    return services.summarise(session, upcoming_limit=upcoming_limit)


@router.get("/alerts", response_model=list[schemas.ComplianceItemRead])
def alerts(
    session: Session = Depends(get_session),
    within_days: int | None = Query(
        default=None,
        ge=0,
        le=3650,
        description=f"Renewal horizon in days (defaults to WARNING_DAYS={settings.warning_days}).",
    ),
    register: list[str] | None = Query(
        default=None, description="Restrict to one or more registers."
    ),
    state: list[str] | None = Query(default=None, description="Restrict to states."),
) -> list[dict]:
    """Everything needing action across all four registers, most urgent first."""
    items = services.alert_queue(
        session, within_days=within_days, registers=register, state_names=state
    )
    return [{**item.__dict__, "register_label": item.register_label} for item in items]


@router.get(
    "/alerts.csv",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/csv": {}}}},
)
def alerts_csv(
    session: Session = Depends(get_session),
    within_days: int | None = Query(default=None, ge=0, le=3650),
    register: list[str] | None = Query(default=None),
    state: list[str] | None = Query(default=None),
) -> StreamingResponse:
    """The same renewal queue as a CSV, for circulation outside the tool."""
    items = services.alert_queue(
        session, within_days=within_days, registers=register, state_names=state
    )

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CSV_COLUMNS)
    for item in items:
        writer.writerow(
            [
                item.register_label,
                item.reference_number,
                item.title,
                item.product_name or "",
                item.state_name or "",
                item.valid_from.isoformat() if item.valid_from else "",
                item.valid_until.isoformat() if item.valid_until else "",
                "" if item.days_remaining is None else item.days_remaining,
                item.status.value,
                item.compliance_state.value,
            ]
        )
    buffer.seek(0)
    filename = f"renewal-queue-{date.today().isoformat()}.csv"
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/reference")
def reference() -> dict[str, object]:
    """Controlled vocabularies, so the UI never hard-codes them."""
    return {
        "states": list(INDIAN_STATES),
        "product_categories": [member.value for member in ProductCategory],
        "formulation_types": [member.value for member in FormulationType],
        "registration_sections": [member.value for member in RegistrationSection],
        "registration_purposes": [member.value for member in RegistrationPurpose],
        "licence_types": [member.value for member in LicenceType],
        "compliance_statuses": [member.value for member in ComplianceStatus],
        "compliance_states": [member.value for member in ComplianceState],
        "registers": [
            {"key": key, "label": compliance.REGISTER_LABELS[key]}
            for key in services.REGISTER_KEYS
        ],
        "audit_entity_types": sorted(set(audit.AUDITED.values())),
        "thresholds": {
            "critical_days": settings.critical_days,
            "warning_days": settings.warning_days,
        },
    }
