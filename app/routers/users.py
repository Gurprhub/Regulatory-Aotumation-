"""Account administration. Every endpoint here requires the admin role."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import auth, schemas, security, services
from app.database import get_session
from app.models import User
from app.reference import Role

router = APIRouter(
    prefix="/api/users",
    tags=["users"],
    dependencies=[Depends(auth.require_admin)],
)


@router.get("", response_model=list[schemas.UserRead])
def list_users(
    db: Session = Depends(get_session),
    role: Role | None = None,
    is_active: bool | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[User]:
    stmt = select(User).order_by(User.full_name)
    if role is not None:
        stmt = stmt.where(User.role == role)
    if is_active is not None:
        stmt = stmt.where(User.is_active == is_active)
    return list(db.scalars(stmt.limit(limit).offset(offset)))


@router.get("/{user_id}", response_model=schemas.UserRead)
def get_user(user_id: int, db: Session = Depends(get_session)) -> User:
    return services.get_or_404(db, User, user_id, "User")


@router.post("", response_model=schemas.UserRead, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: schemas.UserCreate, db: Session = Depends(get_session)
) -> User:
    data = payload.model_dump()
    password = data.pop("password")
    user = User(**data, password_hash=security.hash_password(password))
    db.add(user)
    services.commit_or_conflict(db, context=f"User {payload.email!r}")
    db.refresh(user)
    return user


@router.patch("/{user_id}", response_model=schemas.UserRead)
def update_user(
    user_id: int,
    payload: schemas.UserUpdate,
    db: Session = Depends(get_session),
) -> User:
    user = services.get_or_404(db, User, user_id, "User")
    updates = payload.model_dump(exclude_unset=True)

    # An admin must not be able to lock everyone out by demoting or disabling
    # themselves while they are the only administrator left.
    _guard_last_admin(db, user, updates)

    if "password" in updates:
        user.password_hash = security.hash_password(updates.pop("password"))
        # A reset password invalidates whatever sessions the account had.
        auth.revoke_all_sessions(db, user.id)

    services.apply_updates(user, updates)

    # Deactivating an account must take effect immediately, not at session expiry.
    if updates.get("is_active") is False:
        auth.revoke_all_sessions(db, user.id)

    services.commit_or_conflict(db, context=f"User {user_id}")
    db.refresh(user)
    return user


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    user_id: int,
    db: Session = Depends(get_session),
    actor: User = Depends(auth.require_admin),
) -> Response:
    """Delete an account outright, with its sessions and tokens.

    Prefer deactivating (``is_active = false``) where the account has history
    worth keeping.
    """
    user = services.get_or_404(db, User, user_id, "User")
    if user.id == actor.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You cannot delete the account you are signed in with.",
        )
    _guard_last_admin(db, user, {"role": Role.VIEWER})

    db.delete(user)
    services.commit_or_conflict(db, context=f"User {user_id}")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _guard_last_admin(db: Session, user: User, updates: dict) -> None:
    """Refuse a change that would leave the system with no active administrator."""
    losing_admin = (
        user.role is Role.ADMIN
        and (updates.get("role", user.role) is not Role.ADMIN
             or updates.get("is_active", user.is_active) is False)
    )
    if not losing_admin:
        return

    remaining = db.scalar(
        select(User)
        .where(User.role == Role.ADMIN, User.is_active.is_(True), User.id != user.id)
        .limit(1)
    )
    if remaining is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This is the only active administrator; promote another account "
                "before changing this one."
            ),
        )
