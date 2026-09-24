"""Create or update an administrator account, interactively.

Usage::

    python -m scripts.create_admin                     # prompts for everything
    python -m scripts.create_admin --email a@b.com     # prompts for the password

The password is read without echo and never taken from the command line, where
it would land in the shell history and the process list.
"""

from __future__ import annotations

import argparse
import getpass
import sys

from sqlalchemy import select

from app import security
from app.database import SessionLocal, init_db
from app.models import User
from app.reference import Role
from app.schemas import MIN_PASSWORD_LENGTH


def prompt_password() -> str:
    while True:
        password = getpass.getpass("Password: ")
        if len(password) < MIN_PASSWORD_LENGTH:
            print(
                f"Too short — use at least {MIN_PASSWORD_LENGTH} characters.",
                file=sys.stderr,
            )
            continue
        if password != getpass.getpass("Confirm password: "):
            print("Those did not match.", file=sys.stderr)
            continue
        return password


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", help="Email address to sign in with.")
    parser.add_argument("--name", help="Full name shown in the dashboard.")
    parser.add_argument(
        "--role",
        choices=[role.value for role in Role],
        default=Role.ADMIN.value,
        help="Role to grant (default: admin).",
    )
    args = parser.parse_args()

    init_db()

    email = (args.email or input("Email: ")).strip().casefold()
    if "@" not in email:
        raise SystemExit(f"Not an email address: {email!r}")

    with SessionLocal() as db:
        existing = db.scalar(select(User).where(User.email == email))
        name = args.name or (existing.full_name if existing else None) or input("Full name: ")
        password = prompt_password()

        if existing is not None:
            answer = input(
                f"{email} already exists. Reset its password and set role to "
                f"{args.role}? [y/N] "
            )
            if answer.strip().casefold() not in {"y", "yes"}:
                raise SystemExit("Nothing changed.")
            existing.full_name = name
            existing.role = Role(args.role)
            existing.is_active = True
            existing.password_hash = security.hash_password(password)
            existing.failed_login_count = 0
            existing.locked_until = None
            # Any session opened with the old password must not survive.
            existing.sessions.clear()
            action = "Updated"
        else:
            db.add(
                User(
                    email=email,
                    full_name=name,
                    role=Role(args.role),
                    password_hash=security.hash_password(password),
                )
            )
            action = "Created"

        db.commit()

    print(f"{action} {args.role} account for {email}.")


if __name__ == "__main__":
    main()
