from types import SimpleNamespace

from sqlalchemy import select

from app.models import OpenAIUsage
from app.services import enrichment, openai_usage


def fake_response(input_tokens=1200, output_tokens=300):
    return SimpleNamespace(usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens))


def test_record_and_summary(db):
    openai_usage.record('enrichment', 'test-model', fake_response())
    openai_usage.record('enrichment', 'test-model', fake_response(800, 100))
    openai_usage.record('recommendation', 'test-model', fake_response(5000, 900))
    openai_usage.record('chat', 'test-model', SimpleNamespace())  # usage 없는 응답은 기록하지 않음
    rows = openai_usage.summary(db)
    assert [(row['kind'], row['calls'], row['input_tokens'], row['output_tokens']) for row in rows] == [
        ('enrichment', 2, 2000, 400), ('recommendation', 1, 5000, 900)]


def test_enrichment_request_records_usage(db):
    class Client:
        responses = None

        def __init__(self):
            self.responses = self

        def create(self, **kwargs):
            return SimpleNamespace(status='completed', output_text='{}', usage=SimpleNamespace(input_tokens=10, output_tokens=5))

    settings = SimpleNamespace(enrich_model='test-model', enrich_max_output_tokens=100)
    enrichment._request(Client(), settings, {'title_raw': '', 'raw_text': '', 'events': []})
    row = db.scalar(select(OpenAIUsage))
    assert (row.kind, row.model, row.input_tokens, row.output_tokens) == ('enrichment', 'test-model', 10, 5)
