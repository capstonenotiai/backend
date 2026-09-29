"""
공지 원문 → 일정 5필드(title, start_date, end_date, location, detail) 추출.

EXTRACTOR 환경변수로 구현체를 고른다. 모델이 완성되면 model_api 로 바꾸기만 하면 되고
파이프라인·API 코드는 그대로 둔다.

  stub      : LLM 없이 제목만 저장 (전부 needs_review) — 키 없이 흐름 테스트용
  gpt       : OpenAI 로 추출 (모델 완성 전 임시). model repo 의 SYSTEM_PROMPT / postprocess 를 재사용
  model_api : 모델 팀 추론 서버 호출 (POST MODEL_API_URL)
"""
import importlib
import json
import logging
import re
import sys
from dataclasses import dataclass
from typing import Protocol

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DETAIL_MAX = 120

# model repo 를 못 찾을 때 쓰는 요약본 (원본: capstonenotiai/model src/config.py SYSTEM_PROMPT)
FALLBACK_SYSTEM_PROMPT = (
    "당신은 대학 공지문에서 자동 캘린더 등록용 일정 정보를 추출하는 assistant입니다. "
    "반드시 JSON 객체 하나만 출력하세요. JSON 키는 title, start_date, end_date, location, detail만 사용하세요. "
    "날짜는 YYYY-MM-DD 형식이며, 확정할 수 없으면 빈 문자열 \"\"로 쓰세요. 연도 근거가 없으면 임의로 추론하지 마세요. "
    "title은 [제목] 섹션을 기준으로 하고 기관명·기업명만 있는 대괄호 prefix와 D-day 표시는 제거하세요. "
    "모집·공모는 접수 시작일/마감일을, 행사·교육은 행사 시작일/종료일을 사용하세요. "
    "시작일이 명시되지 않으면 start_date는 \"\"이며 end_date를 복사하지 마세요. 발표일·시상일은 detail에만 넣으세요. "
    "location은 실제 장소가 명확할 때만 쓰고, 온라인 접수·이메일 제출은 장소가 아닙니다. 행사 자체가 온라인이면 \"온라인\". "
    "detail은 대상·접수방법·발표일 등 보조정보를 1문장 120자 이내로 쓰세요."
)


@dataclass
class Extraction:
    title: str
    start_date: str
    end_date: str
    location: str
    detail: str
    review_status: str  # auto | needs_review
    review_reason: str | None = None


class Extractor(Protocol):
    name: str

    def extract(self, title: str, body: str) -> Extraction: ...


def build_user_content(title: str, body: str) -> str:
    """model repo src/infer.py 와 같은 입력 형식"""
    return f"[제목]\n{title}\n\n[본문]\n{body}"


def decide_review_status(start: str, end: str) -> tuple[str, str | None]:
    """
    자동 등록 가능 여부 — model repo scripts/cascade_infer.py get_auto_register_status 의 기본 규칙만 옮김.
    (마감일을 실제보다 늦게 알려주는 사고를 막는 게 목적 → 애매하면 needs_review)
    """
    if not end:
        return "needs_review", "end_date 없음"
    if start and start > end:
        return "needs_review", f"start({start}) > end({end}): 날짜 역전"
    return "auto", None


def _clean_date(value) -> str:
    text = str(value or "").strip()
    return text if _DATE_RE.match(text) else ""


def to_extraction(pred: dict, fallback_title: str) -> Extraction:
    start = _clean_date(pred.get("start_date"))
    end = _clean_date(pred.get("end_date"))
    status, reason = decide_review_status(start, end)
    return Extraction(
        title=str(pred.get("title") or fallback_title).strip(),
        start_date=start,
        end_date=end,
        location=str(pred.get("location") or "").strip(),
        detail=str(pred.get("detail") or "").strip()[:DETAIL_MAX],
        review_status=status,
        review_reason=reason,
    )


def _model_repo_module(name: str):
    """MODEL_REPO_PATH 가 설정돼 있으면 model repo 의 모듈(src.config 등)을 import"""
    path = get_settings().model_repo_path
    if not path:
        return None
    if path not in sys.path:
        sys.path.insert(0, path)
    try:
        return importlib.import_module(name)
    except Exception as error:  # noqa: BLE001 — model repo 의존성이 없어도 서버는 떠야 함
        log.warning("model repo 모듈 %s 를 불러오지 못했습니다: %s", name, error)
        return None


class StubExtractor:
    name = "stub"

    def extract(self, title: str, body: str) -> Extraction:
        return Extraction(
            title=title.strip(),
            start_date="",
            end_date="",
            location="",
            detail="",
            review_status="needs_review",
            review_reason="stub extractor (일정 미추출)",
        )


class GptExtractor:
    name = "gpt"

    def __init__(self):
        from openai import OpenAI

        settings = get_settings()
        if not settings.openai_api_key:
            raise RuntimeError("EXTRACTOR=gpt 인데 OPENAI_API_KEY 가 없습니다.")
        self.client = OpenAI(api_key=settings.openai_api_key, max_retries=2, timeout=60)
        self.model = settings.extract_model
        config = _model_repo_module("src.config")
        self.system_prompt = getattr(config, "SYSTEM_PROMPT", None) or FALLBACK_SYSTEM_PROMPT
        postprocess_module = _model_repo_module("src.postprocess")
        self.postprocess = getattr(postprocess_module, "postprocess", None)

    def extract(self, title: str, body: str) -> Extraction:
        user_content = build_user_content(title, body)
        response = self.client.chat.completions.create(
            model=self.model,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_content},
            ],
        )
        pred = json.loads(response.choices[0].message.content or "{}")
        if self.postprocess:
            pred = self.postprocess(pred, user_content)
        return to_extraction(pred, title)


class ModelApiExtractor:
    """
    모델 팀 추론 서버 호출.
    요청: POST MODEL_API_URL { "title", "body", "user_content" }
    응답: { title, start_date, end_date, location, detail, auto_register_status?, auto_register_reason? }
          또는 cascade_infer.py 형태 { final_prediction: {...}, auto_register_status, auto_register_reason }
    → 실제 형식은 모델 팀과 맞춘 뒤 이 함수만 고치면 됨
    """

    name = "model_api"

    def __init__(self):
        self.url = get_settings().model_api_url
        if not self.url:
            raise RuntimeError("EXTRACTOR=model_api 인데 MODEL_API_URL 이 없습니다.")

    def extract(self, title: str, body: str) -> Extraction:
        response = httpx.post(
            self.url,
            json={"title": title, "body": body, "user_content": build_user_content(title, body)},
            timeout=120,
        )
        response.raise_for_status()
        data = response.json()
        pred = data.get("final_prediction", data)
        result = to_extraction(pred, title)
        # 모델 쪽 판단(cascade trigger 등)이 더 정교하므로 있으면 우선 사용
        if data.get("auto_register_status") in ("auto", "needs_review"):
            result.review_status = data["auto_register_status"]
            result.review_reason = data.get("auto_register_reason")
        return result


_EXTRACTORS = {"stub": StubExtractor, "gpt": GptExtractor, "model_api": ModelApiExtractor}


def get_extractor(name: str | None = None) -> Extractor:
    key = name or get_settings().extractor
    if key not in _EXTRACTORS:
        raise ValueError(f"알 수 없는 EXTRACTOR: {key} (가능: {', '.join(_EXTRACTORS)})")
    return _EXTRACTORS[key]()
