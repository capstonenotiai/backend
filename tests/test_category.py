from pathlib import Path
import pytest
from sqlalchemy import select

from app.models import Event, Notice
from app.services.category import categorize, contestkorea_board, recategorize, source_board, wevity_board
from app.services.pipeline import import_records
from crawler import contestkorea, wevity
from test_crawler_metadata import FakeResponse, CONTESTKOREA_HTML


@pytest.mark.parametrize('code', list(contestkorea.CATEGORIES.values()))
def test_contestkorea_category_codes_and_detail(monkeypatch, code):
    expected = 'activity' if code.startswith('04') else 'contest'
    monkeypatch.setattr(contestkorea, 'get', lambda url: FakeResponse(CONTESTKOREA_HTML))
    result = contestkorea.parse_detail_page(f'https://contestkorea.com/sub/view.php?Txt_bcode={code}&str_no=203001010001')
    assert result['board'] == contestkorea_board(code) == expected
    assert result['category_code'] == code


@pytest.mark.parametrize('code', [None, 40110001, '', '05other', '04011x001', '04', ' 040110001'])
def test_invalid_contestkorea_codes_not_guessed(code):
    assert contestkorea_board(code) is None


@pytest.mark.parametrize('field,expected', [
    ('대외활동/서포터즈', 'activity'), ('봉사활동', 'activity'),
    ('서포터즈, 기자단, 홍보대사', 'activity'), ('대외활동 / 서포터즈 · 봉사활동', 'activity'),
    ('대외활동/서포터즈, 영상/UCC/사진', 'contest'), ('기획/아이디어, 봉사활동', 'contest'),
    ('취업/창업', 'contest'), ('기타', 'contest'), ('서포터즈 공모전', 'contest'),
    ('', None), ('  ', None), (None, None), ([], None),
])
def test_wevity_field_boundaries(field, expected):
    assert wevity_board({'분야': field}) == expected


@pytest.mark.parametrize('ix,expected', [
    (111533, 'activity'), (111485, 'activity'), (111391, 'activity'),
    (111311, 'contest'), (110023, 'contest'), (109996, 'contest'),
])
def test_wevity_real_field_snippets(monkeypatch, ix, expected):
    fixture = Path(__file__).parent / 'fixtures/category' / f'wevity_{ix}.html'
    # 제목은 의도적으로 반대 분류를 암시하지만 분야 표시만 판단해야 한다.
    title = '영상 공모전 테스트 제목' if expected == 'activity' else '서포터즈 모집 테스트 제목'
    html = f'<h6 class="tit">{title}</h6>' + fixture.read_text(encoding='utf-8')
    monkeypatch.setattr(wevity, '_get', lambda url: html)
    result = wevity.parse_detail_page(ix)
    assert result['board'] == expected
    assert result['meta']['분야']


def test_wevity_missing_field_defaults_for_new_notice_only(monkeypatch):
    monkeypatch.setattr(wevity, '_get', lambda url: '<h6 class="tit">서포터즈 모집이라는 제목만 있음</h6>')
    assert wevity.parse_detail_page(1)['board'] == 'contest'
    assert source_board('wevity', 'activity', {}, 'https://www.wevity.com/?c=active') is None


@pytest.mark.parametrize('board,meta,url,expected', [
    ('activity', {'category_code':'030110001'}, '', 'activity'),
    (None, {'category_code':'040110001'}, '', 'activity'),
    (None, {'category':'교육·멘토링'}, '', 'activity'),
    (None, {'category':'문학·문예'}, '', 'contest'),
    (None, {'접수기간':'2030-01-01'}, 'https://contestkorea.com/sub/view.php?Txt_bcode=040610001', 'activity'),
    (None, {'접수기간':'2030-01-01'}, 'https://contestkorea.com/sub/view.php?Txt_bcode=030110001', 'contest'),
    (None, {'category':'미확인'}, '', None),
    (None, {}, 'https://contestkorea.com/sub/view.php?Txt_bcode=040610001', None),
])
def test_existing_contestkorea_metadata_and_url(board, meta, url, expected):
    assert source_board('contestkorea', board, meta, url) == expected


@pytest.mark.parametrize('codes', [('030110001', '040110001'), ('040110001', '030110001')])
def test_contestkorea_cross_category_duplicate_keeps_first(monkeypatch, capsys, codes):
    calls = []
    monkeypatch.setattr(contestkorea, 'get_total_pages', lambda code: 1)
    monkeypatch.setattr(contestkorea, 'parse_list_page', lambda page, code: [
        {'source_url': f'https://contestkorea.com/sub/view.php?Txt_bcode={code}&str_no=203001010001',
         'title_raw': '같은 글', 'category_code': code}])
    monkeypatch.setattr(contestkorea, 'parse_detail_page', lambda url: calls.append(url) or {'meta': {'접수기간':'2030-01-01'}})
    result = contestkorea.crawl(max_pages=1, categories=list(codes))
    assert len(result) == len(calls) == 1
    assert result[0]['board'] == contestkorea_board(codes[0])
    assert result[0]['category_code'] == codes[0]
    assert '목록 간 중복 글 1건' in capsys.readouterr().out


def test_contestkorea_existing_other_category_url_skipped(monkeypatch):
    monkeypatch.setattr(contestkorea, 'get_total_pages', lambda code: 1)
    monkeypatch.setattr(contestkorea, 'parse_list_page', lambda page, code: [
        {'source_url': f'https://contestkorea.com/sub/view.php?Txt_bcode={code}&str_no=203001010001', 'title_raw':'같은 글'}])
    monkeypatch.setattr(contestkorea, 'parse_detail_page', lambda url: pytest.fail('같은 str_no는 다시 수집하면 안 됨'))
    assert contestkorea.crawl(max_pages=1, categories=['040110001'], existing_urls={
        'http://contestkorea.com/sub/view.php?Txt_bcode=030110001&str_no=203001010001'}) == []


def test_import_preserves_board_and_contestkorea_category_metadata(db):
    records = [
        {'site':'wevity','source_url':'https://example.com/w','board':'activity','meta':{'분야':'대외활동/서포터즈'}},
        {'site':'contestkorea','source_url':'https://example.com/c','board':'activity','meta':{'접수기간':'2030-01-01'},
         'category_code':'040110001','category':'서포터즈·기자단'},
    ]
    assert sum(import_records(db, records).values()) == 2
    rows = db.scalars(select(Notice).order_by(Notice.id)).all()
    assert [row.board for row in rows] == ['activity','activity']
    assert rows[1].source_metadata['category_code'] == '040110001'
    assert rows[1].source_metadata['category'] == '서포터즈·기자단'
    assert 'category_code' not in records[1]['meta']  # 입력 레코드 변경 금지
    assert categorize(rows[0].site, rows[0].board) == 'activity'


def category_dataset(db):
    cases = [
        ('wevity', None, {'분야':'대외활동/서포터즈'}, 'contest', '', 2),
        ('wevity', None, {'분야':'대외활동/서포터즈, 영상/UCC/사진'}, 'activity', '', 1),
        ('wevity', 'activity', {}, 'contest', '', 1),
        ('wevity', None, {'주최':'기관'}, 'academic', '', 1),
        ('contestkorea', None, {'접수기간':'2030-01-01'}, 'contest', '?Txt_bcode=040610001', 1),
        ('contestkorea', None, {'category_code':'030110001'}, None, '', 1),
        ('contestkorea', None, {}, 'contest', '?Txt_bcode=040610001', 1),
        ('cbnu', 'employment', {}, 'academic', '', 1),
    ]
    events = []
    for index,(site,board,meta,old,query,count) in enumerate(cases):
        notice = Notice(site=site, board=board, source_metadata=meta, source_url=f'https://example.com/{index}{query}', title_raw='분류에 사용하지 않는 제목')
        db.add(notice); db.flush()
        for _ in range(count):
            event = Event(notice_id=notice.id, source=site, title='일정', category=old, revision=7)
            db.add(event); db.flush(); events.append(event)
    db.commit()
    return events


@pytest.mark.parametrize('dry_run', [True, False])
def test_recategorize_dry_run_apply_and_unknown_unchanged(db, dry_run):
    events = category_dataset(db)
    before = [event.category for event in events]
    result = recategorize(db, dry_run)
    assert result['changes'] == {('wevity','contest','activity'):2, ('wevity','activity','contest'):1,
                                  ('contestkorea','contest','activity'):1, ('contestkorea',None,'contest'):1}
    assert result['skipped'] == {'wevity':2, 'contestkorea':1}
    assert result['missing_metadata'] == {'wevity':1, 'contestkorea':1}
    db.expire_all()
    expected = before if dry_run else ['activity','activity','contest','contest','academic','activity','contest','contest','academic']
    assert [event.category for event in events] == expected
    assert all(event.revision == 7 for event in events)
    if not dry_run:
        assert not recategorize(db)['changes']
        from app.services.planner_context import interest_match
        item = {'opportunity_id':'o1','category':events[0].category, 'enrichment':{'facts':[]}}
        assert interest_match(item, ['activity'])['direct'] is True
        assert interest_match(item, ['contest'])['direct'] is False


@pytest.mark.parametrize('dry_run', [True, False])
def test_recategorize_cli_dispatch_and_counts(db, monkeypatch, capsys, dry_run):
    from app import cli
    events = category_dataset(db)
    monkeypatch.setattr(cli, 'SessionLocal', lambda: db)
    monkeypatch.setattr(cli, 'init_db', lambda: pytest.fail('재분류는 스키마를 변경하면 안 됨'))
    monkeypatch.setattr('sys.argv', ['app.cli','recategorize'] + (['--dry-run'] if dry_run else []))
    cli.main()
    output = capsys.readouterr().out
    assert '변경 일정 5건' in output
    assert 'wevity: contest→activity 2건' in output
    assert 'wevity: 판단 불가 공지 2건, 메타 없음 공지 1건' in output
    assert 'contestkorea: 판단 불가 공지 1건, 메타 없음 공지 1건' in output
    assert db.get(Event, events[0].id).category == ('contest' if dry_run else 'activity')


def test_cbnu_category_rules_unchanged():
    for board, expected in [('sw_notice','academic'), ('major','academic'), ('scholarship','scholarship'),
                            ('employment','career'), ('activity','activity'), (None,'academic')]:
        assert categorize('cbnu',board) == expected
