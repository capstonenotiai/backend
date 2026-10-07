"""Authenticated, user-scoped facts for the future planner. No public UI yet."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db import get_db
from app.deps import get_current_user
from app.models import User
from app.services.events import _visible_rows, event_detail, get_event_or_404
from app.services.planner_facts import all_opportunity_facts, event_facts

router = APIRouter(prefix='/api/internal/planner', tags=['planner-facts'])


@router.get('/facts')
def facts(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return [event_facts(db, user, event, state) for event, state in _visible_rows(db, user)]


@router.get('/facts/{event_id}')
def fact(event_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    event_detail(db, user, event_id)
    return event_facts(db, user, get_event_or_404(db, event_id))


@router.get('/opportunities')
def opportunities(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return all_opportunity_facts(db, user)
