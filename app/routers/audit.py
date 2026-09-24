"""Reading the audit trail.

The trail is append-only: there is no endpoint here that writes, amends or
deletes an event, and events are only ever created by the flush listener in
``app.audit``, inside the same transaction as the change they describe.
"""

from __future__ import annotations

import csv
import io
from datetime import date, datetime, time, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import auth, schemas
from app.audit import AUDITED
from app.database import get_session
from app.models import AuditEvent, User
from app.reference import Role

router = APIRouter(prefix="/api/audit", tags=["audit trail"])

#: Events about accounts and credentials, rather than about the registers.
ACCOUNT_ENTITIES = frozenset({"user", "api_token"})

ENTITY_TYPES = sorted(set(AUDITED.values()))

CSV_COLUMNS = (
    "occurred_at",
    "actor_email",
    "actor_name",
    "action",
    "entity_type",
    "entity_id",
    "entity_label",
    "changes",
)


def _visible(stmt, user: User, entity_type: str | None):
    """Restrict account events to admins.

    Everyone signed in may read the register trail — that is the point of
    keeping one. Who changed whose password or minted which token is account
    administration, and stays with the admins.
    """
    if user.role is Role.ADMIN:
        return stmt
    if entity_type in ACCOUNT_ENTITIES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Reading the account trail needs the admin role.",
        )
    return stmt.where(AuditEvent.entity_type.notin_(ACCOUNT_ENTITIES))


def _query(
    db: Session,
    user: User,
    *,
    entity_type: str | None,
    entity_id: int | None,
    actor_email: str | None,
    action: str | None,
    since: date | None,
    until: date | None,
    limit: int,
    offset: int,
):
    if entity_type is not None and entity_type not in ENTITY_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown entity type. Known types: {', '.join(ENTITY_TYPES)}",
        )

    stmt = select(AuditEvent).order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc())
    stmt = _visible(stmt, user, entity_type)

    if entity_type is not None:
        stmt = stmt.where(AuditEvent.entity_type == entity_type)
    if entity_id is not None:
        stmt = stmt.where(AuditEvent.entity_id == entity_id)
    if actor_email is not None:
        stmt = stmt.where(AuditEvent.actor_email == actor_email.strip().casefold())
    if action is not None:
        stmt = stmt.where(AuditEvent.action == action)
    if since is not None:
        stmt = stmt.where(
            AuditEvent.occurred_at >= datetime.combine(since, time.min, tzinfo=timezone.utc)
        )
    if until is not None:
        stmt = stmt.where(
            AuditEvent.occurred_at <= datetime.combine(until, time.max, tzinfo=timezone.utc)
        )

    return db.scalars(stmt.limit(limit).offset(offset))


@router.get("", response_model=list[schemas.AuditEventRead])
def list_events(
    db: Session = Depends(get_session),
    user: User = Depends(auth.require_viewer),
    entity_type: str | None = Query(default=None, description=f"One of: {', '.join(ENTITY_TYPES)}"),
    entity_id: int | None = Query(
        default=None, description="With entity_type, gives one record's history."
    ),
    actor_email: str | None = None,
    action: str | None = Query(default=None, description="create, update or delete."),
    since: date | None = None,
    until: date | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> list[AuditEvent]:
    """The trail, most recent first."""
    return list(
        _query(
            db,
            user,
            entity_type=entity_type,
            entity_id=entity_id,
            actor_email=actor_email,
            action=action,
            since=since,
            until=until,
            limit=limit,
            offset=offset,
        )
    )


@router.get(
    ".csv",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/csv": {}}}},
)
def export_csv(
    db: Session = Depends(get_session),
    user: User = Depends(auth.require_viewer),
    entity_type: str | None = None,
    entity_id: int | None = None,
    actor_email: str | None = None,
    action: str | None = None,
    since: date | None = None,
    until: date | None = None,
    limit: int = Query(default=5000, ge=1, le=50000),
) -> StreamingResponse:
    """The same trail as a CSV, for handing to an auditor."""
    events = _query(
        db,
        user,
        entity_type=entity_type,
        entity_id=entity_id,
        actor_email=actor_email,
        action=action,
        since=since,
        until=until,
        limit=limit,
        offset=0,
    )

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CSV_COLUMNS)
    for record in events:
        writer.writerow(
            [
                record.occurred_at.isoformat(),
                record.actor_email,
                record.actor_name,
                record.action,
                record.entity_type,
                "" if record.entity_id is None else record.entity_id,
                record.entity_label,
                "; ".join(_describe_change(field, value) for field, value in record.changes.items()),
            ]
        )
    buffer.seek(0)
    filename = f"audit-trail-{date.today().isoformat()}.csv"
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _describe_change(field: str, value: dict) -> str:
    """Flatten one change into a phrase a spreadsheet can hold."""
    if "added" in value or "removed" in value:
        parts = []
        if value.get("added"):
            parts.append(f"+{value['added']}")
        if value.get("removed"):
            parts.append(f"-{value['removed']}")
        return f"{field} {' '.join(parts)}"
    before = value.get("from")
    after = value.get("to")
    return f"{field}: {before!r} -> {after!r}"
