"""
공지 → 관심 분야(category) 분류.

모델은 5필드만 추출하므로 category 는 "어느 게시판에서 수집했는지"(크롤러 레코드의 board)로 정한다.
id 는 프론트 src/config/interestCategories.js 와 같아야 함:
  scholarship | academic | career | contest | activity
"""
import re
from collections import Counter

CATEGORY_IDS = {"scholarship", "academic", "career", "contest", "activity"}

# crawler/cbnu.py 의 board 값 (EXTRA_BOARDS 키 + 학부공지 분류 CATEGORY_BOARD)
# 학생활동(공모전 등) 은 board="activity" 로 들어와 아래 CATEGORY_IDS 규칙으로 처리
CBNU_BOARD_CATEGORY = {
    "sw_notice": "academic",
    "major": "academic",
    "scholarship": "scholarship",
    "employment": "career",
}

# 사이트별 board 가 없을 때 기본값
# 사이트 메타로 판단할 수 없을 때 새 공지에 사용하는 보수적인 기본값
SITE_DEFAULT_CATEGORY = {
    "cbnu": "academic",
    "wevity": "contest",
    "contestkorea": "contest",
}


def categorize(site: str, board: str | None) -> str:
    """
    - CBNU: 게시판 이름(sw_notice / scholarship / employment) → 분야
    - 그 외: board 에 분야 id 를 그대로 넣으면 그 값 사용
      (예: 크롤러에 Wevity 대외활동 목록을 추가할 때 board="activity")
    - board 가 없거나 모르는 값이면 사이트 기본값
    """
    if site == "cbnu" and board in CBNU_BOARD_CATEGORY:
        return CBNU_BOARD_CATEGORY[board]
    if board in CATEGORY_IDS:
        return board
    return SITE_DEFAULT_CATEGORY.get(site, "contest")


def contestkorea_board(code) -> str | None:
    if not isinstance(code, str) or not re.fullmatch(r'0[34]\d{7}', code):
        return None
    return 'activity' if code.startswith('04') else 'contest'


def contestkorea_metadata_board(metadata) -> str | None:
    """상세 대표분야만 판정한다. 목록 코드나 기존 board는 보정 근거가 아니다."""
    field = metadata.get('대표분야') if isinstance(metadata, dict) else None
    if not isinstance(field, str):
        return None
    normalize = lambda value: re.sub(r'[\s•·]+', '', value)
    activity = {'서포터즈·기자단', '교육·멘토링', '교육·강연·멘토링·세미나',
                '교육', '강연', '멘토링', '세미나', '전시·행사·축제',
                '기타대외활동', '기획·홍보·마케팅'}
    contest = {'문학·문예', '네이밍·슬로건', '학문·과학·IT', '미술·디자인·웹툰',
               '스포츠', '음악·콩쿠르·댄스', '사진·영상·영화제', '아이디어·건축·창업',
               '요리·뷰티·오디션', '기타공모전'}
    value = normalize(field)
    if value in {normalize(name) for name in activity}:
        return 'activity'
    if value in {normalize(name) for name in contest}:
        return 'contest'
    return None


def wevity_board(metadata) -> str | None:
    """목록 경로나 제목 대신 사이트의 분야 표시만 사용한다."""
    field = metadata.get('분야') if isinstance(metadata, dict) else None
    if not isinstance(field, str) or not field.strip():
        return None
    parts = [re.sub(r'\s+', '', part) for part in re.split(r'[,/·;|\n]+', field) if part.strip()]
    activity_fields = {'대외활동', '서포터즈', '봉사', '봉사활동', '기자단', '홍보대사'}
    return 'activity' if parts and all(part in activity_fields for part in parts) else 'contest'


def source_board(site, board, metadata, source_url='') -> str | None:
    """기존 DB용: 근거가 없으면 None이며 사이트 기본값으로 덮어쓰지 않는다."""
    if site == 'wevity':
        # 예전 파서가 비워 둔 메타는 board가 있어도 재분류하지 않는다.
        if not isinstance(metadata, dict) or not metadata:
            return None
        return wevity_board(metadata)
    if site == 'contestkorea':
        return contestkorea_metadata_board(metadata)
    return None


def recategorize(db, dry_run=False):
    from sqlalchemy import select
    from app.models import Event, Notice

    changes = Counter()
    skipped = Counter()
    missing_metadata = Counter()
    notices = db.scalars(select(Notice).where(Notice.site.in_(('wevity', 'contestkorea'))).order_by(Notice.id))
    for notice in notices:
        if not isinstance(notice.source_metadata, dict) or not notice.source_metadata:
            missing_metadata[notice.site] += 1
        board = source_board(notice.site, notice.board, notice.source_metadata, notice.source_url)
        if board is None:
            skipped[notice.site] += 1
            continue
        category = categorize(notice.site, board)
        for event in db.scalars(select(Event).where(Event.notice_id == notice.id)):
            if event.category != category:
                changes[(notice.site, event.category, category)] += 1
                if not dry_run:
                    event.category = category
    if not dry_run:
        db.commit()
    return {'changes': changes, 'skipped': skipped, 'missing_metadata': missing_metadata}
