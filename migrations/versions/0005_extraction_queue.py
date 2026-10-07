"""Extraction retry schedule. Revert with downgrade 0004."""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = depends_on = None


def upgrade():
    existing = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("notices")}
    if "next_attempt_at" not in existing:
        op.add_column("notices", sa.Column("next_attempt_at", sa.DateTime(timezone=True)))


def downgrade():
    with op.batch_alter_table("notices") as batch:
        batch.drop_column("next_attempt_at")
