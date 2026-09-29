"""
DB 테이블.

날짜(start_date / end_date)는 model 출력과 같은 'YYYY-MM-DD' 문자열, 없으면 '' 로 저장한다.
(문자열 비교로 정렬/필터가 되고, 프론트 계약과 변환 없이 맞음)
"""
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    google_sub: Mapped[str | None] = mapped_column(String(64), unique=True)
    email: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(100))
    # Google Calendar 호출용. 실제 서비스라면 암호화해서 저장할 것
    google_refresh_token: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    preference: Mapped["Preference"] = relationship(back_populates="user", uselist=False)


class Preference(Base):
    __tablename__ = "preferences"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    ai_mode: Mapped[str] = mapped_column(String(32), default="study")
    interests: Mapped[list] = mapped_column(JSON, default=list)
    enabled_sources: Mapped[dict] = mapped_column(JSON, default=dict)
    notifications: Mapped[dict] = mapped_column(JSON, default=dict)
    auto_mode_recommend: Mapped[bool] = mapped_column(Boolean, default=True)

    user: Mapped[User] = relationship(back_populates="preference")


class Notice(Base):
    """크롤러가 수집한 공지 원문 (model repo crawler 의 레코드 1건)"""

    __tablename__ = "notices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    site: Mapped[str] = mapped_column(String(32), index=True)  # cbnu | wevity | contestkorea
    board: Mapped[str | None] = mapped_column(String(32))  # cbnu: sw_notice | scholarship | employment
    source_url: Mapped[str] = mapped_column(String(1000), unique=True)
    title_raw: Mapped[str] = mapped_column(Text)
    raw_text: Mapped[str] = mapped_column(Text, default="")
    crawled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    event: Mapped["Event | None"] = relationship(back_populates="notice", uselist=False)


class Event(Base):
    """모델이 추출한 일정 (프론트 Event)"""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    notice_id: Mapped[int | None] = mapped_column(ForeignKey("notices.id", ondelete="CASCADE"), unique=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    source_url: Mapped[str] = mapped_column(String(1000), default="")

    # ── LLM 추출 5필드 ──
    title: Mapped[str] = mapped_column(Text)
    start_date: Mapped[str] = mapped_column(String(10), default="")
    end_date: Mapped[str] = mapped_column(String(10), default="", index=True)
    location: Mapped[str] = mapped_column(String(255), default="")
    detail: Mapped[str] = mapped_column(Text, default="")

    category: Mapped[str | None] = mapped_column(String(32))
    review_status: Mapped[str] = mapped_column(String(16), default="needs_review")  # auto | needs_review
    review_reason: Mapped[str | None] = mapped_column(Text)
    extractor: Mapped[str | None] = mapped_column(String(32))
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    notice: Mapped[Notice | None] = relationship(back_populates="event")


class UserEvent(Base):
    """사용자별 일정 상태 (캘린더 등록 / 북마크)"""

    __tablename__ = "user_events"
    __table_args__ = (UniqueConstraint("user_id", "event_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    registered: Mapped[bool] = mapped_column(Boolean, default=False)
    google_event_id: Mapped[str | None] = mapped_column(String(255))
    bookmarked: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class CrawlRun(Base):
    """사이트별 수집 1회 기록 (대시보드 수집 현황)"""

    __tablename__ = "crawl_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    site: Mapped[str] = mapped_column(String(32), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    new_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="running")  # running | done | failed
