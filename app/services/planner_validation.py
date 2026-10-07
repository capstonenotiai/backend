"""Repair model judgments without mutating payloads, facts or user state."""


def _default(item, mode):
    result = {'opportunity_id': item['opportunity_id'], 'fact_refs': [],
              'reason': '확인된 활동 정보와 공지를 살펴본 뒤 참여 여부를 검토해 주세요.'}
    if mode == 'priority':
        step = item['priority_context']['next_step_type']
        target = item['priority_context']['verify_target']
        actions = {'act': '공지에 안내된 방법을 확인하고 다음 행동을 진행해 주세요.',
                   'prepare': '공지에 안내된 준비물을 미리 확인해 주세요.',
                   'verify': '공지에서 행동 가능 시점과 일정 정보를 확인해 주세요.',
                   'monitor': '추후 행동 시점을 다시 확인해 주세요.'}
        if step == 'verify':
            actions[step] = {'date': '공지에서 일정 날짜를 확인해 주세요.',
                             'grouping': '공지에서 같은 활동에 속한 일정인지 확인해 주세요.',
                             'action_window': '공지에서 행동 가능한 시점을 확인해 주세요.',
                             'application_confirmation': '실제 신청을 완료했는지 확인해 주세요.'}.get(target, actions[step])
        result.update(next_step_label=step, next_action=actions[step], reason={
            'act': '서버가 정한 순서에서 현재 진행할 다음 행동이 있는 활동입니다.',
            'prepare': '행동 가능 시점 전이며 확인된 준비 항목이 있는 활동입니다.',
            'verify': '서버가 다음 행동 전에 확인할 정보가 있다고 판단한 활동입니다.',
            'monitor': '서버가 추후 행동 시점을 확인하도록 분류한 활동입니다.'}[step])
    elif mode == 'discover':
        result.update(grade='potential_match', check_reasons=[])
    elif mode == 'focus':
        result.update(tier='secondary', secondary_reason='information_uncertain', chosen_over=[], conflicts_with=[])
    else:
        raise ValueError('Unknown planner mode')
    return result


def validate_output(payload, output):
    mode = payload['mode']
    source = {item['opportunity_id']: item for item in payload['opportunities']}
    corrections = []

    def note(code):
        if code not in corrections:
            corrections.append(code)

    raw_items = output.get('items') if isinstance(output, dict) else None
    if not isinstance(raw_items, list):
        note('invalid_output_replaced')
        raw_items = []
    selected = {}
    for raw in raw_items:
        if not isinstance(raw, dict):
            note('invalid_item_removed')
            continue
        oid = raw.get('opportunity_id')
        if not isinstance(oid, str) or oid not in source:
            note('unknown_opportunity_removed')
            continue
        if oid in selected:
            note('duplicate_opportunity_removed')
            continue
        selected[oid] = raw
    if mode == 'priority' and list(selected) != [oid for oid in source if oid in selected]:
        note('priority_order_restored')

    def strings(raw, field):
        value = raw.get(field)
        result = list(dict.fromkeys(item for item in value if isinstance(item, str) and item.strip())) if isinstance(value, list) else []
        if result != value:
            note('invalid_' + field + '_repaired')
        return result

    items = []
    for oid, source_item in source.items():
        default = _default(source_item, mode)
        raw = selected.get(oid)
        if raw is None:
            note('missing_opportunity_restored')
            raw = default
        enum_valid = (raw.get('next_step_label') in ('act', 'prepare', 'verify', 'monitor') if mode == 'priority' else
                      raw.get('grade') in ('strong_match', 'potential_match', 'not_recommended') if mode == 'discover' else
                      raw.get('tier') in ('core', 'secondary') and raw.get('secondary_reason') in (
                          'stronger_alternative', 'schedule_conflict', 'information_uncertain', None))
        if not enum_valid:
            note('invalid_enum_replaced')
            raw = default
        item = {field: raw.get(field) for field in default}
        for field in ('reason', 'next_action') if mode == 'priority' else ('reason',):
            if not isinstance(item[field], str) or not item[field].strip():
                item[field] = default[field]
                note('invalid_text_replaced')
        allowed_refs = {fact['fact_id'] for fact in source_item['facts']}
        refs = strings(raw, 'fact_refs')
        item['fact_refs'] = [ref for ref in refs if ref in allowed_refs]
        if item['fact_refs'] != refs:
            note('invalid_fact_ref_removed')
        if mode == 'priority':
            step = source_item['priority_context']['next_step_type']
            if item['next_step_label'] != step:
                # Replace the explanation too: it may direct an action contrary to server facts.
                item.update(next_step_label=step, reason=default['reason'], next_action=default['next_action'], fact_refs=[])
                note('next_step_restored')
        elif mode == 'discover':
            item['check_reasons'] = strings(raw, 'check_reasons')
            caps = [('eligibility', source_item['eligibility'] in ('needs_check', 'unknown')),
                    ('low_confidence', source_item['enrichment_status'] == 'low_confidence'),
                    ('grouping', source_item['grouping_review_required'])]
            if item['grade'] == 'strong_match' and any(enabled for _, enabled in caps):
                item['grade'] = 'potential_match'
                for code, enabled in caps:
                    if enabled:
                        note('discover_cap_' + code)
            for requirement in source_item['unparsed_requirements']:
                text = f'지원 조건을 확인해 주세요: {requirement}'
                if not any(requirement in reason for reason in item['check_reasons']):
                    item['check_reasons'].append(text)
                    note('unparsed_requirement_added')
        else:
            item['chosen_over'] = strings(raw, 'chosen_over')
            item['conflicts_with'] = strings(raw, 'conflicts_with')
        items.append(item)

    if mode == 'focus':
        by_id = {item['opportunity_id']: item for item in items}
        pairs = {frozenset(pair) for pair in payload['pairwise_conflicts']}
        for item in items:
            oid = item['opportunity_id']
            chosen = [other for other in item['chosen_over'] if other in by_id and other != oid
                      and item['tier'] == 'core' and by_id[other]['tier'] == 'secondary']
            if chosen != item['chosen_over']:
                note('inconsistent_chosen_over_removed')
            item['chosen_over'] = chosen
            conflicts = [other for other in item['conflicts_with'] if other in by_id and other != oid
                         and frozenset((oid, other)) in pairs]
            if conflicts != item['conflicts_with']:
                note('unverified_conflict_removed')
            item['conflicts_with'] = conflicts
            if item['tier'] == 'core' and item['secondary_reason'] is not None:
                item['secondary_reason'] = None
                note('core_secondary_reason_removed')
            elif item['tier'] == 'secondary':
                reason = item['secondary_reason']
                if conflicts and reason != 'schedule_conflict':
                    item['secondary_reason'] = 'schedule_conflict'
                    note('schedule_conflict_enforced')
                elif not conflicts and reason in (None, 'schedule_conflict'):
                    item['secondary_reason'] = 'information_uncertain'
                    note('secondary_reason_repaired')
        winners = {other for item in items if item['tier'] == 'core' for other in item['chosen_over']}
        for item in items:
            if item['secondary_reason'] == 'stronger_alternative' and item['opportunity_id'] not in winners:
                item['secondary_reason'] = 'information_uncertain'
                note('unsupported_stronger_alternative_removed')
    return items, corrections
