"""Feedback, onboarding and retained review history. Revert with downgrade 0009."""
from alembic import op
import sqlalchemy as sa

revision = '0010'
down_revision = '0009'
branch_labels = depends_on = None


def upgrade():
    bind = op.get_bind()
    columns = {column['name'] for column in sa.inspect(bind).get_columns('users')}
    if 'onboarding_done' not in columns:
        op.add_column('users', sa.Column('onboarding_done', sa.Boolean(), nullable=False, server_default=sa.false()))
        preferences = sa.table('preferences', sa.column('user_id', sa.Integer()),
                               sa.column('major', sa.String()), sa.column('interests', sa.JSON()))
        users = sa.table('users', sa.column('id', sa.Integer()), sa.column('onboarding_done', sa.Boolean()))
        rows = bind.execute(sa.select(preferences.c.user_id, preferences.c.major, preferences.c.interests))
        for user_id, major, interests in rows:
            if (major or '').strip() or interests:
                bind.execute(users.update().where(users.c.id == user_id).values(onboarding_done=True))
    actor = next(column for column in sa.inspect(bind).get_columns('review_logs') if column['name'] == 'actor_id')
    if not actor['nullable']:
        with op.batch_alter_table('review_logs') as batch:
            batch.alter_column('actor_id', existing_type=sa.Integer(), nullable=True)
    if 'service_feedback' not in sa.inspect(bind).get_table_names():
        op.create_table('service_feedback',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
            sa.Column('type', sa.String(16), nullable=False),
            sa.Column('message', sa.Text(), nullable=False),
            sa.Column('reply_email', sa.String(255), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))


def downgrade():
    bind = op.get_bind()
    if bind.execute(sa.text('SELECT COUNT(*) FROM review_logs WHERE actor_id IS NULL')).scalar():
        raise RuntimeError('Cannot downgrade retained reviews with deleted actors to a NOT NULL actor_id')
    if 'service_feedback' in sa.inspect(bind).get_table_names():
        op.drop_table('service_feedback')
    actor = next(column for column in sa.inspect(bind).get_columns('review_logs') if column['name'] == 'actor_id')
    if actor['nullable']:
        with op.batch_alter_table('review_logs') as batch:
            batch.alter_column('actor_id', existing_type=sa.Integer(), nullable=False)
    columns = {column['name'] for column in sa.inspect(bind).get_columns('users')}
    if 'onboarding_done' in columns:
        with op.batch_alter_table('users') as batch:
            batch.drop_column('onboarding_done')
