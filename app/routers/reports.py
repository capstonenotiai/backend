from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user
from app.models import User
from app.services import reports

router = APIRouter(prefix='/api/reports', tags=['reports'])


@router.get('/mine')
def mine(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return reports.list_mine(db, user)
