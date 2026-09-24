"""API tokens for scripts and integrations.

A token acts as its owner: it carries that user's role, and it stops working the
moment the account is deactivated. Tokens are personal — you manage your own,
and an admin can see and revoke anyone's.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import auth, schemas, security, services
from app.database import get_session
from app.models import ApiToken, User
from app.reference import Role

router = APIRouter(prefix="/api/tokens", tags=["api tokens"])


@router.get("", response_model=list[schemas.ApiTokenRead])
def list_tokens(
    db: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
    all_users: bool = Query(
        default=False, description="Admins only: list every user's tokens."
    ),
) -> list[ApiToken]:
    stmt = select(ApiToken).order_by(ApiToken.created_at.desc())
    if all_users:
        if user.role is not Role.ADMIN:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Listing every user's tokens needs the admin role.",
            )
    else:
        stmt = stmt.where(ApiToken.user_id == user.id)
    return list(db.scalars(stmt))


@router.post("", response_model=schemas.ApiTokenCreated, status_code=status.HTTP_201_CREATED)
def create_token(
    payload: schemas.ApiTokenCreate,
    db: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> dict:
    """Mint a token for yourself.

    The plaintext is in this response and nowhere else — it is not recoverable
    afterwards, so store it now.
    """
    generated = security.new_api_token()
    expires_at = (
        datetime.now(timezone.utc) + timedelta(days=payload.expires_in_days)
        if payload.expires_in_days is not None
        else None
    )
    token = ApiToken(
        user_id=user.id,
        name=payload.name,
        lookup=generated.lookup,
        token_hash=generated.digest,
        expires_at=expires_at,
    )
    db.add(token)
    services.commit_or_conflict(db, context=f"API token {payload.name!r}")
    db.refresh(token)

    return {
        "id": token.id,
        "name": token.name,
        "created_at": token.created_at,
        "expires_at": token.expires_at,
        "last_used_at": token.last_used_at,
        "revoked_at": token.revoked_at,
        "token": generated.plaintext,
    }


@router.delete("/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_token(
    token_id: int,
    db: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> Response:
    """Revoke a token. Yours always; anyone's if you are an admin."""
    token = services.get_or_404(db, ApiToken, token_id, "API token")
    if token.user_id != user.id and user.role is not Role.ADMIN:
        # Do not confirm that someone else's token id exists.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"API token {token_id} was not found",
        )

    if token.revoked_at is None:
        token.revoked_at = datetime.now(timezone.utc)
        db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
