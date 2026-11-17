from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user
from app.models import User
from app.services import accounts

router = APIRouter(prefix='/api/users', tags=['user'])


@router.delete('/me', status_code=204)
def delete_account(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    accounts.delete_account(db, user)
    request.session.clear()
    return Response(status_code=204)
