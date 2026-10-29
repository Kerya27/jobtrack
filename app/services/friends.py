"""Friends: a request by email; once accepted, both users can see each other's applications.

Visibility is read-only and all-or-nothing (no per-application privacy): the point is to see
where a friend has already applied and how it went. Every function takes the acting user's id,
so a user can only act on requests that involve them.
"""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.orm import Session

from app.models import Application, ApplicationStatus, Friendship, FriendshipStatus, User
from app.services.applications import NotFoundError, normalize_company


class RequestOutcome(StrEnum):
    SENT = "sent"  # also returned for unknown emails, so the form doesn't reveal who is registered
    ACCEPTED = "accepted"  # they had already asked us: the request is accepted right away
    SELF = "self"
    ALREADY = "already"


@dataclass(frozen=True)
class FriendRow:
    friendship_id: int
    user: User
    applications: int = 0


def display_name(user: User) -> str:
    return user.email.split("@", 1)[0]


def _between(a: int, b: int):  # type: ignore[no-untyped-def]
    return or_(
        and_(Friendship.requester_id == a, Friendship.addressee_id == b),
        and_(Friendship.requester_id == b, Friendship.addressee_id == a),
    )


def friend_ids(db: Session, user_id: int) -> list[int]:
    rows = db.execute(
        select(Friendship.requester_id, Friendship.addressee_id).where(
            Friendship.status == FriendshipStatus.ACCEPTED,
            or_(Friendship.requester_id == user_id, Friendship.addressee_id == user_id),
        )
    ).all()
    return [b if a == user_id else a for a, b in rows]


def get_friend(db: Session, user_id: int, friend_id: int) -> User:
    """The friend's account, or NotFoundError if they are not (yet) friends."""
    if friend_id not in friend_ids(db, user_id):
        raise NotFoundError
    friend = db.get(User, friend_id)
    if friend is None:
        raise NotFoundError
    return friend


def send_request(db: Session, user: User, email: str) -> RequestOutcome:
    email = email.strip().lower()
    if email == user.email:
        return RequestOutcome.SELF
    other = db.scalar(select(User).where(User.email == email))
    if other is None:
        return RequestOutcome.SENT
    existing = db.scalar(select(Friendship).where(_between(user.id, other.id)))
    if existing is None:
        db.add(Friendship(requester_id=user.id, addressee_id=other.id))
        db.commit()
        return RequestOutcome.SENT
    if existing.status == FriendshipStatus.PENDING and existing.addressee_id == user.id:
        existing.status = FriendshipStatus.ACCEPTED
        db.commit()
        return RequestOutcome.ACCEPTED
    return RequestOutcome.ALREADY


def _with_users(
    db: Session, user_id: int, *, status: FriendshipStatus, incoming: bool | None
) -> list[FriendRow]:
    """Requests involving ``user_id`` with the other side's account.

    incoming=True: sent to the user, False: sent by the user, None: either direction.
    """
    stmt = select(Friendship).where(Friendship.status == status)
    if incoming is True:
        stmt = stmt.where(Friendship.addressee_id == user_id)
    elif incoming is False:
        stmt = stmt.where(Friendship.requester_id == user_id)
    else:
        stmt = stmt.where(
            or_(Friendship.requester_id == user_id, Friendship.addressee_id == user_id)
        )
    friendships = db.scalars(stmt.order_by(Friendship.created_at.desc())).all()
    other_ids = {
        f.addressee_id if f.requester_id == user_id else f.requester_id for f in friendships
    }
    users = {u.id: u for u in db.scalars(select(User).where(User.id.in_(other_ids)))}
    rows = []
    for f in friendships:
        other_id = f.addressee_id if f.requester_id == user_id else f.requester_id
        if other_id in users:
            rows.append(FriendRow(f.id, users[other_id]))
    return rows


def incoming_requests(db: Session, user_id: int) -> list[FriendRow]:
    return _with_users(db, user_id, status=FriendshipStatus.PENDING, incoming=True)


def outgoing_requests(db: Session, user_id: int) -> list[FriendRow]:
    return _with_users(db, user_id, status=FriendshipStatus.PENDING, incoming=False)


def incoming_count(db: Session, user_id: int) -> int:
    return (
        db.scalar(
            select(func.count()).where(
                Friendship.addressee_id == user_id,
                Friendship.status == FriendshipStatus.PENDING,
            )
        )
        or 0
    )


def friends(db: Session, user_id: int) -> list[FriendRow]:
    rows = _with_users(db, user_id, status=FriendshipStatus.ACCEPTED, incoming=None)
    if not rows:
        return []
    counts = dict(
        db.execute(
            select(Application.user_id, func.count())
            .where(Application.user_id.in_([r.user.id for r in rows]))
            .group_by(Application.user_id)
        ).all()
    )
    rows = [FriendRow(r.friendship_id, r.user, counts.get(r.user.id, 0)) for r in rows]
    return sorted(rows, key=lambda r: r.user.email)


def accept(db: Session, user_id: int, friendship_id: int) -> User:
    """Only the addressee can accept a pending request."""
    friendship = db.scalar(
        select(Friendship).where(
            Friendship.id == friendship_id,
            Friendship.addressee_id == user_id,
            Friendship.status == FriendshipStatus.PENDING,
        )
    )
    if friendship is None:
        raise NotFoundError
    friendship.status = FriendshipStatus.ACCEPTED
    db.commit()
    requester = db.get(User, friendship.requester_id)
    if requester is None:
        raise NotFoundError
    return requester


def remove(db: Session, user_id: int, friendship_id: int) -> None:
    """Decline, cancel or unfriend: either side can delete a request or a friendship."""
    result = db.execute(
        delete(Friendship).where(
            Friendship.id == friendship_id,
            or_(Friendship.requester_id == user_id, Friendship.addressee_id == user_id),
        )
    )
    if not result.rowcount:  # type: ignore[attr-defined]
        raise NotFoundError
    db.commit()


def friends_by_company(db: Session, user_id: int) -> dict[str, list[str]]:
    """normalized company → names of friends who applied there ("also applied" badge)."""
    ids = friend_ids(db, user_id)
    if not ids:
        return {}
    names = {u.id: display_name(u) for u in db.scalars(select(User).where(User.id.in_(ids)))}
    rows: Sequence[tuple[str, int]] = db.execute(  # type: ignore[assignment]
        select(Application.company, Application.user_id)
        .where(Application.user_id.in_(ids), Application.status != ApplicationStatus.SAVED)
        .distinct()
    ).all()
    result: dict[str, list[str]] = defaultdict(list)
    for company, owner_id in rows:
        key = normalize_company(company)
        name = names.get(owner_id)
        if key and name and name not in result[key]:
            result[key].append(name)
    return dict(result)
