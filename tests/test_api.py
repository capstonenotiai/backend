from app.services.extractor import decide_review_status, to_extraction
from app.services.pipeline import extract_pending, import_records
from app.services.planner import build_events_context, parse_chat_request, PlannerError

import pytest


def test_health(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["db"]["events"] == 6


def test_events_match_frontend_contract(client):
    events = client.get("/api/events").json()
    assert len(events) == 6
    keys = {
        "id", "title", "start_date", "end_date", "location", "detail", "source", "source_url",
        "category", "is_new", "registered", "bookmarked", "collected_at", "review_status",
    }
    assert set(events[0]) == keys
    assert all(isinstance(event["id"], str) for event in events)
    by_title = {event["title"]: event for event in events}
    assert by_title["2026 충북대학교 창업경진대회"]["registered"] is True
    assert by_title["제27회 대한민국 대학생 광고대상"]["is_new"] is True
    assert by_title["제27회 대한민국 대학생 광고대상"]["location"] == ""


def test_register_and_unregister(client):
    event = next(e for e in client.get("/api/events").json() if not e["registered"])
    assert client.post("/api/calendar/register", json={"event_id": event["id"]}).json() == {
        "id": event["id"], "registered": True,
    }
    assert next(e for e in client.get("/api/events").json() if e["id"] == event["id"])["registered"] is True
    assert client.delete(f"/api/calendar/register/{event['id']}").json()["registered"] is False


def test_login_without_google_config_redirects_in_dev(client):
    response = client.get("/api/auth/google/login", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"].endswith("/dashboard")


def test_bookmark_preflight_allows_frontend_origin(client):
    response = client.options(
        "/api/events/1/bookmark",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "PUT",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert response.headers["access-control-allow-credentials"] == "true"


def test_unexpected_error_keeps_cors_and_message(client, monkeypatch):
    def boom(_db):
        raise RuntimeError("DB 연결 끊김")

    monkeypatch.setattr("app.routers.dashboard.get_summary", boom)
    response = client.get("/api/dashboard", headers={"Origin": "http://localhost:5173"})
    assert response.status_code == 500
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "서버 오류" in response.json()["message"]


def test_collect_records_site_failures(db, monkeypatch, tmp_path):
    import json

    from app.models import CrawlRun
    from app.services import pipeline

    crawled = tmp_path / "crawled_all.jsonl"
    crawled.write_text(
        json.dumps({"source_url": "https://c/1", "site": "cbnu", "board": "scholarship", "title_raw": "장학", "raw_text": ""}) + "\n",
        encoding="utf-8",
    )
    summary = {
        "cbnu": {"status": "done", "new": 1, "failed_requests": ["https://c/board (HTTPError: 404)"], "error": None},
        "wevity": {"status": "failed", "new": 0, "failed_requests": [], "error": "RuntimeError: curl_cffi 없음"},
        # contestkorea: 결과 없음 → 실패로 기록
    }
    monkeypatch.setattr(pipeline, "CRAWLED_FILE", crawled)
    monkeypatch.setattr(pipeline, "run_crawler", lambda site=None: (1, summary))

    result = pipeline.collect(db)
    assert result["imported"] == {"cbnu": 1}
    assert sorted(result["failed"]) == ["contestkorea", "wevity"]

    runs = {run.site: run for run in db.query(CrawlRun).all()}
    assert runs["cbnu"].status == "done" and "요청 실패 1건" in runs["cbnu"].message
    assert runs["wevity"].status == "failed" and "curl_cffi" in runs["wevity"].message
    assert runs["contestkorea"].status == "failed" and "exit code 1" in runs["contestkorea"].message


def test_interrupted_runs_marked_failed(db):
    from app.models import CrawlRun
    from app.services.pipeline import mark_interrupted_runs

    db.add_all([CrawlRun(site="cbnu", status="running"), CrawlRun(site="wevity", status="done")])
    db.commit()
    assert mark_interrupted_runs(db) == 1
    statuses = {run.site: (run.status, run.message) for run in db.query(CrawlRun).all()}
    assert statuses["cbnu"] == ("failed", "서버 재시작으로 수집이 중단됨")
    assert statuses["wevity"][0] == "done"


def test_register_unknown_event_returns_message(client):
    response = client.post("/api/calendar/register", json={"event_id": "nope"})
    assert response.status_code == 404
    assert response.json() == {"message": "일정을 찾을 수 없습니다."}


def test_bookmark(client):
    event_id = client.get("/api/events").json()[0]["id"]
    assert client.put(f"/api/events/{event_id}/bookmark", json={"bookmarked": True}).json()["bookmarked"] is True


def test_preferences_roundtrip_and_source_filter(client):
    prefs = client.get("/api/user/preferences").json()
    assert prefs["ai_mode"] == "study"
    prefs["ai_mode"] = "explorer"
    prefs["enabled_sources"]["wevity"] = False
    saved = client.put("/api/user/preferences", json=prefs).json()
    assert saved["ai_mode"] == "explorer"
    assert all(e["source"] != "wevity" for e in client.get("/api/events").json())


def test_unknown_mode_falls_back(client):
    prefs = client.get("/api/user/preferences").json()
    prefs["ai_mode"] = "removed-mode"
    assert client.put("/api/user/preferences", json=prefs).json()["ai_mode"] == "study"


def test_profile_and_dashboard(client):
    assert client.get("/api/user").json()["email"] == "dev@notiai.local"
    summary = client.get("/api/dashboard").json()
    assert {s["source"] for s in summary["sources"]} == {"cbnu", "wevity", "contestkorea"}
    assert summary["collectedChangeLabel"].endswith("vs 어제")


def test_planner_validation_and_missing_key(client):
    assert client.post("/api/planner/chat", json={"message": "  "}).status_code == 400
    assert client.post("/api/planner/chat", content=b"not json").status_code == 400
    assert client.post("/api/planner/chat", content=b"x" * (65 * 1024)).status_code == 413
    response = client.post("/api/planner/chat", json={"message": "이번 주 뭐 해?", "mode": "study", "history": []})
    assert response.status_code == 500
    assert response.json()["message"] == "AI 플래너 서버 설정이 완료되지 않았습니다."


def test_parse_chat_request_drops_duplicate_and_bad_roles():
    message, mode, history = parse_chat_request(
        {
            "message": "안녕",
            "mode": "unknown",
            "history": [{"role": "system", "content": "무시"}, {"role": "user", "content": "안녕"}],
        }
    )
    assert (message, mode, history) == ("안녕", "study", [])
    with pytest.raises(PlannerError):
        parse_chat_request({"message": "a" * 3001})


def test_events_context_lists_upcoming(client):
    from datetime import date

    from app.schemas import EventOut

    event = EventOut(
        id="1", title="테스트 공모전", start_date="", end_date="2026-10-01", location="", detail="",
        source="wevity", source_url="", category="contest", is_new=False, registered=False,
        bookmarked=False, collected_at=None, review_status="auto",
    )
    context = build_events_context([event], date(2026, 9, 29))
    assert "D-2 | 테스트 공모전 | ~ 2026-10-01" in context


def test_import_and_stub_extract(db):
    records = [
        {"source_url": "https://a", "site": "cbnu", "board": "scholarship", "title_raw": "장학 공지", "raw_text": "본문"},
        {"source_url": "https://a", "site": "cbnu", "title_raw": "중복", "raw_text": ""},
        {"source_url": "https://b", "site": "wevity", "title_raw": "OO 서포터즈 모집", "raw_text": "본문"},
    ]
    assert dict(import_records(db, records)) == {"cbnu": 1, "wevity": 1}
    assert extract_pending(db) == 2
    assert extract_pending(db) == 0


def test_categorize_by_board():
    from app.services.category import categorize

    assert categorize("cbnu", "scholarship") == "scholarship"
    assert categorize("cbnu", "employment") == "career"
    assert categorize("cbnu", "sw_notice") == "academic"
    assert categorize("cbnu", "major") == "academic"
    assert categorize("cbnu", "activity") == "activity"
    assert categorize("cbnu", None) == "academic"
    assert categorize("wevity", None) == "contest"
    assert categorize("wevity", "activity") == "activity"
    assert categorize("contestkorea", "unknown-board") == "contest"


def test_cbnu_canonical_url_drops_list_params():
    from crawler.cbnu import canonical_url

    expected = "https://software.cbnu.ac.kr/index.php?mid=sub0401&document_srl=1154319"
    assert canonical_url("https://software.cbnu.ac.kr/index.php?mid=sub0401&page=1&document_srl=1154319") == expected
    assert canonical_url("/index.php?mid=sub0401&category=8409&page=3&document_srl=1154319") == expected


def test_review_status_rules():
    assert decide_review_status("", "") == ("needs_review", "end_date 없음")
    assert decide_review_status("2026-06-02", "2026-06-01")[0] == "needs_review"
    assert decide_review_status("", "2026-06-01") == ("auto", None)
    result = to_extraction({"title": "t", "start_date": "2026.05.01", "end_date": "2026-05-22"}, "raw")
    assert result.start_date == "" and result.review_status == "auto"
