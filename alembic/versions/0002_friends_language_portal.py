"""Friends, UI language, daily goal, company portal link.

Only adds columns/tables, so existing users and applications are kept as they are.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(
            sa.Column("language", sa.String(length=5), nullable=False, server_default="en")
        )
        batch.add_column(
            sa.Column("daily_goal", sa.Integer(), nullable=False, server_default="5")
        )

    with op.batch_alter_table("applications") as batch:
        batch.add_column(sa.Column("portal_url", sa.String(length=2000), nullable=True))
    op.create_index("ix_applications_user_applied_at", "applications", ["user_id", "applied_at"])

    op.create_table(
        "friendships",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("requester_id", sa.Integer(), nullable=False),
        sa.Column("addressee_id", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "accepted",
                name="friendship_status",
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "requester_id <> addressee_id", name=op.f("ck_friendships_not_self")
        ),
        sa.ForeignKeyConstraint(
            ["requester_id"],
            ["users.id"],
            name=op.f("fk_friendships_requester_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["addressee_id"],
            ["users.id"],
            name=op.f("fk_friendships_addressee_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_friendships")),
        sa.UniqueConstraint(
            "requester_id", "addressee_id", name=op.f("uq_friendships_requester_id")
        ),
    )
    op.create_index("ix_friendships_addressee", "friendships", ["addressee_id"])


def downgrade() -> None:
    op.drop_table("friendships")
    op.drop_index("ix_applications_user_applied_at", table_name="applications")
    with op.batch_alter_table("applications") as batch:
        batch.drop_column("portal_url")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("daily_goal")
        batch.drop_column("language")
