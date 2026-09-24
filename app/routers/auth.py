"""Sign-in, sign-out and the caller's own account."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app import auth, schemas, security
from app.database import get_session
from app.models import User

router = APIRouter(prefix="/api/auth", tags=["authentication"])


@router.post("/login", response_model=schemas.UserRead)
def login(
    payload: schemas.LoginRequest,
    response: Response,
    db: Session = Depends(get_session),
) -> User:
    """Exchange credentials for a session cookie."""
    user = auth.authenticate(db, payload.email, payload.password)
    session_id = auth.create_session(db, user)
    db.commit()
    auth.set_session_cookie(response, session_id)
    return user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, db: Session = Depends(get_session)) -> Response:
    """End the current session.

    The session row is deleted, not merely un-cookied, so a copy of the cookie
    taken beforehand is worthless afterwards. Anonymous callers get the same
    204, which makes logout safe to call twice and after expiry.
    """
    session_id = request.cookies.get(auth.SESSION_COOKIE)
    if session_id:
        auth.revoke_session(db, session_id)
        db.commit()

    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    auth.clear_session_cookie(response)
    return response


@router.get("/me", response_model=schemas.UserRead)
def me(user: User = Depends(auth.current_user)) -> User:
    """Who the caller is, and what they may do."""
    return user


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    payload: schemas.PasswordChange,
    db: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> Response:
    """Change your own password, ending every session you hold."""
    if not security.verify_password(payload.current_password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Current password is incorrect"
        )

    user.password_hash = security.hash_password(payload.new_password)
    # Sessions opened with the old password must not survive the change.
    auth.revoke_all_sessions(db, user.id)
    db.commit()

    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    auth.clear_session_cookie(response)
    return response
