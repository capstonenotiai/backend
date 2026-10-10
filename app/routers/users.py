from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user, is_admin
from app.models import User
from app.schemas import OnboardingIn, Preferences, Profile, RecommendationProfile
from app.services import preferences

router = APIRouter(prefix="/api/user", tags=["user"])


@router.get("", response_model=Profile)
def get_profile(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    pref = preferences.get_or_create_preference(db, user)
    return Profile(name=user.name, email=user.email, is_admin=is_admin(user),
        major=pref.major, grade=pref.grade, enrollment_status=pref.enrollment_status,
        onboarding_done=user.onboarding_done)


@router.put('/onboarding', response_model=Profile)
def put_onboarding(body: OnboardingIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    user.onboarding_done = body.done
    db.commit()
    return get_profile(user, db)


@router.put('/profile', response_model=Profile)
def put_profile(body: RecommendationProfile, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    pref = preferences.get_or_create_preference(db, user)
    for key, value in body.model_dump().items():
        setattr(pref, key, value)
    db.commit()
    return get_profile(user, db)


@router.get("/preferences", response_model=Preferences)
def get_preferences(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return preferences.to_schema(preferences.get_or_create_preference(db, user))


@router.put("/preferences", response_model=Preferences)
def put_preferences(body: Preferences, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return preferences.save(db, user, body)
