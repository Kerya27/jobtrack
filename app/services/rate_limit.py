"""Per-user hourly limit for the URL parser, stored in the database.

A DB table (not process memory) keeps the limit correct across restarts and multiple
workers, which matters on free hosts that restart often.
"""

from datetime import timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models import ParseRequest, utcnow

WINDOW = timedelta(hours=1)


def try_acquire(db: Session, user_id: int, limit: int) -> bool:
    """Record one parser call if the user is under the limit. Returns False when over it."""
    cutoff = utcnow() - WINDOW
    db.execute(
        delete(ParseRequest).where(
            ParseRequest.user_id == user_id, ParseRequest.created_at < cutoff
        )
    )
    used = db.scalar(
        select(func.count()).select_from(ParseRequest).where(ParseRequest.user_id == user_id)
    )
    if (used or 0) >= limit:
        db.commit()
        return False
    db.add(ParseRequest(user_id=user_id))
    db.commit()
    return True
