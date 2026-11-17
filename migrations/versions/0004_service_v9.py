"""Publication metadata and per-user confirmation state. Revert with downgrade 0003."""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = depends_on = None

COLUMNS = {
    "notices": [sa.Column("published_at", sa.String(10)),
                sa.Column("source_metadata", sa.JSON()),
                sa.Column("application_end_date", sa.String(10)),
                sa.Column("extraction_attempts", sa.Integer(), nullable=False, server_default="0")],
    "events": [sa.Column("ai_extracted", sa.Boolean(), nullable=False, server_default="0")],
    "user_events": [sa.Column("action_status", sa.String(16), nullable=False, server_default="pending"),
                    sa.Column("overrides", sa.JSON())],
}


def upgrade():
    bind = op.get_bind()
    for table, columns in COLUMNS.items():
        existing = {c["name"] for c in sa.inspect(bind).get_columns(table)}
        for column in columns:
            if column.name not in existing:
                op.add_column(table, column)
    tables = sa.inspect(bind).get_table_names()
    if "user_notices" not in tables:
        op.create_table("user_notices",
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("notice_id", sa.Integer(), sa.ForeignKey("notices.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("dismissed", sa.Boolean(), nullable=False, server_default="0"))
    if "event_reports" not in tables:
        op.create_table("event_reports",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("event_id", sa.Integer(), sa.ForeignKey("events.id", ondelete="CASCADE"), nullable=False),
            sa.Column("reason", sa.String(24), nullable=False),
            sa.Column("memo", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
        op.create_index("ix_event_reports_user_id", "event_reports", ["user_id"])
        op.create_index("ix_event_reports_event_id", "event_reports", ["event_id"])


def downgrade():
    # Newly introduced user state is removed; original notices/events/user links remain intact.
    op.drop_table("event_reports")
    op.drop_table("user_notices")
    for table, columns in reversed(list(COLUMNS.items())):
        with op.batch_alter_table(table) as batch:
            for column in reversed(columns):
                batch.drop_column(column.name)
    op.get_bind().execute(sa.text("UPDATE notices SET extraction_state='pending' WHERE extraction_state='retry_pending'"))
