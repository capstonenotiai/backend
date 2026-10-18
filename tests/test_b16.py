import copy
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app import cli
from app.config import Settings
from app.deps import get_or_create_dev_user
from app.models import Event, NoticeEnrichment, UserEvent
from app.services import enrichment, planner_context
from app.services.application_end import SITE_DEADLINE_REASON, fill_application_ends
from app.services.pipeline import extract_pending
from app.services.review import confirmation_reason
from test_enrichment import FakeClient, claim, make_event, make_notice, output_for
from test_planner_context import PROFILE, opportunity
from test_planner_opportunities import NOW
from test_service_v9 import CachedModel, response


@pytest.mark.parametrize('value,evidence,expected', [
    ('excludes_university', '초등학생만', 'excludes_university'),
    ('open_to_university', '누구나', 'open_to_university'),
    ('excludes_university', '없는 문구', 'unclear'),
    ('excludes_university', '', 'unclear'),
    ('invalid', '초등학생만', 'unclear'),
])
def test_audience_schema_validation(value, evidence, expected):
    output = output_for('e1')
    output['audience'] = claim(value, evidence)
    result = enrichment.validate_enrichment(output, 1, '', '초등학생만 / 누구나', ['e1'])
    assert result['audience'] == expected
    assert enrichment.ENRICHMENT_SCHEMA['properties']['audience']['properties']['value']['enum'] == [
        'open_to_university', 'excludes_university', 'unclear']


def test_audience_storage_legacy_and_version_requeue(db, monkeypatch):
    monkeypatch.setattr(enrichment, 'get_settings', lambda: Settings(_env_file=None, openai_api_key='fake'))
    notice = make_notice(db, body='초등학생만')
    event = make_event(db, notice)
    saved = NoticeEnrichment(notice_id=notice.id, state='done', prompt_version='enrich-v3', facts={})
    db.add(saved)
    db.commit()
    assert enrichment.get_enrichment(db, notice.id)['audience'] == 'unclear'
    saved.prompt_version = 'enrich-v1'
    db.commit()
    assert enrichment.get_enrichment(db, notice.id)['audience'] == 'unclear'
    output = output_for(f'e{event.id}')
    output['audience'] = claim('excludes_university', '초등학생만')
    client = FakeClient(output)
    assert enrichment.enrich_pending(db, client=client, now=NOW) == 1
    assert len(client.calls) == 1 and saved.prompt_version == 'enrich-v3'
    assert enrichment.get_enrichment(db, notice.id)['audience'] == 'excludes_university'


@pytest.mark.parametrize('mode', ['priority', 'discover', 'focus'])
@pytest.mark.parametrize('managed', [False, True])
def test_excluded_audience_candidates(mode, managed):
    item = opportunity(user_managed=managed)
    item['enrichment']['audience'] = 'excludes_university'
    item['events'][0]['action_date'] = '2030-10-10'
    assert planner_context.eligibility(item, PROFILE) == 'ineligible'
    selected, _ = planner_context.select_candidates([item], PROFILE, mode, NOW)
    assert bool(selected) == (mode == 'priority' and managed)
    item['enrichment']['grouping_review_required'] = True
    assert planner_context.eligibility(item, PROFILE) == 'needs_check'


@pytest.mark.parametrize('mode', ['priority', 'discover', 'focus'])
@pytest.mark.parametrize('days', [0, 1, 2, None])
@pytest.mark.parametrize('managed', [False, True])
def test_candidate_lead_days(mode, days, managed):
    item = opportunity(user_managed=managed)
    item['events'][0]['action_date'] = (NOW.date() + timedelta(days=days)).isoformat() if days is not None else None
    selected, _ = planner_context.select_candidates([item], PROFILE, mode, NOW)
    assert bool(selected) == (mode == 'priority' or managed or days is None or days >= 2)


def test_lead_days_use_seoul_day():
    item = opportunity()
    item['events'][0]['action_date'] = '2030-10-10'
    # UTC Oct 8 16:00 is Seoul Oct 9, leaving only one day.
    now = datetime(2030, 10, 8, 16, tzinfo=timezone.utc)
    assert planner_context.select_candidates([item], PROFILE, 'discover', now)[0] == []


@pytest.mark.parametrize('clock', ['23:59', '00:00', '24:00'])
def test_extraction_time_boundary_no_day_shift(db, clock):
    raw = response()
    raw['candidate']['events'] = [raw['candidate']['events'][0]]
    raw['candidate']['events'][0].update(end_date='2030-11-22', end_time=clock)
    notice = make_notice(db)
    db.commit()
    assert extract_pending(db, CachedModel(raw)) == (0 if clock == '24:00' else 1)
    if clock == '24:00':
        assert db.scalar(select(Event)) is None
        assert notice.extraction_state == 'failed'
    else:
        event = db.scalar(select(Event))
        assert (event.end_date, event.end_time) == ('2030-11-22', clock)
        assert notice.extraction_result == raw


@pytest.mark.parametrize('deadline,start,filled', [
    ('2030-10-22', '2030-10-01', True),
    (None, '2030-10-01', False),
    ('2030-10-22', '2030-10-23', False),
])
def test_site_deadline_at_extraction(db, deadline, start, filled):
    raw = response()
    raw['candidate']['events'] = [raw['candidate']['events'][0]]
    raw['candidate']['events'][0].update(start_date=start, end_date=None, end_time=None)
    notice = make_notice(db)
    notice.application_end_date = deadline
    db.commit()
    assert extract_pending(db, CachedModel(raw)) == 1
    event = db.scalar(select(Event))
    assert event.end_date == (deadline if filled else '')
    if filled:
        assert event.extraction_metadata['end_date_source'] == 'site_deadline'
        assert SITE_DEADLINE_REASON in confirmation_reason(event)
        assert '마감일이 없어요' not in confirmation_reason(event)
        assert '종료일 또는 마감일' not in confirmation_reason(event)
        assert notice.extraction_result == raw  # Original model output stays available.
    else:
        assert '마감일이 없어요' in confirmation_reason(event)


def test_fill_cli_dry_run_and_user_overrides(db, monkeypatch, capsys):
    notice = make_notice(db)
    notice.application_end_date = '2030-10-22'
    event = make_event(db, notice, end_date='', ai_extracted=True,
                       review_reason='종료일 또는 마감일을 원문에서 확인해 주세요.')
    user = get_or_create_dev_user(db)
    state = UserEvent(user_id=user.id, event_id=event.id, overrides={'end_date': '2030-12-01'})
    db.add(state)
    db.commit()
    monkeypatch.setattr(cli, 'SessionLocal', lambda: db)
    monkeypatch.setattr(cli, 'init_db', lambda: pytest.fail('must not initialize schema'))
    monkeypatch.setattr('sys.argv', ['app.cli', 'fill-application-end', '--dry-run'])
    cli.main()
    db.expire_all()
    assert event.end_date == '' and event.extraction_metadata is None
    assert 'dry-run' in capsys.readouterr().out
    assert fill_application_ends(db) == 1
    event = db.get(Event, event.id)
    state = db.get(UserEvent, state.id)
    assert event.end_date == '2030-10-22'
    assert state.overrides == {'end_date': '2030-12-01'}
    assert event.review_reason == SITE_DEADLINE_REASON
    assert fill_application_ends(db) == 0


def test_inspect_cli_date_fields_only_read_only(db, monkeypatch, capsys):
    notice = make_notice(db, body='PRIVATE_BODY')
    raw = response()
    raw['candidate']['events'][0]['title'] = 'PRIVATE_TITLE'
    raw['secret'] = 'PRIVATE_SECRET'
    notice.extraction_result = raw
    event = make_event(db, notice)
    before = copy.deepcopy(notice.extraction_result)
    monkeypatch.setattr(cli, 'SessionLocal', lambda: db)
    monkeypatch.setattr(cli, 'init_db', lambda: pytest.fail('must not initialize schema'))
    monkeypatch.setattr('sys.argv', ['app.cli', 'inspect-extraction', str(notice.id), '999'])
    cli.main()
    output = capsys.readouterr().out
    assert 'PRIVATE_' not in output and 'title' not in output
    result = json.loads(output)
    assert result[0]['comparison'][0]['extraction']['end_date'] == '2030-10-23'
    assert result[0]['comparison'][0]['stored_event']['event_id'] == event.id
    assert result[1] == {'notice_id': 999, 'missing': True}
    db.expire_all()
    assert notice.extraction_result == before


def test_inspect_multiple_response_layers_keep_event_positions(db):
    notice = make_notice(db)
    event = make_event(db, notice)
    notice.extraction_result = {
        'raw_prediction': {'end_date': '2030-11-22', 'end_time': '23:59'},
        'final_prediction': {'end_date': '2030-11-23', 'end_time': '23:59'},
        'native_prediction': {'end_date': {'body': 'PRIVATE_BODY'}},
    }
    db.commit()
    output = cli.inspect_extraction(db, [notice.id])
    comparison = json.loads(output)[0]['comparison']
    assert len(comparison) == 2
    assert [row['stored_event']['event_id'] for row in comparison] == [event.id, event.id]
    assert [row['extraction']['end_date'] for row in comparison] == ['2030-11-22', '2030-11-23']
    assert 'PRIVATE_BODY' not in output
