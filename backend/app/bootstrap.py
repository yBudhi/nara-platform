import argparse
import getpass
import sys
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.database import get_session_factory
from app.models.user import User
from app.services.bootstrap import (
    BootstrapError,
    bootstrap_initial_company,
    normalize_email,
)


@dataclass(frozen=True)
class _BootstrapSummary:
    company_name: str
    company_slug: str
    admin_email: str
    agent_email: str
    admin_role: str
    agent_role: str


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Create the initial WE Playground company, administrator, "
            "NARA AI Agent, roles, memberships, and incidents module."
        )
    )
    parser.add_argument(
        "--admin-email",
        help="Administrator email address (prompted when omitted)",
    )
    parser.add_argument(
        "--agent-email",
        help="NARA AI Agent email address (prompted when omitted)",
    )
    return parser


def _prompt_email(label: str, provided: str | None) -> str:
    if provided:
        return normalize_email(provided)

    while True:
        value = input(f"{label} email: ")
        try:
            return normalize_email(value)
        except BootstrapError as exc:
            print(exc, file=sys.stderr)


def _prompt_password(label: str) -> str:
    while True:
        password = getpass.getpass(f"{label} password: ")
        if not password:
            print("Password must not be empty.", file=sys.stderr)
            continue

        confirmation = getpass.getpass(f"Confirm {label.lower()} password: ")
        if password != confirmation:
            print("Passwords do not match.", file=sys.stderr)
            continue

        return password


def _user_exists(session: Session, email: str) -> bool:
    return (
        session.scalar(
            select(User.id).where(func.lower(User.email) == email)
        )
        is not None
    )


def _print_result(result: _BootstrapSummary) -> None:
    print(f"Bootstrap complete: {result.company_name} ({result.company_slug})")
    print(f"  Administrator: {result.admin_email}")
    print(f"  NARA AI Agent: {result.agent_email}")
    print(f"  Administrator role: {result.admin_role}")
    print(f"  NARA AI Agent role: {result.agent_role}")
    print("  Incidents module: enabled")


def _run(args: argparse.Namespace) -> _BootstrapSummary:
    admin_email = _prompt_email("Administrator", args.admin_email)
    agent_email = _prompt_email("NARA AI Agent", args.agent_email)
    if admin_email == agent_email:
        raise BootstrapError("Administrator and AI Agent emails must differ")

    session_factory = get_session_factory()
    with session_factory() as session:
        try:
            admin_password = (
                None
                if _user_exists(session, admin_email)
                else _prompt_password("Administrator")
            )
            agent_password = (
                None
                if _user_exists(session, agent_email)
                else _prompt_password("NARA AI Agent")
            )

            result = bootstrap_initial_company(
                session,
                admin_email=admin_email,
                admin_password=admin_password,
                agent_email=agent_email,
                agent_password=agent_password,
            )
            summary = _BootstrapSummary(
                company_name=result.company.name,
                company_slug=result.company.slug,
                admin_email=result.admin_user.email,
                agent_email=result.agent_user.email,
                admin_role=result.admin_role.name,
                agent_role=result.agent_role.name,
            )
            session.commit()
        except Exception:
            session.rollback()
            raise

    return summary


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        result = _run(args)
    except BootstrapError as exc:
        print(f"Bootstrap failed: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nBootstrap cancelled.", file=sys.stderr)
        return 130
    except (EOFError, OSError, RuntimeError, SQLAlchemyError):
        print("Bootstrap failed due to an unexpected error.", file=sys.stderr)
        return 1

    _print_result(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
