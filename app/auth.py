"""Authentication and authorisation.

A request is identified either by a session cookie (the dashboard) or by a
bearer token (scripts and integrations). Both resolve to a :class:`User`, and
authorisation is then purely a question of that user's role.

The dependencies at the bottom are what routers use: ``require_viewer`` to read,
``require_editor`` to change a register, ``require_admin`` to manage accounts.
"""

from __future__ import annotations

import hmac
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import security
from app.config import settings
from app.models import ApiToken, User, UserSession
from app.database import get_session
from app.reference import Role, role_at_least

SESSION_COOKIE = "regulatory_session"

#: Returned whenever the caller is unidentified. Deliberately uniform, so it
#: never reveals whether an account exists, is locked, or is merely deactivated.
_UNAUTHENTICATED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not authenticated",
    headers={"WWW-Authenticate": "Bearer"},
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware(value: datetime | None) -> datetime | None:
    """SQLite hands back naive datetimes; treat those as UTC."""
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# Sessions
# --------------------------------------------------------------------------- #
def create_session(db: Session, user: User) -> str:
    """Open a browser session and return its plaintext identifier."""
    session_id = security.new_session_id()
    db.add(
        UserSession(
            user_id=user.id,
            session_hash=security.hash_secret(session_id),
            expires_at=_utcnow() + timedelta(hours=settings.session_hours),
        )
    )
    return session_id


def revoke_session(db: Session, session_id: str) -> None:
    record = db.scalar(
        select(UserSession).where(
            UserSession.session_hash == security.hash_secret(session_id)
        )
    )
    if record is not None:
        db.delete(record)


def revoke_all_sessions(db: Session, user_id: int) -> None:
    """Drop every session for a user — used on password change or deactivation."""
    for record in db.scalars(select(UserSession).where(UserSession.user_id == user_id)):
        db.delete(record)


def purge_expired_sessions(db: Session) -> int:
    """Delete sessions that have already lapsed. Returns how many went."""
    removed = 0
    for record in db.scalars(select(UserSession).where(UserSession.expires_at < _utcnow())):
        db.delete(record)
        removed += 1
    return removed


def set_session_cookie(response: Response, session_id: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        session_id,
        max_age=settings.session_hours * 3600,
        httponly=True,
        samesite="lax",
        secure=settings.session_cookie_secure,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


# --------------------------------------------------------------------------- #
# Resolving the caller
# --------------------------------------------------------------------------- #
def _user_from_cookie(db: Session, request: Request) -> User | None:
    session_id = request.cookies.get(SESSION_COOKIE)
    if not session_id:
        return None

    record = db.scalar(
        select(UserSession).where(
            UserSession.session_hash == security.hash_secret(session_id)
        )
    )
    if record is None:
        return None

    expires_at = _as_aware(record.expires_at)
    if expires_at is None or expires_at <= _utcnow():
        db.delete(record)
        db.commit()
        return None

    record.last_seen_at = _utcnow()
    db.commit()
    return record.user


def _user_from_bearer(db: Session, request: Request) -> User | None:
    header = request.headers.get("Authorization", "")
    scheme, _, presented = header.partition(" ")
    if scheme.lower() != "bearer" or not presented:
        return None

    parts = security.split_api_token(presented.strip())
    if parts is None:
        return None
    lookup, digest = parts

    # The lookup narrows to (almost always) one row; the digest decides.
    for token in db.scalars(select(ApiToken).where(ApiToken.lookup == lookup)):
        if not hmac.compare_digest(token.token_hash, digest):
            continue
        if token.revoked_at is not None:
            return None
        expires_at = _as_aware(token.expires_at)
        if expires_at is not None and expires_at <= _utcnow():
            return None
        token.last_used_at = _utcnow()
        db.commit()
        return token.user
    return None


def current_user_or_none(
    request: Request, db: Session = Depends(get_session)
) -> User | None:
    """Resolve the caller, or ``None`` when the request is anonymous."""
    user = _user_from_cookie(db, request) or _user_from_bearer(db, request)
    if user is None or not user.is_active:
        return None
    return user


def current_user(
    user: User | None = Depends(current_user_or_none),
) -> User:
    """Resolve the caller, rejecting anonymous requests."""
    if user is None:
        raise _UNAUTHENTICATED
    return user


def _require(minimum: Role):
    """Build a dependency that admits only callers holding ``minimum`` or above."""

    def dependency(user: User = Depends(current_user)) -> User:
        if not role_at_least(user.role, minimum):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"This action needs the {minimum.value} role; "
                    f"your account is {user.role.value}."
                ),
            )
        return user

    return dependency


require_viewer = _require(Role.VIEWER)
require_editor = _require(Role.EDITOR)
require_admin = _require(Role.ADMIN)


# --------------------------------------------------------------------------- #
# Sign-in
# --------------------------------------------------------------------------- #
def authenticate(db: Session, email: str, password: str) -> User:
    """Verify credentials, applying lockout, or raise 401.

    Every failure path raises the same error: whether the address is unknown,
    the password wrong, the account deactivated or the account locked is not
    something an unauthenticated caller gets to learn.
    """
    invalid = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Incorrect email address or password",
    )

    user = db.scalar(select(User).where(User.email == email.strip().casefold()))
    if user is None:
        # Spend comparable time on an unknown address so the response time does
        # not distinguish it from a wrong password.
        security.verify_password(password, security.hash_password("timing-equaliser"))
        raise invalid

    locked_until = _as_aware(user.locked_until)
    if locked_until is not None and locked_until > _utcnow():
        raise invalid

    if not user.is_active or not security.verify_password(password, user.password_hash):
        user.failed_login_count += 1
        if user.failed_login_count >= settings.max_failed_logins:
            user.locked_until = _utcnow() + timedelta(minutes=settings.lockout_minutes)
            user.failed_login_count = 0
        db.commit()
        raise invalid

    # Opportunistically upgrade a hash made with weaker parameters.
    if security.needs_rehash(user.password_hash):
        user.password_hash = security.hash_password(password)

    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = _utcnow()
    db.commit()
    return user
