"""Invitations to, and membership in, a persistent two-person application battle."""

from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import DuoBattle, DuoStatus, Friendship, FriendshipStatus, User
from app.services.applications import NotFoundError
from app.services.friends import _between, display_name, friend_ids


class InviteOutcome(StrEnum):
    SENT = "sent"
    ALREADY_EXISTS = "already_exists"
    SENDER_BUSY = "sender_busy"
    FRIEND_BUSY = "friend_busy"


@dataclass(frozen=True)
class DuoInvite:
    battle: DuoBattle
    user: User

    @property
    def name(self) -> str:
        return display_name(self.user)


@dataclass(frozen=True)
class DuoView:
    battle: DuoBattle
    friend: User

    @property
    def friend_name(self) -> str:
        return display_name(self.friend)


def _battles_for(db: Session, user_id: int) -> list[DuoBattle]:
    return db.scalars(
        select(DuoBattle)
        .where(
            DuoBattle.status.in_((DuoStatus.PENDING, DuoStatus.ACTIVE)),
            or_(DuoBattle.challenger_id == user_id, DuoBattle.opponent_id == user_id),
        )
        .order_by(DuoBattle.created_at.desc(), DuoBattle.id.desc())
    ).all()


def current_battle(db: Session, user_id: int) -> DuoView | None:
    battles = _battles_for(db, user_id)
    if not battles:
        return None
    battle = battles[0]
    friend_id = (
        battle.opponent_id if battle.challenger_id == user_id else battle.challenger_id
    )
    friend = db.get(User, friend_id)
    return DuoView(battle, friend) if friend else None


def incoming_invites(db: Session, user_id: int) -> list[DuoInvite]:
    rows = db.execute(
        select(DuoBattle, User)
        .join(User, User.id == DuoBattle.challenger_id)
        .where(DuoBattle.opponent_id == user_id, DuoBattle.status == DuoStatus.PENDING)
        .order_by(DuoBattle.created_at.desc(), DuoBattle.id.desc())
    ).all()
    return [DuoInvite(battle, user) for battle, user in rows]


def send_invite(db: Session, user_id: int, friend_id: int) -> InviteOutcome:
    if friend_id not in friend_ids(db, user_id):
        raise NotFoundError
    if any(
        {battle.challenger_id, battle.opponent_id} == {user_id, friend_id}
        for battle in _battles_for(db, user_id)
    ):
        return InviteOutcome.ALREADY_EXISTS
    if _battles_for(db, user_id):
        return InviteOutcome.SENDER_BUSY
    if _battles_for(db, friend_id):
        return InviteOutcome.FRIEND_BUSY
    db.add(DuoBattle(challenger_id=user_id, opponent_id=friend_id))
    db.commit()
    return InviteOutcome.SENT


def accept_invite(db: Session, user_id: int, battle_id: int) -> None:
    battle = db.scalar(
        select(DuoBattle).where(
            DuoBattle.id == battle_id,
            DuoBattle.opponent_id == user_id,
            DuoBattle.status == DuoStatus.PENDING,
        )
    )
    if battle is None:
        raise NotFoundError
    if (
        any(item.id != battle.id for item in _battles_for(db, user_id))
        or any(item.id != battle.id for item in _battles_for(db, battle.challenger_id))
    ):
        raise NotFoundError
    friendship = db.scalar(
        select(Friendship).where(
            _between(user_id, battle.challenger_id),
            Friendship.status == FriendshipStatus.ACCEPTED,
        )
    )
    if friendship is None:
        raise NotFoundError
    battle.status = DuoStatus.ACTIVE
    db.commit()


def leave_battle(db: Session, user_id: int, battle_id: int) -> None:
    battle = db.scalar(
        select(DuoBattle).where(
            DuoBattle.id == battle_id,
            DuoBattle.status.in_((DuoStatus.PENDING, DuoStatus.ACTIVE)),
            or_(DuoBattle.challenger_id == user_id, DuoBattle.opponent_id == user_id),
        )
    )
    if battle is None:
        raise NotFoundError
    db.delete(battle)
    db.commit()
