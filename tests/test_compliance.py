"""Unit tests for the compliance engine."""

from __future__ import annotations

from datetime import date

import pytest

from app.compliance import ComplianceItem, days_remaining, evaluate, state_priority
from app.reference import ComplianceState, ComplianceStatus

TODAY = date(2026, 6, 15)


@pytest.mark.parametrize(
    ("valid_until", "expected"),
    [
        (None, ComplianceState.VALID),
        (date(2026, 6, 14), ComplianceState.EXPIRED),
        (date(2026, 6, 15), ComplianceState.CRITICAL),  # expires today
        (date(2026, 7, 15), ComplianceState.CRITICAL),  # exactly 30 days
        (date(2026, 7, 16), ComplianceState.EXPIRING_SOON),  # 31 days
        (date(2026, 9, 13), ComplianceState.EXPIRING_SOON),  # exactly 90 days
        (date(2026, 9, 14), ComplianceState.VALID),  # 91 days
    ],
)
def test_state_boundaries(valid_until: date | None, expected: ComplianceState) -> None:
    assert evaluate(valid_until, ComplianceStatus.ACTIVE, today=TODAY) is expected


@pytest.mark.parametrize(
    "status",
    [ComplianceStatus.SUSPENDED, ComplianceStatus.CANCELLED, ComplianceStatus.SURRENDERED],
)
def test_blocking_statuses_are_non_compliant(status: ComplianceStatus) -> None:
    """A suspended certificate is unusable however far away its expiry is."""
    assert (
        evaluate(date(2030, 1, 1), status, today=TODAY) is ComplianceState.NON_COMPLIANT
    )


def test_under_renewal_does_not_extend_validity() -> None:
    """Filing a renewal does not keep a certificate valid on its own."""
    assert (
        evaluate(date(2026, 6, 1), ComplianceStatus.UNDER_RENEWAL, today=TODAY)
        is ComplianceState.EXPIRED
    )


def test_future_validity_is_not_yet_effective() -> None:
    assert (
        evaluate(
            date(2027, 1, 1),
            ComplianceStatus.ACTIVE,
            valid_from=date(2026, 8, 1),
            today=TODAY,
        )
        is ComplianceState.NOT_YET_EFFECTIVE
    )


def test_custom_thresholds_are_honoured() -> None:
    assert (
        evaluate(
            date(2026, 8, 1),
            ComplianceStatus.ACTIVE,
            today=TODAY,
            critical_days=60,
            warning_days=120,
        )
        is ComplianceState.CRITICAL
    )


def test_days_remaining() -> None:
    assert days_remaining(None, today=TODAY) is None
    assert days_remaining(date(2026, 6, 25), today=TODAY) == 10
    assert days_remaining(date(2026, 6, 5), today=TODAY) == -10


def test_urgency_ordering() -> None:
    order = [
        ComplianceState.NON_COMPLIANT,
        ComplianceState.EXPIRED,
        ComplianceState.CRITICAL,
        ComplianceState.EXPIRING_SOON,
        ComplianceState.NOT_YET_EFFECTIVE,
        ComplianceState.VALID,
    ]
    assert [state_priority(state) for state in order] == sorted(
        state_priority(state) for state in order
    )


def _item(state: ComplianceState, remaining: int | None, ref: str) -> ComplianceItem:
    return ComplianceItem(
        register="registration",
        record_id=1,
        title="t",
        reference_number=ref,
        product_name=None,
        state_name=None,
        valid_from=None,
        valid_until=None,
        status=ComplianceStatus.ACTIVE,
        compliance_state=state,
        days_remaining=remaining,
    )


def test_items_without_expiry_sort_after_dated_ones() -> None:
    dated = _item(ComplianceState.VALID, 500, "B")
    perpetual = _item(ComplianceState.VALID, None, "A")
    assert sorted([perpetual, dated], key=lambda i: i.sort_key()) == [dated, perpetual]
