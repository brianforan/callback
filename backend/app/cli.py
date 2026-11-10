"""Admin commands, run inside the API container."""

import argparse
import getpass
import logging
import sys

import httpx
from sqlalchemy import select

from app.billing.reprice import reprice_runs
from app.companies.lists import seed_catalog
from app.config import get_settings
from app.db import SessionLocal
from app.logs import configure_logging
from app.models import ApiToken, Resume, Role, User
from app.postings.store import sync_postings
from app.postings.text import queue_missing_texts
from app.profiles import first_profile
from app.scout.limits import Plan
from app.scout.worker import _Answer
from app.security import (
    MIN_PASSWORD_LENGTH,
    generate_api_token,
    hash_api_token,
    hash_password,
)
from app.similarity import tex_to_text
from app.workers import TailorUnavailable, post_json

logger = logging.getLogger("callback.cli")


def create_user(email: str) -> int:
    email = email.strip().lower()
    if "@" not in email:
        logger.error("%s is not an email address", email)
        return 1
    password = getpass.getpass("Password: ")
    if len(password) < MIN_PASSWORD_LENGTH:
        logger.error("Password must be at least %d characters", MIN_PASSWORD_LENGTH)
        return 1
    if password != getpass.getpass("Repeat password: "):
        logger.error("Passwords do not match")
        return 1
    with SessionLocal() as db:
        if db.scalar(select(User).where(User.email == email)):
            logger.error("A user with email %s already exists", email)
            return 1
        user = User(email=email, password_hash=hash_password(password))
        db.add(user)
        db.flush()
        db.add(first_profile(user.id))
        db.commit()
    logger.info("Created user %s", email)
    return 0


def create_token(email: str, name: str) -> int:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email.strip().lower()))
        if user is None:
            logger.error("No user with email %s", email)
            return 1
        token = generate_api_token()
        db.add(ApiToken(user_id=user.id, name=name, token_hash=hash_api_token(token)))
        db.commit()
    # Stdout carries only the token so scripts can capture it; it is never shown again.
    sys.stdout.write(f"{token}\n")
    return 0


def sync_user_postings(email: str) -> int:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email.strip().lower()))
        if user is None:
            logger.error("No user with email %s", email)
            return 1
        created, parsed = sync_postings(db, user)
    logger.info("Created %d postings, parsed %d in total", created, parsed)
    return 0


def fetch_posting_texts(email: str) -> int:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email.strip().lower()))
        if user is None:
            logger.error("No user with email %s", email)
            return 1
        copied, queued = queue_missing_texts(db, user.id)
    logger.info(
        "Copied %d postings' text from the catalog; %d wait for the tailor worker to read their "
        "links",
        copied,
        queued,
    )
    return 0


def refresh_resume_text(email: str) -> int:
    """Re-extract stored resume text after a change to the LaTeX rules. Safe to repeat."""
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email.strip().lower()))
        if user is None:
            logger.error("No user with email %s", email)
            return 1
        resumes = db.scalars(select(Resume).where(Resume.user_id == user.id)).all()
        for resume in resumes:
            resume.plain_text = tex_to_text(resume.source_tex)
            resume.word_count = len(resume.plain_text.split())
        db.commit()
    logger.info("Refreshed text for %d resumes", len(resumes))
    return 0


def set_plan(email: str, plan: str) -> int:
    """Turn premium on or off for one user. There's no billing yet; Admin → Users does this too."""
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email.strip().lower()))
        if user is None:
            logger.error("No user with email %s", email)
            return 1
        user.plan = plan
        db.commit()
    logger.info("Set %s to the %s plan", email, plan)
    return 0


def set_role(email: str, role: str) -> int:
    """Make one user an admin or a member: how the first admin is made, and the only way to
    take an admin's role away."""
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email.strip().lower()))
        if user is None:
            logger.error("No user with email %s", email)
            return 1
        user.role = role
        db.commit()
    logger.info("Set %s to the %s role", email, role)
    return 0


def catalog_seed() -> int:
    """Fill the Companies catalog from saved postings, Scout runs and cached board results,
    and put each user's companies on their profile's list. Safe to run again."""
    with SessionLocal() as db:
        summary = seed_catalog(db)
    logger.info(
        "Seeded the catalog: %d posting links, %d leads, %d run links, %d board listings, "
        "on %d profiles' lists",
        summary.postings,
        summary.leads,
        summary.candidates,
        summary.board_listings,
        len(summary.profiles),
    )
    return 0


def reprice(apply: bool) -> int:
    """Price past Scout runs again with tailor's current rules. Shows the changes, and
    saves them only with --apply. Run it where tailor can be reached (the scout-worker)."""
    settings = get_settings()
    with httpx.Client(base_url=settings.tailor_url, timeout=60.0) as client:

        def price(requests: list[dict]) -> list[dict]:
            costs: list[dict] = []
            for start in range(0, len(requests), 500):
                answer = post_json(
                    client,
                    "/pricing/reprice",
                    {"requests": requests[start : start + 500]},
                    _Answer,
                    "pricing service",
                ).root
                costs.extend(answer["costs"])
            return costs

        with SessionLocal() as db:
            try:
                changes = reprice_runs(db, price, apply)
            except TailorUnavailable as exc:
                logger.error("Couldn't price the runs: %s", exc)
                return 1
    for change in changes:
        if abs(change.new_cost - change.old_cost) >= 0.0001:
            logger.info(
                "%s %s: $%.4f -> $%.4f",
                change.engine,
                change.run_id,
                change.old_cost,
                change.new_cost,
            )
    old = sum(change.old_cost for change in changes)
    new = sum(change.new_cost for change in changes)
    saved = "" if apply else " Nothing saved; run with --apply to save."
    logger.info("%d runs: $%.2f measured before, $%.2f now.%s", len(changes), old, new, saved)
    return 0


def main() -> int:
    configure_logging(level=logging.INFO, fmt="%(message)s", stream=sys.stderr)
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)

    user_parser = commands.add_parser("create-user", help="Create a login")
    user_parser.add_argument("--email", required=True)

    token_parser = commands.add_parser("create-token", help="Create an API token")
    token_parser.add_argument("--email", required=True)
    token_parser.add_argument("--name", default="cli")

    sync_parser = commands.add_parser(
        "sync-postings", help="Create missing postings from applications and re-parse all"
    )
    sync_parser.add_argument("--email", required=True)

    refresh_parser = commands.add_parser(
        "refresh-resumes", help="Re-extract stored resume text from its LaTeX source"
    )
    refresh_parser.add_argument("--email", required=True)

    fetch_parser = commands.add_parser(
        "fetch-posting-texts",
        help="Get the text of postings saved with a link and none, from the catalog or the link",
    )
    fetch_parser.add_argument("--email", required=True)

    plan_parser = commands.add_parser("set-plan", help="Set a user's plan (premium unlocks Scout)")
    plan_parser.add_argument("--email", required=True)
    plan_parser.add_argument("--plan", required=True, choices=[plan.value for plan in Plan])

    role_parser = commands.add_parser(
        "set-role", help="Set a user's role (admin opens the Admin panel)"
    )
    role_parser.add_argument("--email", required=True)
    role_parser.add_argument("--role", required=True, choices=[role.value for role in Role])

    reprice_parser = commands.add_parser(
        "reprice-runs", help="Price past Scout runs again with tailor's current rules"
    )
    reprice_parser.add_argument("--apply", action="store_true", help="Save the new costs")

    commands.add_parser(
        "catalog-seed",
        help="Fill the Companies catalog from postings, Scout runs and cached boards",
    )

    args = parser.parse_args()
    if args.command == "reprice-runs":
        return reprice(args.apply)
    if args.command == "catalog-seed":
        return catalog_seed()
    if args.command == "set-plan":
        return set_plan(args.email, args.plan)
    if args.command == "set-role":
        return set_role(args.email, args.role)
    if args.command == "create-user":
        return create_user(args.email)
    if args.command == "sync-postings":
        return sync_user_postings(args.email)
    if args.command == "fetch-posting-texts":
        return fetch_posting_texts(args.email)
    if args.command == "refresh-resumes":
        return refresh_resume_text(args.email)
    return create_token(args.email, args.name)


if __name__ == "__main__":
    sys.exit(main())
