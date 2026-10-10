from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, inspect, select, text

from app.deps import get_or_create_dev_user
from app.models import Event, Notice, Preference, User, UserEvent, UserNotice
from app.services.planner import MODE_PROMPTS, MODE_ALIASES, DEFAULT_MODE
from app.services.planner_facts import SEOUL, derived_event_facts, opportunity_facts, pairwise_conflicts

NOW = datetime(2030, 10, 8, 12, tzinfo=SEOUL)


def make_notice(db):
    notice = Notice(site='cbnu', title_raw='활동', source_url=f'https://example.com/{len(db.new)}-{db.query(Notice).count()}')
    db.add(notice)
    db.flush()
    return notice


def make_event(db, notice=None, **values):
    data = dict(source='cbnu', title='일정', detail='', source_url='https://example.com/event',
        start_date='2030-10-08', end_date='2030-10-08', start_time='13:00', end_time='14:00',
        event_type='event', category='학사', review_status='auto')
    data.update(values)
    event = Event(notice_id=notice.id if notice else None, **data)
    db.add(event)
    db.flush()
    return event


def test_profile_defaults_roundtrip_and_preferences_preserve_profile(client, db):
    profile = client.get('/api/user').json()
    assert (profile['major'], profile['grade'], profile['enrollment_status']) == ('', None, 'unknown')
    for status in ('enrolled', 'leave', 'graduated', 'unknown'):
        response = client.put('/api/user/profile', json={'major': '소프트웨어학부', 'grade': 3, 'enrollment_status': status})
        assert response.status_code == 200
        assert response.json()['grade'] == 3 and response.json()['enrollment_status'] == status
    prefs = client.get('/api/user/preferences').json()
    prefs['ai_mode'] = 'focus'
    assert client.put('/api/user/preferences', json=prefs).status_code == 200
    assert client.get('/api/user').json()['major'] == '소프트웨어학부'
    user = get_or_create_dev_user(db)
    db.expire_all()
    assert db.get(Preference, user.id).grade == 3
    assert client.put('/api/user/profile', json={'major': '', 'grade': None, 'enrollment_status': 'unknown'}).json()['grade'] is None


@pytest.mark.parametrize('patch', [{'grade': 0}, {'grade': 7}, {'grade': True}, {'grade': 2.5},
    {'grade': '3'}, {'enrollment_status': 'invalid'}, {'major': None}, {'major': 123}, {'major': 'x' * 256}])
def test_profile_validation(client, patch):
    assert client.put('/api/user/profile', json=patch).status_code == 400
    assert client.get('/api/user').json()['grade'] is None


@pytest.mark.parametrize('grade', [1, 6])
def test_profile_grade_bounds(client, grade):
    assert client.put('/api/user/profile', json={'grade': grade}).json()['grade'] == grade


@pytest.mark.parametrize('mode', ['study', 'explorer', 'balanced', 'removed'])
def test_old_stored_modes_read_as_priority(client, db, mode):
    user = get_or_create_dev_user(db)
    client.get('/api/user/preferences')
    pref = db.get(Preference, user.id)
    pref.ai_mode = mode
    db.commit()
    assert client.get('/api/user/preferences').json()['ai_mode'] == 'priority'
    db.refresh(pref)
    assert pref.ai_mode == mode


@pytest.mark.parametrize('mode', ['priority', 'discover', 'focus', 'study', 'explorer', 'balanced', 'unknown'])
def test_chat_mode_compatibility(client, monkeypatch, mode):
    calls = []
    monkeypatch.setattr('app.services.planner.create_reply', lambda instructions, messages: calls.append((instructions, messages)) or '응답')
    response = client.post('/api/planner/chat', json={'message': '내 일정', 'mode': mode})
    assert response.status_code == 200 and response.json() == {'reply': '응답'}
    expected = mode if mode in MODE_PROMPTS else MODE_ALIASES.get(mode, DEFAULT_MODE)
    assert MODE_PROMPTS[expected] in calls[0][0]
    assert calls[0][1] == [{'role': 'user', 'content': '내 일정'}]
    if mode in ('priority', 'discover', 'focus'):
        assert client.put('/api/user/preferences', json={'ai_mode': mode}).json()['ai_mode'] == mode


@pytest.mark.parametrize('kind,action,day', [('application', 'apply', '2030-10-10'),
    ('submission', 'submit', '2030-10-10'), ('event', 'attend', '2030-10-09'),
    ('interview', 'attend', '2030-10-09'), ('unknown', 'unknown', '2030-10-10')])
def test_action_mapping(db, kind, action, day):
    event = make_event(db, event_type=kind, start_date='2030-10-09', end_date='2030-10-10')
    facts = derived_event_facts(event, now=NOW)
    assert facts['action_type'] == action and facts['action_date'] == day


@pytest.mark.parametrize('days,expected', [(-1, 'none'), (0, 'urgent'), (2, 'urgent'), (3, 'soon'),
    (7, 'soon'), (8, 'upcoming'), (14, 'upcoming'), (15, 'later')])
def test_urgency_boundaries(db, days, expected):
    day = (NOW.date() + timedelta(days=days)).isoformat()
    event = make_event(db, start_date=day, end_date=day)
    assert derived_event_facts(event, now=NOW)['urgency'] == expected


@pytest.mark.parametrize('kind', ['application', 'submission'])
def test_application_window_start_missing_and_timed_deadline(db, kind):
    event = make_event(db, event_type=kind, start_time='', end_time='18:00', end_date='2030-10-09')
    assert derived_event_facts(event, now=NOW)['action_window'] == 'open'
    event.start_date = '2030-10-09'
    assert derived_event_facts(event, now=NOW)['action_window'] == 'not_open'
    event.start_date = ''
    assert derived_event_facts(event, now=NOW)['action_window'] == 'open'
    at_end = datetime(2030, 10, 9, 18, tzinfo=SEOUL)
    assert derived_event_facts(event, now=at_end)['expired'] is False
    after = derived_event_facts(event, now=at_end + timedelta(seconds=1))
    assert after['expired'] is True and after['action_window'] == 'unknown'
    event.end_time = ''
    assert derived_event_facts(event, now=datetime(2030, 10, 9, 23, 59, 59, tzinfo=SEOUL))['expired'] is False
    event.end_date = ''
    facts = derived_event_facts(event, now=NOW)
    assert facts['action_window'] == 'unknown' and facts['urgency'] == 'none' and facts['expired'] is None


def test_attend_fallback_missing_dates_review_and_seoul_day(db):
    event = make_event(db, start_date='', end_time='', end_date='2030-10-09', ai_extracted=True, review_reason='날짜 확인 필요')
    facts = derived_event_facts(event, now=datetime(2030, 10, 8, 16, tzinfo=timezone.utc))
    assert facts['action_date'] == '2030-10-09' and facts['action_window'] == 'open'
    assert facts['urgency'] == 'urgent' and facts['review_status'] == 'needs_review'
    event.ai_extracted = False
    event.end_date = ''
    facts = derived_event_facts(event, now=NOW)
    assert facts['action_date'] is None and facts['action_window'] == 'unknown' and facts['review_status'] == 'ok'
    with pytest.raises(ValueError):
        derived_event_facts(event, now=NOW.replace(tzinfo=None))


def test_opportunity_states_visible_events_and_expired_application(db):
    user = get_or_create_dev_user(db)
    notice = make_notice(db)
    application = make_event(db, notice, event_type='application', end_date='2030-10-07', start_date='', end_time='')
    main = make_event(db, notice)
    hidden = make_event(db, notice, event_type='result')
    make_event(db, notice, review_status='needs_review')
    db.add_all([UserEvent(user_id=user.id, event_id=main.id, bookmarked=True),
        UserNotice(user_id=user.id, notice_id=notice.id, dismissed=True)])
    db.commit()
    facts = opportunity_facts(db, user, notice, NOW)
    assert facts['opportunity_id'] == f'o{notice.id}' and facts['title'] == notice.title_raw
    assert facts['source_url'] == notice.source_url and facts['category'] == '학사'
    assert facts['dismissed'] and facts['user_managed'] and facts['application_expired_not_done']
    assert {e['event_id'] for e in facts['events']} == {f'e{application.id}', f'e{main.id}'}
    assert all(e['action_status'] == 'pending' for e in facts['events'])
    db.add(UserEvent(user_id=user.id, event_id=application.id, action_status='done'))
    db.commit()
    assert not opportunity_facts(db, user, notice, NOW)['application_expired_not_done']
    # Registration alone also marks a notice as managed, including hidden child state.
    db.scalar(select(UserEvent).where(UserEvent.event_id == main.id)).bookmarked = False
    db.add(UserEvent(user_id=user.id, event_id=hidden.id, registered=True))
    db.commit()
    assert opportunity_facts(db, user, notice, NOW)['user_managed']


def test_application_expiration_requires_all_and_no_done(db):
    user = get_or_create_dev_user(db)
    notice = make_notice(db)
    make_event(db, notice, event_type='application', end_date='2030-10-07', start_date='')
    second = make_event(db, notice, event_type='submission', end_date='2030-10-09')
    db.commit()
    assert not opportunity_facts(db, user, notice, NOW)['application_expired_not_done']
    second.end_date = ''
    db.commit()
    assert not opportunity_facts(db, user, notice, NOW)['application_expired_not_done']


def test_conflicts_registered_attend_only_overrides_self_and_other_users(db):
    user = get_or_create_dev_user(db)
    notice = make_notice(db)
    main = make_event(db, notice)
    application = make_event(db, event_type='application', start_time='', end_time='')
    db.add_all([UserEvent(user_id=user.id, event_id=main.id, registered=True),
        UserEvent(user_id=user.id, event_id=application.id, registered=True)])
    other_user = User(name='other', email='other@local')
    db.add(other_user)
    db.flush()
    other_main = make_event(db)
    db.add(UserEvent(user_id=other_user.id, event_id=other_main.id, registered=True))
    db.commit()
    assert opportunity_facts(db, user, notice, NOW)['calendar_conflict'] is False
    other = make_event(db, event_type='interview', start_time='14:00', end_time='15:00')
    state = UserEvent(user_id=user.id, event_id=other.id, registered=True)
    db.add(state)
    db.commit()
    assert opportunity_facts(db, user, notice, NOW)['calendar_conflict'] is False
    state.overrides = {'start_time': '13:30'}
    db.commit()
    assert opportunity_facts(db, user, notice, NOW)['calendar_conflict'] is True
    state.overrides = {'start_date': '', 'end_date': ''}
    db.commit()
    assert opportunity_facts(db, user, notice, NOW)['calendar_conflict'] is None
    known = make_event(db)
    db.add(UserEvent(user_id=user.id, event_id=known.id, registered=True))
    db.commit()
    assert opportunity_facts(db, user, notice, NOW)['calendar_conflict'] is True


def test_pairwise_conflicts_effective_dates_and_application_exclusion(db):
    user = get_or_create_dev_user(db)
    notices = [make_notice(db) for _ in range(4)]
    first = make_event(db, notices[0])
    make_event(db, notices[1], start_time='13:30', end_time='14:30')
    make_event(db, notices[2], start_time='14:30', end_time='15:30')
    make_event(db, notices[3], event_type='application', start_time='', end_time='')
    db.commit()
    facts = [opportunity_facts(db, user, n, NOW) for n in notices]
    assert pairwise_conflicts(facts) == [[f'o{notices[0].id}', f'o{notices[1].id}']]
    assert facts[3]['calendar_conflict'] is None
    db.add(UserEvent(user_id=user.id, event_id=first.id, overrides={'start_date': '2030-10-09', 'end_date': '2030-10-09'}))
    db.commit()
    facts = [opportunity_facts(db, user, n, NOW) for n in notices]
    assert facts[0]['events'][0]['action_date'] == '2030-10-09'
    assert pairwise_conflicts(facts) == []
    assert pairwise_conflicts([]) == []


def test_application_override_updates_all_derived_values(db):
    user = get_or_create_dev_user(db)
    notice = make_notice(db)
    application = make_event(db, notice, event_type='application', start_date='2030-10-01',
        end_date='2030-10-07', start_time='', end_time='')
    db.add(UserEvent(user_id=user.id, event_id=application.id,
        overrides={'start_date': '2030-10-09', 'end_date': '2030-10-23'}))
    db.commit()
    facts = opportunity_facts(db, user, notice, NOW)
    event = facts['events'][0]
    assert event['action_date'] == '2030-10-23' and event['urgency'] == 'later'
    assert event['action_window'] == 'not_open' and event['expired'] is False
    assert facts['application_expired_not_done'] is False
    assert application.end_date == '2030-10-07'


def test_pairwise_unknown_intervals_and_all_day_overlap(db):
    user = get_or_create_dev_user(db)
    notices = [make_notice(db) for _ in range(3)]
    missing = make_event(db, notices[0], start_date='', end_date='', start_time='', end_time='')
    make_event(db, notices[1], start_time='', end_time='')
    make_event(db, notices[2])
    db.commit()
    facts = [opportunity_facts(db, user, n, NOW) for n in notices]
    assert facts[0]['calendar_conflict'] is None
    assert facts[1]['application_expired_not_done'] is False
    assert pairwise_conflicts(facts) == [[f'o{notices[1].id}', f'o{notices[2].id}']]


def test_opportunities_api_visibility_source_filter_and_user_isolation(client, db):
    user = get_or_create_dev_user(db)
    visible = make_notice(db)
    event = make_event(db, visible)
    hidden = make_notice(db)
    make_event(db, hidden, review_status='needs_review')
    disabled = make_notice(db)
    make_event(db, disabled, source='wevity')
    another = User(name='other', email='other@local')
    db.add(another)
    db.flush()
    db.add(UserEvent(user_id=another.id, event_id=event.id, bookmarked=True,
        overrides={'start_date': '2030-10-09', 'end_date': '2030-10-09'}))
    db.commit()
    client.put('/api/user/preferences', json={'enabled_sources': {'wevity': False}})
    response = client.get('/api/internal/planner/opportunities')
    assert response.status_code == 200
    assert [item['opportunity_id'] for item in response.json()] == [f'o{visible.id}']
    assert response.json()[0]['events'][0]['action_date'] == '2030-10-08'
    assert response.json()[0]['user_managed'] is False
    assert client.get('/api/internal/planner/facts').status_code == 200


def test_profile_migration_existing_columns_downgrade_and_row_preservation(tmp_path):
    from alembic import command
    from alembic.config import Config
    from app.db import BACKEND_ROOT, run_migrations

    engine = create_engine(f'sqlite:///{tmp_path / "profile.db"}')
    with engine.begin() as conn:
        run_migrations(conn, '0006')
        conn.execute(text("INSERT INTO users(id,email,name,created_at) VALUES (1,'test@local','keep','2030-01-01')"))
        conn.execute(text("INSERT INTO preferences(user_id,ai_mode,interests,enabled_sources,notifications,auto_mode_recommend) VALUES (1,'study','[]','{}','{}',1)"))
        conn.execute(text("ALTER TABLE preferences ADD COLUMN major VARCHAR(255) NOT NULL DEFAULT ''"))
        conn.execute(text("UPDATE preferences SET major='keep'"))
        run_migrations(conn)
        assert conn.execute(text('SELECT major,grade,enrollment_status FROM preferences')).one() == ('keep', None, 'unknown')
        assert conn.execute(text('SELECT version_num FROM alembic_version')).scalar() == '0010'
        config = Config()
        config.set_main_option('script_location', str(BACKEND_ROOT / 'migrations'))
        config.attributes['connection'] = conn
        command.downgrade(config, '0006')
        assert conn.execute(text('SELECT ai_mode FROM preferences')).scalar() == 'study'
        assert 'major' not in {c['name'] for c in inspect(conn).get_columns('preferences')}
        run_migrations(conn)
        assert conn.execute(text('SELECT major FROM preferences')).scalar() == ''
