# PC·Android 기능 정합화와 완료 결과→Story 연결 계획

## 문서 상태

- 작성일: 2026-09-24
- 상태: 구현·macOS 설치본 배포 완료 · Python 회귀/Android debug·release 조립 통과 · 실제 등록 기기 E2E 대기
- 대상: macOS `PhotosMcp.app`, Android `PhotosMcp 앨범`, 공통 PhotosMcp daemon/BFF
- 우선 해결 과제: macOS의 `사진 분석 완료` 결과를 그대로 사용해 Story를 만들 수 없는 흐름 단절
- 이번 문서의 범위: 현행 기능·데이터 흐름 감사, 개선 원칙, 단계별 구현·검증 계획
- 이번 문서에서 하지 않는 일: 기존 Story·사진·작업 기록의 파괴적 변경, 운영 앱 재배포, Android 기기 설치

## 1. 결론

두 앱은 이미 상당한 공통 데이터를 사용하지만, **같은 기능을 다른 화면과 다른 데이터 투영으로 제공**하고 있다. 가장 큰 문제는 다음 두 “결과”가 이름만 같고 실제 소스가 다르다는 점이다.

1. macOS `사진 분석 완료`: 특정 photo-ranker `job_id`에 저장된 최대 10,000장의 분석 결과
2. Android `결과`: 현재 Story 또는 특정 Story manifest에 속한 추천 자산 projection

macOS의 완료 결과 창에는 선택·내보내기·앨범 저장은 있지만 Story 생성 동작이 없다. 별도 `Story` 탭에는 날짜 기반 새 분석 요청이 있지만, 현재 보고 있는 완료 결과와 연결되지 않는다. 따라서 사용자는 이미 분석된 사진을 보고도 날짜·출처·장수를 다시 입력하고 별도 분석 파이프라인을 시작해야 한다.

권장안은 UI 버튼만 복제하는 것이 아니라 다음 세 계층을 먼저 통일하는 것이다.

1. **완료 결과 스냅샷**: 어떤 작업의 어떤 사진을 어떤 순서로 Story에 사용했는지 불변 기록으로 고정한다.
2. **공통 Story 명령 서비스**: macOS와 Android가 `완료 결과`, `선택 사진`, `날짜 범위`, `기존 Story 재분석`을 같은 명령 계약으로 호출한다.
3. **공통 작업·오류 projection**: 어느 앱에서 시작했든 두 앱에 같은 operation, 진행 상태, 결과 Story와 오류 원인을 표시한다.

첫 구현은 **macOS 완료 결과 → 추천 사진으로 Story 만들기**를 우선한다. 이 경로는 기본적으로 모델 분석을 다시 실행하지 않고, 현재 결과를 관리 보관소에 안전하게 materialize한 뒤 Story manifest를 만든다.

## 2. 확인한 현행 구조

### 2.1 macOS 앱

macOS 사이드바는 `홈`, `사진 분류`, `Story`, `작업 기록`, `저장 공간`, `환경 및 권한`, `인물 관리`로 구성된다.

- `사진 분류`: Apple Photos, Google Photos, 로컬 파일 등 데스크톱 입력을 실행한다.
- `작업 기록`: AppKit state snapshot의 active/recent job을 보여주고 결과가 있으면 별도 `사진 분석 완료` 창을 연다.
- `사진 분석 완료`: 장면별/사진별 보기, 추천·검토 필터, 선택, 로컬/Apple/Google 내보내기, 저장 용량, 고급 품질 도구를 제공한다.
- `Story`: `WKWebView` 안에서 `/photos` 소유자 포털을 연다. 날짜·출처·구성·장수를 입력해 새 수동 작업을 등록하고, 기존 Story 조회·테마 변경·공유 생성/폐기·이야기 새로 구성을 제공한다.
- `저장 공간`: 추천 보관, Google 임시 원본, Story 파생 이미지, 분석·얼굴 데이터 및 Story별 용량을 표시한다.
- `환경 및 권한`: Photos 권한, MCP/daemon, Vision runtime 등 Mac 전용 준비 상태를 진단한다.
- `인물 관리`: 공통 private identity 저장소의 인물·예외 검토를 AppKit UI로 관리한다.

코드 근거:

- macOS 탭 구성: `src/photos_mcp/interfaces/appkit/main/controller.py::_build_sidebar`
- 완료 결과 창: `src/photos_mcp/interfaces/appkit/results/controller.py::PhotosMcpResultsController`
- 특정 작업 결과 열기: `src/photos_mcp/interfaces/appkit/menu/controller.py::showJobResult_`
- macOS Story 포털: `src/photos_mcp/interfaces/appkit/main/controller.py::_build_story`
- 날짜 기반 macOS Story 요청: `src/photos_mcp/interfaces/mcp/server.py::http_owner_manual_story`

### 2.2 Android 앱

Android는 native shell과 제한된 Story WebView를 결합한다.

- `홈`: Mac 연결, 최신 통합 작업, 최신 Story, 사용자 조치 수를 요약한다.
- `실행`: combined automation run과 Apple/Google 하위 진행 상태·오류를 표시한다.
- `날짜로 Story 만들기`: 기간·소스·장수·사진 구성을 미리 본 뒤, 해당 기간의 휴대폰 원본 GPS를 먼저 전송하고 수동 작업을 등록한다.
- `결과`: 현재 Story 또는 선택한 Story의 추천 자산을 native grid와 확대/플리킹 viewer로 표시한다.
- `Story`: 목록·보기와 수동 Story의 같은 기간 전체 재분석, 안전 삭제를 제공한다.
- `알림`: 완료·부분 완료·실패·사용자 조치 event를 모으고 읽음 처리한다.
- `설정`: 등록, GPS 동기화, Android 권한, 설치 버전/서버 최신 버전을 관리한다.
- `인물 관리`: 공통 identity 저장소를 사용해 인물 현황, 얼굴별 연결, 새 이름, 무시, Story 이름 동의와 자동 인식을 관리한다.

코드 근거:

- Android 화면: `android/location-bridge/app/src/main/java/com/photosmcp/locationbridge/MainActivity.java`
- Android owner API: `android/location-bridge/app/src/main/java/com/photosmcp/locationbridge/OwnerApiClient.java`
- mobile BFF route: `src/photos_mcp/interfaces/http/mobile_client.py::route_specs`
- mobile DTO/projection: `src/photos_mcp/application/mobile_client.py`
- GPS ledger: `src/photos_mcp/infrastructure/mobile_location`

### 2.3 공통 백엔드

두 앱은 다음 핵심 원장을 공유한다.

- combined/manual curation operation과 automation run
- recommendation collection/member와 managed recommendation asset
- Story manifest/presentation/share package
- private person identity와 얼굴 관측/검토 상태
- mobile location ledger와 Story location projection

하지만 macOS 직접 분류 결과는 photo-ranker job/result 저장소에 먼저 남고, Android 결과는 Story/recommendation projection을 읽는다. **현재는 이 경계 사이에 “이 완료 결과를 하나의 Story source로 고정한다”는 공통 계약이 없다.**

## 3. 기능 비교표

표의 `부분`은 기능은 있으나 다른 앱과 같은 범위·상태·데이터 의미를 보장하지 않는다는 뜻이다.

| 기능 영역 | macOS 앱 | Android 앱 | 현재 판단 |
|---|---|---|---|
| 서버·연결 상태 | 홈/환경 진단 | 홈/설정 연결 상태 | 부분 정합. 표시 정보와 원천 projection이 다름 |
| 사진 분석 시작 | 로컬·Apple·Google 직접 분류 | 날짜 기반 Apple·Google Story 작업 | 기기 역할에 맞는 차이. 억지로 동일화하지 않음 |
| 날짜 Story 새 작업 | Web form으로 즉시 queue 등록 | native preview → GPS 선동기화 → 서명 등록 | 기능 불균형. PC에는 preview·재분석 선택·GPS 상태가 없음 |
| 작업 목록·상세 | AppKit active/recent job snapshot | combined run·provider timeline | 의미가 다름. 하나의 작업을 양쪽에서 찾기 어려움 |
| 완료 분석 결과 | 작업별 최대 10,000장, 풍부한 검토·선택·내보내기 | 현재/Story별 추천 결과, 읽기 중심 | 동일 명칭이지만 서로 다른 데이터 모델 |
| 완료 결과로 Story 만들기 | 없음 | 없음. 날짜 작업만 가능 | **최우선 blocker** |
| Story 목록·열람 | 있음 | 있음 | 공통 renderer를 사용해 비교적 일치 |
| Story 테마 | 소유자 포털 | 제한 WebView | 공통 presentation 저장소 사용 |
| Story 재분석 | 소유자 포털에는 전역 이야기 재구성만 있음 | 수동 Story 범위 전체 재분석 + GPS 선동기화 | Android 기능이 더 완전함 |
| Story 삭제 | 소유자 포털에 없음 | soft delete + 공유 폐기 | PC parity 필요 |
| Story 공유 관리 | 30일 공유 생성·폐기·다운로드 정책 | Story 열람 중심 | Mac 중심 기능으로 유지하되 모바일 상태 조회는 유용 |
| 추천 사진 내보내기 | 로컬·Apple·Google 대상 | 없음 | Mac 전용으로 유지 |
| 저장 공간 | 전체·Story별 대시보드와 정리 계획 | 없음 | Mac 전용이 적합. 모바일에는 요약만 선택적 제공 |
| 인물 관리 | 상세 AppKit workspace | native overview·얼굴 review·동의·자동 인식 | 공통 저장소지만 진입·표현·일부 action이 다름 |
| 위치/GPS 수집 | 수집 불가, 결과만 소비 | 원본 MediaStore GPS 수집·암호화 outbox | Android 전용 유지. PC는 상태와 요청만 표시 |
| 알림·조치함 | 전용 inbox 없음 | event inbox/ack | PC에도 같은 event projection 필요 |
| 앱 버전·권한 | Mac 환경/Photos/Vision 진단 | Android 버전/사진·위치 권한 | 기기별 기능으로 유지 |

## 4. 핵심 문제와 UX 심각도

### S4 · 핵심 작업 완료 불가: 완료 결과→Story 단절

사용자가 `사진 분석 완료` 화면에서 좋은 사진을 확인·선택해도 가능한 다음 동작은 내보내기나 앨범 저장이다. Story를 만들려면 창을 닫고 Story 탭으로 이동해 날짜 기반 작업을 새로 구성해야 한다. 사용자가 가진 “이 결과로 Story를 만든다”는 목표와 제품의 객체 모델이 일치하지 않는다.

### S3 · 같은 명칭의 데이터 의미 불일치

macOS `결과`는 job result이고 Android `결과`는 Story/recommendation result다. 결과 수, 선택 상태, 정렬과 사용 가능한 동작이 앱마다 다르므로 “PC에서 본 결과가 왜 휴대폰에 없지?”라는 오해를 만든다.

### S3 · Story 관리 기능 불균형

Android에는 정확 범위를 복원하는 재분석과 soft delete가 있지만 macOS 소유자 포털에는 같은 동작이 없다. 반대로 공유 생성·폐기는 macOS에 집중되어 있다. 데이터는 즉시 동기화되더라도 한쪽 앱에서 동작을 찾지 못한다.

### S3 · 수동 Story 시작 계약 불균형

Android는 preview와 GPS preflight를 강제하지만 Mac form은 곧바로 `mac_app` operation을 queue에 넣는다. 특히 과거 Android 사진의 GPS 보강이 필요한 범위는 Mac이 직접 MediaStore를 읽을 수 없으므로, PC에서 시작한 작업이 “위치 없는 Story”가 되는 원인을 사용자가 알기 어렵다.

### S2 · 작업·오류·조치 projection 분산

Android는 combined run timeline과 event inbox를 사용하고, macOS는 AppKit job snapshot과 별도 Story 진행 카드에 의존한다. 작업 ID가 같아도 양쪽 화면의 제목·진행률·오류 상세가 같다는 보장이 없다.

### S2 · 인물 관리 기능의 발견성 차이

공통 저장소와 상당수 공통 action이 이미 있지만 macOS는 독립 사이드바, Android는 설정 하위 진입점이다. 같은 인물 상태를 관리하는 기능이라는 점이 충분히 드러나지 않는다.

## 5. 현재 흐름과 목표 흐름

### 5.1 현재

```text
macOS 사진 분류
  → photo-ranker job/result
  → 사진 분석 완료 창
  → 선택/내보내기/앨범
  └─ Story로 가는 연결 없음

macOS Story 탭
  → 날짜·소스·장수 다시 입력
  → 별도 manual curation
  → recommendation collection
  → Story manifest

Android 날짜 Story
  → preview
  → 선택 기간 원본 GPS 동기화
  → signed manual curation
  → recommendation collection
  → Story manifest
  → Android 결과/Story
```

### 5.2 목표

```text
                         ┌─ 날짜 범위 새 분석
macOS 완료 결과 ─┐       ├─ 완료 결과 전체 추천
macOS 선택 사진 ─┼──────▶│  StoryCommandService
Android 날짜 범위┤       ├─ 선택 사진
기존 Story 재분석┘       └─ 기존 Story 재분석
                              │
                              ▼
                    StorySourceSnapshot
               작업·사진·순서·선택 정책 고정
                              │
                              ▼
              managed asset 확인/materialize
                              │
                              ▼
            Story manifest + presentation + operation
                     │                         │
                     ├─ macOS Story/작업/알림 ┤
                     └─ Android Story/작업/알림
```

## 6. 권장 제품·화면 설계

### 6.1 macOS `사진 분석 완료`

기본 `사진 보기` workspace의 주동작을 다음처럼 정리한다.

- 1차 CTA: `추천 결과로 Story 만들기`
- 선택 모드 CTA: `선택한 사진으로 Story 만들기`
- 기존 내보내기·앨범 저장은 `선택 및 저장` workspace에 유지
- 고급 품질 도구는 현재처럼 developer/advanced 영역에 유지

Story 버튼을 누르면 새 전체 페이지가 아니라 짧은 sheet를 연다.

```text
Story 만들기

원본 결과       2026-09-24 사진 분석 · 추천 36장
포함 범위       장면별 베스트 18장 / 선택한 12장
촬영 날짜       2026-08-06 ~ 2026-08-09
인물            확인된 인물 3명 · 미확인 2장
위치            GPS 11장 · 장소 보강 4장 · 위치 없음 3장

[ ] 다시 분석하고 최신 결과 사용
기본값: 현재 분석 결과를 그대로 사용하며 외부 앨범은 변경하지 않습니다.

취소                         Story 만들기
```

기본은 재분석하지 않는다. `다시 분석`은 비용·시간·GPS precondition이 다른 작업이므로 같은 버튼의 숨은 부작용으로 만들지 않는다.

### 6.2 macOS `Story`

기존 owner WebView를 장기적으로 다음 두 층으로 분리한다.

- AppKit native header/list: Story 목록, 상태, 생성 원천, 사진 수, 재분석, 삭제
- 공통 renderer WebView: 선택한 Story 본문과 테마

즉 Story 관리 action은 native command service를 사용하고, WebView는 감상과 presentation에 집중한다. 단기 1차 구현에서는 현 WebView를 유지하면서 결과→Story CTA와 정확한 Story 선택 route만 추가해도 된다.

각 Story 카드에 양쪽 앱에서 같은 action을 제공한다.

- `Story 보기`
- `추천 사진 보기`
- `같은 범위 전체 재분석` — 가능한 Story에만 표시
- `Story 삭제` — 원본·추천 archive·인물·GPS는 보존
- `공유 상태` — macOS는 생성/폐기, Android는 우선 조회

### 6.3 Android

Android 고유 기능은 유지한다.

- 원본 GPS 읽기와 encrypted outbox
- Android 권한과 앱 버전
- push/event 수신
- 휴대폰 사진 수 preview

PC와 동일하게 만들 필요가 없는 기능을 억지로 복제하지 않는다. 대신 PC가 Android 기능의 **상태와 필요한 조치**를 볼 수 있게 한다.

- `휴대폰 GPS 동기화 필요`
- `마지막 위치 동기화 시각`
- `선택 기간 조회/수신 수`
- `Android에서 계속` event

PC에서 과거 날짜 재분석을 시작했는데 휴대폰 위치 정보가 필요한 경우에는 분석을 먼저 시작하지 않는다. operation을 `waiting_mobile_location`으로 만들고 Android 알림함에서 해당 범위를 동기화한 후 같은 operation을 진행한다.

## 7. 공통 데이터·API 설계

### 7.0 2026-09-24 구현 결정 — 10,000장 상한과 Google Picker 세션 경계

- 논리 작업의 분석 상한은 Apple Photos, 로컬, Android 수동 작업 모두 **10,000장**으로 통일한다.
- Google Photos Picker는 Google 제공 세션 상한인 **2,000장**을 넘길 수 없으므로, 최대 10,000장 요청은 최대 다섯 개의 순차 Picker 세션으로 구성한다.
- 각 Google 세션은 `선택 → 다운로드 → 분석 완료 → 추천 보관`을 끝낸 뒤 다음 세션으로 넘어간다. ranker가 비동기로 `pending`을 반환하면 해당 추천 보관 receipt가 terminal이 될 때까지 대기한다. 각 단계의 다운로드/GPS 전송은 기존 100장 checkpoint를 유지한다.
- 10,000장은 Google Picker에서 선택할 수 있는 **총 후보 수**의 상한이다. 이미 분석한 사진·동영상이 다운로드 단계에서 제외되어도 그 항목은 Picker quota와 날짜 오프셋에는 포함한다. 따라서 재실행 시 중복 제외를 위해 추가 세션을 열어 총 선택 수가 10,000장을 넘지 않는다.
- 다섯 세션은 하나의 Google 하위 run 아래 `analysis_run_ids`, batch 진행률, 통합 recommendation storage로 기록한다. 마지막 세션이 끝나기 전에는 Google-first gate를 열지 않으므로 Apple 병렬 분석이 첫 번째 2,000장만 보고 시작되지 않는다.
- 사용자가 요청한 날짜 범위에 사진이 부족하면 그 시점의 실제 선택 수로 정상 종료하고, 남은 수는 operation의 `unfinished_count`에 남긴다. 이전에 처리된 Google 자산과 PhotosMCP가 만든 출력 앨범 자산은 재분석 옵션이 없는 한 다시 포함하지 않는다.

### 7.0.1 이번 구현 범위와 후속 UX

- 구현됨: 공통 10,000장 상한, Google 2,000장 session coordinator, Mac 결과 갤러리 10,000장, completed result→managed recommendation→불변 source snapshot→scoped Story, Mac Story의 재분석/삭제 action. 다회 Google Picker 작업의 모든 recommendation collection은 snapshot과 Story scope에 함께 기록되어 앞선 2,000장 묶음이 빠지지 않는다.
- 다음 UI polish: `이 결과로 Story 만들기`의 sheet에서 전체 추천/사용자 선택 사진을 고르고 범위·GPS 상태를 미리 보여 주는 단계와 Mac native 알림 inbox.

### 7.1 `StorySourceSnapshot`

새 Story가 어떤 결과에서 만들어졌는지 다음 최소 정보를 불변 저장한다.

| 필드 | 의미 |
|---|---|
| `source_snapshot_id` | 임의 opaque ID |
| `source_kind` | `completed_result`, `selected_assets`, `date_range`, `story_reanalysis` |
| `source_run_id` | combined/manual run이 있으면 기록 |
| `photo_ranker_job_id` | 직접 분류 결과에서 시작한 경우 기록 |
| `collection_ids` | 기존 recommendation collection 참조 |
| `asset_ids` | 순서가 고정된 managed local asset ID |
| `selection_policy` | scene best, recommended, explicit selection |
| `content_hash` | 사진 ID·순서·정책의 무결성 hash |
| `created_by` | `mac_app`, `android`, `automation` |
| `created_at` | UTC 저장, 화면은 KST 표시 |

원본 절대 경로나 Android provider ID를 mobile DTO에 노출하지 않는다.

### 7.2 공통 명령

응용 계층에 하나의 명령 서비스를 둔다.

```python
create_story(
    source_kind,
    source_ref,
    selection_policy,
    title=None,
    reanalyze=False,
    initiator="mac_app|android|automation",
    idempotency_key=...,
) -> StoryOperation
```

UI별 HTTP form이나 signed mobile request는 이 명령의 adapter가 된다. macOS는 로컬 application call 또는 loopback owner route를, Android는 현재 owner session·P-256 서명 경계를 유지한다.

### 7.3 완료 결과 materialize

photo-ranker 결과에는 원본 경로·preview만 있고 Story가 요구하는 managed `local_asset_id`가 없는 항목이 있을 수 있다. 따라서 완료 결과→Story는 다음 순서를 지킨다.

1. job/result artifact와 선택 revision 확인
2. 선택한 photo ID가 해당 job에 속하는지 검증
3. 파일 존재·hash·MIME·크기 확인
4. 필요한 사진만 추천 관리 보관소에 원자적 materialize
5. managed asset ID와 순서를 snapshot에 고정
6. snapshot만 사용해 Story 생성
7. 인물·위치 projection 갱신
8. manifest와 operation을 원자적으로 ready 전환

중간 실패 시 기존 Story와 외부 앨범은 변경하지 않는다. 부분 materialize 파일은 receipt를 기준으로 재개하거나 안전 정리한다.

### 7.4 공통 작업·오류 projection

두 앱이 같은 operation을 다음 공통 상태로 표시한다.

- `queued`
- `waiting_mobile_location`
- `materializing`
- `generating_story`
- `ready`
- `partial`
- `failed`
- `cancelled`

오류는 최소한 `error_code`, `error_stage`, 사용자 설명, 재시도 가능 여부, source별 오류를 제공한다. raw stack trace, 로컬 경로, secret은 노출하지 않는다.

## 8. 단계별 구현 계획

### Phase 0 · 계약과 회귀 기준 고정

**상태: 완료.** 대용량 상한, 기존 Android Story projection, non-destructive storage/Story 회귀를 Python 전체 테스트에 포함했다. Apple 범위 미리보기도 10,000장 + 1건을 조회해 정확한 cap 상태를 표시한다.

목표: 기존 동작을 바꾸기 전에 두 결과 모델과 Story source 경계를 테스트로 고정한다.

- macOS direct result payload와 Story asset 요구 필드를 명시한다.
- Android/mobile result projection이 Story-scoped라는 사실을 contract test로 고정한다.
- Story 생성이 원본 삭제·앨범 쓰기·기존 Story 대체를 하지 않는 안전 조건을 고정한다.
- 기존 날짜 Story, 자동 Story, 공유, 인물·GPS projection 회귀 기준을 만든다.

완료 조건:

- “현재 결과를 Story로 쓰는 데 필요한 필드” 누락 보고서가 만들어진다.
- 직접 결과와 recommendation collection 사이 ID mapping 전략이 테스트로 증명된다.

### Phase 1 · 공통 Story source/operation 구현

**상태: 완료(1차).** `story_source_snapshots`와 결과→관리 보관→scoped Story 서비스를 추가했다. snapshot에는 경로 대신 job/collection/policy hash만 저장한다.

추가 검증: 10,000장 Google 작업의 다섯 세션 컬렉션을 하나의 Story snapshot과 Story scope로 보존하는 회귀 테스트를 추가했다.

목표: UI와 독립적으로 완료 결과에서 정확한 Story를 만들 수 있게 한다.

- `StorySourceSnapshot` persistence와 repository API 추가
- 완료 결과 validation/materialization service 추가
- idempotent `StoryCommandService` 추가
- source snapshot → Story manifest lineage 저장
- 실패·재시도·동시 실행 보호

완료 조건:

- fixture job에서 선택한 자산만 정확한 순서로 Story가 된다.
- 같은 idempotency key는 하나의 operation/Story만 만든다.
- 기존 분석 모델을 호출하지 않았음이 spy/test로 확인된다.

### Phase 2 · macOS 완료 결과→Story UX

**상태: 완료(핵심).** `사진 분석 완료`의 기본 사진 보기 화면에서 `이 결과로 Story 만들기`를 제공하며, 재분석 없이 추천 결과를 Story로 만든 뒤 Story 탭으로 연다. 체크된 사진이 있으면 modal 선택 창에서 `선택한 N장`과 `추천 장면 사진`을 분리해 고를 수 있다. 명시 선택은 화면 경로를 신뢰하지 않고 photo-ranker에 저장된 선택 ID와 재대조한 뒤 별도 policy version으로 materialize한다.

목표: 사용자가 결과 창을 떠나지 않고 Story를 만든다.

- 결과 payload에 stable job/result revision 추가
- `추천 결과로 Story 만들기`, `선택한 사진으로 Story 만들기` CTA 추가
- source summary sheet와 누락 자산 안내 추가
- operation 진행률·완료 Story 열기 연결
- Story 탭 목록에 새 Story가 즉시 나타나도록 refresh/event 연결

완료 조건:

- 완료된 실제 작업 하나를 열어 3클릭 이내에 Story 생성을 시작한다.
- 생성된 Story의 사진 수·순서가 선택 창의 범위와 일치한다.
- Android Story 목록에도 같은 Story가 보인다.

### Phase 3 · Story 관리 기능 정합화

**상태: 완료(핵심).** Mac owner Story 화면에서 범위가 복원 가능한 Story는 재분석을 요청할 수 있고, 어느 Story나 soft delete할 수 있다. 삭제는 공유를 폐기하되 원본/추천 보관소는 제거하지 않는다.

목표: 생성 후 관리 기능을 양쪽에서 같은 의미로 제공한다.

- macOS에 정확 범위 재분석과 soft delete 추가
- Android와 같은 reanalysis spec 복원
- macOS Story 선택 route가 현재 선택 Story를 대상으로 share/refresh하게 수정
- 공유 상태 read projection을 Android에 추가
- theme/presentation conflict는 기존 revision guard 유지

완료 조건:

- 어느 앱에서 삭제해도 양쪽 목록에서 사라지고 활성 공유가 폐기된다.
- 재분석 실패 시 기존 Story가 남는다.
- 특정 Story에서 만든 공유가 최신 Story로 잘못 바뀌지 않는다.

### Phase 4 · 작업·알림·GPS handoff 통합

**상태: 완료.** Mac Story form에서 `휴대폰 원본 GPS를 먼저 확인하고 시작`을 선택하면 Google이 포함된 작업은 `waiting_mobile_location`으로 보류된다. Mac의 작업·알림 영역과 Android 알림함은 같은 operation ID와 기간을 보인다. Android의 `GPS 동기화 후 계속`은 선택 기간만 MediaStore에서 읽고 encrypted ledger 업로드의 완료 영수증을 보낸 뒤, SQLite 조건부 전환으로 **그 동일 작업만** `queued`로 풀어 준다. GPS 좌표·기기 asset ID·원본 경로는 curation operation에 저장하지 않는다.

목표: 시작한 앱과 관계없이 같은 상태와 조치가 보이게 한다.

- macOS 작업 화면에 manual/combined `StoryOperation` projection 통합
- macOS에 간단한 `알림·확인 필요` inbox 추가 또는 작업 화면 상단에 병합
- PC 시작 작업의 `waiting_mobile_location` event를 Android에 전달
- Android GPS receipt 후 동일 operation 재개
- 양쪽 오류 문구와 error code mapping 통일

완료 조건:

- PC에서 시작한 작업이 Android에 같은 operation ID·기간으로 보인다.
- GPS 미수신 상태에서는 분석이 시작되지 않고, Android 동기화 후 자동 재개된다.
- 한 앱에서 확인한 event의 ack 상태가 다른 앱에도 반영된다.

### Phase 5 · 정보 구조와 발견성 정리

**상태: 완료(이번 범위).** 결과와 Story의 용어를 `완료 분석 결과`와 `추천 Story`로 분리했고, 고급 검토 도구는 developer 영역에 유지한다. Android 결과는 100장 단위 page navigation으로 대용량 grid를 제한한다. Mac은 Story 소유자 작업 화면 상단에 `진행 중인 Story 작업`과 `알림·확인 필요`를 병합해 별도 native inbox가 없어도 조치 경로를 찾을 수 있게 했다. Android는 상단 알림에서 같은 GPS handoff를 즉시 이어갈 수 있다.

목표: 기능이 추가된 뒤 다시 복잡해지지 않게 한다.

- macOS의 `작업 기록`과 Story operation 명칭 통일
- Android `인물 관리`를 설정 하위 보조 기능이 아니라 Story와 연결된 명확한 진입점으로 보강
- Mac 전용 기능과 Android 전용 기능에 이유를 표시
- capability 기반으로 불가능한 버튼은 숨기고 대체 경로를 안내
- 오래된 중복 owner HTML action을 정리하되 renderer는 공용으로 유지

## 9. 검증 계획

### 9.1 자동 검증

- persistence: snapshot immutability, revision, idempotency, soft delete
- mapping: job photo ID → managed local asset ID 정확성
- security: mobile DTO에 절대 경로·정확 원본 metadata·secret 미노출
- Story: 선택 자산만 포함, 순서 보존, 기존 Story 비파괴
- cross-client API: Mac 생성 Story가 `/mobile-client/v1/stories`와 detail에 동일하게 보임
- AppKit: CTA enable 조건, 추천/명시 선택 scope, 실패 sheet
- Android: 새 Story 표시, 재분석/삭제/공유 상태 projection
- 전체 Python 회귀, Android lint/debug/release, macOS bundle smoke test

2026-09-25 현재 자동 검증 증거:

- Python: `.venv/bin/pytest -q` → **1,127 passed**
- Android: Homebrew Java 17.0.20.1과 Android SDK 36으로 `:app:testDebugUnitTest :app:assembleDebug :app:assembleRelease` → **BUILD SUCCESSFUL**. Java 26은 시스템에 유지하되 이 Android 모듈은 Java 17로 실행해 target/source compatibility를 고정했다.
- Handoff: PC Google 작업의 대기·Android signed receipt·동일 operation ID queue 복귀를 `tests/test_manual_curation.py`, `tests/test_mobile_client.py`에 추가해 검증했다.
- 대용량·Story 핵심 회귀: Google 다섯 세션, 결과→Story snapshot/materialize, 수동 범위·GPS handoff를 포함한 관련 64개 테스트 → **64 passed**.
- AppKit 대용량 결과: `test_results_gallery_scrolls_all_ten_thousand_items_without_pagination`은 10,000개 결과를 넣고 visible item만 생성되는 collection view와 스크롤 가능한 전체 content size를 검증했다 → **passed**.
- macOS 설치본: `PhotosMcp.app`을 새 staging bundle에서 `/Users/byoungyoungla/Applications/PhotosMcp.app`으로 교체하고 정상 종료·재실행했다. 설치된 bundle에서 `--health`, `--runtime-import-smoke`, `--vendor-runtime-smoke`가 모두 통과했고, `analysis_limits.py`와 `result_story_service.py` 포함을 직접 확인했다.
- mobile BFF: LaunchAgent `com.photosmcp.mobile-client`를 재기동해 새 코드 프로세스로 동작 중임을 확인했다. 서비스는 공격면을 줄이기 위해 unauthenticated `/health`·`/docs`를 제공하지 않으며, Android의 서명·세션 경계 안에서만 상태/명령 route를 연다.

### 9.2 실제 사용자 경로 E2E

1. macOS에서 완료된 분석 결과를 연다.
2. 장면별 추천을 확인하고 `추천 결과로 Story 만들기`를 누른다.
3. sheet의 사진 수·날짜·위치/인물 요약을 확인한다.
4. 재분석 없이 Story를 만든다.
5. macOS Story 탭에서 새 Story를 연다.
6. Android 앱 Story 목록과 추천 사진에서 같은 사진 수·순서를 확인한다.
7. Android에서 Story를 삭제하고 macOS에 즉시 반영되는지 확인한다.
8. 별도 Story로 PC 재분석을 요청해 `waiting_mobile_location`을 확인한다.
9. Android에서 GPS를 동기화하고 같은 operation이 재개되는지 확인한다. *(실제 등록 기기에서의 최종 수동 E2E만 남음)*
10. 실패·재시도·앱 재시작 뒤에도 중복 Story가 생기지 않는지 확인한다.

### 9.3 비파괴 확인

- 원본 사진 삭제 0건
- 기존 Apple/Google 앨범 변경 0건
- 사용자가 명시하지 않은 재분석 0건
- 기존 Story 선삭제 0건
- 인물 이름·동의·GPS ledger 초기화 0건
- 민감 경로·키·정확 좌표의 로그 노출 0건

## 10. 범위에서 제외할 것

- Android 원본 사진 전체를 Mac으로 새로 업로드하는 기능
- PC에서 Android MediaStore를 직접 탐색하는 기능
- Android에 Mac의 저장 공간 정리·로컬 Finder 내보내기를 그대로 복제하는 기능
- macOS와 Android UI를 픽셀 단위로 동일하게 만드는 작업
- Story 생성 버튼을 누를 때 암묵적으로 외부 앨범을 변경하는 기능
- 기존 photo-ranker 결과와 recommendation 저장소를 검증 없이 같은 ID로 간주하는 우회 구현

## 11. 권장 구현 순서와 확인 요청

승인 시 다음 순서로 진행한다.

1. Phase 0~1: 공통 source snapshot과 command service
2. Phase 2: macOS 완료 결과→Story 수직 기능
3. 실제 완료 작업 1건으로 macOS→Android 교차 확인
4. Phase 3: 재분석·삭제·공유 상태 parity
5. Phase 4: 작업·알림·GPS handoff
6. Phase 5: 화면 명칭·진입점 정리 및 전체 회귀

완료된 수직 범위는 **“PC의 완료 결과를 재분석 없이 정확히 Story로 만들고 Android에서도 같은 Story를 보는 것”**이다. 별도 UI polish는 기존 Story·원본·앨범을 바꾸지 않는 후속 개선으로 분리한다.
