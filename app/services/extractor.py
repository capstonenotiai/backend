"""
공지 원문 → 일정 5필드(title, start_date, end_date, location, detail) 추출.

EXTRACTOR 환경변수로 구현체를 고른다. 모델이 완성되면 model_api 로 바꾸기만 하면 되고
파이프라인·API 코드는 그대로 둔다.

  stub      : LLM 없이 제목만 저장 (전부 needs_review) — 키 없이 흐름 테스트용
  gpt       : OpenAI 로 추출 (모델 완성 전 임시). model repo 의 SYSTEM_PROMPT / postprocess 를 재사용
  model_api : 모델 팀 추론 서버 호출 (POST MODEL_API_URL)
"""
import importlib
import copy
import json
import logging
import re
import sys
from dataclasses import dataclass
from datetime import date
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
    event_type: str = 'event'
    start_time: str = ''
    end_time: str = ''
    timezone: str = 'Asia/Seoul'
    attendance_mode: str = 'unknown'
    schedule_status: str = 'confirmed'
    extraction_metadata: dict | None = None


@dataclass
class ExtractionResult:
    events: list[Extraction]
    raw: dict


class InvalidExtraction(ValueError):
    def __init__(self,message,raw):
        super().__init__(message)
        self.raw=raw


class RetryableExtraction(InvalidExtraction):
    """Transport failure eligible for a later extraction run."""


def parse_model_response(data: dict, title: str) -> ExtractionResult:
    from app.services.review import EventEdit
    if not isinstance(data,dict): raise ValueError('Model response must be an object')
    if data.get('schema_version') == 'notiai-model-candidate-v1':
        if data.get('metadata') is not None and not isinstance(data['metadata'], dict):
            raise ValueError('Invalid model metadata')
        if data.get('status') != 'candidate' or data.get('validation_errors') != [] or not isinstance(data.get('review_required'), bool):
            raise ValueError('Invalid or incomplete model generation')
        candidate = data.get('candidate')
        if not isinstance(candidate, dict) or set(candidate) != {'events'} or not isinstance(candidate['events'], list):
            raise ValueError('Invalid candidate events')
        if len(candidate['events']) > 50: raise ValueError('Too many candidate events')
        fields = {'title','event_type','start_date','end_date','start_time','end_time','location','attendance_mode','schedule_status'}
        results = []
        for item in candidate['events']:
            if not isinstance(item, dict) or not fields <= set(item) or set(item) - fields - {'review_reason', 'review_required'}:
                raise ValueError('Invalid candidate fields')
            if any(item[key] is not None and not isinstance(item[key], str) for key in fields):
                raise ValueError('Invalid candidate value type')
            if 'review_reason' in item and item['review_reason'] is not None and not isinstance(item['review_reason'], str):
                raise ValueError('Invalid review reason')
            if 'review_required' in item and not isinstance(item['review_required'], bool):
                raise ValueError('Invalid review flag')
            if item['event_type'] not in ('application','submission','event','interview'):
                raise ValueError('Unsupported slim-v9 event type')
            if item['event_type'] in ('application','submission') and item['attendance_mode'] != 'not_applicable':
                raise ValueError('Invalid non-event attendance mode')
            value = EventEdit(**{key: item[key] if item[key] is not None else '' for key in fields})
            if not value.start_date and not value.end_date:
                continue
            native = data.get('native_prediction')
            if (data.get('metadata') or {}).get('prompt_mode') == 'v10-native' and isinstance(native, dict):
                detail = native.get('detail', '')
                if not isinstance(detail, str): raise ValueError('Invalid native detail')
                value.detail = detail[:DETAIL_MAX]
            metadata = data.get('metadata') or {}
            reasons = []
            if item.get('review_reason'):
                reasons.append(item['review_reason'])
            elif item.get('review_required'):
                reasons.append('모델이 원문 확인을 요청했습니다.')
            if metadata.get('review_required_events'):
                reasons.append('모델이 날짜·장소 원문 확인을 요청했습니다.')
            if not value.end_date:
                reasons.append('종료일 또는 마감일을 원문에서 확인해 주세요.')
            results.append(Extraction(**value.model_dump(exclude={'id'}), review_status='auto',
                review_reason=' / '.join(reasons) or None, extraction_metadata=item))
        # This transport is deliberately NOT promoted to the full v2 evidence contract.
        return ExtractionResult(results, data)
    if 'events' not in data:
        pred=data.get('final_prediction',data)
        if not isinstance(pred,dict) or not {'title','start_date','end_date','location','detail'} <= set(pred):
            raise ValueError('Incomplete legacy response')
        result=to_extraction(pred,title)
        if result.review_status == 'needs_review':
            raise ValueError('Invalid legacy model dates')
        # The model cannot override a failed deterministic check to auto.
        if data.get('auto_register_status')=='needs_review':
            result.review_status='needs_review'
            result.review_reason=str(data.get('auto_register_reason') or '모델 검토 요청')
        return ExtractionResult([result],data)
    if data.get('schema_version')!='student-calendar-v2.0' or not isinstance(data['events'],list):
        raise ValueError('Unsupported events schema')
    if data.get('coverage') not in ('complete','partial','unavailable') or not isinstance(data.get('relations'),list) or not isinstance(data.get('issues'),list):
        raise ValueError('Missing coverage/relations/issues')
    if len(data['events'])>50: raise ValueError('Too many events')
    results=[]
    event_ids=set()
    for item in data['events']:
        if not isinstance(item,dict) or not item.get('event_id') or item['event_id'] in event_ids:
            raise ValueError('Missing/duplicate event_id')
        event_ids.add(item['event_id'])
        locations=item.get('locations',[])
        if not isinstance(locations,list) or any(not isinstance(x,dict) or not isinstance(x.get('name'),str) for x in locations):
            raise ValueError('Invalid locations')
        value=EventEdit(title=item['title'],event_type=item['event_type'],
            start_date=item.get('start_date') or '',end_date=item.get('end_date') or '',
            start_time=item.get('start_time') or '',end_time=item.get('end_time') or '',
            timezone=item.get('timezone') or 'Asia/Seoul',location=' / '.join(x['name'] for x in locations),
            detail=item.get('detail') or '',attendance_mode=item.get('attendance_mode','unknown'),
            schedule_status=item.get('schedule_status','unknown'))
        results.append(Extraction(**value.model_dump(exclude={'id'}),review_status='needs_review',
            review_reason='v2 추출 결과 관리자 검토 필요',extraction_metadata=item))
    return ExtractionResult(results,data)


class Extractor(Protocol):
    name: str

    def extract(self, title: str, body: str, reference_time: str | None = None) -> Extraction | ExtractionResult: ...


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
    try:
        date.fromisoformat(end)
        if start: date.fromisoformat(start)
    except ValueError:
        return 'needs_review','유효하지 않은 날짜'
    if start and start > end:
        return "needs_review", f"start({start}) > end({end}): 날짜 역전"
    return "auto", None


def _clean_date(value) -> str:
    text = str(value or "").strip()
    try:
        return text if _DATE_RE.match(text) and date.fromisoformat(text) else ''
    except ValueError:
        return ''


def to_extraction(pred: dict, fallback_title: str) -> Extraction:
    start = _clean_date(pred.get("start_date"))
    end = _clean_date(pred.get("end_date"))
    status, reason = decide_review_status(start, end)
    if (pred.get('start_date') and not start) or (pred.get('end_date') and not end):
        status,reason='needs_review','유효하지 않은 날짜'
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

    def extract(self, title: str, body: str, reference_time: str | None = None) -> Extraction:
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

    def extract(self, title: str, body: str, reference_time: str | None = None) -> Extraction:
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
        raw=copy.deepcopy(pred)
        if self.postprocess:
            pred = self.postprocess(pred, user_content)
        return ExtractionResult([to_extraction(pred,title)],{'raw_prediction':raw,'final_prediction':pred})


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
        self.token = get_settings().model_api_token
        if not self.url:
            raise RuntimeError("EXTRACTOR=model_api 인데 MODEL_API_URL 이 없습니다.")

    def extract(self, title: str, body: str, reference_time: str | None = None) -> ExtractionResult:
        from app.services.notice_metadata import iso_publication
        try:
            response = httpx.post(
                self.url,
                json={"title": title, "body": body, "user_content": build_user_content(title, body),
                      "reference_time": iso_publication(reference_time)},
                timeout=120,
                headers={'X-Model-Token': self.token} if self.token else {},
                follow_redirects=False,  # Never forward the shared secret to another origin.
            )
        except httpx.TransportError as exc:
            raise RetryableExtraction('모델 연결 실패 또는 시간 초과', {'error_type': type(exc).__name__}) from None
        if response.status_code == 429 or response.status_code >= 500:
            raise RetryableExtraction('모델 서버 일시 오류', {'status_code': response.status_code})
        try: data = response.json()
        except ValueError as exc:
            raise InvalidExtraction('Model API returned non-JSON', {'status_code': response.status_code,
                'response_text': response.text[:10000]}) from exc
        if response.is_error:
            raise InvalidExtraction(f'Model API HTTP {response.status_code}', {'status_code': response.status_code, 'response': data})
        try: return parse_model_response(data,title)
        except (ValueError,KeyError,TypeError) as exc:
            raise InvalidExtraction(str(exc),data) from exc


_EXTRACTORS = {"stub": StubExtractor, "gpt": GptExtractor, "model_api": ModelApiExtractor}


def get_extractor(name: str | None = None) -> Extractor:
    key = name or get_settings().extractor
    if key not in _EXTRACTORS:
        raise ValueError(f"알 수 없는 EXTRACTOR: {key} (가능: {', '.join(_EXTRACTORS)})")
    return _EXTRACTORS[key]()
