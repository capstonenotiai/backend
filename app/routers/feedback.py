from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user
from app.models import User
from app.schemas import FeedbackIn, FeedbackOut
from app.services import feedback

router = APIRouter(prefix='/api/feedback', tags=['feedback'])


@router.post('', response_model=FeedbackOut, status_code=201)
def submit(body: FeedbackIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return feedback.submit(db, user, body)
