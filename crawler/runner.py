"""
크롤러 실행기 — 세 사이트 순차 크롤링 후 data/crawled_all.jsonl 저장
사용법:
  python crawler/runner.py               # 전체 (사이트 기본 max_pages)
  python crawler/runner.py --max 5       # 사이트당 최대 5 페이지
  python crawler/runner.py --site wevity # 특정 사이트만
"""
import argparse
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
OUT_FILE = os.path.join(DATA_DIR, "crawled_all.jsonl")
# 사이트별 실행 결과 (백엔드 app/services/pipeline.py 가 읽어서 수집 기록에 반영)
SUMMARY_FILE = os.path.join(DATA_DIR, "last_run.json")


def load_existing_urls(path: str) -> set:
    urls = set()
    if not os.path.exists(path):
        return urls
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if "source_url" in obj:
                    urls.add(obj["source_url"])
            except json.JSONDecodeError:
                pass
    return urls


def append_results(path: str, items: list[dict]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")


def run_wevity(max_pages: int, existing_urls: set) -> list[dict]:
    from crawler.wevity import crawl
    # max_pages를 max_items로 매핑 (Wevity는 페이지 대신 개수로 제어)
    max_items = max_pages * 20 if max_pages < 999 else 500
    return crawl(max_items=max_items, existing_urls=existing_urls)


def run_contestkorea(max_pages: int, existing_urls: set) -> list[dict]:
    from crawler.contestkorea import crawl
    return crawl(max_pages=max_pages, existing_urls=existing_urls)


def run_cbnu(max_pages: int, existing_urls: set) -> list[dict]:
    from crawler.cbnu import crawl, EXTRA_BOARDS
    return crawl(max_pages=max_pages, existing_urls=existing_urls, boards=EXTRA_BOARDS)


SITES = {
    "wevity": run_wevity,
    "contestkorea": run_contestkorea,
    "cbnu": run_cbnu,
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--max", type=int, default=999, help="사이트당 최대 페이지 수")
    parser.add_argument("--site", choices=list(SITES.keys()), default=None,
                        help="특정 사이트만 크롤 (기본: 전체)")
    args = parser.parse_args()

    existing_urls = load_existing_urls(OUT_FILE)
    print(f"[Runner] 기존 URL {len(existing_urls)}개 로드 ({OUT_FILE})")

    targets = [args.site] if args.site else list(SITES.keys())
    total_new = 0
    summary = {}

    from crawler import base

    for site in targets:
        fn = SITES[site]
        print(f"\n{'='*50}")
        print(f"[Runner] {site} 크롤 시작 (max_pages={args.max})")
        print(f"{'='*50}")
        base.FAILED_REQUESTS.clear()
        result = {"status": "done", "new": 0, "failed_requests": [], "error": None}
        try:
            if site == "wevity":
                from crawler import wevity
                if wevity._SESSION is None:
                    raise RuntimeError("curl_cffi 가 설치되지 않아 Wevity 에 접속할 수 없습니다")
            items = fn(args.max, existing_urls)
            if items:
                append_results(OUT_FILE, items)
                total_new += len(items)
                print(f"[Runner] {site} → {len(items)}건 저장")
            else:
                print(f"[Runner] {site} → 새 항목 없음")
            result["new"] = len(items)
        except Exception as e:
            print(f"[Runner] {site} 오류: {e}")
            import traceback
            traceback.print_exc()
            result["status"], result["error"] = "failed", f"{e.__class__.__name__}: {e}"[:500]

        # 요청이 실패했는데 새 항목도 없으면 사이트에 접속하지 못한 것으로 본다
        result["failed_requests"] = list(base.FAILED_REQUESTS)
        if result["status"] == "done" and result["new"] == 0 and result["failed_requests"]:
            result["status"] = "failed"
        summary[site] = result

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(SUMMARY_FILE, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(f"\n[Runner] 완료. 총 {total_new}건 추가 → {OUT_FILE}")
    failed = [site for site, result in summary.items() if result["status"] == "failed"]
    if failed:
        print(f"[Runner] 실패한 사이트: {', '.join(failed)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
