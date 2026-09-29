"""
공지 → 관심 분야(category) 분류. 모델은 category 를 추출하지 않으므로 백엔드 규칙으로 정한다.
id 는 프론트 src/config/interestCategories.js 와 같아야 함:
  scholarship | academic | career | contest | activity
"""
import re

# model repo crawler/cbnu.py 의 EXTRA_BOARDS 키
CBNU_BOARD_CATEGORY = {
    "sw_notice": "academic",
    "scholarship": "scholarship",
    "employment": "career",
}

_ACTIVITY_RE = re.compile(r"서포터즈|기자단|대외활동|봉사|멘토링|홍보대사|앰배서더|탐방")
_CAREER_RE = re.compile(r"인턴|채용|취업|신입|공채")


def categorize(site: str, board: str | None, title: str) -> str:
    if site == "cbnu":
        return CBNU_BOARD_CATEGORY.get(board or "", "academic")
    if _CAREER_RE.search(title):
        return "career"
    if _ACTIVITY_RE.search(title):
        return "activity"
    return "contest"
