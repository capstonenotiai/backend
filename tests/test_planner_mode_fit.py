"""모드 이름에 맞는 후보 고르기 (2026-11-17): 새 기회=미관리·최근 수집, 집중할 것=마감 구간 분산, 먼저 할 일=지난 접수 제외."""
from datetime import timedelta

from app.services import planner_context as planner
from test_planner_context import PROFILE, opportunity
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
