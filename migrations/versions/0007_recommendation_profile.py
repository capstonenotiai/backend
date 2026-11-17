"""Recommendation profile fields. Revert with downgrade 0006."""
from alembic import op
import sqlalchemy as sa

revision = '0007'
down_revision = '0006'
branch_labels = depends_on = None


def upgrade():
    existing = {column['name'] for column in sa.inspect(op.get_bind()).get_columns('preferences')}
    for column in (
        sa.Column('major', sa.String(255), nullable=False, server_default=''),
        sa.Column('grade', sa.Integer(), nullable=True),
        sa.Column('enrollment_status', sa.String(16), nullable=False, server_default='unknown'),
    ):
        if column.name not in existing:
            op.add_column('preferences', column)


def downgrade():
    existing = {column['name'] for column in sa.inspect(op.get_bind()).get_columns('preferences')}
    with op.batch_alter_table('preferences') as batch:
        for name in ('enrollment_status', 'grade', 'major'):
            if name in existing:
                batch.drop_column(name)
