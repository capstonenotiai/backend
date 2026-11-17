"""OpenAI 호출별 토큰 사용량 기록 (비용 추정용). 기록 실패는 본 기능을 멈추지 않는다."""
import logging
from collections import defaultdict
from datetime import timedelta

from sqlalchemy import select

from app.db import SessionLocal
from app.models import OpenAIUsage, utcnow
from app.timeutil import to_local

log = logging.getLogger(__name__)


def record(kind: str, model: str, response) -> None:
    """kind: enrichment | recommendation | chat. response.usage 가 없으면(테스트 가짜 응답 등) 기록하지 않는다."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    log.info("[openai] %s model=%s input=%d output=%d", kind, model, input_tokens, output_tokens)
    try:
        with SessionLocal() as db:
            db.add(OpenAIUsage(kind=kind, model=model, input_tokens=input_tokens, output_tokens=output_tokens))
            db.commit()
    except Exception:  # noqa: BLE001
        log.warning("OpenAI 사용량 기록 실패 kind=%s", kind)


def summary(db, days: int = 7) -> list[dict]:
    """최근 N일 날짜(Asia/Seoul)·종류·모델별 호출 수와 토큰 합계"""
    since = utcnow() - timedelta(days=days)
    totals = defaultdict(lambda: {"calls": 0, "input_tokens": 0, "output_tokens": 0})
    for row in db.scalars(select(OpenAIUsage).where(OpenAIUsage.created_at >= since)):
        key = (to_local(row.created_at).date().isoformat(), row.kind, row.model)
        totals[key]["calls"] += 1
        totals[key]["input_tokens"] += row.input_tokens
        totals[key]["output_tokens"] += row.output_tokens
    return [{"date": day, "kind": kind, "model": model, **values}
            for (day, kind, model), values in sorted(totals.items())]
