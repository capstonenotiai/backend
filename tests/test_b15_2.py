from pathlib import Path

import pytest

from app.models import Event, Notice
from app.services.category import contestkorea_metadata_board, recategorize, source_board
from app.services.contestkorea import recategorize_contestkorea
from crawler import contestkorea as ck
from test_crawler_metadata import FakeResponse


@pytest.mark.parametrize('field,expected', [
    ('서포터즈 • 기자단', 'activity'), (' 서포터즈· 기자단\n', 'activity'),
    ('교육 • 강연 • 멘토링 • 세미나', 'activity'), ('교육·멘토링', 'activity'),
    ('전시 행사 축제', 'activity'), ('기타 대외활동', 'activity'),
    ('기획·홍보·마케팅', 'activity'), ('문학•문예', 'contest'),
    ('기타 공모전', 'contest'), ('학문 · 과학 • IT', 'contest'),
    ('서포터즈 공모전', None), ('문학·문예·서포터즈', None),
    ('모름', None), ('', None), (None, None), ([], None),
])
def test_field_boundaries(field, expected):
    metadata = {'대표분야': field, 'category_code': '040110001'}
    assert contestkorea_metadata_board(metadata) == expected
    assert source_board('contestkorea', 'contest', metadata, '?Txt_bcode=040110001') == expected


@pytest.mark.parametrize('metadata', [None, {}, [], {'category_code': '040110001'}])
def test_missing_field(metadata):
    assert contestkorea_metadata_board(metadata) is None


@pytest.mark.parametrize('code,expected', [('040110001', 'activity'), ('040410001', 'activity'),
                                          ('030110001', 'contest')])
def test_real_detail_field_overrides_list_code(monkeypatch, code, expected):
    html = (Path(__file__).parent / 'fixtures/contestkorea' / f'field_{code}.html').read_text(encoding='utf-8')
    monkeypatch.setattr(ck, 'get', lambda url: FakeResponse(html))
    opposite = '030110001' if expected == 'activity' else '040110001'
    result = ck.parse_detail_page(f'https://contestkorea.com/sub/view.php?Txt_bcode={opposite}&str_no=202610160008')
    assert result['board'] == expected
    monkeypatch.setattr(ck, 'get_total_pages', lambda code: 1)
    monkeypatch.setattr(ck, 'parse_list_page', lambda page, code: [
        {'source_url': result['source_url'], 'title_raw': '목록 제목'}])
    records = ck.crawl(categories=[opposite], max_pages=1)
    assert records[0]['board'] == expected
    assert records[0]['category_code'] == opposite


@pytest.mark.parametrize('dry_run', [True, False])
def test_bidirectional_cli_offline_and_shared_rule(db, monkeypatch, capsys, dry_run):
    from app import cli
    import requests
    monkeypatch.setattr(requests.sessions.Session, 'request', lambda *a, **kw: pytest.fail('네트워크 금지'))
    monkeypatch.setattr(ck, 'activity_list_codes', lambda: pytest.fail('목록 순회 금지'))
    cases = [('contest', 'activity', '서포터즈 • 기자단'),
             ('activity', 'contest', '문학•문예'),
             ('activity', 'activity', '문학상 미확인'),
             ('contest', 'contest', None)]
    notices, events = [], []
    for i, (old, new, field) in enumerate(cases):
        notice = Notice(site='contestkorea', board=old, title_raw=f'예시{i}',
                        source_url=f'https://example.com/{i}',
                        source_metadata={'대표분야': field, 'category_code': '030110001', 'category': '보존'})
        db.add(notice)
        db.flush()
        # Multiple events must follow the same correction without changing other fields.
        for j in range(2):
            event = Event(notice_id=notice.id, source='contestkorea', category=old, title='일정', revision=7)
            db.add(event)
            events.append(event)
        notices.append(notice)
    db.commit()
    ids = [notice.id for notice in notices]
    before_metadata = [dict(notice.source_metadata) for notice in notices]
    monkeypatch.setattr(cli, 'SessionLocal', lambda: db)
    monkeypatch.setattr(cli, 'init_db', lambda: pytest.fail('스키마 변경 금지'))
    monkeypatch.setattr('sys.argv', ['app.cli', 'recategorize-contestkorea'] + (['--dry-run'] if dry_run else []))
    cli.main()
    output = capsys.readouterr().out
    assert '변경 공지 2건' in output and '판단 불가 공지 2건' in output
    for old, new in [('contest', 'activity'), ('activity', 'contest')]:
        assert f'board: {old}→{new} 1건' in output
        assert f'Event.category: {old}→{new} 2건' in output
    assert '예시0' in output and '예시1' in output
    for i, (old, new, field) in enumerate(cases):
        notice = db.get(Notice, ids[i])
        assert notice.board == (old if dry_run else new)
        assert notice.source_metadata == before_metadata[i]
        assert all(event.category == (old if dry_run else new) for event in notice.events)
        assert all(event.revision == 7 for event in notice.events)
    if not dry_run:
        assert not recategorize(db)['changes']
        assert recategorize_contestkorea(db)['changed'] == 0


def test_cli_examples_capped(db):
    for i in range(7):
        db.add(Notice(site='contestkorea', board='contest', title_raw=f'변경{i}',
                      source_url=f'https://example.com/change/{i}', source_metadata={'대표분야': '기타대외활동'}))
    db.commit()
    result = recategorize_contestkorea(db, dry_run=True)
    assert result['changed'] == 7
    assert len(result['examples']) == 5
