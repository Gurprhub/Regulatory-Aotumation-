"""State sale permission register endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app import models, schemas, services
from app.database import get_session
from app.reference import ComplianceState, ComplianceStatus, normalise_state

router = APIRouter(prefix="/api/sale-permissions", tags=["state sale permissions"])


@router.get("", response_model=list[schemas.SalePermissionRead])
def list_sale_permissions(
    session: Session = Depends(get_session),
    product_id: int | None = None,
    state: str | None = Query(default=None, description="State or union territory."),
    status_filter: ComplianceStatus | None = Query(default=None, alias="status"),
    compliance_state: list[ComplianceState] | None = Query(default=None),
    expiring_within: int | None = Query(default=None, ge=0, le=3650),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[models.StateSalePermission]:
    stmt = (
        select(models.StateSalePermission)
        .options(selectinload(models.StateSalePermission.product))
        .order_by(
            models.StateSalePermission.valid_until.is_(None),
            models.StateSalePermission.valid_until,
        )
    )
    if product_id is not None:
        stmt = stmt.where(models.StateSalePermission.product_id == product_id)
    if state is not None:
        stmt = stmt.where(models.StateSalePermission.state == normalise_state(state))
    if status_filter is not None:
        stmt = stmt.where(models.StateSalePermission.status == status_filter)

    records = services.filter_records_by_derived_state(
        list(session.scalars(stmt)),
        compliance_states=compliance_state,
        expiring_within=expiring_within,
    )
    return records[offset : offset + limit]


@router.get("/{permission_id}", response_model=schemas.SalePermissionRead)
def get_sale_permission(
    permission_id: int, session: Session = Depends(get_session)
) -> models.StateSalePermission:
    return services.get_or_404(
        session, models.StateSalePermission, permission_id, "State sale permission"
    )


@router.post(
    "", response_model=schemas.SalePermissionRead, status_code=status.HTTP_201_CREATED
)
def create_sale_permission(
    payload: schemas.SalePermissionCreate, session: Session = Depends(get_session)
) -> models.StateSalePermission:
    services.ensure_product_exists(session, payload.product_id)
    services.ensure_registration_exists(session, payload.registration_id)
    permission = models.StateSalePermission(**payload.model_dump())
    session.add(permission)
    services.commit_or_conflict(
        session,
        context=f"Sale permission {payload.permission_number!r} for {payload.state}",
    )
    session.refresh(permission)
    return permission


@router.patch("/{permission_id}", response_model=schemas.SalePermissionRead)
def update_sale_permission(
    permission_id: int,
    payload: schemas.SalePermissionUpdate,
    session: Session = Depends(get_session),
) -> models.StateSalePermission:
    permission = services.get_or_404(
        session, models.StateSalePermission, permission_id, "State sale permission"
    )
    updates = payload.model_dump(exclude_unset=True)
    services.ensure_product_exists(session, updates.get("product_id"))
    services.ensure_registration_exists(session, updates.get("registration_id"))
    services.apply_updates(permission, updates)
    services.validate_validity_window(permission)
    services.commit_or_conflict(session, context=f"Sale permission {permission_id}")
    session.refresh(permission)
    return permission


@router.delete("/{permission_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_sale_permission(
    permission_id: int, session: Session = Depends(get_session)
) -> Response:
    permission = services.get_or_404(
        session, models.StateSalePermission, permission_id, "State sale permission"
    )
    session.delete(permission)
    services.commit_or_conflict(session, context=f"Sale permission {permission_id}")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
