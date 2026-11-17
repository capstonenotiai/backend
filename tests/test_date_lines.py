from app.models import Event, Notice
from app.services.date_lines import MAX_LINES, date_lines

BODY = """□ 신청 기간: 2030. 10. 20.(목) ~ 10. 28.(금)까지
문의: 학생지원팀 043-261-1234
□ 본선 발표: 11월 5일(금) 14시, 공학관 301호

※ 결과 발표는 11월 10일 예정
잘못된 날짜 13.40 은 무시"""


def test_only_lines_with_valid_dates_in_original_order():
    assert date_lines(BODY) == ['□ 신청 기간: 2030. 10. 20.(목) ~ 10. 28.(금)까지',
                                '□ 본선 발표: 11월 5일(금) 14시, 공학관 301호', '※ 결과 발표는 11월 10일 예정']
    assert date_lines('') == [] and date_lines(None) == []
    assert date_lines('전화 043-261-1234') == []


def test_limit_keeps_lines_matching_event_dates_and_trims_long_lines():
    filler = '\n'.join(f'참고 {m}월 1일 안내' for m in range(1, 11))
    lines = date_lines(filler + '\n마감 12월 24일', ['2030-12-24'])
    assert len(lines) == MAX_LINES and lines[-1] == '마감 12월 24일'
    assert lines[:-1] == [f'참고 {m}월 1일 안내' for m in range(1, MAX_LINES)]
    long = date_lines('3월 2일 ' + '가' * 500)[0]
    assert len(long) == 200 and long.endswith('…')
    assert date_lines('3월 2일 안내\n  3월  2일   안내 ') == ['3월 2일 안내']


def test_detail_includes_date_lines(client, db):
    notice = Notice(site='cbnu', source_url='https://example.com/lines', title_raw='모집', raw_text=BODY)
    db.add(notice); db.flush()
    event = Event(notice_id=notice.id, source='cbnu', title='모집', event_type='application',
                  start_date='2030-10-20', end_date='2030-10-28', review_status='approved')
    db.add(event); db.commit()
    detail = client.get(f'/api/events/{event.id}').json()
    assert detail['date_lines'][0].startswith('□ 신청 기간') and len(detail['date_lines']) == 3
