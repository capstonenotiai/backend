from types import SimpleNamespace

import pytest

from app import cli
from app.deps import get_or_create_dev_user
from app.config import Settings
from app.models import Event, NoticeEnrichment, ReviewLog, UserEvent
from app.services.planner import PlannerError
from app.services.planner_prompts import schema_for
from app.services import enrichment, planner_context as planner, planner_recommendations as recommendations
from app.services.application_end import OFFSET_REASON, correct_event_end, fix_application_end_offsets
from app.services.review import confirmation_reason
from app.services.pipeline import extract_pending
from test_b16 import CachedModel, response
from test_enrichment import BODY, FakeClient, claim, output_for
from test_planner_context import PROFILE, opportunity
from test_planner_opportunities import NOW, make_event, make_notice
from test_planner_recommendations import fake_sdk, payload_for


@pytest.mark.parametrize('day,corrected', [('2030-10-11', True), ('2030-10-12', False),
                                         ('2030-10-09', False), ('2030-10-10', False)])
def test_correct_exact_offset_only(day, corrected):
    event = SimpleNamespace(event_type='application', start_date='2030-10-01', end_date=day,
                            extraction_metadata={'existing': True}, review_reason='existing')
    assert correct_event_end(event, '2030-10-10') is corrected
    assert event.end_date == ('2030-10-10' if corrected else day)
    if corrected:
        assert event.extraction_metadata == {'existing': True, 'end_date_source': 'site_deadline_corrected'}
        assert OFFSET_REASON in event.review_reason


def test_offset_at_extraction_keeps_confirmation(db):
    raw = response()
    raw['candidate']['events'] = [raw['candidate']['events'][0]]
    raw['candidate']['events'][0].update(start_date='2030-10-01', end_date='2030-10-11')
    notice = make_notice(db)
    notice.application_end_date = '2030-10-10'
    db.commit()
    assert extract_pending(db, CachedModel(raw)) == 1
    event = db.query(Event).one()
    assert event.end_date == '2030-10-10'
    assert OFFSET_REASON in confirmation_reason(event)
    assert notice.extraction_result == raw


def test_fix_cli_dry_run_and_review_protection(db, monkeypatch, capsys):
    events = []
    for index in range(5):
        notice = make_notice(db)
        notice.application_end_date = '2030-10-10'
        events.append(make_event(db, notice, event_type='application', start_date='2030-10-01',
                                 end_date='2030-10-11'))
    events[1].review_status = 'approved'
    db.add(ReviewLog(notice_id=events[2].notice_id, action='edit', reason='', before=[], after=[]))
    user = get_or_create_dev_user(db)
    db.add(UserEvent(user_id=user.id, event_id=events[3].id, overrides={'end_date': '2030-10-09'}))
    events[4].end_date = '2030-10-12'
    db.commit()
    event_id = events[0].id
    monkeypatch.setattr(cli, 'SessionLocal', lambda: db)
    monkeypatch.setattr(cli, 'init_db', lambda: pytest.fail('no schema changes'))
    monkeypatch.setattr('sys.argv', ['app.cli', 'fix-application-end-offset', '--dry-run'])
    cli.main()
    text = capsys.readouterr().out
    assert f'event_id={event_id}: 2030-10-11 -> 2030-10-10' in text
    db.expire_all()
    assert db.get(Event, event_id).end_date == '2030-10-11'
    assert fix_application_end_offsets(db) == [dict(event_id=event_id, before='2030-10-11', after='2030-10-10')]
    assert fix_application_end_offsets(db) == []
    assert [db.get(Event, e.id).end_date for e in events[1:]] == ['2030-10-11'] * 3 + ['2030-10-12']


@pytest.mark.parametrize('value,evidence,expected', [('high', '진로 상담 프로그램', 'high'),
    ('low', '진로 상담 프로그램', 'low'), ('low', 'missing', 'unclear'), ('invalid', BODY, 'unclear')])
def test_student_fit_validated(value, evidence, expected):
    output = output_for('e1')
    output['student_fit'] = claim(value, evidence)
    result = enrichment.validate_enrichment(output, 1, '', BODY, ['e1'])
    assert result['student_fit'] == expected


def test_student_fit_storage_and_v2_refresh(db, monkeypatch):
    monkeypatch.setattr(enrichment, 'get_settings', lambda: Settings(_env_file=None, openai_api_key='fake'))
    notice = make_notice(db)
    notice.raw_text = BODY
    event = make_event(db, notice, start_date='2030-10-10', end_date='2030-10-10')
    saved = NoticeEnrichment(notice_id=notice.id, prompt_version='enrich-v2', state='done',
                             facts={'student_fit': 'low'})
    db.add(saved)
    db.commit()
    assert enrichment.get_enrichment(db, notice.id)['student_fit'] == 'unclear'
    output = output_for(f'e{event.id}')
    output['student_fit'] = claim('high', '진로 상담 프로그램')
    fake = FakeClient(output)
    assert enrichment.enrich_pending(db, fake, now=NOW) == 1
    assert saved.prompt_version == 'enrich-v3'
    assert saved.raw['output']['student_fit']['evidence'] == '진로 상담 프로그램'
    assert enrichment.get_enrichment(db, notice.id)['student_fit'] == 'high'


@pytest.mark.parametrize('mode', ['priority', 'discover', 'focus'])
def test_fit_order_interest_and_managed(mode):
    low, high, unrelated = [opportunity(i) for i in range(1, 4)]
    low['enrichment']['student_fit'] = 'low'
    high['enrichment']['student_fit'] = 'high'
    low['events'][0]['action_date'] = '2030-10-10'
    high['events'][0]['action_date'] = '2030-10-12'
    unrelated['category'] = 'academic'
    selected, _ = planner.select_candidates([low, unrelated, high], PROFILE, mode, NOW)
    assert [i['opportunity_id'] for i in selected][:2] == ['o2', 'o1']
    assert planner._payload_opportunity(selected[1], mode)['student_fit'] == 'low'
    low['user_managed'] = True
    selected, _ = planner.select_candidates([high, low], PROFILE, mode, NOW)
    assert selected[0]['opportunity_id'] == 'o1'


@pytest.mark.parametrize('mode', ['priority', 'discover', 'focus'])
def test_site_deadline_boundary_and_managed(mode):
    items = [opportunity(i) for i in range(1, 4)]
    for item in items:
        item['application_end_date'] = '2030-10-07'
    items[1]['application_end_date'] = '2030-10-08'
    items[2]['user_managed'] = True
    items[2]['user_registered'] = True
    selected, _ = planner.select_candidates(items, PROFILE, mode, NOW)
    assert {i['opportunity_id'] for i in selected} == {'o2', 'o3'}


@pytest.mark.parametrize('mode', ['priority', 'discover', 'focus'])
def test_duplicate_preference_and_grouping(mode):
    items = [opportunity(i, title=title) for i, title in enumerate([
        '[모집] Basketball!', ' basket BALL ', 'Basketball', 'basketball'], 1)]
    items[0]['published_at'] = '2030-10-09'
    items[0]['enrichment'] = enrichment.empty_enrichment()
    items[1]['published_at'] = '2030-10-07'
    items[2]['published_at'] = '2030-10-08'
    items[3]['user_managed'] = True
    selected, grouping = planner.select_candidates(items, PROFILE, mode, NOW)
    assert [i['opportunity_id'] for i in selected] == ['o4'] and not grouping
    selected, _ = planner.select_candidates(items[:3], PROFILE, mode, NOW)
    assert [i['opportunity_id'] for i in selected] == ['o3']


def test_focus_duplicate_unrelated_cannot_replace_matching_candidate():
    direct = opportunity(1, title='same')
    unrelated = opportunity(2, title='same', category='academic', user_managed=True)
    selected, _ = planner.select_candidates([direct, unrelated], PROFILE, 'focus', NOW)
    assert [i['opportunity_id'] for i in selected] == ['o1']


def test_incomplete_logs_only_reason(monkeypatch, caplog):
    fake_sdk(monkeypatch, SimpleNamespace(status='incomplete', output_text='PRIVATE_CONTENT',
        incomplete_details=SimpleNamespace(reason='max_output_tokens')))
    with pytest.raises(PlannerError) as error:
        recommendations.create_judgment(payload_for('focus'))
    assert error.value.status == 502
    assert 'max_output_tokens' in caplog.text and 'PRIVATE_CONTENT' not in caplog.text
    assert all(branch['properties']['reason']['maxLength'] == 120
               for branch in schema_for('focus')['properties']['items']['items']['anyOf'])
