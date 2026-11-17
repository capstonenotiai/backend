"""모드 이름에 맞는 후보 고르기 (2026-11-17): 새 기회=미관리·최근 수집, 집중할 것=마감 구간 분산, 먼저 할 일=지난 접수 제외."""
from datetime import timedelta

from app.services import planner_context as planner
from test_planner_context import PROFILE, opportunity, requirement
from test_planner_opportunities import NOW


def at(item, days):
    item['events'][0]['action_date'] = (NOW.date() + timedelta(days=days)).isoformat()
    return item


def test_discover_prefers_recently_collected_and_skips_managed():
    old, new, saved, unknown = (at(opportunity(i), 10) for i in range(1, 5))
    old['collected_at'] = '2030-09-01T09:00:00'
    new['collected_at'] = '2030-10-05T09:00:00'
    saved.update(collected_at='2030-10-06T09:00:00', user_managed=True)
    unknown['collected_at'] = None
    selected, _ = planner.select_candidates([old, unknown, saved, new], PROFILE, 'discover', NOW)
    assert [item['opportunity_id'] for item in selected] == ['o2', 'o1', 'o4']


def test_focus_spreads_deadlines_but_keeps_rank_order():
    soon = [at(opportunity(i), 2) for i in range(1, 15)]
    later = [at(opportunity(i), 10) for i in range(20, 24)]
    selected, _ = planner.select_candidates(soon + later, PROFILE, 'focus', NOW)
    ids = [item['opportunity_id'] for item in selected]
    assert len(ids) == planner.CANDIDATE_LIMITS['focus']
    assert {'o20', 'o21', 'o22', 'o23'} <= set(ids)  # 마감 임박 공지가 자리를 독차지하지 않는다
    assert ids == sorted(ids, key=lambda value: [item['opportunity_id'] for item in soon + later].index(value))


def test_priority_skips_expired_application_confirmation():
    expired = opportunity(1, user_managed=True, application_expired_not_done=True)
    expired['events'][0]['expired'] = True
    active = at(opportunity(2, user_managed=True), 1)
    selected, _ = planner.select_candidates([expired, active], PROFILE, 'priority', NOW)
    assert [item['opportunity_id'] for item in selected] == ['o2']


def test_no_interests_does_not_empty_priority_or_focus():
    items = [at(opportunity(i), 5) for i in range(1, 4)]
    profile = {**PROFILE, 'interests': []}
    for mode in ('priority', 'focus'):
        selected, _ = planner.select_candidates(items, profile, mode, NOW)
        assert len(selected) == 3, mode


def test_discover_needs_five_days_and_confirmed_eligibility_first():
    near, far_unsure, far_ok = at(opportunity(1), 4), at(opportunity(2), 6), at(opportunity(3), 9)
    for item in (near, far_unsure, far_ok):
        item['collected_at'] = '2030-10-07T09:00:00'
    requirement(far_unsure, value='애매')  # 해석 못 한 학년 조건 → 자격 확인 필요
    selected, _ = planner.select_candidates([near, far_unsure, far_ok], PROFILE, 'discover', NOW)
    assert [item['eligibility'] for item in selected] == ['eligible', 'needs_check']
    assert [item['opportunity_id'] for item in selected] == ['o3', 'o2']  # 4일 남은 o1 은 제외


def test_focus_prefers_comparable_candidates():
    # 순위가 낮아도 시간이 겹치는 쌍과 같은 분야 묶음을 먼저 고른다.
    def attend(item, start, end):
        item['events'][0].update(action_type='attend', start_date='2030-10-15', end_date='2030-10-15', start_time=start,
                                 end_time=end, timezone='Asia/Seoul', title='일정', detail='', source_url='https://e', location='')
        return item
    solo = [at(opportunity(i, category=f'c{i}'), 5 + i) for i in range(1, 15)]  # 각자 다른 분야, 겹침 없음
    for item in solo:
        item['interest_match'] = {'direct': True, 'related': False, 'related_fact_refs': []}
    left, right = attend(at(opportunity(50), 7), '13:00', '15:00'), attend(at(opportunity(51), 7), '14:00', '16:00')
    pair = [at(opportunity(60, category='same'), 9), at(opportunity(61, category='same'), 10)]
    profile = {**PROFILE, 'interests': []}  # 관심 조건 없이 후보 순서만 본다
    selected, _ = planner.select_candidates(solo + [left, right] + pair, profile, 'focus', NOW)
    ids = {item['opportunity_id'] for item in selected}
    assert {'o50', 'o51', 'o60', 'o61'} <= ids and len(ids) == planner.CANDIDATE_LIMITS['focus']


def test_focus_server_items_carry_category_and_benefit(db, monkeypatch):
    from app.deps import get_or_create_dev_user
    from test_planner_context import add_fact
    item = at(opportunity(1, source_url='https://example.com/n'), 6)
    item['events'][0].update(start_date=item['events'][0]['action_date'], end_date=item['events'][0]['action_date'], start_time='', end_time='')
    add_fact(item, 'benefit', '상금 100만원')
    monkeypatch.setattr(planner, 'all_opportunity_facts', lambda *args: [item])
    _, server_items = planner.build_recommendation_context(db, get_or_create_dev_user(db), 'focus', NOW)
    assert server_items['o1']['category'] == 'career' and server_items['o1']['benefit'] == '상금 100만원'


def _attend(item, start, end, day='2030-10-15'):
    item['events'][0].update(action_type='attend', start_date=day, end_date=day, start_time=start, end_time=end,
                             timezone='Asia/Seoul', title='일정', detail='', source_url='https://e', location='')
    return item


def test_comparison_groups_are_deterministic():
    a, b, c = _attend(opportunity(1), '13:00', '15:00'), _attend(opportunity(2), '14:00', '16:00'), _attend(opportunity(3), '15:30', '17:00')
    x, y = opportunity(4, category='contest'), opportunity(5, category='contest')
    solo = opportunity(6, category='academic')
    items = [a, b, c, x, y, solo]
    groups = planner.comparison_groups(items)
    assert groups == [{'group_id': 'g1', 'kind': 'time', 'members': ['o1', 'o2', 'o3']},
                      {'group_id': 'g2', 'kind': 'similar', 'members': ['o4', 'o5']}]
    assert planner.comparison_groups(items) == groups  # 같은 입력이면 같은 묶음


def test_enforce_one_core_per_group():
    items = [{'opportunity_id': f'o{i}', 'tier': tier, 'secondary_reason': None if tier == 'core' else 'information_uncertain',
              'chosen_over': [], 'conflicts_with': []} for i, tier in [(1, 'secondary'), (2, 'core'), (3, 'core'), (4, 'secondary'), (5, 'secondary')]]
    groups = [{'group_id': 'g1', 'kind': 'time', 'members': ['o1', 'o2', 'o3']}, {'group_id': 'g2', 'kind': 'similar', 'members': ['o4', 'o5']}]
    assert planner.enforce_comparison_groups(items, groups) is True
    tiers = {item['opportunity_id']: (item['tier'], item['secondary_reason']) for item in items}
    assert tiers == {'o1': ('secondary', 'schedule_conflict'), 'o2': ('core', None), 'o3': ('secondary', 'schedule_conflict'),
                     'o4': ('core', None), 'o5': ('secondary', 'stronger_alternative')}
    assert items[1]['conflicts_with'] == ['o1', 'o3'] and items[3]['chosen_over'] == ['o5']
    assert planner.enforce_comparison_groups(items, groups) is False  # 이미 맞으면 고치지 않음
