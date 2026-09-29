"""
공지 → 관심 분야(category) 분류.

모델은 5필드만 추출하므로 category 는 "어느 게시판에서 수집했는지"(크롤러 레코드의 board)로 정한다.
id 는 프론트 src/config/interestCategories.js 와 같아야 함:
  scholarship | academic | career | contest | activity
"""

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
# (현재 크롤러는 Wevity / ContestKorea 모두 공모전 목록만 수집)
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
