"""Deterministic planner gates, ordering and minimal mode payloads. No GPT calls."""
import re
from datetime import datetime

from app.services.planner_facts import SEOUL, all_opportunity_facts, pairwise_conflicts
from app.services.preferences import get_or_create_preference

CANDIDATE_LIMITS = {'priority': 15, 'discover': 20, 'focus': 12}
URGENCY_ORDER = {'urgent': 0, 'soon': 1, 'upcoming': 2, 'later': 3, 'none': 4}
WINDOW_ORDER = {'open': 0, 'not_open': 1, 'unknown': 2}


def parse_grade(value):
    """Only accept complete, unambiguous grade expressions (profile grades 1..6)."""
    if not isinstance(value, str):
        return None
    value = re.sub(r'\s+', '', value)
    match = re.fullmatch(r'([1-6])(?:학년)?[~～\-–]([1-6])학년', value)
    if match:
        lower, upper = map(int, match.groups())
        return set(range(lower, upper + 1)) if lower <= upper else None
    match = re.fullmatch(r'([1-6])학년(이상|이하)?', value)
    if not match:
        return None
    grade, bound = int(match[1]), match[2]
    return set(range(grade, 7)) if bound == '이상' else set(range(1, grade + 1)) if bound == '이하' else {grade}


def parse_enrollment_status(value):
    aliases = {'enrolled': 'enrolled', '재학': 'enrolled', '재학생': 'enrolled',
               'leave': 'leave', '휴학': 'leave', '휴학생': 'leave',
               'graduated': 'graduated', '졸업': 'graduated', '졸업생': 'graduated'}
    if not isinstance(value, str):
        return None
    parts = re.split(r'\s*(?:/|,|또는|및)\s*', value.strip())
    if not parts or any(part not in aliases for part in parts):
        return None
    return {aliases[part] for part in parts}


def validated_facts(opportunity):
    # B8B's facts list contains only evidence-validated claims. Check ownership too.
    prefix = opportunity['opportunity_id'] + '.'
    return [fact for fact in opportunity['enrichment']['facts']
            if isinstance(fact.get('fact_id'), str) and fact['fact_id'].startswith(prefix)]


def requirement_comparisons(opportunity, profile):
    """Keep major comparisons for inspection, but never use them for exclusion."""
    comparisons = []
    for fact in validated_facts(opportunity):
        if fact['type'] != 'requirement':
            continue
        claim = fact['value']
        kind, value = claim['type'], claim['value']
        actual = profile.get(kind)
        result = None
        if kind == 'grade':
            allowed = parse_grade(value)
            if allowed is not None and type(actual) is int and 1 <= actual <= 6:
                result = actual in allowed
        elif kind == 'enrollment_status':
            allowed = parse_enrollment_status(value)
            if allowed is not None and actual in ('enrolled', 'leave', 'graduated'):
                result = actual in allowed
        elif kind == 'major' and actual and actual != 'unknown' and value.strip():
            result = actual.strip() == value.strip()
        comparisons.append({'fact_id': fact['fact_id'], 'type': kind,
                            'required': claim['required'], 'matches': result})
    return comparisons


def eligibility(opportunity, profile):
    enrichment = opportunity['enrichment']
    comparisons = requirement_comparisons(opportunity, profile)
    # Low confidence may mean B8B removed a required claim whose evidence failed.
    uncertain = enrichment['enrichment_status'] == 'low_confidence'
    grouped = enrichment['grouping_review_required']
    for comparison in comparisons:
        if (comparison['type'] in ('grade', 'enrollment_status') and comparison['required'] is True
                and comparison['matches'] is False and not grouped):
            return 'ineligible'
        # 우대 조건(required=false)은 자격 판정을 흐리지 않는다
        if comparison['required'] is True and (
                comparison['matches'] is None or comparison['type'] not in ('grade', 'enrollment_status', 'major')):
            uncertain = True
    if any(fact['type'] == 'unparsed_requirement' for fact in validated_facts(opportunity)):
        uncertain = True
    if grouped and comparisons:
        uncertain = True
    if uncertain:
        return 'needs_check'
    if not comparisons and enrichment['enrichment_status'] == 'missing':
        return 'unknown'
    return 'eligible'


def interest_match(opportunity, interests):
    refs = sorted(fact['fact_id'] for fact in validated_facts(opportunity)
                  if fact['type'] == 'related_interest' and fact['value'] in interests
                  and fact['value'] != opportunity['category'])
    return {'direct': opportunity['category'] in interests, 'related': bool(refs),
            'related_fact_refs': refs}


def _id_number(value):
    return int(value[1:])


def _event_key(event):
    return (event['action_date'] or '9999-12-31', _id_number(event['event_id']))


def focus_event(opportunity):
    events = opportunity['events']
    if opportunity['user_managed'] and opportunity['application_expired_not_done']:
        return max((event for event in events if event['action_type'] in ('apply', 'submit')),
                   key=_event_key, default=None)
    return min((event for event in events if event['action_status'] == 'pending'
                and event['expired'] is not True), key=_event_key, default=None)


def _early_close(opportunity, focus):
    ids = {fact['fact_id'] for fact in validated_facts(opportunity)
           if fact['type'] == 'early_close' and fact['value'] is True}
    return bool(focus and any(event['event_id'] == focus['event_id']
                             and event.get('early_close_fact_id') in ids
                             for event in opportunity['enrichment']['events']))


def priority_context(opportunity):
    focus = focus_event(opportunity)
    result = {'focus_event_id': focus['event_id'] if focus else None,
              'next_step_type': 'monitor', 'verify_target': None}
    if not focus:
        return result
    enrichment = opportunity['enrichment']
    target = ('date' if focus['review_status'] == 'needs_review' else
              'grouping' if enrichment['grouping_review_required'] else
              # 만료된 접수(신청 여부 확인 흐름)의 window=unknown 은 만료 표시일 뿐이므로 건너뛴다
              'action_window' if focus['action_window'] == 'unknown' and focus['expired'] is not True else
              'application_confirmation' if opportunity['application_expired_not_done']
              and opportunity['user_managed'] else None)
    if target:
        result.update(next_step_type='verify', verify_target=target)
    elif focus['action_window'] == 'not_open':
        result['next_step_type'] = 'prepare' if any(
            fact['type'] == 'prep_item' for fact in validated_facts(opportunity)) else 'monitor'
    elif (focus['urgency'] in ('later', 'none') and not enrichment['is_mandatory']
          and not _early_close(opportunity, focus)):
        result['next_step_type'] = 'monitor'
    else:
        result['next_step_type'] = 'act'
    return result


def priority_sort_key(opportunity):
    focus = focus_event(opportunity)
    day = (focus['action_date'] or '9999-12-31') if focus else '9999-12-31'
    return (URGENCY_ORDER[focus['urgency']] if focus else 4,
            not opportunity['enrichment']['is_mandatory'], not _early_close(opportunity, focus),
            not opportunity['user_managed'], WINDOW_ORDER[focus['action_window']] if focus else 2,
            day,
            _id_number(opportunity['opportunity_id']))


def _discover_candidate(opportunity):
    events = opportunity['events']
    return (bool(events) and not opportunity['dismissed'] and opportunity['eligibility'] != 'ineligible'
            and not all(event['expired'] is True for event in events)
            and not all(event['action_status'] == 'done' for event in events)
            and not opportunity['application_expired_not_done'])


def _deadline_key(opportunity):
    focus = focus_event(opportunity)
    day = (focus['action_date'] or '9999-12-31') if focus else '9999-12-31'
    return (day, _id_number(opportunity['opportunity_id']))


def select_candidates(opportunities, profile, mode):
    if mode not in CANDIDATE_LIMITS:
        raise ValueError('Unknown planner mode')
    prepared = [{**item, 'eligibility': eligibility(item, profile),
                 'interest_match': interest_match(item, profile['interests']),
                 'priority_context': priority_context(item),
                 'requirement_comparisons': requirement_comparisons(item, profile)} for item in opportunities]
    grouping = []
    if mode == 'priority':
        candidates = [item for item in prepared if not item['dismissed']
                      and item['eligibility'] != 'ineligible' and focus_event(item)]
        candidates.sort(key=priority_sort_key)
    else:
        candidates = sorted((item for item in prepared if _discover_candidate(item)), key=_deadline_key)
        if mode == 'focus':
            candidates = [item for item in candidates if item['interest_match']['direct'] or item['interest_match']['related']]
            grouping = [item for item in candidates if item['enrichment']['grouping_review_required']]
            candidates = [item for item in candidates if not item['enrichment']['grouping_review_required']]
    return candidates[:CANDIDATE_LIMITS[mode]], grouping


def _payload_opportunity(item, mode):
    facts = validated_facts(item)
    if mode == 'priority':
        focus = focus_event(item)
        early_ids = {event.get('early_close_fact_id') for event in item['enrichment']['events']
                     if focus and event['event_id'] == focus['event_id']}
        facts = [fact for fact in facts if fact['type'] in (
            'prep_item', 'how_to_apply', 'requirement', 'unparsed_requirement')
            or fact['type'] == 'early_close' and fact['fact_id'] in early_ids]
    output = {key: item[key] for key in ('opportunity_id', 'title', 'eligibility')}
    output['facts'] = [{key: fact[key] for key in ('fact_id', 'type', 'value')} for fact in facts]
    for field, kind in (('requirements', 'requirement'), ('unparsed_requirements', 'unparsed_requirement')):
        output[field] = [fact['value'] for fact in facts if fact['type'] == kind]
    if mode == 'priority':
        output.update(user_managed=item['user_managed'], priority_context=item['priority_context'])
        for field, kind in (('prep_items', 'prep_item'), ('how_to_apply', 'how_to_apply')):
            output[field] = [fact['value'] for fact in facts if fact['type'] == kind]
    else:
        output.update(category=item['category'], interest_match=item['interest_match'],
                      enrichment_status=item['enrichment']['enrichment_status'],
                      benefits=[fact['value'] for fact in facts if fact['type'] == 'benefit'])
        if mode == 'discover':
            output.update(grouping_review_required=item['enrichment']['grouping_review_required'], new_to_user=None)
        else:
            output.update(user_managed=item['user_managed'], calendar_conflict=item['calendar_conflict'])
    return output


def _build_context(db, user, mode, now=None):
    if mode not in CANDIDATE_LIMITS:
        raise ValueError('Unknown planner mode')
    now = now or datetime.now(SEOUL)
    if now.tzinfo is None:
        raise ValueError('Facts require a timezone-aware now')
    pref = get_or_create_preference(db, user)
    profile = {'major': pref.major, 'grade': pref.grade, 'enrollment_status': pref.enrollment_status,
               'interests': pref.interests or []}
    opportunities = all_opportunity_facts(db, user, now)
    candidates, grouping = select_candidates(opportunities, profile, mode)
    result = {'mode': mode, 'opportunities': [_payload_opportunity(item, mode) for item in candidates]}
    if mode == 'discover':
        result['user'] = profile
    elif mode == 'focus':
        result.update(user={'interests': profile['interests']}, pairwise_conflicts=pairwise_conflicts(candidates),
                      needs_grouping_check=[{'opportunity_id': item['opportunity_id'], 'title': item['title']}
                                            for item in grouping])
    return result, candidates, now


def build_payload(db, user, mode, now=None):
    return _build_context(db, user, mode, now)[0]


def build_recommendation_context(db, user, mode, now=None):
    payload, candidates, now = _build_context(db, user, mode, now)
    server_items = {}
    for candidate, prompt_item in zip(candidates, payload['opportunities']):
        focus = focus_event(candidate)
        day = focus['action_date'] if focus else None
        server_items[candidate['opportunity_id']] = {
            'title': candidate['title'], 'source_url': candidate['source_url'],
            'focus_event_id': focus['event_id'] if focus else None,
            **{field: focus[field] if focus else None for field in (
                'start_date', 'end_date', 'start_time', 'end_time', 'action_date')},
            'days_until_deadline': (datetime.fromisoformat(day).date() - now.astimezone(SEOUL).date()).days if day else None,
            'eligibility': candidate['eligibility'], 'interest_match': candidate['interest_match'],
            'priority_context': candidate['priority_context'],
            'next_step_type': candidate['priority_context']['next_step_type'],
        }
        if mode == 'priority':
            prompt_item.update(urgency=focus['urgency'] if focus else 'none',
                              action_window=focus['action_window'] if focus else 'unknown',
                              is_mandatory=candidate['enrichment']['is_mandatory'],
                              early_close=_early_close(candidate, focus))
    return payload, server_items
