"""
알림 대상 선정 → 알림함 저장 → 이메일 발송.

정책(POLICY_DECISIONS 서비스 표시 규칙 4·6번)
- 접수 마감 알림은 관심 저장(북마크)만 해도 보낸다. 본행사·발대식·면접 등 나머지는 캘린더에 등록한 경우만.
- 마감(본행사는 시작) D-3·D-1 당일 오전 9시에 한 번에 만든다. "확인 필요" 일정은 당분간 제외.
- 완료(done)·관심 없음·취소된 일정은 보내지 않는다.
"""
import logging
from datetime import date, datetime

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.deps import DEV_USER_SUB
from app.models import Notice, Notification, User, UserNotice, utcnow
from app.services.display_rules import APPLICATION_TYPES, is_open
from app.services.email import EmailError, send_email
from app.services.events import _visible_rows
from app.services.preferences import get_or_create_preference
from app.services.review import requires_confirmation
from app.services.user_schedule import effective_event
from app.timeutil import now_local, to_local

log = logging.getLogger(__name__)

# 알림을 만드는 날: 기준 날짜까지 남은 일수 → 사용자 설정 키
DAYS_BEFORE = {3: "d3", 1: "d1"}
EMAIL_MAX_ATTEMPTS = 3


def notification_candidates(db, user, now=None):
    """정책상 이 사용자에게 알릴 수 있는 일정 (날짜 조건 전)"""
    now = now or now_local()
    rows = _visible_rows(db, user)
    interested_notices = {event.notice_id for event, state in rows
                          if event.notice_id is not None and state and state.bookmarked}
    result = []
    for event, state in rows:
        if event.review_status not in ('auto', 'approved') or event.schedule_status == 'cancelled':
            continue
        if event.event_type in APPLICATION_TYPES:
            interested = bool(state and (state.bookmarked or state.registered)) or event.notice_id in interested_notices
            if interested and is_open(event, now):
                result.append({'event': event, 'state': state, 'kind': 'application_deadline'})
        elif state and state.registered and is_open(event, now):
            result.append({'event': event, 'state': state, 'kind': 'registered_event'})
    return result


def _when(day: str, clock: str) -> str:
    value = date.fromisoformat(day)
    label = f"{value.month}월 {value.day}일({'월화수목금토일'[value.weekday()]})"
    return f"{label} {clock}" if clock else label


def _content(kind: str, days: int, effective, notice_title: str | None) -> tuple[str, str]:
    lead = "내일" if days == 1 else f"{days}일 후"
    if kind == "deadline":
        title = f"[마감 {lead}] {effective.title}"
        body = f"{_when(effective.end_date, effective.end_time)}까지 접수 마감입니다."
    else:
        title = f"[일정 {lead}] {effective.title}"
        start = effective.start_date or effective.end_date
        body = _when(start, effective.start_time) + (f" · {effective.location}" if effective.location else "")
    if notice_title and notice_title not in effective.title:
        body = f"{notice_title}\n{body}"
    return title, body


def due_notifications(db: Session, user: User, now: datetime | None = None) -> list[dict]:
    """오늘 만들어야 하는 알림 (아직 저장하지 않음)"""
    now = now or now_local()
    today = now.astimezone(get_settings().tz).date()
    pref = get_or_create_preference(db, user)
    settings = (pref.notifications or {})
    enabled = {key for key in ("d3", "d1") if settings.get(key, True)}
    disabled_sources = {source for source, on in (pref.enabled_sources or {}).items() if on is False}
    dismissed = set(db.scalars(select(UserNotice.notice_id).where(
        UserNotice.user_id == user.id, UserNotice.dismissed.is_(True))))

    due = []
    for candidate in notification_candidates(db, user, now):
        event, state = candidate['event'], candidate['state']
        if event.source in disabled_sources or event.notice_id in dismissed:
            continue
        if state and state.action_status == 'done':
            continue
        if requires_confirmation(event) and not get_settings().notify_review_required:
            continue
        effective = effective_event(event, state)
        kind = "deadline" if candidate['kind'] == 'application_deadline' else "event"
        target = effective.end_date if kind == "deadline" else (effective.start_date or effective.end_date)
        if not target:
            continue
        days = (date.fromisoformat(target) - today).days
        if DAYS_BEFORE.get(days) not in enabled:
            continue
        notice = db.get(Notice, event.notice_id) if event.notice_id else None
        title, message = _content(kind, days, effective, notice.title_raw if notice else None)
        due.append({'event_id': event.id, 'kind': f"{kind}_{DAYS_BEFORE[days]}", 'target_date': target,
                    'title': title, 'message': message})
    return due


def generate_notifications(db: Session, now: datetime | None = None) -> int:
    """모든 사용자의 오늘 알림을 알림함에 저장. 이미 만든 알림은 건너뛴다. 새로 만든 건수 반환"""
    created = 0
    for user in db.scalars(select(User).order_by(User.id)).all():
        for item in due_notifications(db, user, now):
            exists = db.scalar(select(Notification.id).where(
                Notification.user_id == user.id, Notification.event_id == item['event_id'],
                Notification.kind == item['kind'], Notification.target_date == item['target_date']))
            if exists:
                continue
            db.add(Notification(user_id=user.id, **item))
            created += 1
        db.commit()
    return created


def _can_email(user: User, pref) -> bool:
    if not (pref.notifications or {}).get("email", True):
        return False
    # 개발용 로그인 사용자는 실제 메일 주소가 없다
    return bool(user.google_sub and user.google_sub != DEV_USER_SUB and "@" in (user.email or ""))


def send_pending_emails(db: Session, now: datetime | None = None) -> int:
    """대기 중인 알림을 사용자별로 묶어 이메일 1통으로 보낸다. 보낸 메일 수 반환"""
    now = now or now_local()
    today = now.astimezone(get_settings().tz).date().isoformat()
    pending = db.scalars(select(Notification).where(Notification.email_status == 'pending')
                         .order_by(Notification.user_id, Notification.target_date, Notification.id)).all()
    by_user: dict[int, list[Notification]] = {}
    for item in pending:
        by_user.setdefault(item.user_id, []).append(item)

    sent = 0
    for user_id, items in by_user.items():
        user = db.get(User, user_id)
        # 이미 지난 날짜의 알림은 늦게 보내지 않는다 (서버가 며칠 꺼져 있던 경우)
        stale = [item for item in items if item.target_date < today]
        fresh = [item for item in items if item.target_date >= today]
        for item in stale:
            item.email_status = 'skipped'
        if fresh and not _can_email(user, get_or_create_preference(db, user)):
            for item in fresh:
                item.email_status = 'skipped'
            fresh = []
        if fresh:
            subject = f"[NotiAI] 다가오는 일정 {len(fresh)}건" if len(fresh) > 1 else f"[NotiAI] {fresh[0].title}"
            body = "\n\n".join(f"{item.title}\n{item.message}" for item in fresh)
            body += f"\n\nNotiAI에서 확인하기: {get_settings().frontend_url}"
            try:
                send_email(user.email, subject, body)
            except EmailError as error:
                for item in fresh:
                    item.email_attempts += 1
                    item.email_error = str(error)
                    if item.email_attempts >= EMAIL_MAX_ATTEMPTS:
                        item.email_status = 'failed'
                log.warning("알림 메일 실패 user=%s: %s", user_id, error)
            else:
                for item in fresh:
                    item.email_status, item.email_sent_at, item.email_error = 'sent', utcnow(), None
                sent += 1
        db.commit()
    return sent


# ── 알림함 ────────────────────────────────────────────────────────────────────

def to_out(item: Notification) -> dict:
    return {
        'id': str(item.id), 'kind': item.kind, 'title': item.title, 'message': item.message,
        'event_id': str(item.event_id), 'target_date': item.target_date,
        'created_at': to_local(item.created_at).isoformat(timespec="seconds"), 'read': item.read_at is not None,
    }


def list_notifications(db: Session, user: User, limit: int, before: int | None) -> dict:
    query = select(Notification).where(Notification.user_id == user.id)
    if before:
        query = query.where(Notification.id < before)
    rows = db.scalars(query.order_by(Notification.id.desc()).limit(limit + 1)).all()
    return {'items': [to_out(item) for item in rows[:limit]],
            'next_before': str(rows[limit - 1].id) if len(rows) > limit else None}


def unread_count(db: Session, user: User) -> int:
    return db.scalar(select(func.count()).select_from(Notification).where(
        Notification.user_id == user.id, Notification.read_at.is_(None)))


def mark_read(db: Session, user: User, notification_id: int) -> bool:
    item = db.get(Notification, notification_id)
    if not item or item.user_id != user.id:
        return False
    if item.read_at is None:
        item.read_at = utcnow()
        db.commit()
    return True


def mark_all_read(db: Session, user: User) -> int:
    result = db.execute(update(Notification).where(Notification.user_id == user.id, Notification.read_at.is_(None))
                        .values(read_at=utcnow()), execution_options={'synchronize_session': False})
    db.commit()
    return result.rowcount
