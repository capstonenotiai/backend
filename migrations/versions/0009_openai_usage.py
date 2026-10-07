"""OpenAI token usage log. Revert with downgrade 0008."""
from alembic import op
import sqlalchemy as sa

revision = "0009"
down_revision = "0008"
branch_labels = depends_on = None


def upgrade():
    if "openai_usage" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table("openai_usage",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_openai_usage_created_at", "openai_usage", ["created_at"])


def downgrade():
    op.drop_table("openai_usage")
