# NotiAI — Backend

> 다중 프롬프트 기반 AI 대학생 일정 자동 생성 시스템 · 충북대학교 소프트웨어학부 졸업작품

- 프론트엔드: [jaeyeongt/NotiAi](https://github.com/jaeyeongt/NotiAi) — API 계약은 프론트 `src/services/*.js` 기준
- 모델: [capstonenotiai/model](https://github.com/capstonenotiai/model) · 크롤러는 이 repo 의 `crawler/` (model repo 에서 옮겨 옴)

```
[스케줄러] crawler/ → notices(원문) → Extractor(모델) → events
[React] ──쿠키 세션──▶ [FastAPI] ── DB / Google Calendar / OpenAI(AI 플래너)
```

## 기술 스택

| 구분 | 사용 |
|---|---|
| 서버 | FastAPI (Python 3.12) |
| DB | MySQL 8.4 (Docker) + SQLAlchemy 2 / PyMySQL. `DATABASE_URL` 이 없으면 SQLite 파일로 동작 |
| 인증 | Google OAuth 2.0 + 서명 세션 쿠키 |
| 캘린더 | Google Calendar API (REST) |
| 수집 | APScheduler + `crawler/runner.py` (requests · BeautifulSoup · curl_cffi) |
| AI 플래너 | OpenAI Responses API (프론트 `functions/` 에서 이전) |

크롤러·모델이 Python 이라 같은 언어로 import 해서 재사용하려고 FastAPI 를 선택했습니다.

## 로컬 실행 (Windows PowerShell)

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

python -m app.cli seed               # 데모 일정 6건 (프론트 mock 과 동일, 오늘 날짜 기준으로 이동)
uvicorn app.main:app --reload        # http://localhost:8000/docs 에서 API 확인
pytest                               # 테스트
```

### Docker (MySQL)

```powershell
docker compose up -d db              # MySQL 만 (localhost:3307) → 백엔드는 위처럼 uvicorn 으로 실행
docker compose up -d --build         # MySQL + 백엔드 컨테이너 (localhost:8000)
docker compose exec api python -m app.cli seed
docker compose down                  # 중지 (데이터는 볼륨에 남음, 완전히 지우려면 down -v)
```

- `.env` 의 `DATABASE_URL=mysql+pymysql://notiai:notiai@localhost:3307/notiai?charset=utf8mb4` (로컬 uvicorn 용). 컨테이너는 compose 가 `db:3306` 으로 덮어씀
- PC 에 설치된 MySQL 이 3306 을 쓰고 있어서 Docker MySQL 은 3307
- 테이블 구조는 **Alembic**(`migrations/`)으로 관리. 서버 시작 시 자동으로 `alembic upgrade head` 가 실행됨
  (Alembic 도입 전 `create_all` 로 만든 DB 는 데이터를 그대로 두고 `0001` 로 표시한 뒤 이어서 적용)
- 모델(`app/models.py`)을 바꿨으면:
  ```powershell
  alembic revision --autogenerate -m "무엇을 바꿨는지"   # migrations/versions/ 에 파일 생성 → 내용 꼭 확인
  alembic upgrade head                                  # 적용 (또는 서버 재시작)
  alembic downgrade -1                                  # 되돌리기
  ```
  마이그레이션을 빠뜨리면 `tests/test_api.py::test_migrations_match_models` 가 실패함
- MySQL 로 테스트: `docker exec notiai-mysql mysql -uroot -proot -e "CREATE DATABASE IF NOT EXISTS notiai_test; GRANT ALL ON notiai_test.* TO 'notiai'@'%';"` 후
  `$env:TEST_DATABASE_URL="mysql+pymysql://notiai:notiai@localhost:3307/notiai_test?charset=utf8mb4"; pytest`

### 환경변수 (`.env`)

| 이름 | 예시 / 기본값 | 설명 |
|---|---|---|
| `DATABASE_URL` | `mysql+pymysql://notiai:notiai@localhost:3307/notiai?charset=utf8mb4` | 없으면 SQLite 파일 |
| `MYSQL_DATABASE` / `MYSQL_USER` / `MYSQL_PASSWORD` / `MYSQL_ROOT_PASSWORD` / `MYSQL_PORT` | `notiai` / `notiai` / `notiai` / `root` / `3307` | docker-compose MySQL 설정 (바꾸면 `DATABASE_URL` 도 같이) |
| `SESSION_SECRET` | 긴 랜덤 문자열 | 세션 쿠키 서명 키 |
| `TOKEN_ENCRYPTION_KEY` | 비움 (→ `SESSION_SECRET` 에서 생성) | Google refresh token 암호화 키. ⚠️ 바꾸면 기존 사용자는 다시 로그인 필요 |
| `DEV_LOGIN` | `true` | 로그인 안 한 요청도 개발용 사용자로 처리 (배포 시 `false`) |
| `FRONTEND_URL` / `CORS_ORIGINS` | `http://localhost:5173` | 프론트 주소 |
| `COOKIE_SECURE` | `false` | 도메인이 다른 https 배포에서 `true` |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` / `GOOGLE_REDIRECT_URI` | | Google OAuth |
| `OPENAI_API_KEY` / `OPENAI_MODEL` | | AI 플래너, `EXTRACTOR=gpt` |
| `EXTRACTOR` / `MODEL_API_URL` / `MODEL_REPO_PATH` | `stub` | 일정 추출기 |
| `CRAWL_ENABLED` / `CRAWL_CRON` / `CRAWL_MAX_PAGES` | `false` / `0 9 * * *` / `2` | 정기 수집 |

전체 목록과 기본값은 `app/config.py`.

프론트 연결: 프론트 `.env.local` 에 `VITE_USE_MOCK=false`, `VITE_API_BASE_URL=http://localhost:8000`

`DEV_LOGIN=true`(기본값)이면 로그인 없이 '개발용 사용자'로 동작하므로 Google OAuth 설정 전에도 모든 화면을 연결해 볼 수 있습니다.

## API

| Method | 경로 | 설명 |
|---|---|---|
| GET | `/api/health` | 서버 · DB · 설정 상태 |
| GET | `/api/auth/google/login` | Google 로그인 시작 (프론트 로그인 버튼이 이 주소로 이동) |
| GET | `/api/auth/google/callback` | 로그인 완료 → `FRONTEND_URL/dashboard` 로 이동 |
| POST | `/api/auth/logout` | 로그아웃 |
| GET | `/api/user` | `{ name, email }` |
| GET / PUT | `/api/user/preferences` | `{ ai_mode, interests, enabled_sources, notifications, auto_mode_recommend }` |
| GET | `/api/events` | Event 배열 (끈 출처는 제외) |
| PUT | `/api/events/{id}/bookmark` | `{ bookmarked }` — **프론트 미정 API, 제안 형태** |
| GET | `/api/dashboard` | 수집 현황 (`mockDashboard.js` 형태) |
| POST | `/api/calendar/register` | `{ event_id }` → Google Calendar 등록 |
| DELETE | `/api/calendar/register/{id}` | 등록 해제 |
| POST | `/api/planner/chat` | `{ message, mode, history }` → `{ reply }` (사용자 일정 목록을 context 로 포함) |

에러는 모두 `{ "message": "..." }` 형태입니다. (프론트 `apiClient.js` 가 그대로 표시)

## 일정 추출기 (모델 교체 지점)

`EXTRACTOR` 환경변수로 선택 — 파이프라인·API 코드는 그대로입니다. (`app/services/extractor.py`)

| 값 | 동작 | 용도 |
|---|---|---|
| `stub` | 제목만 저장, 전부 `needs_review` | 키 없이 흐름 테스트 |
| `gpt` | OpenAI 로 5필드 추출. `MODEL_REPO_PATH` 가 있으면 model repo 의 `SYSTEM_PROMPT` · `postprocess` 재사용 | 모델 완성 전 임시 |
| `model_api` | `MODEL_API_URL` 로 POST (`ModelApiExtractor` docstring 참고) | 모델 팀 추론 서버 완성 후 |

`review_status`: 마감일 없음 · 날짜 역전이면 `needs_review` (model repo `cascade_infer.py` 기본 규칙). 모델 서버가 `auto_register_status` 를 주면 그 값을 우선합니다.

## 수집 파이프라인

```powershell
python -m app.cli import path\to\crawled_all.jsonl   # 크롤 결과 파일 → notices (source_url 중복 제외)
python -m app.cli extract --limit 20                 # 미추출 notices → events
python -m app.cli collect [--site cbnu]              # 크롤러 실행 + import + extract (결과: data/crawled_all.jsonl)
```

`CRAWL_ENABLED=true` 면 서버 실행 중 `CRAWL_CRON`(기본 매일 09:00, Asia/Seoul)마다 `collect` 가 실행됩니다.
`category` 는 모델이 아니라 수집한 게시판(크롤러 레코드의 `board`)으로 정합니다. (`app/services/category.py`)
CBNU 는 `sw_notice`/`scholarship`/`employment` → 학사/장학/취업, Wevity·ContestKorea 는 기본 공모전이고
크롤러에 다른 목록을 추가할 때 `board` 에 분야 id(예: `"activity"`)를 넣으면 그대로 사용합니다.

## 폴더 구조

```
app/
  main.py            앱 생성 · CORS · 세션 · 에러 형식 · 라우터 등록
  config.py          환경변수
  db.py, models.py   DB 연결 · 마이그레이션 실행 / 테이블 (users, preferences, notices, events, user_events, crawl_runs)
  crypto.py          refresh token 암호화 (Fernet)
  schemas.py         요청·응답 형태 (프론트 계약)
  deps.py            로그인 사용자 (DEV_LOGIN)
  routers/           auth, users, events, dashboard, calendar, planner, health
  services/          extractor, pipeline, category, events, preferences, dashboard, google, planner
  scheduler.py       정기 수집
  seed.py, cli.py    데모 데이터 / 관리 명령어
crawler/             크롤러 (cbnu, wevity, contestkorea, runner)
migrations/          Alembic 마이그레이션
tests/               pytest
```

## 배포 시 체크

- `DEV_LOGIN=false`, `SESSION_SECRET` 랜덤 값, `TOKEN_ENCRYPTION_KEY` 별도 키 권장, `DATABASE_URL` 운영 MySQL
- 프론트(`notiai.pages.dev`)와 도메인이 다르면 `COOKIE_SECURE=true` (https 필수, `SameSite=None; Secure`) + `CORS_ORIGINS` 에 프론트 주소
  - 브라우저의 서드파티 쿠키 차단을 피하려면 같은 사이트 아래(예: 프론트 `notiai.example.com`, API `api.notiai.example.com`)에 두는 것이 가장 안전
- Google Cloud Console: OAuth 동의 화면에 `calendar.events` 범위 추가, 승인된 리디렉션 URI 에 `GOOGLE_REDIRECT_URI` 등록

## 남은 작업

- [x] slim-v9 모델 서버 연결과 사용자 확인·수정 등록 (아래 계약)
- [ ] 7일 브리핑 / AI 액션 제안 API (프론트는 아직 mock)
- [ ] 알림(D-7, D-3, 새 일정)
- [ ] AI 플래너 최종 다중 프롬프트 모드 → `app/services/planner.py` `MODE_PROMPTS` 교체
- [ ] 대외활동 큐레이션 `/api/curation` (부트캠프 서비스 연동)

## slim-v9 서비스 계약

`EXTRACTOR=model_api`, `MODEL_API_URL=<모델 서버>/extract`, `MODEL_API_TOKEN`을 로컬 환경에 설정한다.
토큰은 `X-Model-Token` 헤더로만 전송하며 리다이렉트를 따라가지 않는다.
요청은 `title`, `body`, `user_content`, `reference_time`이다. `reference_time`은
`Notice.published_at`의 ISO 게시일이며 사이트에서 얻지 못한 값은 null이다. 수집 시각을 게시일로 대신 쓰지 않는다.
크롤러의 `meta`는 `source_metadata`에 보존하고, 명시된 접수기간의 종료 날짜를 모델 마감일과 대조한다.
불일치는 모델 값을 바꾸지 않고 `review_reason`/`review_required`의 원문 확인 표시로 전달한다.

저장 유형은 `application/submission/event/interview`이며 공지 1:N 일정 구조를 사용한다.
유효한 모델 결과는 `review_status=auto`, `ai_extracted=true`로 공개한다. 모델의 원문 확인 사유도 보존한다.
일정 없는 성공은 `no_events`, 무효 응답은 `failed`로 기록하고 공개하지 않는다.
연결·시간 초과·429·5xx는 `retry_pending`으로 두고 다음 `python -m app.cli extract`에서 재시도한다.
`EXTRACTION_MAX_ATTEMPTS`는 전체 시도 횟수 상한(기본 3)이며 소진 시 `failed`이다.
결과 발표·시상식·선택 설문·시스템 중단 안내는 서비스에서 제외한다.

기존 일정 응답에 `ai_extracted`, `review_required`, `action_status`, `dismissed`, `user_modified`가 추가된다.
날짜·시각·장소 응답에는 해당 사용자 수정 값이 우선 적용되며 공유 Event 원본은 보존된다.
모든 사용자 API는 로그인 세션으로 사용자를 결정하며 다른 사용자의 ID를 받지 않는다.

| API | 요청/동작 |
| --- | --- |
| `GET /api/events?include_dismissed=true` | 기본은 관심 없음 제외; true이면 포함 |
| `GET /api/events/{id}` | 공지의 공개된 전체 일정과 dismissed |
| `POST /api/calendar/register` | event_id, confirmed=true, overrides(일정 ID별 수정 객체); 면접 제외한 공지 묶음 등록 |
| `POST /api/calendar/interviews/{id}` | confirmed=true, overrides; 면접 개별 추가 |
| `PATCH /api/events/{id}/overrides` | start_date/end_date/start_time/end_time/location; 생략=유지, null=원본 복원, 빈 문자열=비움 |
| `PUT /api/events/{id}/action-status` | action_status: pending 또는 done |
| `PUT /api/events/{id}/dismissed` | dismissed: true/false; 사용자×공지 저장 |
| `POST /api/events/{id}/reports` | reason: date/time/location/nonexistent/other, 선택 memo(최대 2000자); 저장만 수행 |
| `GET /api/internal/planner/facts[/{id}]` | 로그인 사용자의 사실 계산; GPT 호출 없음 |

등록 예시:

```json
{"event_id":"8","confirmed":true,"overrides":{"8":{"end_date":"2026-10-22","end_time":"18:00","location":""}}}
```

등록은 전체 묶음의 수정 값을 검증한 뒤 수행한다. Google에는 사용자 수정 값을 보낸다.
Google 일부 등록 실패 시 성공한 일정은 유지하고 재시도 시 중복 등록하지 않는다.
등록 이후 PATCH 수정은 `needs_sync`이며 `POST /api/calendar/sync/{id}`로 수정 값을 Google에 반영한다.
접수 기간에 마감 시각만 있으면 그 종료일의 마감 시각을 1분 일정으로 표시한다.

사실 API는 수정 값, 남은 날짜, 접수 가능 여부, 만료, 관심/등록/완료/관심 없음,
등록한 다른 일정과 겹치는 ID, 원문 확인 여부를 계산한다. 날짜 계산은 Asia/Seoul 기준이다.
접수 시작일·마감일이 없으면 접수 가능 여부는 null이며, 검증할 수 없는 충돌도 null이다.
충돌은 NotiAI에 등록한 사용자 일정 범위이고 Google 캘린더의 외부 일정을 조회하지 않는다.
`app/services/event_types.py`의 `to_planner_event_type`은 향후 팀 계약 변환용으로만 정의되어 있다.
application/submission→application, event→main_event, interview→other, 미지정→unknown; 현재 어떤 실행 경로에도 연결하지 않는다.
관리자 API는 내부용으로 유지한다. `GET /api/admin/notices?state=reported`로 신고 공지,
`state=failed`/`retry_pending`으로 실패를 조회한다. 검수 메뉴는 일반 서비스 화면에서 제거했다.

마이그레이션 `0004_service_v9.py`는 게시일·재시도·AI 표시·사용자 수정/완료·관심 없음·신고 구조를 추가한다.
작업 전에 DB를 백업하고 `alembic upgrade head`를 실행한다. 되돌리기는 `alembic downgrade 0003`이다.
되돌리면 새 사용자 상태/신고/수정 컬럼은 제거되며 기존 공지·일정·사용자 연결 행은 유지된다.
새 상태까지 복구하려면 작업 전 백업을 사용한다. 원본 DB 대신 복제 DB로 검증할 수 있다.
