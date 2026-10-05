"""Multiple events, review audit and user calendar revisions."""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = depends_on = None

def upgrade():
    bind = op.get_bind()
    # Also tolerate an unversioned database created from current metadata.
    def missing(table, name):
        return name not in {c['name'] for c in sa.inspect(bind).get_columns(table)}
    notice_cols = [sa.Column('extraction_state', sa.String(24), nullable=False, server_default='pending'),
                   sa.Column('extraction_result', sa.JSON()), sa.Column('extraction_error', sa.Text()),
                   sa.Column('revision', sa.Integer(), nullable=False, server_default='0')]
    for col in notice_cols:
        if missing('notices', col.name): op.add_column('notices', col)
    uniques = sa.inspect(bind).get_unique_constraints('events')
    # MySQL needs a replacement index for the notice foreign key before dropping its unique index.
    if 'ix_events_notice_id' not in {i['name'] for i in sa.inspect(bind).get_indexes('events')}:
        op.create_index('ix_events_notice_id','events',['notice_id'],unique=False)
    with op.batch_alter_table('events', naming_convention={'uq':'uq_%(table_name)s_%(column_0_name)s'}) as batch:
        for constraint in uniques:
            if constraint['column_names'] == ['notice_id']:
                batch.drop_constraint(constraint['name'] or 'uq_events_notice_id', type_='unique')
        for name, size, default in [('event_type',24,'event'),('start_time',5,''),('end_time',5,''),
                ('timezone',64,'Asia/Seoul'),('attendance_mode',24,'unknown'),('schedule_status',24,'confirmed')]:
            if missing('events',name): batch.add_column(sa.Column(name,sa.String(size),nullable=False,server_default=default))
        if missing('events','revision'): batch.add_column(sa.Column('revision',sa.Integer(),nullable=False,server_default='1'))
        if missing('events','extraction_metadata'): batch.add_column(sa.Column('extraction_metadata',sa.JSON()))
    for col in [sa.Column('synced_revision',sa.Integer(),nullable=False,server_default='0'),
                sa.Column('sync_status',sa.String(24),nullable=False,server_default='none'),
                sa.Column('sync_error',sa.Text())]:
        if missing('user_events',col.name): op.add_column('user_events',col)
    if 'review_logs' not in sa.inspect(bind).get_table_names():
        op.create_table('review_logs',sa.Column('id',sa.Integer(),primary_key=True),
            sa.Column('notice_id',sa.Integer(),sa.ForeignKey('notices.id'),nullable=False),
            sa.Column('actor_id',sa.Integer(),sa.ForeignKey('users.id'),nullable=False),
            sa.Column('action',sa.String(24),nullable=False),sa.Column('reason',sa.Text(),nullable=False),
            sa.Column('before',sa.JSON(),nullable=False),sa.Column('after',sa.JSON(),nullable=False),
            sa.Column('created_at',sa.DateTime(timezone=True),nullable=False))
        op.create_index('ix_review_logs_notice_id','review_logs',['notice_id'])
    bind.execute(sa.text("UPDATE notices SET extraction_state='extracted' WHERE EXISTS (SELECT 1 FROM events WHERE events.notice_id=notices.id) AND extraction_state='pending'"))
    bind.execute(sa.text("UPDATE user_events SET synced_revision=1, sync_status='synced' WHERE registered=1 AND sync_status='none'"))

def downgrade():
    raise RuntimeError('Review history and multiple events cannot be safely downgraded automatically. Restore a verified backup.')
