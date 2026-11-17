"""공지 본문에서 날짜가 나온 줄을 골라낸다 (등록 확인 창의 '공지에서 날짜가 나온 줄').

본문 전체 대신 날짜 줄만 보낸다. 규칙은 프론트 관리자 화면(utils/adminReview.js dateLines)과 같다.
"""
import calendar
import re

DATE_TOKEN = re.compile(r'(?<!\d)(?:(\d{4})\s*[.년/\-]\s*)?(\d{1,2})\s*[.월/\-]\s*(\d{1,2})(?!\d)(?:\s*일)?')
MAX_LINES = 8
MAX_LINE_CHARS = 200


def _valid(match) -> bool:
    year, month, day = match.group(1), int(match.group(2)), int(match.group(3))
    if not 1 <= month <= 12:
        return False
    return 1 <= day <= calendar.monthrange(int(year) if year else 2000, month)[1]


def date_lines(body: str | None, event_dates: list[str] = ()) -> list[str]:
    """날짜가 들어간 줄을 원문 순서대로 최대 MAX_LINES 개. 넘치면 일정 날짜와 맞는 줄을 먼저 남긴다."""
    wanted = set()
    for value in event_dates:
        if value:
            _, month, day = value.split('-')
            wanted.add((int(month), int(day)))
    found, seen = [], set()
    for index, raw in enumerate((body or '').splitlines()):
        line = ' '.join(raw.split())
        if not line or line in seen:
            continue
        tokens = [m for m in DATE_TOKEN.finditer(line) if _valid(m)]
        if not tokens:
            continue
        seen.add(line)
        hit = any((int(m.group(2)), int(m.group(3))) in wanted for m in tokens)
        text = line if len(line) <= MAX_LINE_CHARS else line[:MAX_LINE_CHARS - 1] + '…'
        found.append((index, hit, text))
    if len(found) > MAX_LINES:
        found = sorted(sorted(found, key=lambda item: not item[1])[:MAX_LINES])
    return [text for _, _, text in found]
