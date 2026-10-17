from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.models import Event, Notice, UserEvent
from app.deps import get_or_create_dev_user
from app.services.planner import build_events_context
from app.services import planner_context, enrichment
from app.services.google import build_calendar_body
from app.services.review import confirmation_reason, MAX_NOTICE_DATE_GAP_DAYS, MAX_APPLICATION_PERIOD_DAYS
from test_planner_context import opportunity, PROFILE, add_fact
from test_enrichment import FakeClient, output_for, make_notice, make_event, NOW


def test_chat_managed_interests_hidden_and_cap():
    def event(number, **values):
        return SimpleNamespace(title=f'일정{number}', end_date='2030-10-09', start_date='',
                               category='contest', registered=False, bookmarked=False, dismissed=False,
                               source='cbnu', location='', review_status='auto', detail='', **values)
    ordinary = [event(i) for i in range(30)]
    managed = [event(100 + i) for i in range(65)]
    for item in managed:
        item.registered = True
        item.end_date = '2030-11-01'
    interested = event(200)
    interested.category = 'career'
    interested.end_date = '2030-12-01'
    bookmark = event(201)
    bookmark.bookmarked = True
    hidden = event(202)
    hidden.dismissed = hidden.registered = True
    context = build_events_context(ordinary + managed + [interested, bookmark, hidden], date(2030, 10, 8), ['career'])
    assert '일정100 |' in context and '일정158 |' in context
    assert '일정159 |' not in context  # bookmark + 59 registered events
    assert '일정200 |' in context and '일정201 |' in context and '관심 저장됨' in context
    assert '일정202 |' not in context and '일정19 |' not in context


@pytest.mark.parametrize('mode', ['discover', 'focus'])
def test_interest_and_enrichment_order_before_limit(mode):
    unrelated = [opportunity(i, category='contest') for i in range(1, 25)]
    missing = opportunity(30)
    missing['enrichment']['enrichment_status'] = 'missing'
    direct = opportunity(31)
    direct['events'][0]['action_date'] = '2030-12-01'
    related = opportunity(32, category='activity')
    related['events'][0]['action_date'] = '2030-11-01'
    add_fact(related, 'related_interest', 'career')
    selected, _ = planner_context.select_candidates(unrelated + [missing, direct, related], PROFILE, mode)
    assert [item['opportunity_id'] for item in selected[:3]] == ['o32', 'o31', 'o30']


def test_priority_managed_before_urgent_and_unrelated_excluded():
    managed = opportunity(1, user_managed=True, category='contest')
    managed['events'][0].update(urgency='later', action_date='2030-12-01')
    urgent = opportunity(2)
    unrelated = opportunity(3, category='contest')
    selected, _ = planner_context.select_candidates([urgent, unrelated, managed], PROFILE, 'priority')
    assert [item['opportunity_id'] for item in selected] == ['o1', 'o2']


@pytest.mark.parametrize('start_only', [False, True])
def test_enrich_nearest_date_before_id(db, monkeypatch, start_only):
    from app.config import Settings
    monkeypatch.setattr(enrichment, 'get_settings', lambda: Settings(_env_file=None, openai_api_key='test'))
    notices = [make_notice(db, str(i)) for i in range(4)]
    make_event(db, notices[0], end_date='2030-12-01')
    near = make_event(db, notices[1], start_date='2030-10-09', end_date='' if start_only else '2030-10-09')
    make_event(db, notices[2], end_date='2030-10-09')
    make_event(db, notices[3], start_date='2030-10-10', end_date='')
    client = FakeClient(output_for(f'e{near.id}'))
    assert enrichment.enrich_pending(db, client, limit=1, now=NOW) == 1
    from app.models import NoticeEnrichment
    assert db.get(NoticeEnrichment, notices[1].id) is not None
    assert db.get(NoticeEnrichment, notices[0].id) is None


def test_crawler_activity_wins(monkeypatch):
    from crawler import contestkorea as ck
    monkeypatch.setattr(ck, 'get_total_pages', lambda code: 1)
    def listing(page, code):
        return [{'source_url': f'http://contestkorea.com/sub/view.php?Txt_bcode={code}&str_no={number}',
                 'title_raw': f'공지{number}'} for number in (['1', '2'] if code.startswith('04') else ['1', '3'])]
    monkeypatch.setattr(ck, 'parse_list_page', listing)
    monkeypatch.setattr(ck, 'parse_detail_page', lambda url: {'source_url': url, 'site': 'contestkorea'})
    records = ck.crawl(categories=['030110001', '040110001'])
    by_id = {ck.post_id(row['source_url']): row for row in records}
    assert {key: row['board'] for key, row in by_id.items()} == {'1': 'activity', '2': 'activity', '3': 'contest'}
    assert by_id['1']['category_code'] == '040110001'


@pytest.mark.parametrize('dry_run', [True, False])
def test_recategorize_cli_lists_only(db, monkeypatch, capsys, dry_run):
    from app import cli
    from crawler import contestkorea as ck
    notice = Notice(site='contestkorea', board='contest', title_raw='수정 예시',
                    source_url='http://contestkorea.com/sub/view.php?Txt_bcode=030110001&str_no=1',
                    source_metadata={'category_code': '030110001', 'keep': 'value'})
    db.add(notice)
    db.flush()
    event = make_event(db, notice, category='contest')
    codes = []
    monkeypatch.setattr(ck, 'get_total_pages', lambda code: codes.append(code) or 1)
    monkeypatch.setattr(ck, 'parse_list_page', lambda page, code: [{'source_url': 'http://contestkorea.com/sub/view.php?str_no=1'}])
    monkeypatch.setattr(ck, 'parse_detail_page', lambda url: pytest.fail('상세 조회 금지'))
    monkeypatch.setattr(cli, 'SessionLocal', lambda: db)
    monkeypatch.setattr(cli, 'init_db', lambda: pytest.fail('스키마 변경 금지'))
    monkeypatch.setattr('sys.argv', ['app.cli', 'recategorize-contestkorea'] + (['--dry-run'] if dry_run else []))
    cli.main()
    assert codes and all(code.startswith('04') for code in codes)
    assert '변경 공지 1건' in capsys.readouterr().out
    db.expire_all()
    assert event.category == notice.board == ('contest' if dry_run else 'activity')
    assert notice.source_metadata == {'category_code': '030110001' if dry_run else '040110001', 'keep': 'value'}


@pytest.mark.parametrize('kind,start,end_time,start_time', [
    ('application', '2030-10-01', '18:00', ''), ('submission', '', '17:00', ''),
    ('application', '2030-10-01', '', ''), ('event', '2030-10-09', '18:00', '17:00')])
def test_calendar_period_and_existing_behavior(kind, start, end_time, start_time):
    event = Event(title='일정', event_type=kind, start_date=start, end_date='2030-10-09',
                  end_time=end_time, start_time=start_time, detail='설명')
    body = build_calendar_body(event)
    if start_time:
        assert body['start']['dateTime'] == '2030-10-09T17:00:00+09:00'
        assert body['end']['dateTime'] == '2030-10-09T18:00:00+09:00'
    else:
        assert body['start'] == {'date': start or '2030-10-09'}
        assert body['end'] == {'date': '2030-10-10'}
        assert body['summary'] == ('일정' + (f' (마감 {end_time})' if end_time else ''))
        if end_time:
            assert body['description'].startswith(f'접수 마감: 2030-10-09 {end_time}\n')
        assert '1분' not in body['description']


@pytest.mark.parametrize('case', ['distant', 'long', 'missing', 'missing_submission', 'normal', 'gap_boundary', 'period_boundary', 'fallback'])
def test_confirmation_anomalies(db, case):
    notice = make_notice(db)
    notice.published_at = '2030-10-01'
    notice.crawled_at = datetime(2030, 10, 1, tzinfo=timezone.utc)
    event = make_event(db, notice, ai_extracted=True)
    if case in ('distant', 'gap_boundary', 'fallback'):
        days = MAX_NOTICE_DATE_GAP_DAYS + (case != 'gap_boundary')
        event.start_date = event.end_date = (date(2030, 10, 1) + timedelta(days=days)).isoformat()
        if case == 'fallback':
            notice.published_at = None
    elif case in ('long', 'period_boundary'):
        event.end_date = (date.fromisoformat(event.start_date) + timedelta(days=MAX_APPLICATION_PERIOD_DAYS + (case == 'long'))).isoformat()
    elif case in ('missing', 'missing_submission'):
        event.end_date = ''
        if case == 'missing_submission':
            event.event_type = 'submission'
    reason = confirmation_reason(event)
    if case in ('normal', 'gap_boundary', 'period_boundary'):
        assert reason is None
    else:
        assert {'distant': '많이 늦어요', 'long': '기간이 길어요', 'missing': '마감일이 없어요',
                'missing_submission': '마감일이 없어요', 'fallback': '많이 늦어요'}[case] in reason


def test_missing_deadline_list_and_calendar_registration(client, db):
    notice = make_notice(db)
    notice.published_at = '2030-10-01'
    event = make_event(db, notice, end_date='', ai_extracted=True)
    assert str(event.id) not in {item['id'] for item in client.get('/api/events').json()}
    response = client.post('/api/calendar/register', json={'event_id': event.id, 'confirmed': True})
    assert response.status_code == 400 and '종료일 또는 마감일' in response.json()['message']
    assert client.get(f'/api/events/{event.id}').json()['events'][0]['review_required'] is True


def test_investigate_retained_finished_main_events(client, db, monkeypatch):
    from app.services import events
    monkeypatch.setattr(events, 'now_local', lambda: NOW)
    user = get_or_create_dev_user(db)
    retained, removed, saved, closed = [make_notice(db, f'retention-{i}') for i in range(4)]
    recent = make_event(db, retained, event_type='event', start_date='2030-10-01', end_date='2030-10-01')
    old = make_event(db, removed, event_type='event', start_date='2030-01-01', end_date='2030-01-01')
    bookmarked = make_event(db, saved, event_type='event', start_date='2030-01-01', end_date='2030-01-01')
    make_event(db, closed, end_date='2030-10-01')
    upcoming = make_event(db, closed, event_type='event', end_date='2030-12-01')
    db.add(UserEvent(user_id=user.id, event_id=bookmarked.id, bookmarked=True))
    db.commit()
    listed = {item['id']: item for item in client.get('/api/events').json()}
    assert {str(recent.id), str(bookmarked.id)} <= listed.keys()
    assert str(old.id) not in listed and str(upcoming.id) not in listed
    assert {'end_date', 'end_time', 'timezone', 'event_type'} <= listed[str(recent.id)].keys()
    assert 'expired' not in listed[str(recent.id)]
