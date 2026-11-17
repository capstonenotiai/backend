from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import User

DEV_USER_EMAIL = "dev@notiai.local"
# google_sub 는 unique → 동시 요청이 개발용 사용자를 두 번 만들지 못하게 막는 용도
DEV_USER_SUB = "dev-user"


def is_admin(user: User) -> bool:
    allowed = {x.strip().casefold() for x in get_settings().admin_emails.split(',') if x.strip()}
    return bool(user.google_sub and user.google_sub != DEV_USER_SUB and user.email.casefold() in allowed)


def require_admin(request: Request, db: Session = Depends(get_db)) -> User:
    user = db.get(User, request.session.get('user_id')) if request.session.get('user_id') else None
    if not user:
        raise HTTPException(401, '관리자 로그인이 필요합니다.')
    if not is_admin(user):
        raise HTTPException(403, '관리자 권한이 필요합니다.')
    # Cross-origin cookie deployments must not allow third-party admin writes.
    if request.method not in ('GET','HEAD','OPTIONS'):
        origin = request.headers.get('origin')
        if origin not in get_settings().cors_origin_list:
            raise HTTPException(403, '허용되지 않은 요청 출처입니다.')
    return user


def get_or_create_dev_user(db: Session) -> User:
    user = db.scalar(select(User).where(User.google_sub == DEV_USER_SUB))
    if user:
        return user
    db.add(User(google_sub=DEV_USER_SUB, email=DEV_USER_EMAIL, name="개발용 사용자"))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
    return db.scalar(select(User).where(User.google_sub == DEV_USER_SUB))


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """
    세션 쿠키(user_id)로 로그인 사용자를 찾는다.
    DEV_LOGIN=true 이면 로그인 안 된 요청도 '개발용 사용자'로 처리한다.
    """
    user_id = request.session.get("user_id")
    if user_id is not None:
        user = db.get(User, user_id)
        if user:
            return user
        request.session.clear()

    if get_settings().dev_login:
        return get_or_create_dev_user(db)

    raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
