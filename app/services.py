"""Persistence helpers and the cross-register aggregation used by the dashboard."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from datetime import date
from typing import Any, TypeVar

from fastapi import HTTPException, status as http_status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app import compliance, models
from app.config import settings
from app.reference import ComplianceState

ModelT = TypeVar("ModelT", bound=models.Base)

#: register key -> (model, eager-load options, item factory)
_REGISTERS: dict[str, tuple[type[models.Base], tuple[Any, ...], Callable[..., compliance.ComplianceItem]]] = {
    "registration": (
        models.Registration,
        (selectinload(models.Registration.product),),
        compliance.from_registration,
    ),
    "sale_permission": (
        models.StateSalePermission,
        (selectinload(models.StateSalePermission.product),),
        compliance.from_sale_permission,
    ),
    "licence": (models.Licence, (), compliance.from_licence),
    "label_approval": (
        models.LabelApproval,
        (selectinload(models.LabelApproval.product),),
        compliance.from_label_approval,
    ),
}

REGISTER_KEYS: tuple[str, ...] = tuple(_REGISTERS)


# --------------------------------------------------------------------------- #
# Generic CRUD
# --------------------------------------------------------------------------- #
def get_or_404(session: Session, model: type[ModelT], record_id: int, label: str) -> ModelT:
    record = session.get(model, record_id)
    if record is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"{label} {record_id} was not found",
        )
    return record


def commit_or_conflict(session: Session, *, context: str) -> None:
    """Commit, translating a constraint violation into HTTP 409."""
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail=f"{context} conflicts with an existing record or a missing reference.",
        ) from exc


def ensure_product_exists(session: Session, product_id: int | None) -> None:
    if product_id is not None:
        get_or_404(session, models.Product, product_id, "Product")


def ensure_registration_exists(session: Session, registration_id: int | None) -> None:
    if registration_id is not None:
        get_or_404(session, models.Registration, registration_id, "Registration")


def resolve_products(session: Session, product_ids: Iterable[int]) -> list[models.Product]:
    """Load every product id, rejecting the request if any is unknown."""
    resolved: list[models.Product] = []
    for product_id in dict.fromkeys(product_ids):  # de-duplicate, keep order
        resolved.append(get_or_404(session, models.Product, product_id, "Product"))
    return resolved


def apply_updates(record: models.Base, payload: dict[str, Any]) -> None:
    for field, value in payload.items():
        setattr(record, field, value)


def validate_validity_window(record: Any) -> None:
    """Re-check the validity window after a partial update."""
    valid_from = getattr(record, "valid_from", None)
    valid_until = getattr(record, "valid_until", None)
    if valid_from is not None and valid_until is not None and valid_until < valid_from:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="valid_until must not be earlier than valid_from",
        )


# --------------------------------------------------------------------------- #
# Cross-register aggregation
# --------------------------------------------------------------------------- #
def collect_items(
    session: Session,
    *,
    today: date | None = None,
    registers: Sequence[str] | None = None,
) -> list[compliance.ComplianceItem]:
    """Build the unified compliance view across the requested registers.

    Compliance state depends on ``date.today()``, so it cannot be filtered in
    SQL; rows are loaded with their product eagerly joined and evaluated in
    Python. The registers hold thousands of rows at most, which this handles
    comfortably.
    """
    today = today or date.today()
    keys = tuple(registers) if registers else REGISTER_KEYS
    unknown = set(keys) - set(REGISTER_KEYS)
    if unknown:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown register(s): {', '.join(sorted(unknown))}",
        )

    items: list[compliance.ComplianceItem] = []
    for key in keys:
        model, options, to_item = _REGISTERS[key]
        stmt = select(model)
        if options:
            stmt = stmt.options(*options)
        for record in session.scalars(stmt):
            items.append(to_item(record, today=today))
    items.sort(key=lambda item: item.sort_key())
    return items


def filter_items(
    items: Iterable[compliance.ComplianceItem],
    *,
    within_days: int | None = None,
    states: Sequence[ComplianceState] | None = None,
    state_names: Sequence[str] | None = None,
    include_expired: bool = True,
) -> list[compliance.ComplianceItem]:
    """Narrow the unified view down to what the caller asked for."""
    wanted_states = set(states) if states else None
    wanted_state_names = {name.casefold() for name in state_names} if state_names else None

    selected: list[compliance.ComplianceItem] = []
    for item in items:
        if wanted_states is not None and item.compliance_state not in wanted_states:
            continue
        if (
            wanted_state_names is not None
            and (item.state_name or "").casefold() not in wanted_state_names
        ):
            continue
        if not include_expired and item.compliance_state in {
            ComplianceState.EXPIRED,
            ComplianceState.NON_COMPLIANT,
        }:
            continue
        if within_days is not None:
            remaining = item.days_remaining
            # Blocking statuses have no meaningful countdown but always matter.
            if item.compliance_state is ComplianceState.NON_COMPLIANT:
                selected.append(item)
                continue
            if remaining is None or remaining > within_days:
                continue
        selected.append(item)
    return selected


def alert_queue(
    session: Session,
    *,
    within_days: int | None = None,
    today: date | None = None,
    registers: Sequence[str] | None = None,
    state_names: Sequence[str] | None = None,
) -> list[compliance.ComplianceItem]:
    """Everything that needs action, most urgent first."""
    horizon = settings.warning_days if within_days is None else within_days
    items = collect_items(session, today=today, registers=registers)
    return filter_items(
        filter_items(items, state_names=state_names),
        within_days=horizon,
        states=sorted(compliance.ACTIONABLE_STATES, key=lambda s: s.value),
    )


def summarise(
    session: Session,
    *,
    today: date | None = None,
    upcoming_limit: int = 10,
) -> dict[str, Any]:
    """Counts by register and compliance state, plus the top of the renewal queue."""
    today = today or date.today()
    items = collect_items(session, today=today)

    totals: Counter[str] = Counter()
    for item in items:
        totals[item.compliance_state.value] += 1
    for state in ComplianceState:
        totals.setdefault(state.value, 0)

    registers: list[dict[str, Any]] = []
    for key in REGISTER_KEYS:
        register_items = [item for item in items if item.register == key]
        by_state: Counter[str] = Counter(
            item.compliance_state.value for item in register_items
        )
        for state in ComplianceState:
            by_state.setdefault(state.value, 0)
        registers.append(
            {
                "register": key,
                "register_label": compliance.REGISTER_LABELS[key],
                "total": len(register_items),
                "by_state": dict(by_state),
                "actionable": sum(
                    1
                    for item in register_items
                    if item.compliance_state in compliance.ACTIONABLE_STATES
                ),
            }
        )

    upcoming = filter_items(
        items,
        within_days=settings.warning_days,
        states=sorted(compliance.ACTIONABLE_STATES, key=lambda s: s.value),
    )[:upcoming_limit]

    return {
        "generated_on": today,
        "critical_days": settings.critical_days,
        "warning_days": settings.warning_days,
        "totals": dict(totals),
        "registers": registers,
        "upcoming": [
            {**item.__dict__, "register_label": item.register_label} for item in upcoming
        ],
    }


def filter_records_by_derived_state(
    records: Sequence[Any],
    *,
    compliance_states: Sequence[ComplianceState] | None = None,
    expiring_within: int | None = None,
    today: date | None = None,
) -> list[Any]:
    """Filter ORM rows on their derived compliance state.

    Used by the per-register list endpoints so that ``?compliance_state=critical``
    and ``?expiring_within=45`` behave the same way there as on the alert feed.
    """
    if compliance_states is None and expiring_within is None:
        return list(records)

    today = today or date.today()
    wanted = set(compliance_states) if compliance_states else None
    kept: list[Any] = []
    for record in records:
        state = compliance.evaluate(
            record.valid_until,
            record.status,
            valid_from=record.valid_from,
            today=today,
        )
        if wanted is not None and state not in wanted:
            continue
        if expiring_within is not None:
            if state is ComplianceState.NON_COMPLIANT:
                kept.append(record)
                continue
            remaining = compliance.days_remaining(record.valid_until, today=today)
            if remaining is None or remaining > expiring_within:
                continue
        kept.append(record)
    return kept
