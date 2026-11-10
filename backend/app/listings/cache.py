"""Where job board results are kept between searches and restarts.

The search code only needs load and save, so the store can move to Valkey or another cache
server later by adding a class here. Today it's Postgres, which is already running.
"""

import hashlib
import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import delete
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.listings.boards import RawListing
from app.models import BoardCache, utcnow

# Older results are still shown while newer ones are fetched, until they're this old, and then
# they're deleted from the store.
KEEP_RESULTS = timedelta(hours=48)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StoredResults:
    """One board's listings for one search, as fetched, and until when they count as fresh."""

    board: str
    listings: list[RawListing]
    fetched_at: datetime
    fresh_until: datetime
    warning: str | None = None


class ListingStore(Protocol):
    def load(self, key: str) -> StoredResults | None: ...

    def save(self, key: str, results: StoredResults) -> None: ...


def cache_key(board: str, keywords: str, location: str, depth: str = "") -> str:
    """The stored results' key. A plain search's key is the same as before deep searches."""
    parts = [board, keywords, location, *([depth] if depth else [])]
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()


def _encode(listing: RawListing) -> dict[str, Any]:
    data = asdict(listing)
    data["published_on"] = listing.published_on.isoformat() if listing.published_on else None
    return data


def decode_listing(data: dict[str, Any]) -> RawListing:
    published = data.get("published_on")
    return RawListing(
        company=data["company"],
        title=data["title"],
        url=data["url"],
        description=data.get("description", ""),
        location=data.get("location"),
        published_on=date.fromisoformat(published) if published else None,
        direct_url=data.get("direct_url"),
    )


class SqlListingStore:
    """Keeps results in the board_cache table. Anything older than keep is deleted on save.

    A database error is logged and treated as a cache miss, so search still works without it.
    """

    def __init__(self, session_factory: Callable[[], Session], keep: timedelta) -> None:
        self._session_factory = session_factory
        self._keep = keep

    def load(self, key: str) -> StoredResults | None:
        try:
            with self._session_factory() as db:
                row = db.get(BoardCache, key)
                if row is None or _aware(row.fetched_at) < utcnow() - self._keep:
                    return None
                return StoredResults(
                    board=row.board,
                    listings=[decode_listing(item) for item in row.listings],
                    fetched_at=_aware(row.fetched_at),
                    fresh_until=_aware(row.fresh_until),
                    warning=row.warning,
                )
        except (SQLAlchemyError, KeyError, TypeError, ValueError):
            logger.warning("Could not read cached results for %s", key, exc_info=True)
            return None

    def save(self, key: str, results: StoredResults) -> None:
        try:
            with self._session_factory() as db:
                db.merge(
                    BoardCache(
                        key=key,
                        board=results.board,
                        listings=[_encode(listing) for listing in results.listings],
                        warning=results.warning,
                        fetched_at=results.fetched_at,
                        fresh_until=results.fresh_until,
                    )
                )
                db.execute(delete(BoardCache).where(BoardCache.fetched_at < utcnow() - self._keep))
                db.commit()
        except SQLAlchemyError:
            logger.warning("Could not save cached results for %s", key, exc_info=True)


def _aware(moment: datetime) -> datetime:
    """SQLite returns times without a zone. They're stored in UTC."""
    return moment if moment.tzinfo else moment.replace(tzinfo=utcnow().tzinfo)
