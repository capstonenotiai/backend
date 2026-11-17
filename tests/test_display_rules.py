from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from app.deps import get_or_create_dev_user
from app.models import Event, Notice, UserEvent
from app.services.display_rules import representative
from app.services.notifications import notification_candidates

NOW = datetime(2030, 10, 6, 16, tzinfo=ZoneInfo('Asia/Seoul'))


def notice(db):
    row = Notice(site='cbnu', source_url='https://example.com/display', title_raw='모집과 행사', raw_text='원문')
    db.add(row)
    db.flush()
    return row


def event(db, parent, kind='event', end='2030-10-20', **kwargs):
    row = Event(notice_id=parent.id, source='cbnu', title=kind, event_type=kind,
                start_date=end, end_date=end, review_status='approved', **kwargs)
    db.add(row)
    db.flush()
    return row


def test_select_nearest_open_application_not_first_row(db):
    parent = notice(db)
    past = event(db, parent, 'application', '2030-10-05')
    later = event(db, parent, 'application', '2030-10-10')
    nearest = event(db, parent, 'submission', '2030-10-07')
    main = event(db, parent)
    assert representative([past, later, nearest, main], NOW).id == nearest.id


def test_no_application_selects_main_not_interview_or_result(db):
    parent = notice(db)
    main = event(db, parent)
    interview = event(db, parent, 'interview', '2030-10-08')
    result = event(db, parent, 'result', '2030-10-07')
    assert representative([interview, result, main], NOW).id == main.id
    assert representative([interview, result], NOW) is None


def test_deadline_seoul_time_and_date_only_end_of_day(db):
    parent = notice(db)
    timed = event(db, parent, 'application', '2030-10-06', end_time='15:59')
    all_day = event(db, parent, 'submission', '2030-10-06')
    assert representative([timed], NOW) is None
    assert representative([all_day], NOW).id == all_day.id
    assert representative([all_day], NOW.astimezone(timezone.utc)).id == all_day.id
    midnight = datetime(2030, 10, 7, tzinfo=ZoneInfo('Asia/Seoul'))
    assert representative([all_day], midnight) is None


def test_unknown_deadline_does_not_revive_main(db):
    parent = notice(db)
    application = event(db, parent, 'application', '')
    main = event(db, parent)
    assert representative([application, main], NOW) is None


def test_closed_notice_hidden_despite_bookmark_registration_and_retention(client, db, monkeypatch):
    monkeypatch.setattr('app.services.events.now_local', lambda: NOW)
    parent = notice(db)
    application = event(db, parent, 'application', '2030-10-05')
    main = event(db, parent)
    user = get_or_create_dev_user(db)
    db.add_all([UserEvent(user_id=user.id, event_id=application.id, bookmarked=True),
                UserEvent(user_id=user.id, event_id=main.id, registered=True)])
    db.commit()
    listed = client.get('/api/events').json()
    assert not any(row['notice_id'] == parent.id for row in listed)
    assert str(main.id) in {row['id'] for row in client.get('/api/calendar/events').json()}
    assert {row['id'] for row in client.get(f'/api/events/{main.id}').json()['events']} == {str(application.id), str(main.id)}


def test_bundle_and_interview_detail_and_results(client, db):
    parent = notice(db)
    application = event(db, parent, 'application')
    main = event(db, parent)
    opening = event(db, parent, 'orientation')
    interview = event(db, parent, 'interview')
    result = event(db, parent, 'result')
    db.commit()
    notice_rows = [row for row in client.get('/api/events').json() if row['notice_id'] == parent.id]
    assert [row['id'] for row in notice_rows] == [str(application.id)]
    detail = client.get(f'/api/events/{application.id}').json()['events']
    assert {row['id'] for row in detail} == {str(e.id) for e in [application, main, opening, interview]}
    assert client.get(f'/api/events/{result.id}').status_code == 404
    assert client.post('/api/calendar/register', json={'event_id': str(application.id)}).status_code == 200
    registered = {row['id'] for row in client.get('/api/calendar/events').json()}
    assert {str(application.id), str(main.id), str(opening.id)} <= registered
    assert str(interview.id) not in registered and str(result.id) not in registered
    assert client.post('/api/calendar/register', json={'event_id': str(interview.id)}).status_code == 400
    assert client.post(f'/api/calendar/interviews/{interview.id}').status_code == 200
    assert str(interview.id) in {row['id'] for row in client.get('/api/calendar/events').json()}
    assert client.post(f'/api/calendar/interviews/{main.id}').status_code == 400
    assert client.post('/api/calendar/register', json={'event_id': str(result.id)}).status_code == 400
    assert client.delete(f'/api/calendar/register/{application.id}').status_code == 200
    # Explicit interview opt-in survives automatic bundle removal.
    remaining = {row['id'] for row in client.get('/api/calendar/events').json()}
    assert str(interview.id) in remaining and str(main.id) not in remaining
    assert client.delete(f'/api/calendar/register/{interview.id}').status_code == 200


def test_interest_survives_representative_change_and_notifications(client, db):
    parent = notice(db)
    first = event(db, parent, 'application', '2030-10-07')
    second = event(db, parent, 'submission', '2030-10-09')
    main = event(db, parent)
    interview = event(db, parent, 'interview')
    result = event(db, parent, 'result')
    db.commit()
    assert client.put(f'/api/events/{first.id}/bookmark', json={'bookmarked': True}).status_code == 200
    user = get_or_create_dev_user(db)
    candidates = notification_candidates(db, user, NOW)
    assert {row['event'].id for row in candidates} == {first.id, second.id}
    assert client.post('/api/calendar/register', json={'event_id': str(first.id)}).status_code == 200
    assert {row['event'].id for row in notification_candidates(db, user, NOW)} == {first.id, second.id, main.id}
    assert client.post(f'/api/calendar/interviews/{interview.id}').status_code == 200
    assert {row['event'].id for row in notification_candidates(db, user, NOW)} == {first.id, second.id, main.id, interview.id}
    assert result.id not in {row['event'].id for row in notification_candidates(db, user, NOW)}


def test_google_partial_failure_retry_skips_successful_siblings(client, db, monkeypatch):
    from app.services import google
    parent = notice(db)
    application = event(db, parent, 'application')
    main = event(db, parent)
    user = get_or_create_dev_user(db)
    user.google_refresh_token = 'test-only'
    db.commit()
    calls = []
    def insert(_token, target):
        calls.append(target.id)
        if target.id == main.id and calls.count(main.id) == 1:
            raise google.GoogleError('test failure')
        return f'test-{target.id}'
    monkeypatch.setattr(google, 'insert_event', insert)
    assert client.post('/api/calendar/register', json={'event_id': str(application.id)}).status_code == 502
    assert client.post('/api/calendar/register', json={'event_id': str(application.id)}).status_code == 200
    assert calls == [application.id, main.id, main.id]


def test_bundle_validation_happens_before_google_write(client, db, monkeypatch):
    parent = notice(db)
    application = event(db, parent, 'application')
    event(db, parent, 'event', '')
    db.commit()
    writes = []
    monkeypatch.setattr('app.services.google.insert_event', lambda *_: writes.append(True))
    assert client.post('/api/calendar/register', json={'event_id': str(application.id)}).status_code == 400
    assert not writes
