from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user
from app.schemas import DashboardSummary
from app.services.dashboard import get_summary

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("", response_model=DashboardSummary, dependencies=[Depends(get_current_user)])
def get_dashboard(db: Session = Depends(get_db)):
    return get_summary(db)
