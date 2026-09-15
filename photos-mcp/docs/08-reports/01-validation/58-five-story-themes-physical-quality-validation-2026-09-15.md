# Story 5종 물리 표현·비율 목록 통합 품질 기록

작성: 2026-09-15. 상태: 운영 앱·모바일 웹 서비스 반영, 설치본 브라우저 최종 검증 중. Android 실기기 검증 미완료.

## 범위

03 스크롤 시네마 기본 유지, 01 고요한 추억 / 02 순간의 묶음 / 04 공간을 흐르는 사진 / 05 펼쳐보는 기억의 책을 모두 독립 테마로 선택한다. 색상 치환이 아닌 구조·조작 방식의 구분이며 저장된 Story·인물·GPS·원본을 재분석하거나 삭제하지 않는다. 기존 공유의 레거시 theme ID를 보존한다.

## 실제 화면 기반 개선 이력

| 회차 | 책 담당 | 공간 담당 | 목록·01/02 담당 |
|---|---|---|---|
| R1 | PC가 한 면이 되는 MediaQueryList 전달 오류, 사진 위 종이 질감, 하단 조작부 넘침 발견 | 고정 세로 프레임·과한 검정 여백·캡션 겹침 발견 | 전역 원형 버튼 스타일 상속과 정사각 썸네일 crop 발견 |
| R2 | PC 양면·모바일 한 면 확인, 실제 드래그에서 종이 조각 흰 이음선 발견 | flex-shrink로 가로 사진 폭이 눌리는 원인 확인 | 원본 비율 이미지 확인, 세로 대표 사진 과대 배치와 비율 반올림 오차 발견 |
| R3 | Canvas 앞뒷면 곡면으로 이음선 제거, 전환 후 HTML 복귀 | 좌우 이동 변환의 부호가 반대로 적용돼 사진이 중앙에 겹치는 문제 계측 | subpixel 비율·원본 순서·315장 겹침 0 확인 |
| R4 | 긴 제목·장문·가로/좁은 모바일에서 전체 텍스트/사진 보존 검증 및 편집 보완 | PC7장/모바일 중앙240×320+양옆83px, 실제 드래그 4→5장 확인 | 모바일2~3장/행, filmstrip의 페이지 전체 스크롤 부작용 제거 |

각 에이전트는 담당 화면뿐 아니라 다른 테마 화면도 교차 검토했다. 실제 사용자 사진 캡처는 저장소에 넣지 않고 `/tmp/photos-review-r{1,2,3,4}-*` 및 `/tmp/photos-book-r4-*`에만 둔다. 임시 파일이 삭제되면 문서만으로 스크린샷 검증을 재현했다고 주장하지 않는다.

## 구현 및 기술적 한계

- 책: 정지 화면은 의미 있는 HTML, 전환 중에만 PC32/모바일22개 곡면 조각과 Canvas 앞뒷면. 진행도별 법선·깊이·그림자, 책등·종이 층을 표현한다. 정지 후 Canvas0개로 복귀한다. 모서리 위치별 비틀림·재질 물성 시뮬레이터는 아니다. [책 세부 검증](57-story-book-physical-rendering-notes-2026-09-15.md).
- 공간: Swiper 입력/관성 관리 위에 별도 진행률 기반 위치·깊이·회전·광원·반사 계층. 원본 비율 프레임과 실제 인접 사진만 사용한다. 단일 장면이면 의미 없는 중복 타임라인을 숨긴다. 장면명·사건을 임의로 생성하지 않는다.
- 목록: EXIF 방향 보정 후 최대768px의 비율 유지 gallery JPEG. 기존640px 정사각 thumb는 기존 소비자를 위해 유지한다. 메타데이터를 파생 JPEG에 포함하지 않으며 인증·공유 만료 경계를 유지한다.
- 모자이크: 계산한 폭·높이·좌표를 직접 배치한다. 315장 모바일 실측 겹침0, hit-test 누락0, 최대 비율 오차0.000153. 일반사진은 2장146행/3장6행, 극단 비율 예외4장1행. 마지막1장은 왼쪽 정렬.
- 책 경계 사례: 1440×1000 / 390×844 / 844×390 / 320×568 × 1장·2장·혼합9장·긴 제목/장문의16개 조합. 사진 누락0, 전체 텍스트 순서 보존, 페이지/문서 가로 넘침0. 낮은 가로화면은 세로스크롤로 가독성을 유지한다.
- 원본의 날짜·추천 요약 위주 Story는 시안의 풍부한 서사와 동일하지 않다. 기존 확인된 내용을 정확히 보여주며 디자인 때문에 내용을 꾸미지 않는다.

## 통합 단계에서 추가로 찾은 문제

1. `quiet`의 초기 filmstrip scrollIntoView가 페이지 전체를 스크롤하여 상단 버튼 클릭이 빗나감 → strip 내부 scrollTo로 한정. 정보 패널도 동작 중 좌표가 바뀌지 않도록 즉시 이동·preventScroll focus 사용.
2. 독립 테마에서 원본 chapter를 숨기면 지도도 접근 불가 → 원래 지도 DOM과 이벤트를 정보 패널로 이동, 스크롤 테마로 돌아오면 원래 위치 복원. URL/지도 노드를 복제하지 않음.
3. 큰 사진에서 다른 사진을 본 뒤 닫아도 테마는 첫 사진에 남음 → 닫을 때 마지막 감상 사진을 테마의 initialTile로 복원.
4. light 순간의 묶음 설명 대비4.07:1 → 5.22:1로 강화. 이름 있는 grid에 role=group 부여. 공간형은 Swiper가 button 역할을 group으로 덮어쓰지 않도록 설정.

## 검증 증거와 아직 남은 항목

- `scripts/check_story_quality.mjs`: PC1440/모바일390 × 5종 캡처, 원본315장 전체 목록 pairwise 겹침·hit-test·스크롤 검사. R4 실행 exit0.
- `scripts/check_story_book_browser.mjs`: 위16개 경계 사례 exit0.
- `scripts/check_story_browser.mjs`: 5종별 실제 클릭·확대·드래그·키보드·목록 복귀·인물 필터·테마 반복 전환. 신규5종으로 확대했고 최종 전체 실행 중이다. 2개 테마용 과거 통과 결과를 5개 완료 근거로 사용하지 않는다.
- 모바일 TestClient: 실제 WebView bootstrap 인증 이후5종 저장→GET 재접속, gallery 파생 응답을 검사한다. 실제 Android 앱 조작을 대신하는 검증은 아니다.
- 공개 공유: 5종 모두 생성 당시 테마를 snapshot으로 보존하고 소유자 테마 변경과 분리되는지 테스트한다. 운영에 테스트 공유 링크를 발행하지 않는다.
- 전체 pytest 최종 재실행 **1,115개 통과(17.74초)**. 이후 문서/테마 집중31개도 통과했다. 기존 `test_mobile_client.py`의 이번 변경 밖 FLY002 등 lint는 범위를 넓혀 기계적으로 변경하지 않았다. 신규 테마 모듈·새 테스트·preview 스크립트의 ruff와 diff-check는 통과했다.
- 일부 textured/gradient 배경 명암비는 axe가 incomplete로 남긴다. 위반0이라는 자동 결과를 접근성 전체 보증으로 표현하지 않는다.
- ADB는 설치되어 있으나 연결 기기 목록이 비어 있다. Android 실기기 제스처/시스템바 검증은 완료하지 않았다. USB 디버깅 연결과 잠금 해제를 사용자에게 요청했다.

## 운영 반영

- staging `/tmp/photos-story-release.FdXBxf`에서 `scripts/build_framework_standalone.sh` 실행 exit0. 서명 strict/deep, osxphotos·photo-source·인물 런타임 smoke 통과.
- 설치 전5개 Story 모듈의 staging 소스와 작업 소스가 byte-for-byte 동일한 것을 `cmp`로 확인.
- 분석 active_job_count0 및 수동 curation의 실행/대기 없음 확인 후 Mac 앱 교체. 이전 번들만 staging의 `previous-PhotosMcp.app`으로 이동했으며 원본·추천·DB·인물·GPS는 이동/삭제하지 않았다.
- 설치 경로 `/Users/byoungyoungla/Applications/PhotosMcp.app`, 새 PID77765. 모바일 `com.photosmcp.mobile-client` 재시작 PID77767.
- 운영 HTML: 자산v11, 신규5종 선택 항목 모두 확인. 운영CSS/JS SHA-256은 소스와 일치.
- 운영 gallery: 첫 실제 사진576×768, EXIF0개 확인.
- Tailscale `/photos` HTTP200. 인증 없는 모바일 CSS/gallery 모두401 유지.
- `scripts/check_story_installed.mjs`는 실제 설치 서버에서5종을 미저장 미리보기로 선택하고 뷰어·지도 접근을 검사한다. 사용자 테마 설정을 POST하지 않으며 새로고침 후 기본 스크롤 시네마가 유지되는지 확인한다. 현재 실행 중.
- APK/native 시스템바 코드는 변경하지 않았다. Android 앱은 서버의 v11 웹 자산을 받지만 실제 폰에서의 제스처 완료 증거는 아직 없다.

## 시각 기준 평가

refactoring-ui의8개 기준으로 개발안은 정성 **9/10(7/8)**: 사진 중심 위계, 무채색 기반, 여백, 보조 정보 강조도, 글줄 제한, 확인한 텍스트 대비, 공간/책의 목적 있는 음영은 충족했다. 기존 공통 스타일과 새 물리 geometry가 섞여 전체 간격 값이 하나의 토큰 척도로 완전히 통합된 것은 아니다. 이 점수는 실기기 성능·접근성 전체 보증이나 원안과 픽셀 단위 동일함을 의미하지 않는다.

## 재현 명령

```bash
PYTHONPATH=src .venv/bin/python scripts/preview_story_design.py
/opt/homebrew/bin/node scripts/check_story_browser.mjs
STORY_REVIEW_ROUND=4 /opt/homebrew/bin/node scripts/check_story_quality.mjs
/opt/homebrew/bin/node scripts/check_story_book_browser.mjs
.venv/bin/python -m pytest -q
```

미리보기는 loopback18809, 실제 Story DB 읽기 전용, 별도 세션에서만 실행한다. 운영 사용자의 테마 저장·분석 실행을 검증 코드가 수행하지 않는다.
