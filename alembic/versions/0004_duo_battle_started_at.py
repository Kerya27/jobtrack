"""Track when an accepted duo battle started."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "duo_battles",
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute("UPDATE duo_battles SET started_at = created_at WHERE status = 'active'")


def downgrade() -> None:
    op.drop_column("duo_battles", "started_at")
