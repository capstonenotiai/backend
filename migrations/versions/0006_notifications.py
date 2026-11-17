"""Notification inbox and email delivery log. Revert with downgrade 0005."""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = depends_on = None


def upgrade():
    if "notifications" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table("notifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_id", sa.Integer(), sa.ForeignKey("events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("target_date", sa.String(10), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True)),
        sa.Column("email_status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("email_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("email_sent_at", sa.DateTime(timezone=True)),
        sa.Column("email_error", sa.Text()),
        sa.UniqueConstraint("user_id", "event_id", "kind", "target_date"))
    op.create_index("ix_notifications_user_id", "notifications", ["user_id"])
    op.create_index("ix_notifications_event_id", "notifications", ["event_id"])


def downgrade():
    op.drop_table("notifications")
