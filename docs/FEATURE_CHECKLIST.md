# 백엔드 기능 체크리스트

기준: TASK_B10, 현재 feature/service-v9. 정책은 capstone의 `docs/policy/POLICY_DECISIONS.md`, 명세는 이 저장소의 `docs/recommendation-backend-requirements.md` 22절이다.
완료는 해당 백엔드 범위의 구현·검증을 뜻한다. 부분은 현재 구현의 제한 또는 후속 작업이 있고, 미구현은 임시값만 제공하는 경우다. 프론트 표시와 실제 배포는 이번 작업 범위에 포함되지 않는다.
아래 테스트명은 매개변수화 테스트의 원래 함수 이름이다. 외부 네트워크·실제 GPT·SMTP를 호출하지 않는다.

## 서비스 표시 규칙 1~10 및 수집·추출

| 항목 | 근거(정책 번호 또는 명세 절) | 구현 위치(파일·함수) | 테스트(파일::테스트 이름) | 상태(완료/부분/미구현) |
|---|---|---|---|---|
| 공지별 대표 1개, 가장 가까운 미마감 접수 | 표시 1 | app/services/display_rules.py::representative; app/services/events.py::list_events | tests/test_display_rules.py::test_select_nearest_open_application_not_first_row | 완료 |
| 접수 없으면 본행사, 모든 접수 마감 시 목록 제외 | 표시 1 | app/services/display_rules.py::representative | tests/test_display_rules.py::test_no_application_selects_main_not_interview_or_result; tests/test_display_rules.py::test_closed_notice_hidden_despite_bookmark_registration_and_retention | 완료 |
| 공지의 모든 공개 일정 상세 및 원문 링크 | 표시 2·5 | app/services/events.py::event_detail, to_event_out | tests/test_display_rules.py::test_bundle_and_interview_detail_and_results; tests/test_api_contract.py::test_frontend_response_keys | 완료 |
| 공지 묶음 캘린더 등록·해제, 면접 자동 등록 제외·개별 추가 | 표시 3 | app/routers/calendar.py::register, unregister, register_interview | tests/test_display_rules.py::test_bundle_and_interview_detail_and_results; tests/test_service_v9.py::test_interview_exclusion_user_override_google_and_isolation | 완료 |
| 접수 관심 저장만으로 알림, 본행사는 등록한 경우 | 표시 4 | app/services/notifications.py::notification_candidates, due_notifications | tests/test_notifications.py::test_bookmarked_deadline_d3_and_d1_once_each; tests/test_notifications.py::test_unfollowed_and_main_events_need_registration | 완료 |
| 카드용 제목·출처·기간·장소·D-day 계산용 날짜 제공 | 표시 5 | app/services/events.py::to_event_out | tests/test_api_contract.py::test_frontend_response_keys | 완료 (카드 배치·문구는 프론트 범위) |
| 일반 일정 즉시 등록, 확인 필요만 confirmed 요구 | 표시 5 | app/services/review.py::requires_confirmation; app/routers/calendar.py::_register_targets | tests/test_registration_policy.py::test_normal_ai_bundle_registers_without_confirmation; tests/test_registration_policy.py::test_sibling_review_requires_confirmation_before_any_writes | 완료 |
| 등록 후 개인 날짜·시각·장소 수정과 sync | 표시 5 | app/services/user_schedule.py::validate_overrides; app/routers/events.py::edit_schedule; app/routers/calendar.py::sync | tests/test_service_v9.py::test_override_patch_reset_and_sync_effective_values | 완료 |
| 관심 없음 저장 API 유지 | 표시 5·명세 5.2 | app/routers/events.py::dismiss | tests/test_service_v9.py::test_report_action_dismissed_and_restore | 완료 |
| 알림함 4 API·읽지 않은 개수·사용자 격리 | 표시 6 | app/routers/notifications.py; app/services/notifications.py::list_notifications, mark_read, mark_all_read | tests/test_notifications.py::test_inbox_api; tests/test_api_contract.py::test_frontend_response_keys | 완료 |
| D-3·D-1 날짜 기준 알림, 하루 중복 방지, 09:00 서울 스케줄 | 표시 6 | app/services/notifications.py::generate_notifications; app/scheduler.py::start_scheduler | tests/test_notifications.py::test_bookmarked_deadline_d3_and_d1_once_each; tests/test_notifications.py::test_notification_scheduler_uses_seoul_nine_am | 완료 |
| 확인 필요 알림 기본 제외·설정으로 활성화 | 표시 6 | app/services/notifications.py::due_notifications | tests/test_notifications.py::test_excluded_schedules; tests/test_notifications.py::test_review_required_can_be_enabled_later | 완료 |
| 이메일+알림함, SMTP/console 전환·수신 설정·재시도 | 표시 6 | app/services/email.py::send_email; app/services/notifications.py::send_pending_emails | tests/test_notifications.py::test_one_digest_email_per_user; tests/test_notifications.py::test_email_opt_out_keeps_inbox; tests/test_notifications.py::test_email_failure_retries_then_fails_and_stale_is_skipped; tests/test_notifications.py::test_smtp_backend_requires_credentials | 완료 (실제 Gmail 발송은 본 작업에서 검증하지 않음) |
| Facts→모드 Payload→GPT→서버 출력 검증 연결 | 표시 7 | app/routers/planner.py::recommendations; app/services/planner_recommendations.py::recommend | tests/test_planner_recommendations.py::test_api_normal_path_and_server_facts; tests/test_planner_recommendations.py::test_sdk_strict_request_privacy_and_model | 완료 |
| 기존 자유형 채팅 유지, 새 프론트에서 사용 결정 | 표시 7·TASK_B10 | app/routers/planner.py::chat | tests/test_planner_opportunities.py::test_chat_mode_compatibility | 부분 (기존 동작 유지, 신규 화면 적용은 후속 결정) |
| 백엔드 완성→API 동결→PR·원본 DB→프론트 작업 순서 | 표시 8 | docs/API.md; tests/test_api_contract.py; docs/FEATURE_CHECKLIST.md | tests/test_api_contract.py::test_openapi_routes (동결만); 후속 PR·DB·프론트는 테스트 없음 | 부분 (동결까지, PR·DB 반영·프론트 미수행) |
| 확인 필요 정보 및 부드러운 안내 반환 | 표시 9 | app/services/review.py::confirmation_reason; app/services/events.py::to_event_out; app/routers/calendar.py::_register_targets | tests/test_registration_policy.py::test_sibling_review_requires_confirmation_before_any_writes; tests/test_registration_policy.py::test_old_generic_status_warning_does_not_trigger_confirmation | 완료 (화면 표시 확정은 프론트 범위) |
| PC 서버 운영·도메인 후속 준비 | 표시 10 | README.md (실행 안내); Dockerfile; docker-compose.yml | 테스트 없음 | 부분 (실행 수단은 존재, 실제 운영·도메인 검증 안 함) |
| 모델 서버 중단 대기열·시도 차감 방지·복구·재시도 | 추출 정책 10·TASK_B10 수집 범위 | app/services/pipeline.py::extract_pending, requeue_failed, queue_status | tests/test_service_v9.py::test_model_down_keeps_queue_without_using_attempts; tests/test_service_v9.py::test_retry_then_success_and_exhaustion; tests/test_service_v9.py::test_interrupted_extraction_is_retryable | 완료 |
| 게시일 보존·정규화·reference_time 전달, 게시일 없음은 null | 추출 정책 14 | app/services/notice_metadata.py; app/services/pipeline.py::import_records; app/services/extractor.py::ModelApiExtractor | tests/test_service_v9.py::test_publication_normalization; tests/test_service_v9.py::test_crawler_metadata_import_preserves_source_and_null; tests/test_service_v9.py::test_reference_time_sent_and_warning_publication | 완료 (연도 추론은 모델 책임) |

## 명세 22절 P0 (18개)

| 항목 | 근거(정책 번호 또는 명세 절) | 구현 위치(파일·함수) | 테스트(파일::테스트 이름) | 상태(완료/부분/미구현) |
|---|---|---|---|---|
| Notice 1건에서 Event 여러 개 저장 | 명세 22 P0·3 | app/models.py::Notice, Event; app/services/pipeline.py::extract_pending | tests/test_admin_review.py::test_v2_multiple_events_and_time_body | 완료 |
| Opportunity grouping key | 명세 22 P0·3 | app/services/planner_facts.py::opportunity_facts (o+notice.id) | tests/test_planner_opportunities.py::test_opportunity_states_visible_events_and_expired_application | 완료 (Notice를 부모로 사용) |
| major / grade / enrollment_status | 명세 22 P0·4 | app/models.py::Preference; app/schemas.py::RecommendationProfile | tests/test_planner_opportunities.py::test_profile_validation; tests/test_planner_opportunities.py::test_profile_grade_bounds | 완료 |
| 프로필 조회/수정 가능 | 명세 22 P0·4 | app/routers/users.py::get_profile, put_profile | tests/test_planner_opportunities.py::test_profile_defaults_roundtrip_and_preferences_preserve_profile | 완료 |
| Event pending / done | 명세 22 P0·5.1 | app/models.py::UserEvent; app/services/user_schedule.py::ActionIn | tests/test_service_v9.py::test_report_action_dismissed_and_restore | 완료 |
| 완료/완료취소 가능 | 명세 22 P0·5.1 | app/routers/events.py::set_action_status | tests/test_service_v9.py::test_report_action_dismissed_and_restore | 완료 |
| Enrichment 저장 | 명세 22 P0·6 | app/models.py::NoticeEnrichment; app/services/enrichment.py::enrich_pending | tests/test_enrichment.py::test_normal_storage_ids_input_privacy_and_opportunity | 완료 |
| raw_text evidence validation | 명세 22 P0·6.2 | app/services/enrichment.py::normalize_whitespace, evidence_valid, validate_enrichment | tests/test_enrichment.py::test_whitespace_normalized_and_title_evidence; tests/test_enrichment.py::test_missing_evidence_drops_only_general_fact; tests/test_enrichment.py::test_critical_failure_uses_defaults_and_low_confidence | 부분 (명세의 raw_text literal만이 아니라 제목+본문, 공백 정규화 후 substring 검증) |
| fact_id 생성·활동 범위 참조 검증 | 명세 22 P0·6.2 | app/services/enrichment.py::validate_enrichment; app/services/planner_validation.py::validate_output | tests/test_enrichment.py::test_normal_storage_ids_input_privacy_and_opportunity; tests/test_planner_recommendations.py::test_fact_refs_must_belong_to_activity_and_text_arrays_repaired | 완료 |
| eligibility | 명세 22 P0·7 | app/services/planner_context.py::eligibility, requirement_comparisons | tests/test_planner_context.py::test_eligibility; tests/test_planner_context.py::test_eligibility_missing_unparsed_grouping_and_failed_evidence | 완료 |
| interest_match | 명세 22 P0·8 | app/services/planner_context.py::interest_match | tests/test_planner_context.py::test_interest_direct_related_ownership_and_title_ignored | 완료 |
| expired / action_date / action_window / urgency | 명세 22 P0·9 | app/services/planner_facts.py::derived_event_facts | tests/test_planner_opportunities.py::test_action_mapping; tests/test_planner_opportunities.py::test_urgency_boundaries; tests/test_planner_opportunities.py::test_application_override_updates_all_derived_values | 완료 |
| focus_event_id / next_step_type / verify_target | 명세 22 P0·10 | app/services/planner_context.py::focus_event, priority_context | tests/test_planner_context.py::test_priority_rules; tests/test_planner_context.py::test_priority_precedence_and_no_eligibility_override; tests/test_planner_context.py::test_focus_done_move_numeric_ties_unknown_dates_and_confirmation_fallback | 완료 |
| PRIORITY 정렬 | 명세 22 P0·11 | app/services/planner_context.py::priority_sort_key, select_candidates | tests/test_planner_context.py::test_priority_sort_all_levels_and_irrelevant_fields; tests/test_planner_context.py::test_sort_urgency_all_bands_before_other_signals_and_notice_ties | 부분 (긴급도→필수→조기 마감→관리→window는 구현; 동률은 기존 순서 유지 대신 action_date·공지 ID 정렬) |
| DISCOVER / FOCUS Gate | 명세 22 P0·12 | app/services/planner_context.py::_discover_candidate, select_candidates | tests/test_planner_context.py::test_candidate_filters; tests/test_planner_context.py::test_focus_conflicts_only_selected_candidates_and_grouping_not_capped | 완료 |
| priority / discover / focus Mode | 명세 22 P0·14 | app/schemas.py::AI_MODE_IDS; app/services/planner_recommendations.py::parse_request | tests/test_planner_recommendations.py::test_api_invalid_modes_400; tests/test_planner_opportunities.py::test_old_stored_modes_read_as_priority | 완료 |
| Mode별 Payload | 명세 22 P0·15·4 개인정보 | app/services/planner_context.py::_payload_opportunity, _build_context, build_recommendation_context | tests/test_planner_context.py::test_payload_minimal_fields_verified_facts_single_bulk_call; tests/test_planner_recommendations.py::test_sdk_strict_request_privacy_and_model; tests/test_planner_recommendations.py::test_contacts_masked_in_payload | 완료 |
| Structured Output 검증 | 명세 22 P0·16 | app/services/planner_prompts.py::schema_for; app/services/planner_validation.py::validate_output | tests/test_planner_recommendations.py::test_valid_outputs_and_strict_schemas; tests/test_planner_recommendations.py::test_missing_duplicate_unknown_and_untrusted_fields; tests/test_planner_recommendations.py::test_discover_caps; tests/test_planner_recommendations.py::test_focus_relations_conflicts_and_secondary_reasons | 완료 |

## 명세 22절 P1 (5개) / P2 (1개)

| 항목 | 근거(정책 번호 또는 명세 절) | 구현 위치(파일·함수) | 테스트(파일::테스트 이름) | 상태(완료/부분/미구현) |
|---|---|---|---|---|
| dismissed | 명세 22 P1·5.2 | app/models.py::UserNotice; app/services/planner_facts.py::opportunity_context | tests/test_planner_opportunities.py::test_opportunity_states_visible_events_and_expired_application; tests/test_planner_context.py::test_candidate_filters | 완료 |
| 관심없음/취소 가능 | 명세 22 P1·5.2 | app/routers/events.py::dismiss | tests/test_service_v9.py::test_report_action_dismissed_and_restore | 완료 |
| user_managed | 명세 22 P1·5.3 | app/services/planner_facts.py::opportunity_context | tests/test_planner_opportunities.py::test_opportunity_states_visible_events_and_expired_application | 부분 (현재 bookmarked/registered에서 계산, 과거 선택 이력은 유지하지 않음) |
| Calendar conflict | 명세 22 P1·13 | app/services/planner_facts.py::opportunity_context, opportunity_facts, schedule_interval | tests/test_planner_opportunities.py::test_conflicts_registered_attend_only_overrides_self_and_other_users | 부분 (서비스 등록 일정끼리 확인, Google 외부 일정 조회 및 OAuth 미연동 여부 반영 없음) |
| pairwise_conflicts | 명세 22 P1·13 | app/services/planner_facts.py::pairwise_conflicts; app/services/planner_context.py::_build_context | tests/test_planner_opportunities.py::test_pairwise_conflicts_effective_dates_and_application_exclusion; tests/test_planner_context.py::test_focus_conflicts_only_selected_candidates_and_grouping_not_capped | 완료 |
| new_to_user | 명세 22 P2·17 | app/services/planner_context.py::_payload_opportunity (null) | tests/test_planner_context.py::test_payload_minimal_fields_verified_facts_single_bulk_call (null 임시값만); 실제 노출 추적 테스트 없음 | 미구현 (명세에서 허용한 null 임시값) |

## TASK_B10 동결·문장 개선

| 항목 | 근거(정책 번호 또는 명세 절) | 구현 위치(파일·함수) | 테스트(파일::테스트 이름) | 상태(완료/부분/미구현) |
|---|---|---|---|---|
| 34개 메서드·경로 추가/삭제 감지 | 표시 8·TASK_B10 §2 | tests/snapshots/api_routes.json | tests/test_api_contract.py::test_openapi_routes | 완료 |
| 주요 프론트 응답 키 및 세 추천 모드 항목 고정 | 표시 8·TASK_B10 §2 | tests/snapshots/api_response_keys.json | tests/test_api_contract.py::test_frontend_response_keys | 완료 |
| 오류 {message}, 입력 검증 {message, errors} 고정 | TASK_B10 §2 | app/main.py::http_error, validation_error | tests/test_api_contract.py::test_error_response_keys | 완료 (실제 현 계약 유지) |
| PRIORITY 사용자 관점 이유·방법/준비물/조건 fact_refs 지시 | 표시 7·TASK_B10 §4 | app/services/planner_prompts.py::MODE_PROMPTS, PLANNER_PROMPT_VERSION (planner-v3) | tests/test_planner_recommendations.py::test_priority_prompt_user_reasons_and_required_fact_references | 완료 (프롬프트 지시 검증, 실제 GPT 출력 품질은 미측정) |

부분/미구현 항목은 동작을 바꾸지 않고 기록했다. 명세와 구현 간 판단이 필요한 제한은 후속 검토 대상으로 남긴다.
