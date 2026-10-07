import copy
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, delete, inspect, select, text

from app.config import Settings
from app.models import Event, Notice, NoticeEnrichment, UserEvent
from app.services import enrichment as enrich
from app.deps import get_or_create_dev_user
from app.services.planner_facts import opportunity_facts

NOW = datetime(2030, 10, 8, 12, tzinfo=timezone.utc)
BODY = ('접수는 선착순이며 조기 마감될 수 있습니다. 18:00까지 온라인으로 신청.\n'
        '1~3학년 재학생만 지원 가능. 컴퓨터공학 전공자 우대. GPA 3.5 이상.\n'
        '참가자에게 인턴 면접 기회 제공. 재학증명서 준비. 진로 상담 프로그램.\n'
        '본행사 참여는 반드시 해야 합니다. 모집 기간 연장. 별도 활동도 포함됩니다.')


def claim(value, evidence=''):
    return {'value': value, 'evidence': evidence}


def make_notice(db, suffix='1', body=BODY):
    notice = Notice(site='cbnu', source_url=f'https://example.com/{suffix}', title_raw='진로 프로그램', raw_text=body)
    db.add(notice)
    db.flush()
    return notice


def make_event(db, notice, **kwargs):
    values = dict(notice_id=notice.id, source='cbnu', title='프로그램 접수',
                  event_type='application', review_status='auto', start_date='2030-10-01', end_date='2030-10-09')
    values.update(kwargs)
    event = Event(**values)
    db.add(event)
    db.commit()
    return event


def output_for(event_id):
    return {
        'notice_kind': claim('normal'), 'grouping_review_required': claim(False),
        'grouping_review_reason': claim(''),
        'related_interests': [claim('career', '진로 상담 프로그램')],
        'requirements': [{'type': 'grade', 'value': '1~3학년', 'required': True, 'evidence': '1~3학년 재학생만 지원 가능'},
                         {'type': 'major', 'value': '컴퓨터공학', 'required': False, 'evidence': '컴퓨터공학 전공자 우대'}],
        'unparsed_requirements': [claim('GPA 3.5 이상', 'GPA 3.5 이상')],
        'benefits': [claim('인턴 면접 기회', '참가자에게 인턴 면접 기회 제공')],
        'prep_items': [claim('재학증명서', '재학증명서 준비')],
        'how_to_apply': [claim('온라인 신청', '온라인으로 신청')],
        'is_mandatory': claim(True, '본행사 참여는 반드시 해야 합니다.'),
        'events': [{'event_id': event_id, 'role_evidence': claim('접수', '접수는 선착순이며'),
                    'deadline_time_text': claim('18:00까지', '18:00까지 온라인으로 신청'),
                    'early_close': claim(True, '선착순이며 조기 마감될 수 있습니다.')}],
    }


class FakeClient:
    def __init__(self, output=None, error=None, status='completed', text_output=None):
        self.output, self.error, self.status, self.text_output = output, error, status, text_output
        self.calls = []
        self.responses = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return SimpleNamespace(status=self.status, output_text=self.text_output if self.text_output is not None
                               else json.dumps(self.output, ensure_ascii=False))


@pytest.fixture()
def settings(monkeypatch):
    settings = Settings(_env_file=None, openai_api_key='fake-test-key', openai_model='test-model',
                        enrich_max_output_tokens=2500)
    monkeypatch.setattr(enrich, 'get_settings', lambda: settings)
    return settings


def validate(output, notice_id=101, title='진로 프로그램', body=BODY, event_ids=('e1',)):
    return enrich.validate_enrichment(output, notice_id, title, body, event_ids)


def test_normal_storage_ids_input_privacy_and_opportunity(db, settings):
    notice = make_notice(db)
    event = make_event(db, notice)
    user = get_or_create_dev_user(db)
    user.name, user.email = 'PRIVATE_NAME', 'private-email@local'
    user.google_refresh_token = 'PRIVATE_TOKEN'
    db.add(UserEvent(user_id=user.id, event_id=event.id, overrides={'end_date': '2030-10-20', 'title': 'PRIVATE_OVERRIDE'}))
    db.commit()
    client = FakeClient(output_for(f'e{event.id}'))
    before = (event.start_date, event.end_date, event.event_type, event.title)
    assert enrich.enrich_pending(db, client=client, now=NOW) == 1
    saved = db.get(NoticeEnrichment, notice.id)
    assert saved.state == 'done' and saved.attempts == 1 and saved.error is None and saved.next_attempt_at is None
    assert saved.prompt_version == 'enrich-v1' and saved.raw['output'] == client.output
    assert saved.raw['input_truncated'] is False and saved.enrichment_status == 'ok'
    result = enrich.get_enrichment(db, notice.id)
    ids = [fact['fact_id'] for fact in result['facts']]
    assert len(ids) == len(set(ids)) == 9
    assert set(ids) == {f'o{notice.id}.{kind}1' for kind in ('int', 'unp', 'ben', 'prep', 'how', 'man', 'early', 'req')} | {f'o{notice.id}.req2'}
    assert result['is_mandatory'] is True
    assert result['events'][0]['early_close'] and result['events'][0]['deadline_time_text'] == '18:00까지'
    assert result['events'][0]['early_close_fact_id'] == f'o{notice.id}.early1'
    call = client.calls[0]
    assert call['model'] == 'test-model' and call['max_output_tokens'] == 2500 and call['store'] is False
    assert call['text']['format']['strict'] and call['text']['format']['schema'] == enrich.ENRICHMENT_SCHEMA
    payload = json.loads(call['input'][0]['content'])
    assert set(payload) == {'title_raw', 'raw_text', 'raw_text_truncated', 'events'}
    assert set(payload['events'][0]) == {'event_id', 'event_type', 'start_date', 'end_date', 'title'}
    assert payload['events'][0]['end_date'] == '2030-10-09'
    serialized = json.dumps(call, ensure_ascii=False)
    assert all(value not in serialized for value in ('PRIVATE_NAME', 'private-email@local', 'PRIVATE_TOKEN', 'PRIVATE_OVERRIDE'))
    facts = opportunity_facts(db, user, notice, NOW)
    assert facts['enrichment'] == result
    assert facts['events'][0]['action_date'] == '2030-10-20'
    db.refresh(event)
    assert (event.start_date, event.end_date, event.event_type, event.title) == before
    assert enrich.enrich_pending(db, client, now=NOW + timedelta(days=1)) == 0 and len(client.calls) == 1


def test_whitespace_normalized_and_title_evidence():
    output = output_for('e1')
    output['benefits'] = [claim('혜택', '참가자에게\t인턴\n면접   기회 제공'), claim('제목', '진로\n프로그램')]
    result = validate(output)
    benefits = [fact for fact in result['facts'] if fact['type'] == 'benefit']
    assert len(benefits) == 2 and benefits[0]['fact_id'] == 'o101.ben1' and benefits[1]['fact_id'] == 'o101.ben2'
    assert benefits[0]['evidence'] == output['benefits'][0]['evidence']


@pytest.mark.parametrize('key', ['benefits', 'prep_items', 'how_to_apply', 'related_interests', 'unparsed_requirements'])
def test_missing_evidence_drops_only_general_fact(key):
    output = output_for('e1')
    output[key][0]['evidence'] = '원문에 없는 근거'
    result = validate(output)
    assert result['enrichment_status'] == 'ok'
    assert all(fact['evidence'] != '원문에 없는 근거' for fact in result['facts'])
    assert len(result['facts']) == 8


@pytest.mark.parametrize('key', ['requirements', 'is_mandatory', 'notice_kind'])
def test_critical_failure_uses_defaults_and_low_confidence(key):
    output = output_for('e1')
    if key == 'requirements':
        output[key][0]['evidence'] = '없는 학년 제한'
    else:
        output[key] = claim('extension' if key == 'notice_kind' else True, '없는 근거')
    result = validate(output)
    assert result['enrichment_status'] == 'low_confidence'
    assert result['notice_kind'] == 'normal'
    if key == 'is_mandatory':
        assert result[key] is False and not any(f['type'] == key for f in result['facts'])
    assert any(f['type'] == 'benefit' for f in result['facts'])


def test_notice_grouping_evidence_and_nonrequired_failure():
    output = output_for('e1')
    output['notice_kind'] = claim('extension', '모집 기간 연장')
    output['grouping_review_required'] = claim(True, '별도 활동도 포함됩니다.')
    output['grouping_review_reason'] = claim('다른 활동이 함께 있음', '별도 활동도 포함됩니다.')
    output['requirements'][1]['evidence'] = '없는 전공'
    result = validate(output)
    assert result['notice_kind'] == 'extension' and result['grouping_review_required']
    assert result['grouping_review_reason'] == '다른 활동이 함께 있음' and result['enrichment_status'] == 'ok'
    output['grouping_review_required']['evidence'] = '없는 근거'
    output['grouping_review_reason']['evidence'] = '없는 근거'
    result = validate(output)
    assert not result['grouping_review_required'] and result['grouping_review_reason'] == ''


def test_unknown_ids_enums_shapes_and_valid_siblings():
    output = output_for('e1')
    output['related_interests'] += [claim('unknown', '진로 상담 프로그램'), claim('career', '진로 상담 프로그램')]
    output['benefits'] += [{'value': '누락'}, 'string', claim(123, '인턴 면접 기회'),
                           {**claim('혜택', '인턴 면접 기회'), 'extra': True}]
    output['requirements'] += [{'type': 'bogus', 'value': '조건', 'required': False, 'evidence': '1~3학년'}]
    unknown = copy.deepcopy(output['events'][0])
    unknown['event_id'] = 'e999'
    output['events'].extend([unknown, copy.deepcopy(output['events'][0])])
    output['events'][0]['deadline_time_text']['value'] = 123
    result = validate(output)
    assert len([f for f in result['facts'] if f['type'] == 'related_interest']) == 2
    assert len([f for f in result['facts'] if f['type'] == 'benefit']) == 1
    assert len([f for f in result['facts'] if f['type'] == 'requirement']) == 2
    assert len(result['events']) == 1 and result['events'][0]['early_close']
    assert result['events'][0]['deadline_time_text'] == '' and result['enrichment_status'] == 'ok'


def test_required_invalid_enum_and_bool_types():
    output = output_for('e1')
    output['requirements'][0]['type'] = 'income'
    output['requirements'][1]['required'] = 1
    output['is_mandatory']['value'] = 'true'
    result = validate(output)
    assert result['enrichment_status'] == 'low_confidence'
    assert not result['is_mandatory'] and not any(f['type'] == 'requirement' for f in result['facts'])


def test_empty_required_value_is_dropped_conservatively():
    output = output_for('e1')
    output['requirements'][0]['value'] = ' \n '
    result = validate(output)
    assert result['enrichment_status'] == 'low_confidence'
    assert len([fact for fact in result['facts'] if fact['type'] == 'requirement']) == 1


@pytest.mark.parametrize('evidence', ['', ' \n ', '원문에 없음'])
def test_empty_evidence_never_passes(evidence):
    assert not enrich.evidence_valid(evidence, BODY)


def test_booleans_need_explicit_markers_and_event_text_cannot_invent_time():
    output = output_for('e1')
    output['is_mandatory'] = claim(True, '진로 상담 프로그램')
    output['events'][0]['early_close'] = claim(True, '온라인으로 신청')
    output['events'][0]['deadline_time_text'] = claim('23:59까지', '18:00까지 온라인으로 신청')
    result = validate(output)
    assert not result['is_mandatory'] and result['enrichment_status'] == 'low_confidence'
    assert not result['events'][0]['early_close'] and result['events'][0]['deadline_time_text'] == ''


def test_missing_event_fields_default_and_ids_unique_across_events():
    output = output_for('e1')
    second = copy.deepcopy(output['events'][0])
    second['event_id'] = 'e2'
    output['events'].append(second)
    result = validate(output, event_ids=('e1', 'e2', 'e3'))
    assert [e['early_close_fact_id'] for e in result['events']] == ['o101.early1', 'o101.early2', None]
    assert result['events'][2]['early_close'] is False
    assert validate({}, event_ids=('e1',))['facts'] == []
    assert validate(None)['enrichment_status'] == 'ok'


def test_retry_delays_exhaustion_and_requeue(db, settings):
    notice = make_notice(db)
    event = make_event(db, notice)
    client = FakeClient(error=RuntimeError('private token / original text must never be persisted'))
    assert enrich.enrich_pending(db, client, now=NOW) == 0
    saved = db.get(NoticeEnrichment, notice.id)
    assert saved.state == 'retry_pending' and saved.attempts == 1
    assert saved.next_attempt_at == NOW + timedelta(minutes=10)
    assert saved.error == '공지 보강 실패: RuntimeError'
    assert enrich.enrich_pending(db, client, now=NOW + timedelta(minutes=9)) == 0 and len(client.calls) == 1
    assert enrich.enrich_pending(db, client, now=NOW + timedelta(minutes=10)) == 0
    assert saved.attempts == 2 and saved.next_attempt_at == NOW + timedelta(minutes=40)
    assert enrich.enrich_pending(db, client, now=NOW + timedelta(minutes=40)) == 0
    assert saved.state == 'failed' and saved.attempts == 3 and saved.next_attempt_at is None
    assert enrich.enrich_pending(db, client, now=NOW + timedelta(days=1)) == 0 and len(client.calls) == 3
    assert enrich.get_enrichment(db, notice.id)['enrichment_status'] == 'missing'
    assert enrich.requeue_failed(db) == 1
    client.error, client.output = None, output_for(f'e{event.id}')
    assert enrich.enrich_pending(db, client, now=NOW + timedelta(days=1)) == 1
    assert saved.state == 'done' and saved.attempts == 1


@pytest.mark.parametrize('status,text_output', [('incomplete', '{}'), ('completed', 'bad json'), ('completed', '[]'),
                                              ('completed', ''), ('failed', '{}')])
def test_response_failures_retry(db, settings, status, text_output):
    notice = make_notice(db)
    make_event(db, notice)
    assert enrich.enrich_pending(db, FakeClient(status=status, text_output=text_output), now=NOW) == 0
    assert db.get(NoticeEnrichment, notice.id).state == 'retry_pending'


def test_no_key_no_mutations_or_calls_even_requeue(db, settings):
    notice = make_notice(db)
    make_event(db, notice)
    settings.openai_api_key = ''
    client = FakeClient(error=AssertionError('must not call'))
    assert enrich.enrich_pending(db, client) == 0 and not client.calls
    assert db.get(NoticeEnrichment, notice.id) is None
    saved = NoticeEnrichment(notice_id=notice.id, state='failed', attempts=3, prompt_version='old')
    db.add(saved)
    db.commit()
    assert enrich.requeue_failed(db) == 0 and saved.state == 'failed'
    assert enrich.enrich_pending(db, client) == 0 and saved.prompt_version == 'old'


@pytest.mark.parametrize('state', ['done', 'failed', 'retry_pending'])
def test_version_change_resets_and_reprocesses(db, settings, monkeypatch, state):
    notice = make_notice(db)
    event = make_event(db, notice)
    client = FakeClient(output_for(f'e{event.id}'))
    enrich.enrich_pending(db, client, now=NOW)
    saved = db.get(NoticeEnrichment, notice.id)
    saved.state, saved.attempts, saved.next_attempt_at = state, 3, NOW + timedelta(days=1)
    db.commit()
    monkeypatch.setattr(enrich, 'ENRICHMENT_PROMPT_VERSION', 'enrich-v2')
    assert enrich.get_enrichment(db, notice.id)['enrichment_status'] == 'missing'
    assert enrich.enrich_pending(db, client, now=NOW) == 1 and len(client.calls) == 2
    assert saved.attempts == 1 and saved.prompt_version == 'enrich-v2' and saved.state == 'done'


def test_public_only_limit_and_prompt_version_processing_not_claimed(db, settings):
    eligible = make_notice(db, 'eligible')
    make_event(db, eligible, review_status='approved')
    second = make_notice(db, 'second')
    make_event(db, second)
    for index, kwargs in enumerate([{'review_status': 'needs_review'}, {'review_status': 'rejected'},
                                    {'event_type': 'result'},
                                    {'title': '시상식', 'event_type': 'event'}, {'event_type': 'interview'}]):
        notice = make_notice(db, f'hidden-{index}')
        event = make_event(db, notice, **kwargs)
        if event.event_type == 'interview':
            processing = NoticeEnrichment(notice_id=notice.id, state='processing', attempts=1, prompt_version='old')
            db.add(processing)
            db.commit()
    client = FakeClient({})
    assert enrich.enrich_pending(db, client, limit=0, now=NOW) == 0
    assert enrich.enrich_pending(db, client, limit=1, now=NOW) == 1 and len(client.calls) == 1
    assert db.get(NoticeEnrichment, second.id) is None
    assert enrich.enrich_pending(db, client, now=NOW) == 1 and len(client.calls) == 2
    assert len(list(db.scalars(select(NoticeEnrichment)))) == 3


def test_truncation_saved_no_user_overrides(db, settings):
    notice = make_notice(db, body='x' * (enrich.ENRICHMENT_MAX_TEXT_CHARS + 20))
    make_event(db, notice)
    client = FakeClient({})
    assert enrich.enrich_pending(db, client, now=NOW) == 1
    payload = json.loads(client.calls[0]['input'][0]['content'])
    assert len(payload['raw_text']) == 12_000 and payload['raw_text_truncated']
    raw = db.get(NoticeEnrichment, notice.id).raw
    assert raw == {'output': {}, 'input_truncated': True, 'raw_text_original_chars': 12_020, 'raw_text_sent_chars': 12_000}
    assert len(notice.raw_text) == 12_020


@pytest.mark.parametrize('key', ['requirements', 'is_mandatory', 'notice_kind'])
def test_critical_failure_persisted_with_other_facts(db, settings, key):
    notice = make_notice(db)
    event = make_event(db, notice)
    output = output_for(f'e{event.id}')
    if key == 'requirements':
        output[key][0]['evidence'] = '원문에 없는 제한'
    else:
        output[key] = claim('cancellation' if key == 'notice_kind' else True, '원문에 없는 제한')
    assert enrich.enrich_pending(db, FakeClient(output), now=NOW) == 1
    saved = db.get(NoticeEnrichment, notice.id)
    assert saved.state == 'done' and saved.enrichment_status == 'low_confidence'
    assert saved.notice_kind == 'normal'
    result = enrich.get_enrichment(db, notice.id)
    assert result['enrichment_status'] == 'low_confidence'
    assert any(fact['type'] == 'benefit' for fact in result['facts'])
    assert all(fact['evidence'] != '원문에 없는 제한' for fact in result['facts'])


def test_multiple_events_one_request_and_claim_prevents_reentrant_duplicate(db, settings):
    notice = make_notice(db)
    first = make_event(db, notice)
    second = make_event(db, notice, event_type='event', title='본행사')
    hidden = make_event(db, notice, review_status='needs_review')
    competing = FakeClient(error=AssertionError('already claimed'))
    class ReentrantClient(FakeClient):
        def create(self, **kwargs):
            assert enrich.enrich_pending(db, competing, now=NOW) == 0
            return super().create(**kwargs)
    output = output_for(f'e{first.id}')
    output['events'].append({'event_id': f'e{second.id}', 'role_evidence': claim('본행사', '본행사 참여'),
                             'deadline_time_text': claim(''), 'early_close': claim(False)})
    client = ReentrantClient(output)
    assert enrich.enrich_pending(db, client, now=NOW) == 1
    assert len(client.calls) == 1 and not competing.calls
    ids = [event['event_id'] for event in json.loads(client.calls[0]['input'][0]['content'])['events']]
    assert ids == [f'e{first.id}', f'e{second.id}'] and f'e{hidden.id}' not in ids
    result = enrich.get_enrichment(db, notice.id)
    assert [event['early_close'] for event in result['events']] == [True, False]


def test_failed_notice_does_not_stop_remaining_notices(db, settings):
    notices = [make_notice(db, str(index)) for index in range(2)]
    for notice in notices:
        make_event(db, notice)
    class FailFirstClient(FakeClient):
        def create(self, **kwargs):
            self.error = RuntimeError('first fails') if not self.calls else None
            return super().create(**kwargs)
    client = FailFirstClient({})
    assert enrich.enrich_pending(db, client, now=NOW) == 1
    assert [db.get(NoticeEnrichment, n.id).state for n in notices] == ['retry_pending', 'done']


def test_default_client_disables_sdk_retries(db, settings, monkeypatch):
    import openai
    notice = make_notice(db)
    make_event(db, notice)
    constructor_options = []
    client = FakeClient({})
    monkeypatch.setattr(openai, 'OpenAI', lambda **options: constructor_options.append(options) or client)
    assert enrich.enrich_pending(db, now=NOW) == 1
    assert constructor_options == [{'api_key': 'fake-test-key', 'max_retries': 0, 'timeout': 45}]


def test_get_missing_and_removal_of_hidden_event_facts(db, settings):
    assert enrich.get_enrichment(db, 999) == enrich.empty_enrichment()
    notice = make_notice(db)
    event = make_event(db, notice)
    enrich.enrich_pending(db, FakeClient(output_for(f'e{event.id}')), now=NOW)
    event.review_status = 'rejected'
    db.commit()
    result = enrich.get_enrichment(db, notice.id)
    assert not result['events'] and not any(f['type'] == 'early_close' for f in result['facts'])
    assert any(f['type'] == 'benefit' for f in result['facts'])


def test_interrupted_processing_recovered(db, settings):
    for index, attempts in enumerate([1, 3]):
        notice = make_notice(db, str(index))
        db.add(NoticeEnrichment(notice_id=notice.id, state='processing', attempts=attempts, prompt_version='enrich-v1'))
    db.commit()
    assert enrich.mark_interrupted_enrichments(db) == 2
    assert [row.state for row in db.scalars(select(NoticeEnrichment).order_by(NoticeEnrichment.notice_id))] == ['retry_pending', 'failed']


def test_model_default_and_override():
    assert Settings(_env_file=None, openai_model='custom').enrich_model == 'custom'
    assert Settings(_env_file=None, openai_model='custom', enrich_model='enrich-custom').enrich_model == 'enrich-custom'
    assert Settings(_env_file=None).enrich_cron == '*/30 * * * *'


def test_schema_all_objects_strict_all_fields_required():
    def walk(schema):
        if schema['type'] == 'object':
            assert schema['additionalProperties'] is False
            assert set(schema['required']) == set(schema['properties'])
            for child in schema['properties'].values():
                walk(child)
        elif schema['type'] == 'array':
            walk(schema['items'])
    walk(enrich.ENRICHMENT_SCHEMA)


def test_migration_skip_downgrade_and_cascade(tmp_path):
    from alembic import command
    from alembic.config import Config
    from app.db import BACKEND_ROOT, run_migrations
    engine = create_engine(f'sqlite:///{tmp_path / "enrichment.db"}')
    with engine.begin() as conn:
        conn.execute(text('PRAGMA foreign_keys=ON'))
        run_migrations(conn, '0007')
        conn.execute(text("INSERT INTO notices(id,site,source_url,title_raw,raw_text,crawled_at) VALUES (1,'cbnu','https://example.com/1','keep','original','2030-01-01')"))
        # create_all-era table must be skipped without overwriting existing rows.
        NoticeEnrichment.__table__.create(conn)
        conn.execute(NoticeEnrichment.__table__.insert().values(notice_id=1, prompt_version='keep', facts={'keep': True}))
        run_migrations(conn)
        assert conn.execute(text('SELECT prompt_version FROM notice_enrichments')).scalar() == 'keep'
        assert conn.execute(text('SELECT version_num FROM alembic_version')).scalar() == '0008'
        conn.execute(delete(Notice).where(Notice.id == 1))
        assert conn.execute(text('SELECT COUNT(*) FROM notice_enrichments')).scalar() == 0
        config = Config()
        config.set_main_option('script_location', str(BACKEND_ROOT / 'migrations'))
        config.attributes['connection'] = conn
        command.downgrade(config, '0007')
        assert 'notice_enrichments' not in inspect(conn).get_table_names()
        assert 'notices' in inspect(conn).get_table_names()
        run_migrations(conn)
        assert 'notice_enrichments' in inspect(conn).get_table_names()


def test_cli_dispatch_requeue_and_limit(db, settings, monkeypatch, capsys):
    from app import cli
    calls = []
    monkeypatch.setattr(cli, 'init_db', lambda: None)
    monkeypatch.setattr(cli, 'SessionLocal', lambda: db)
    monkeypatch.setattr(cli, 'requeue_enrichment_failed', lambda session: calls.append(('requeue', session)) or 2)
    monkeypatch.setattr(cli, 'enrich_pending', lambda session, limit: calls.append(('enrich', session, limit)) or 1)
    monkeypatch.setattr('sys.argv', ['app.cli', 'enrich', '--limit', '4', '--requeue-failed'])
    cli.main()
    assert calls == [('requeue', db), ('enrich', db, 4)]
    assert '공지 보강 1건' in capsys.readouterr().out


def test_scheduler_crawl_gate_and_enrich_cron(settings, monkeypatch):
    from app import scheduler
    jobs = []
    class FakeScheduler:
        def __init__(self, **kwargs):
            pass
        def add_job(self, func, trigger, **kwargs):
            jobs.append((func, trigger, kwargs))
        def start(self):
            pass
    monkeypatch.setattr(scheduler, 'get_settings', lambda: settings)
    monkeypatch.setattr(scheduler, 'BackgroundScheduler', FakeScheduler)
    settings.crawl_enabled = settings.notify_enabled = False
    assert scheduler.start_scheduler() is None and jobs == []
    settings.crawl_enabled = True
    scheduler.start_scheduler()
    assert [job[2]['id'] for job in jobs] == ['collect', 'extract', 'enrich']
    assert jobs[2][0] == scheduler.run_enrich_job
    assert str(jobs[2][1].fields[6]) == '*/30'
    assert jobs[2][2]['max_instances'] == 1 and jobs[2][2]['coalesce']


def test_finished_notices_are_not_enriched(db, settings):
    finished = make_notice(db, 'finished')
    make_event(db, finished, start_date='2030-09-01', end_date='2030-09-30')
    upcoming_main = make_notice(db, 'upcoming-main')
    make_event(db, upcoming_main, event_type='event', start_date='2030-10-20', end_date='')
    client = FakeClient(output_for('e0'))
    assert enrich.enrich_pending(db, client, now=NOW) == 1
    assert db.get(enrich.NoticeEnrichment, finished.id) is None
    assert db.get(enrich.NoticeEnrichment, upcoming_main.id).state == 'done'
