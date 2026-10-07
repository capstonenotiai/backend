"""
DB 테이블.

날짜(start_date / end_date)는 model 출력과 같은 'YYYY-MM-DD' 문자열, 없으면 '' 로 저장한다.
(문자열 비교로 정렬/필터가 되고, 프론트 계약과 변환 없이 맞음)
"""
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.crypto import EncryptedText
from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    google_sub: Mapped[str | None] = mapped_column(String(64), unique=True)
    email: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(100))
    # Google Calendar 호출용. DB 에는 암호화되어 저장됨 (app/crypto.py)
    google_refresh_token: Mapped[str | None] = mapped_column(EncryptedText)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    preference: Mapped["Preference"] = relationship(back_populates="user", uselist=False)


class Preference(Base):
    __tablename__ = "preferences"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    ai_mode: Mapped[str] = mapped_column(String(32), default="priority")
    major: Mapped[str] = mapped_column(String(255), default="", server_default="")
    grade: Mapped[int | None] = mapped_column(Integer)
    enrollment_status: Mapped[str] = mapped_column(String(16), default="unknown", server_default="unknown")
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
    # MySQL(utf8mb4) 은 unique 인덱스가 3072 byte(=768자) 까지라 700 으로 제한
    source_url: Mapped[str] = mapped_column(String(700), unique=True)
    title_raw: Mapped[str] = mapped_column(Text)
    raw_text: Mapped[str] = mapped_column(Text, default="")
    crawled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    published_at: Mapped[str | None] = mapped_column(String(10))
    source_metadata: Mapped[dict | None] = mapped_column(JSON)
    application_end_date: Mapped[str | None] = mapped_column(String(10))
    extraction_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # 재시도 대기 중이면 이 시각(UTC) 이후에 다시 추출
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    extraction_state: Mapped[str] = mapped_column(String(24), default="pending", server_default="pending")
    extraction_result: Mapped[dict | None] = mapped_column(JSON)
    extraction_error: Mapped[str | None] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    events: Mapped[list["Event"]] = relationship(back_populates="notice")


class Event(Base):
    """모델이 추출한 일정 (프론트 Event)"""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    notice_id: Mapped[int | None] = mapped_column(ForeignKey("notices.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    source_url: Mapped[str] = mapped_column(String(1000), default="")

    # ── LLM 추출 5필드 ──
    title: Mapped[str] = mapped_column(Text)
    start_date: Mapped[str] = mapped_column(String(10), default="")
    end_date: Mapped[str] = mapped_column(String(10), default="", index=True)
    location: Mapped[str] = mapped_column(String(255), default="")
    detail: Mapped[str] = mapped_column(Text, default="")

    event_type: Mapped[str] = mapped_column(String(24), default="event", server_default="event")
    start_time: Mapped[str] = mapped_column(String(5), default="", server_default="")
    end_time: Mapped[str] = mapped_column(String(5), default="", server_default="")
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Seoul", server_default="Asia/Seoul")
    attendance_mode: Mapped[str] = mapped_column(String(24), default="unknown", server_default="unknown")
    schedule_status: Mapped[str] = mapped_column(String(24), default="confirmed", server_default="confirmed")
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    extraction_metadata: Mapped[dict | None] = mapped_column(JSON)
    ai_extracted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")

    category: Mapped[str | None] = mapped_column(String(32))
    review_status: Mapped[str] = mapped_column(String(16), default="needs_review")  # auto | needs_review
    review_reason: Mapped[str | None] = mapped_column(Text)
    extractor: Mapped[str | None] = mapped_column(String(32))
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    notice: Mapped[Notice | None] = relationship(back_populates="events")


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
    action_status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    overrides: Mapped[dict | None] = mapped_column(JSON)
    synced_revision: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    sync_status: Mapped[str] = mapped_column(String(24), default="none", server_default="none")
    sync_error: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class UserNotice(Base):
    __tablename__ = "user_notices"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    notice_id: Mapped[int] = mapped_column(ForeignKey("notices.id", ondelete="CASCADE"), primary_key=True)
    dismissed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")


class Notification(Base):
    """서비스 안 알림함 + 이메일 발송 기록. 같은 일정·종류·대상 날짜는 한 번만 만든다."""

    __tablename__ = "notifications"
    __table_args__ = (UniqueConstraint("user_id", "event_id", "kind", "target_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(24))  # deadline_d3 | deadline_d1 | event_d3 | event_d1
    target_date: Mapped[str] = mapped_column(String(10))  # 알림 기준 날짜 (마감일 / 시작일)
    title: Mapped[str] = mapped_column(Text)
    message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    email_status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | sent | failed | skipped
    email_attempts: Mapped[int] = mapped_column(Integer, default=0)
    email_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    email_error: Mapped[str | None] = mapped_column(Text)


class EventReport(Base):
    __tablename__ = "event_reports"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    reason: Mapped[str] = mapped_column(String(24))
    memo: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ReviewLog(Base):
    __tablename__ = "review_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    notice_id: Mapped[int] = mapped_column(ForeignKey("notices.id"), index=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(24))
    reason: Mapped[str] = mapped_column(Text)
    before: Mapped[list] = mapped_column(JSON)
    after: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CrawlRun(Base):
    """사이트별 수집 1회 기록 (대시보드 수집 현황)"""

    __tablename__ = "crawl_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    site: Mapped[str] = mapped_column(String(32), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    new_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="running")  # running | done | failed
    # 실패 사유 / 경고 (예: 게시판 404, 사이트 접속 불가)
    message: Mapped[str | None] = mapped_column(Text)
