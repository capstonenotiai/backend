from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user, is_admin
from app.models import User
from app.schemas import Preferences, Profile
from app.services import preferences

router = APIRouter(prefix="/api/user", tags=["user"])


@router.get("", response_model=Profile)
def get_profile(user: User = Depends(get_current_user)):
    return Profile(name=user.name, email=user.email, is_admin=is_admin(user))


@router.get("/preferences", response_model=Preferences)
def get_preferences(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return preferences.to_schema(preferences.get_or_create_preference(db, user))


@router.put("/preferences", response_model=Preferences)
def put_preferences(body: Preferences, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return preferences.save(db, user, body)
