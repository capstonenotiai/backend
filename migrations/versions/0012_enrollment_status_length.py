"""Widen preferences.enrollment_status: 'expected_graduation' (19 chars) did not fit String(16)."""
from alembic import op
import sqlalchemy as sa

revision = '0012'
down_revision = '0011'
branch_labels = depends_on = None


def upgrade():
    with op.batch_alter_table('preferences') as batch:
        batch.alter_column('enrollment_status', existing_type=sa.String(16), type_=sa.String(32),
                           existing_nullable=False, existing_server_default='unknown')


def downgrade():
    with op.batch_alter_table('preferences') as batch:
        batch.alter_column('enrollment_status', existing_type=sa.String(32), type_=sa.String(16),
                           existing_nullable=False, existing_server_default='unknown')
