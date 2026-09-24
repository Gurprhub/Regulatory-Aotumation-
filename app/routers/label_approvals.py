"""Label and leaflet approval register endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app import auth, models, schemas, services
from app.database import get_session
from app.reference import ComplianceState, ComplianceStatus

router = APIRouter(
    prefix="/api/label-approvals",
    tags=["label approvals"],
    dependencies=[Depends(auth.require_viewer)],
)


@router.get("", response_model=list[schemas.LabelApprovalRead])
def list_label_approvals(
    session: Session = Depends(get_session),
    product_id: int | None = None,
    registration_id: int | None = None,
    status_filter: ComplianceStatus | None = Query(default=None, alias="status"),
    compliance_state: list[ComplianceState] | None = Query(default=None),
    expiring_within: int | None = Query(default=None, ge=0, le=3650),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[models.LabelApproval]:
    stmt = (
        select(models.LabelApproval)
        .options(selectinload(models.LabelApproval.product))
        .order_by(
            models.LabelApproval.valid_until.is_(None), models.LabelApproval.valid_until
        )
    )
    if product_id is not None:
        stmt = stmt.where(models.LabelApproval.product_id == product_id)
    if registration_id is not None:
        stmt = stmt.where(models.LabelApproval.registration_id == registration_id)
    if status_filter is not None:
        stmt = stmt.where(models.LabelApproval.status == status_filter)

    records = services.filter_records_by_derived_state(
        list(session.scalars(stmt)),
        compliance_states=compliance_state,
        expiring_within=expiring_within,
    )
    return records[offset : offset + limit]


@router.get("/{approval_id}", response_model=schemas.LabelApprovalRead)
def get_label_approval(
    approval_id: int, session: Session = Depends(get_session)
) -> models.LabelApproval:
    return services.get_or_404(
        session, models.LabelApproval, approval_id, "Label approval"
    )


@router.post(
    "", response_model=schemas.LabelApprovalRead, status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(auth.require_editor)],
)
def create_label_approval(
    payload: schemas.LabelApprovalCreate, session: Session = Depends(get_session)
) -> models.LabelApproval:
    services.ensure_product_exists(session, payload.product_id)
    services.ensure_registration_exists(session, payload.registration_id)
    approval = models.LabelApproval(**payload.model_dump())
    session.add(approval)
    services.commit_or_conflict(
        session,
        context=(
            f"Label approval {payload.approval_number!r} v{payload.label_version}"
        ),
    )
    session.refresh(approval)
    return approval


@router.patch(
    "/{approval_id}",
    response_model=schemas.LabelApprovalRead,
    dependencies=[Depends(auth.require_editor)],
)
def update_label_approval(
    approval_id: int,
    payload: schemas.LabelApprovalUpdate,
    session: Session = Depends(get_session),
) -> models.LabelApproval:
    approval = services.get_or_404(
        session, models.LabelApproval, approval_id, "Label approval"
    )
    updates = payload.model_dump(exclude_unset=True)
    services.ensure_product_exists(session, updates.get("product_id"))
    services.ensure_registration_exists(session, updates.get("registration_id"))
    services.apply_updates(approval, updates)
    services.validate_validity_window(approval)
    services.commit_or_conflict(session, context=f"Label approval {approval_id}")
    session.refresh(approval)
    return approval


@router.delete(
    "/{approval_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(auth.require_editor)],
)
def delete_label_approval(
    approval_id: int, session: Session = Depends(get_session)
) -> Response:
    approval = services.get_or_404(
        session, models.LabelApproval, approval_id, "Label approval"
    )
    session.delete(approval)
    services.commit_or_conflict(session, context=f"Label approval {approval_id}")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
