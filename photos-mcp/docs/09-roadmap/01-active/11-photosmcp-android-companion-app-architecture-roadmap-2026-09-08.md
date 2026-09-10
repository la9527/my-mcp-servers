# PhotosMcp Android Companion 앱 아키텍처·제품 로드맵

## 문서 상태

- 작성일: 2026-09-08
- 상태: Phase 0~4 read-only vertical slice, visual redesign, native 추천 grid, 수동 Story·재분석·안전 삭제와 개인 베타 APK 0.5.2 구현 완료. Phase 5~7 진행 중
- 대상: 기존 `com.photosmcp.locationbridge` Android 앱과 PhotosMcp 서버
- 검토 방식: 제품·UX, Android 클라이언트, PhotosMcp 연계, 개인정보·보안의 네 관점에서 병렬 검토 후 현행 코드와 대조
- 목표: GPS 메타데이터만 전송하던 개인용 APK를 **사진 처리 현황, 추천 결과, Story Album, 공유 상태를 한곳에서 확인하는 PhotosMcp 전용 Companion 앱**으로 확장한다.

이 문서는 당장 APK를 전면 재작성하라는 의미가 아니다. 현재 검증된 GPS 수집·암호화 outbox·기기 서명을 보존하면서, 서버에 모바일 전용 조회 계층을 추가하고 화면을 수직 기능 단위로 확장하는 구현 순서를 정한다.

## 2026-09-08 구현 진행 현황

설계 확정 직후 첫 운영 가능한 vertical slice를 구현했다. 원래 순서대로 read-only 기반부터 연결했으며 공개 GPS 수신 경계는 변경하지 않았다.

| 범위 | 상태 | 구현 결과 |
|---|---|---|
| Phase 0 계약·보안 경계 | 완료 | `K_ingest`와 별도 Android Keystore `K_owner`, P-256 challenge 서명, 일회용 nonce, 10분 bearer, revoke 시 session version 폐기를 구현했다. |
| Phase 1 Home·Runs | 완료 | `/mobile-client/v1` capabilities, dashboard, 통합 run 목록·상세·timeline과 native 홈·실행 화면을 구현했다. |
| Phase 2 Results·파생 이미지 | native slice 완료 | allow-list result DTO, cursor, bearer 보호 EXIF 제거 thumb/preview route, 적응형 실제 사진 grid, 큰 preview, 24MiB memory·128MiB private disk cache를 구현했다. 1,000장 가상화·연속 pagination은 남았다. |
| Phase 3 Story WebView | viewer 제스처 완료 | 45초 일회용 교환 코드, 제한 WebView에 더해 1×~4× pinch·double-tap zoom, bounded pan, 기본 배율 수평 fling, 키보드 fallback을 구현했다. 중복 버튼을 제거하고 하단 위치 indicator를 추가했다. |
| Phase 4 Event inbox | local inbox 완료 | dedupe 가능한 완료 event projection, device별 ack 저장소와 Android 알림함·확인 UI를 구현했다. FCM generic push는 남았다. |
| Phase 5 제한 제어·공유 관리 | 미착수 | capability를 `controls=false`로 명시해 read-only release에서 원격 mutation을 열지 않았다. |
| Phase 6 release 운영 | 개인 베타 전달 완료 | 앱 0.4.2 R8 release를 non-debuggable로 빌드하고 기존 debug 설치와 같은 인증서로 서명·정렬·v2/v3 검증했다. Tailnet owner 전용 설치 페이지·APK·checksum을 제공한다. 0.4.2 실기기 설치와 장기 고유 release keystore migration은 남았다. |
| Phase 7 선택 확장 | 보류 | 실제 사용 근거가 생긴 뒤 판단한다. |

운영 진입점은 `https://byoungyoung-macmini.tail53bcc7.ts.net/mobile-client`이며 Tailscale Serve의 443에서만 제공한다. 독립 loopback 서비스 `127.0.0.1:18794`와 LaunchAgent `com.photosmcp.mobile-client`를 사용한다. direct loopback 무인증 요청은 403이고 Tailnet 소유자 identity 요청은 200이다. 공개 Funnel 8443에서 `/mobile-client`와 `/mobile-location/v1/results`는 모두 404다.

자동 검증 결과는 Python 전체 `817 passed`, Android debug/release assemble·lint 성공이다. Android 16 실기기에서는 0.2.0의 Home·Runs·Results·Story·viewer swipe·Inbox·Settings와 light/dark·130% 글자·landscape를 실제 운영 데이터로 확인했다. 0.4.2 네이티브 추천 grid, 확대 제스처, 하단 위치 indicator와 시스템 바 보존은 단말이 분리되어 자동·계약·release 검증까지만 완료했으며 다음 연결 때 시각 회귀를 확인한다. Story 쪽 확대 제스처는 실제 운영 28장에 mobile viewport 자동화를 적용해 확인했다. UI 계약은 [Android Companion 디자인 시스템](../../07-design-system/06-android-companion.md), 최초 실기기 근거는 [Android Companion read-only vertical slice 검증](../../08-reports/01-validation/30-android-companion-readonly-vertical-slice-2026-09-08.md), APK 배포 근거는 [Android 추천 사진 grid와 APK 배포 검증](../../08-reports/01-validation/31-android-native-recommendation-gallery-and-apk-delivery-2026-09-08.md), 확대 제스처 근거는 [사진 뷰어 확대·플리킹 검증](../../08-reports/01-validation/32-photo-viewer-zoom-pan-fling-2026-09-08.md), 컨트롤 정리 근거는 [사진 뷰어 제스처 중심 컨트롤 정리](../../08-reports/01-validation/33-photo-viewer-gesture-first-controls-2026-09-08.md), 시스템 바 근거는 [Android 뷰어 시스템 바 보존](../../08-reports/01-validation/34-android-viewer-system-bars-2026-09-08.md)에 기록한다.

## 결론

앱으로 확장하는 방향이 맞다. 다만 기존 `/photos` HTML 전체를 광범위한 WebView에 넣는 방식은 장기 구조로 채택하지 않는다. 권장 구조는 다음과 같은 **네이티브 셸 + 제한된 Story WebView + 모바일 전용 BFF/API**다.

```text
Android PhotosMcp 앨범
├─ 네이티브: 홈, 실행 현황, 분석 결과, 알림함, 설정
├─ 제한 WebView: 읽기 전용 Story Album 본문
├─ 기존 엔진: GPS 추출, Keystore 키, AES-GCM outbox, 24시간 동기화
└─ 네이티브 네트워크: 기기 인증, API 조회, 파생 이미지 저장
                         │
                         │ Tailnet 전용 HTTPS 443
                         ▼
PhotosMcp Mobile BFF /mobile-client/v1
├─ 기존 run·automation·recommendation·story 원장을 안전한 DTO로 투영
├─ 기기 서명 + Tailscale 소유자 identity의 이중 확인
├─ 짧은 session, event cursor, 읽음/확인 상태
└─ thumb/preview/download 파생본만 제공

별도 유지
├─ Public 8443 /mobile-location : GPS write-only 수신
└─ Public 8443 /s/{share_id}    : 명시적으로 만든 30일 공유본
```

이 분리의 핵심은 다음과 같다.

1. 휴대폰이 외부에 있을 때 GPS 업로드는 지금처럼 VPN 없이 재시도할 수 있다.
2. 개인 분석 결과, 실행 로그, Story 관리 기능은 기본적으로 Tailnet 안에서만 읽는다.
3. Story는 서버가 만든 고정 HTML renderer를 재사용하되 WebView 권한과 이동 범위를 좁힌다.
4. 앱은 원본 파일 경로나 전체 내부 JSON을 받지 않고 화면에 필요한 최소 projection만 받는다.
5. 향후 push는 결과 데이터를 직접 싣지 않고 불투명한 event ID만 알린 뒤 앱이 인증 조회한다.

## 제품 가치와 범위

### 앱이 맡을 역할

- 오늘 또는 최근 실행의 통합 상태 확인
- Apple Photos와 Google Photos가 하나로 합쳐진 처리 단계 확인
- 처리 중인 사진 수, 완료 수, 추천 수, 보류·실패 수 확인
- 날짜·그룹별 추천 사진 grid와 개별 분석 이유 확인
- 생성된 Story Album 읽기
- 활성 30일 공유 상태, 만료 시각, 다운로드 허용 여부 확인
- GPS 메타데이터 동기화 상태와 마지막 성공 시각 확인
- 사용자 조치가 필요한 Picker, 권한, Mac offline 상태 안내
- 후속 단계에서 재시도·중지·Story 재생성·공유 생성/폐기 같은 제한된 명령 제공
- 작업 완료·부분 실패·사용자 조치 필요 알림 제공

### 앱이 맡지 않을 역할

- 휴대폰 원본 사진 전체 업로드
- PhotosMcp 내부 파일 시스템 탐색
- raw prompt, 모델의 전체 raw response, face embedding, 정확 GPS 표시
- WebView에서 임의 URL 탐색 또는 임의 JavaScript bridge 실행
- 앱만 믿고 공개 인터넷에 관리자 API 전체 노출
- Google Picker의 사용자 동의 경계를 우회
- 1차 릴리스에서 PhotosMcp의 모든 MCP 도구를 원격 실행

## 현행 구현에서 재사용할 것

### Android 앱

현재 앱은 `android/location-bridge` 아래에 있고 `minSdk 26`, `targetSdk 36`, Java 17을 사용한다. 다음 구현은 그대로 보존할 가치가 있다.

| 현행 요소 | 재사용 결정 | 이유 |
|---|---|---|
| package ID `com.photosmcp.locationbridge` | 1차에서 유지 | package를 바꾸면 앱 데이터, Keystore key, 등록 상태와 checkpoint가 끊길 수 있다. 사용자 표시 이름만 `PhotosMcp 앨범`으로 바꾼다. |
| Android Keystore P-256 서명 키 | ingest 키 유지, owner 키 추가 | 기존 `K_ingest`는 GPS append만 서명한다. 앱 열람·제어에는 별도 non-exportable `K_owner`를 만들고 서로 권한 승격이 불가능하게 한다. |
| AES-GCM encrypted outbox | 유지 | Mac이나 네트워크가 꺼진 동안 GPS payload를 보존하는 검증된 경계다. |
| `JobScheduler` 24시간 실행 | 유지 | GPS 증분 수집용으로만 유지한다. UI 데이터 polling이나 push 대체 수단으로 남용하지 않는다. |
| ADB 자동 설치·등록 helper | 유지 | 개발 설치에서 1회용 등록 JSON을 사용자가 입력할 필요가 없다. |
| Java 단일 `Activity` UI | 단계적 교체 | 장기 화면 구조에는 부족하다. GPS engine은 Java로 남기고 Kotlin·Compose 화면을 같은 모듈에 점진적으로 추가한다. |

package ID 변경은 앱이 안정화되고 migration/export-import 절차가 생긴 뒤 별도 결정한다. 현 단계에서 이름을 예쁘게 만들기 위해 검증된 key identity를 버리지 않는다.

### PhotosMcp 서버와 데이터

새 앱에 필요한 원천 데이터는 이미 대부분 존재한다.

| 현재 원장·서비스 | 앱에서 사용할 projection |
|---|---|
| `workflow_runs`, `run_events` | 모델 분석 작업의 stage, status, progress timeline |
| `photo_automation_runs` | Apple·Google 통합 일일 실행과 하위 작업 요약 |
| `processed_photo_assets` | 발견·분석·이월·완료 수 집계 |
| `user_action_requests` | Picker 또는 사용자 확인이 필요한 항목 |
| `recommendation_collections`, `recommendation_members` | 추천 묶음, 장면, 추천 순위와 분석 요약 |
| `local_recommendation_assets` | 검증된 로컬 추천 자산과 파생 이미지 연결 |
| private location/inference 원장 | 앱에 공개할 도시 단위 label과 근거 상태 |
| `recommendation_groups`, destination receipts | 날짜 그룹과 Apple/Google 앨범 반영 상태 |
| `story_manifests` | 소유자 Story revision과 생성 정보 |
| `shared_story_packages` | 30일 공유, 만료, 다운로드 허용, 폐기 상태 |
| `StoryShareService`, `ShareImageService` | 공유 정책과 EXIF 제거 파생 이미지 생성 |

중요한 차이는 데이터 존재와 모바일 API 존재가 같지 않다는 점이다. 현재 소유자 화면은 주로 `/photos` 한 페이지이고 상태·결과 화면별 JSON API가 없다. 앱이 DB payload를 직접 해석하게 하지 말고 BFF가 버전이 지정된 DTO로 변환해야 한다.

## 정보 구조와 사용자 흐름

### 앱 내비게이션

하단 내비게이션은 네 영역으로 제한하고 설정과 알림은 상단 아이콘으로 둔다. 결과 사진을 자주 보는 것이 앱의 핵심 가치이므로 `결과`를 처음부터 최상위에 둔다.

```text
홈       작업       추천       이야기
Home     Runs       Results      Stories
  └──────── 상단 알림함 · 설정 ─────────┘
```

### 1. 홈

- Mac/PhotosMcp 연결 상태: 온라인, Tailnet 필요, Mac 꺼짐, 서버 점검 필요
- 최신 통합 작업 카드: 처리 기간, Apple/Google 선택, 진행률, 경과 시간
- 핵심 수치: 발견, 분석, 추천, 저장, 중복, 실패, 이월
- 최신 Story 대표 이미지와 `스토리 보기`
- GPS bridge 카드: 등록 상태, 마지막 스캔, 전송 대기 batch 수
- 사용자 조치 카드: Google Picker, 권한, 재인증 등

Google 로그인과 Photos Picker는 Android WebView에 넣지 않는다. 사용자가 `Chrome에서 계속`을 선택하면 시스템 브라우저 또는 Custom Tab을 열고, 완료 후 opaque action ID를 통해 앱의 해당 run으로 돌아온다. embedded browser에서 Google 인증을 우회하려 하지 않는다.

홈의 첫 화면은 기술 로그가 아니라 “사진 정리가 어디까지 됐는가”를 답해야 한다.

### 2. 실행 목록·상세

실행 목록은 provider별 결과를 분리하지 않고 하나의 parent run을 기본 단위로 한다.

```text
2026-09-08 사진 정리
진행 중 · 643/1,000장 · 1시간 42분

✓ Apple 사진 발견        412장
✓ Google 사진 가져오기   231장
● 품질·장면 분석          643/1,000장
○ 추천 파일 저장
○ 앨범 반영
○ Story 생성
```

상세 화면에 표시하는 stage는 서버의 여러 내부 상태를 안정적인 공개 enum으로 정규화한다.

- `discovering`
- `waiting_user_action`
- `importing`
- `analyzing`
- `materializing`
- `publishing_album`
- `generating_story`
- `completed`
- `completed_empty`
- `partial`
- `failed`
- `cancelled`

1000장·최대 6시간 정책에서 제한시간을 넘기면 `partial`로 끝내고 남은 수량과 다음 실행 carry-over 여부를 분명히 표시한다.

### 3. 추천 결과

- 날짜·장소·장면별 grid
- 썸네일 선택 시 native 상세 sheet 또는 전용 상세 화면
- 추천 순위, 대표 점수, 선명도·노출·구도 등 사람이 이해할 수 있는 reason code
- `GPS 확인`, `문맥 추정`, `위치 미상` 구분
- 원천 provider는 보조 정보로만 표시
- 로컬 저장, Apple/Google 앨범 반영 영수증
- 정확 좌표, 로컬 절대 경로, 내부 provider ID는 표시하지 않음

Android GPS bridge는 사진 bytes를 Mac으로 보내지 않고, 현재 owner asset route도 추천 저장본의 파생 이미지만 제공한다. 따라서 모든 항목에 원본과 추천본 두 장이 있다고 가정하지 않는다. API와 화면은 `추천 저장본`, `분석용 preview 사용 가능`, `provider에 원본 존재`, `Android 위치 정보만 연결됨`, `현재 preview 없음`을 별도 availability 상태로 표현한다.

중간 분석 정보는 raw model text 대신 다음과 같이 설명 가능한 projection을 사용한다.

```json
{
  "asset_id": "mob_asset_random_id",
  "recommendation": {"slot": 1, "label": "대표 추천"},
  "quality": {
    "overall": 91.4,
    "reasons": ["초점이 선명함", "표정과 구도가 안정적임"]
  },
  "capture": {"local_date": "2026-09-07", "place": "서울", "place_state": "confirmed_gps"},
  "delivery": {"local": "verified", "album": "completed"}
}
```

### 4. Story

- Story 목록: 날짜 범위, revision, 사진 수, 생성 방식, 갱신 시각
- Story 상세: 기존 server-rendered Story Album을 읽기 전용 WebView로 표시
- grid 선택 후 큰 이미지 viewer는 기존 same-origin JavaScript controller를 사용
- WebView 위에는 native app bar와 닫기·새로고침·공유 상태 버튼만 배치
- 활성 공유 목록과 만료까지 남은 기간은 native 화면으로 표시
- 실제 외부 공유본 미리보기는 앱 내부 WebView가 아니라 Android Custom Tab으로 연다.

Swiper CDN을 다시 도입하지 않는다. 현재처럼 동일 출처의 vendored controller를 사용하고 viewer failure 시 단순한 이미지 상세 페이지로 graceful fallback한다.

### 5. 알림함

알림 종류는 다음으로 제한한다.

- 작업 완료
- 일부 완료와 carry-over
- 작업 실패
- 사용자 조치 필요
- Story 생성 완료
- 공유 만료 임박·만료
- GPS 동기화 반복 실패

각 항목은 하나의 event에 연결되고 앱에서 읽음 처리한다. Telegram은 초기 운영 보조 채널로 유지하되, 앱 알림이 검증되면 동일 event를 여러 메시지로 중복 보내지 않도록 notification policy에서 채널 우선순위를 정한다.

### 6. 설정·진단

- 서버 연결 상태와 마지막 성공
- 등록된 기기 identity와 기기 폐기 안내
- 사진·원본 위치 권한 상태
- Tailnet 연결 필요 여부와 Tailscale 앱 열기
- 캐시 크기, 캐시 비우기
- 알림 범주 설정
- 앱·서버 버전과 capability
- 개인정보 설명: 무엇을 읽고 무엇을 전송하는지
- 지원용 진단 export: secret·경로·GPS가 제거된 요약만 생성

신규 등록 fallback은 장기적으로 JSON 붙여넣기보다 Mac owner 화면이 만든 10분·1회용 QR 또는 승인 링크를 사용한다. ADB 자동 등록은 개발 경로로 유지하고, QR token도 clipboard·URL history·로그에 남기지 않는다. 권한은 앱 첫 실행에 한꺼번에 요청하지 않고 `Android 사진 위치 보강`과 `완료 알림`을 각각 처음 켜는 순간 설명 후 요청한다.

## Native와 WebView 역할 결정

| 기능 | Native | WebView | 결정 이유 |
|---|---:|---:|---|
| 홈·실행 현황 | O |  | 빠른 상태 갱신, offline state, 접근성, 알림 deep link |
| 분석 결과 grid·상세 | O | 선택 | 대량 이미지 pagination과 안정적인 상태 표현은 native가 유리하다. |
| Story 본문 |  | O | 현재 고정 renderer와 StoryManifest를 재사용하고 웹·앱 결과를 일치시킨다. |
| 외부 공유본 미리보기 | Custom Tab |  | 앱 session과 공개 share cookie를 분리한다. |
| 파일 다운로드·저장 | O |  | 앱 private cache와 MediaStore 저장 동의를 통제한다. |
| 실행 제어 | O |  | 서명, idempotency, 확인 UI가 필요하다. |
| 임의 외부 링크 | Custom Tab |  | WebView allow-list를 우회하지 않는다. |

### WebView 보안 계약

- 허용 host와 path를 `https://<tailnet-host>/mobile-client/story/*`로 고정한다.
- `http`, `file`, `content`, `intent`, data URL navigation을 거부한다.
- mixed content, third-party cookie, file access, content access를 끈다.
- JavaScript는 Story viewer에만 허용하고 `addJavascriptInterface`를 사용하지 않는다.
- 새 창과 외부 URL은 자동으로 열지 않고 명시적 Custom Tab으로 넘긴다.
- WebView debug는 debug build에서만 허용한다.
- renderer는 arbitrary LLM HTML을 표시하지 않고 검증된 StoryManifest를 고정 template로 변환한다.
- Story 문서와 파생 이미지에 `no-store`, `nosniff`, 엄격한 CSP를 유지한다.
- WebView cookie는 mobile session 전용 path와 짧은 만료를 사용한다.

현재 CSP의 `frame-ancestors 'none'`은 top-level WebView navigation을 막지 않지만, 모바일 전용 Story route를 별도로 두어 owner 관리 form과 refresh/share mutation을 Story WebView에서 제거한다.

## 네트워크와 신뢰 경계

### 유지할 세 개의 통로

| 통로 | 공개 범위 | 용도 | 허용 데이터 |
|---|---|---|---|
| `8443/mobile-location` | Funnel public | 휴대폰 GPS write-only batch | 최소 지문, 촬영 시각, GPS, 서명 header |
| `443/mobile-client/*` | Tailnet only | 소유자 조회·제어 | redacted run/result/story projection, 파생 이미지 |
| `8443/s/{share_id}` | 명시적 공개 share | 다른 사람에게 30일 공유 | immutable 공개 문장, coarse 위치, EXIF 제거 파생본 |

`/mobile-location`에 GET dashboard나 결과 조회를 붙이지 않는다. write-only 공개 receiver와 private read/control API를 같은 인증 규칙으로 섞으면 기존 최소 노출 설계가 무너진다.

### VPN이 꺼진 휴대폰

기본 정책은 다음과 같다.

- GPS upload: 계속 동작한다.
- push 수신: generic 알림만 수신할 수 있다.
- 개인 결과 열람: `Tailnet 연결 필요` 상태를 표시하고 Tailscale 앱으로 이동한다.
- 캐시된 Story: 사용자가 명시적으로 offline 보관한 파생본만 열 수 있다.
- 실행 제어: Tailnet 연결 전에는 금지한다.

향후 정말 VPN 없이 결과 열람이 필요하다는 사용 패턴이 확인되면 별도 `8443/mobile-api`를 고려할 수 있다. 그 경우에도 device-bound 서명, 짧은 token, derivative-only, scope 제한, rate limit, remote revoke를 갖춘 별도 gateway로 구현해야 하며 1차 범위에는 넣지 않는다.

## 인증·권한 설계

“나만 설치한 APK”도 인증을 없애는 근거가 되지 않는다. APK는 복사·분석될 수 있고 공개 URL은 인터넷에서 발견될 수 있다. 앱에 장기 secret을 하드코딩하지 않고 Android Keystore의 non-exportable 기기 key를 사용한다.

GPS 자동 ingest와 owner 열람은 권한 성격이 다르므로 같은 key를 재사용하지 않는다.

| Key | 사용 위치 | capability |
|---|---|---|
| `K_ingest` | public `8443/mobile-location` | `gps:write`만 |
| `K_owner` | Tailnet `443/mobile-client` | 승인된 read/control scope |

둘 다 Android Keystore 밖으로 내보내지 않는다. `K_owner`는 release 앱 승인을 거쳐 별도 등록하고, 향후 공유 생성·민감 다운로드에는 biometric 또는 기기 credential을 요구할 수 있다.

### session bootstrap

```text
1. 앱 → GET /mobile-client/v1/challenge
2. 서버 → nonce, audience, expires_at
3. 앱 → canonical challenge를 Android Keystore `K_owner`로 서명
4. 앱 → POST /mobile-client/v1/session
5. 서버 → Tailscale owner login + active device/key + nonce/replay 확인
6. 서버 → 5~15분 access session과 capability 목록 반환
7. 만료 시 앱은 저장된 장기 bearer token이 아니라 새 challenge에 다시 서명
```

WebView에는 bearer token을 JavaScript로 주입하지 않는다. 네이티브 bootstrap 뒤 `Secure`, `HttpOnly`, `SameSite=Strict`, `/mobile-client/story` path의 짧은 cookie를 서버에서 발급한다.

### capability

- `gps:write`: 기존 public receiver와 `K_ingest` 전용
- `status:read`
- `result:read`
- `story:read`
- `derivative:read`
- `run:control`: 후속 opt-in
- `share:manage`: 후속 opt-in

기본 기기는 read capability만 받고, 실행 제어와 공유 생성은 앱 설정에서 명시적으로 활성화한 기기에만 준다. 기기 폐기 시 GPS batch, mobile session, push token을 한 번에 무효화한다.

### mutation 규칙

- 요청마다 device signature 또는 짧은 session + anti-replay nonce를 검증한다.
- `Idempotency-Key`를 필수로 한다.
- retry/stop/share/revoke는 대상 run/share ID를 화면에 다시 보여 주고 확인한다.
- Story 재생성과 공유 생성은 긴 작업으로 처리하고 즉시 `202 + operation_id`를 반환한다.
- 서버 파일 path, 임의 action 이름, 임의 destination은 client 입력으로 받지 않는다.

## 모바일 BFF/API 초안

prefix는 `/mobile-client/v1`로 고정한다. MCP response를 그대로 전달하지 않고 앱이 안정적으로 사용할 작은 계약을 둔다.

| Method | Route | 기능 |
|---|---|---|
| GET | `/capabilities` | 서버 버전, 지원 기능, 최소 앱 버전 |
| GET | `/challenge` | 기기 session challenge |
| POST | `/session` | 기기 서명 session 생성 |
| GET | `/dashboard` | 홈 카드와 최신 run/story/action 집계 |
| GET | `/runs?cursor=&limit=` | 통합 실행 목록 |
| GET | `/runs/{run_id}` | 안정적인 stage와 전체 요약 |
| GET | `/runs/{run_id}/timeline` | redacted run event timeline |
| GET | `/runs/{run_id}/results?cursor=` | 추천 결과 pagination |
| GET | `/stories?cursor=` | Story 목록 |
| GET | `/stories/{story_id}` | Story metadata와 revision |
| GET | `/story/{story_id}/view` | 읽기 전용 same-origin HTML |
| GET | `/assets/{asset_id}/{kind}` | `thumb`, `preview`, 선택적 `download` |
| GET | `/actions?status=` | 사용자 조치 목록 |
| GET | `/events?after=&wait=` | 알림 event cursor; 1차 long polling 가능 |
| POST | `/events/{event_id}/ack` | 읽음·확인 처리 |
| POST | `/runs/{run_id}/retry` | 실패·partial 작업 재시도 |
| POST | `/runs/{run_id}/stop` | 실행 중지, 확인 필수 |
| POST | `/stories/{story_id}/refresh` | Story 재생성 |
| POST | `/shares` | 30일 공유 생성 |
| POST | `/shares/{share_id}/revoke` | 즉시 공유 폐기 |

현재 uvicorn은 WebSocket을 끈 상태다. 1차는 cursor polling 또는 최대 20~30초 bounded long polling이면 충분하다. 실제 배터리·지연 측정에서 필요성이 확인되기 전 WebSocket을 켜지 않는다.

### 응답 envelope

```json
{
  "schema_version": 1,
  "request_id": "req_random",
  "server_time": "2026-09-08T12:34:56+09:00",
  "data": {},
  "next_cursor": null,
  "capabilities": ["status:read", "story:read"]
}
```

날짜 표시는 앱에서 KST로 현지화하되 API timestamp는 offset이 포함된 ISO 8601로 전달한다.

### 공개 DTO allow-list

허용:

- 공개용 run ID, status, stage, count, percent, 시작·종료·경과 시각
- provider 이름, 모델 alias, 제한된 token/throughput 요약
- 추천 점수와 사람이 읽는 reason code
- 도시 단위 위치 label과 `confirmed/contextual/unknown` 상태
- Story title, revision, 생성 방식, 사진 수
- 앨범 반영 상태와 중립적인 오류 code

금지:

- 로컬 절대 path와 원본 filename
- exact GPS 숫자
- 얼굴 embedding과 사람 identity 내부 key
- OAuth token, API key, session secret, device public/private material
- raw prompt와 raw LLM response
- Chrome/Picker cookie·profile path
- stack trace, shell command, 환경변수 전체
- provider 원본 asset ID

`attestation_status`처럼 클라이언트가 스스로 보낸 문자열은 권한 판단에 사용하지 않는다. Android certificate chain, enrollment challenge, package signing digest 등을 서버가 실제 검증하는 attestation을 도입하기 전에는 단순 진단 정보다.

내부 payload가 발전해도 allow-list projection test를 통과하지 못한 새 field는 자동으로 모바일에 나오지 않게 한다.

## 이미지·캐시·다운로드 정책

- 목록은 512px급 thumb, 상세는 최대 1280~2048px preview를 사용한다.
- 모든 모바일 파생본은 orientation 보정, sRGB, EXIF/GPS 제거를 보장한다.
- 원본 다운로드는 1차 앱 기능에 넣지 않는다.
- 앱 cache는 app-private storage에 크기 상한과 TTL을 둔다.
- 기본은 memory/disk image cache이고, offline Story 보관은 명시적 선택 기능으로 분리한다.
- offline package는 manifest와 파생 이미지만 포함하고 Android Keystore 기반 암호화를 적용한다.
- 공유가 폐기되거나 device가 revoke되면 관련 cookie, API session과 offline package를 제거한다.
- `MediaStore` 저장은 사용자가 사진별 또는 묶음 저장을 명시한 경우에만 수행한다.
- WebView의 자동 다운로드와 `file://` 접근은 사용하지 않는다.

## 알림 설계

### 단계적 도입

1. 앱 내 event inbox와 foreground polling
2. Telegram event와 앱 event를 같은 server event에서 생성하여 중복 의미를 제거
3. Android push provider 연결
4. 안정화 뒤 Telegram을 fallback 또는 오류 전용으로 선택 가능하게 변경

Android에서 가장 운영이 단순한 push는 FCM이지만 Google에 push token과 전송 시각이 노출될 수 있다. payload에는 사진 제목, 파일명, 사람, GPS, Story 문장, passcode를 넣지 않는다.

```json
{
  "type": "photosmcp_event",
  "event_id": "evt_opaque_random",
  "category": "run_completed"
}
```

앱은 알림 선택 후 인증된 API로 상세 내용을 조회한다. FCM을 원하지 않는 경우 self-hosted UnifiedPush/ntfy는 선택형 대안이지만 Mac이 꺼져 있을 때의 가용성과 운영 복잡도를 별도로 검증해야 한다.

## 접근성·사용 편의 원칙

- 한국어 중심 문장과 기술 용어의 짧은 설명을 함께 제공한다.
- 색만으로 상태를 구분하지 않고 icon, label, 보조 문장을 사용한다.
- TalkBack label, 48dp touch target, font scaling, dark mode를 지원한다.
- 대량 사진 grid는 pagination과 placeholder를 사용하여 1,000장을 한 번에 decode하지 않는다.
- Mac offline, Tailnet off, 서버 시작 중, 데이터 0장 완료를 오류와 구분한다.
- `0장 정상 완료`도 완료 카드와 알림을 남긴다.
- loading이 10초 이상이면 현재 stage, 경과 시간, 마지막 갱신 시각을 표시한다.
- 실패 화면에는 사용자가 할 수 있는 다음 행동 하나만 우선 제안한다.
- 공유 link와 passcode는 같은 화면에 표시할 수 있지만 복사는 별도 action으로 유지한다.
- 앱은 기존 Telegram보다 정보를 많이 보여 주되, Telegram의 원격 도달성과 긴급 fallback 역할은 유지한다.

## 위협 모델과 대응

| 위협 | 영향 | 대응 |
|---|---|---|
| APK reverse engineering | hardcoded secret 탈취 | 앱에 공용 장기 secret을 넣지 않고 용도별 Keystore device key 사용 |
| public 8443 route 탐색 | 개인 결과 노출 | GPS write-only와 immutable share route 외 관리자 조회를 두지 않음 |
| 분실 단말 | cached Story·session 노출 | 짧은 session, revoke, encrypted offline package, 선택적 biometric gate |
| WebView 외부 이동·XSS | cookie·사진 노출 | host/path allow-list, 고정 renderer, CSP, JS bridge 금지, 외부 URL Custom Tab |
| replay·중복 mutation | 작업 중복·공유 반복 생성 | nonce, sequence, idempotency key, target confirmation |
| 서버 raw payload 과다 노출 | path·GPS·prompt 유출 | mobile DTO allow-list와 금지 field 테스트 |
| push payload 노출 | 잠금 화면·provider에 개인정보 노출 | generic text와 opaque event ID만 전송 |
| Tailscale header spoof | owner 권한 탈취 | loopback bind + Serve 경계, exact login allow-list, active device 서명 이중 확인 |
| 파생 이미지 cache 잔존 | 폐기 후 열람 | TTL, scope별 cache key, revoke purge, no-store response |
| screenshot·화면 녹화 | 개인 분석 노출 | 기본 허용하되 민감 상세에서 선택형 `FLAG_SECURE`; 사용성 저하를 고려해 전역 강제하지 않음 |

### 운영 전 보안 보완 backlog

- 모바일 owner API는 현재 소유자 HTML의 `loopback이면 자동 허용` fallback을 재사용하지 않는다. Tailscale identity와 `K_owner` proof가 모두 필요하다.
- WebView cookie로 mutation을 수행하지 않는다. Native JSON mutation은 짧은 token, request proof, `application/json`, idempotency를 사용한다. Web form을 남길 경우 별도 CSRF token을 추가한다.
- 공개 receiver와 공유 잠금의 메모리 rate limiter는 재시작 시 초기화되므로 device/share 기준 durable limiter와 global abuse limit을 도입한다.
- proxy가 검증해 준 경우 외에는 forwarded IP와 `Tailscale-User-Login` header를 신뢰하지 않는다.
- `strong_content_digest`, 정밀 촬영 시각, batch 활동 패턴도 민감 metadata로 분류해 mobile API·로그에서 제외하고 매칭 완료 뒤 retention을 적용한다.
- owner read 기능을 운영 활성화하기 전 개인 release key 서명, non-debuggable, debug receiver 부재, signing continuity를 통과한다.

### 권장 retention과 폐기

| 데이터 | 초기 기본값 | 폐기 시점 |
|---|---:|---|
| 사용·만료 enrollment token, nonce | 24시간 이하 | 주기 sweep |
| batch idempotency receipt | 30일 | retry window 종료 뒤 |
| 매칭되지 않은 암호화 GPS sidecar | 최대 90일 | 매칭 또는 보존기한 종료 뒤 |
| owner 파생 이미지 cache | 30일 LRU | quota, logout, device revoke |
| 공개 Story package와 파생본 | 최대 30일 | revoke 즉시, expiry sweep 24시간 이내 |
| 상세 run/error event | 30~90일 | 집계치만 남기고 payload 제거 |
| push token | active device 동안 | rotation, 알림 해제, device revoke |

기기 `revoke`는 접근 차단이고 `purge`는 보관 데이터 삭제다. 두 행동을 UI와 감사 기록에서 구분한다. owner device를 폐기하면 access token, WebView session, push token, offline package를 함께 무효화하고 Tailscale node 확인을 안내한다.

## 개발 구조 제안

### Android

기존 모듈에 Kotlin과 Compose를 추가하는 점진 migration을 권장한다.

```text
android/location-bridge/app/src/main/
├─ java/com/photosmcp/locationbridge/       # 기존 GPS engine, 당분간 유지
├─ kotlin/com/photosmcp/companion/
│  ├─ app/                                  # navigation, lifecycle, DI composition
│  ├─ feature/home/
│  ├─ feature/runs/
│  ├─ feature/results/
│  ├─ feature/stories/
│  ├─ feature/inbox/
│  ├─ feature/settings/
│  ├─ data/api/                             # DTO, paging, session challenge
│  ├─ data/cache/                           # Room/app-private cache
│  ├─ security/                             # Keystore signer, cookie/session
│  └─ web/                                  # hardened Story WebView
└─ res/
```

- UI: Jetpack Compose + Material 3
- state: ViewModel + immutable UI state
- network: 작은 typed HTTP client; timeout, retry, certificate 오류를 명시적으로 처리
- local data: Room은 event/read/cache metadata에만 사용
- background: Phase 1까지 GPS는 기존 JobScheduler를 유지한다. golden/in-place upgrade test가 생긴 뒤 `unique periodic work`와 관찰 가능한 상태가 필요한 경우 WorkManager로 한 번만 점진 전환한다.
- image: cache size와 EXIF 제거 서버 계약을 검증할 수 있는 loader
- DI framework는 앱 규모가 커질 때 도입하며 1차 vertical slice에는 수동 composition도 충분하다.

GPS engine을 Kotlin으로 동시에 다시 쓰지 않는다. 앱 shell과 API가 안정된 다음 테스트가 확보된 클래스부터 이동한다.

### 서버

```text
src/photos_mcp/
├─ application/mobile_client/
│  ├─ dashboard_service.py
│  ├─ run_projection.py
│  ├─ result_projection.py
│  ├─ story_projection.py
│  ├─ event_service.py
│  └─ command_service.py
├─ interfaces/http/mobile_client.py
├─ infrastructure/mobile_client/
│  ├─ session_repository.py
│  └─ push_repository.py
└─ interfaces/http/story_web.py             # renderer 재사용, mobile read-only variant
```

기존 `RunRepository`의 JSON payload를 route handler에서 직접 조립하지 않는다. application projection layer가 allow-list DTO와 schema version을 책임지고 HTTP layer는 인증·validation·response만 맡는다.

## 단계별 구현 로드맵

### Phase 0 — 계약과 기준선 고정

목표: GPS bridge를 깨뜨리지 않고 앱 확장의 경계를 테스트로 고정한다.

- 기존 APK package, key alias, SharedPreferences, outbox DB, JobScheduler ID inventory
- 현재 등록 상태를 유지한 in-place upgrade instrumentation test
- `K_ingest` 유지와 별도 `K_owner` 생성·등록 계약
- 모바일 공개 DTO allow-list와 금지 field 목록 확정
- Tailnet owner read와 public GPS write 경계의 route test
- `mobile-client/v1/capabilities` 계약 작성
- 앱 이름·아이콘만 Companion으로 변경하되 package ID 유지
- owner read를 켜기 전 개인 release signing과 debug surface 제거 gate

완료 기준:

- 기존 설치 위에 새 debug/release 후보를 설치해 device ID, key ID, GPS checkpoint가 유지된다.
- 기존 GPS upload E2E와 24시간 예약이 그대로 통과한다.
- public 8443에서 `/mobile-client/*`가 열리지 않는다.
- debug APK에는 owner Story/read/download 운영 capability를 발급하지 않는다.

### Phase 1 — Read-only Home vertical slice

목표: 앱에서 PhotosMcp 연결과 최신 통합 작업을 안전하게 본다.

- 기기 challenge/session bootstrap
- `/capabilities`, `/dashboard`, `/runs`, `/runs/{id}`
- Compose app shell, 홈, 실행 목록·상세, 연결 상태
- QR/승인 링크 등록과 사용 시점별 권한 설명
- server offline/Tailnet off/0장 완료/partial 상태 처리
- cursor pagination과 KST 표시

완료 기준:

- 실제 Android 단말에서 최신 통합 run과 Apple/Google 단계가 한 화면에 표시된다.
- exact GPS, path, token, raw prompt가 API와 local cache에 없다.
- Tailnet을 끄면 앱이 무한 spinner 대신 원인과 Tailscale 열기 action을 보여 준다.
- 앱을 update해도 GPS 동기화가 계속 동작한다.

### Phase 2 — 추천 결과와 이미지

목표: 분석 중간 요약과 최종 추천을 안정적으로 탐색한다.

- run timeline과 results API
- thumb/preview derivative route와 authorization
- 날짜·장면별 native grid, 상세 sheet, reason code
- 위치 evidence 상태와 앨범 저장 영수증
- 원본·분석 preview·추천 저장본 availability 구분
- 1,000장 pagination, memory·disk cache 제한

완료 기준:

- 0, 1, 100, 1,000장 fixture에서 OOM 없이 탐색한다.
- 파생 JPEG에 EXIF/GPS가 없고 원본 route가 존재하지 않는다.
- 실패·carry-over 사진 수가 결과와 parent run에서 일치한다.
- 같은 자산을 반복 조회해도 path traversal이나 임의 파일 조회가 불가능하다.

### Phase 3 — Story WebView

목표: 기존 Story HTML을 앱에서 안정적으로 읽는다.

- Story 목록·metadata API
- mutation form이 없는 mobile read-only renderer
- hardened WebView와 mobile session cookie
- grid→viewer, swipe, orientation, process death 복구
- viewer JavaScript 실패 시 단순 상세 fallback
- 공개 share 미리보기 Custom Tab
- Google OAuth·Picker도 시스템 Chrome/Custom Tab 경계로 유지

완료 기준:

- 현행 Story와 앱 Story의 revision·사진 순서·문장이 같다.
- WebView에서 허용되지 않은 외부 URL, `file://`, 새 창을 열 수 없다.
- 네트워크 중단 시 명확한 retry 상태를 표시한다.
- TalkBack, font scaling, dark mode와 Android 주요 화면 크기를 확인한다.

### Phase 4 — Event inbox와 알림

목표: Telegram 여러 메시지 대신 앱에서 하나의 작업 상태와 알림을 추적한다.

- server event table/outbox, event cursor, ack/read
- 앱 inbox, badge, run/story deep link
- foreground polling 또는 bounded long polling
- Telegram과 동일 server event를 공유하는 notification policy
- FCM 또는 privacy 대안에 대한 실제 단말 spike
- App Link는 Tailnet MagicDNS 검증 가능성을 실기기에서 확인하고, 실패 시 기존 HTTPS/QR fallback 유지

완료 기준:

- 완료, 0장 완료, partial, 실패, 사용자 조치 필요가 각각 한 event로 생성된다.
- 재시작·재전송에서도 중복 알림이 생기지 않는다.
- push payload와 Android notification log에 사진·위치·passcode가 없다.
- push가 실패해도 Telegram fallback과 앱 inbox 원장은 보존된다.

### Phase 5 — 제한된 제어와 공유 관리

목표: 자주 필요한 수동 실행과 공유 관리를 앱에서 안전하게 수행한다.

- retry, stop, Story refresh, 공유 생성·폐기
- 30일 기본값과 download policy 표시
- operation ID 기반 비동기 상태 추적
- 실행 기간, provider, 장수 등 기존 Telegram 명령 option을 typed form으로 제공
- destructive/비용 큰 action 확인과 idempotency

완료 기준:

- 동일 요청 반복 탭이 하나의 operation만 만든다.
- stop/share/revoke 대상이 바뀌면 이전 확인을 재사용할 수 없다.
- 공유 passcode가 DB, push, 로그에 평문으로 남지 않는다.
- 앱에서 만든 share도 기존 30일 만료·EXIF 제거·즉시 폐기 E2E를 통과한다.

### Phase 6 — Offline Story와 개인 release 운영

목표: 일상 사용 품질과 장기 운영을 완성한다.

- 선택형 encrypted offline Story package
- cache quota·TTL·폐기 purge
- biometric gate 선택 기능
- 개인 release keystore, versioning, reproducible build 기록
- release APK in-place update, rollback이 아니라 데이터 migration 검증
- 장애 진단 export와 서버 capability compatibility gate

완료 기준:

- release 서명 APK에서 WebView debug·ADB auto-enroll 경로가 없다.
- device revoke 뒤 API/session/offline content를 열 수 없다.
- 구버전 앱에는 안전한 upgrade-required 화면이 표시된다.
- 7일 이상 실사용에서 GPS 예약, 앱 조회, 알림, Story cache가 충돌하지 않는다.

### Phase 7 — 선택형 후속 확장

다음은 실제 사용 데이터가 필요성을 증명할 때만 진행한다.

- VPN 없는 device-bound derivative-only read gateway
- self-hosted UnifiedPush/ntfy
- tablet·foldable 전용 master-detail UI
- 사용자 추천 수정과 개인화 feedback
- 원본 다운로드 또는 휴대폰↔Mac 양방향 파일 전송
- iOS Companion 앱

## 테스트 전략과 release gate

### 서버

- projection unit test: 허용 field snapshot, 금지 key recursive 검사
- auth test: owner login, device signature, expiry, replay, revoked device
- route exposure test: 443/8443 경계와 unknown route 404
- pagination·cursor·idempotency·rate limit test
- derivative test: 크기, orientation, color profile, EXIF 0, path traversal
- migration test: 현행 SQLite에서 mobile table 추가 후 기존 데이터 보존

### Android

- JVM unit: canonical signature, DTO parsing, redaction, state reducer
- instrumentation: upgrade install, navigation, WebView allow-list, process death
- screenshot/accessibility: font 1.0/1.3/2.0, dark mode, TalkBack label
- network matrix: Wi-Fi, mobile data, Tailnet off, Mac off, slow/timeout, certificate error
- media matrix: 0/1/100/1,000장과 매우 큰 portrait/landscape 이미지
- storage matrix: cache full, permission revoked, app update, device reboot
- Google auth/Picker가 embedded WebView가 아닌 Chrome에서 열리고 callback 뒤 정확한 run으로 복귀하는지 검증

### 통합 E2E

```text
기존 APK 등록 상태
  → 새 APK in-place 설치
  → GPS 신규 metadata public upload
  → PhotosMcp와 matching
  → Apple+Google 통합 run
  → 분석·추천·로컬 저장·앨범 영수증
  → Story 생성
  → 앱 Home/Run/Result/Story 표시
  → generic 완료 알림
  → 30일 share 생성·Custom Tab 해제·download
  → share revoke와 cache purge
```

release 후보는 다음을 모두 통과해야 한다.

- Python 전체 회귀 테스트
- Android unit, lint, instrumentation, release build
- docs validation
- 실제 단말 in-place upgrade와 GPS 재전송
- Tailnet/public route probe
- 개인정보 leak scan
- 6시간 partial/carry-over 상태 simulation

## 관점별 검토 결과 통합

### 제품·UX 관점

- 장점: Telegram 메시지와 여러 웹 링크로 흩어진 상태를 하나의 run과 Story로 통합할 수 있다.
- 핵심: 사용자는 내부 provider나 모델 로그보다 “어디까지 됐고, 무엇이 골라졌고, 지금 할 일이 있는가”를 먼저 본다.
- 결정: 홈과 run은 native, Story는 기존 renderer를 재사용한다. 중간 결과도 raw log가 아니라 stage와 count 중심으로 표시한다.

### Android 관점

- 장점: 검증된 GPS bridge와 device key를 폐기하지 않고 앱 shell을 확장할 수 있다.
- 위험: 단일 Activity를 한 번에 전면 rewrite하면 GPS 회귀 범위가 커진다.
- 결정: package ID와 Java GPS engine을 유지하고 Kotlin·Compose를 side-by-side로 추가한다. 별도 package의 side-by-side 앱은 안전해 보이지만 debug→release migration에서 기존 Keystore와 등록을 잃고 두 앱이 중복 수집할 위험이 있어 이번 목표에는 채택하지 않는다. 대신 기능별 vertical slice가 끝날 때마다 실기기 in-place upgrade를 검증한다.

### PhotosMcp 연계 관점

- 장점: run, event, recommendation, story, share 원장이 이미 있어 BFF projection이 중심 작업이다.
- 위험: MCP 또는 repository payload를 그대로 앱 계약으로 사용하면 내부 schema 변경과 개인정보 누출에 취약하다.
- 결정: `/mobile-client/v1` application projection을 별도로 만들고 cursor·schema version·capability를 명시한다.

### 개인정보·보안 관점

- 장점: public GPS write-only, Tailnet owner read, public immutable share의 기존 삼중 경계를 확장할 수 있다.
- 위험: “개인용 APK”를 이유로 공개 read API나 embedded secret을 허용하면 가장 큰 회귀가 된다.
- 결정: 기본 read/control은 Tailnet + 별도 `K_owner` proof, push는 opaque event, exact GPS·path·raw prompt는 mobile DTO에서 제외한다. 기존 `K_ingest`는 public GPS append 권한에서 승격시키지 않는다.

### 절충 결론

- 전면 WebView 앱은 개발은 빠르지만 인증·offline·push·대량 이미지 UX에서 장기 비용이 크므로 채택하지 않는다.
- 전면 native Story renderer는 웹과 앱의 story 표현이 갈라지고 현행 renderer를 버리므로 채택하지 않는다.
- 공개 8443에 앱 관리자 API를 바로 추가하는 안은 편리하지만 기존 write-only 보안 경계를 깨므로 보류한다.
- 첫 vertical slice는 “앱 shell + 안전한 dashboard/run read”다. Story WebView나 push부터 시작하지 않는다.

## 구현 착수 순서

다음 작업은 Phase 0과 Phase 1을 하나의 작은 vertical slice로 진행하는 것이 가장 안전하다.

1. Android upgrade 보존 테스트와 현재 key/prefs/job ID 계약 고정
2. `/mobile-client/v1/capabilities`, challenge/session, dashboard projection 구현
3. Compose shell에 홈·연결 상태만 추가
4. 실제 단말 in-place 설치 후 GPS upload와 dashboard read 동시 검증
5. 결과가 안정되면 run 목록·상세를 같은 API/화면 구조로 확장

여기까지 완료되면 앱 방향이 실제 사용에서 유효한지 확인할 수 있고, 이후 추천 grid와 Story WebView는 같은 인증·navigation 위에 독립적으로 추가할 수 있다.

## 추적할 의사결정

구현 중 다음 선택은 별도 기록하되 Phase 1을 막지 않는다.

- push provider: FCM 또는 self-hosted 대안
- offline Story 기본 비활성 여부와 cache quota
- biometric gate 적용 화면
- 결과 탭의 기본 filter를 `추천만`으로 둘지 최근 실행 전체로 둘지
- run control capability를 기본 허용할지 별도 활성화할지
- VPN 없는 private read gateway를 정말 필요한지 판단할 실제 사용 기록
- JobScheduler에서 WorkManager로 전환할 경우의 battery·중복 실행·upgrade 측정

현재 권장 기본값은 FCM 결정 보류, offline Story opt-in, biometric 선택형, run control 별도 활성화, private read Tailnet-only다.

## 2026-09-08 Phase 5 수동 날짜 Story vertical slice

Phase 5의 첫 제한 제어를 `PhotosMcp 앨범` 0.5.0에 운영 반영했다.

- 앱에서 촬영일 1~31일, Apple/Google, 총 1~1,000장을 선택한다.
- Apple Photos exact count와 Android MediaStore 참고 count를 미리 보여주고, Google은 공식 Picker 전까지 수량 미상으로 표시한다.
- Tailnet owner session의 별도 `curation:write` scope와 Android owner key command signature, 120초 timestamp, nonce, idempotency를 적용한다.
- 작업은 영속 FIFO queue로 실행하며 03:00 자동 요청도 수동 작업 뒤에서 유실 없이 이어진다.
- 수동 작업은 6시간 제한과 carry-over 기반을 공유하지만 자동 앨범 publish는 하지 않는다.
- Google worker는 선택한 명시 날짜를 전용 Chrome/Qwen mission에 전달하며 UI의 오늘 표시는 실제 오늘로 해석한다.
- 완료 결과는 operation별 Story ID, WebView session, 추천 사진 API에 동일하게 바인딩한다.
- deterministic Story를 항상 만들고 설정된 Hermes Story Director가 성공하면 같은 scope에서 LLM Story로 보강한다.

검증과 설치 정보는 [Android 날짜 선택 Story 수동 실행 구현·검증](../../08-reports/01-validation/35-android-manual-date-story-2026-09-08.md)에 기록했다.

Phase 5에서 아직 남은 범위는 앱의 retry/stop, 공유 생성·폐기 관리다. push, offline Story, 고유 private release signing migration은 각각 Phase 4·6에 남긴다.
