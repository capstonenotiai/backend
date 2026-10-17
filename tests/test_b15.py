from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from bs4 import BeautifulSoup

from crawler import contestkorea as ck

FIXTURES = Path(__file__).parent / 'fixtures' / 'contestkorea'


def saved(name):
    return (FIXTURES / name).read_text(encoding='utf-8')


def response(html):
    return SimpleNamespace(text=html, apparent_encoding='utf-8')


@pytest.mark.parametrize('code,count,pages', [
    ('030110001', 13, 616), ('040110001', 13, 626), ('040410001', 12, 613),
])
def test_live_list_fixture_excludes_sidebar(monkeypatch, code, count, pages):
    html = saved(f'list_{code}.html')
    bs = BeautifulSoup(html, 'lxml')
    expected = {ck.post_id(a['href']) for a in bs.select('.list_style_2 .title a')}
    sidebar = {ck.post_id(a.get('href', '')) for a in bs.select('.listStyle_1 a, .best-line-box a')}
    assert sidebar - expected
    monkeypatch.setattr(ck, 'get', lambda url: response(html))
    items = ck.parse_list_page(1, code)
    assert len(items) == count
    assert {ck.post_id(item['source_url']) for item in items} == expected
    assert all(item['list_date_raw'] for item in items)
    assert all(item['category_code'] == code for item in items)
    assert ck.get_total_pages(code) == pages


@pytest.mark.parametrize('code', list(ck.CATEGORIES.values()))
def test_list_paging_and_detail_use_category_group(monkeypatch, code):
    urls = []
    html = saved('list_040410001.html')
    monkeypatch.setattr(ck, 'get', lambda url: urls.append(url) or response(html))
    ck.parse_list_page(3, code)
    ck.get_total_pages(code)
    ck.parse_detail_page(f'https://www.contestkorea.com/sub/view.php?int_gbn=9&Txt_bcode={code}&str_no=202610160008')
    assert len(urls) == 3
    for url in urls:
        query = parse_qs(urlparse(url).query)
        assert query['int_gbn'] == ['2' if code.startswith('04') else '1']
        assert query['Txt_bcode'] == [code]
    assert parse_qs(urlparse(urls[0]).query)['page'] == ['3']


def test_missing_container_does_not_fall_back(monkeypatch, capsys):
    bs = BeautifulSoup(saved('list_040410001.html'), 'lxml')
    bs.select_one('.list_style_2').decompose()
    monkeypatch.setattr(ck, 'get', lambda url: response(str(bs)))
    assert ck.parse_list_page(1, '040410001') == []
    assert '경고' in capsys.readouterr().out


def test_duplicate_and_dday_links_ignored(monkeypatch):
    bs = BeautifulSoup(saved('list_040410001.html'), 'lxml')
    title = bs.select_one('.list_style_2 .title')
    original = title.a
    for text in [original.get_text(), 'D-10', 'D+2', '', '짧음']:
        anchor = bs.new_tag('a', href=original['href'])
        anchor.string = text
        title.append(anchor)
    # D-day appearing before a valid title must not suppress that title.
    badge = bs.new_tag('a', href=original['href'])
    badge.string = 'D-10'
    title.insert(0, badge)
    monkeypatch.setattr(ck, 'get', lambda url: response(str(bs)))
    assert len(ck.parse_list_page(1, '040410001')) == 12


def test_activity_codes_only_collect_body_items(monkeypatch):
    html = saved('list_040410001.html')
    expected = {ck.post_id(a['href']) for a in BeautifulSoup(html, 'lxml').select('.list_style_2 .title a')}
    urls = []
    monkeypatch.setattr(ck, 'CATEGORIES', {'문학': '030110001', '교육': '040410001'})
    # Keep real page detection, with a one-page version of the saved navigation.
    bs = BeautifulSoup(html, 'lxml')
    bs.select_one('.pagination').decompose()
    monkeypatch.setattr(ck, 'get', lambda url: urls.append(url) or response(str(bs)))
    assert ck.activity_list_codes() == dict.fromkeys(expected, '040410001')
    assert len(urls) == 2
    assert all(parse_qs(urlparse(url).query)['int_gbn'] == ['2'] for url in urls)


def test_activity_detail_saved_public_fields(monkeypatch):
    urls = []
    monkeypatch.setattr(ck, 'get', lambda url: urls.append(url) or response(saved('detail_activity.html')))
    result = ck.parse_detail_page('https://www.contestkorea.com/sub/view.php?int_gbn=1&Txt_bcode=040110001&str_no=202610160008')
    assert result['title_raw'] == '시슐랭 프렌즈 1기 모집'
    assert '2026. 10. 16.' in result['raw_text']
    assert result['period_hint'] == '2026.10.16 ~ 2026.10.25'
    assert result['meta']['대표분야'] == '서포터즈 • 기자단'
    assert result['published_at'] == '2026-10-16'
    assert result['board'] == 'activity'
    assert result['source_url'] == urls[0]
    assert parse_qs(urlparse(urls[0]).query)['int_gbn'] == ['2']
