"""CIB&RC registration register endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app import models, schemas, services
from app.database import get_session
from app.reference import (
    ComplianceState,
    ComplianceStatus,
    RegistrationPurpose,
    RegistrationSection,
)

router = APIRouter(prefix="/api/registrations", tags=["registrations"])


@router.get("", response_model=list[schemas.RegistrationRead])
def list_registrations(
    session: Session = Depends(get_session),
    product_id: int | None = None,
    section: RegistrationSection | None = None,
    purpose: RegistrationPurpose | None = None,
    status_filter: ComplianceStatus | None = Query(default=None, alias="status"),
    compliance_state: list[ComplianceState] | None = Query(default=None),
    expiring_within: int | None = Query(default=None, ge=0, le=3650),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[models.Registration]:
    stmt = (
        select(models.Registration)
        .options(selectinload(models.Registration.product))
        .order_by(models.Registration.valid_until.is_(None), models.Registration.valid_until)
    )
    if product_id is not None:
        stmt = stmt.where(models.Registration.product_id == product_id)
    if section is not None:
        stmt = stmt.where(models.Registration.section == section)
    if purpose is not None:
        stmt = stmt.where(models.Registration.purpose == purpose)
    if status_filter is not None:
        stmt = stmt.where(models.Registration.status == status_filter)

    records = services.filter_records_by_derived_state(
        list(session.scalars(stmt)),
        compliance_states=compliance_state,
        expiring_within=expiring_within,
    )
    return records[offset : offset + limit]


@router.get("/{registration_id}", response_model=schemas.RegistrationRead)
def get_registration(
    registration_id: int, session: Session = Depends(get_session)
) -> models.Registration:
    return services.get_or_404(
        session, models.Registration, registration_id, "Registration"
    )


@router.post(
    "", response_model=schemas.RegistrationRead, status_code=status.HTTP_201_CREATED
)
def create_registration(
    payload: schemas.RegistrationCreate, session: Session = Depends(get_session)
) -> models.Registration:
    services.ensure_product_exists(session, payload.product_id)
    registration = models.Registration(**payload.model_dump())
    session.add(registration)
    services.commit_or_conflict(
        session, context=f"Registration {payload.registration_number!r}"
    )
    session.refresh(registration)
    return registration


@router.patch("/{registration_id}", response_model=schemas.RegistrationRead)
def update_registration(
    registration_id: int,
    payload: schemas.RegistrationUpdate,
    session: Session = Depends(get_session),
) -> models.Registration:
    registration = services.get_or_404(
        session, models.Registration, registration_id, "Registration"
    )
    updates = payload.model_dump(exclude_unset=True)
    services.ensure_product_exists(session, updates.get("product_id"))
    services.apply_updates(registration, updates)
    services.validate_validity_window(registration)
    services.commit_or_conflict(session, context=f"Registration {registration_id}")
    session.refresh(registration)
    return registration


@router.delete("/{registration_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_registration(
    registration_id: int, session: Session = Depends(get_session)
) -> Response:
    registration = services.get_or_404(
        session, models.Registration, registration_id, "Registration"
    )
    session.delete(registration)
    services.commit_or_conflict(session, context=f"Registration {registration_id}")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
