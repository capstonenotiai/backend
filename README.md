# NotiAI — Backend

> 다중 프롬프트 기반 AI 대학생 일정 자동 생성 시스템 · 충북대학교 소프트웨어학부 졸업작품

- 프론트엔드: [jaeyeongt/NotiAi](https://github.com/jaeyeongt/NotiAi) — API 계약은 프론트 `src/services/*.js` 기준
- 모델 · 크롤러: [capstonenotiai/model](https://github.com/capstonenotiai/model)

```
[스케줄러] model repo 크롤러 → notices(원문) → Extractor(모델) → events
[React] ──쿠키 세션──▶ [FastAPI] ── DB / Google Calendar / OpenAI(AI 플래너)
```

## 기술 스택

| 구분 | 사용 |
|---|---|
| 서버 | FastAPI (Python 3.12) |
| DB | MySQL 8.4 (Docker) + SQLAlchemy 2 / PyMySQL. `DATABASE_URL` 이 없으면 SQLite 파일로 동작 |
| 인증 | Google OAuth 2.0 + 서명 세션 쿠키 |
| 캘린더 | Google Calendar API (REST) |
| 수집 | APScheduler + model repo `crawler/runner.py` |
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
- 테이블은 서버 시작 시 자동 생성(`create_all`). 단, **이미 있는 테이블의 컬럼 변경은 반영되지 않음** → 모델을 바꾸면 개발 중에는 `docker compose down -v` 후 다시 seed, 운영 단계에서는 Alembic 도입
- MySQL 로 테스트: `docker exec notiai-mysql mysql -uroot -proot -e "CREATE DATABASE IF NOT EXISTS notiai_test; GRANT ALL ON notiai_test.* TO 'notiai'@'%';"` 후
  `$env:TEST_DATABASE_URL="mysql+pymysql://notiai:notiai@localhost:3307/notiai_test?charset=utf8mb4"; pytest`

### 환경변수 (`.env`)

| 이름 | 예시 / 기본값 | 설명 |
|---|---|---|
| `DATABASE_URL` | `mysql+pymysql://notiai:notiai@localhost:3307/notiai?charset=utf8mb4` | 없으면 SQLite 파일 |
| `MYSQL_DATABASE` / `MYSQL_USER` / `MYSQL_PASSWORD` / `MYSQL_ROOT_PASSWORD` / `MYSQL_PORT` | `notiai` / `notiai` / `notiai` / `root` / `3307` | docker-compose MySQL 설정 (바꾸면 `DATABASE_URL` 도 같이) |
| `SESSION_SECRET` | 긴 랜덤 문자열 | 세션 쿠키 서명 키 |
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
python -m app.cli collect                            # 크롤러 실행 + import + extract (MODEL_REPO_PATH 필요)
```

`CRAWL_ENABLED=true` 면 서버 실행 중 `CRAWL_CRON`(기본 매일 09:00, Asia/Seoul)마다 `collect` 가 실행됩니다.
`category` 는 모델이 뽑지 않으므로 `app/services/category.py` 규칙(CBNU 게시판 → 분야, 제목 키워드)으로 정합니다.

## 폴더 구조

```
app/
  main.py            앱 생성 · CORS · 세션 · 에러 형식 · 라우터 등록
  config.py          환경변수
  db.py, models.py   DB 연결 / 테이블 (users, preferences, notices, events, user_events, crawl_runs)
  schemas.py         요청·응답 형태 (프론트 계약)
  deps.py            로그인 사용자 (DEV_LOGIN)
  routers/           auth, users, events, dashboard, calendar, planner, health
  services/          extractor, pipeline, category, events, preferences, dashboard, google, planner
  scheduler.py       정기 수집
  seed.py, cli.py    데모 데이터 / 관리 명령어
tests/               pytest
```

## 배포 시 체크

- `DEV_LOGIN=false`, `SESSION_SECRET` 랜덤 값, `DATABASE_URL` PostgreSQL
- 프론트(`notiai.pages.dev`)와 도메인이 다르면 `COOKIE_SECURE=true` (https 필수, `SameSite=None; Secure`) + `CORS_ORIGINS` 에 프론트 주소
  - 브라우저의 서드파티 쿠키 차단을 피하려면 같은 사이트 아래(예: 프론트 `notiai.example.com`, API `api.notiai.example.com`)에 두는 것이 가장 안전
- Google Cloud Console: OAuth 동의 화면에 `calendar.events` 범위 추가, 승인된 리디렉션 URI 에 `GOOGLE_REDIRECT_URI` 등록
- `google_refresh_token` 은 현재 평문 저장 — 실서비스라면 암호화

## 남은 작업

- [ ] 모델 팀과 추론 서버 입출력 확정 → `ModelApiExtractor`
- [ ] 프론트: 로그인 버튼 → `/api/auth/google/login`, 북마크 API 연결, `needs_review` 검토 UI
- [ ] 7일 브리핑 / AI 액션 제안 API (프론트는 아직 mock)
- [ ] 알림(D-7, D-3, 새 일정)
- [ ] AI 플래너 최종 다중 프롬프트 모드 → `app/services/planner.py` `MODE_PROMPTS` 교체
- [ ] 대외활동 큐레이션 `/api/curation` (부트캠프 서비스 연동)
