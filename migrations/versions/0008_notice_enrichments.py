"""Evidence-validated notice enrichment. Revert with downgrade 0007."""
from alembic import op
import sqlalchemy as sa

revision = '0008'
down_revision = '0007'
branch_labels = depends_on = None


def upgrade():
    if 'notice_enrichments' in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table('notice_enrichments',
        sa.Column('notice_id', sa.Integer(), sa.ForeignKey('notices.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('state', sa.String(24), nullable=False, server_default='pending'),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('next_attempt_at', sa.DateTime(timezone=True)),
        sa.Column('error', sa.Text()),
        sa.Column('prompt_version', sa.String(64), nullable=False),
        sa.Column('raw', sa.JSON()),
        sa.Column('facts', sa.JSON(), nullable=False),
        sa.Column('notice_kind', sa.String(16), nullable=False, server_default='normal'),
        sa.Column('grouping_review_required', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('grouping_review_reason', sa.Text(), nullable=False, server_default=''),
        sa.Column('enrichment_status', sa.String(24), nullable=False, server_default='ok'),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False))


def downgrade():
    if 'notice_enrichments' in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table('notice_enrichments')
