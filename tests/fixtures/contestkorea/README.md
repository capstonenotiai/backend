2026-10-18 ContestKorea 공개 페이지에서 수집한 고정 HTML 조각.

- 목록: https://www.contestkorea.com/sub/list.php?int_gbn=2&Txt_bcode=040410001&page=1 (다른 파일은 파일명의 분류 코드 사용, 03은 int_gbn=1)
- 상세: https://www.contestkorea.com/sub/view.php?int_gbn=2&Txt_bcode=040110001&str_no=202610160008
- 목록은 본문, 옆 칸 일부, 페이지 이동 구조를 보존했다. 이미지·주최 정보·스크립트·주석·연락처는 제거했다.
- 상세는 제목, 대표분야, 접수기간, 연락처 없는 본문 앞부분만 보존했다.
- 교육 목록은 12건, 문학과 서포터즈 목록은 일반 12건 + 특별 노출 1건이다. 본문 항목을 임의로 12건으로 자르지 않는다.
- 테스트는 저장된 파일만 사용하며 네트워크를 호출하지 않는다.

B15-2 (2026-10-18): 상세 제목·대표분야만 보존한 샘플 추가. 추가 HTTP 요청 총 4회, 요청 간격 1.6초 이상.
- detail_field_030110001.html: https://www.contestkorea.com/sub/view.php?int_gbn=1&Txt_bcode=030110001&str_no=202602240031 — 문학•문예
- detail_field_040410001.html: https://www.contestkorea.com/sub/view.php?int_gbn=2&Txt_bcode=040410001&str_no=202610160011 — 교육 • 강연 • 멘토링 • 세미나
- 기존 detail_activity.html의 서포터즈 • 기자단도 함께 검증한다. 본문·연락처는 추가 저장하지 않았다.
