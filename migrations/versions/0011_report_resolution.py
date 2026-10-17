"""Track administrator resolution of event reports."""
from alembic import op
import sqlalchemy as sa

revision = '0011'
down_revision = '0010'
branch_labels = depends_on = None


def upgrade():
    columns = {column['name'] for column in sa.inspect(op.get_bind()).get_columns('event_reports')}
    with op.batch_alter_table('event_reports') as batch:
        if 'resolved_at' not in columns:
            batch.add_column(sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True))
        if 'resolved_by' not in columns:
            batch.add_column(sa.Column('resolved_by', sa.Integer(), nullable=True))
            batch.create_foreign_key('fk_event_reports_resolved_by', 'users', ['resolved_by'], ['id'], ondelete='SET NULL')


def downgrade():
    inspector = sa.inspect(op.get_bind())
    columns = {column['name'] for column in inspector.get_columns('event_reports')}
    convention = {'fk': 'fk_%(table_name)s_%(column_0_name)s'}
    with op.batch_alter_table('event_reports', naming_convention=convention) as batch:
        if 'resolved_by' in columns:
            for fk in inspector.get_foreign_keys('event_reports'):
                if fk['constrained_columns'] == ['resolved_by']:
                    batch.drop_constraint(fk['name'] or 'fk_event_reports_resolved_by', type_='foreignkey')
            batch.drop_column('resolved_by')
        if 'resolved_at' in columns:
            batch.drop_column('resolved_at')
