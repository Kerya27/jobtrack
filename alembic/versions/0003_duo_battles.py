"""Duo battle invitations and active battles.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-30
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "duo_battles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("challenger_id", sa.Integer(), nullable=False),
        sa.Column("opponent_id", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "active",
                name="duo_status",
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "challenger_id <> opponent_id", name=op.f("ck_duo_battles_not_self")
        ),
        sa.ForeignKeyConstraint(
            ["challenger_id"],
            ["users.id"],
            name=op.f("fk_duo_battles_challenger_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["opponent_id"],
            ["users.id"],
            name=op.f("fk_duo_battles_opponent_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_duo_battles")),
    )
    op.create_index(
        "ix_duo_battles_challenger_status", "duo_battles", ["challenger_id", "status"]
    )
    op.create_index(
        "ix_duo_battles_opponent_status", "duo_battles", ["opponent_id", "status"]
    )


def downgrade() -> None:
    op.drop_index("ix_duo_battles_opponent_status", table_name="duo_battles")
    op.drop_index("ix_duo_battles_challenger_status", table_name="duo_battles")
    op.drop_table("duo_battles")
