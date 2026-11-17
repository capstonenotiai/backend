"""Site-specific publication date and labeled-period parsing from saved HTML snippets."""
from crawler import contestkorea, wevity
from app.services.notice_metadata import application_deadline

CONTESTKOREA_HTML = """<html><body>
<h1>2026 문학 공모전 안내</h1>
<!--<span class="date">2026-02-28</span>//-->
<table><tr><th>접수기간</th><td>2026.12.01 ~ 2027.01.05</td></tr>
<tr><th>대회지역</th><td>온라인</td></tr></table>
<table><tr><th class="date2">등록일</th></tr><tr><td class="date2">2026.10.05</td></tr></table>
<div class="view_detail_area">본문</div></body></html>"""

WEVITY_HTML = """<html><body><div class="content">
<h6 class="tit">7th Da-Ho Global Video Competition</h6>
<ul class="cd-info-list">
<li><span class="tit">분야</span> 광고/마케팅, 영상/UCC/사진 </li>
<li><span class="tit">후원/협찬</span> </li>
<li class="dday-area"><span class="tit">접수기간</span> 2026-08-01 ~ 2027-01-15 <span class="cil-dday">D-100</span></li>
<li class="sns"><span class="tit"></span><div>공유</div></li>
</ul><div class="comm-desc" id="viewContents">상세 내용</div></div></body></html>"""


class FakeResponse:
    def __init__(self, text):
        self.text, self.apparent_encoding, self.encoding = text, 'utf-8', 'utf-8'


def test_contestkorea_publication_from_post_number(monkeypatch):
    monkeypatch.setattr(contestkorea, 'get', lambda url: FakeResponse(CONTESTKOREA_HTML))
    url = 'http://contestkorea.com/sub/view.php?int_gbn=1&Txt_bcode=030110001&str_no=202602240031'
    result = contestkorea.parse_detail_page(url)
    # 관련 글 목록의 '등록일'(2026.10.05)이나 수정일일 수 있는 주석 날짜가 아니라 글 번호의 등록일
    assert result['published_at'] == '2026-02-24'
    assert application_deadline(result['meta']) == '2027-01-05'


def test_contestkorea_publication_falls_back_to_hidden_date():
    assert contestkorea.registration_date('http://contestkorea.com/sub/view.php?str_no=abc',
                                          CONTESTKOREA_HTML) == '2026-02-28'
    assert contestkorea.registration_date('http://contestkorea.com/sub/view.php?str_no=209913990001', '') is None


def test_wevity_info_list_is_parsed(monkeypatch):
    monkeypatch.setattr(wevity, '_get', lambda url: WEVITY_HTML)
    result = wevity.parse_detail_page(110179)
    assert result['meta'] == {'분야': '광고/마케팅, 영상/UCC/사진', '접수기간': '2026-08-01 ~ 2027-01-15'}
    assert result['period_hint'] == '2026-08-01 ~ 2027-01-15'
    assert application_deadline(result['meta']) == '2027-01-15'
    # Wevity 는 페이지 어디에도 게시일이 없다
    assert result['published_at'] is None
