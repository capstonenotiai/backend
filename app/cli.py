"""
관리용 명령어

  python -m app.cli init-db                      테이블 생성
  python -m app.cli seed                         데모 일정 6건 (프론트 mock 과 동일)
  python -m app.cli import <crawled.jsonl>       크롤러 결과 파일 → notices
  python -m app.cli extract [--limit N] [--extractor stub|gpt|model_api] [--requeue-failed]
                                                 미추출 notices → events (--requeue-failed: 실패 공지를 다시 대기열에)
  python -m app.cli collect [--site cbnu]        크롤러 실행 + import + extract (MODEL_REPO_PATH 필요)
  python -m app.cli enrich [--limit N] [--requeue-failed]  공개 일정이 있는 공지 보강
  python -m app.cli usage [--days 7]             OpenAI 호출 수·토큰 합계 (날짜·종류·모델별)
  python -m app.cli inspect-extraction <notice_id>...  모델 원출력·저장 일정 날짜/시각 비교
  python -m app.cli fill-application-end [--dry-run]   빈 접수 마감일을 사이트 정보로 보완
  python -m app.cli fix-application-end-offset [--dry-run]  사이트보다 하루 늦은 접수 마감일 보정
  python -m app.cli recategorize [--dry-run]      저장된 사이트 분류 근거로 Event.category 재분류
  python -m app.cli recategorize-contestkorea [--dry-run]  저장된 대표분야로 공지·일정 분류 보정
  python -m app.cli planner --email <사용자> --mode priority|discover|focus [--call-gpt]
                                                 플래너 입력(payload) 확인, --call-gpt 면 실제 추천 결과까지 (B 검증용)
"""
import argparse
import json

from app.db import SessionLocal, init_db
from app.seed import seed
from app.services.extractor import get_extractor
from app.services.pipeline import collect, extract_pending, import_records, read_jsonl, requeue_failed
from app.services.enrichment import enrich_pending, requeue_failed as requeue_enrichment_failed


def inspect_extraction(db, notice_ids):
    """Only allow date/time fields from known response envelopes into diagnostics."""
    from sqlalchemy import select
    from app.models import Event, Notice

    fields = ('start_date', 'end_date', 'start_time', 'end_time', 'timezone')
    wrappers = ('candidate', 'events', 'raw_prediction', 'final_prediction',
                'native_prediction', 'legacy_prediction')

    def dates(value, path='response', event_index=0):
        if isinstance(value, list):
            return [item for index, child in enumerate(value)
                    for item in dates(child, f'{path}[{index}]', index)]
        if not isinstance(value, dict):
            return []
        result = []
        row = {key: value[key] for key in fields
               if key in value and (value[key] is None or isinstance(value[key], str))}
        if row:
            result.append({'path': path, 'event_index': event_index, **row})
        for key in wrappers:
            if key in value:
                result.extend(dates(value[key], f'{path}.{key}', event_index))
        return result

    result = []
    for notice_id in notice_ids:
        notice = db.get(Notice, notice_id)
        if notice is None:
            result.append({'notice_id': notice_id, 'missing': True})
            continue
        raw = dates(notice.extraction_result)
        stored = db.scalars(select(Event).where(Event.notice_id == notice_id).order_by(Event.id)).all()
        # Compare each response layer by its event position, never by flattened layer order.
        # Admin changes / filtered events can change positions, so expose indices explicitly.
        comparison = []
        represented = set()
        for row in raw:
            index = row['event_index']
            represented.add(index)
            comparison.append({'extraction': row, 'stored_event': (
                {'event_id': stored[index].id, **{key: getattr(stored[index], key) for key in fields}}
                if index < len(stored) else None)})
        for index, event in enumerate(stored):
            if index not in represented:
                comparison.append({'extraction': None, 'stored_event': {
                    'event_id': event.id, **{key: getattr(event, key) for key in fields}}})
        result.append({'notice_id': notice_id, 'comparison': comparison})
    return json.dumps(result, ensure_ascii=False, indent=2)


def planner_preview(db, email: str, mode: str, call_gpt: bool) -> str:
    import json
    from sqlalchemy import select
    from app.models import User
    from app.services import planner_context, planner_recommendations
    from app.timeutil import now_local

    user = db.scalar(select(User).where(User.email == email))
    if not user:
        return f"사용자 없음: {email}"
    now = now_local()
    payload, server_items = planner_context.build_recommendation_context(db, user, mode, now)
    result = (planner_recommendations.recommend(user.id, payload, server_items, now) if call_gpt
              else {"payload": payload})
    return json.dumps(result, ensure_ascii=False, indent=2)


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db")
    sub.add_parser("seed")
    p_inspect = sub.add_parser('inspect-extraction', help='추출 원출력과 저장 일정의 날짜·시각만 비교')
    p_inspect.add_argument('notice_ids', type=int, nargs='+')
    p_fill = sub.add_parser('fill-application-end', help='비어 있는 접수 마감일을 사이트 정보로 보완')
    p_fill.add_argument('--dry-run', action='store_true')
    p_fix = sub.add_parser('fix-application-end-offset', help='사이트보다 하루 늦은 접수 마감일 보정')
    p_fix.add_argument('--dry-run', action='store_true')
    p_import = sub.add_parser("import")
    p_import.add_argument("path")
    p_extract = sub.add_parser("extract")
    p_extract.add_argument("--limit", type=int)
    p_extract.add_argument("--extractor")
    p_extract.add_argument("--requeue-failed", action="store_true")
    p_enrich = sub.add_parser("enrich")
    p_enrich.add_argument("--limit", type=int)
    p_enrich.add_argument("--requeue-failed", action="store_true")
    p_usage = sub.add_parser("usage")
    p_usage.add_argument("--days", type=int, default=7)
    p_planner = sub.add_parser("planner")
    p_planner.add_argument("--email", required=True)
    p_planner.add_argument("--mode", required=True, choices=["priority", "discover", "focus"])
    p_planner.add_argument("--call-gpt", action="store_true")
    p_collect = sub.add_parser("collect")
    p_collect.add_argument("--site", choices=["cbnu", "wevity", "contestkorea"])
    p_recategorize = sub.add_parser('recategorize')
    p_recategorize.add_argument('--dry-run', action='store_true')
    p_ck = sub.add_parser('recategorize-contestkorea')
    p_ck.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    # 진단·보완·재분류 명령은 스키마 초기화/마이그레이션을 실행하지 않는다.
    if args.command not in ('recategorize', 'recategorize-contestkorea', 'inspect-extraction', 'fill-application-end', 'fix-application-end-offset'):
        init_db()
    db = SessionLocal()
    try:
        if args.command == "init-db":
            print("테이블 생성 완료")
        elif args.command == 'inspect-extraction':
            print(inspect_extraction(db, args.notice_ids))
        elif args.command == 'fill-application-end':
            from app.services.application_end import fill_application_ends
            count = fill_application_ends(db, dry_run=args.dry_run)
            print(f"접수 마감일 보완 {'dry-run (저장 안 함)' if args.dry_run else '반영'}: {count}건")
        elif args.command == 'fix-application-end-offset':
            from app.services.application_end import fix_application_end_offsets
            changes = fix_application_end_offsets(db, dry_run=args.dry_run)
            print(f"접수 마감일 보정 {'dry-run (저장 안 함)' if args.dry_run else '반영'}: {len(changes)}건")
            for change in changes:
                print(f"event_id={change['event_id']}: {change['before']} -> {change['after']}")
        elif args.command == 'recategorize-contestkorea':
            from app.services.contestkorea import recategorize_contestkorea
            result = recategorize_contestkorea(db, args.dry_run)
            print('재분류 dry-run (저장 안 함)' if args.dry_run else '재분류 반영 완료')
            print(f"변경 공지 {result['changed']}건")
            for label, key in (('board', 'changes'), ('Event.category', 'event_changes')):
                for (old, new), count in sorted(result[key].items(), key=lambda item: str(item[0])):
                    print(f'{label}: {old}→{new} {count}건')
            print(f"판단 불가 공지 {result['skipped']}건")
            for title in result['examples']:
                print(f'변경 예시: {title}')
            for title in result['unknown_examples']:
                print(f'판단 불가 예시: {title}')
        elif args.command == 'recategorize':
            from app.services.category import recategorize
            result = recategorize(db, args.dry_run)
            print('재분류 dry-run (저장 안 함)' if args.dry_run else '재분류 반영 완료')
            for (site, old, new), count in sorted(result['changes'].items(), key=lambda item: str(item[0])):
                print(f'{site}: {old}→{new} {count}건')
            print(f"변경 일정 {sum(result['changes'].values())}건")
            for site in ('wevity', 'contestkorea'):
                print(f"{site}: 판단 불가 공지 {result['skipped'][site]}건, 메타 없음 공지 {result['missing_metadata'][site]}건")
        elif args.command == "seed":
            print(f"데모 일정 {seed(db)}건 추가")
        elif args.command == "import":
            added = import_records(db, read_jsonl(args.path))
            print(f"새 공지 {sum(added.values())}건: {dict(added)}")
        elif args.command == "extract":
            if args.requeue_failed:
                print(f"실패 공지 {requeue_failed(db)}건을 대기열에 다시 넣음")
            count = extract_pending(db, get_extractor(args.extractor), args.limit)
            print(f"추출 {count}건")
        elif args.command == "collect":
            print(collect(db, args.site))
        elif args.command == "usage":
            from app.services.openai_usage import summary
            rows = summary(db, args.days)
            for row in rows:
                print(f"{row['date']} {row['kind']:<15} {row['model']:<20} 호출 {row['calls']:>4}  "
                      f"입력 {row['input_tokens']:>9,}  출력 {row['output_tokens']:>9,}")
            if not rows:
                print("기록 없음")
        elif args.command == "planner":
            print(planner_preview(db, args.email, args.mode, args.call_gpt))
        elif args.command == "enrich":
            if args.requeue_failed:
                print(f"보강 실패 공지 {requeue_enrichment_failed(db)}건을 대기열에 다시 넣음")
            print(f"공지 보강 {enrich_pending(db, limit=args.limit)}건")
    finally:
        db.close()


if __name__ == "__main__":
    main()
