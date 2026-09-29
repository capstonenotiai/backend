from datetime import date, datetime, timezone

from app.config import get_settings


def now_local() -> datetime:
    return datetime.now(get_settings().tz)


def today_local() -> date:
    return now_local().date()


def as_aware(value: datetime) -> datetime:
    """SQLite 는 tz 정보를 버리므로, naive 값은 UTC 로 저장된 것으로 본다."""
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def to_local(value: datetime) -> datetime:
    return as_aware(value).astimezone(get_settings().tz)
