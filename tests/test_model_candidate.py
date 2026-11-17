import pytest
from sqlalchemy import select

from app.models import Event, Notice
from app.services.extractor import ModelApiExtractor, InvalidExtraction, parse_model_response
from app.services.pipeline import extract_pending


def candidate():
    return {'schema_version': 'notiai-model-candidate-v1', 'status': 'candidate', 'review_required': True,
        'validation_errors': [], 'raw_output': 'original model text', 'candidate': {'events': [
            {'title': '접수', 'event_type': 'application', 'start_date': None, 'end_date': '2030-10-01',
             'start_time': None, 'end_time': '18:00', 'location': None, 'attendance_mode': 'not_applicable', 'schedule_status': 'confirmed'},
            {'title': '행사', 'event_type': 'event', 'start_date': '2030-10-03', 'end_date': '2030-10-03',
             'start_time': '14:00', 'end_time': '15:00', 'location': 'S4-1동 101호', 'attendance_mode': 'offline', 'schedule_status': 'confirmed'}]}}


def test_valid_candidate_is_automatically_public(db):
    raw = candidate()
    class Extractor:
        name = 'model_api'
        def extract(self, title, body): return parse_model_response(raw, title)
    notice = Notice(site='cbnu', title_raw='공지', raw_text='body', source_url='https://example.com/model')
    db.add(notice); db.commit()
    assert extract_pending(db, Extractor()) == 1
    events = db.scalars(select(Event).where(Event.notice_id == notice.id)).all()
    assert len(events) == 2
    assert all(e.review_status == 'auto' and e.ai_extracted for e in events)
    assert events[0].location == '' and events[0].end_time == '18:00'
    assert events[1].location == 'S4-1동 101호'
    assert notice.extraction_result == raw


@pytest.mark.parametrize('change', [{'status': 'invalid'}, {'review_required': None}, {'validation_errors': ['generation_timeout']}])
def test_invalid_envelope_rejected(change):
    raw = candidate(); raw.update(change)
    with pytest.raises(ValueError): parse_model_response(raw, '공지')


def test_invalid_generation_preserved_on_failed_notice(db):
    raw = candidate(); raw['status'] = 'invalid'
    class Extractor:
        name = 'model_api'
        def extract(self, title, body): raise InvalidExtraction('generation_timeout', raw)
    notice = Notice(site='cbnu', title_raw='공지', source_url='https://example.com/failed')
    db.add(notice); db.commit()
    assert extract_pending(db, Extractor()) == 0
    assert notice.extraction_state == 'failed'
    assert notice.extraction_result == raw
    assert not db.scalars(select(Event)).all()


@pytest.mark.parametrize('response', ['http_error', 'invalid_json'])
def test_http_failure_preserves_response(monkeypatch, response):
    import httpx
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), 'model_api_url', 'http://127.0.0.1:8765/extract')
    result = httpx.Response(503, json={'error': 'model_busy'}) if response == 'http_error' else httpx.Response(200, text='truncated')
    monkeypatch.setattr(httpx, 'post', lambda *args, **kwargs: result)
    with pytest.raises(InvalidExtraction) as caught:
        ModelApiExtractor().extract('공지', '본문')
    assert caught.value.raw['status_code'] == result.status_code


def test_shared_token_header_and_native_detail(monkeypatch):
    import httpx
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), 'model_api_url', 'http://127.0.0.1:8765/extract')
    monkeypatch.setattr(get_settings(), 'model_api_token', 'test-only-not-a-real-secret')
    raw = candidate()
    raw['candidate']['events'] = raw['candidate']['events'][:1]
    raw['metadata'] = {'prompt_mode':'v10-native'}
    raw['native_prediction'] = {'detail':'원래 5필드 요약'}
    calls = []
    def post(*args, **kwargs):
        calls.append(kwargs)
        return httpx.Response(200, json=raw)
    monkeypatch.setattr(httpx, 'post', post)
    result = ModelApiExtractor().extract('공지', '본문')
    assert result.events[0].detail == '원래 5필드 요약'
    assert result.events[0].review_status == 'auto'
    assert calls[0]['headers'] == {'X-Model-Token':'test-only-not-a-real-secret'}
    assert calls[0]['follow_redirects'] is False
