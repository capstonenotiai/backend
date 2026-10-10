# 백엔드 API 동결 계약

기준: feature/service-v9의 현재 라우터 39개, TASK_B12. `/api` 접두사를 포함한 경로이며 `/api/dashboard`에는 `/summary`가 없다.
프론트는 `credentials: 'include'`로 세션 쿠키 `notiai_session`을 보낸다. 로그인 필수 API는 미로그인 시 401 `{"message":"로그인이 필요합니다."}`.
DEV_LOGIN=true에서는 일반 사용자 API를 개발용 사용자로 호출할 수 있지만 관리자는 실제 세션과 관리자 허용 이메일이 필요하다.
관리자 변경 요청은 허용된 Origin도 필요하다. 관리자 공통 오류: 401 `{"message":"관리자 로그인이 필요합니다."}`, 403 `{"message":"관리자 권한이 필요합니다."}` 또는 `{"message":"허용되지 않은 요청 출처입니다."}`.

요청은 JSON이다. 아래 예시는 임시 SQLite의 테스트 데이터를 실제 API로 호출한 응답이다. 추천·채팅은 가짜 GPT, 캘린더는 개발 모드, 관리자 예시는 테스트에서 관리자 의존성을 주입했다. 외부 서비스는 호출하지 않았다.
배열 예시는 설명을 위해 첫 항목 하나만 보인 경우가 있으며, 빈 배열도 정상이다. ID와 시각은 예시 값이다. 날짜는 YYYY-MM-DD, 시각은 HH:MM, 미확인 일정 날짜·시각은 빈 문자열이 가능하다.
일정 ID는 외부 응답에서 문자열, notice_id는 정수 또는 null, 추천은 `o<notice_id>`/`e<event_id>`다. 추천의 `days_until_deadline`은 이름과 달리 focus 일정의 행동일까지 남은 일수다(참석은 시작일).

## 오류 계약

HTTPException·플래너 오류·예상 못 한 오류는 `{message}`. Pydantic 입력 검증 오류는 **400 `{message, errors}`**이며 OpenAPI의 자동 422 표기와 실제 상태가 다르다.
예: `{"message":"요청 형식이 올바르지 않습니다.","errors":[{"type":"greater_than_equal","loc":["body","grade"],"msg":"Input should be greater than or equal to 1","input":0,"ctx":{"ge":1}}]}`.
공통 500: `{"message":"서버 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."}`. 다음 절에서는 각 경로의 주요 추가 오류를 기재한다.

## 값 목록과 화면 표시 제안

한국어 라벨은 프론트 제안이며 API가 라벨 필드를 반환하는 것은 아니다.

| 값 종류 | 코드 → 한국어 제안 |
|---|---|
| 추천/설정 ai_mode | priority → 우선순위; discover → 새 기회 탐색; focus → 집중할 활동 |
| chat mode | general → 내 일정 질문(기본); priority → 우선순위; discover → 활동 탐색; focus → 집중할 활동 고르기. 호환 별칭 explorer → discover, study·balanced → general; 모르는 값은 general |
| source | cbnu → CBNU 포털; wevity → Wevity; contestkorea → ContestKorea |
| 관심분야 category/id | scholarship → 장학; academic → 학사; career → 취업/인턴; contest → 공모전; activity → 대외활동 |
| 서비스 event_type | application → 접수; submission → 제출/납부; event → 본행사; interview → 면접; orientation → 발대식/OT; result → 결과 발표(비공개); service_change → 시스템/시설 안내(비공개) |
| 내부 플래너 event_type | application → 접수; main_event → 본행사; other → 기타; unknown → 미확인 (서비스 유형과 다름) |
| action_type | apply → 신청; submit → 제출; attend → 참석; check → 확인; other → 기타; unknown → 미확인 |
| action_status | pending → 진행 전; done → 완료 |
| enrollment_status | enrolled → 재학; expected_graduation → 졸업 예정; leave → 휴학; graduated → 졸업; unknown → 미입력 |
| 알림 kind | deadline_d3 → 마감 3일 전; deadline_d1 → 마감 1일 전; event_d3 → 일정 3일 전; event_d1 → 일정 1일 전 |
| next_step_label / next_step_type | act → 지금 진행; prepare → 미리 준비; verify → 확인 필요; monitor → 추후 확인 |
| grade(추천 등급) | strong_match → 적극 추천; potential_match → 검토 추천; not_recommended → 추천 안 함 |
| tier | core → 핵심; secondary → 후순위 |
| secondary_reason | stronger_alternative → 더 적합한 대안; schedule_conflict → 일정 중복; information_uncertain → 정보 확인 필요; null → 해당 없음 |
| eligibility | eligible → 지원 가능; ineligible → 조건 미충족; needs_check → 조건 확인 필요; unknown → 자격 미확인 |
| verify_target | date → 공지 날짜; grouping → 활동 묶음; action_window → 진행 시점; application_confirmation → 신청 완료 여부; null → 해당 없음 |
| review_status | auto → 자동 공개; approved → 검토 완료; needs_review → 검토 대기; rejected → 제외 |
| attendance_mode | offline → 현장; online → 온라인; hybrid → 혼합; unknown → 미확인; not_applicable → 해당 없음 |
| schedule_status | confirmed → 확정; tentative → 잠정; cancelled → 취소; unknown → 미확인 |
| sync_status | none → 미등록; synced → 반영 완료; needs_sync → 갱신 필요; failed → 반영 실패 |
| action_window / urgency | open → 진행 가능; not_open → 진행 전; unknown → 시점 미확인 / urgent → 긴급; soon → 임박; upcoming → 예정; later → 여유; none → 날짜 미확인 |

Preferences는 관심분야·출처 키를 위 목록으로 제한하지 않는다. ai_mode는 priority/discover/focus만 저장하고 알 수 없는 값은 priority로 복원하고 interests 중복은 제거한다.
설정을 처음 만들 때의 기본값은 ai_mode=priority, interests=[], enabled_sources={cbnu:true,wevity:true,contestkorea:true}, notifications={d3:true,d1:true,email:true}, auto_mode_recommend=true다.
설정 PUT는 요청에 들어온 필드만 바꾸며 notifications는 들어온 키만 기존 값에 병합한다. 빈 본문 객체는 기존 값을 유지한다. enabled_sources를 보내면 그 dict 전체를 교체한다. 응답은 항상 전체 Preferences다. 프로필 PUT는 기존대로 생략 시 major="", grade=null, enrollment_status="unknown"이다. name/email은 프로필 PUT으로 수정할 수 없다.
알림은 D-3/D-1 당일 09:00 Asia/Seoul에 생성한다. 확인 필요 일정은 기본 제외한다. 이메일 수신을 꺼도 알림함은 유지된다.
실제 기본 스케줄은 `0 9-23 * * *`: 09시에 생성하고 10~23시에도 미생성분·이메일 재시도를 처리한다. 서버가 09시에 꺼져 있으면 당일 다음 실행에서 채우며 같은 알림은 중복 생성하지 않는다. NOTIFY_ENABLED를 켜야 스케줄이 실행된다.
캘린더 묶음 등록은 면접을 제외하고, 면접은 별도 API로 추가한다. 묶음 해제에서도 면접은 유지된다. Google 일부 실패 시 이미 성공한 형제 일정은 유지될 수 있다.
목록은 공지마다 가장 가까운 미마감 접수 하나, 접수가 없으면 본행사 하나다. 접수가 모두 마감되면 목록에서 제외하지만 등록된 캘린더 일정은 유지한다. 상세는 공개된 형제 일정이며 결과 발표·시스템 안내 등 서비스 제외 일정은 포함하지 않는다.
`ai_extracted`는 카드에 AI 표시를 하라는 뜻이 아니다. `review_required`일 때만 등록 확인을 유도하며, 날짜 등 개인 수정은 `/overrides`로 저장한다.
추천 응답은 서버 사실과 모드 판단을 합친다. fact_refs는 해당 활동의 검증 Fact ID 목록, corrections는 서버 보정 코드 목록, needs_grouping_check는 별도 확인할 `{opportunity_id,title}` 목록이다. corrections는 사용자 화면에 코드 그대로 노출하지 않는다. 캐시는 사용자·모드·입력·프롬프트 버전별 30분이다. refresh=true는 캐시를 건너뛰고 성공한 결과로 교체한다. summary는 캐시 응답에도 포함되는 서버 생성 한 문장으로, 추천 개수와 행동일까지 0~3일 남은 항목 개수를 설명한다.
현재 calendar_conflict는 사용자가 서비스에 등록한 일정 간 비교다. Google 캘린더의 외부 일정을 조회하지 않는다. new_to_user는 null 임시값이다.


## 인증


### `GET /api/auth/google/login`

로그인: 불필요.

307 Location으로 Google 동의 화면 이동. 개발 모드·Google 미설정이면 프론트 /dashboard 이동. JSON 응답 없음.

요청: 본문·쿼리 없음.


주요 오류: 500 `{"message": "Google 로그인 설정이 완료되지 않았습니다."}`


성공 307:

본문 없음. Location: `http://localhost:5173/dashboard` (테스트 예시).


### `GET /api/auth/google/callback`

로그인: 불필요.

성공 307 /dashboard, 실패 307 /?login=failed. state 검증 및 이메일 검증 후 세션 발급. JSON 응답 없음.

요청 필드:

| 필드 | 타입 | 필수 여부 | 허용값·기본값·제한 |
|---|---|---|---|
| `code` (query) | string / null | 선택 | 해당 타입; 해당 타입 |
| `state` (query) | string / null | 선택 | 해당 타입; 해당 타입 |

입력 타입·범위 위반: 공통 400 `{message, errors}`(플래너는 직접 검증하므로 `{message}`).


주요 오류: 공통 오류 참조.


성공 307:

본문 없음. Location: `http://localhost:5173/dashboard` (테스트의 개발 모드 예시).


### `POST /api/auth/logout`

로그인: 불필요.

204 본문 없음.

요청: 본문·쿼리 없음.


주요 오류: 공통 오류 참조.


성공 204:

본문 없음.


## 사용자·설정·프로필


### `GET /api/user`

로그인: 필수.

요청: 본문·쿼리 없음.


주요 오류: 401 `{"message": "로그인이 필요합니다."}`


성공 200:

```json
{
  "major": "",
  "grade": null,
  "enrollment_status": "unknown",
  "name": "개발용 사용자",
  "email": "dev@notiai.local",
  "is_admin": false,
  "onboarding_done": false
}
```

### `PUT /api/user/profile`

로그인: 필수.

grade는 정수 1~6 또는 null(문자열·bool 불가), major는 엄격 문자열 최대 255자. 추가 필드 금지.

요청 필드:

| 필드 | 타입 | 필수 여부 | 허용값·기본값·제한 |
|---|---|---|---|
| `major` | string | 선택 | maxLength=255; default="" |
| `grade` | integer / null | 선택 | minimum=1.0; maximum=6.0; 해당 타입 |
| `enrollment_status` | string | 선택 | enum=["enrolled", "expected_graduation", "leave", "graduated", "unknown"]; default="unknown" |

입력 타입·범위 위반: 공통 400 `{message, errors}`(플래너는 직접 검증하므로 `{message}`).


주요 오류: 401 `{"message": "로그인이 필요합니다."}`


성공 200:

```json
{
  "major": "소프트웨어학부",
  "grade": 3,
  "enrollment_status": "enrolled",
  "name": "개발용 사용자",
  "email": "dev@notiai.local",
  "is_admin": false,
  "onboarding_done": false
}
```

### `GET /api/user/preferences`

로그인: 필수.

요청: 본문·쿼리 없음.


주요 오류: 401 `{"message": "로그인이 필요합니다."}`


성공 200:

```json
{
  "ai_mode": "priority",
  "interests": [
    "academic"
  ],
  "enabled_sources": {
    "cbnu": true,
    "wevity": true,
    "contestkorea": true
  },
  "notifications": {
    "d3": true,
    "d1": true,
    "email": true
  },
  "auto_mode_recommend": true
}
```

### `PUT /api/user/preferences`

로그인: 필수.

요청 필드:

| 필드 | 타입 | 필수 여부 | 허용값·기본값·제한 |
|---|---|---|---|
| `ai_mode` | string | 선택 | default="priority" |
| `interests` | array<string> | 선택 | 해당 타입 |
| `enabled_sources` | object<string, boolean> | 선택 | 해당 타입 |
| `notifications` | object | 선택 | 해당 타입 |
| `notifications.d3` | boolean | 선택 | default=true |
| `notifications.d1` | boolean | 선택 | default=true |
| `notifications.email` | boolean | 선택 | default=true |
| `auto_mode_recommend` | boolean | 선택 | default=true |

입력 타입·범위 위반: 공통 400 `{message, errors}`(플래너는 직접 검증하므로 `{message}`).


주요 오류: 401 `{"message": "로그인이 필요합니다."}`


성공 200:

```json
{
  "ai_mode": "priority",
  "interests": [
    "academic"
  ],
  "enabled_sources": {
    "cbnu": true,
    "wevity": true,
    "contestkorea": true
  },
  "notifications": {
    "d3": true,
    "d1": true,
    "email": true
  },
  "auto_mode_recommend": true
}
```

### `PUT /api/user/onboarding`

로그인: 필수. 본문 `{ "done": true }` 또는 `{ "done": false }` (엄격한 boolean, 필수).
성공 200: `GET /api/user`와 같은 전체 Profile, 갱신된 `onboarding_done` 포함.
신규 사용자는 false. 0010 마이그레이션에서 기존 전공이 공백 외 문자열이거나 관심분야가 비어 있지 않으면 true로 백필한다.
입력 검증 오류 400 `{message, errors}`, 미로그인 401 `{message}`.

### `DELETE /api/users/me`

로그인: 필수. 본문·쿼리 없음. 성공 204, 본문 없음. 미로그인 401 `{message}`.
refresh token이 있으면 Google 철회를 먼저 요청하고, 실패해도 경고만 남긴 뒤 탈퇴를 진행한다.
사용자·설정·일정 상태·공지 상태·알림·신고·서비스 의견을 명시적으로 삭제한다. 검토 기록은 유지하며 actor_id만 null로 바꾼다.
세션과 쿠키는 `/api/auth/logout`과 같은 방식으로 종료한다.
**이미 등록된 Google 캘린더 일정은 삭제하지 않는다. 탈퇴 후 Google 캘린더에서 직접 지울 수 있다.**
0010 downgrade는 익명 검토 기록(actor_id=null)이 있으면 기록을 보존하기 위해 변경 전에 중단한다. 이전 버전의 NOT NULL 제약으로 표현할 수 없기 때문이다.

## 서비스 의견·내 신고 내역

### `POST /api/feedback`

로그인: 필수. 본문 `{ "type": "suggestion", "message": "의견 내용", "reply_email": "reply@example.com" }`.
type: inconvenience / suggestion / praise / other. message: 문자열, 양끝 공백 제거 후 1~2000자.
reply_email: 선택 문자열/null, 양끝 공백 제거 후 `@` 포함 및 255자 이하. 빈 문자열은 null로 저장한다.
성공 201: `{ "id": 1, "created_at": "2026-10-11T00:04:00+00:00" }` (ISO 8601).
잘못된 입력은 400 `{message, errors}`, 미로그인은 401 `{message}`. 의견·이메일 원문은 로그에 남기지 않는다.

### `GET /api/reports/mine`

로그인: 필수. 본문·쿼리 없음. 로그인 사용자 신고만 created_at 내림차순, 동률이면 id 내림차순.
성공 200: 배열 (없으면 `[]`). 예:

```json
[{"id": 1, "event_id": 12, "title": "행사 제목", "reason": "date", "memo": "날짜 확인", "status": "received", "created_at": "2026-10-11T00:04:00+00:00"}]
```

title은 이벤트가 없으면 null. reason·memo는 기존 신고 필드 그대로다. 현재 모델에는 처리 여부 필드가 없어 status는 모두 received다(관리자 검토 상태로 추측하지 않는다). 미로그인 401 `{message}`.

## 일정


### `GET /api/events`

로그인: 필수.

include_dismissed=false가 기본이며 관심 없음 활동을 숨긴다.

요청 필드:

| 필드 | 타입 | 필수 여부 | 허용값·기본값·제한 |
|---|---|---|---|
| `include_dismissed` (query) | boolean | 선택 | default=false |

입력 타입·범위 위반: 공통 400 `{message, errors}`(플래너는 직접 검증하므로 `{message}`).


주요 오류: 401 `{"message": "로그인이 필요합니다."}`


성공 200:

```json
[
  {
    "id": "1",
    "title": "일정",
    "start_date": "2030-10-08",
    "end_date": "2030-10-08",
    "location": "",
    "detail": "",
    "source": "cbnu",
    "source_url": "https://example.com/event",
    "category": "academic",
    "is_new": true,
    "registered": false,
    "bookmarked": false,
    "collected_at": "2026-10-08T03:20:37+09:00",
    "review_status": "auto",
    "review_reason": null,
    "notice_id": 1,
    "event_type": "event",
    "start_time": "13:00",
    "end_time": "14:00",
    "timezone": "Asia/Seoul",
    "attendance_mode": "unknown",
    "schedule_status": "confirmed",
    "revision": 1,
    "can_register": true,
    "registration_reason": null,
    "sync_status": "none",
    "ai_extracted": false,
    "review_required": false,
    "action_status": "pending",
    "dismissed": false,
    "user_modified": false
  }
]
```

### `GET /api/events/{event_id}`

로그인: 필수.

응답은 단일 Event가 아닌 {notice_id, dismissed, events:[EventOut]} 공지 묶음이다.

요청 필드:

| 필드 | 타입 | 필수 여부 | 허용값·기본값·제한 |
|---|---|---|---|
| `event_id` (path) | string | 필수 | 해당 타입 |

입력 타입·범위 위반: 공통 400 `{message, errors}`(플래너는 직접 검증하므로 `{message}`).


주요 오류: 401 `{"message": "로그인이 필요합니다."}`; 404 `{"message": "일정을 찾을 수 없습니다."}`; 404 `{"message": "공개된 일정을 찾을 수 없습니다."}`


성공 200:

```json
{
  "notice_id": 1,
  "dismissed": false,
  "events": [
    {
      "id": "1",
      "title": "일정",
      "start_date": "2030-10-08",
      "end_date": "2030-10-08",
      "location": "",
      "detail": "",
      "source": "cbnu",
      "source_url": "https://example.com/event",
      "category": "academic",
      "is_new": true,
      "registered": false,
      "bookmarked": false,
      "collected_at": "2026-10-08T03:20:37+09:00",
      "review_status": "auto",
      "review_reason": null,
      "notice_id": 1,
      "event_type": "event",
      "start_time": "13:00",
      "end_time": "14:00",
      "timezone": "Asia/Seoul",
      "attendance_mode": "unknown",
      "schedule_status": "confirmed",
      "revision": 1,
      "can_register": true,
      "registration_reason": null,
      "sync_status": "none",
      "ai_extracted": false,
      "review_required": false,
      "action_status": "pending",
      "dismissed": false,
      "user_modified": false
    },
    {
      "id": "2",
      "title": "면접",
      "start_date": "2030-10-08",
      "end_date": "2030-10-08",
      "location": "",
      "detail": "",
      "source": "cbnu",
      "source_url": "https://example.com/event",
      "category": "academic",
      "is_new": true,
      "registered": false,
      "bookmarked": false,
      "collected_at": "2026-10-08T03:20:37+09:00",
      "review_status": "auto",
      "review_reason": null,
      "notice_id": 1,
      "event_type": "interview",
      "start_time": "13:00",
      "end_time": "14:00",
      "timezone": "Asia/Seoul",
      "attendance_mode": "unknown",
      "schedule_status": "confirmed",
      "revision": 1,
      "can_register": true,
      "registration_reason": null,
      "sync_status": "none",
      "ai_extracted": false,
      "review_required": false,
      "action_status": "pending",
      "dismissed": false,
      "user_modified": false
    }
  ]
}
```

### `PUT /api/events/{event_id}/bookmark`

로그인: 필수.

공지의 auto/approved 형제 일정에 관심 상태를 함께 저장한다(결과 발표 제외).

요청 필드:

| 필드 | 타입 | 필수 여부 | 허용값·기본값·제한 |
|---|---|---|---|
| `event_id` (path) | string | 필수 | 해당 타입 |
| `bookmarked` | boolean | 필수 | 해당 타입 |

입력 타입·범위 위반: 공통 400 `{message, errors}`(플래너는 직접 검증하므로 `{message}`).


주요 오류: 401 `{"message": "로그인이 필요합니다."}`; 404 `{"message": "일정을 찾을 수 없습니다."}`


성공 200:

```json
{
  "id": "1",
  "bookmarked": true
}
```

### `PATCH /api/events/{event_id}/overrides`

로그인: 필수.

개인 일정 수정. null은 해당 수정값을 지워 원본으로 복원한다. 등록된 일정은 needs_sync가 된다.

날짜는 YYYY-MM-DD, 시각은 HH:MM 또는 빈 문자열. 시작일≤종료일·같은 날 시작 시각≤종료 시각이며 시각에는 해당 날짜가 필요하다. 추가 키 금지.

요청 필드:

| 필드 | 타입 | 필수 여부 | 허용값·기본값·제한 |
|---|---|---|---|
| `event_id` (path) | string | 필수 | 해당 타입 |
| `start_date` | string / null | 선택 | 해당 타입; 해당 타입 |
| `end_date` | string / null | 선택 | 해당 타입; 해당 타입 |
| `start_time` | string / null | 선택 | 해당 타입; 해당 타입 |
| `end_time` | string / null | 선택 | 해당 타입; 해당 타입 |
| `location` | string / null | 선택 | maxLength=255; 해당 타입 |

입력 타입·범위 위반: 공통 400 `{message, errors}`(플래너는 직접 검증하므로 `{message}`).


주요 오류: 401 `{"message": "로그인이 필요합니다."}`; 404 `{"message": "일정을 찾을 수 없습니다."}`; 400 `{"message": "날짜·시각·장소 수정 값을 확인해 주세요."}`


성공 200:

```json
{
  "id": "1",
  "title": "일정",
  "start_date": "2030-10-08",
  "end_date": "2030-10-08",
  "location": "강의실",
  "detail": "",
  "source": "cbnu",
  "source_url": "https://example.com/event",
  "category": "academic",
  "is_new": true,
  "registered": false,
  "bookmarked": true,
  "collected_at": "2026-10-08T03:20:37+09:00",
  "review_status": "auto",
  "review_reason": null,
  "notice_id": 1,
  "event_type": "event",
  "start_time": "13:00",
  "end_time": "14:00",
  "timezone": "Asia/Seoul",
  "attendance_mode": "unknown",
  "schedule_status": "confirmed",
  "revision": 1,
  "can_register": true,
  "registration_reason": null,
  "sync_status": "none",
  "ai_extracted": false,
  "review_required": false,
  "action_status": "pending",
  "dismissed": false,
  "user_modified": true
}
```

### `PUT /api/events/{event_id}/action-status`

로그인: 필수.

요청 필드:

| 필드 | 타입 | 필수 여부 | 허용값·기본값·제한 |
|---|---|---|---|
| `event_id` (path) | string | 필수 | 해당 타입 |
| `action_status` | string | 필수 | enum=["pending", "done"] |

입력 타입·범위 위반: 공통 400 `{message, errors}`(플래너는 직접 검증하므로 `{message}`).


주요 오류: 401 `{"message": "로그인이 필요합니다."}`; 404 `{"message": "일정을 찾을 수 없습니다."}`


성공 200:

```json
{
  "id": "1",
  "action_status": "pending"
}
```

### `POST /api/events/{event_id}/reports`

로그인: 필수.

요청 필드:

| 필드 | 타입 | 필수 여부 | 허용값·기본값·제한 |
|---|---|---|---|
| `event_id` (path) | string | 필수 | 해당 타입 |
| `reason` | string | 필수 | enum=["date", "time", "location", "nonexistent", "other"] |
| `memo` | string | 선택 | maxLength=2000; default="" |

입력 타입·범위 위반: 공통 400 `{message, errors}`(플래너는 직접 검증하므로 `{message}`).


주요 오류: 401 `{"message": "로그인이 필요합니다."}`; 404 `{"message": "일정을 찾을 수 없습니다."}`


성공 201:

```json
{
  "id": 1,
  "saved": true
}
```

### `PUT /api/events/{event_id}/dismissed`

로그인: 필수.

요청 필드:

| 필드 | 타입 | 필수 여부 | 허용값·기본값·제한 |
|---|---|---|---|
| `event_id` (path) | string | 필수 | 해당 타입 |
| `dismissed` | boolean | 필수 | 해당 타입 |

입력 타입·범위 위반: 공통 400 `{message, errors}`(플래너는 직접 검증하므로 `{message}`).


주요 오류: 401 `{"message": "로그인이 필요합니다."}`; 404 `{"message": "일정을 찾을 수 없습니다."}`; 400 `{"message": "원문 공지가 연결되지 않은 일정입니다."}`


성공 200:

```json
{
  "notice_id": 1,
  "dismissed": false
}
```

## 캘린더


### `POST /api/calendar/register`

로그인: 필수.

확인 필요 형제가 있으면 confirmed=true가 필요하다. overrides의 키는 수정 대상 일정 ID 문자열이다.

overrides는 `{일정ID: {start_date?, end_date?, start_time?, end_time?, location?}}`. 날짜·시각은 string/null(YYYY-MM-DD, HH:MM, 빈 문자열 허용), location은 string/null 최대 255자. null은 개인 수정값 제거, 추가 키 금지. 시작일≤종료일·같은 날 시작 시각≤종료 시각이며 시각에는 해당 날짜가 필요하다.

요청 필드:

| 필드 | 타입 | 필수 여부 | 허용값·기본값·제한 |
|---|---|---|---|
| `overrides` | object<string, object> | 선택 | 해당 타입 |
| `confirmed` | boolean | 선택 | default=false |
| `event_id` | string / integer | 필수 | 해당 타입; 해당 타입 |

입력 타입·범위 위반: 공통 400 `{message, errors}`(플래너는 직접 검증하므로 `{message}`).


주요 오류: 401 `{"message": "로그인이 필요합니다."}`; 404 `{"message": "일정을 찾을 수 없습니다."}`; 400 `{"message": "면접은 상세 화면에서 개별 추가해 주세요."}`; 400 `{"message": "공개된 일정만 등록할 수 있습니다."}`; 502 `{"message": "일부 일정 등록에 실패했습니다. 이미 등록한 일정은 유지됩니다. 다시 시도해 주세요."}`; 400 `{"message": "Google 캘린더 권한이 없습니다. 다시 로그인해 주세요."}`; 400 `{"message": "공지에서 날짜를 한 번 확인해 주세요."}`


성공 200:

```json
{
  "id": "1",
  "registered": true
}
```

### `GET /api/calendar/events`

로그인: 필수.

요청: 본문·쿼리 없음.


주요 오류: 401 `{"message": "로그인이 필요합니다."}`


성공 200:

배열 중 첫 항목 예시:

```json
[
  {
    "id": "1",
    "title": "일정",
    "start_date": "2030-10-08",
    "end_date": "2030-10-08",
    "location": "강의실",
    "detail": "",
    "source": "cbnu",
    "source_url": "https://example.com/event",
    "category": "academic",
    "is_new": true,
    "registered": true,
    "bookmarked": true,
    "collected_at": "2026-10-08T03:20:37+09:00",
    "review_status": "auto",
    "review_reason": null,
    "notice_id": 1,
    "event_type": "event",
    "start_time": "13:00",
    "end_time": "14:00",
    "timezone": "Asia/Seoul",
    "attendance_mode": "unknown",
    "schedule_status": "confirmed",
    "revision": 1,
    "can_register": true,
    "registration_reason": null,
    "sync_status": "synced",
    "ai_extracted": false,
    "review_required": false,
    "action_status": "pending",
    "dismissed": false,
    "user_modified": true
  }
]
```

### `POST /api/calendar/interviews/{event_id}`

로그인: 필수.

면접만 개별 추가한다. 본문 생략 가능, 확인 필요 시 confirmed=true 필요.

overrides는 `{일정ID: {start_date?, end_date?, start_time?, end_time?, location?}}`. 날짜·시각은 string/null(YYYY-MM-DD, HH:MM, 빈 문자열 허용), location은 string/null 최대 255자. null은 개인 수정값 제거, 추가 키 금지. 시작일≤종료일·같은 날 시작 시각≤종료 시각이며 시각에는 해당 날짜가 필요하다.

요청 필드:

| 필드 | 타입 | 필수 여부 | 허용값·기본값·제한 |
|---|---|---|---|
| `event_id` (path) | string | 필수 | 해당 타입 |
| `overrides` | object<string, object> | 선택 | 해당 타입 |
| `confirmed` | boolean | 선택 | default=false |

입력 타입·범위 위반: 공통 400 `{message, errors}`(플래너는 직접 검증하므로 `{message}`).


주요 오류: 401 `{"message": "로그인이 필요합니다."}`; 404 `{"message": "일정을 찾을 수 없습니다."}`; 400 `{"message": "면접 일정만 개별 추가할 수 있습니다."}`; 502 `{"message": "일부 일정 등록에 실패했습니다. 이미 등록한 일정은 유지됩니다. 다시 시도해 주세요."}`; 400 `{"message": "Google 캘린더 권한이 없습니다. 다시 로그인해 주세요."}`; 400 `{"message": "공지에서 날짜를 한 번 확인해 주세요."}`


성공 200:

```json
{
  "id": "2",
  "registered": true
}
```

### `DELETE /api/calendar/register/{event_id}`

로그인: 필수.

요청 필드:

| 필드 | 타입 | 필수 여부 | 허용값·기본값·제한 |
|---|---|---|---|
| `event_id` (path) | string | 필수 | 해당 타입 |

입력 타입·범위 위반: 공통 400 `{message, errors}`(플래너는 직접 검증하므로 `{message}`).


주요 오류: 401 `{"message": "로그인이 필요합니다."}`; 404 `{"message": "일정을 찾을 수 없습니다."}`; 502 `{"message": "일부 일정 해제에 실패했습니다. 다시 시도해 주세요."}`; 400 `{"message": "Google 캘린더 권한이 없습니다. 다시 로그인해 주세요."}`


성공 200:

```json
{
  "id": "1",
  "registered": false
}
```

### `POST /api/calendar/sync/{event_id}`

로그인: 필수.

등록한 한 일정의 변경을 Google에 반영한다. 제외/취소 일정은 삭제한다.

요청 필드:

| 필드 | 타입 | 필수 여부 | 허용값·기본값·제한 |
|---|---|---|---|
| `event_id` (path) | string | 필수 | 해당 타입 |

입력 타입·범위 위반: 공통 400 `{message, errors}`(플래너는 직접 검증하므로 `{message}`).


주요 오류: 401 `{"message": "로그인이 필요합니다."}`; 404 `{"message": "일정을 찾을 수 없습니다."}`; 400 `{"message": "등록된 일정만 갱신할 수 있습니다."}`; 502 `{"message": "Google 캘린더 반영 실패. 다시 시도해 주세요."}`; 400 `{"message": "Google 캘린더 권한이 없습니다. 다시 로그인해 주세요."}`


성공 200:

```json
{
  "id": "1",
  "registered": true
}
```

## 대시보드


### `GET /api/dashboard`

lastCollectedAt은 서울 시간대 ISO 8601(초 포함), 예: `2026-10-11T09:04:00+09:00`. 수집 기록이 없으면 기존 `"-"`를 유지한다. collectionFinishedAt은 기존 HH:MM 또는 `"-"` 형식을 유지한다.

로그인: 필수.

요청: 본문·쿼리 없음.


주요 오류: 401 `{"message": "로그인이 필요합니다."}`


성공 200:

```json
{
  "collectedToday": 1,
  "collectedChangeLabel": "+1 vs 어제",
  "lastCollectedAt": "-",
  "collectionFinishedAt": "-",
  "referenceTimeLabel": "현재 오전 03:20 기준",
  "greeting": "좋은 저녁이에요",
  "sources": [
    {
      "source": "cbnu",
      "count": 1,
      "progress": 100,
      "status": "none"
    },
    {
      "source": "wevity",
      "count": 0,
      "progress": 0,
      "status": "none"
    },
    {
      "source": "contestkorea",
      "count": 0,
      "progress": 0,
      "status": "none"
    }
  ]
}
```

## 알림함


### `GET /api/notifications`

로그인: 필수.

최신 ID 내림차순, next_before는 다음 페이지 before에 전달할 문자열 ID 또는 null이다.

요청 필드:

| 필드 | 타입 | 필수 여부 | 허용값·기본값·제한 |
|---|---|---|---|
| `limit` (query) | integer | 선택 | minimum=1; maximum=50; default=20 |
| `before` (query) | integer / null | 선택 | minimum=1; 해당 타입 |

입력 타입·범위 위반: 공통 400 `{message, errors}`(플래너는 직접 검증하므로 `{message}`).


주요 오류: 401 `{"message": "로그인이 필요합니다."}`


성공 200:

```json
{
  "items": [
    {
      "id": "1",
      "kind": "event_d1",
      "title": "내일 일정",
      "message": "일정을 확인해 주세요.",
      "event_id": "1",
      "target_date": "2030-10-08",
      "created_at": "2026-10-08T03:20:37+09:00",
      "read": false
    }
  ],
  "next_before": null
}
```

### `GET /api/notifications/unread-count`

로그인: 필수.

요청: 본문·쿼리 없음.


주요 오류: 401 `{"message": "로그인이 필요합니다."}`


성공 200:

```json
{
  "count": 1
}
```

### `POST /api/notifications/read-all`

로그인: 필수.

요청: 본문·쿼리 없음.


주요 오류: 401 `{"message": "로그인이 필요합니다."}`


성공 200:

```json
{
  "updated": 0
}
```

### `POST /api/notifications/{notification_id}/read`

로그인: 필수.

요청 필드:

| 필드 | 타입 | 필수 여부 | 허용값·기본값·제한 |
|---|---|---|---|
| `notification_id` (path) | integer | 필수 | 해당 타입 |

입력 타입·범위 위반: 공통 400 `{message, errors}`(플래너는 직접 검증하므로 `{message}`).


주요 오류: 401 `{"message": "로그인이 필요합니다."}`; 404 `{"message": "알림을 찾을 수 없습니다."}`


성공 200:

```json
{
  "id": "1",
  "read": true
}
```

## AI 플래너(추천·채팅)


### `POST /api/planner/recommendations`

로그인: 필수.

본문 최대 64 KiB. mode·refresh 외 키는 무시한다. 모드별 성공 예시는 아래 3개다.
refresh=true는 사용자·모드별 60초에 한 번 허용한다(실패한 생성 요청도 제한에 포함). bool이 아닌 refresh는 false로 처리하며 일반 요청은 제한하지 않는다.
제한 초과 시 429 `{"message":"잠시 후 다시 시도해 주세요."}`. 제한은 프로세스 메모리·monotonic 시계로 관리하며 최대 4096개 활성 키가 찬 경우 새 refresh 요청도 429다. 60초가 지난 항목은 정리한다.

요청 필드:

| 필드 | 타입 | 필수 여부 | 허용값·기본값·제한 |
|---|---|---|---|
| `mode` (본문) | string | 필수 | priority / discover / focus |
| `refresh` (본문) | boolean | 선택 | true일 때 캐시 무시; 생략/false/boolean 아닌 값은 기존 캐시 사용 |

입력 타입·범위 위반: 공통 400 `{message, errors}`(플래너는 직접 검증하므로 `{message}`).


주요 오류: 401 `{"message": "로그인이 필요합니다."}`; 400 `{"message": "추천 모드는 priority, discover, focus 중 하나여야 합니다."}`; 413 `{"message": "요청이 너무 큽니다."}`; 500 `{"message": "AI 플래너 서버 설정이 완료되지 않았습니다."}`; 502 `{"message": "AI 응답 생성에 실패했습니다."}`; 503 `{"message": "요청이 많아 잠시 후 다시 시도해 주세요."}`


성공 200 (priority):

```json
{
  "mode": "priority",
  "generated_at": "2030-10-08T12:00:00+09:00",
  "summary": "추천 활동 1개 중 3일 안에 진행할 활동이 1개 있어요.",
  "items": [
    {
      "opportunity_id": "o1",
      "fact_refs": [],
      "reason": "확인된 활동 정보를 바탕으로 검토해 주세요.",
      "next_step_label": "act",
      "next_action": "공지에서 신청 방법을 확인해 주세요.",
      "title": "활동",
      "source_url": "https://example.com/0-0",
      "focus_event_id": "e1",
      "start_date": "2030-10-08",
      "end_date": "2030-10-08",
      "start_time": "13:00",
      "end_time": "14:00",
      "action_date": "2030-10-08",
      "days_until_deadline": 0,
      "eligibility": "eligible",
      "interest_match": {
        "direct": true,
        "related": false,
        "related_fact_refs": []
      },
      "priority_context": {
        "focus_event_id": "e1",
        "next_step_type": "act",
        "verify_target": null
      },
      "next_step_type": "act"
    }
  ],
  "needs_grouping_check": [],
  "corrections": []
}
```

성공 200 (discover):

```json
{
  "mode": "discover",
  "generated_at": "2030-10-08T12:00:00+09:00",
  "summary": "추천 활동 1개 중 3일 안에 진행할 활동이 1개 있어요.",
  "items": [
    {
      "opportunity_id": "o1",
      "fact_refs": [],
      "reason": "확인된 활동 정보를 바탕으로 검토해 주세요.",
      "grade": "strong_match",
      "check_reasons": [],
      "title": "활동",
      "source_url": "https://example.com/0-0",
      "focus_event_id": "e1",
      "start_date": "2030-10-08",
      "end_date": "2030-10-08",
      "start_time": "13:00",
      "end_time": "14:00",
      "action_date": "2030-10-08",
      "days_until_deadline": 0,
      "eligibility": "eligible",
      "interest_match": {
        "direct": true,
        "related": false,
        "related_fact_refs": []
      },
      "priority_context": {
        "focus_event_id": "e1",
        "next_step_type": "act",
        "verify_target": null
      },
      "next_step_type": "act"
    }
  ],
  "needs_grouping_check": [],
  "corrections": []
}
```

성공 200 (focus):

```json
{
  "mode": "focus",
  "generated_at": "2030-10-08T12:00:00+09:00",
  "summary": "추천 활동 1개 중 3일 안에 진행할 활동이 1개 있어요.",
  "items": [
    {
      "opportunity_id": "o1",
      "fact_refs": [],
      "reason": "확인된 활동 정보를 바탕으로 검토해 주세요.",
      "tier": "core",
      "secondary_reason": null,
      "chosen_over": [],
      "conflicts_with": [],
      "title": "활동",
      "source_url": "https://example.com/0-0",
      "focus_event_id": "e1",
      "start_date": "2030-10-08",
      "end_date": "2030-10-08",
      "start_time": "13:00",
      "end_time": "14:00",
      "action_date": "2030-10-08",
      "days_until_deadline": 0,
      "eligibility": "eligible",
      "interest_match": {
        "direct": true,
        "related": false,
        "related_fact_refs": []
      },
      "priority_context": {
        "focus_event_id": "e1",
        "next_step_type": "act",
        "verify_target": null
      },
      "next_step_type": "act"
    }
  ],
  "needs_grouping_check": [],
  "corrections": []
}
```

### `POST /api/planner/chat`

로그인: 필수.

본문 최대 64 KiB. 신규 추천과 별개인 기존 자유형 채팅이다. 잘못된 history 항목은 무시한다.

요청 필드:

| 필드 | 타입 | 필수 여부 | 허용값·기본값·제한 |
|---|---|---|---|
| `message` (본문) | string | 필수 | trim 후 1~3000자 |
| `mode` (본문) | string | 선택 | general(기본)/priority/discover/focus; explorer→discover, study·balanced→general; 미지정·모르는 값은 general |
| `history` (본문) | array<object> | 선택 | role=user/assistant, content=string; 빈 항목 제거 후 최근 12개, content 최대 2000자로 자름 |

입력 타입·범위 위반: 공통 400 `{message, errors}`(플래너는 직접 검증하므로 `{message}`).


주요 오류: 401 `{"message": "로그인이 필요합니다."}`; 400 `{"message": "메시지를 입력해 주세요."}`; 413 `{"message": "요청이 너무 큽니다."}`; 500 `{"message": "AI 플래너 서버 설정이 완료되지 않았습니다."}`; 502 `{"message": "AI 응답 생성에 실패했습니다."}`; 503 `{"message": "요청이 많아 잠시 후 다시 시도해 주세요."}`


성공 200:

```json
{
  "reply": "다가오는 일정을 확인해 주세요."
}
```

## 관리자

### `GET /api/admin/feedback`

관리자 로그인: 필수(기존 관리자 권한 검사). GET이므로 Origin 검사는 하지 않는다.
쿼리: limit 정수 기본 50, 1~200; offset 정수 기본 0, 0 이상.
created_at 내림차순, 동률이면 id 내림차순. 성공 200:

```json
{"total": 1, "items": [{"id": 1, "user_id": 2, "type": "suggestion", "message": "서비스 의견", "reply_email": null, "created_at": "2026-10-11T00:04:00+00:00"}]}
```

미로그인 401, 일반 사용자 403 `{message}`. 잘못된 쿼리는 기존 검증 방식 400 `{message, errors}`.


### `GET /api/admin/notices`

로그인: 필수, 관리자 권한 및 변경 시 허용 Origin 필요.

state=all/pending/retry_pending/processing/needs_review/approved/rejected/failed/extracted/no_events/reported. 기본 needs_review.

요청 필드:

| 필드 | 타입 | 필수 여부 | 허용값·기본값·제한 |
|---|---|---|---|
| `state` (query) | string | 선택 | default="needs_review" |
| `limit` (query) | integer | 선택 | minimum=1; maximum=100; default=30 |
| `offset` (query) | integer | 선택 | minimum=0; default=0 |

입력 타입·범위 위반: 공통 400 `{message, errors}`(플래너는 직접 검증하므로 `{message}`).


주요 오류: 400 `{"message": "알 수 없는 검토 상태입니다."}`


성공 200:

```json
{
  "total": 1,
  "items": [
    {
      "id": 1,
      "title": "활동",
      "state": "extracted",
      "revision": 0,
      "source": "cbnu",
      "error": null
    }
  ]
}
```

### `GET /api/admin/notices/{notice_id}`

로그인: 필수, 관리자 권한 및 변경 시 허용 Origin 필요.

요청 필드:

| 필드 | 타입 | 필수 여부 | 허용값·기본값·제한 |
|---|---|---|---|
| `notice_id` (path) | integer | 필수 | 해당 타입 |

입력 타입·범위 위반: 공통 400 `{message, errors}`(플래너는 직접 검증하므로 `{message}`).


주요 오류: 404 `{"message": "공지를 찾을 수 없습니다."}`


성공 200:

```json
{
  "id": 1,
  "title": "활동",
  "body": "",
  "source_url": "https://example.com/0-0",
  "state": "extracted",
  "revision": 0,
  "model_output": null,
  "error": null,
  "events": [
    {
      "id": 1,
      "title": "일정",
      "start_date": "2030-10-08",
      "end_date": "2030-10-08",
      "start_time": "13:00",
      "end_time": "14:00",
      "timezone": "Asia/Seoul",
      "location": "",
      "detail": "",
      "event_type": "event",
      "attendance_mode": "unknown",
      "schedule_status": "confirmed",
      "revision": 1,
      "review_status": "auto",
      "review_reason": null
    },
    {
      "id": 2,
      "title": "면접",
      "start_date": "2030-10-08",
      "end_date": "2030-10-08",
      "start_time": "13:00",
      "end_time": "14:00",
      "timezone": "Asia/Seoul",
      "location": "",
      "detail": "",
      "event_type": "interview",
      "attendance_mode": "unknown",
      "schedule_status": "confirmed",
      "revision": 1,
      "review_status": "auto",
      "review_reason": null
    }
  ],
  "reports": [
    {
      "id": 1,
      "event_id": 1,
      "reason": "date",
      "memo": "날짜 확인 요청"
    }
  ],
  "history": []
}
```

### `POST /api/admin/notices/{notice_id}/review`

로그인: 필수, 관리자 권한 및 변경 시 허용 Origin 필요.

revision 비교 후 기록. save=비공개 검토 대기, approve=공개, reject=제외. 누락된 기존 일정은 거절 처리하며 삭제하지 않는다.

요청 필드:

| 필드 | 타입 | 필수 여부 | 허용값·기본값·제한 |
|---|---|---|---|
| `notice_id` (path) | integer | 필수 | 해당 타입 |
| `revision` | integer | 필수 | minimum=0.0 |
| `action` | string | 필수 | enum=["save", "approve", "reject"] |
| `reason` | string | 필수 | minLength=1; maxLength=2000 |
| `events` | array<object> | 선택 | maxItems=50 |
| `events[].id` | integer / null | 선택 | 해당 타입; 해당 타입 |
| `events[].title` | string | 필수 | minLength=1; maxLength=1000 |
| `events[].start_date` | string | 선택 | default="" |
| `events[].end_date` | string | 선택 | default="" |
| `events[].start_time` | string | 선택 | default="" |
| `events[].end_time` | string | 선택 | default="" |
| `events[].timezone` | string | 선택 | default="Asia/Seoul" |
| `events[].location` | string | 선택 | maxLength=255; default="" |
| `events[].detail` | string | 선택 | maxLength=2000; default="" |
| `events[].event_type` | string | 선택 | enum=["application", "submission", "event", "interview", "orientation", "result", "service_change"]; default="event" |
| `events[].attendance_mode` | string | 선택 | enum=["offline", "online", "hybrid", "unknown", "not_applicable"]; default="unknown" |
| `events[].schedule_status` | string | 선택 | enum=["confirmed", "tentative", "cancelled", "unknown"]; default="confirmed" |

입력 타입·범위 위반: 공통 400 `{message, errors}`(플래너는 직접 검증하므로 `{message}`).


주요 오류: 404 `{"message": "공지를 찾을 수 없습니다."}`; 400 `{"message": "중복되거나 다른 공지의 일정 ID입니다."}`; 409 `{"message": "다른 작업에서 변경되었습니다. 새로 불러온 뒤 검토해 주세요."}`


성공 200:

```json
{
  "id": 1,
  "revision": 1,
  "state": "needs_review"
}
```

## 내부 API — 프론트 미사용


### `GET /api/internal/planner/payload`

로그인: 필수.

요청 필드:

| 필드 | 타입 | 필수 여부 | 허용값·기본값·제한 |
|---|---|---|---|
| `mode` (query) | string | 필수 | enum=["priority", "discover", "focus"] |

입력 타입·범위 위반: 공통 400 `{message, errors}`(플래너는 직접 검증하므로 `{message}`).


주요 오류: 401 `{"message": "로그인이 필요합니다."}`


성공 200:

```json
{
  "mode": "priority",
  "opportunities": [
    {
      "opportunity_id": "o1",
      "title": "활동",
      "eligibility": "eligible",
      "facts": [],
      "requirements": [],
      "unparsed_requirements": [],
      "user_managed": true,
      "priority_context": {
        "focus_event_id": "e1",
        "next_step_type": "monitor",
        "verify_target": null
      },
      "prep_items": [],
      "how_to_apply": []
    }
  ]
}
```

### `GET /api/internal/planner/facts`

로그인: 필수.

요청: 본문·쿼리 없음.


주요 오류: 401 `{"message": "로그인이 필요합니다."}`


성공 200:

배열 중 첫 항목 예시:

```json
[
  {
    "event_id": 1,
    "notice_id": 1,
    "event_type": "event",
    "start_date": "2030-10-08",
    "end_date": "2030-10-08",
    "start_time": "13:00",
    "end_time": "14:00",
    "location": "강의실",
    "timezone": "Asia/Seoul",
    "days_until_deadline": 1461,
    "can_apply_now": null,
    "expired": false,
    "bookmarked": true,
    "registered": false,
    "action_status": "pending",
    "dismissed": false,
    "calendar_conflict": true,
    "conflicting_event_ids": [
      2
    ],
    "review_required": false,
    "action_type": "attend",
    "action_date": "2030-10-08",
    "action_window": "open",
    "urgency": "later",
    "review_status": "ok"
  }
]
```

### `GET /api/internal/planner/facts/{event_id}`

로그인: 필수.

요청 필드:

| 필드 | 타입 | 필수 여부 | 허용값·기본값·제한 |
|---|---|---|---|
| `event_id` (path) | string | 필수 | 해당 타입 |

입력 타입·범위 위반: 공통 400 `{message, errors}`(플래너는 직접 검증하므로 `{message}`).


주요 오류: 401 `{"message": "로그인이 필요합니다."}`; 404 `{"message": "일정을 찾을 수 없습니다."}`


성공 200:

```json
{
  "event_id": 1,
  "notice_id": 1,
  "event_type": "event",
  "start_date": "2030-10-08",
  "end_date": "2030-10-08",
  "start_time": "13:00",
  "end_time": "14:00",
  "location": "강의실",
  "timezone": "Asia/Seoul",
  "days_until_deadline": 1461,
  "can_apply_now": null,
  "expired": false,
  "bookmarked": true,
  "registered": false,
  "action_status": "pending",
  "dismissed": false,
  "calendar_conflict": true,
  "conflicting_event_ids": [
    2
  ],
  "review_required": false,
  "action_type": "attend",
  "action_date": "2030-10-08",
  "action_window": "open",
  "urgency": "later",
  "review_status": "ok"
}
```

### `GET /api/internal/planner/opportunities`

로그인: 필수.

요청: 본문·쿼리 없음.


주요 오류: 401 `{"message": "로그인이 필요합니다."}`


성공 200:

```json
[
  {
    "opportunity_id": "o1",
    "title": "활동",
    "source_url": "https://example.com/0-0",
    "category": "academic",
    "dismissed": false,
    "user_managed": true,
    "events": [
      {
        "event_id": "e1",
        "event_type": "main_event",
        "title": "일정",
        "detail": "",
        "source_url": "https://example.com/event",
        "start_date": "2030-10-08",
        "end_date": "2030-10-08",
        "start_time": "13:00",
        "end_time": "14:00",
        "timezone": "Asia/Seoul",
        "location": "강의실",
        "action_type": "attend",
        "action_date": "2030-10-08",
        "expired": false,
        "action_window": "open",
        "urgency": "later",
        "review_status": "ok",
        "action_status": "pending"
      },
      {
        "event_id": "e2",
        "event_type": "other",
        "title": "면접",
        "detail": "",
        "source_url": "https://example.com/event",
        "start_date": "2030-10-08",
        "end_date": "2030-10-08",
        "start_time": "13:00",
        "end_time": "14:00",
        "timezone": "Asia/Seoul",
        "location": "",
        "action_type": "attend",
        "action_date": "2030-10-08",
        "expired": false,
        "action_window": "open",
        "urgency": "later",
        "review_status": "ok",
        "action_status": "pending"
      }
    ],
    "enrichment": {
      "notice_kind": "normal",
      "grouping_review_required": false,
      "grouping_review_reason": "",
      "enrichment_status": "ok",
      "low_confidence_reasons": [],
      "is_mandatory": false,
      "facts": [],
      "events": []
    },
    "application_expired_not_done": false,
    "calendar_conflict": false
  }
]
```

## health


### `GET /api/health`

로그인: 불필요.

200 status=ok/degraded. DB 실패도 degraded 응답일 수 있다. 설정값은 비밀값 대신 설정 여부만 반환.

요청: 본문·쿼리 없음.


주요 오류: 공통 오류 참조.


성공 200:

```json
{
  "status": "ok",
  "service": "notiai-backend",
  "time": "2026-10-08T03:20:37+09:00",
  "db": {
    "ok": true,
    "notices": 1,
    "events": 2
  },
  "extraction": {
    "pending": 0,
    "retryPending": 0,
    "processing": 0,
    "failed": 0,
    "modelAvailable": null,
    "modelCheckedAt": null
  },
  "crawl": {
    "cbnu": null,
    "wevity": null,
    "contestkorea": null
  },
  "config": {
    "devLogin": true,
    "extractor": "stub",
    "openaiApiKeyConfigured": false,
    "googleOAuthConfigured": false,
    "crawlEnabled": false
  }
}
```

## 계약 스냅샷 갱신

`tests/test_api_contract.py`가 OpenAPI 경로·메서드, 실제 주요 응답의 최상위/항목/주요 중첩 키를 고정한다. 문자열 내용·타입 전체를 고정하는 테스트는 아니다.
의도적 변경 시 파일 맨 위 PowerShell 명령으로 `api_routes.json`·`api_response_keys.json`을 갱신한 뒤 diff를 검토하고 이 문서도 함께 수정한다. 일반 테스트 실행에서는 스냅샷을 쓰지 않는다.

## 프론트 개편 때 바꿔야 할 것

- 설정/추천 AI 모드 ID를 priority/discover/focus로 교체한다. 기존 채팅 모드와 혼동하지 않는다.
- 알림 설정을 `{d3,d1,email}`로 교체한다. 설정 PUT는 변경 필드만 전송할 수 있고 notifications도 키별 병합된다.
- 프로필 입력을 `PUT /api/user/profile`로 연결한다(전공, 학년 1~6/null, 재학 상태).
- 알림함 4개 API: 목록, unread-count, 개별 읽음, read-all을 연결한다.
- `POST /api/planner/recommendations`의 구조화된 모드별 응답과 한국어 라벨을 적용한다.
- `/api/planner/chat`은 기존 응답 `{reply}`를 유지하며 기본 general로 내 일정 질문에 답한다.
- 새로 받기는 추천 요청에 refresh=true를 보내고 429 message를 표시한다. summary가 문자열이면 표시한다.
- 첫 로그인은 Profile.onboarding_done으로 판단하고 완료 시 onboarding PUT를 호출한다. 탈퇴는 `/api/users/me` DELETE를 사용한다.
- 일정 카드는 D-day·제목/출처·기간·장소·일정 등록으로 구성하고, 상세에서 공지별 일정·원문 링크·개인 수정·면접 개별 등록을 연결한다.
- 확인 필요 일정은 `review_required`로 안내하고 확인 등록, 등록 후 수정과 sync를 연결한다. 오류는 message를 표시하되 입력 검증의 errors도 허용한다.
