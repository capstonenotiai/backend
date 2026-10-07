from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.deps import get_or_create_dev_user
from app.models import Event, Notice, Notification, Preference, User, UserEvent, UserNotice
from app.services import notifications
from app.services.notifications import generate_notifications, send_pending_emails

SEOUL = ZoneInfo('Asia/Seoul')
NOW = datetime(2030, 10, 6, 9, tzinfo=SEOUL)  # 일요일 09:00


def test_notification_scheduler_uses_seoul_nine_am(monkeypatch):
    from types import SimpleNamespace
    from app.config import Settings
    from app import scheduler

    settings = Settings(_env_file=None, crawl_enabled=False, notify_enabled=True)
    jobs = []
    fake = SimpleNamespace(add_job=lambda func, trigger, **kw: jobs.append((func, trigger, kw)), start=lambda: None)
    monkeypatch.setattr(scheduler, 'get_settings', lambda: settings)
    monkeypatch.setattr(scheduler, 'BackgroundScheduler', lambda **kw: fake)
    assert scheduler.start_scheduler() is fake
    assert len(jobs) == 1
    func, trigger, options = jobs[0]
    assert func is scheduler.run_notify_job and options['id'] == 'notify'
    assert str(trigger.timezone) == 'Asia/Seoul'
    assert trigger.get_next_fire_time(None, NOW.replace(hour=8)) == NOW
    # 서버가 09시에 꺼졌다면 당일 다음 실행에서 채우며 생성의 중복 방지는 별도 테스트한다.
    assert trigger.get_next_fire_time(NOW, NOW) == NOW.replace(hour=10)
    assert options['max_instances'] == 1 and options['coalesce'] is True


def make_event(db, kind='application', start='2030-10-01', end='2030-10-09', title=None, **kwargs):
    notice = Notice(site='cbnu', source_url=f'https://example.com/n/{kind}/{end}/{title}', title_raw='SW 공모전 안내')
    db.add(notice); db.flush()
    event = Event(notice_id=notice.id, source='cbnu', title=title or f'{kind} 일정', event_type=kind,
                  start_date=start, end_date=end, review_status='auto', **kwargs)
    db.add(event); db.flush()
    return event


def follow(db, user, event, **state):
    db.add(UserEvent(user_id=user.id, event_id=event.id, **state)); db.commit()


def made(db):
    return [(n.event_id, n.kind, n.target_date) for n in db.scalars(select(Notification).order_by(Notification.id))]


def test_bookmarked_deadline_d3_and_d1_once_each(db):
    user = get_or_create_dev_user(db)
    event = make_event(db, end_time='18:00')
    follow(db, user, event, bookmarked=True)
    assert generate_notifications(db, NOW) == 1
    assert generate_notifications(db, NOW.replace(hour=15)) == 0  # 같은 날 다시 실행해도 중복 없음
    assert generate_notifications(db, datetime(2030, 10, 7, 9, tzinfo=SEOUL)) == 0  # D-2
    assert generate_notifications(db, datetime(2030, 10, 8, 9, tzinfo=SEOUL)) == 1
    assert made(db) == [(event.id, 'deadline_d3', '2030-10-09'), (event.id, 'deadline_d1', '2030-10-09')]
    first = db.scalar(select(Notification).order_by(Notification.id))
    assert first.title == '[마감 3일 후] application 일정'
    assert first.message == 'SW 공모전 안내\n10월 9일(수) 18:00까지 접수 마감입니다.'


def test_unfollowed_and_main_events_need_registration(db):
    user = get_or_create_dev_user(db)
    application = make_event(db)
    main = make_event(db, kind='event', start='2030-10-07', end='2030-10-07', location='강당')
    db.commit()
    assert generate_notifications(db, NOW) == 0
    follow(db, user, main, bookmarked=True)
    assert generate_notifications(db, NOW) == 0  # 본행사는 관심 저장만으로는 알리지 않음
    db.scalar(select(UserEvent).where(UserEvent.event_id == main.id)).registered = True
    db.commit()
    assert generate_notifications(db, NOW) == 1
    assert made(db) == [(main.id, 'event_d1', '2030-10-07')]
    assert application.id not in {row[0] for row in made(db)}


@pytest.mark.parametrize('case', ['done', 'dismissed', 'review_required', 'cancelled', 'pref_off', 'source_off'])
def test_excluded_schedules(db, case):
    user = get_or_create_dev_user(db)
    extra = {}
    if case == 'review_required':
        extra = {'ai_extracted': True, 'review_reason': '사이트 접수 마감일과 AI 마감일이 다릅니다.'}
    if case == 'cancelled':
        extra = {'schedule_status': 'cancelled'}
    event = make_event(db, **extra)
    follow(db, user, event, bookmarked=True, action_status='done' if case == 'done' else 'pending')
    if case == 'dismissed':
        db.add(UserNotice(user_id=user.id, notice_id=event.notice_id, dismissed=True))
    if case in ('pref_off', 'source_off'):
        db.add(Preference(user_id=user.id, ai_mode='study', interests=[],
                          enabled_sources={'cbnu': case != 'source_off'},
                          notifications={'d3': case != 'pref_off', 'd1': True, 'email': True}))
    db.commit()
    assert generate_notifications(db, NOW) == 0


def test_review_required_can_be_enabled_later(db, monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), 'notify_review_required', True)
    user = get_or_create_dev_user(db)
    event = make_event(db, ai_extracted=True, review_reason='사이트 접수 마감일과 AI 마감일이 다릅니다.')
    follow(db, user, event, bookmarked=True)
    assert generate_notifications(db, NOW) == 1


def test_user_override_moves_the_notification_date(db):
    user = get_or_create_dev_user(db)
    event = make_event(db)
    follow(db, user, event, bookmarked=True, overrides={'end_date': '2030-10-07'})
    assert generate_notifications(db, NOW) == 1
    assert made(db) == [(event.id, 'deadline_d1', '2030-10-07')]


def test_inbox_api(client, db):
    user = get_or_create_dev_user(db)
    other = User(google_sub='other', email='other@example.com', name='다른 사용자')
    db.add(other); db.flush()
    event = make_event(db)
    for owner, target in ((user, '2030-10-01'), (user, '2030-10-02'), (other, '2030-10-03')):
        db.add(Notification(user_id=owner.id, event_id=event.id, kind='deadline_d3', target_date=target,
                            title='알림', message=''))
        db.flush()
    db.commit()
    mine = [row.id for row in db.scalars(select(Notification).where(Notification.user_id == user.id)
                                         .order_by(Notification.id))]
    theirs = db.scalar(select(Notification.id).where(Notification.user_id == other.id))

    assert client.get('/api/notifications/unread-count').json() == {'count': 2}
    page = client.get('/api/notifications', params={'limit': 1}).json()
    assert [item['id'] for item in page['items']] == [str(mine[1])] and page['next_before'] == str(mine[1])
    rest = client.get('/api/notifications', params={'limit': 1, 'before': page['next_before']}).json()
    assert [item['id'] for item in rest['items']] == [str(mine[0])] and rest['next_before'] is None
    assert rest['items'][0]['read'] is False and rest['items'][0]['event_id'] == str(event.id)

    assert client.post(f'/api/notifications/{theirs}/read').status_code == 404
    assert client.post(f'/api/notifications/{mine[0]}/read').json() == {'id': str(mine[0]), 'read': True}
    assert client.get('/api/notifications/unread-count').json() == {'count': 1}
    assert client.post('/api/notifications/read-all').json() == {'updated': 1}
    assert client.get('/api/notifications/unread-count').json() == {'count': 0}


def test_preferences_expose_notification_settings(client):
    body = client.get('/api/user/preferences').json()
    assert body['notifications'] == {'d3': True, 'd1': True, 'email': True}
    body['notifications'] = {'d3': False, 'd1': True, 'email': False, 'd7': True}  # 예전 키는 무시
    saved = client.put('/api/user/preferences', json=body).json()
    assert saved['notifications'] == {'d3': False, 'd1': True, 'email': False}


@pytest.fixture()
def outbox(monkeypatch):
    sent = []
    monkeypatch.setattr(notifications, 'send_email', lambda to, subject, body: sent.append((to, subject, body)))
    return sent


def real_user(db, email='student@example.com', **notification_prefs):
    user = User(google_sub=f'g-{email}', email=email, name='학생')
    db.add(user); db.flush()
    if notification_prefs:
        db.add(Preference(user_id=user.id, ai_mode='study', interests=[], enabled_sources={},
                          notifications={'d3': True, 'd1': True, 'email': True, **notification_prefs}))
    return user


def test_one_digest_email_per_user(db, outbox):
    user = real_user(db)
    first, second = make_event(db, title='접수 A'), make_event(db, title='접수 B')
    follow(db, user, first, bookmarked=True); follow(db, user, second, bookmarked=True)
    dev = get_or_create_dev_user(db)
    follow(db, dev, first, bookmarked=True)
    assert generate_notifications(db, NOW) == 3
    assert send_pending_emails(db, NOW) == 1
    assert [(to, subject) for to, subject, _ in outbox] == [('student@example.com', '[NotiAI] 다가오는 일정 2건')]
    assert '접수 A' in outbox[0][2] and '접수 B' in outbox[0][2]
    statuses = {n.user_id: n.email_status for n in db.scalars(select(Notification))}
    assert statuses == {user.id: 'sent', dev.id: 'skipped'}  # 개발용 사용자는 실제 메일 주소가 없음
    assert send_pending_emails(db, NOW) == 0 and len(outbox) == 1


def test_email_opt_out_keeps_inbox(db, outbox):
    user = real_user(db, email=False)
    event = make_event(db)
    follow(db, user, event, bookmarked=True)
    assert generate_notifications(db, NOW) == 1
    assert send_pending_emails(db, NOW) == 0 and outbox == []
    assert db.scalar(select(Notification)).email_status == 'skipped'


def test_email_failure_retries_then_fails_and_stale_is_skipped(db, monkeypatch):
    from app.services.email import EmailError

    def broken(*args):
        raise EmailError('SMTP 발송 실패: SMTPAuthenticationError')

    monkeypatch.setattr(notifications, 'send_email', broken)
    user = real_user(db)
    event = make_event(db)
    follow(db, user, event, bookmarked=True)
    generate_notifications(db, NOW)
    item = db.scalar(select(Notification))
    for attempt in (1, 2):
        send_pending_emails(db, NOW)
        assert item.email_status == 'pending' and item.email_attempts == attempt
    send_pending_emails(db, NOW)
    assert item.email_status == 'failed' and 'SMTPAuthenticationError' in item.email_error

    late = make_event(db, title='늦은 일정')
    follow(db, user, late, bookmarked=True)
    generate_notifications(db, NOW)
    late_item = db.scalar(select(Notification).where(Notification.event_id == late.id))
    send_pending_emails(db, datetime(2030, 10, 10, 9, tzinfo=SEOUL))  # 마감이 지난 뒤
    assert late_item.email_status == 'skipped'


def test_smtp_backend_requires_credentials(monkeypatch):
    from app.config import get_settings
    from app.services.email import EmailError, send_email
    monkeypatch.setattr(get_settings(), 'email_backend', 'smtp')
    monkeypatch.setattr(get_settings(), 'smtp_password', '')
    with pytest.raises(EmailError):
        send_email('a@example.com', 'subject', 'body')
