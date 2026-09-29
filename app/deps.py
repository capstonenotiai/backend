from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import User

DEV_USER_EMAIL = "dev@notiai.local"


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
        user = db.scalar(select(User).where(User.email == DEV_USER_EMAIL))
        if not user:
            user = User(email=DEV_USER_EMAIL, name="개발용 사용자")
            db.add(user)
            db.commit()
        return user

    raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
