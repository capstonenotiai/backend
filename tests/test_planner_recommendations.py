import copy
import json
from datetime import timedelta
from types import SimpleNamespace

import httpx
import openai
import pytest
from sqlalchemy import select

from app.config import Settings
from app.deps import get_current_user, get_or_create_dev_user
from app.main import app
from app.models import NoticeEnrichment, UserEvent, UserNotice
from app.services import planner_context, planner_recommendations as service
from app.services.enrichment import empty_enrichment
from app.services.planner import PlannerError
from app.services.planner_prompts import PLANNER_PROMPT_VERSION, instructions_for, schema_for
from app.services.planner_validation import validate_output
from app.services.preferences import get_or_create_preference
from test_planner_context import opportunity, add_fact
from test_planner_opportunities import NOW, make_event, make_notice


@pytest.fixture(autouse=True)
def clear_cache(monkeypatch):
    service._cache.clear()
    monkeypatch.setattr('app.routers.planner.now_local', lambda: NOW)
    yield
    service._cache.clear()


def payload_for(mode, count=3):
    items = [opportunity(index) for index in range(1, count + 1)]
    for item in items:
        item['eligibility'] = 'eligible'
        item['enrichment']['enrichment_status'] = 'ok'
        item['interest_match'] = {'direct': True, 'related': False, 'related_fact_refs': []}
        item['priority_context'] = {'focus_event_id': 'e1', 'next_step_type': 'act', 'verify_target': None}
        add_fact(item, 'benefit', '인턴 면접 기회')
    payload = {'mode': mode, 'opportunities': [planner_context._payload_opportunity(item, mode) for item in items]}
    if mode == 'focus':
        payload.update(pairwise_conflicts=[['o1', 'o2']], needs_grouping_check=[])
    return payload


def output_for(payload):
    mode = payload['mode']
    items = []
    for source in payload['opportunities']:
        item = {'opportunity_id': source['opportunity_id'], 'reason': '확인된 활동 정보를 바탕으로 검토해 주세요.',
                'fact_refs': [fact['fact_id'] for fact in source['facts']]}
        if mode == 'priority':
            item.update(next_step_label=source['priority_context']['next_step_type'], next_action='공지에서 신청 방법을 확인해 주세요.')
        elif mode == 'discover':
            item.update(grade='strong_match', check_reasons=[])
        else:
            item.update(tier='core', secondary_reason=None, chosen_over=[], conflicts_with=[])
        items.append(item)
    return {'items': items}


@pytest.mark.parametrize('mode', ['priority', 'discover', 'focus'])
def test_valid_outputs_and_strict_schemas(mode):
    payload = payload_for(mode)
    output = output_for(payload)
    assert validate_output(payload, output) == (output['items'], [])
    schema = schema_for(mode)
    for node in (schema, schema['properties']['items']['items']):
        assert node['additionalProperties'] is False
        assert set(node['required']) == set(node['properties'])
    assert set(output['items'][0]) == set(schema['properties']['items']['items']['properties'])
    prompt = instructions_for(mode)
    assert '한국어' in prompt and 'career, needs_check' in prompt and 'fact_id' in prompt
    assert PLANNER_PROMPT_VERSION == 'planner-v2'
    schema['properties'].clear()
    assert schema_for(mode)['properties']


@pytest.mark.parametrize('mode', ['priority', 'discover', 'focus'])
def test_missing_duplicate_unknown_and_untrusted_fields(mode):
    payload = payload_for(mode)
    original = copy.deepcopy(payload)
    output = output_for(payload)
    output['items'] = [output['items'][1], output['items'][0], output['items'][0], {'opportunity_id': 'o999'}, None]
    output['items'][0].update(title='GPT 위조 제목', eligibility='ineligible', source_url='https://bad')
    items, corrections = validate_output(payload, output)
    assert [item['opportunity_id'] for item in items] == ['o1', 'o2', 'o3']
    assert {'missing_opportunity_restored', 'duplicate_opportunity_removed', 'unknown_opportunity_removed',
            'invalid_item_removed'} <= set(corrections)
    assert all('title' not in item and 'eligibility' not in item for item in items)
    assert items[-1]['fact_refs'] == []
    if mode == 'priority':
        assert 'priority_order_restored' in corrections
        assert items[-1]['next_step_label'] == 'act'
    elif mode == 'discover':
        assert items[-1]['grade'] == 'potential_match'
    else:
        assert items[-1]['tier'] == 'secondary' and items[-1]['secondary_reason'] == 'information_uncertain'
    assert payload == original


@pytest.mark.parametrize('mode', ['priority', 'discover', 'focus'])
@pytest.mark.parametrize('broken', [None, [], {'items': 'bad'}, {'items': [1, {'opportunity_id': []}]}])
def test_malformed_outputs_restore_all_candidates(mode, broken):
    items, corrections = validate_output(payload_for(mode), broken)
    assert len(items) == 3 and corrections


@pytest.mark.parametrize('mode,field', [('priority', 'next_step_label'), ('discover', 'grade'), ('focus', 'tier'),
                                       ('focus', 'secondary_reason')])
@pytest.mark.parametrize('invalid', ['INVALID', [], {}])
def test_invalid_enums_fall_back(mode, field, invalid):
    payload = payload_for(mode)
    output = output_for(payload)
    output['items'][0][field] = invalid
    items, corrections = validate_output(payload, output)
    assert 'invalid_enum_replaced' in corrections
    assert items[0]['fact_refs'] == []
    if mode == 'discover':
        assert items[0]['grade'] == 'potential_match'
    elif mode == 'focus':
        assert items[0]['tier'] == 'secondary' and items[0]['secondary_reason'] == 'information_uncertain'


def test_fact_refs_must_belong_to_activity_and_text_arrays_repaired():
    payload = payload_for('discover')
    output = output_for(payload)
    own = output['items'][0]['fact_refs'][0]
    other = output['items'][1]['fact_refs'][0]
    output['items'][0].update(fact_refs=[own, other, 'o1.missing', own, None, {}], reason=None, check_reasons=[None, {}, '확인', '확인'])
    items, corrections = validate_output(payload, output)
    assert items[0]['fact_refs'] == [own]
    assert items[0]['check_reasons'] == ['확인'] and isinstance(items[0]['reason'], str)
    assert 'invalid_fact_ref_removed' in corrections


@pytest.mark.parametrize('field,value,code', [('eligibility', 'needs_check', 'eligibility'),
    ('eligibility', 'unknown', 'eligibility'), ('enrichment_status', 'low_confidence', 'low_confidence'),
    ('grouping_review_required', True, 'grouping')])
def test_discover_caps(field, value, code):
    payload = payload_for('discover')
    payload['opportunities'][0][field] = value
    items, corrections = validate_output(payload, output_for(payload))
    assert items[0]['grade'] == 'potential_match'
    assert items[1]['grade'] == 'strong_match'
    assert 'discover_cap_' + code in corrections
    output = output_for(payload)
    output['items'][0]['grade'] = 'not_recommended'
    assert validate_output(payload, output)[0][0]['grade'] == 'not_recommended'


def test_unparsed_requirements_add_checks_without_standalone_cap():
    payload = payload_for('discover')
    payload['opportunities'][0]['unparsed_requirements'] = ['어학 성적 제출', '소득분위 확인']
    items, corrections = validate_output(payload, output_for(payload))
    assert items[0]['grade'] == 'strong_match'
    assert len(items[0]['check_reasons']) == 2
    assert '어학 성적 제출' in items[0]['check_reasons'][0]
    assert 'unparsed_requirement_added' in corrections
    repeated, _ = validate_output(payload, {'items': items})
    assert repeated == items


def test_focus_relations_conflicts_and_secondary_reasons():
    payload = payload_for('focus', 4)
    output = output_for(payload)
    a, b, c, d = output['items']
    a.update(chosen_over=['o2', 'o3', 'o1', 'o999'], secondary_reason='stronger_alternative')
    b.update(tier='secondary', secondary_reason='stronger_alternative', chosen_over=['o1'], conflicts_with=['o1', 'o3', 'o999', 'o2'])
    c.update(chosen_over=['o1'])
    d.update(tier='secondary', secondary_reason='stronger_alternative')
    items, corrections = validate_output(payload, output)
    assert items[0]['chosen_over'] == ['o2'] and items[0]['secondary_reason'] is None
    assert items[1]['chosen_over'] == [] and items[1]['conflicts_with'] == ['o1']
    assert items[1]['secondary_reason'] == 'schedule_conflict'
    assert items[2]['chosen_over'] == []
    assert items[3]['secondary_reason'] == 'information_uncertain'
    assert {'inconsistent_chosen_over_removed', 'unverified_conflict_removed', 'schedule_conflict_enforced',
            'unsupported_stronger_alternative_removed', 'core_secondary_reason_removed'} <= set(corrections)
    assert validate_output(payload, {'items': items}) == (items, [])


def test_focus_valid_alternative_and_unbacked_schedule_reason():
    payload = payload_for('focus')
    output = output_for(payload)
    output['items'][0]['chosen_over'] = ['o2']
    output['items'][1].update(tier='secondary', secondary_reason='stronger_alternative')
    output['items'][2].update(tier='secondary', secondary_reason='schedule_conflict')
    items, corrections = validate_output(payload, output)
    assert items[1]['secondary_reason'] == 'stronger_alternative'
    assert items[2]['secondary_reason'] == 'information_uncertain'
    assert 'secondary_reason_repaired' in corrections


@pytest.mark.parametrize('step,target', [('act', None), ('prepare', None), ('verify', 'date'),
    ('verify', 'grouping'), ('verify', 'action_window'), ('verify', 'application_confirmation'), ('monitor', None)])
def test_priority_next_step_always_server_value(step, target):
    payload = payload_for('priority', 1)
    payload['opportunities'][0]['priority_context'].update(next_step_type=step, verify_target=target)
    output = output_for(payload)
    output['items'][0].update(next_step_label='monitor' if step != 'monitor' else 'act', next_action='잘못된 행동')
    items, corrections = validate_output(payload, output)
    assert items[0]['next_step_label'] == step and items[0]['next_action'] != '잘못된 행동'
    assert 'next_step_restored' in corrections


@pytest.mark.parametrize('mode', ['priority', 'discover', 'focus'])
def test_api_normal_path_and_server_facts(client, db, monkeypatch, mode):
    user = get_or_create_dev_user(db)
    pref = get_or_create_preference(db, user)
    pref.ai_mode, pref.interests = 'discover', ['학사']
    notice = make_notice(db)
    event = make_event(db, notice)
    db.add(NoticeEnrichment(notice_id=notice.id, state='done', prompt_version='enrich-v1', facts=empty_enrichment('ok')))
    db.commit()
    calls = []
    monkeypatch.setattr(service, 'create_judgment', lambda payload: calls.append(copy.deepcopy(payload)) or output_for(payload))
    response = client.post('/api/planner/recommendations', json={'mode': mode})
    assert response.status_code == 200
    body = response.json()
    assert body['mode'] == mode and body['generated_at'] == NOW.isoformat()
    assert body['corrections'] == [] and body['needs_grouping_check'] == []
    item = next(item for item in body['items'] if item['opportunity_id'] == f'o{notice.id}')
    assert item['title'] == notice.title_raw and item['source_url'] == notice.source_url
    assert item['focus_event_id'] == f'e{event.id}' and item['action_date'] == '2030-10-08'
    assert item['start_date'] == item['end_date'] == '2030-10-08' and item['days_until_deadline'] == 0
    assert item['next_step_type'] == item['priority_context']['next_step_type']
    db.refresh(pref)
    assert pref.ai_mode == 'discover' and len(calls) == 1


@pytest.mark.parametrize('mode', ['priority', 'discover', 'focus'])
def test_no_candidates_never_calls_gpt(client, monkeypatch, mode):
    def forbidden(*args):
        pytest.fail('No candidates must not call GPT')
    monkeypatch.setattr(service, 'create_judgment', forbidden)
    monkeypatch.setattr(planner_context, 'all_opportunity_facts', lambda *args: [])
    response = client.post('/api/planner/recommendations', json={'mode': mode})
    assert response.status_code == 200 and response.json()['items'] == []


def test_focus_grouping_only_keeps_check_list(client, db, monkeypatch):
    pref = get_or_create_preference(db, get_or_create_dev_user(db))
    pref.interests = ['career']
    db.commit()
    grouped = opportunity()
    grouped['enrichment']['grouping_review_required'] = True
    monkeypatch.setattr(planner_context, 'all_opportunity_facts', lambda *args: [grouped])
    monkeypatch.setattr(service, 'create_judgment', lambda payload: pytest.fail('No GPT candidates'))
    result = client.post('/api/planner/recommendations', json={'mode': 'focus'}).json()
    assert result['items'] == [] and result['needs_grouping_check'] == [{'opportunity_id': 'o1', 'title': grouped['title']}]


@pytest.mark.parametrize('body', [{'mode': 'study'}, {'mode': None}, {'mode': []}, {}, [], 'bad'])
def test_api_invalid_modes_400(client, body):
    response = client.post('/api/planner/recommendations', json=body)
    assert response.status_code == 400 and set(response.json()) == {'message'}


def test_api_invalid_json_size_missing_key_and_auth(client, db, monkeypatch):
    assert client.post('/api/planner/recommendations', content=b'bad').status_code == 400
    assert client.post('/api/planner/recommendations', content=b'x' * (65 * 1024)).status_code == 413
    notice = make_notice(db)
    make_event(db, notice)
    db.commit()
    response = client.post('/api/planner/recommendations', json={'mode': 'priority'})
    assert response.status_code == 500 and set(response.json()) == {'message'}
    monkeypatch.setattr('app.deps.get_settings', lambda: SimpleNamespace(dev_login=False))
    assert client.post('/api/planner/recommendations', json={'mode': 'priority'}).status_code == 401


def test_cache_reuse_expiration_isolation_invalidation_and_copies(monkeypatch):
    clock = [0]
    monkeypatch.setattr(service, 'monotonic', lambda: clock[0])
    calls = []
    monkeypatch.setattr(service, 'create_judgment', lambda payload: calls.append(copy.deepcopy(payload)) or output_for(payload))
    payload = payload_for('priority', 1)
    server = {'o1': {'title': '서버 제목', 'days_until_deadline': 0}}
    first = service.recommend(1, payload, server, NOW)
    first['items'][0]['title'] = 'mutated'
    clock[0] = 1799
    second = service.recommend(1, payload, server, NOW + timedelta(minutes=29))
    assert second['items'][0]['title'] == '서버 제목' and second['generated_at'] == NOW.isoformat()
    assert len(calls) == 1
    clock[0] = 1800
    service.recommend(1, payload, server, NOW + timedelta(minutes=30))
    service.recommend(2, payload, server, NOW)
    service.recommend(1, payload_for('discover', 1), server, NOW)
    server['o1']['days_until_deadline'] = -1
    service.recommend(1, payload, server, NOW)
    payload['opportunities'][0]['title'] = '새 제목'
    service.recommend(1, payload, server, NOW)
    assert len(calls) == 6
    monkeypatch.setattr(service, 'CACHE_MAX_ENTRIES', 2)
    service.recommend(3, payload, server, NOW)
    assert len(service._cache) <= 2


def test_gpt_errors_not_cached_and_api_message(client, db, monkeypatch):
    notice = make_notice(db)
    make_event(db, notice)
    db.commit()
    calls = []
    def failed(payload):
        calls.append(payload)
        raise PlannerError('잠시 후 다시 시도해 주세요.', 503)
    monkeypatch.setattr(service, 'create_judgment', failed)
    for _ in range(2):
        response = client.post('/api/planner/recommendations', json={'mode': 'priority'})
        assert response.status_code == 503 and response.json() == {'message': '잠시 후 다시 시도해 주세요.'}
    assert len(calls) == 2


def test_bad_gpt_output_does_not_change_db(client, db, monkeypatch):
    user = get_or_create_dev_user(db)
    pref = get_or_create_preference(db, user)
    notice = make_notice(db)
    event = make_event(db, notice)
    facts = empty_enrichment('ok')
    facts['facts'] = [{'fact_id': f'o{notice.id}.ben1', 'type': 'benefit', 'value': '면접 기회', 'evidence': '개인 원문'}]
    enriched = NoticeEnrichment(notice_id=notice.id, state='done', prompt_version='enrich-v1', facts=facts)
    state = UserEvent(user_id=user.id, event_id=event.id, bookmarked=True, action_status='pending')
    notice_state = UserNotice(user_id=user.id, notice_id=notice.id, dismissed=False)
    db.add_all([enriched, state, notice_state])
    db.commit()
    def snapshot():
        db.expire_all()
        return (copy.deepcopy(enriched.facts), state.bookmarked, state.action_status, notice_state.dismissed,
                pref.ai_mode, event.start_date, event.end_date, event.review_status,
                len(db.scalars(select(NoticeEnrichment)).all()), len(db.scalars(select(UserEvent)).all()))
    original = snapshot()
    monkeypatch.setattr(service, 'create_judgment', lambda payload: {'items': [{'opportunity_id': f'o{notice.id}',
        'fact_refs': ['o999.fake'], 'grade': 'bad', 'dismissed': True, 'action_status': 'done'}]})
    response = client.post('/api/planner/recommendations', json={'mode': 'discover'})
    assert response.status_code == 200 and response.json()['corrections']
    assert snapshot() == original


def fake_sdk(monkeypatch, response=None, error=None):
    calls = []
    class FakeClient:
        def __init__(self, **kwargs):
            self.responses = self
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def create(self, **kwargs):
            calls.append(kwargs)
            if error:
                raise error
            return response
    monkeypatch.setattr(openai, 'OpenAI', FakeClient)
    monkeypatch.setattr(service, 'get_settings', lambda: SimpleNamespace(
        openai_api_key='test-placeholder', planner_model='configured-planner', planner_max_output_tokens=4567))
    return calls


@pytest.mark.parametrize('mode', ['priority', 'discover', 'focus'])
def test_sdk_strict_request_privacy_and_model(client, db, monkeypatch, mode):
    user = get_or_create_dev_user(db)
    user.name, user.email = 'PRIVATE_NAME_MARKER', 'PRIVATE_EMAIL_MARKER@local'
    pref = get_or_create_preference(db, user)
    pref.interests = ['학사']
    notice = make_notice(db)
    notice.raw_text = 'PRIVATE_RAW_MARKER'
    make_event(db, notice)
    facts = empty_enrichment('ok')
    facts['facts'] = [{'fact_id': f'o{notice.id}.ben1', 'type': 'benefit', 'value': '면접 기회', 'evidence': 'PRIVATE_EVIDENCE_MARKER'}]
    db.add(NoticeEnrichment(notice_id=notice.id, state='done', prompt_version='enrich-v1', facts=facts))
    db.commit()
    payload, _ = planner_context.build_recommendation_context(db, user, mode, NOW)
    calls = fake_sdk(monkeypatch, SimpleNamespace(status='completed', output_text=json.dumps(output_for(payload))))
    assert client.post('/api/planner/recommendations', json={'mode': mode}).status_code == 200
    call = calls[0]
    serialized = json.dumps(call, ensure_ascii=False)
    for secret in ('PRIVATE_NAME_MARKER', 'PRIVATE_EMAIL_MARKER', 'PRIVATE_RAW_MARKER', 'PRIVATE_EVIDENCE_MARKER', 'google_token', 'evidence'):
        assert secret not in serialized
    assert call['model'] == 'configured-planner' and call['max_output_tokens'] == 4567
    assert call['store'] is False
    assert call['text']['format'] == {'type': 'json_schema', 'name': 'planner_' + mode, 'strict': True, 'schema': schema_for(mode)}


@pytest.mark.parametrize('status,expected', [(429, 503), (400, 502), (500, 502)])
def test_sdk_status_errors(monkeypatch, status, expected):
    response = httpx.Response(status, request=httpx.Request('POST', 'https://example.com'))
    fake_sdk(monkeypatch, error=openai.APIStatusError('failure', response=response, body=None))
    with pytest.raises(PlannerError) as error:
        service.create_judgment(payload_for('priority'))
    assert error.value.status == expected


def test_sdk_connection_error_and_invalid_completed_json(monkeypatch):
    fake_sdk(monkeypatch, error=openai.APIConnectionError(request=httpx.Request('POST', 'https://example.com')))
    with pytest.raises(PlannerError) as error:
        service.create_judgment(payload_for('priority'))
    assert error.value.status == 502
    fake_sdk(monkeypatch, SimpleNamespace(status='completed', output_text='not JSON'))
    assert service.create_judgment(payload_for('priority')) is None


@pytest.mark.parametrize('status,text', [('incomplete', '{'), ('completed', ''), ('completed', None), ('failed', '{}')])
def test_sdk_incomplete_or_refusal_error(monkeypatch, status, text):
    fake_sdk(monkeypatch, SimpleNamespace(status=status, output_text=text))
    with pytest.raises(PlannerError) as error:
        service.create_judgment(payload_for('priority'))
    assert error.value.status == 502


def test_planner_settings_fallback():
    settings = Settings(_env_file=None, openai_model='base-model', planner_model='')
    assert settings.planner_model == 'base-model' and settings.planner_max_output_tokens == 4000
    assert Settings(_env_file=None, planner_model='specific-model').planner_model == 'specific-model'


def test_cli_planner_preview_payload_only(db):
    from app.cli import planner_preview
    from app.deps import get_or_create_dev_user
    user = get_or_create_dev_user(db)
    assert planner_preview(db, 'nobody@example.com', 'priority', False).startswith('사용자 없음')
    assert '"payload"' in planner_preview(db, user.email, 'focus', False)


def test_ids_and_codes_removed_from_user_text():
    payload = payload_for('priority')
    payload['opportunities'][0]['facts'].append({'fact_id': 'o1.how1', 'type': 'how_to_apply', 'value': '이메일 제출'})
    output = output_for(payload)
    first = output['items'][0]
    first.update(fact_refs=[], next_action='3~5인 팀으로 신청서를 이메일로 제출하세요. [o1.how1, o1.how9]',
                 reason='career 관심과 연결된 o1 활동입니다.')
    output['items'][1]['reason'] = '지금 act 단계입니다.'
    items, corrections = validate_output(payload, output)
    assert items[0]['next_action'] == '3~5인 팀으로 신청서를 이메일로 제출하세요.'
    assert items[0]['reason'] == '진로/취업 관심과 연결된 활동입니다.'
    assert items[0]['fact_refs'] == ['o1.how1']  # 문장에 있던 유효한 fact_id 는 fact_refs 로 옮긴다
    assert items[1]['reason'] == '서버가 정한 순서에서 현재 진행할 다음 행동이 있는 활동입니다.'
    assert {'id_removed_from_text', 'internal_code_replaced'} <= set(corrections)


def test_server_check_reasons_only_when_gpt_wrote_fewer():
    payload = payload_for('discover')
    payload['opportunities'][0]['unparsed_requirements'] = ['수원 거주자', '관내 학교 재학']
    output = output_for(payload)
    output['items'][0]['check_reasons'] = ['거주지가 수원인지 확인하세요.', '수원 관내 학교 재학 여부를 확인하세요.']
    items, corrections = validate_output(payload, output)
    assert len(items[0]['check_reasons']) == 2 and 'unparsed_requirement_added' not in corrections
    output['items'][0]['check_reasons'] = ['거주지가 수원인지 확인하세요.']
    items, corrections = validate_output(payload, output)
    assert items[0]['check_reasons'][1:] == ['지원 조건을 확인해 주세요: 수원 거주자', '지원 조건을 확인해 주세요: 관내 학교 재학']


def test_contacts_masked_in_payload():
    item = opportunity()
    item.update(eligibility='eligible', priority_context={'focus_event_id': 'e1', 'next_step_type': 'act', 'verify_target': None})
    add_fact(item, 'how_to_apply', '접수 이메일: food-tech@example.co.kr / 문의 043-261-1234')
    add_fact(item, 'requirement', {'type': 'other', 'value': '문의 010 1234 5678', 'required': True})
    text = json.dumps(planner_context._payload_opportunity(item, 'priority'), ensure_ascii=False)
    assert 'example.co.kr' not in text and '261-1234' not in text and '5678' not in text
    assert '(공지에 안내된 이메일)' in text and '(공지에 안내된 전화번호)' in text
