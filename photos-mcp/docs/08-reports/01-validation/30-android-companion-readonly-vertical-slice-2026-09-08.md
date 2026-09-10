# Android Companion read-only vertical slice 검증

## 결과

기존 Android GPS Bridge를 삭제하거나 새 package로 갈아타지 않고 `com.photosmcp.locationbridge`를 표시 이름 `PhotosMcp 앨범`, 버전 0.2.0으로 확장했다. GPS 메타데이터 전송 기능은 그대로 유지하고, Tailnet 안에서만 개인 작업 상태·통합 추천·Story를 읽는 별도 모바일 API와 앱 화면을 추가했다. 후속 0.3.0의 실제 추천 썸네일과 설치 가능한 Tailnet APK는 [별도 검증 보고서](31-android-native-recommendation-gallery-and-apk-delivery-2026-09-08.md)에 이어서 기록한다.

자동 검증과 Mac 운영 배치에 이어 실제 Android 16 단말에 새 APK를 기존 앱 위로 설치하고 화면·인증·Story WebView·GPS Bridge 보존까지 확인했다. 설치 과정에서 등록 JSON을 다시 입력하거나 앱 데이터를 지우지 않았으며 기존 기기 identity, ingest key, checkpoint와 암호화 outbox가 유지됐다. 다만 현재 설치본은 개발용 debug 서명이므로 개인 release key로 서명한 운영판 완료로 과장하지 않는다.

## 구현 범위

### 서버

- `src/photos_mcp/application/mobile_client.py`
  - 통합 run, dashboard, timeline, 추천 결과, Story, event를 mobile allow-list DTO로 변환
  - 원본 경로, provider asset ID, exact 좌표, raw prompt·응답을 제외
  - schema version, server time, cursor를 공통 envelope로 제공
- `src/photos_mcp/infrastructure/mobile_client/repository.py`
  - public GPS ledger와 분리된 owner device·challenge·session DB
  - challenge 일회 소비와 만료, bearer와 WebView session hash 저장
  - 기기 revoke 시 session version 증가와 API/Web session 즉시 폐기
  - device별 event acknowledgement
- `src/photos_mcp/interfaces/http/mobile_client.py`
  - `K_ingest`로 별도 `K_owner` 등록
  - `K_owner`로 짧은 API session 발급
  - Tailscale owner identity와 device session을 함께 확인
  - dashboard, runs, timeline, results, stories, event/ack route
  - 45초 일회용 WebView exchange와 30분 HttpOnly cookie
  - 추천 원본 대신 EXIF 제거 JPEG thumb/preview만 제공
- `src/photos_mcp/mobile_client_server.py`
  - `127.0.0.1:18794` 전용 프로세스
  - non-loopback bind 거부
  - LaunchAgent `com.photosmcp.mobile-client`로 로그인 시 시작·비정상 종료 시 재시작

### Android

- 기존 package ID, GPS P-256 alias, AES-GCM outbox, asset HMAC, 24시간 JobScheduler를 유지
- 별도 non-exportable `photosmcp-owner-signing-v1` P-256 key 추가
- bearer는 SharedPreferences나 disk에 저장하지 않고 process memory에만 10분 보유
- 네이티브 홈, 작업, 통합 추천, 알림함, 설정 화면 추가
- warm paper·forest semantic token, local vector icon, adaptive/themed launcher icon과 light/dark theme 추가
- `홈 · 작업 · 추천 · 이야기` bottom navigation과 상단 알림·설정 icon action, 선택 pill·ripple·accessibility state 추가
- 알림함은 완료·부분 완료·오류·사용자 확인 필요 event를 표시하고 기기별 확인 상태를 저장
- Apple·Google 결과는 provider별 앱 화면이 아니라 하나의 combined run/result로 표시
- Story는 bearer를 JavaScript에 전달하지 않고 native가 일회용 exchange code를 POST해 제한 cookie를 받음
- WebView는 HTTPS의 고정 `/mobile-client/story` origin만 내부 이동 허용
- Story grid·viewer에 48px SVG control, safe area, screen-reader live region, reduced-motion과 앱 명시 dark/light 전달 추가
- file/content access, mixed content, third-party cookie, JavaScript bridge를 사용하지 않음
- 앱 시작 시 사진 권한을 요청하지 않고 설정의 GPS 동기화 버튼을 누를 때만 요청
- Tailnet이 꺼져도 GPS public upload/outbox 재시도는 독립적으로 유지됨을 화면에서 설명

## 인증 흐름

```text
기존 K_ingest가 등록된 Android
  → Tailnet owner identity로 challenge 요청
  → K_ingest가 새 K_owner 공개키 등록문에 서명
  → 서버는 K_ingest 권한을 read로 승격하지 않고 K_owner를 별도 원장에 등록
  → K_owner가 session challenge 서명
  → 10분 status/result/story/derivative bearer
  → Story 진입 때 45초 exchange code
  → HttpOnly·Secure·SameSite=Strict 30분 WebView cookie
```

## 자동 테스트

### Python 전체 회귀

```text
.venv/bin/pytest -q
817 passed in 10.83s
```

새 모바일 계약 테스트는 다음을 포함한다.

- Tailnet identity가 없으면 capabilities 403, API/Story 401
- 공개 mobile-location 앱에는 mobile-client·read route 404
- ingest/owner key 분리 강제
- challenge replay 401
- 원본 path, provider ID, photo ID, exact 위경도 문자열이 DTO에 없음
- Web exchange single-use, Secure·HttpOnly·SameSite cookie
- Story HTML의 mobile asset/static URL
- thumb JPEG 생성
- owner revoke 뒤 API와 Web session 동시 401

### Android

```text
JAVA_HOME=/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home
ANDROID_HOME=/opt/homebrew/share/android-commandlinetools
ANDROID_SDK_ROOT=/opt/homebrew/share/android-commandlinetools
./gradlew clean assembleDebug lintDebug assembleRelease lintRelease
BUILD SUCCESSFUL
```

APK는 `android/location-bridge/app/build/outputs/apk/debug/app-debug.apk`에 생성됐다. `assembleRelease lintRelease`도 성공해 R8 축소된 unsigned release 후보를 만들었다. 실제 운영 release는 개인 서명 키가 필요하다.

### 실기기 설치·화면 검증

2026-09-08에 Android 16 / API 36 단말(`SM-F966N`)을 ADB로 연결하고 다음 순서로 검증했다. 단말 serial, 기기 키, session과 등록 payload는 문서에 기록하지 않는다.

```text
.venv/bin/python scripts/install_connected_android_bridge.py --no-build
Android bridge installed; existing pairing was preserved
```

- `com.photosmcp.locationbridge` 0.2.0, versionCode 2가 기존 설치 위에 갱신됨
- 최초 설치 시각은 유지되고 마지막 갱신 시각만 변경됨
- private app data에 `shared_prefs/bridge.xml`과 암호화 outbox DB가 그대로 존재함
- `READ_MEDIA_IMAGES`, `ACCESS_MEDIA_LOCATION` 권한이 유지됨
- JobScheduler 710931이 등록된 채 다음 24시간 실행을 기다림
- Home은 Mac 준비 상태, 최신 combined run, 최신 Story 28장과 사용자 조치 수를 실제 운영 원장에서 표시함
- Runs는 최대 1,000장·6시간 및 이월 정책과 완료·오류·일부 완료 이력을 표시함
- Results는 Apple·Google 통합 추천을 표시하고 빈 위치를 중복 구분자 없이 `위치 미상`으로 표기함
- Story WebView는 일회용 교환을 거쳐 별도 JSON 입력이나 재로그인 없이 열림
- Story의 실제 파생 이미지 grid, 전체 화면 viewer와 좌우 swipe `1 / 28 → 2 / 28`이 동작함
- Inbox는 완료·오류·일부 완료를 표시함. 기존 미확인 event는 테스트 중 임의로 확인 처리하지 않음
- Settings는 `GPS Bridge · 연결됨`, 마지막 위치 동기화 시각, 10일/증분/일 1회 정책을 표시함
- Settings에서 수동 GPS 동기화를 실행해 `위치 0건 큐 등록 · 0개 배치 전송 · 남은 배치 0`으로 정상 종료함. 새 데이터가 없는 0건 경로에서도 checkpoint와 outbox가 정상 유지됨
- Android 16 edge-to-edge 상태바·하단 navigation inset을 적용해 제목과 버튼이 시스템 영역에 겹치지 않음
- 앱 실행 후 logcat에서 fatal exception, TLS handshake, unknown host 오류가 발견되지 않음

### 디자인·접근성 실기기 재검증

- 앱 launcher label을 `PhotosMcp 앨범`으로 바꾸고, 위치 pin 대신 겹친 사진 프레임·선택 표시·sparkle을 사용한 adaptive icon과 Android 13 monochrome layer를 제공함
- 상단 알림·설정은 각각 48×48dp icon action과 접근성 이름을 제공함
- 하단 네 목적지는 UI Automator 실측 약 99×72dp이고 icon과 label을 함께 표시함
- 선택 목적지는 pill·primary tint·굵은 label과 `선택됨, 탭 1/4` 접근성 상태를 함께 제공함
- 실제 28장 Story에서 grid, viewer, `1 / 28 → 2 / 28` swipe, Android back viewer dismiss를 확인함
- WebView가 단말 dark preference를 자체 반영하지 않는 현상을 발견해 native가 `data-theme=dark|light`를 명시하도록 수정했고, 이후 dark Story와 native shell 색상이 일치함
- light mode + Android 글자 크기 130%, landscape를 각각 실측해 잘림·겹침이 없음을 확인하고 단말을 기존 dark·100%·자동 회전 설정으로 복구함
- 주요 text/token 대비는 5.22:1 이상이며 headline/body, outlined card, selected state가 색상 없이도 구분됨
- 화면 순회 뒤 해당 앱 process log에서 fatal exception, TLS·DNS 오류가 없었음

디자인 간이 점수는 기존 5.0/10에서 8.4/10으로 개선됐다. 이 0.2.0 검증 당시 native 추천 목록은 텍스트 card였으며, 후속 0.3.0에서 사진 중심 적응형 thumbnail grid, 큰 preview와 제한된 private image cache를 구현했다.

실기기 확인 중 모바일 서버가 임시 in-memory run repository를 열어 Home에 운영 run과 Story를 0건으로 보이는 결함을 발견했다. `mobile_client_server.create_app()`이 canonical persistent `RunRepository`를 명시하도록 수정하고 회귀 테스트를 추가한 뒤 LaunchAgent를 재시작했다. 이후 실제 운영 run·Story가 정상 표시됐다.

## 운영 경계 검증

Tailscale Serve 443에 다음 path만 추가했다.

```text
https://byoungyoung-macmini.tail53bcc7.ts.net/mobile-client
  → http://127.0.0.1:18794/mobile-client
```

기존 route와 공개 범위는 유지됐다.

| Probe | 결과 |
|---|---:|
| direct `127.0.0.1:18794/mobile-client/v1/capabilities` 무인증 | 403 |
| Tailnet `/mobile-client/v1/capabilities` owner identity | 200 |
| public Funnel 8443 `/mobile-client/v1/capabilities` | 404 |
| public Funnel 8443 `/mobile-location/v1/results` | 404 |
| Tailnet Open WebUI `/` | 200 |
| Tailnet owner Photos `/photos` | 200 |
| Tailnet Hermes Dashboard `:9119` | 302 로그인 이동 |

`tailscale serve status --json`에서 Funnel 허용은 계속 8443 하나뿐이다. 443의 `/mobile-client`는 Tailnet only이고 `/`, `/photos`, `/photos-actions`, `/story-assets`를 보존한다.

## 남은 release gate

다음은 코드만으로 확정할 수 없어서 완료 처리하지 않았다.

1. 0.3.0 Tailnet 개인 베타를 실제 단말에 update하고 새 native grid를 시각 회귀 검증
2. 장기 고유 release keystore로 전환할 때 기존 debug 인증서 앱 데이터를 옮기는 migration 결정
3. 48장 초과 lazy pagination, generic push, 안전한 제한 제어는 roadmap Phase 4~6에서 진행

현 단계 capability는 `controls=false`, `push=false`로 명시한다. 검증되지 않은 mutation이나 공개 read API를 편의상 열지 않았다.
