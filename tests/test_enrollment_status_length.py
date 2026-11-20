from typing import get_args

from app.models import Preference
from app.schemas import RecommendationProfile


def test_every_enrollment_status_fits_column():
    """'선택 안 함'(unknown)을 포함해 모든 재학 상태 값이 DB 칸에 들어가야 한다."""
    length = Preference.__table__.c.enrollment_status.type.length
    values = get_args(RecommendationProfile.model_fields['enrollment_status'].annotation)
    assert 'unknown' in values and all(len(value) <= length for value in values)


def test_major_column_fits_schema_limit():
    """학과명은 화면 입력 상한(255자)까지 저장할 수 있어야 한다."""
    assert Preference.__table__.c.major.type.length >= next(m.max_length for m in RecommendationProfile.model_fields['major'].metadata if hasattr(m, 'max_length'))
