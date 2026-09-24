"""The audit trail: who changed what, and when.

Capture hangs off SQLAlchemy's ``before_flush`` rather than living in each
endpoint. That way a change is recorded because it reached the database, not
because someone remembered to log it — an endpoint added later is audited
without being told to, and a change made from a script or the shell is recorded
on the same terms as one made from the dashboard.

Events are written in the same transaction as the change they describe, so the
two commit or roll back together: there is no window in which a record is
amended but the trail does not say so.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import event, insert, inspect, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import get_history
from sqlalchemy.orm.base import PASSIVE_OFF

from app.models import (
    ApiToken,
    AuditEvent,
    LabelApproval,
    Licence,
    Product,
    Registration,
    StateSalePermission,
    User,
)

CREATE = "create"
UPDATE = "update"
DELETE = "delete"

#: Models whose changes are recorded, and the name the trail calls them.
#:
#: ``UserSession`` is deliberately absent: it changes on every request and
#: records nothing a reader of a compliance trail wants. ``AuditEvent`` is
#: absent because the trail does not audit itself.
AUDITED: dict[type, str] = {
    Product: "product",
    Registration: "registration",
    StateSalePermission: "sale_permission",
    Licence: "licence",
    LabelApproval: "label_approval",
    User: "user",
    ApiToken: "api_token",
}

#: Never recorded: bookkeeping the application maintains by itself. Without
#: this, every sign-in would append a "user changed" event for last_login_at.
IGNORED_FIELDS = frozenset(
    {
        "created_at",
        "updated_at",
        "last_login_at",
        "failed_login_count",
        "locked_until",
        "last_used_at",
    }
)

#: Recorded as having changed, but never with their values.
REDACTED_FIELDS = frozenset({"password_hash", "token_hash", "lookup", "session_hash"})
REDACTED = "[redacted]"

#: Many-to-many links worth recording alongside a model's own columns.
COLLECTION_FIELDS: dict[type, tuple[str, ...]] = {Licence: ("products",)}

#: Used when a change arrives with no signed-in user — a migration, a shell
#: session, or the first-run bootstrap.
SYSTEM_ACTOR = {"id": None, "email": "system", "name": "System"}


def set_actor(session: Session, user: User | None) -> None:
    """Record who is responsible for the writes made through this session."""
    session.info["audit_actor"] = (
        SYSTEM_ACTOR
        if user is None
        else {"id": user.id, "email": user.email, "name": user.full_name}
    )


def _actor(session: Session) -> dict:
    return session.info.get("audit_actor") or SYSTEM_ACTOR


def _jsonable(value: Any) -> Any:
    """Reduce a column value to something JSON can hold."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def describe(instance: Any) -> str:
    """A label that still means something once the record itself is gone."""
    for attribute in (
        "registration_number",
        "permission_number",
        "licence_number",
        "approval_number",
        "email",
        "name",
    ):
        value = getattr(instance, attribute, None)
        if value:
            label = str(value)
            break
    else:
        label = f"{type(instance).__name__} {getattr(instance, 'id', '?')}"

    # A bare number means little in a trail read months later; add the product.
    product = getattr(instance, "product", None)
    product_name = getattr(product, "name", None)
    if product_name and product_name != label:
        label = f"{label} ({product_name})"
    state = getattr(instance, "state", None)
    if isinstance(state, str) and state:
        label = f"{label} — {state}"
    return label[:300]


def _history(instance: Any, field: str):
    """Pending change for ``field``, as SQLAlchemy sees it in memory."""
    return get_history(instance, field, passive=PASSIVE_OFF)


def _persisted_row(session: Session, instance: Any) -> dict[str, Any]:
    """The row as the database still holds it, read inside this transaction.

    SQLAlchemy only reports a previous value when the attribute happened to be
    loaded before it was changed; an instance expired by an earlier commit has
    no such memory, and the trail would record a change *to* a value *from*
    nothing — the half of an audit trail nobody needs. The pre-image is still in
    the database at ``before_flush``, so it is read from there. The identity map
    is bypassed deliberately: it holds the new values, not the old ones.
    """
    state = inspect(instance)
    identity = state.identity
    if identity is None:  # not yet persisted
        return {}

    table = state.mapper.local_table
    primary_key = list(table.primary_key.columns)
    if len(primary_key) != len(identity):
        return {}

    stmt = select(table).where(
        *[column == value for column, value in zip(primary_key, identity)]
    )
    row = session.connection().execute(stmt).mappings().first()
    return dict(row) if row is not None else {}


def _column_changes(session: Session, instance: Any) -> dict[str, dict[str, Any]]:
    """Field-level before/after for a modified instance."""
    state = inspect(instance)
    changes: dict[str, dict[str, Any]] = {}
    persisted: dict[str, Any] | None = None

    for attribute in state.mapper.column_attrs:
        field = attribute.key
        if field in IGNORED_FIELDS:
            continue
        history = _history(instance, field)
        if not history.has_changes():
            continue

        if history.deleted:
            before = history.deleted[0]
        else:
            # Read the pre-image once, and only when something needs it.
            if persisted is None:
                persisted = _persisted_row(session, instance)
            before = persisted.get(attribute.expression.name)
        after = history.added[0] if history.added else None
        if field in REDACTED_FIELDS:
            changes[field] = {"from": REDACTED, "to": REDACTED}
        else:
            changes[field] = {"from": _jsonable(before), "to": _jsonable(after)}

    for field in COLLECTION_FIELDS.get(type(instance), ()):
        history = _history(instance, field)
        if not history.has_changes():
            continue
        changes[field] = {
            "added": sorted(item.id for item in history.added if item.id is not None),
            "removed": sorted(
                item.id for item in history.deleted if item.id is not None
            ),
        }

    return changes


def _initial_values(instance: Any) -> dict[str, dict[str, Any]]:
    """The values a newly created record starts with."""
    values: dict[str, dict[str, Any]] = {}
    state = inspect(instance)
    for attribute in state.mapper.column_attrs:
        field = attribute.key
        if field in IGNORED_FIELDS:
            continue
        value = getattr(instance, field, None)
        if value is None:
            continue
        values[field] = {
            "from": None,
            "to": REDACTED if field in REDACTED_FIELDS else _jsonable(value),
        }
    return values


@dataclass
class _Pending:
    """An event captured before the flush, written out after it."""

    instance: Any
    action: str
    entity_type: str
    entity_label: str
    changes: dict


def collect(session: Session) -> list[_Pending]:
    """Capture what this flush is about to do.

    This has to run *before* the flush: the pre-image of an updated row is only
    available while the UPDATE has yet to be sent, and a deleted instance is
    only readable while it is still there to describe.
    """
    pending: list[_Pending] = []

    def capture(instance: Any, action: str, changes: dict) -> None:
        pending.append(
            _Pending(
                instance=instance,
                action=action,
                entity_type=AUDITED[type(instance)],
                entity_label=describe(instance),
                changes=changes,
            )
        )

    for instance in session.new:
        if type(instance) in AUDITED:
            capture(instance, CREATE, _initial_values(instance))

    for instance in session.dirty:
        if type(instance) not in AUDITED:
            continue
        changes = _column_changes(session, instance)
        if not changes:
            # Touched but not actually altered, or altered only in ignored
            # bookkeeping fields — nothing a reader would want to see.
            continue
        capture(instance, UPDATE, changes)

    for instance in session.deleted:
        if type(instance) in AUDITED:
            capture(instance, DELETE, {})

    return pending


@event.listens_for(Session, "before_flush")
def _capture_changes(session: Session, _flush_context, _instances) -> None:
    if session.info.get("audit_disabled"):
        return
    session.info.setdefault("audit_pending", []).extend(collect(session))


@event.listens_for(Session, "after_flush")
def _write_events(session: Session, _flush_context) -> None:
    """Write the captured events, now that created rows have their ids.

    A created row has no primary key until the flush assigns one, so the events
    are written here rather than in ``before_flush`` — otherwise every "created"
    entry would point at nothing and a record's history would not include its
    own beginning.

    These go in as a Core INSERT rather than ORM objects: the unit of work has
    already been planned for this flush, and a Core statement joins the same
    transaction immediately without asking for another pass.
    """
    pending: list[_Pending] = session.info.pop("audit_pending", [])
    if not pending:
        return

    actor = _actor(session)
    now = datetime.now(timezone.utc)
    rows = [
        {
            "occurred_at": now,
            "actor_id": actor["id"],
            "actor_email": actor["email"],
            "actor_name": actor["name"],
            "action": item.action,
            "entity_type": item.entity_type,
            "entity_id": getattr(item.instance, "id", None),
            "entity_label": item.entity_label,
            "changes": item.changes,
        }
        for item in pending
    ]
    session.execute(insert(AuditEvent.__table__), rows)
