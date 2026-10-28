# PhotosMcp 기기 간 UX 감사와 전반 재설계 계획

- 작성: 2026-10-06, UX 담당 독립 감사
- 저장소: `/Volumes/ExtData/01_Projects/PhotosMcp/photos-mcp`
- 목적: 코드 수정 없이 현재 구현과 검증 증거를 토대로 IA, 화면, 상태 계약 및 개선 작업의 수용기준을 정의한다.
- 범위: macOS PyObjC/AppKit, 소유자 웹 및 모바일 웹, Android Java/View. Story의 감상 테마·편집·생성 품질 심층은 별도 담당이며, 이 문서는 Story 진입·범위·작업 상태·접근성 경계만 다룬다.
- 적용 스킬: `ux-heuristics`의 Nielsen/Trunk Test/심각도, `refactoring-ui`의 위계·토큰·회색조·대비, `swift-macos`의 AppKit 창/메뉴/파일 선택 원칙, `mobile-android-design`의 네이티브 탐색·48dp·상태 복원·적응형 원칙. Swift/SwiftUI 또는 Compose 전환은 전제하지 않는다.
- 기록 기준: 아래 경로·줄번호는 감사 시점의 작업 트리 기준이다. `people/controller.py`에는 기존 수정이 있었으며 읽기만 했다. 다른 담당의 동시 변경으로 후속 줄번호가 이동할 수 있다.
- 현재 계약은 실행 코드와 `docs/09-roadmap/01-active`를 우선한다. `docs/99-archive`는 현재 계약으로 사용하지 않았다. 기존 23번 계획의 과거 비교표는 구현 이후에도 일부 '없음' 항목이 남아 있으므로 그대로 결함 목록으로 복제하지 않았다.

## 1. 판단과 증거 수준

우선 해결할 문제는 외관보다 **동일 작업의 상태와 사용자 행위의 의미가 화면마다 달라지는 것**이다. 분류 시작 화면이 완료된 작업을 진행 중으로 표시하고, 인물 화면이 실패와 '모두 정리됨'을 동시에 표시한다. 결과 검토 화면은 창을 크게 해도 모드 버튼과 미리보기가 겹친다. 이 세 가지는 부모의 설치본 직접 관찰로 확인됐으며, 상태 변경 경로 및 fixture 기하 검증으로 구현 근거를 찾았다.

새 제품 구조는 `사진 범위 선택 → 실행 전 확인 → 작업 → 결과 검토 → 저장/이야기`를 기본 흐름으로 삼고, `연결·권한·환경`을 이를 돕는 설정으로 둔다. 각 기기에서 모든 기능을 복제하기보다 **같은 객체의 범위, 상태, 다음 행동을 동일하게 이해**하도록 한다.

### 1.1 증거 표기

| 표기 | 의미 | 이번 감사에서 실제 수행한 범위 |
|---|---|---|
| D | UX 담당 직접 실행 | 안전한 기존 fixture 테스트, AppKit 결과 창 기하 측정, 웹 빈 보관함 fixture의 AX·스크린샷·computed style 확인 |
| L | 부모 담당의 설치본 직접 관찰 전달 | 홈/분류/작업 기록/Story/결과/저장 공간/인물 관리. 개인 사진·이름·장소는 복제하지 않음. 상세 live 증거는 부모 `04-live-observations-and-documentation.md`에 기록 |
| C | 현재 코드에서 확인 | 컨트롤 생성, 상태 전이, API 범위, 복원·에러 처리. 실제 사용자 성공률을 증명하지 않음 |
| U | 잔여 미검증 | 설치본과 소스의 완전 동일성, Android 실기기, VoiceOver/TalkBack, 권한·인증·쓰기·AI E2E 등 |

L은 이 담당의 독립 직접 검증으로 바꾸어 기록하지 않는다. C의 '경로 없음'은 조사한 화면/컨트롤러 범위의 부재이며 전체 제품 기능이 없다는 뜻은 아니다. 심각도는 0~4; 3은 주요 작업 실패·오해, 2는 지연·반복 마찰, 1은 시각적 경미 문제다. 우선순위 P0는 상태 신뢰·명백한 겹침·접근성 차단, P1은 핵심 흐름/복구, P2는 확장·일관성이다. 이번 감사에서 원본 데이터 손실이나 전체 제품 사용 불가 S4는 입증하지 않았다.

### 1.2 실행한 검증과 제한

1. `.venv/bin/python -m pytest -q -p no:cacheprovider`에 `PYTHONDONTWRITEBYTECODE=1`을 지정하고 다음 기존 fixture 테스트만 실행: `tests/test_menu_appkit_layout.py`의 popover 접근성, main sidebar/home, 분류 native input flow, 분류 overlap(860/1080/1320), 결과 10,000항목, 결과 열 수, resize 선택 유지; `tests/test_google_photos_appkit.py::test_google_photos_window_exposes_user_action_boundaries`; `tests/test_result_gallery_appkit.py` 전체. **16 passed in 1.27s**. 서비스/모델 실행·실제 라이브러리·키체인 조작은 하지 않았다.
2. 위 테스트 모듈의 SimpleNamespace 상태 fixture로 AppKit 결과 창을 1920×978로 구성하고 각 모드 버튼과 inspector 이미지의 root 좌표 교집합을 측정했다. 버튼 각각 `110×32`, 이미지 `488×461`; 교집합 높이 12, 면적 **1,320pt²**. 이는 실제 NSView 기하 측정 D이며 설치본의 픽셀 스크린샷 검증은 L이다. AppKit 좌표는 아래에서 위로 증가하므로 부모 스크린샷 y좌표와 숫자를 직접 비교하지 않는다.
3. `render_owner()`에 사진 0장·가상 GPS 대기·가상 알림만 전달하고, 메모리 HTTP fixture를 loopback 임시 포트에서 제공했다. POST는 405, 운영 저장소 연결 없음. CUA의 별도 숨김 탭에서 AX와 스크린샷을 확인했다. 실제 `STORY_CSS` 사용; JS 및 사진 자산은 제공하지 않았으므로 enhanced JS, 실제 인증/네트워크·Story 전체 품질 검증은 아니다.
4. 웹 기본 1280×720 및 390×844 확인: 390px에서 body scrollWidth=390, 날짜 폼은 320px 단열, 실행 버튼 높이 48px. 초기 AX에서 날짜 생성·알림은 접힌 '정보와 보기 설정' 내부; 펼친 뒤 접근 가능. 다크 scroll_cinema fixture의 활성 실행 버튼 computed style은 foreground `rgb(251,251,253)`, background `rgb(230,230,233)`, 대비 **1.21:1**. 버튼을 제출하지 않았다. viewport를 복원하고 탭·임시 서버를 종료했다.
5. `adb` 스킬을 읽고 Android project의 `local.properties`가 없음을 확인한 뒤 PATH의 `/opt/homebrew/bin/adb`로 `adb devices`를 실행했다. 결과는 헤더만 있고 **연결 기기 0대**였다. package 정보 조회/기기 UI 검사는 대상이 없어 수행하지 않았다. 저장소 `android/location-bridge/app/build.gradle:6-14`에서 applicationId `com.photosmcp.locationbridge`, versionCode 24, versionName 0.8.5를 확인했으나 이는 설치 상태 증거가 아니다. 기기 연결·권한·설치·ADB server 재시작은 하지 않았다.
6. 테스트 통과는 설치 앱의 상태 일관성, 이미지와 타 컨트롤의 충돌, 전체 키보드 사용 또는 Android 품질을 보장하지 않는다. 결과 overlap 사례는 기존 통과 테스트의 커버리지 밖에 있다.

### 1.3 진단 점수

점수는 사용자 테스트 결과가 아닌 **증거 기반 임시 설계 진단**이다. UX 스킬의 10점에서 중대 실패 행을 약 2점, 경미 실패 행을 약 1점 차감하는 방법상 현재 핵심 흐름은 **3/10(잠정)**: 상태 가시성, 주 행동 발견성, 복구/뒤로, 탐색 맥락, 일관성, 혼란 없는 문구에 복수 실패가 있다. 모든 버튼 무라벨 또는 모든 검색 부재로 평가하지 않는다.

Refactoring UI의 8개 진단 중 현재 확인한 만족은 제한된 본문 폭·일부 label/value 위계·표면 구분 3개, 명백한 실패는 배치/행동 위계와 일부 대비 2개, 회색조/충분한 여백/전체 spacing 준수 3개는 U다. 증거 확인률을 점수화하면 **4/10(3/8 반올림, 미검증을 실패로 단정하지 않는 하한 참고값)**. 10/10 완료 판정은 후술한 전체 화면·테마·폭별 진단을 직접 검증하고 S3를 해소한 뒤에만 한다.

## 2. 현재 화면 및 기능 구조

| 표면 | 현재 탐색/화면 | 코드 근거 | 제품상 의미 |
|---|---|---|---|
| Mac main | 홈 / 사진 분류 / Story / 작업 기록 / 저장 공간 / 환경 및 권한 / 인물 관리 | `src/photos_mcp/interfaces/appkit/main/controller.py:169-218,250-290` | 운영 진단과 사용자 사진 작업이 같은 최상위에 배치 |
| Mac 분류 | Apple/로컬/Google → 범위 → 방법 → 실행 전 확인 | `src/photos_mcp/interfaces/appkit/classification/controller.py:214-491,938-975` | 출처별 준비와 모델 작업 시작. 아직 저장 실행이 아님 |
| Mac 로컬 | 폴더 tree / grid·단일 사진 / inspector, 현재 폴더 검색 | `src/photos_mcp/interfaces/appkit/local_browser/controller.py:403-488,1880-1909` | 파일명 기준의 현재 폴더 선택 도구 |
| Mac Google | 연결 → Picker 선택 → 원본 준비, 별도 OAuth 설정 | `src/photos_mcp/interfaces/appkit/google_photos/controller.py:147-217,299-316` | 외부 브라우저/계정 승인과 가져오기 상태 연결 |
| Mac 결과 | 사진 보기 / 선택 및 저장 / 저장 공간; 추천/검토; 장면/사진; inspector | `src/photos_mcp/interfaces/appkit/results/controller.py:694-864,1122-1144` | 특정 분석 job의 결과와 저장 선택 |
| 웹 소유자 | Story 감상 renderer 안에 날짜 생성·작업·알림·목록·공유 삽입 | `src/photos_mcp/interfaces/http/story_web.py:730-894` | 운영 포털과 개별 이야기 감상 화면의 혼합 |
| Android | 홈 / 작업 / 추천 / 이야기, 상단 알림·설정, 설정 아래 인물 | `android/location-bridge/app/src/main/java/com/photosmcp/locationbridge/MainActivity.java:252-364,1862-1907` | Java Activity/View + 제한된 WebView. Compose가 아님 |
| Android 인물 review | 별도 Activity에서 얼굴·사진·draft·진행 표시 | `android/location-bridge/app/src/main/java/com/photosmcp/locationbridge/PeopleReviewActivity.java:60-90,99-130` | 예외 검토의 단일 작업 흐름. 일반 main보다 복원 상태가 풍부함 |
| 모바일 웹 | 소유자 포털 반응형 / 인증된 Story renderer / 공유 열람 | `src/photos_mcp/interfaces/http/mobile_client.py:2837-2868`; `src/photos_mcp/interfaces/http/story_web.py:75` | Android 전체 native 메뉴와 동등한 앱은 아님. iOS native 구현은 이 범위에서 확인되지 않음 |

좋은 기존 기반은 보존한다: AppKit NSCollectionView 가상화(10,000항목 fixture 통과), 결과 resize 선택 유지, 분류 실행 전 범위 확인, 원본 변경을 명시하는 export plan/승인, 내보내기 receipt 재시도, 폴더 검색 범위 AX label, 인물 Cmd+Z와 row 선택, Android 선택 탭 stateDescription·48dp 버튼·system inset·가상 작업 polling 세대 취소, 웹 reduced-motion·focus-visible. 감사는 이를 전면 부정하지 않는다.

## 3. 이슈 원장 — 상태 신뢰와 IA

### UX-01 · 완료 작업과 분류 화면의 stale 상태 불일치 — S3 / P0 / L+C

- 증상: 부모 설치본 1398×812에서 이전 로컬 범위가 보존되고 '준비하고 있습니다', '작업을 시작했습니다', disabled '분석 진행 중'이 동시 노출. 작업 기록에서는 해당 작업 100% 완료·진행 중 0. 숫자/경과일은 상태 비교 증거이며 개인 데이터는 기록하지 않는다.
- 근거: `classification/controller.py:890-917`은 접수 후 `_run_accepted=True`, 버튼 title/disabled를 변경한다. job_id는 이 메서드 지역 변수이며 화면의 현재 작업 식별자로 보존·추적되지 않는다. `main/controller.py:155-161`은 classification을 snapshot refresh rebuild에서 제외하고 `637-667`에서 같은 컨트롤러를 재사용한다. `_update_step_states`의 `classification/controller.py:1454-1455`는 접수 여부만으로 모든 단계를 complete로 설정한다.
- 원인 판단: 시작 폼의 접수 로컬 상태와 백엔드 작업 terminal 상태를 연결하는 UI 상태 전이가 부족하다. 부모 설치본의 정확한 빌드와 과거 전이까지 추적한 것은 아니므로 소스가 모든 원인이라는 단정은 하지 않는다.
- 제안/수용: `draft → previewing → ready → submitting → accepted(job_id) → running → terminal`을 구분한다. scope draft는 보존하되 실행 상태는 authoritative job으로 결정. terminal fixture를 주입하고 탭 왕복·foreground·resize 후 2초 이내 모든 화면이 완료/결과 보기로 바뀌며 진행 중 버튼이 남지 않아야 한다. 새 범위를 수정하면 accepted는 이전 작업 이력으로 분리한다.

### UX-02 · 실행 버튼 title/AX/Help가 서로 다른 의미 — S3 / P0 / L+C

- 증상: 부모 AX에서 화면 '분석 진행 중', Description은 이전 장수의 '선별 시작', Help는 초기 '50장 분류 시작'. 비시각 사용자가 실행 가능한 행동을 오해한다.
- 근거: `classification/controller.py:1499-1505`는 title과 accessibilityLabel만 갱신; `1588-1601,1625-1627`은 최초 title을 tooltip으로 설정; `906-908`은 접수 시 title/disabled만 갱신.
- 제안/수용: 단일 action view model에서 title, accessible name, help, enabled, state description을 함께 생성. 접수/실행/완료/오류/장수·mode 변경 각각에서 AX 이름이 현재 행동과 일치하고 Help가 현재 scope를 설명해야 한다. disabled 상태는 이유와 대신 가능한 '작업 보기'를 제공한다.

### UX-03 · 홈이 작업 목표보다 MCP 준비 중심, 연결 headline은 고정 — S3 / P1 / L+C

- 근거: `main/controller.py:310-337`의 '사진 보관함에 연결됨'·'MCP 요청을 받을 준비'는 고정 문구; server badge만 상태 조건을 가진다. 분류 카드 설명은 '앨범과 기간'으로 local/Google 선택을 설명하지 못한다. 실제 홈도 부모가 MCP 중심으로 관찰했다.
- 제안/수용: 초기 홈은 '사진 출처 연결', 사용 후 홈은 '최근 작업 이어보기 / 확인 필요 / 새 사진 선택 / 최근 이야기'를 우선. MCP는 설정의 고급 연결 정보. daemon stopped·사진 권한 거부·Google만 연결·로컬만 사용 fixture에서 '보관함 연결됨'을 허위 표시하지 않고 사용 가능한 주 행동 하나가 보인다.

### UX-04 · 작업 기록의 서버 정상 badge는 고정 — S2 / P0 / C

- 근거: `main/controller.py:760-786`에서 `서버 실행 중` 및 success tone을 무조건 생성한다. 홈의 조건부 badge와도 다르다.
- 제안/수용: 마지막 동기화 시각과 서비스 상태를 공통 projection으로 표시. stopped/unreachable 상태 fixture에서는 정상 badge가 사라지고 '저장된 작업 기록 · 연결 확인 필요'가 되며 이미 완료된 결과의 오프라인 접근 가능성은 별도 표시한다.

### UX-05 · '결과'의 객체와 출처가 기기마다 다름 — S3 / P1 / C

- 근거: `results/controller.py:199-230,383-400`은 job payload/items; Android `MainActivity.java:1019-1044`는 선택 storyId 또는 최신 추천 조회; `mobile_client.py:2837-2854`도 특정/현재 Story projection을 읽는다. 23번 계획 `:14-25`도 이 경계를 설명하지만 현재 버튼 존재는 반영해야 한다.
- 제안/수용: 결과 header에 '분석 결과' vs '보관된 추천'을 명확히 표기하고 source scope/작업/생성일/선택·추천 수를 표시. shared result snapshot 식별자를 common workspace로 연결한다. Mac에서 1,000장 분석→100장 추천이면 Android에서 100장만 보이는 이유와 전체 결과를 볼 기기를 즉시 설명한다. 장수 일치를 강제하지 않는다.

### UX-06 · Story 탭 shell 안내와 현재 웹 경로 맥락 불일치 — S2 / P1 / L+C

- 증상: 부모 설치본 Story 탭에서 한 Story 상세 감상 화면이 보존되지만 shell은 '날짜를 선택해 Story를 만들고…'를 계속 표시한다.
- 근거: `main/controller.py:703-752`은 고정 heading/설명과 WKWebView 구성; navigation delegate 또는 현재 path에 따른 header 갱신 경로가 이 블록에 없다. `results/controller.py:465-480`은 새 Story 완료 후 main Story 탭만 연다.
- 제안/수용: 목록/새 만들기/상세를 별도 route로 다루고 shell title·breadcrumb·Back을 route와 동기화. 상세에서는 제목/목록으로, 생성에서는 범위 확인으로 안내. 탭 진입 시 기본 목록 또는 의도한 deep link 정책을 명시. navigation/resize/재방문 후 현재 화면 설명이 일치하고 새 Story 생성 성공은 해당 storyId 상세로 이동해야 한다. WKWebView 보존의 정확한 설치본 원인은 U.

### UX-07 · 생성·작업·알림이 감상 설정 disclosure 안에 숨음 — S3 / P1 / D+C

- 근거: `story_web.py:712-713`은 meta를 '정보와 보기 설정' details 안에 둔다. `730-894`, 특히 `890-893`은 meta 앞에 생성/작업/알림/목록을 문자열 치환 삽입한다. fixture 초기 AX에서 이 기능들은 숨고 disclosure를 열어야 나타났다. `story_experience.py:24,31-35`의 감상 cover/disclosure 레이아웃도 운영 기능에 적용된다.
- 제안/수용: 소유자 운영 홈과 감상 상세를 분리한다. 작업·확인 필요는 홈에서 항상 표시하고 date form은 '새 작업' 화면으로 이동. 빈 보관함 첫 viewport에 '사진 선택/날짜로 시작' CTA와 권한/연결 미충족 이유가 보인다. 설정 펼치기 없이 첫 작업을 찾을 수 있어야 한다.

### UX-08 · 웹 알림이 행동 없는 설명 카드 — S3 / P1 / D+C

- 근거: `story_web.py:795-834`의 operation/event 카드에는 title/detail만 있다. GPS 대기는 'Android 앱의 알림에서 …' 지시만 제공한다. fixture에서도 가상 조치 카드에 action이 없었다. Android `MainActivity.java:1634-1780`에는 ack와 GPS 이어하기 별도 경로가 있다.
- 제안/수용: 확인 필요 카드에 관련 작업 열기, 연결된 기기 상태, 휴대폰에서 계속할 방법, 동기화 확인을 제공. Mac이 할 수 없는 GPS 수집을 버튼으로 가장하지 않는다. 동일 eventId는 읽음/해결/아직 할 일의 차이를 기기 간 동일하게 표현하고 terminal 작업 조치는 자동 정리한다.

## 4. 이슈 원장 — 첫 실행·권한·연결

### UX-09 · Android 최초 연결은 설정의 등록 JSON/ADB 중심 — S3 / P1 / C

- 근거: `MainActivity.java:137-159`은 항상 showHome; `1862-1877`은 'ADB를 쓸 수 없을 때만 등록 JSON 붙여넣기'; `1034-1036,1969-1971`은 미등록 화면에서 설정 안내 문장만 표시한다. APK 다운로드와 앱 등록은 다른 작업이다.
- 제안/수용: Mac '기기 연결'에서 일회성 pairing code/QR을 제시하는 사용자 흐름을 설계하고 현재 서명·scope·TTL 보안 계약을 보존. 초기 Android 홈에 'Mac과 연결' CTA, 필요한 네트워크, 연결 확인·만료·재시도·기기 제거 경로 제공. 개발 JSON/ADB는 고급으로 이동. 기존 연결 기기는 등록 wizard를 다시 강제하지 않는다. 실제 등록·보안 변경은 이 감사에서 수행하지 않았다.

### UX-10 · 사진 읽기와 원본 GPS 권한을 단일 성공 조건으로 묶음 — S3 / P1 / C

- 근거: `MainActivity.java:2471-2494`는 사진+ACCESS_MEDIA_LOCATION을 요청하고 `read && location`으로 판정; `858-862`의 날짜 작업 시작도 동일 조건을 사용한다. Manifest `:3-8`에는 선택 사진 권한 선언이 있지만 이 helper에는 READ_MEDIA_VISUAL_USER_SELECTED 상태 분기가 없다.
- 제안/수용: 보기/선택/원본 GPS 각 능력을 분리하고 최소 필요 시 요청. 부분 사진 접근·GPS 거부를 '아무것도 못함'으로 합치지 않는다. 전체/부분/거부/영구거부/GPS만 거부/권한 취소 각각의 상태표를 설계하고 사용 가능한 동작과 범위 감소를 설명한다. Android 버전별 실제 OS 동작과 scanner의 부분 접근 처리 전체는 U이므로 현재 앱 전체가 부분 권한을 지원하지 않는다고 단정하지 않는다.

### UX-11 · 권한 승인 이후 원래 작업으로 복귀하지 않음 — S2 / P1 / C

- 근거: `MainActivity.java:2497-2503`은 설정 status에 '동기화 버튼을 다시 눌러'만 쓴다. 날짜 생성 중 요청했을 경우에는 원래 date form status/control과 이어하기 대상이 명시적으로 복원되지 않는다.
- 제안/수용: pending intent에 scope draft/actionId만 저장하고 권한 callback 후 같은 화면에 '이 범위로 계속'을 보여준다. 승인 즉시 AI 실행은 하지 않으며 사용자 최종 실행 선택을 남긴다. Settings에서 돌아와도 날짜/출처/장수/재분석 의도가 보존되어야 한다.

### UX-12 · Google 계정 연결 설정은 기술 계약이 주 사용자 경로 — S2 / P1 / C

- 근거: `google_photos/settings_controller.py:143-199`은 Desktop app Client ID/secret/Console 지침; `google_photos/controller.py:299-305,588-602`은 unconfigured 시 해당 설정을 요구한다. 연결→선택→준비 단계와 취소는 이미 구현되어 있다.
- 제안/수용: 일반 사용자는 'Google 연결'에서 필요한 준비를 이해하고, 배포 방식상 사용자 OAuth client가 반드시 필요한 경우 '연결 준비(고급)'로 명시하여 단계별 검사·복구 안내를 준다. 비밀 필드는 secure로 유지하고 마스킹된 저장 여부만 노출. 연결 권한과 새 사본 업로드 권한을 분리 표시한다. 계정 만료/설정 없음/Picker 대기/준비 실패에 대해 각각 기존 선택 유지 여부와 다음 버튼이 명확해야 한다.

### UX-13 · 연결 오류가 일반 체크리스트와 raw message로 묶임 — S2 / P1 / C

- 근거: Android `MainActivity.java:2954-2960`은 예외 message + Tailscale/Mac/계정 동일 checklist; Mac 분류 `classification/controller.py:832-837,894-896`은 preview exception이나 미매핑 error를 그대로 표기한다. Android에는 `2695-2788`의 일부 구조화 오류 번역이 있으므로 이를 재사용한다.
- 제안/수용: 사용자 설명/가능 행동/기술 상세를 분리하고 연결 끊김·세션 만료·권한 부족·출처 실패·입력 오류를 다른 recovery로 매핑. diagnostics 복사는 경로·URL·token·이름을 redaction. retry fixture는 입력/선택을 보존하며 원인이 아직 남으면 중복 제출하지 않는다.

## 5. 이슈 원장 — 탐색·검색·검토·저장

### UX-14 · 제품 전체 검색과 현재 폴더 파일명 검색의 범위 차이 — S2 / P2 / C

- 근거: `local_browser/controller.py:472-487,1880-1885`는 label은 현재 폴더, 구현은 `photo.name` substring. main tab 구성 `main/controller.py:250-261`, 결과 controls `results/controller.py:694-864`, Android 추천 `MainActivity.java:1019-1114`에는 이 수준의 날짜/인물/출처 통합 검색 UI가 없다.
- 제안/수용: 먼저 인덱스가 이미 있는 필드의 구조화 검색(날짜·출처·추천/검토·확인된 인물)을 공통 query로 정의. 파일명 검색은 명시적으로 '현재 폴더 파일명 검색'. 의미검색/새 VLM 실행은 별도 제안·승인 범위다. query scope, 활성 filter chip, 결과 수, clear, 검색 0건 vs 라이브러리 0건을 구분하고 뒤로가기 후 복원한다.

### UX-15 · 대형 Mac 창 최소 크기와 전체 rebuild 전략 — S2 / P2 / C

- 근거: `main/controller.py:139-142,177-218`은 min 1180×760, resize 시 모든 subview 제거/rebuild. 분류 `classification/controller.py:196-200`은 860px 하한이며 main `637-667`은 스크롤로 수용한다. 기존 테스트는 이 하한 계약을 확인한다.
- 제안/수용: 최소 크기를 무조건 줄이지 말고 1024×768/작은 notebook/분할창에서 필수 CTA 접근을 먼저 검증. shell·content incremental layout, pane 축소/접힘, 의미 있는 min size를 설계. resize/탭 전환 후 focus·스크롤·입력·선택을 유지하며 새 preview나 개인 데이터 읽기를 불필요하게 반복하지 않는다. 각 화면 하한을 하나의 토큰/테스트 matrix로 관리한다.

### UX-16 · 결과 모드 전환 버튼과 inspector 이미지가 겹침 — S3 / P0 / L+D+C

- 근거: `results/controller.py:884-891` workspace_y=`height-60`; `929-930` inspector는 top=`height-32`; `971-975` image는 inspector top에서 16px 아래까지 위치. 서로 다른 header/body 예약영역을 쓰므로 교집합이 생긴다. 부모 live에서 보기/선택 모두 관찰; 1920×978 fixture에서도 세 버튼 각각 1,320pt² 교집합 확인.
- 제안/수용: 공통 header 끝 아래부터 gallery와 inspector를 시작한다. toolbar를 inspector 이미지 위 독립 줄에 놓고 content hit region과 중복시키지 않는다. 1100/1280/1398/1920 폭, 최소/978 높이, 세 mode 모두에서 visible interactive rect와 image open overlay 교집합 0; z-order에 숨기는 수정은 불합격. inspector image click과 segmented selection이 각자 작동해야 한다.

### UX-17 · 선택한 사진으로 이야기 만드는 CTA가 선택 모드에서 숨음 — S3 / P1 / L+C

- 근거: `results/controller.py:952` Story 버튼은 bottom action_y=66; `1122-1136`은 selection_mode일 때 Story 버튼을 숨긴다. `383-429`에는 실제 선택/추천 범위 chooser와 '재분석 없음' 설명이 있으므로 기능 부재가 아닌 발견성 결함이다.
- 제안/수용: 공통 selection action bar에 '선택 N장으로 이야기'와 '선택 N장 저장'을 나란히 제공. 선택 없으면 '추천 장면 N장으로 이야기', 현재 filter 전체와 실제 선택 범위를 구분한다. 버튼 앞 scope summary에 출처·장수·기존 분석 재사용을 표시한다. browse/selection 어디서든 동일 결과 범위로 실행 가능한 행동을 찾고 추천/선택 choice가 예측 가능해야 한다.

### UX-18 · 내보내기 계획/재시도는 있으나 작업 이어하기가 modal 중심 — S2 / P1 / C

- 근거: `results/controller.py:1669-1725` plan 승인; `1752-1805` partial receipt를 modal에서 재시도; `1230-1239` 선택/장수 표시. `tests/test_selected_export_resume.py:10,57,84,154`는 receipt 재시도 계약을 검사하는 기존 테스트다(이번에는 실행하지 않음).
- 제안/수용: 승인 전 대상/원본·사본/중복·누락·필요 다운로드·선택 scope를 한 장으로 보여주고 승인 후 durable export activity를 작업에 남긴다. 닫기/앱 재실행 후 partial receipt를 찾고 완료된 대상은 재실행하지 않는다. 데이터 변경은 항상 plan 승인 후; '취소'가 준비 중단/아직 미실행/실행 중단 가능 중 무엇인지 명시한다. 이번 감사는 실제 export 하지 않았다.

### UX-19 · 저장 공간은 7개 Story까지만 표시, 전체 탐색 불가 — S2 / P1 / L+C

- 근거: `main/controller.py:480-500`에서 `stories[:min(7,...)]`를 labels로 렌더; 해당 영역에 scroll/페이지/전체보기 없음. 부모 설치본도 7개 고정 행을 관찰했다.
- 제안/수용: total count + sortable/scrollable table, 참조 원본과 재생성 가능한 캐시 용량을 분리. 0/7/8/100 Story fixture에서 모든 행을 검색/정렬·키보드로 접근하고 현재 표시 범위를 알 수 있어야 한다. Story별 drilldown과 정리 preview로 이어지며 원본 제거를 캐시 정리로 오인하지 않게 한다.

### UX-20 · Android 추천은 page 단위 탐색이며 화면 컨테이너 폭을 사용하지 않음 — S2 / P2 / C

- 근거: `MainActivity.java:1086-1114`는 displayMetrics widthPixels를 기준으로 2~4열 및 100장 이전/다음; Mac은 NSCollectionView 가상화. split-screen/parent padding/font scale 변화는 실기기 U.
- 제안/수용: measured available width로 grid를 계산하고 RecyclerView 등 기존 View 생태계에서 virtualize. paging 자체가 결함은 아니며 총수/현재 범위/최종 페이지/뒤로 복원이 명확해야 한다. 320/390/600/840dp, landscape/split-screen/fontScale 1.0/1.3/2.0에서 잘림·가로 overflow 없이 사진·caption·page action 접근.

### UX-21 · 추천 없음·Story 삭제·404가 비슷한 빈 상태 — S2 / P1 / C+D

- 근거: `MainActivity.java:1053-1057,1071-1074`는 404를 empty grid로 표시; `story_web.py:854-855`는 사진 없으면 일부 control을 제거. 빈 owner fixture는 '다음 자동 정리가 끝나면…'만 기본 안내하고 생성은 disclosure 안에 있다.
- 제안/수용: 최초 미연결/연결됐으나 없음/필터 0건/삭제된 Story/권한 없음/네트워크 실패를 분리. 모두 새 분석을 강요하지 않고 해당 상태의 주 행동(연결, 범위 수정, 목록으로, 다시 불러오기)을 제시. 삭제된 deep link는 새 '추천 없음'처럼 보이지 않아야 한다.

## 6. 이슈 원장 — 인물·접근성·반응형·작업 복원

### UX-22 · 인물 오류/미조회가 '모두 정리됨'으로 오해됨 — S3 / P0 / L+C

- 증상: 부모 live에서 '지금은 모두 정리됐어요'와 '인물 찾기 실패 · ConfirmedMembershipConflictError'가 동시 표시. 색인/새 분석/새로고침은 실행하지 않음.
- 근거: `people/controller.py:154-175`는 dashboard count 없음도 0으로 바꾸어 정리 완료 표현; `1196-1210`은 dashboard 조회 예외시 `{}`/aliases `()`로 덮음; `615-635`는 색인 오류를 별도 `_index_message`에 기록한 뒤 catalog를 로드한다. **색인 실패 → dashboard 실패가 항상 연쇄한다는 것은 입증하지 않았다.** 둘은 독립적으로 모순 UI를 만들 수 있는 경로다.
- 제안/수용: `loading/ready-empty/ready-with-review/partial/error/stale`를 구분하고 count는 unknown을 허용. 마지막 성공 조회 시각과 새 색인 실패를 별도로 보여준다. repository/dashboard exception fixture 또는 indexing conflict fixture에서 success '모두 정리됨'을 표시하지 않는다. 기존 성공 데이터는 '마지막 확인 결과'로 계속 열람하되 최신 조사 완료로 가장하지 않는다.

### UX-23 · 인물 확정 충돌이 raw exception으로 끝남 — S3 / P1 / L+C

- 근거: `people/controller.py:625-628`은 type명만 표시; `673-674`는 다른 경로에서 type+message. `application/person_identity_repository.py:1926-1950`은 다른 owner-confirmed identity 및 같은 사진 내 중복 identity에 대해 ConfirmedMembershipConflictError를 던진다. 실제 live 충돌이 이 두 분기 중 어디인지 또는 다른 동일 예외 분기인지 U.
- 제안/수용: '이미 확인한 인물 연결과 새 제안이 충돌했습니다. 기존 연결은 유지됩니다'를 보여주고 충돌 review로 연결. 현재 확정/새 제안/출처/최근 변경 시각을 권한 있는 화면에서 비교하고 유지/다른 연결/보류를 선택; 자동 병합·덮어쓰기·재분석 자동 실행 금지. 원래 명시 확정은 보호하고 새 검토는 revision+승인 경계를 가진다. 부분 색인 성공과 실패 자산을 나눠 재시도 범위를 제한한다.

### UX-24 · 동일 이름 인물 카드의 구별 단서 부족 — S2 / P1 / L+C

- 근거: 부모 live에 동일 표시 이름의 별개 카드 관찰(이름 기록하지 않음). `people/controller.py:1215-1223`은 name만 label, `1239-1255`는 별개 identity id로 정렬한다. 같은 이름은 데이터 중복의 증거가 아니며 자동 병합 근거가 아니다.
- 제안/수용: 대표 얼굴/확정 여부/출처/관리 사진 수/충돌 표식을 붙이고 내부 id는 필요시 상세에만. 이름이 같은 두 fixture identity를 다른 대상으로 선택·연결 가능해야 하며 사용자 확인 전 합치지 않는다. 이름 변경/연결/분리/숨김 각각의 효과와 되돌리기 범위를 표시한다.

### UX-25 · Android 인물 진입 접근성 문구가 '읽기 전용'인데 실제 변경 기능 존재 — S2 / P1 / C

- 근거: `MainActivity.java:1881-1883`의 contentDescription은 읽기 전용; `2210-2381`에는 동의/자동 인식/연결/새 인물/제외 action; 실제 인물 화면 `1951-1959`도 관리 설명이다.
- 제안/수용: 진입 label은 '인물 관리 · 확인 필요 N건'으로 통일하고 view/change 권한을 action별 표시. 접근성 description이 실제 scope와 일치하며 허용되지 않는 action은 이유와 Mac에서 할 방법을 안내한다.

### UX-26 · Android main 화면 복원이 날짜 세 필드로 제한 — S3 / P1 / C

- 근거: `MainActivity.java:144-157,2542-2546`은 날짜/selectionMode만 복원, 항상 홈. 현재 탭, resultsStoryId/page, operationId, source/limit/reanalyze, scroll은 명시 저장되지 않는다. 반면 `PeopleReviewActivity.java:60-90`는 사진/face index/draft를 저장하는 좋은 기존 모델이다.
- 제안/수용: current destination + stable object id + query/scroll + draft를 Bundle/ViewModel/안전한 local store로 보존(Compose 필요 없음). 회전/OS process recreation/외부 권한 화면 복귀 후 원래 결과와 작업으로 돌아가고 중복 작업을 만들지 않는다. 얼굴 action handle은 TTL이 있으므로 복원 후 server revision·handle 재조회, stale draft를 재검토하여 서명 작업에 적용한다.

### UX-27 · Android Back이 상위 맥락 대신 홈으로 이동 — S2 / P1 / C

- 근거: `MainActivity.java:2506-2526`은 viewer 우선 close 및 WebView back을 제공하지만 일반 non-home에서는 showHome; 인물 화면 `1957-1959`에는 설정으로 버튼이 별도로 있다.
- 제안/수용: top-level tab과 nested detail/draft를 구분한 destination stack. 인물 review→인물 목록→진입 화면, operation detail→같은 필터 작업 목록, Story detail→Story 목록. Back/gesture/화면 내 뒤로의 결과가 같고 draft 손실 전 저장/보류를 선택할 수 있어야 한다. 뷰어 close 우선 행동은 보존.

### UX-28 · 웹 활성 primary 버튼의 다크 테마 대비 실패 — S3 / P0 / D+C

- 근거: `story_web.py:67`의 button text는 `#fbfbfd`, background는 theme별 --accent; `77-78`은 dark 변수만 변경; `178-179`는 경험 CSS를 합친다. 빈 scroll_cinema fixture의 computed 활성 버튼 대비 1.21:1(전경 251/251/253, 배경 230/230/233). `story_experience.py:43`은 일부 presentation action만 전경/배경을 역전한다.
- 제안/수용: owner action 전용 semantic `primary/on-primary`를 감상 테마와 분리. 전체 테마·시스템 light/dark·explicit theme에서 활성/hover/focus/disabled를 각각 측정. 일반 텍스트 4.5:1, 큰 텍스트 3:1, focus/상태 식별 3:1을 목표 계약으로 적용하고 enabled 버튼을 disabled처럼 보이게 하지 않는다. 이번 측정은 default scroll_cinema dark fixture 한 조합이며 모든 테마 실패로 일반화하지 않는다.

### UX-29 · native 단축키·focus 계약은 화면별로 분산 — S2 / P2 / C

- 근거: `classification/controller.py:1633-1637` focus loop; `recommendation_review/controller.py:59-83`의 Enter=저장 후 다음/숫자/좌우; `results/photo_viewer.py:165-188`의 viewer key handling; `people/controller.py:1584-1587,2131-2142`의 Cmd+Z. main rebuild는 `main/controller.py:189-218`에서 view 제거. 모든 화면의 실제 VoiceOver/focus loop/IME 조합은 U.
- 제안/수용: 공통 키보드 문서와 화면 내 help: Cmd+F 검색, Escape overlay/draft 닫기, Space 선택, Return 기본 행위, arrow 이동, Cmd+Z 마지막 가능한 변경. text 입력/한글 IME 중 숫자·Enter global shortcut이 실행되지 않게 한다. tab 왕복/resize/render 후 logical focus 복원, 숨긴 control은 focus 순서에서 제거. 모든 변경은 keyboard-only로 수행 가능해야 한다.

### UX-30 · 시각 토큰이 존재하지만 적용·의미가 표면별로 다름 — S2 / P2 / C

- 근거: `shared/theme.py:18-60` spacing/font 확대; main은 다수 고정 frame(`main/controller.py:250-290,310-372`), web `story_web.py:62-79`와 Android `res/values/colors.xml:3-16`, `values-night/colors.xml:3-16`, `MainActivity.java:2639-2648`에는 각 semantic 색상이 있다. 단순 녹색 brand 복제보다 native 사용자 accent/시스템 text를 보존해야 한다.
- 제안/수용: 의미 토큰(heading/body/caption, primary/on-primary, success/warning/error/unknown, within-group/between-group)을 매핑. AppKit은 NSColor/system font, Android는 기존 resource/View theme, web은 CSS variables로 구현. 12px spacing 등 기존 유효 native scale을 무조건 제거하지 않음. 회색조에서도 primary/secondary/선택/오류가 텍스트·형태로 식별되어야 한다.

### UX-31 · 검토 저장과 이동의 의미를 명시하는 화면 계약 부족 — S2 / P1 / C

- 근거: `recommendation_review/controller.py:65-76`의 Enter=saveAndNext, 숫자 선택/0 primary clear; `PeopleReviewActivity.java:82-89,624-736`의 draft와 명시 저장 흐름. 사용자 관점의 선택/추천 판정/이름 연결/내보내기 선택은 서로 다른 변경이다.
- 제안/수용: header에 '추천 검토'/'인물 연결 확인'/'저장할 사진 선택'을 구분하고 현재 변경을 저장하는 시점, 다음 이동 시 저장 여부, Undo의 범위를 명시. 클릭 highlight를 저장 완료로 오해시키지 않는다. 다음/이전/닫기/실패·revision conflict에서 draft가 보존되고 선택을 놓치지 않으며 retry가 멱등이어야 한다.

### UX-32 · 통과 테스트가 제품 상태/겹침/기기간 역할을 보장하지 않음 — S2 / P1 / D+C

- 근거: `tests/test_menu_appkit_layout.py:203-240` sidebar/min size, `1123-1217` 결과 가상화/열수/resize, `1278-1330` input/focus; fixture 16개 통과와 UX-16 겹침이 공존. `tests/test_mobile_client.py`는 HTTP/서명/투영 중심이며 Android View 렌더 시험과 같지 않다.
- 제안/수용: 구현을 그대로 복제하는 테스트보다 위험한 상태 전이·모드 조합·누적 수·실패·재방문의 계약 테스트를 추가. 설치본은 합성 데이터 실행모드에서 macOS AX/스크린샷, Android instrumentation/TalkBack, 웹 viewport/키보드로 확인한다. API 200이나 native object 생성만으로 화면 품질 완료 처리하지 않는다.

## 7. 화면별 재설계 제안

| 화면 | 목적/주 행동 | 구조 및 상태 변경 | 관련 ID |
|---|---|---|---|
| 첫 실행/연결 | 사용할 사진 출처와 기기를 준비 | ①사용할 출처 선택 ②필요 권한 설명 ③연결 확인 ④작은 범위로 시작. 모든 권한을 최초 일괄 요구하지 않음. 연결 이후 재방문에는 작업 홈 | 03,09~13 |
| 홈 | 다음 할 일을 발견 | 확인 필요 → 이어할 작업 → 사진 선택 → 최근 결과/이야기. 상단 준비 상태는 실제 capability, 운영 MCP는 설정 하위 | 01,03,04,08 |
| 사진 선택/분류 | 명확한 범위로 작업 시작 | 좌측/상단 출처, 범위 chips, 준비 상태, 분석 방식의 점진 노출, 장수·다운로드/스크린샷 제외·재사용 요약, 단일 CTA. 접수 후 작업 상세로 이동; 보존된 draft는 '이전 설정' | 01,02,10,11,14,15 |
| 연결/Google Picker | 현재 필요한 계정 행동 완료 | 연결 필요 / 사용자 선택 대기 / 준비 / 실패 / 완료의 진행 상태. browser 돌아오기·cancel·다시 선택을 명확히. OAuth client 기술 설정은 고급 | 12,13 |
| 작업 목록/상세 | 기기에서 시작한 작업을 끝까지 추적 | job/run/operation 관계를 사용자 작업 하나로 묶고 내부 id는 상세. 진행·조치·부분완료·완료·재시도/취소 가능 여부. 결과로 이어가기 및 마지막 갱신 시각 | 01,04,05,08,18,26 |
| 결과 검토 | 사진을 비교·선택하고 다음 행동 | 상단 scope/header/모드/검색, 중앙 virtualized gallery+독립 inspector, 하단 selection action bar. 이야기/저장 모두 같은 선택 summary, filter와 선택 범위 구분 | 05,14,16~18,20,29,31 |
| 이야기 목록/상세 | 보관한 이야기를 찾고 감상 | 운영 목록/새 생성과 상세 감상을 다른 route로. shell breadcrumb/back 일치, 상세에서는 Story 담당의 renderer를 유지. 삭제/재분석/공유의 현재 구현을 common action 계약으로 노출 | 06~08,17,21,28 |
| 인물 목록/예외 검토 | 모호한 연결만 확인 | 신뢰 가능한 count/조회시각, 현재 확정과 제안 비교, 충돌/동명이인 구별. 실패를 0건 성공으로 바꾸지 않음. 동의와 자동인식은 별도 설정 | 22~27,29,31 |
| 저장/내보내기 | 선택 결과를 승인된 대상에 저장 | 대상별 plan 요약 → 승인 → durable receipt/작업 → 완료/부분완료. 닫아도 재개 가능, 완료된 대상 중복 실행 방지 | 18,31 |
| 저장 공간 | 보관·캐시 용량과 정리 영향을 이해 | 전체 count/table/search/sort, 참조와 물리 용량, '실제 확인 시각'. cache cleanup preview와 사용자 생성 자산/원본 구분 | 19,30 |
| 설정/환경/조치함 | 막힌 이유와 다음 행동 해결 | 기기 연결/출처 계정/사진 권한/위치·이름 공개/저장/고급 실행 환경 분리. 장치별 권한은 해당 장치에서 변경 | 08~13,25,30 |

## 8. 새 IA와 공통 객체 계약

### 8.1 목표 IA

```text
홈
  확인 필요 / 이어할 작업 / 최근 결과 / 최근 이야기
사진
  출처·범위 선택 → 실행 전 확인 → 작업 시작
  검색 / 보관된 추천 / 분석 결과 / 저장할 선택
작업
  진행·대기·조치 / 완료·부분완료 / 실패·중단 / 내보내기
  작업 상세 → 결과 검토 → 선택으로 저장 또는 이야기
이야기
  목록 / 새 만들기 / 상세 감상 (Story 담당)
인물
  목록 / 확인 필요 / 충돌 비교 / 동의·자동인식
설정
  연결된 기기 / 사진 출처·계정 / 권한·개인정보
  저장 공간 / 고급 실행 환경·MCP / 도움말
```

Mac sidebar는 위 계층을 직접 보여주고 결과 별도 창을 유지하더라도 breadcrumb와 object scope를 공유한다. Android bottom nav는 `홈 / 사진 / 작업 / 이야기` 4개, 인물은 사진 내 '인물'과 확인 필요 deep link로 진입; 설정/조치함은 상단. 좁은 모바일 웹도 같은 의미를 쓰되 지원하지 않는 작업은 연결된 Mac으로 전달할 방법을 명확히 한다. 이 IA는 제안이며 이번 감사에서 구현하지 않았다.

### 8.2 공통 상태와 언어

- 사진 출처: Apple Photos / Google Photos / 로컬 폴더. Android 원본 GPS는 출처 사진을 보강하는 능력이며 일반 사진 분석 출처와 혼동하지 않는다.
- scope: source, date/timezone, 명시 선택 또는 기간, limit, 제외, 재사용/재분석, location readiness. 표시 장수는 전체 후보/이번 분석/추천/선택/저장 완료를 각각 명명한다.
- 사용자 작업: stable operationId에 필요할 때 jobId/runId/resultSnapshotId/storyId/exportReceiptId를 연결. UI는 내부 id들을 병렬 작업으로 중복 표시하지 않는다.
- 작업 상태: 제출 중 / 대기 / 준비 중 / 분석 중 / 확인 필요 / 부분 완료 / 완료 / 실패 / 중단. `accepted`는 완료가 아니며 `unknown`은 0건이 아니다.
- 선택 상태: inspector focus / 저장·Story 선택 / 추천 판정 / 이름 확정 / 공개 동의는 각각 독립. 같은 체크 모양으로 의미를 혼합하지 않는다.
- freshness: '마지막 확인', 현재 조회 실패, 백그라운드 새 작업 진행을 함께 표현. 이전 결과를 지우지 않되 최신 성공처럼 표시하지 않는다.
- recovery: 재시도는 같은 작업 재개인지 새 작업 생성인지 설명. mutation은 plan과 명시 승인·revision·idempotency를 보존. UX 편의 때문에 권한/원본 쓰기/AI 실행 경계를 제거하지 않는다.

## 9. 기기 역할과 기능 parity

표의 현재는 C 중심이다. Android 실기기 동작은 이번 담당 U, Mac 설치본 일부는 L, 웹 owner 빈 상태만 D다. 웹은 소유자/공유자 권한에 따라 기능이 다르므로 native 앱의 전체 대체로 간주하지 않는다.

| 기능/객체 | Mac 현재 | Android 현재 | 소유자 웹·모바일 웹 현재 | 목표 parity와 역할 |
|---|---|---|---|---|
| 출처·폴더 선택 | Apple/local/Google Picker | 날짜 Apple/Google orchestration; phone GPS | 날짜 Apple/Google form | Mac은 로컬·파일 관리 주 담당. 범위 언어·장수·readiness 공유 |
| 첫 실행/연결 | main+환경 검사, Google 별도 설정 | JSON/ADB 등록, Settings | Tailnet/owner 접근, 별도 APK 다운로드 | 사용자 pairing 안내·연결 capability 동등, 권한 변경은 해당 기기 |
| 통합 검색 | 폴더 파일명, 결과 필터 | Story 추천 paging | Story 내 인물 필터/목록 | 구조화 검색 query를 공유; 실제 가용 인덱스부터 |
| 작업/상태 | AppKit job history, 웹 operation 별도 | runs/manual polling/inbox | 진행중 operation/event 설명 | 동일 사용자 작업·상태·다음 행동. stale 분류 state 제거 |
| 전체 분석 결과 | job별 풍부한 검토·10k grid | 보관된 Story별 추천 중심 | Story 자산 중심 | 전체/추천 차이를 표시하고 snapshot 링크 공유. 모든 기기 장수 동일화 불필요 |
| 선택·이야기 생성 | 완료 결과→Story 이미 구현; selection 모드에서 숨김 | 날짜 생성·Story 조회/재분석 | 날짜 생성·삭제/재분석 현재 구현 | 선택 snapshot 의미 공유, 기기별 미지원 입력은 명시. 생성 후 정확한 Story로 이동 |
| 인물 확인 | 목록·예외·name/merge/split/undo | overview+별도 review, consent/auto | Story 인물 필터 중심 | 예외 count·동의·확정 상태 동등; 복잡한 병합/분리는 Mac 중심 가능 |
| 내보내기/앨범 | local/Apple plan·receipt, Google 새 사본 흐름 | Mac급 export 없음 | 공유 download 정책/사진 저장 | 원본/앨범 쓰기는 Mac 역할, 모바일은 안전한 공유본 저장 및 진행 조회 |
| GPS | 휴대폰에 조치 요청·결과 소비 | MediaStore 원본 위치·outbox | 기다림 안내 | Android 역할 유지, Mac/웹에서 범위별 필요/완료/실패 확인 |
| 저장 공간 | category·Story별 요약/정리 preview | 없음 | 본 감사 범위에서 대시보드 없음 | Mac 상세, 모바일 용량 요약은 선택. 물리·참조·캐시 의미는 공통 |
| 이야기 감상/공유 | WKWebView owner | native 목록+제한 WebView | owner/public renderer | 공통 renderer, route·권한·동작 발견성 검증. 테마 심층은 별도 담당 |
| 작업 이어하기 | export receipt/선택 저장 일부; 분류 stale | people review draft 복원, main 일부 | route/form 오류 복원 제한 | stable id 기반 재개, draft/scroll/query 복원, 중복 실행 방지 |
| 오류/접근성 | native label/focus 일부, raw 에러 잔존 | label/48dp 일부, 읽기전용 문구 오류 | focus/reduced-motion 일부, 대비 실패 | 공통 error code→행동, native AX/TalkBack 및 웹 keyboard를 따로 검증 |

## 10. 개선 작업 순서와 수용기준

### UX-A · 상태 신뢰와 명백한 배치/대비 복구 (P0)

대상 UX-01/02/04/16/22/28. 공유 job/operation projection으로 분류 상태 reconcile, action accessible metadata 통합, dashboard unknown/error 모델, 결과 header/body 영역 분리, owner primary contrast token 분리를 먼저 한다. 레이아웃 미관 개선보다 앞선다.

수용: 완료된 fixture가 분류/홈/작업/결과에서 모순 없이 표기; conflict/조회 실패에서 성공-empty 금지; visible rect intersection 0; 기본·dark theme의 CTA 대비 목표 통과; 변경/이동 없이 기존 scope/선택을 보존. 이 단계만으로도 부모가 관찰한 신뢰 문제를 재검증할 수 있어야 한다.

### UX-B · 목표 중심 IA와 연결/권한 (P1)

대상 UX-03/06~13/17/21/25/27. 네이티브 shell과 owner portal route 분리, 홈 주 행동, 기기 pairing 설계, capability별 권한, action inbox, 이야기/저장 selection bar를 순차 적용한다. Story renderer의 전면 변경은 별도 담당과 조율한다.

수용: 처음 사용자가 개발 JSON/OAuth jargon을 이해하지 않아도 자신의 현재 준비 단계와 다음 행동을 찾는다. 미등록/권한 거부/부분 권한/GPS 대기/세션 만료의 화면별 시나리오를 승인 없는 fixture에서 검증한다. 날짜 작업/Story 상세를 왔다 갔다 해도 shell 안내가 현재 화면과 일치한다.

### UX-C · 결과·검토·복구 공통 계약 (P1)

대상 UX-05/18/19/23/24/26/31. result snapshot scope, durable export activity, 전체 storage table, 충돌 review와 동명이인 구분, main process recreation 복원을 구현한다.

수용: 같은 작업을 어느 기기에서 시작했든 다음 행동과 결과 범위가 맞는다. export 부분완료 후 app 재실행에도 receipt를 찾고 중복 실행하지 않는다. 인물 충돌은 기존 확정을 보존하고 보류/검토 가능하다. 8개 이상 Story와 process recreation 사례를 포함한다.

### UX-D · 검색·적응형·키보드·시각 일관성 (P2)

대상 UX-14/15/20/29/30/32. 기존 AppKit/Java View를 유지한 구조화 query, measured grid/virtualization, incremental layout/focus 복원, semantic token 매핑 및 acceptance automation을 적용한다.

수용: 아래 matrix를 모두 수행하고 각각 실패/미검증을 남긴다. 단순 unit/API 성공률을 10/10 UX 판정으로 대체하지 않는다. 5명 안팎의 실제 사용자가 '첫 연결', '이전 작업 찾기', '선택으로 저장/이야기', '막힌 작업 재개'를 수행하는 간단한 사용자 관찰은 별도 승인된 환경에서 진행한다.

### 10.1 공통 acceptance matrix

| 축 | 최소 case | 합격 조건 |
|---|---|---|
| 데이터 | 0/1/50/100/1,000/10,000 사진, 0/7/8/100 Story | 검색/목록/선택/화면 접근과 count 의미 보장; overflow·누락 없음 |
| 상태 | draft/preview/submitting/queued/running/action/partial/completed/failed/interrupted/unknown | 서로 모순 없는 title/CTA/AX; terminal reconcile; 실패가 empty 성공으로 바뀌지 않음 |
| 출처 | local/Apple/Google 및 unavailable/미준비/선택 0건 | 실제 지원 scope와 필요한 권한·다운로드를 설명 |
| Mac | min, 1280×800, 부모 1398×812, 결과 1920×978, resize/tab 왕복 | header/inspector 충돌 0, CTA 접근, focus/선택/스크롤 유지 |
| Android View | 320/390/600/840dp, 가로, split-screen, fontScale 1/1.3/2 | 48dp actionable target, 의미 label/state, measured layout, system bar/keyboard 가림 없음 |
| 웹 | 390×844/768/1280, light/dark, 테마별, zoom 200%, reduced-motion | 화면 밖 가로 overflow 없음, 폼/input 오류 복구, 명확한 focus와 충분한 대비 |
| 보조기술 | keyboard-only, VoiceOver, TalkBack, 한글 IME | 같은 행동·상태·scope 읽힘, hidden control 제외, focus trap 없는 modal/Back |
| 이어하기 | foreground/rotation/process kill/창 닫기/재실행/TTL 만료 | stable object id/draft 복원, stale command 재검증, 중복 submit/무단 AI 실행 없음 |
| 복구 | network/session/권한/database lock/confirmed conflict/export partial | 이전 입력·성공 범위 유지, 구체적인 다음 행동 및 sanitized technical detail |
| privacy | owner vs public, 위치/이름/다운로드 opt-in, diagnostics | 공개 projection/승인 경계 보존, secret·개인 원본 출력 없음 |

## 11. 잔여 미검증과 부모 통합계획 재검토 기준

- 설치본 버전/소스 SHA 대조와 부모의 `04-live-observations-and-documentation.md`가 같은 폴더에 작성 완료되었다는 전달을 받았다. 그 문서의 재현 step와 이 문서의 L 이슈를 통합 시 연결한다. 여기서는 해당 문서 내용을 재감사하지 않았다. 이 문서는 전달된 관찰을 L로만 기록했다.
- 분류 stale state와 인물 충돌은 운영 데이터로 재실행하지 않았다. acceptance용 합성 terminal/conflict fixture를 추가해야 한다. 특히 인물의 실제 conflict 발생 분기와 같은 이름 카드의 관계는 확인되지 않았다.
- `adb devices`에서 연결 기기 0대이므로 설치 package 조회 및 Android UI 독립 검증은 불가했다. Android 실기기/OS 권한 dialog/부분 접근/회전·process kill/큰 글꼴/키보드·TalkBack 직접 검증 없음. Java View 구현 확인을 실기기 합격으로 표현하지 않는다.
- 웹 fixture는 JS 없는 empty owner rendering과 일부 CSS/AX뿐. 실제 owner login/서명 web bootstrap/세션 만료, enhanced JS, browser back, 모든 Story 테마, 공유·삭제·쓰기·AI 실행 E2E는 미검증.
- Mac NSView 생성·layout 시험은 pixel visual/VoiceOver 전체 검증이 아니다. parent live 이외 화면(로컬 folder/Google OAuth/추천 review/export recovery)은 C/기존 테스트 구조 조사 중심이다.
- 실제 사진 쓰기/클라우드/키체인 변경, 새 분석/색인/재분석/모델 실행, 서비스 배포·기기 설치는 수행하지 않았다. 사진·이름·장소·비밀값을 산출물에 복제하지 않았다.
- 부모가 보낼 통합 계획은 같은 UX 담당 관점에서 재검토 예정. 검토 기준: UX-01~32 각각의 owner/phase/acceptance가 빠짐없이 연결되는가, 현재 있는 기능을 '없음'으로 회귀시키지 않는가, 상태 신뢰 P0가 시각 polish보다 앞서는가, 기기 역할 차이를 의도적으로 설명하는가, Story 담당 계약과 route/state가 충돌하지 않는가, 미검증 항목을 완료로 바꾸지 않는가.

## 12. 증거 탐색용 파일 목록

모든 경로는 저장소 기준 상대 경로이며 §3~6의 이슈에 정확한 줄번호를 붙였다. 핵심 화면은 `src/photos_mcp/interfaces/appkit/{main,classification,results,people,local_browser,google_photos,recommendation_review}`; 웹은 `src/photos_mcp/interfaces/http/{story_web,story_experience,mobile_client}.py`; Android는 `android/location-bridge/app/src/main/java/com/photosmcp/locationbridge/{MainActivity,PeopleReviewActivity}.java`, 색상/Manifest는 같은 app의 res/src/main이다. 상태 conflict 원인은 `src/photos_mcp/application/person_identity_repository.py`다.

핵심 현재 비교 문서는 `docs/09-roadmap/01-active/23-pc-mobile-feature-parity-and-result-to-story-plan-2026-09-24.md:14-41,53-69,88-106`이다. 이 문서는 초기 문제와 이후 구현이 섞여 있어 코드 재검증을 거쳤다. 기존 검증 보고를 현재 독립 검증으로 복제하지 않았다.

이번 담당의 영속 산출물은 이 파일 하나다. 소스·테스트·기타 계획 문서에는 변경을 가하지 않았다.
