"""
Wevity 크롤러 (curl_cffi 사용 — Cloudflare 우회)
상세: https://www.wevity.com/?c=find&s=1&gub=1&gbn=view&gp=1&ix={ix}

목록 페이지는 JavaScript 렌더링 필요 + Cloudflare 차단으로 직접 접근 불가.
대신 최근 ix 번호 범위를 스캔하는 방식으로 크롤.
"""
import re
import sys
import time
import random
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from crawler.base import soup, clean_text

try:
    from curl_cffi import requests as cffi_requests
    _SESSION = cffi_requests.Session(impersonate="chrome124")
except ImportError:
    cffi_requests = None
    _SESSION = None

BASE = "https://www.wevity.com"
DETAIL_URL = BASE + "/?c=find&s=1&gub=1&gbn=view&gp=1&ix={ix}"
SITE = "wevity"
HEADERS = {"Accept-Language": "ko-KR,ko;q=0.9"}


def _get(url: str, retries: int = 3) -> str | None:
    """curl_cffi로 Cloudflare 우회 HTTP GET"""
    if _SESSION is None:
        return None
    for attempt in range(retries):
        try:
            time.sleep(1.0 + random.uniform(0, 0.5))
            resp = _SESSION.get(url, headers=HEADERS, timeout=15)
            if resp.status_code == 200 and len(resp.text) > 500:
                return resp.text
        except Exception as e:
            if attempt == retries - 1:
                print(f"  [Wevity GET 오류] {url[:60]}: {e}")
    return None


def _find_max_ix() -> int:
    """목록 페이지에서 알려진 ix 번호를 추출하거나 기본값 반환"""
    html = _get(f"{BASE}/?c=find&s=1&gub=1&gbn=view&gp=1")
    if html:
        bs = soup(type("R", (), {"text": html, "apparent_encoding": "utf-8"})())
        ix_links = [a for a in bs.select("a") if "ix=" in a.get("href", "")]
        for a in ix_links:
            m = re.search(r"ix=(\d+)", a.get("href", ""))
            if m:
                return int(m.group(1))
    return 105566  # 알려진 최근 ix 기본값


def parse_detail_page(ix: int) -> dict | None:
    url = DETAIL_URL.format(ix=ix)
    html = _get(url)
    if not html:
        return None

    bs = soup(type("R", (), {"text": html, "apparent_encoding": "utf-8"})())
    result = {"source_url": url, "site": SITE}

    # 제목
    title_el = bs.select_one(".tit") or bs.select_one(".view_tit") or bs.select_one("h4")
    title_raw = title_el.get_text(strip=True) if title_el else ""
    if not title_raw or len(title_raw) < 8:
        return None  # 유효하지 않은 페이지 (네비게이션 등)
    result["title_raw"] = title_raw

    # 메타 정보
    meta = {}
    content_el = bs.select_one(".content")
    if content_el:
        raw = content_el.get_text(separator="\n")
        for line in raw.split("\n"):
            m = re.match(
                r"^(분야|응모대상|주최|주관|기간|접수기간|행사기간|장소|행사장소|시상내역|후원)\s*[:\s]\s*(.+)",
                line.strip(),
            )
            if m:
                meta[m.group(1)] = m.group(2).strip()
    result["meta"] = meta

    # 본문
    for tag in bs.select("script, style, .ad, .banner, nav, footer, .sns-wrap"):
        tag.decompose()

    body_el = (
        bs.select_one(".view_con")
        or bs.select_one(".con_detail")
        or bs.select_one(".content")
    )
    result["raw_text"] = clean_text(body_el.get_text(separator="\n")) if body_el else ""

    # 장소 힌트
    for key in ("장소", "행사장소", "행사 장소"):
        if key in meta:
            result["location_hint"] = meta[key]
            break

    # 기간 힌트
    for key in ("접수기간", "기간", "행사기간"):
        if key in meta:
            result["period_hint"] = meta[key]
            break

    return result


def crawl(max_items: int = 500, existing_urls: set = None, start_ix: int = None) -> list[dict]:
    """최근 ix 번호부터 하향 스캔하며 공모전 수집"""
    if cffi_requests is None:
        print("[Wevity] curl_cffi 미설치. pip install curl_cffi")
        return []

    existing_urls = existing_urls or set()
    results = []

    if start_ix is None:
        start_ix = _find_max_ix()
    print(f"[Wevity] ix={start_ix}부터 스캔 (최대 {max_items}건)")

    consecutive_miss = 0
    ix = start_ix

    while ix > 0 and len(results) < max_items:
        url = DETAIL_URL.format(ix=ix)

        if url in existing_urls:
            ix -= 1
            consecutive_miss = 0
            continue

        detail = parse_detail_page(ix)
        if detail:
            results.append(detail)
            existing_urls.add(url)
            consecutive_miss = 0
            print(f"  ✓ [{ix}] {detail['title_raw'][:50]}")
        else:
            consecutive_miss += 1
            # 연속 50개 실패 시 종료 (너무 오래된 범위)
            if consecutive_miss >= 50:
                print(f"  [종료] 연속 {consecutive_miss}개 빈 페이지")
                break

        ix -= 1

    print(f"[Wevity] 수집 완료: {len(results)}건")
    return results


if __name__ == "__main__":
    import json
    out = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "crawled_wevity.jsonl")
    items = crawl(max_items=50)
    with open(out, "w", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    print(f"저장: {out} ({len(items)}건)")
