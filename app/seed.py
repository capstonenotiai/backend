"""
데모 데이터 — 프론트 src/data/mockEvents.js 6건.

원본은 2026-05-19 를 '오늘'로 둔 데이터라, 실행한 날짜 기준으로 날짜를 옮겨서
D-day 가 디자인 시안과 똑같이 나오게 한다.
"""
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.deps import get_or_create_dev_user
from app.models import Event, UserEvent
from app.timeutil import today_local

MOCK_TODAY = date(2026, 5, 19)
SEED_EXTRACTOR = "seed"

MOCK_EVENTS = [
    {
        "title": "2026 충북대학교 창업경진대회",
        "start_date": "2026-05-01", "end_date": "2026-05-22", "location": "충북대학교 개신문화관",
        "detail": "재학생 팀(2~4인) 대상, 창업지원단 홈페이지 접수. 본선 진출팀 발표 예정.",
        "source": "cbnu", "source_url": "https://software.cbnu.ac.kr/index.php?mid=sub0401",
        "category": "contest", "collected_at": "2026-05-12", "registered": True, "bookmarked": False,
    },
    {
        "title": "제27회 대한민국 대학생 광고대상",
        "start_date": "2026-05-08", "end_date": "2026-05-25", "location": "",
        "detail": "전국 대학생 대상, 온라인 접수. 인쇄·영상·디지털 부문.",
        "source": "wevity", "source_url": "https://www.wevity.com/",
        "category": "contest", "collected_at": "2026-05-18", "registered": False, "bookmarked": False,
    },
    {
        "title": "충북대 SW중심대학 해커톤",
        "start_date": "2026-05-20", "end_date": "2026-05-30", "location": "충북대학교 공과대학",
        "detail": "SW중심대학 재학생 팀 단위 참가, 무박 2일 진행.",
        "source": "cbnu", "source_url": "https://software.cbnu.ac.kr/index.php?mid=sub0401",
        "category": "academic", "collected_at": "2026-05-18", "registered": False, "bookmarked": False,
    },
    {
        "title": "2026 K-스타트업 그랜드챌린지",
        "start_date": "2026-05-15", "end_date": "2026-06-02", "location": "서울 코엑스",
        "detail": "예비·초기 창업자 대상, K-Startup 누리집 신청.",
        "source": "contestkorea", "source_url": "https://www.contestkorea.com/",
        "category": "activity", "collected_at": "2026-05-19", "registered": False, "bookmarked": False,
    },
    {
        "title": "2026 대학생 UX 디자인 공모전",
        "start_date": "2026-05-10", "end_date": "2026-06-10", "location": "온라인",
        "detail": "대학(원)생 개인·팀 참가, 이메일 제출.",
        "source": "wevity", "source_url": "https://www.wevity.com/",
        "category": "contest", "collected_at": "2026-05-10", "registered": True, "bookmarked": True,
    },
    {
        "title": "청년 사회혁신 아이디어 공모전",
        "start_date": "2026-05-01", "end_date": "2026-06-20", "location": "",
        "detail": "만 19~34세 청년 대상, 온라인 제출.",
        "source": "contestkorea", "source_url": "https://www.contestkorea.com/",
        "category": "contest", "collected_at": "2026-05-10", "registered": False, "bookmarked": False,
    },
]


def _shift(value: str, delta: timedelta) -> str:
    return (date.fromisoformat(value) + delta).isoformat() if value else ""


def seed(db: Session) -> int:
    """이미 seed 된 적 있으면 아무것도 하지 않음. 추가한 건수 반환"""
    if db.scalar(select(Event).where(Event.extractor == SEED_EXTRACTOR)):
        return 0

    delta = today_local() - MOCK_TODAY
    user = get_or_create_dev_user(db)

    for item in MOCK_EVENTS:
        collected = date.fromisoformat(item["collected_at"]) + delta
        event = Event(
            source=item["source"],
            source_url=item["source_url"],
            title=item["title"],
            start_date=_shift(item["start_date"], delta),
            end_date=_shift(item["end_date"], delta),
            location=item["location"],
            detail=item["detail"],
            category=item["category"],
            review_status="auto",
            extractor=SEED_EXTRACTOR,
            collected_at=datetime(collected.year, collected.month, collected.day, tzinfo=timezone.utc),
        )
        db.add(event)
        db.flush()
        if item["registered"] or item["bookmarked"]:
            db.add(
                UserEvent(
                    user_id=user.id, event_id=event.id, registered=item["registered"], bookmarked=item["bookmarked"]
                )
            )
    db.commit()
    return len(MOCK_EVENTS)
