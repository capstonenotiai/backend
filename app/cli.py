"""
관리용 명령어

  python -m app.cli init-db                      테이블 생성
  python -m app.cli seed                         데모 일정 6건 (프론트 mock 과 동일)
  python -m app.cli import <crawled.jsonl>       크롤러 결과 파일 → notices
  python -m app.cli extract [--limit N] [--extractor stub|gpt|model_api]
                                                 미추출 notices → events
  python -m app.cli collect [--site cbnu]        크롤러 실행 + import + extract (MODEL_REPO_PATH 필요)
"""
import argparse

from app.db import SessionLocal, init_db
from app.seed import seed
from app.services.extractor import get_extractor
from app.services.pipeline import collect, extract_pending, import_records, read_jsonl


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db")
    sub.add_parser("seed")
    p_import = sub.add_parser("import")
    p_import.add_argument("path")
    p_extract = sub.add_parser("extract")
    p_extract.add_argument("--limit", type=int)
    p_extract.add_argument("--extractor")
    p_collect = sub.add_parser("collect")
    p_collect.add_argument("--site", choices=["cbnu", "wevity", "contestkorea"])
    args = parser.parse_args()

    init_db()
    db = SessionLocal()
    try:
        if args.command == "init-db":
            print("테이블 생성 완료")
        elif args.command == "seed":
            print(f"데모 일정 {seed(db)}건 추가")
        elif args.command == "import":
            added = import_records(db, read_jsonl(args.path))
            print(f"새 공지 {sum(added.values())}건: {dict(added)}")
        elif args.command == "extract":
            count = extract_pending(db, get_extractor(args.extractor), args.limit)
            print(f"추출 {count}건")
        elif args.command == "collect":
            print(collect(db, args.site))
    finally:
        db.close()


if __name__ == "__main__":
    main()
