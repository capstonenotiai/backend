from contextlib import contextmanager

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event as sql_event, inspect, select, text

from app.db import BACKEND_ROOT, Base, run_migrations
from app.models import Event, EventReport, Notice, ServiceFeedback, User, utcnow
from app.services import admin_notices
from app.services.accounts import delete_account
from test_admin_review import ORIGIN, login, payload
from test_planner_opportunities import make_event, make_notice


@contextmanager
def queries(db):
    statements = []
    def capture(conn, cursor, statement, parameters, context, many):
        statements.append(statement)
    engine = db.get_bind()
    sql_event.listen(engine, 'before_cursor_execute', capture)
    try:
        yield statements
    finally:
        sql_event.remove(engine, 'before_cursor_execute', capture)


def test_counts_and_reported_filter(client, db, monkeypatch):
    owner = login(client, db, monkeypatch)
    db.query(Notice).delete()
    for state in admin_notices.STATES:
        n = make_notice(db)
        n.extraction_state = state
        e = make_event(db, n)
        db.add(EventReport(user_id=owner.id, event_id=e.id, reason='date', resolved_at=utcnow()))
        if state == 'approved':
            db.add_all([EventReport(user_id=owner.id, event_id=e.id, reason='date') for _ in range(2)])
    db.commit()
    with queries(db) as statements:
        counts = admin_notices.counts(db)
    assert len(statements) == 2
    assert counts == {**dict.fromkeys(admin_notices.STATES, 1), 'reported': 1, 'all': 9}
    assert client.get('/api/admin/notices/counts').json() == counts
    body = client.get('/api/admin/notices?state=reported').json()
    assert body['total'] == 1 and body['items'][0]['open_report_count'] == 2


def test_list_reasons_fields_and_fixed_queries(client, db, monkeypatch):
    login(client, db, monkeypatch)
    db.query(Notice).delete()
    ids = []
    for i in range(30):
        n = make_notice(db)
        ids.append(n.id)
        n.published_at = '2030-10-01' if i else None
        make_event(db, n, ai_extracted=False, review_reason='ignored')
        make_event(db, n, ai_extracted=True, review_reason='first\nreason')
        make_event(db, n, ai_extracted=True, review_reason='second')
        if i == 0:
            n.extraction_state = 'failed'
            n.extraction_error = 'error\n' + 'x' * 300
        elif i == 1:
            n.extraction_state = 'failed'
        elif i == 2:
            for e in db.scalars(select(Event).where(Event.notice_id == n.id)):
                e.ai_extracted = False
    db.commit()
    sizes = []
    for limit in (1, 30):
        db.expunge_all()
        with queries(db) as statements:
            result = admin_notices.list_notices(db, 'all', limit, 0)
        sizes.append(len(statements))
    assert sizes == [4, 4]
    items = {item['id']: item for item in result['items']}
    assert len(items) == 30
    assert items[ids[0]]['published_at'] is None
    assert items[ids[0]]['reason'] == ('error ' + 'x' * 300)[:200]
    assert items[ids[1]]['reason'] is None
    assert items[ids[2]]['reason'] is None
    assert items[ids[3]]['reason'] == 'first reason'
    assert items[ids[3]]['published_at'] == '2030-10-01'
    assert items[ids[3]]['crawled_at'].endswith('+00:00')
    response = client.get('/api/admin/notices?state=all&limit=30')
    assert response.status_code == 200 and response.json() == result


@pytest.mark.parametrize('action', ['approve', 'reject', 'save'])
def test_review_report_resolution(client, db, monkeypatch, action):
    owner = login(client, db, monkeypatch)
    n = make_notice(db)
    e = make_event(db, n)
    e2 = make_event(db, n)
    other = make_event(db, make_notice(db))
    old_time = utcnow()
    records = [EventReport(user_id=owner.id, event_id=x.id, reason='date') for x in (e, e2, other)]
    closed = EventReport(user_id=owner.id, event_id=e.id, reason='date', resolved_at=old_time)
    db.add_all([*records, closed]); db.commit()
    path = f'/api/admin/notices/{n.id}'
    response = client.post(path + '/review', json=payload(action=action), headers=ORIGIN)
    assert response.status_code == 200
    assert response.json()['resolved_reports'] == (0 if action == 'save' else 2)
    db.expire_all()
    for report in records[:2]:
        assert bool(report.resolved_at) == (action != 'save')
        assert report.resolved_by == (owner.id if action != 'save' else None)
    assert records[2].resolved_at is None and closed.resolved_by is None
    assert closed.resolved_at.replace(tzinfo=old_time.tzinfo) == old_time
    detail = client.get(path).json()
    assert all(r['created_at'] for r in detail['reports'])
    assert bool(detail['reports'][0]['resolved_at']) == (action != 'save')
    mine = {r['id']: r for r in client.get('/api/reports/mine').json()}
    assert mine[records[0].id]['status'] == ('received' if action == 'save' else 'resolved')
    assert mine[records[2].id]['status'] == 'received'
    assert client.post(path + '/review', json=payload(), headers=ORIGIN).status_code == 409
    assert client.get('/api/admin/notices/counts').json()['reported'] == (2 if action == 'save' else 1)


def test_resolver_account_deletion_preserves_reports(client, db, monkeypatch):
    owner = login(client, db, monkeypatch)
    reporter = User(email='reporter@local', name='reporter')
    db.add(reporter); db.flush()
    e = make_event(db, make_notice(db))
    r = EventReport(user_id=reporter.id, event_id=e.id, reason='date', resolved_at=utcnow(), resolved_by=owner.id)
    db.add(r); db.commit()
    report_id = r.id
    delete_account(db, owner)
    db.expire_all()
    retained = db.get(EventReport, report_id)
    assert retained.resolved_at is not None and retained.resolved_by is None


def test_processing_review_keeps_reports_open(client, db, monkeypatch):
    owner = login(client, db, monkeypatch)
    n = make_notice(db)
    n.extraction_state = 'processing'
    e = make_event(db, n)
    report = EventReport(user_id=owner.id, event_id=e.id, reason='date')
    db.add(report); db.commit()
    assert client.post(f'/api/admin/notices/{n.id}/review', json=payload(), headers=ORIGIN).status_code == 409
    db.refresh(report)
    assert report.resolved_at is None and report.resolved_by is None


@pytest.mark.parametrize('kind', ['inconvenience', 'suggestion', 'praise', 'other'])
def test_feedback_type_counts(client, db, monkeypatch, kind):
    owner = login(client, db, monkeypatch)
    for category, count in [('inconvenience', 1), ('suggestion', 2), ('praise', 3), ('other', 4)]:
        db.add_all([ServiceFeedback(user_id=owner.id, type=category, message='opinion') for _ in range(count)])
    db.commit()
    counts = {'all': 10, 'inconvenience': 1, 'suggestion': 2, 'praise': 3, 'other': 4}
    body = client.get('/api/admin/feedback', params={'type': kind, 'limit': 1}).json()
    assert body['counts'] == counts and body['total'] == counts[kind]
    assert len(body['items']) == 1 and body['items'][0]['type'] == kind
    all_body = client.get('/api/admin/feedback').json()
    assert all_body['counts'] == counts and all_body['total'] == 10
    assert client.get('/api/admin/feedback?type=invalid').status_code == 400
    assert client.get('/api/admin/feedback?type=all').status_code == 400
    assert client.get('/api/admin/feedback?type=').status_code == 400


@pytest.mark.parametrize('path', ['/api/admin/notices/counts', '/api/admin/notices?state=reported', '/api/admin/feedback?type=praise'])
def test_regular_user_forbidden(client, db, monkeypatch, path):
    login(client, db, monkeypatch, admin=False)
    assert client.get(path).status_code == 403


def test_0011_migration_roundtrip_and_existing_schema(tmp_path):
    engine = create_engine(f'sqlite:///{tmp_path / "b17.db"}')
    with engine.begin() as conn:
        run_migrations(conn, '0010')
        conn.execute(text("INSERT INTO users(id,email,name,created_at) VALUES (1,'local@test','user','2030-01-01')"))
        conn.execute(text("INSERT INTO notices(id,site,source_url,title_raw,raw_text,crawled_at) VALUES (1,'cbnu','https://example.com','title','','2030-01-01')"))
        conn.execute(Event.__table__.insert().values(id=1, notice_id=1, source='cbnu', title='event'))
        conn.execute(text("INSERT INTO event_reports(id,user_id,event_id,reason,memo,created_at) VALUES (1,1,1,'date','','2030-01-01')"))
        run_migrations(conn)
        assert conn.execute(text('SELECT resolved_at,resolved_by FROM event_reports')).one() == (None, None)
        fk = next(f for f in inspect(conn).get_foreign_keys('event_reports') if f['constrained_columns'] == ['resolved_by'])
        assert fk['options']['ondelete'] == 'SET NULL'
        conn.execute(text("UPDATE event_reports SET resolved_at='2030-01-02',resolved_by=1"))
        config = Config(); config.set_main_option('script_location', str(BACKEND_ROOT / 'migrations'))
        config.attributes['connection'] = conn
        command.stamp(config, '0010')
        run_migrations(conn)
        assert conn.execute(text('SELECT resolved_by FROM event_reports')).scalar() == 1
        command.downgrade(config, '0010')
        assert not {'resolved_at', 'resolved_by'} & {c['name'] for c in inspect(conn).get_columns('event_reports')}
        assert conn.execute(text('SELECT count(*) FROM event_reports')).scalar() == 1
        run_migrations(conn)
        assert conn.execute(text('SELECT version_num FROM alembic_version')).scalar() == '0011'
    engine.dispose()


def test_0011_existing_orm_schema_and_resolver_foreign_key(tmp_path):
    engine = create_engine(f'sqlite:///{tmp_path / "b17-existing.db"}')
    with engine.begin() as conn:
        conn.execute(text('PRAGMA foreign_keys=ON'))
        Base.metadata.create_all(conn)
        run_migrations(conn)
        conn.execute(User.__table__.insert().values(id=1, email='reporter@local', name='reporter'))
        conn.execute(User.__table__.insert().values(id=2, email='resolver@local', name='resolver'))
        conn.execute(Event.__table__.insert().values(id=1, source='cbnu', title='event'))
        conn.execute(EventReport.__table__.insert().values(id=1, user_id=1, event_id=1, reason='date', resolved_by=2, resolved_at=utcnow()))
        conn.execute(text('DELETE FROM users WHERE id=2'))
        assert conn.execute(text('SELECT resolved_by FROM event_reports')).scalar() is None
        assert conn.execute(text('SELECT resolved_at FROM event_reports')).scalar() is not None
        config = Config(); config.set_main_option('script_location', str(BACKEND_ROOT / 'migrations'))
        config.attributes['connection'] = conn
        command.downgrade(config, '0010')
        assert conn.execute(text('SELECT count(*) FROM event_reports')).scalar() == 1
        assert not {'resolved_at', 'resolved_by'} & {c['name'] for c in inspect(conn).get_columns('event_reports')}
    engine.dispose()
