from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Preference, User
from app.schemas import AI_MODE_IDS, DEFAULT_AI_MODE, Preferences


def get_or_create_preference(db: Session, user: User) -> Preference:
    pref = db.get(Preference, user.id)
    if pref:
        return pref
    defaults = Preferences()
    db.add(
        Preference(
            user_id=user.id,
            ai_mode=defaults.ai_mode,
            interests=defaults.interests,
            enabled_sources=defaults.enabled_sources,
            notifications=defaults.notifications.model_dump(),
            auto_mode_recommend=defaults.auto_mode_recommend,
        )
    )
    try:
        db.commit()
    except IntegrityError:
        # 첫 화면에서 여러 요청이 동시에 만들려고 한 경우 — 먼저 만든 것을 사용
        db.rollback()
    return db.get(Preference, user.id)


def to_schema(pref: Preference) -> Preferences:
    return Preferences(
        ai_mode=pref.ai_mode if pref.ai_mode in AI_MODE_IDS else DEFAULT_AI_MODE,
        interests=pref.interests or [],
        enabled_sources=pref.enabled_sources or {},
        notifications=pref.notifications or {},
        auto_mode_recommend=pref.auto_mode_recommend,
    )


def save(db: Session, user: User, data: Preferences) -> Preferences:
    pref = get_or_create_preference(db, user)
    fields = data.model_fields_set
    if 'ai_mode' in fields:
        pref.ai_mode = data.ai_mode if data.ai_mode in AI_MODE_IDS else DEFAULT_AI_MODE
    if 'interests' in fields:
        pref.interests = list(dict.fromkeys(data.interests))
    if 'enabled_sources' in fields:
        pref.enabled_sources = dict(data.enabled_sources)
    if 'notifications' in fields:
        pref.notifications = {**(pref.notifications or {}), **data.notifications.model_dump(exclude_unset=True)}
    if 'auto_mode_recommend' in fields:
        pref.auto_mode_recommend = data.auto_mode_recommend
    db.commit()
    return to_schema(pref)
