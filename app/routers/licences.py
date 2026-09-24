"""Licence register endpoints (manufacture, sale, stock, storage)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app import auth, models, schemas, services
from app.database import get_session
from app.reference import ComplianceState, ComplianceStatus, LicenceType, normalise_state

router = APIRouter(
    prefix="/api/licences",
    tags=["licences"],
    dependencies=[Depends(auth.require_viewer)],
)


@router.get("", response_model=list[schemas.LicenceRead])
def list_licences(
    session: Session = Depends(get_session),
    state: str | None = None,
    licence_type: LicenceType | None = None,
    product_id: int | None = Query(default=None, description="Licences covering this product."),
    status_filter: ComplianceStatus | None = Query(default=None, alias="status"),
    compliance_state: list[ComplianceState] | None = Query(default=None),
    expiring_within: int | None = Query(default=None, ge=0, le=3650),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[models.Licence]:
    stmt = (
        select(models.Licence)
        .options(selectinload(models.Licence.products))
        .order_by(models.Licence.valid_until.is_(None), models.Licence.valid_until)
    )
    if state is not None:
        stmt = stmt.where(models.Licence.state == normalise_state(state))
    if licence_type is not None:
        stmt = stmt.where(models.Licence.licence_type == licence_type)
    if status_filter is not None:
        stmt = stmt.where(models.Licence.status == status_filter)
    if product_id is not None:
        stmt = stmt.where(models.Licence.products.any(models.Product.id == product_id))

    records = services.filter_records_by_derived_state(
        list(session.scalars(stmt)),
        compliance_states=compliance_state,
        expiring_within=expiring_within,
    )
    return records[offset : offset + limit]


@router.get("/{licence_id}", response_model=schemas.LicenceRead)
def get_licence(licence_id: int, session: Session = Depends(get_session)) -> models.Licence:
    return services.get_or_404(session, models.Licence, licence_id, "Licence")


@router.post(
    "",
    response_model=schemas.LicenceRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(auth.require_editor)],
)
def create_licence(
    payload: schemas.LicenceCreate, session: Session = Depends(get_session)
) -> models.Licence:
    data = payload.model_dump()
    product_ids = data.pop("product_ids", [])
    licence = models.Licence(**data)
    licence.products = services.resolve_products(session, product_ids)
    session.add(licence)
    services.commit_or_conflict(
        session, context=f"Licence {payload.licence_number!r} in {payload.state}"
    )
    session.refresh(licence)
    return licence


@router.patch(
    "/{licence_id}",
    response_model=schemas.LicenceRead,
    dependencies=[Depends(auth.require_editor)],
)
def update_licence(
    licence_id: int,
    payload: schemas.LicenceUpdate,
    session: Session = Depends(get_session),
) -> models.Licence:
    licence = services.get_or_404(session, models.Licence, licence_id, "Licence")
    updates = payload.model_dump(exclude_unset=True)
    if "product_ids" in updates:
        product_ids = updates.pop("product_ids") or []
        licence.products = services.resolve_products(session, product_ids)
    services.apply_updates(licence, updates)
    services.validate_validity_window(licence)
    services.commit_or_conflict(session, context=f"Licence {licence_id}")
    session.refresh(licence)
    return licence


@router.delete(
    "/{licence_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(auth.require_editor)],
)
def delete_licence(licence_id: int, session: Session = Depends(get_session)) -> Response:
    licence = services.get_or_404(session, models.Licence, licence_id, "Licence")
    session.delete(licence)
    services.commit_or_conflict(session, context=f"Licence {licence_id}")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
