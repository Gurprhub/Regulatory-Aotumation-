"""The compliance engine: derives renewal health from dates and status.

Everything the dashboard and the alert feed show is computed here rather than
stored, so a record never goes stale simply because nobody re-saved it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.config import settings
from app.models import LabelApproval, Licence, Registration, StateSalePermission
from app.reference import ComplianceState, ComplianceStatus

#: Statuses that make an item unusable regardless of its dates.
_BLOCKING_STATUSES = frozenset(
    {
        ComplianceStatus.SUSPENDED,
        ComplianceStatus.CANCELLED,
        ComplianceStatus.SURRENDERED,
    }
)

#: Order in which states are triaged — lower sorts first (most urgent).
_STATE_PRIORITY: dict[ComplianceState, int] = {
    ComplianceState.NON_COMPLIANT: 0,
    ComplianceState.EXPIRED: 1,
    ComplianceState.CRITICAL: 2,
    ComplianceState.EXPIRING_SOON: 3,
    ComplianceState.NOT_YET_EFFECTIVE: 4,
    ComplianceState.VALID: 5,
}

#: States that need someone to act.
ACTIONABLE_STATES = frozenset(
    {
        ComplianceState.NON_COMPLIANT,
        ComplianceState.EXPIRED,
        ComplianceState.CRITICAL,
        ComplianceState.EXPIRING_SOON,
    }
)

REGISTER_LABELS: dict[str, str] = {
    "registration": "Registration",
    "sale_permission": "State sale permission",
    "licence": "Licence",
    "label_approval": "Label approval",
}


def days_remaining(valid_until: date | None, *, today: date | None = None) -> int | None:
    """Days until expiry — negative once expired, ``None`` if there is no expiry."""
    if valid_until is None:
        return None
    return (valid_until - (today or date.today())).days


def evaluate(
    valid_until: date | None,
    status: ComplianceStatus,
    *,
    valid_from: date | None = None,
    today: date | None = None,
    critical_days: int | None = None,
    warning_days: int | None = None,
) -> ComplianceState:
    """Derive the compliance state of a single validity envelope.

    ``UNDER_RENEWAL`` is deliberately *not* treated as blocking, nor as an
    extension: filing a renewal does not by itself keep a certificate valid, so
    an item under renewal still reports as expiring or expired on its own dates.
    """
    today = today or date.today()
    critical = settings.critical_days if critical_days is None else critical_days
    warning = settings.warning_days if warning_days is None else warning_days

    if status in _BLOCKING_STATUSES:
        return ComplianceState.NON_COMPLIANT
    if valid_from is not None and valid_from > today:
        return ComplianceState.NOT_YET_EFFECTIVE
    if valid_until is None:
        return ComplianceState.VALID

    remaining = (valid_until - today).days
    if remaining < 0:
        return ComplianceState.EXPIRED
    if remaining <= critical:
        return ComplianceState.CRITICAL
    if remaining <= warning:
        return ComplianceState.EXPIRING_SOON
    return ComplianceState.VALID


def state_priority(state: ComplianceState) -> int:
    """Sort key placing the most urgent state first."""
    return _STATE_PRIORITY[state]


@dataclass(frozen=True)
class ComplianceItem:
    """One row of the unified renewal queue, across all four registers."""

    register: str
    record_id: int
    title: str
    reference_number: str
    product_name: str | None
    state_name: str | None
    valid_from: date | None
    valid_until: date | None
    status: ComplianceStatus
    compliance_state: ComplianceState
    days_remaining: int | None

    @property
    def register_label(self) -> str:
        return REGISTER_LABELS.get(self.register, self.register)

    def sort_key(self) -> tuple[int, int, str]:
        # Items with no expiry sort last within their state bucket.
        remaining = self.days_remaining
        return (
            state_priority(self.compliance_state),
            remaining if remaining is not None else 10**6,
            self.reference_number,
        )


def _item(
    *,
    register: str,
    record_id: int,
    title: str,
    reference_number: str,
    product_name: str | None,
    state_name: str | None,
    valid_from: date | None,
    valid_until: date | None,
    status: ComplianceStatus,
    today: date,
) -> ComplianceItem:
    return ComplianceItem(
        register=register,
        record_id=record_id,
        title=title,
        reference_number=reference_number,
        product_name=product_name,
        state_name=state_name,
        valid_from=valid_from,
        valid_until=valid_until,
        status=status,
        compliance_state=evaluate(
            valid_until, status, valid_from=valid_from, today=today
        ),
        days_remaining=days_remaining(valid_until, today=today),
    )


def from_registration(record: Registration, *, today: date | None = None) -> ComplianceItem:
    today = today or date.today()
    product = record.product.name if record.product else None
    return _item(
        register="registration",
        record_id=record.id,
        title=f"{product or 'Product'} — {record.section.value} registration",
        reference_number=record.registration_number,
        product_name=product,
        state_name=None,
        valid_from=record.valid_from,
        valid_until=record.valid_until,
        status=record.status,
        today=today,
    )


def from_sale_permission(
    record: StateSalePermission, *, today: date | None = None
) -> ComplianceItem:
    today = today or date.today()
    product = record.product.name if record.product else None
    return _item(
        register="sale_permission",
        record_id=record.id,
        title=f"{product or 'Product'} — sale permission, {record.state}",
        reference_number=record.permission_number,
        product_name=product,
        state_name=record.state,
        valid_from=record.valid_from,
        valid_until=record.valid_until,
        status=record.status,
        today=today,
    )


def from_licence(record: Licence, *, today: date | None = None) -> ComplianceItem:
    today = today or date.today()
    descriptor = record.site_name or record.holder_name
    return _item(
        register="licence",
        record_id=record.id,
        title=f"{descriptor} — {record.licence_type.value.replace('_', ' ')} licence, {record.state}",
        reference_number=record.licence_number,
        product_name=None,
        state_name=record.state,
        valid_from=record.valid_from,
        valid_until=record.valid_until,
        status=record.status,
        today=today,
    )


def from_label_approval(record: LabelApproval, *, today: date | None = None) -> ComplianceItem:
    today = today or date.today()
    product = record.product.name if record.product else None
    return _item(
        register="label_approval",
        record_id=record.id,
        title=f"{product or 'Product'} — label v{record.label_version}",
        reference_number=record.approval_number,
        product_name=product,
        state_name=None,
        valid_from=record.valid_from,
        valid_until=record.valid_until,
        status=record.status,
        today=today,
    )
