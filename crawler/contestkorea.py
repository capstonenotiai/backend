"""
ContestKorea 크롤러
목록: http://contestkorea.com/sub/list.php?int_gbn=1&Txt_bcode={code}&page={page}
상세: http://contestkorea.com/sub/view.php?int_gbn=1&Txt_bcode={code}&str_no={no}

주요 카테고리 코드:
  030110001 - 공모전 전체
  030120001 - 대외활동
"""
import os
import re
import sys
from urllib.parse import urljoin, urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from crawler.base import get, soup, clean_text
from app.services.notice_metadata import publication_from_metadata, iso_publication

BASE = "http://contestkorea.com"
LIST_URL = BASE + "/sub/list.php?int_gbn=1&Txt_bcode={code}&page={page}"
CATEGORIES = {
    "문학·문예":        "030110001",
    "네이밍·슬로건":    "030210001",
    "학문·과학·IT":     "030310001",
    "미술·디자인·웹툰": "030610001",
    "스포츠":           "030810001",
    "음악·콩쿠르·댄스": "030910001",
    "사진·영상·영화제": "031210001",
    "아이디어·건축·창업":"031410001",
    "요리·뷰티·오디션": "031610001",
    "기타공모전":       "031810001",
    "서포터즈·기자단":  "040110001",
    "교육·멘토링":      "040410001",
    "전시·행사·축제":   "040510001",
    "기타대외활동":     "040610001",
    "기획·홍보·마케팅": "040710001",
}
SITE = "contestkorea"


# ── 목록 페이지 파싱 ──────────────────────────────────────────────────────────

_DDAY_PAT = re.compile(r"^D[+\-]\d+$")


def parse_list_page(page: int, code: str) -> list[dict]:
    url = LIST_URL.format(code=code, page=page)
    resp = get(url)
    if not resp:
        return []

    bs = soup(resp)
    items = []
    seen = set()

    for a in bs.select("a"):
        href = a.get("href", "")
        if "str_no=" not in href or "view.php" not in href:
            continue

        # urljoin에 실제 페이지 URL 사용 → /sub/ 경로 올바르게 처리
        full_url = urljoin(url, href)
        if full_url in seen:
            continue

        title = a.get_text(strip=True)
        # D-day 배지 링크 제외
        if not title or _DDAY_PAT.match(title) or len(title) < 4:
            continue

        seen.add(full_url)

        parent = a.find_parent("li") or a.find_parent("tr")
        date_txt = ""
        if parent:
            spans = parent.find_all(string=re.compile(r"\d{4}[.\-]\d{2}"))
            date_txt = " ".join(s.strip() for s in spans[:2])

        items.append({
            "source_url": full_url,
            "title_raw": title,
            "list_date_raw": date_txt,
            "category_code": code,
        })

    return items


# ── 상세 페이지 파싱 ──────────────────────────────────────────────────────────

def parse_detail_page(url: str) -> dict | None:
    resp = get(url)
    if not resp:
        return None

    bs = soup(resp)
    result = {"source_url": url, "site": SITE}

    # 제목: 첫 번째 h1 (의미 있는 텍스트)
    title_raw = ""
    for h1 in bs.select("h1"):
        t = h1.get_text(strip=True)
        if len(t) > 5:
            title_raw = t
            break
    if not title_raw:
        for sel in (".view_title h4", ".tit_area h2", ".view_tit", "h4.tit"):
            el = bs.select_one(sel)
            if el:
                title_raw = el.get_text(strip=True)
                break
    result["title_raw"] = title_raw

    # 메타 정보 — table tr 구조 (대회지역, 접수기간 등)
    meta = {}
    for row in bs.select("table tr"):
        th = row.select_one("th")
        td = row.select_one("td")
        if th and td:
            key = th.get_text(strip=True)
            val = td.get_text(separator=" ", strip=True)
            if key:
                meta[key] = val

    result["meta"] = meta
    published = bs.select_one('meta[property="article:published_time"], meta[itemprop="datePublished"], time[itemprop="datePublished"][datetime], .published time[datetime]')
    result['published_at'] = publication_from_metadata(meta) or (
        iso_publication(published.get('content') or published.get('datetime')) if published else None)

    # 본문
    for tag in bs.select("script, style, .ad, nav, header, footer, .relate, .share"):
        tag.decompose()

    body_el = (
        bs.select_one(".view_detail_area")
        or bs.select_one(".view_cont_area")
        or bs.select_one(".view_content")
        or bs.select_one(".con_detail")
        or bs.select_one("#viewContent")
    )
    result["raw_text"] = clean_text(body_el.get_text(separator="\n")) if body_el else ""

    # 장소 힌트: 대회지역이 실제 행사 지역
    for key in ("대회지역", "장소", "행사장소", "행사 장소", "개최지", "개최장소", "행사지역"):
        if key in meta:
            result["location_hint"] = meta[key]
            break

    # 기간 힌트
    for key in ("접수기간", "행사기간", "공모기간", "신청기간", "모집기간", "기간"):
        if key in meta:
            result["period_hint"] = meta[key]
            break

    return result


# ── 페이지 수 탐지 ────────────────────────────────────────────────────────────

def get_total_pages(code: str) -> int:
    resp = get(LIST_URL.format(code=code, page=1))
    if not resp:
        return 1
    bs = soup(resp)
    last = 1
    for a in bs.select(".paging a, .pagination a, .page_num a"):
        txt = a.get_text(strip=True)
        if txt.isdigit():
            last = max(last, int(txt))
    return last


# ── 전체 크롤 ─────────────────────────────────────────────────────────────────

def crawl(max_pages: int = 999, existing_urls: set = None,
          categories: list = None) -> list[dict]:
    existing_urls = existing_urls or set()
    categories = categories or list(CATEGORIES.values())
    results = []

    for code in categories:
        cat_name = {v: k for k, v in CATEGORIES.items()}.get(code, code)
        total = min(get_total_pages(code), max_pages)
        print(f"[ContestKorea/{cat_name}] 총 {total} 페이지 크롤 시작")

        for page in range(1, total + 1):
            print(f"  목록 {page}/{total}")
            items = parse_list_page(page, code)
            if not items:
                print(f"  [종료] 페이지 {page} 항목 없음")
                break

            new_count = 0
            for item in items:
                url = item["source_url"]
                if url in existing_urls:
                    continue
                detail = parse_detail_page(url)
                if detail:
                    detail.update({
                        "title_raw": detail.get("title_raw") or item["title_raw"],
                        "list_date_raw": item.get("list_date_raw", ""),
                        "category": cat_name,
                    })
                    results.append(detail)
                    existing_urls.add(url)
                    new_count += 1
                    print(f"    ✓ {detail['title_raw'][:50]}")

            if new_count == 0:
                print(f"  [종료] 기존 데이터와 전부 중복")
                break

    print(f"[ContestKorea] 수집 완료: {len(results)}건")
    return results


if __name__ == "__main__":
    import json, os
    out = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "crawled_contestkorea.jsonl")
    items = crawl(max_pages=5)
    with open(out, "w", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    print(f"저장: {out} ({len(items)}건)")
