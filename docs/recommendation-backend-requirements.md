# NotiAI 추천·개인화 Backend 추가 요청 명세 v1.2 FINAL

> **전달용 최종본**  
> 기준: `capstonenotiai/backend` main (2026-10-05 확인) + `NotiAI Prompt v3.1.2 FROZEN`  
> 목적: 제가 담당한 추천/프롬프트 기능(PRIORITY / DISCOVER / FOCUS)을 실제 서비스에 연결하기 위해 **현재 Backend에 추가로 필요한 기능만** 정리  
> 핵심 원칙: **Backend = Facts / GPT = Judgment**

---

# 0. 문서 범위

이 문서는 Backend 구현 방식을 지정하려는 문서가 아니라,  
제가 만든 추천/프롬프트 기능이 동작하려면 **Backend에서 어떤 데이터/상태가 필요하고 어떤 값은 Backend가 결정해야 하는지**를 정리한 요청사항입니다.

아래 구현 방식은 기존 Backend 구조에 맞게 편한 방식으로 정하셔도 됩니다.

- DB 테이블을 새로 만들지 / 기존 테이블을 확장할지
- endpoint 이름
- 파일/클래스 이름
- 파생값을 DB에 저장할지 요청 시 계산할지
- 추천 API를 하나로 만들지 모드별로 나눌지
- migration 구성 방식

최종 Prompt 텍스트 / Structured Output Schema / 추천 규칙 자체는 제가 정리해서 코드에 반영할 예정입니다.

---

# 1. 현재 Backend에서 그대로 활용 가능한 부분

현재 코드 기준으로 이미 다음이 있어, 이 부분은 새로 요청드리는 내용이 아닙니다.

- `Notice`
  - `site`
  - `board`
  - `source_url`
  - `title_raw`
  - `raw_text`
  - `crawled_at`
- `Event`
  - 모델 5필드
    - `title`
    - `start_date`
    - `end_date`
    - `location`
    - `detail`
  - `review_status`
  - `review_reason`
  - `extractor`
- `UserEvent`
  - `registered`
  - `google_event_id`
  - `bookmarked`
- `Preference`
  - `interests`
  - `enabled_sources`
  - `notifications`
- Google Calendar 등록 / 해제
- `ModelApiExtractor`
- `source_url` 기반 중복 방지
- OpenAI Responses API 호출 구조
- `raw_text` 원문 저장

아래부터가 **추가 또는 확장이 필요한 부분**입니다.

---

# 2. 우선순위

## P0 — 추천 시스템 연결 전에 우선 필요한 핵심

1. 한 Notice에서 여러 Event 저장 가능
2. 같은 활동의 Event들을 묶을 Opportunity 단위 식별자
3. 사용자 `major / grade / enrollment_status`
4. 사용자별 Event `pending / done`
5. Enrichment 결과 저장 + evidence 검증 + `fact_id`
6. `eligibility`
7. `interest_match`
8. Event 파생값 + `priority_context`
9. 최종 Mode `priority / discover / focus`
10. Mode별 Prompt Payload 구성
11. GPT 출력 서버 검증

### P0도 한 번에 전부 구현 요청드리는 의미는 아닙니다.

추천하는 순서는:

```text
1) events[] / Opportunity 구조
2) 사용자 profile / action_status
3) Enrichment / Fact
4) eligibility / interest_match / priority_context
5) Mode Payload / Structured Output 검증
```

입니다.

---

## P1 — 최종 추천/개인화 완성도를 위해 추가

12. Opportunity `dismissed`
13. `user_managed`
14. Calendar conflict / pairwise conflict

---

## P2 — 없어도 우선 동작 가능

15. `new_to_user`

P2는 바로 구현하지 않아도 되고,  
P1의 Calendar conflict 역시 초기 통합 단계에서는 `null` / 빈 배열로 두어도 Prompt 자체는 동작합니다.

---

# 3. [P0] Notice 1건에서 여러 Event 저장 가능

최종 모델은 접수 일정과 본행사가 나뉘는 경우 다음처럼 `events[]` 형태로 반환할 예정입니다.

```json
{
  "events": [
    {
      "title": "접수 일정",
      "start_date": "...",
      "end_date": "...",
      "location": "...",
      "detail": "..."
    },
    {
      "title": "본행사 일정",
      "start_date": "...",
      "end_date": "...",
      "location": "...",
      "detail": "..."
    }
  ]
}
```

추천 시스템에서는:

```text
하나의 활동(Opportunity)
├─ 접수 Event
└─ 본행사 Event
```

처럼 사용합니다.

## 요청

현재의:

```text
Notice 1 : Event 1
```

구조를:

```text
Notice 1 : Opportunity 1 : Event N
```

처럼 표현 가능하도록 부탁드립니다.

필요 개념:

```text
notice_id
opportunity_id
event_id
```

현재 `Event.notice_id`가 unique이고 `Notice.event`가 `uselist=False`라서, 최종 `events[]` 연결 시 1:N 대응이 필요합니다.

`Opportunity`를 별도 테이블로 둘지, `Notice`를 부모 단위로 활용할지는 기존 구조에 맞게 정하셔도 됩니다.

---

# 4. [P0] 사용자 추천 프로필 확장

DISCOVER에서 자격조건 및 프로필 적합도를 판단하려면:

```text
major
grade
enrollment_status
interests
```

가 필요합니다.

현재 `interests`는 이미 있으므로 아래 3개 추가가 필요합니다.

```json
{
  "major": "소프트웨어학부",
  "grade": 3,
  "enrollment_status": "enrolled"
}
```

`enrollment_status`는 최소 다음 의미를 표현할 수 있으면 됩니다.

```text
enrolled
leave
graduated
unknown
```

실제 enum 이름은 달라도 괜찮습니다.

### 프론트 연결

`major / grade / enrollment_status`는 프론트에서 조회·수정 가능해야 하므로,  
기존 `/api/user` 또는 `/api/user/preferences` 구조를 확장하거나 별도 API로 제공해주시면 됩니다.

Prompt에는 이름, 이메일, Google token 등은 보내지 않습니다.

---

# 5. [P0/P1] 사용자별 일정·활동 상태

핵심 원칙:

```text
Calendar 등록 ≠ 실제 행동 완료
```

현재 `registered / bookmarked`만으로는 신청 완료 여부를 알 수 없어 아래 상태가 필요합니다.

## 5-1. [P0] Event action_status

각 사용자 × Event:

```text
pending
done
```

예:

```text
접수 Event = done
본행사 Event = pending
```

이면 PRIORITY는 본행사 Event를 다음 행동 대상으로 볼 수 있습니다.

프론트에서 `완료 / 완료취소`를 변경할 수 있는 API가 필요합니다.

## 5-2. [P1] Opportunity dismissed

사용자가 더 이상 추천받고 싶지 않은 활동:

```text
dismissed = true / false
```

DISCOVER / FOCUS에서 기본 제외합니다.

프론트에서 `관심없음 / 관심없음 취소`를 변경할 수 있는 사용자 상태 API가 필요합니다.

## 5-3. [P1] user_managed

Opportunity-level:

```text
user_managed: true | false
```

의미:

> 사용자가 이 활동을 직접 저장/등록/관리 대상으로 선택한 적이 있는지

기존 `registered / bookmarked` 등을 이용해 계산해도 되고 별도 상태를 두어도 됩니다.

---

# 6. [P0] Enrichment 결과 저장 + Evidence 검증

LLaMA 모델은 날짜/기본 일정의 Source of Truth로 유지합니다.

Enrichment는 공지 `raw_text`에서 추천에 필요한 의미정보만 추가합니다.

## 6-1. Enrichment 결과

### Notice-level

```text
notice_kind
grouping_review_required
grouping_review_reason
```

`notice_kind`:

```text
normal
extension
correction
cancellation
```

### Opportunity-level

```text
title
related_interests
requirements
unparsed_requirements
benefits
prep_items
how_to_apply
is_mandatory
enrichment_status
```

### Event-level

```text
event_type
action_type
role_evidence
deadline_time_text
early_close
```

`event_type`:

```text
application
main_event
other
unknown
```

`action_type`:

```text
apply
submit
attend
check
other
unknown
```

## 6-2. Evidence 검증

Enrichment가 추출한 의미정보에는 공지 원문 근거 문자열을 함께 반환합니다.

Backend:

```text
Enrichment output
→ evidence가 Notice.raw_text의 실제 literal substring인지 확인
→ 검증 성공한 항목만 Fact로 유지
→ fact_id 부여
```

예:

```json
{
  "fact_id": "o101.ben1",
  "type": "benefit",
  "value": "수상팀에게 인턴 면접 기회 제공"
}
```

### fact_id 조건

- Opportunity 범위에서 유일
- Mode GPT가 전달받은 `fact_id`만 다시 참조 가능
- 다른 Opportunity의 Fact 참조 불가

## 6-3. enrichment_status

다음 핵심 evidence 검증 실패 시:

```text
required=true requirement
is_mandatory=true
notice_kind != normal
```

해당 Opportunity:

```text
enrichment_status = low_confidence
```

그 외 일반 benefit / prep_item / related_interest 검증 실패는 해당 Fact만 제거해도 됩니다.

> Enrichment Prompt / Schema 자체는 제가 코드로 전달할 예정입니다.

---

# 7. [P0] eligibility

Opportunity마다 Backend에서:

```text
eligible
ineligible
needs_check
unknown
```

중 하나를 제공해주시면 됩니다.

## MVP에서 구조화 비교

```text
grade
enrollment_status
major
```

그 외:

```text
TOEIC
GPA
소득분위
주소지
기타 복합조건
```

은 `unparsed_requirements`로 남깁니다.

## ineligible은 보수적으로

다음이 모두 명확할 때만 `ineligible`:

```text
required = true
+ evidence 검증 성공
+ 조건 값이 명확
+ 해당 Opportunity에 귀속됨이 명확
```

애매하면:

```text
needs_check
```

으로 부탁드립니다.

`major`는 구조화 비교는 가능하지만 기본 hard exclusion에는 사용하지 않는 방향입니다.

---

# 8. [P0] interest_match

DISCOVER / FOCUS에서 GPT가 관심분야 연결 자체를 새로 만들지 않습니다.

Backend가 최소 다음 값을 계산해주면 됩니다.

```json
{
  "direct": true,
  "related": false,
  "related_fact_refs": []
}
```

## direct

```text
Opportunity primary category ∈ user.interests
```

## related

검증된 `related_interest` Fact가 사용자의 선택 관심분야와 연결될 때만 true.

단순히 제목에 특정 단어가 있다고 `related=true`로 만들지는 않습니다.

---

# 9. [P0] Event 파생값

각 Event에 다음 값이 필요합니다.

```text
expired
action_date
action_window
urgency
action_status
review_status
```

## expired

기본:

```text
end_date < today
```

## action_date

```text
apply / submit → end_date
attend → start_date
unknown → end_date fallback
```

## action_window

```text
open
not_open
unknown
```

`attend`의 정확한 open 시점은 최종 `events[]` 구조 확인 후 맞추면 됩니다.

## urgency

```text
urgent
soon
upcoming
later
none
```

구간값 자체는 Backend 상수로 두면 됩니다.

---

# 10. [P0] PRIORITY용 priority_context

PRIORITY는 GPT가 행동 시점과 우선순위를 새로 계산하지 않습니다.

Opportunity마다:

```json
{
  "priority_context": {
    "focus_event_id": "e101",
    "next_step_type": "act",
    "verify_target": null
  }
}
```

가 필요합니다.

## 10-1. focus_event_id

기본:

```text
pending
AND not expired
중 action_date가 가장 이른 Event
```

접수 Event가 `done`이면 다음 pending Event로 이동합니다.

접수기간은 끝났지만 사용자의 실제 신청 여부가 확인되지 않았고 `user_managed=true`이면 신청 여부 확인 흐름을 유지할 수 있어야 합니다.

## 10-2. next_step_type

```text
act
prepare
verify
monitor
```

기본 규칙:

```text
focus Event review_status = needs_review
→ verify(date)

grouping_review_required = true
→ verify(grouping)

action_window = unknown
→ verify(action_window)

접수 만료 + done 아님 + user_managed
→ verify(application_confirmation)

action_window = not_open + prep_items 존재
→ prepare

action_window = not_open + prep_items 없음
→ monitor

urgency = later / none + 즉시 행동 근거 없음
→ monitor

그 외 open + pending
→ act
```

중요:

```text
eligibility = needs_check / unknown
```

이라는 이유만으로 `next_step_type=verify`로 바꾸지는 않습니다.

## 10-3. verify_target

```text
date
grouping
action_window
application_confirmation
null
```

---

# 11. [P0] PRIORITY Backend 정렬

PRIORITY GPT는 Backend 순서를 그대로 설명합니다.

기본 순서:

```text
urgent
> soon
> upcoming
> later
> none
```

같은 urgency 내부:

```text
1. is_mandatory = true
2. early_close = true
3. user_managed = true
4. action_window = open > not_open
5. 완전 동률이면 기존 Backend order 유지
```

다음은 PRIORITY 정렬 근거로 사용하지 않습니다.

```text
prep_items
how_to_apply
benefits
interest
eligibility uncertainty
```

---

# 12. [P0/P1] DISCOVER / FOCUS 후보 필터

## DISCOVER 입력 전 제외

기본 제외:

```text
expired
dismissed
done
clearly ineligible
```

추가:

```text
application Event expired
AND action_status != done
```

인 Opportunity도 신규 DISCOVER / FOCUS 추천에서는 기본 제외합니다.

단 `user_managed=true`라면 PRIORITY에서는:

```text
verify(application_confirmation)
```

대상으로 남길 수 있습니다.

## FOCUS Gate

FOCUS 후보:

```text
not expired
not done
not dismissed
not clearly ineligible
user interest와 연결됨
```

`grouping_review_required=true` 후보는 FOCUS GPT에서 판단하지 않고 Backend에서 별도 처리합니다.

---

# 13. [P1] Google Calendar conflict

현재 Calendar 등록/삭제는 이미 있으므로 추천용으로 충돌 조회 결과가 있으면 됩니다.

각 후보:

```text
calendar_conflict:
true
false
null
```

의미:

```text
true  = 실제 확인 완료 + 직접 충돌 있음
false = 실제 확인 완료 + 직접 충돌 없음
null  = 미연동 / 조회 실패 / 해당 범위 미검증
```

중요:

```text
null ≠ 충돌 없음
```

FOCUS 후보끼리의 충돌은:

```text
pairwise_conflicts
```

예:

```json
[
  ["o101", "o103"]
]
```

실제 확인된 관계만 전달합니다.

### 구현 부담이 있으면

초기 연결 단계에서는:

```text
calendar_conflict = null
pairwise_conflicts = []
```

로 두어도 Prompt는 동작합니다.

따라서 모델/추천 통합 자체를 막는 P0 blocker는 아닙니다.

---

# 14. [P0] 최종 Mode ID

현재 임시 Mode:

```text
study
explorer
balanced
```

최종 추천 Mode:

```text
priority
discover
focus
```

Backend의 mode validation / preference / planner 호출부에서 위 3개 ID를 받을 수 있으면 됩니다.

최종 Prompt 텍스트 / Structured Output Schema 코드는 제가 반영해서 전달할 예정입니다.

---

# 15. [P0] Mode별 최소 Payload

DB 객체 전체를 넘기지 않고 필요한 값만 Mode GPT에 전달합니다.

## PRIORITY

```text
opportunity_id
title
eligibility
user_managed
priority_context
prep_items
how_to_apply
requirements / unparsed_requirements (필요 시)
early_close 관련 검증 Fact
```

## DISCOVER

```text
user.major
user.grade
user.enrollment_status
user.interests

opportunity_id
title
category
eligibility
interest_match
enrichment_status
grouping_review_required
new_to_user
requirements
unparsed_requirements
benefits
validated facts
```

## FOCUS

```text
user.interests

opportunity_id
title
category
eligibility
user_managed
interest_match
enrichment_status
calendar_conflict
pairwise_conflicts
requirements
unparsed_requirements
benefits
validated facts
```

실제 DTO / Pydantic 모델명은 자유입니다.

---

# 16. [P0] GPT Structured Output 서버 검증

Strict JSON Schema를 사용해도 Backend에서 최종 검증을 한 번 더 해주면 됩니다.

최소 검증:

```text
1. input Opportunity가 출력에 누락/중복되지 않았는지
2. opportunity_id가 실제 input에 존재하는지
3. fact_refs가 해당 Opportunity의 validated Fact인지
4. enum 값이 유효한지
5. FOCUS chosen_over 관계가 일관적인지
6. conflicts_with가 실제 pairwise_conflicts에 존재하는지
```

## DISCOVER cap 검증

```text
eligibility = needs_check / unknown
→ maximum potential_match

enrichment_status = low_confidence
→ maximum potential_match

grouping_review_required = true
→ maximum potential_match
```

`unparsed_requirements` 자체는 등급을 낮추는 cap이 아니라:

```text
check_reasons += requirement
```

용도입니다.

## 보정 방향

```text
PRIORITY 누락
→ Backend 순서대로 복원

DISCOVER cap 위반
→ potential_match로 clamp

FOCUS 누락
→ SECONDARY(information_uncertain)

chosen_over 관계 불일치
→ 잘못된 관계 제거

conflicts_with가 있는 SECONDARY
→ schedule_conflict 보장

근거 없는 stronger_alternative
→ 제거 / 미표시
```

GPT 출력 오류가 DB Fact나 사용자 원본 상태를 바꾸지는 않도록 부탁드립니다.

---

# 17. [P2] new_to_user

DISCOVER 설명용 보조신호:

```text
true
false
null
```

사용자별 노출 여부까지 추적 가능하면 계산하고, 아직 구현하지 않는다면:

```text
null
```

로 보내도 Prompt는 정상 동작합니다.

따라서 MVP 필수는 아닙니다.

---

# 18. 크롤러와 Backend 경계

크롤러는 제가 서비스용으로 재설계·재작성할 예정입니다.

Backend에서는 기존 Notice의 다음 값이 보존되면 됩니다.

```text
site
board
source_url
title_raw
raw_text
crawled_at
```

특히:

```text
source_url 중복 방지
raw_text 원문 저장
```

은 유지 부탁드립니다.

새 크롤러 출력 형식은 현재 Backend import 구조에 최대한 맞추겠습니다.

---

# 19. 현재 Backend 기준 직접적인 수정 포인트

현재 코드를 기준으로 확인한 연결 지점입니다.

## `app/models.py`

현재:

```text
Notice.event = uselist=False
Event.notice_id = unique
```

→ 최종 `events[]`를 위해 1:N 가능하도록 변경 필요

현재 `UserEvent`:

```text
registered
google_event_id
bookmarked
```

→ 사용자별 `action_status` 등 추가 필요

## `app/schemas.py`

현재:

```text
AI_MODE_IDS = study / explorer / balanced
Profile = name / email
```

→

```text
priority / discover / focus
major / grade / enrollment_status
```

지원 필요

## `app/services/planner.py`

현재 TEMPORARY:

```text
Study / Explorer / Balanced
자유형 { reply }
```

→ 최종 Prompt / Mode Payload / Structured Output 연결 필요

Prompt 텍스트 및 Schema는 제가 반영/전달 예정입니다.

## `app/services/google.py`

현재:

```text
Calendar 등록 / 삭제
```

→ P1 단계에서 conflict 조회 기능 추가 가능

---

# 20. 구현 순서 제안

## 1단계 — 모델 연결과 데이터 구조

```text
events[]
Opportunity grouping
user profile
action_status
```

## 2단계 — 추천 Facts

```text
Enrichment
Evidence validation
fact_id
eligibility
interest_match
event derived facts
priority_context
```

## 3단계 — Prompt 연결

```text
priority / discover / focus
Mode payload
Structured Output
server validation
```

## 4단계 — 부가 개인화

```text
dismissed
user_managed
calendar_conflict
pairwise_conflicts
new_to_user
```

Calendar conflict / new_to_user는 초기 통합에서는 임시값으로 시작해도 됩니다.

---

# 21. 최종 연결 목표

```text
Crawler
↓
Notice(raw_text)
↓
Model events[]
↓
Opportunity + Events
↓
Enrichment + Evidence validation
↓
Backend Facts / User Profile
↓
PRIORITY / DISCOVER / FOCUS Payload
↓
Prompt v3.1.2 FROZEN
↓
Structured Output Validation
↓
Web Front
```

---

# 22. 최종 요청사항 체크리스트

## P0

- [ ] Notice 1건에서 Event 여러 개 저장
- [ ] Opportunity grouping key
- [ ] major / grade / enrollment_status
- [ ] 프로필 조회/수정 가능
- [ ] Event pending / done
- [ ] 완료/완료취소 가능
- [ ] Enrichment 저장
- [ ] raw_text evidence validation
- [ ] fact_id
- [ ] eligibility
- [ ] interest_match
- [ ] expired / action_date / action_window / urgency
- [ ] focus_event_id / next_step_type / verify_target
- [ ] PRIORITY 정렬
- [ ] DISCOVER / FOCUS Gate
- [ ] priority / discover / focus Mode
- [ ] Mode별 Payload
- [ ] Structured Output 검증

## P1

- [ ] dismissed
- [ ] 관심없음/취소 가능
- [ ] user_managed
- [ ] Calendar conflict
- [ ] pairwise_conflicts

## P2

- [ ] new_to_user

---

# 23. 지금 결정하지 않아도 되는 부분

아래는 Backend 최종 구조에 맞게 이후 정하면 됩니다.

- 실제 DB 테이블 / 컬럼명
- Opportunity 별도 테이블 생성 여부
- endpoint 이름
- 파일 / 클래스 이름
- 파생값 저장 vs 요청 시 계산
- `attend`의 정확한 `action_window` 정책
- model API 응답 wrapping 형태
- migration 구성
- `new_to_user` 저장 방식
- Calendar conflict 구현 방식

즉 이 문서는 위 설계 판단을 요청드리는 것이 아니라,  
**추천 기능 연결에 필요한 정보와 동작만 공유드리는 문서**입니다.
