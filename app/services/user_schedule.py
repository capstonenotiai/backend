"""User-only schedule edits shared by API, calendar and factual calculations."""
from types import SimpleNamespace
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.models import UserNotice
from app.services.review import EDIT_FIELDS, EventEdit

OVERRIDE_FIELDS = ('start_date', 'end_date', 'start_time', 'end_time', 'location')


class ScheduleOverrides(BaseModel):
    model_config = ConfigDict(extra='forbid')
    start_date: str | None = None
    end_date: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    location: str | None = Field(default=None, max_length=255)


class ActionIn(BaseModel):
    action_status: Literal['pending', 'done']


class ReportIn(BaseModel):
    reason: Literal['date', 'time', 'location', 'nonexistent', 'other']
    memo: str = Field(default='', max_length=2000)


class DismissIn(BaseModel):
    dismissed: bool


def effective_event(event, state=None, overrides=None):
    values = {key: getattr(event, key) for key in EDIT_FIELDS}
    values.update((state.overrides or {}) if state else {})
    if overrides is not None:
        values.update(overrides)
    return SimpleNamespace(**values, id=event.id, notice_id=event.notice_id,
        source_url=event.source_url, review_status=event.review_status,
        review_reason=event.review_reason, revision=event.revision, ai_extracted=event.ai_extracted)


def validate_overrides(event, state, patch):
    values = dict(state.overrides or {}) if state else {}
    for key, value in patch.items():
        if key not in OVERRIDE_FIELDS:
            raise HTTPException(400, '수정할 수 없는 일정 필드입니다.')
        if value is None:
            values.pop(key, None)
        else:
            values[key] = value.strip()
    candidate = effective_event(event, overrides=values)
    try:
        EventEdit(**{key: getattr(candidate, key) for key in EDIT_FIELDS})
    except ValueError:
        raise HTTPException(400, '날짜·시각·장소 수정 값을 확인해 주세요.') from None
    return values


def get_user_notice(db, user, notice_id):
    state = db.get(UserNotice, (user.id, notice_id))
    if not state:
        state = UserNotice(user_id=user.id, notice_id=notice_id)
        db.add(state)
    return state
