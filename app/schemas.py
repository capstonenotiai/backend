"""
API 요청/응답 형태 — 프론트(jaeyeongt/NotiAi) src/services/*.js 계약에 맞춤.
"""
from pydantic import BaseModel, Field

# 프론트 src/config/aiModes.js / sources.js 의 id 와 같아야 함
AI_MODE_IDS = ("study", "explorer", "balanced")
DEFAULT_AI_MODE = "study"
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


class Profile(BaseModel):
    name: str
    email: str


class Notifications(BaseModel):
    d7: bool = True
    d3: bool = True
    new_event: bool = False


class Preferences(BaseModel):
    ai_mode: str = DEFAULT_AI_MODE
    interests: list[str] = Field(default_factory=list)
    enabled_sources: dict[str, bool] = Field(default_factory=lambda: {s: True for s in SOURCE_IDS})
    notifications: Notifications = Field(default_factory=Notifications)
    auto_mode_recommend: bool = True


class RegisterIn(BaseModel):
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
    status: str  # done | running | failed


class DashboardSummary(BaseModel):
    collectedToday: int
    collectedChangeLabel: str
    lastCollectedAt: str
    collectionFinishedAt: str
    referenceTimeLabel: str
    greeting: str
    sources: list[SourceSummary]
