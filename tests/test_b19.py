import copy
from collections import Counter

import pytest

from app import cli
from app.deps import get_or_create_dev_user
from app.models import Event, Notice, ReviewLog, UserEvent
from app.services.application_end import (
    TITLE_OFFSET_REASON, deadline_mismatch_reason, fill_title_deadlines,
)
from app.services.notice_metadata import title_deadline
from app.services.pipeline import extract_pending, import_records
from app.services.planner_context import select_candidates
from app.services.planner_facts import opportunity_facts
from app.services.planner_prompts import instructions_for, schema_for
from app.services.planner_validation import validate_output
from app.services.review import confirmation_reason
from test_b16 import CachedModel, response
from test_planner_context import PROFILE
from test_planner_opportunities import NOW, make_event, make_notice
from test_planner_recommendations import output_for, payload_for


@pytest.mark.parametrize('title,published,expected', [
    ('추천(~10/16)', '2030-10-01', '2030-10-16'),
    ('추천( ~ 10 / 16 )', '2030-10-16', '2030-10-16'),
    ('추천(~10.16 )', '2030-10-01', '2030-10-16'),
    ('추천(~ 10월 16일 )', '2030-10-01', '2030-10-16'),
    ('추천(~1/5)', '2030-12-01', '2031-01-05'),
    ('추천(~10/16)', '2030-10-17', '2031-10-16'),
    ('추천(~2/29)', '2031-12-01', '2032-02-29'),
    ('추천(~2/30)', '2030-01-01', None),
    ('추천(~13/1)', '2030-01-01', None),
    ('추천(10/16)', '2030-10-01', None),
    ('추천(10/1~10/16)', '2030-10-01', None),
    ('추천 ~10/16', '2030-10-01', None),
    ('추천(~10/16)', None, None),
    ('추천', '2030-10-01', None),
])
def test_title_deadline_patterns(title, published, expected):
    assert title_deadline(title, published) == expected


def test_title_import_preserves_site_deadline_and_metadata(db):
    records = [dict(site='cbnu', source_url=f'https://example.com/title/{i}',
                    title_raw='추천(~10/16)', published_at='2030-10-01',
                    meta={'other': 'kept'}) for i in range(2)]
    records[1]['meta']['접수기간'] = '2030.10.01 ~ 2030.10.20'
    import_records(db, records)
    title, site = db.query(Notice).order_by(Notice.id).all()
    assert title.application_end_date == '2030-10-16'
    assert title.source_metadata == {'other': 'kept', 'application_end_source': 'title'}
    assert site.application_end_date == '2030-10-20'
    assert 'application_end_source' not in site.source_metadata


@pytest.mark.parametrize('end,expected', [('2030-10-17', '2030-10-16'),
                                        ('2030-10-18', '2030-10-18')])
def test_title_extraction_comparison_and_offset(db, end, expected):
    import_records(db, [dict(site='cbnu', source_url='https://example.com/title',
                            title_raw='추천(~10/16)', published_at='2030-10-01')])
    raw = response()
    raw['candidate']['events'] = [raw['candidate']['events'][0]]
    raw['candidate']['events'][0].update(start_date='2030-10-01', end_date=end)
    assert extract_pending(db, CachedModel(raw)) == 1
    event = db.query(Event).one()
    assert event.end_date == expected
    reason = confirmation_reason(event)
    assert '제목' in reason and '사이트' not in reason
    assert (TITLE_OFFSET_REASON if end.endswith('17') else deadline_mismatch_reason('title')) in reason


@pytest.mark.parametrize('mode', ['priority', 'discover', 'focus'])
def test_expired_bookmark_excluded_registered_retained(db, mode):
    user = get_or_create_dev_user(db)
    notices = [make_notice(db) for _ in range(2)]
    for index, notice in enumerate(notices):
        notice.application_end_date = '2030-10-07'
        event = make_event(db, notice, category='career', event_type='application',
                           start_date='2030-10-01', end_date='2030-10-10')
        db.add(UserEvent(user_id=user.id, event_id=event.id, bookmarked=True, registered=bool(index)))
    db.commit()
    facts = [opportunity_facts(db, user, notice, NOW) for notice in notices]
    assert all(item['user_managed'] for item in facts)
    assert [item['user_registered'] for item in facts] == [False, True]
    selected, _ = select_candidates(facts, PROFILE, mode, NOW)
    assert [item['opportunity_id'] for item in selected] == [facts[1]['opportunity_id']]
    # Actual expired applications still use the existing confirmation flow in priority.
    if mode == 'priority':
        selected[0]['events'][0].update(expired=True)
        selected[0]['application_expired_not_done'] = True
        selected, _ = select_candidates(selected, PROFILE, mode, NOW)
        assert selected[0]['priority_context']['verify_target'] == 'application_confirmation'


def test_title_cli_dry_run_and_review_protection(db, monkeypatch, capsys):
    events = []
    for i in range(7):
        notice = make_notice(db)
        notice.title_raw = f'추천{i}(~10/16 )'
        notice.published_at = '2030-10-01'
        notice.source_metadata = {'other': 'kept'}
        events.append(make_event(db, notice, event_type='application', start_date='',
                                 end_date='2030-10-17', ai_extracted=True, review_reason='기존 사유'))
    events[1].review_status = 'approved'
    db.add(ReviewLog(notice_id=events[2].notice_id, action='save', reason='', before=[], after=[]))
    user = get_or_create_dev_user(db)
    db.add(UserEvent(user_id=user.id, event_id=events[3].id, overrides={'end_date': '2030-10-20'}))
    events[4].end_date = '2030-10-18'
    events[5].end_date = ''
    events[6].event_type = 'event'
    db.commit()
    ids = [e.id for e in events]
    monkeypatch.setattr(cli, 'SessionLocal', lambda: db)
    monkeypatch.setattr(cli, 'init_db', lambda: pytest.fail('no schema changes'))
    monkeypatch.setattr('sys.argv', ['app.cli', 'fill-title-deadline', '--dry-run'])
    cli.main()
    text = capsys.readouterr().out
    assert 'dry-run' in text and 'notice_id=' in text and '추천0(~10/16 ) -> 2030-10-16' in text
    assert db.query(Notice).filter(Notice.application_end_date.is_not(None)).count() == 0
    assert all(e.review_reason == '기존 사유' for e in db.query(Event).all())
    assert len(fill_title_deadlines(db)) == 7
    saved = [db.get(Event, i) for i in ids]
    assert [e.end_date for e in saved] == ['2030-10-16'] + ['2030-10-17'] * 3 + ['2030-10-18', '', '2030-10-17']
    assert TITLE_OFFSET_REASON in saved[0].review_reason
    assert all(deadline_mismatch_reason('title') in e.review_reason for e in saved[1:6])
    assert saved[6].review_reason == '기존 사유'
    assert fill_title_deadlines(db) == []


def test_focus_v6_null_reason_reproduction_and_v7_distribution():
    payload = payload_for('focus', 4)
    payload['pairwise_conflicts'] = [['o1', 'o3']]
    legacy = output_for(payload)
    for item, reason in zip(legacy['items'][1:], [None, 'schedule_conflict', 'stronger_alternative']):
        item.update(tier='secondary', secondary_reason=reason)
    original = copy.deepcopy(legacy)
    items, corrections = validate_output(payload, legacy)
    assert Counter(i['secondary_reason'] for i in items[1:]) == {'information_uncertain': 3}
    assert {'secondary_reason_repaired', 'unsupported_stronger_alternative_removed'} <= set(corrections)
    assert legacy == original
    fixed = copy.deepcopy(legacy)
    fixed['items'][0]['chosen_over'] = ['o2']
    fixed['items'][1]['secondary_reason'] = 'stronger_alternative'
    fixed['items'][2]['conflicts_with'] = ['o1']
    fixed['items'][3]['secondary_reason'] = 'information_uncertain'
    items, corrections = validate_output(payload, fixed)
    assert not corrections
    assert {i['secondary_reason'] for i in items[1:]} == {
        'stronger_alternative', 'schedule_conflict', 'information_uncertain'}
    core, secondary = schema_for('focus')['properties']['items']['items']['anyOf']
    assert core['properties']['secondary_reason'] == {'type': 'null'}
    assert secondary['properties']['secondary_reason']['type'] == 'string'
    assert None not in secondary['properties']['secondary_reason']['enum']
    assert secondary['properties']['chosen_over']['maxItems'] == 0
    assert 'null을 쓰지 않는다' in instructions_for('focus')
