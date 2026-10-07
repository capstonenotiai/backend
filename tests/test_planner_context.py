import copy
import json

import pytest

from app.deps import get_current_user, get_or_create_dev_user
from app.main import app
from app.models import NoticeEnrichment, User, UserEvent, UserNotice
from app.services import planner_context as planner
from app.services.enrichment import empty_enrichment, validate_enrichment
from app.services.preferences import get_or_create_preference
from test_enrichment import BODY, output_for
from test_planner_opportunities import NOW, make_event, make_notice

PROFILE = {'major': '소프트웨어학부', 'grade': 3, 'enrollment_status': 'enrolled', 'interests': ['career']}


def opportunity(number=1, **values):
    data = {'opportunity_id': f'o{number}', 'title': 'career 진로 공지', 'category': 'career',
            'dismissed': False, 'user_managed': False, 'application_expired_not_done': False,
            'calendar_conflict': None, 'enrichment': empty_enrichment('ok'),
            'events': [{'event_id': f'e{number}', 'event_type': 'application', 'action_type': 'apply',
                        'action_date': '2030-10-09', 'action_window': 'open', 'urgency': 'urgent',
                        'review_status': 'ok', 'expired': False, 'action_status': 'pending'}]}
    data.update(values)
    return data


def add_fact(item, kind, value, number=1):
    fact = {'fact_id': f"{item['opportunity_id']}.{kind}{number}", 'type': kind,
            'value': value, 'evidence': 'PRIVATE_EVIDENCE'}
    item['enrichment']['facts'].append(fact)
    return fact


def requirement(item, kind='grade', value='4학년', required=True):
    return add_fact(item, 'requirement', {'type': kind, 'value': value, 'required': required})


@pytest.mark.parametrize('value,expected', [('3학년 이상', {3, 4, 5, 6}), ('3학년 이하', {1, 2, 3}),
    ('1~2학년', {1, 2}), ('1학년 ~ 2학년', {1, 2}), ('4학년', {4}), ('2-4학년', {2, 3, 4}),
    ('4~1학년', None), ('3학년 또는 졸업예정', None), ('7학년', None), ('3', None), (3, None)])
def test_grade_parser(value, expected):
    assert planner.parse_grade(value) == expected


@pytest.mark.parametrize('kind,value,required,profile_patch,status,expected', [
    ('grade', '4학년', True, {}, 'ok', 'ineligible'),
    ('grade', '1~2학년', True, {}, 'ok', 'ineligible'),
    ('grade', '3학년 이상', True, {}, 'ok', 'eligible'),
    ('grade', '4학년', False, {}, 'ok', 'eligible'),
    ('grade', '3학년 또는 졸업예정', True, {}, 'ok', 'needs_check'),
    ('grade', '4학년', True, {'grade': None}, 'ok', 'needs_check'),
    ('grade', '4학년', True, {'grade': 'unknown'}, 'ok', 'needs_check'),
    ('enrollment_status', '재학생', True, {}, 'ok', 'eligible'),
    ('enrollment_status', 'leave', True, {}, 'ok', 'ineligible'),
    ('enrollment_status', '재학생 및 휴학생', True, {'enrollment_status': 'leave'}, 'ok', 'eligible'),
    ('enrollment_status', '재학생', True, {'enrollment_status': 'unknown'}, 'ok', 'needs_check'),
    ('enrollment_status', '재학생 우대', True, {}, 'ok', 'needs_check'),
    ('major', '경영학부', True, {}, 'ok', 'eligible'),
    ('other', 'GPA 3.5 이상', True, {}, 'ok', 'needs_check'),
    ('grade', '3학년', True, {}, 'low_confidence', 'needs_check'),
])
def test_eligibility(kind, value, required, profile_patch, status, expected):
    item = opportunity()
    item['enrichment']['enrichment_status'] = status
    requirement(item, kind, value, required)
    assert planner.eligibility(item, {**PROFILE, **profile_patch}) == expected
    if kind == 'major':
        assert planner.requirement_comparisons(item, PROFILE)[0]['matches'] is False


def test_eligibility_missing_unparsed_grouping_and_failed_evidence():
    item = opportunity()
    item['enrichment'] = empty_enrichment()
    assert planner.eligibility(item, PROFILE) == 'unknown'
    item['enrichment']['enrichment_status'] = 'ok'
    assert planner.eligibility(item, PROFILE) == 'eligible'
    add_fact(item, 'unparsed_requirement', 'GPA 3.5 이상')
    assert planner.eligibility(item, PROFILE) == 'needs_check'
    item = opportunity()
    requirement(item)
    item['enrichment']['grouping_review_required'] = True
    assert planner.eligibility(item, PROFILE) == 'needs_check'
    output = output_for('e1')
    output['requirements'][0]['evidence'] = '원문에 없는 근거'
    item['enrichment'] = validate_enrichment(output, 1, '제목', BODY, ['e1'])
    assert not any(f['type'] == 'requirement' and f['value']['type'] == 'grade'
                   for f in item['enrichment']['facts'])
    assert planner.eligibility(item, PROFILE) == 'needs_check'


def test_interest_direct_related_ownership_and_title_ignored():
    item = opportunity()
    assert planner.interest_match(item, ['career']) == {'direct': True, 'related': False, 'related_fact_refs': []}
    assert planner.interest_match(item, ['학사']) == {'direct': False, 'related': False, 'related_fact_refs': []}
    item['category'] = '학사'
    fact = add_fact(item, 'related_interest', 'career')
    add_fact(item, 'related_interest', '학사', 2)
    item['enrichment']['facts'].append({'fact_id': 'o999.int1', 'type': 'related_interest', 'value': 'career'})
    assert planner.interest_match(item, ['career', '학사']) == {
        'direct': True, 'related': True, 'related_fact_refs': [fact['fact_id']]}


@pytest.mark.parametrize('rule,step,target', [('date', 'verify', 'date'), ('grouping', 'verify', 'grouping'),
    ('window', 'verify', 'action_window'), ('confirmation', 'verify', 'application_confirmation'),
    ('prep', 'prepare', None), ('not_open', 'monitor', None), ('later', 'monitor', None), ('act', 'act', None)])
def test_priority_rules(rule, step, target):
    item = opportunity()
    event = item['events'][0]
    if rule == 'date':
        event['review_status'] = 'needs_review'
    elif rule == 'grouping':
        item['enrichment']['grouping_review_required'] = True
    elif rule == 'window':
        event['action_window'] = 'unknown'
    elif rule == 'confirmation':
        item.update(user_managed=True, application_expired_not_done=True)
        event['expired'] = True
    elif rule in ('prep', 'not_open'):
        event['action_window'] = 'not_open'
        if rule == 'prep':
            add_fact(item, 'prep_item', '준비')
    elif rule == 'later':
        event['urgency'] = 'later'
    assert planner.priority_context(item) == {
        'focus_event_id': 'e1', 'next_step_type': step, 'verify_target': target}


def test_priority_precedence_and_no_eligibility_override():
    item = opportunity(user_managed=True, application_expired_not_done=True)
    event = item['events'][0]
    event.update(expired=True, action_window='unknown', review_status='needs_review')
    item['enrichment']['grouping_review_required'] = True
    assert planner.priority_context(item)['verify_target'] == 'date'
    event['review_status'] = 'ok'
    assert planner.priority_context(item)['verify_target'] == 'grouping'
    item['enrichment']['grouping_review_required'] = False
    # 만료된 접수의 window=unknown 은 건너뛰고 신청 여부 확인으로 간다
    assert planner.priority_context(item)['verify_target'] == 'application_confirmation'
    item.update(application_expired_not_done=False, eligibility='unknown')
    event.update(expired=False)
    assert planner.priority_context(item)['verify_target'] == 'action_window'
    event['action_window'] = 'not_open'
    add_fact(item, 'prep_item', '준비')
    assert planner.priority_context(item)['next_step_type'] == 'prepare'
    event['action_window'] = 'open'
    assert planner.priority_context(item)['next_step_type'] == 'act'
    item['eligibility'] = 'needs_check'
    assert planner.priority_context(item)['next_step_type'] == 'act'


@pytest.mark.parametrize('reason', ['mandatory', 'early_close', 'none'])
def test_immediate_action_reasons(reason):
    item = opportunity()
    item['events'][0]['urgency'] = 'none'
    if reason == 'mandatory':
        item['enrichment']['is_mandatory'] = True
    elif reason == 'early_close':
        fact = add_fact(item, 'early_close', True)
        item['enrichment']['events'] = [{'event_id': 'e1', 'early_close_fact_id': fact['fact_id']}]
    assert planner.priority_context(item)['next_step_type'] == ('monitor' if reason == 'none' else 'act')


def test_focus_done_move_numeric_ties_unknown_dates_and_confirmation_fallback():
    item = opportunity()
    main = {**item['events'][0], 'event_id': 'e10', 'action_type': 'attend', 'action_date': '2030-10-10'}
    item['events'].append(main)
    assert planner.focus_event(item)['event_id'] == 'e1'
    item['events'][0]['action_status'] = 'done'
    assert planner.focus_event(item)['event_id'] == 'e10'
    item['events'].append({**main, 'event_id': 'e2'})
    assert planner.focus_event(item)['event_id'] == 'e2'
    main['action_date'] = None
    assert planner.focus_event(item)['event_id'] == 'e2'
    item['events'][0].update(action_status='pending', expired=True)
    item.update(user_managed=True, application_expired_not_done=True)
    item['events'].append({**item['events'][0], 'event_id': 'e11', 'action_date': '2030-10-09'})
    assert planner.focus_event(item)['event_id'] == 'e11'
    item.update(user_managed=False)
    assert planner.focus_event(item)['event_id'] == 'e2'
    for event in item['events']:
        event['action_status'] = 'done'
    assert planner.priority_context(item)['focus_event_id'] is None


def test_priority_sort_all_levels_and_irrelevant_fields():
    items = [opportunity(number) for number in range(1, 10)]
    items[0]['events'][0].update(urgency='soon')
    items[1]['enrichment']['is_mandatory'] = True
    early = add_fact(items[2], 'early_close', True)
    items[2]['enrichment']['events'] = [{'event_id': 'e3', 'early_close_fact_id': early['fact_id']}]
    items[3]['user_managed'] = True
    items[4]['events'][0]['action_window'] = 'not_open'
    items[5]['events'][0]['action_window'] = 'unknown'
    items[6]['events'][0]['action_date'] = '2030-10-08'
    add_fact(items[8], 'benefit', '혜택')
    add_fact(items[8], 'prep_item', '준비')
    items[8]['category'] = '관심 없음'
    requirement(items[8], value='애매')
    selected, _ = planner.select_candidates(list(reversed(items)), PROFILE, 'priority')
    assert [item['opportunity_id'] for item in selected] == ['o2', 'o3', 'o4', 'o7', 'o8', 'o9', 'o5', 'o6', 'o1']


@pytest.mark.parametrize('mode', ['priority', 'discover', 'focus'])
def test_candidate_filters(mode):
    items = [opportunity(number) for number in range(1, 10)]
    items[1]['dismissed'] = True
    requirement(items[2])
    items[3]['events'][0]['expired'] = True
    items[4]['events'][0]['action_status'] = 'done'
    items[5].update(application_expired_not_done=True, user_managed=True)
    items[5]['events'][0]['expired'] = True
    items[5]['events'].append({**items[0]['events'][0], 'event_id': 'e60', 'action_type': 'attend'})
    items[6]['category'] = '학사'
    items[7]['enrichment']['grouping_review_required'] = True
    items[8]['category'] = '학사'
    add_fact(items[8], 'related_interest', 'career')
    selected, grouping = planner.select_candidates(items, PROFILE, mode)
    expected = {'priority': {'o1', 'o6', 'o7', 'o8', 'o9'},
                'discover': {'o1', 'o7', 'o8', 'o9'}, 'focus': {'o1', 'o9'}}
    assert {item['opportunity_id'] for item in selected} == expected[mode]
    assert [item['opportunity_id'] for item in grouping] == (['o8'] if mode == 'focus' else [])


@pytest.mark.parametrize('mode', ['priority', 'discover', 'focus'])
def test_caps_determinism_and_no_mutation(mode):
    items = [opportunity(number) for number in range(30, 0, -1)]
    before = copy.deepcopy(items)
    selected, _ = planner.select_candidates(items, PROFILE, mode)
    assert len(selected) == planner.CANDIDATE_LIMITS[mode]
    assert [item['opportunity_id'] for item in selected] == [f'o{i}' for i in range(1, len(selected) + 1)]
    assert items == before
    assert planner.select_candidates(items, PROFILE, mode)[0] == selected


def test_payload_minimal_fields_verified_facts_single_bulk_call(db, monkeypatch):
    user = get_or_create_dev_user(db)
    pref = get_or_create_preference(db, user)
    pref.major, pref.grade, pref.enrollment_status, pref.interests = '소프트웨어학부', 3, 'enrolled', ['career']
    user.name, user.email, user.google_refresh_token = 'PRIVATE_NAME', 'PRIVATE_EMAIL', 'PRIVATE_TOKEN'
    db.commit()
    item = opportunity()
    for kind in ('benefit', 'prep_item', 'how_to_apply', 'related_interest'):
        add_fact(item, kind, 'career')
    requirement(item, value='3학년')
    calls = []
    monkeypatch.setattr(planner, 'all_opportunity_facts', lambda db, user, now: calls.append(now) or [item])
    for mode in ('priority', 'discover', 'focus'):
        result = planner.build_payload(db, user, mode, NOW)
        candidate = result['opportunities'][0]
        serialized = json.dumps(result)
        assert not any(secret in serialized for secret in ('PRIVATE_NAME', 'PRIVATE_EMAIL', 'PRIVATE_TOKEN', 'PRIVATE_EVIDENCE'))
        assert 'events' not in candidate and 'source_url' not in candidate and 'evidence' not in serialized
        assert all(set(fact) == {'fact_id', 'type', 'value'} for fact in candidate['facts'])
        common = {'opportunity_id', 'title', 'eligibility', 'facts', 'requirements', 'unparsed_requirements'}
        extras = {'priority': {'user_managed', 'priority_context', 'prep_items', 'how_to_apply'},
                  'discover': {'category', 'interest_match', 'enrichment_status', 'benefits', 'grouping_review_required', 'new_to_user'},
                  'focus': {'category', 'interest_match', 'enrichment_status', 'benefits', 'user_managed', 'calendar_conflict'}}
        assert set(candidate) == common | extras[mode]
        if mode == 'discover':
            assert result['user'] == PROFILE and candidate['new_to_user'] is None
        elif mode == 'priority':
            assert 'user' not in result
            assert {fact['type'] for fact in candidate['facts']} == {'requirement', 'prep_item', 'how_to_apply'}
        else:
            assert result['user'] == {'interests': ['career']} and result['pairwise_conflicts'] == []
    assert calls == [NOW] * 3


def test_payload_real_facts_enrichment_conflicts_and_grouping(db):
    user = get_or_create_dev_user(db)
    pref = get_or_create_preference(db, user)
    pref.interests = ['학사']
    notices = [make_notice(db) for _ in range(3)]
    for index, notice in enumerate(notices):
        event = make_event(db, notice)
        enriched = empty_enrichment('ok')
        db.add(NoticeEnrichment(notice_id=notice.id, state='done', prompt_version='enrich-v1',
                               grouping_review_required=index == 2, facts=enriched))
        if index == 0:
            db.add(UserEvent(user_id=user.id, event_id=event.id, registered=True))
    db.commit()
    result = planner.build_payload(db, user, 'focus', NOW)
    ids = [f'o{notice.id}' for notice in notices]
    assert [item['opportunity_id'] for item in result['opportunities']] == ids[:2]
    assert result['pairwise_conflicts'] == [ids[:2]]
    assert result['opportunities'][1]['calendar_conflict'] is True
    assert result['needs_grouping_check'] == [{'opportunity_id': ids[2], 'title': '활동'}]


def test_api_validation_current_user_visibility_and_authentication(client, db, monkeypatch):
    user = get_or_create_dev_user(db)
    pref = get_or_create_preference(db, user)
    pref.interests = ['학사']
    visible, hidden, disabled = [make_notice(db) for _ in range(3)]
    event = make_event(db, visible, start_date='2099-10-08', end_date='2099-10-09')
    make_event(db, hidden, review_status='needs_review')
    make_event(db, disabled, source='wevity')
    pref.enabled_sources = {'wevity': False}
    other = User(name='other', email='other@local')
    db.add(other)
    db.flush()
    db.add_all([UserEvent(user_id=other.id, event_id=event.id, bookmarked=True, action_status='done'),
                UserNotice(user_id=other.id, notice_id=visible.id, dismissed=True)])
    db.commit()
    for mode in ('priority', 'discover', 'focus'):
        response = client.get(f'/api/internal/planner/payload?mode={mode}')
        assert response.status_code == 200
        assert [item['opportunity_id'] for item in response.json()['opportunities']] == [f'o{visible.id}']
    assert client.get('/api/internal/planner/payload?mode=study').status_code == 400
    assert client.get('/api/internal/planner/payload').status_code == 400
    other_pref = get_or_create_preference(db, other)
    other_pref.enabled_sources = {'wevity': False}
    db.commit()
    app.dependency_overrides[get_current_user] = lambda: other
    try:
        assert planner.build_payload(db, other, 'priority', NOW)['opportunities'] == []
        assert client.get('/api/internal/planner/payload?mode=priority').json()['opportunities'] == []
    finally:
        app.dependency_overrides.pop(get_current_user)
    monkeypatch.setattr('app.deps.get_settings', lambda: type('Settings', (), {'dev_login': False})())
    assert client.get('/api/internal/planner/payload?mode=priority').status_code == 401


def test_invalid_mode_and_naive_time(db):
    user = get_or_create_dev_user(db)
    with pytest.raises(ValueError):
        planner.build_payload(db, user, 'study', NOW)
    notice = make_notice(db)
    make_event(db, notice)
    db.commit()
    with pytest.raises(ValueError):
        planner.build_payload(db, user, 'priority', NOW.replace(tzinfo=None))


def test_real_expired_application_rule_order_and_done_advance(db):
    user = get_or_create_dev_user(db)
    notice = make_notice(db)
    application = make_event(db, notice, event_type='application', start_date='2030-10-01',
                             end_date='2030-10-07', start_time='', end_time='')
    main = make_event(db, notice, start_date='2030-10-10', end_date='2030-10-10')
    state = UserEvent(user_id=user.id, event_id=application.id, bookmarked=True)
    db.add(state)
    db.commit()
    result = planner.build_payload(db, user, 'priority', NOW)
    # 만료된 접수의 window=unknown 은 무시하고 신청 여부 확인 흐름을 유지한다
    assert result['opportunities'][0]['priority_context'] == {
        'focus_event_id': f'e{application.id}', 'next_step_type': 'verify', 'verify_target': 'application_confirmation'}
    assert planner.build_payload(db, user, 'discover', NOW)['opportunities'] == []
    state.action_status = 'done'
    db.commit()
    assert planner.build_payload(db, user, 'priority', NOW)['opportunities'][0]['priority_context']['focus_event_id'] == f'e{main.id}'
    assert len(planner.build_payload(db, user, 'discover', NOW)['opportunities']) == 1


def test_sort_urgency_all_bands_before_other_signals_and_notice_ties():
    items = []
    for index, urgency in enumerate(('none', 'later', 'upcoming', 'soon', 'urgent'), 1):
        item = opportunity(index)
        item['events'][0]['urgency'] = urgency
        item['enrichment']['is_mandatory'] = urgency != 'urgent'
        items.append(item)
    selected, _ = planner.select_candidates(items, PROFILE, 'priority')
    assert [item['events'][0]['urgency'] for item in selected] == ['urgent', 'soon', 'upcoming', 'later', 'none']
    same = [opportunity(10), opportunity(2)]
    same[0]['events'][0]['event_id'] = 'e1'
    same[1]['events'][0]['event_id'] = 'e100'
    assert [item['opportunity_id'] for item in sorted(same, key=planner.priority_sort_key)] == ['o2', 'o10']


def test_focus_conflicts_only_selected_candidates_and_grouping_not_capped(db, monkeypatch):
    user = get_or_create_dev_user(db)
    pref = get_or_create_preference(db, user)
    pref.interests = ['career']
    db.commit()
    items = [opportunity(index) for index in range(1, 20)]
    for item in items:
        item['events'][0].update(action_type='attend', start_date='2030-10-09', end_date='2030-10-09',
                                 start_time='13:00', end_time='14:00', timezone='Asia/Seoul',
                                 title='일정', detail='', source_url='https://example.com/event', location='')
    items[-1]['enrichment']['grouping_review_required'] = True
    monkeypatch.setattr(planner, 'all_opportunity_facts', lambda *args: items)
    result = planner.build_payload(db, user, 'focus', NOW)
    selected_ids = {item['opportunity_id'] for item in result['opportunities']}
    assert len(selected_ids) == 12
    assert len(result['pairwise_conflicts']) == 66
    assert all(set(pair) <= selected_ids for pair in result['pairwise_conflicts'])
    assert result['needs_grouping_check'] == [{'opportunity_id': 'o19', 'title': 'career 진로 공지'}]


def test_preferred_conditions_do_not_make_eligibility_uncertain():
    item = opportunity()
    item['enrichment']['enrichment_status'] = 'ok'
    add_fact(item, 'requirement', {'type': 'major', 'value': '컴퓨터공학', 'required': False})
    add_fact(item, 'requirement', {'type': 'other', 'value': '포트폴리오 우대', 'required': False})
    profile = {**PROFILE, 'major': ''}
    assert planner.eligibility(item, profile) == 'eligible'
    add_fact(item, 'requirement', {'type': 'other', 'value': '팀 단위 지원', 'required': True})
    assert planner.eligibility(item, profile) == 'needs_check'
