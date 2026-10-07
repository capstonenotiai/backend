"""
API 요청/응답 형태 — 프론트(jaeyeongt/NotiAi) src/services/*.js 계약에 맞춤.
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from app.services.user_schedule import ScheduleOverrides

# 프론트 src/config/aiModes.js / sources.js 의 id 와 같아야 함
AI_MODE_IDS = ("priority", "discover", "focus")
DEFAULT_AI_MODE = "priority"
SOURCE_IDS = ("cbnu", "wevity", "contestkorea")


class EventOut(BaseModel):
    id: str
    title: str
    start_date: str
    end_date: str
    location: str
    detail: str
    source: str
    source_url: str
    category: str | None
    is_new: bool
    registered: bool
    bookmarked: bool
    collected_at: str | None
    review_status: str
    review_reason: str | None = None
    notice_id: int | None = None
    event_type: str = 'event'
    start_time: str = ''
    end_time: str = ''
    timezone: str = 'Asia/Seoul'
    attendance_mode: str = 'unknown'
    schedule_status: str = 'confirmed'
    revision: int = 1
    can_register: bool = False
    registration_reason: str | None = None
    sync_status: str = 'none'
    ai_extracted: bool = False
    review_required: bool = False
    action_status: str = 'pending'
    dismissed: bool = False
    user_modified: bool = False


class RecommendationProfile(BaseModel):
    model_config = ConfigDict(extra='forbid')
    major: str = Field(default='', max_length=255, strict=True)
    grade: int | None = Field(default=None, ge=1, le=6, strict=True)
    enrollment_status: Literal['enrolled', 'leave', 'graduated', 'unknown'] = 'unknown'


class Profile(RecommendationProfile):
    name: str
    email: str
    is_admin: bool = False


class Notifications(BaseModel):
    # 마감(본행사는 시작) 3일 전 / 1일 전 알림, 이메일로도 받기
    d3: bool = True
    d1: bool = True
    email: bool = True


class Preferences(BaseModel):
    ai_mode: str = DEFAULT_AI_MODE
    interests: list[str] = Field(default_factory=list)
    enabled_sources: dict[str, bool] = Field(default_factory=lambda: {s: True for s in SOURCE_IDS})
    notifications: Notifications = Field(default_factory=Notifications)
    auto_mode_recommend: bool = True


class ConfirmationIn(BaseModel):
    overrides: dict[str, ScheduleOverrides] = Field(default_factory=dict)
    confirmed: bool = False


class RegisterIn(ConfirmationIn):
    event_id: str | int


class RegisterOut(BaseModel):
    id: str
    registered: bool


class BookmarkIn(BaseModel):
    bookmarked: bool


class BookmarkOut(BaseModel):
    id: str
    bookmarked: bool


class SourceSummary(BaseModel):
    source: str
    count: int
    progress: int
    status: str  # done | running | failed | none(수집 기록 없음)


class DashboardSummary(BaseModel):
    collectedToday: int
    collectedChangeLabel: str
    lastCollectedAt: str
    collectionFinishedAt: str
    referenceTimeLabel: str
    greeting: str
    sources: list[SourceSummary]
