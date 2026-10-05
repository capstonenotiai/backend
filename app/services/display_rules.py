"""Notice display rules. Deadlines without a time remain open until midnight in Seoul."""
from datetime import datetime, time
from zoneinfo import ZoneInfo

APPLICATION_TYPES = frozenset({"application", "submission"})
HIDDEN_TYPES = frozenset({"result"})
SEOUL = ZoneInfo("Asia/Seoul")


def deadline(event):
    if not event.end_date:
        return None
    try:
        day = datetime.strptime(event.end_date, "%Y-%m-%d").date()
        clock = time.fromisoformat(event.end_time) if event.end_time else time.max
        return datetime.combine(day, clock, tzinfo=SEOUL)
    except ValueError:
        return None


def is_open(event, now):
    end = deadline(event)
    return end is not None and end >= now.astimezone(SEOUL)


def representative(events, now):
    public = [event for event in events if event.review_status in ("auto", "approved")
              and event.event_type not in HIDDEN_TYPES and event.schedule_status != "cancelled"]
    applications = [event for event in public if event.event_type in APPLICATION_TYPES]
    if applications:
        active = [event for event in applications if is_open(event, now)]
        return min(active, key=lambda e: (deadline(e), e.id)) if active else None
    main = [event for event in public if event.event_type == "event"]
    if not main:
        return None
    upcoming = [event for event in main if is_open(event, now)]
    return min(upcoming or main, key=lambda e: (e.start_date or e.end_date or "9999", e.id))
