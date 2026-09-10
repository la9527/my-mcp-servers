# Android 날짜 선택 Story 수동 실행 구현·검증

## 결론

`PhotosMcp 앨범` 0.5.0에 자동 새벽 작업과 독립된 **날짜로 Story 만들기** 흐름을 구현했다. 사용자는 촬영일 기준 시작일·종료일, Apple Photos·Google Photos, 최대 장수를 선택하고 미리보기 후 Mac mini에 작업을 등록할 수 있다. 작업은 앱을 닫아도 Mac에서 계속되며, 완료된 수동 작업의 추천 사진과 Story는 그 작업의 날짜 범위로 고정된다.

자동 03:00 작업의 증분 수집·승인 앨범 정책은 유지한다. 수동 Story 작업은 기존 분석 결과를 재사용하되 새 사진만 분석하고 자동 앨범에는 쓰지 않는다.

## 사용자 흐름

```text
앱 홈 / 실행 탭
  → 날짜로 Story 만들기
  → 오늘·어제·최근 7일 또는 시작일/종료일 선택
  → Apple / Google, 최대 1,000장 선택
  → 사진 수 미리보기
  → 서명된 작업 등록
  → 대기 / 준비 / 분석 / 추천 저장 / Story 생성
  → 작업 전용 Story 또는 추천 사진 열기
```

Apple Photos 수량은 Mac 사진 보관함의 촬영일 query로 확인한다. Google Photos Picker API는 보관함 전체를 서버가 미리 조회할 수 없으므로 미리보기에서는 `Picker 선택 후 확인`으로 정직하게 표시한다. Android MediaStore의 같은 날짜 수량은 GPS 연결 상태를 이해하기 위한 참고치일 뿐 Apple·Google 수량과 합산하지 않는다.

## 구현 범위

### Android

- 홈과 실행 탭에 `날짜로 Story 만들기` 진입점을 추가했다.
- 한국시간 촬영일, 시작·종료일 포함, 최대 31일 범위를 사용한다.
- 빠른 날짜는 오늘, 어제, 최근 7일을 제공한다.
- Apple Photos와 Google Photos를 개별 선택하고 총 1~1,000장을 설정할 수 있다.
- 조건이 바뀌면 이전 미리보기 승인을 폐기해 잘못된 범위로 시작하지 못하게 한다.
- 시작 직전에 날짜, 출처, 최대 장수, 6시간 제한, 자동 앨범 미변경을 다시 확인한다.
- 작업 상태를 5초 간격으로 확인하고 앱을 닫아도 Mac 작업은 계속된다.
- 완료 Story 목록에 자동/직접 실행, 날짜 범위, 사진 수를 표시한다.
- Story와 추천 grid·큰 사진은 `story_id`로 묶어 다른 실행의 사진이 섞이지 않는다.
- 날짜 선택은 화면 회전·Activity 재생성 뒤에도 유지한다.

### Mac backend

- `curation_operations` 영속 큐와 command nonce 원장을 추가했다.
- signed owner session에만 `curation:write` scope를 발급한다.
- 수동 시작 body는 Android Keystore의 별도 owner key로 서명한다.
- command timestamp 120초, one-time nonce, idempotency key와 canonical request hash를 검증한다.
- 반복 탭은 같은 operation receipt를 반환하고 다른 body로 idempotency key를 재사용하면 거부한다.
- 응답 유실 시 앱은 미확인 body hash와 idempotency key를 private preferences에 보존해 같은 operation receipt를 다시 받고, 영수증을 받은 뒤에만 제거한다.
- 한 번에 combined parent 하나만 실행한다. 수동 작업 중 들어온 03:00/Telegram 자동 요청은 삭제하지 않고 같은 큐 뒤에 보존한다.
- process가 dispatch 직후 중단되면 5분이 지난 미결 claim만 재대기시킨다.
- Apple은 `date_from`/`date_to` 촬영일 query를 사용하며 수동 작업으로 일일 증분 checkpoint를 이동하지 않는다.
- Google은 전용 Chrome profile과 `qwen-agent` browser mission을 별도 process로 시작한다. 명시 날짜를 CLI까지 전달하고 Today/오늘 표시는 실제 오늘로 해석한 뒤 목표 과거 날짜 범위만 허용한다.
- Google Picker 개별 사진·날짜·최종 완료 버튼은 deterministic guard가 다시 검증한다. 현재 Picker의 `2026. 9. 4.` 형식 날짜 라벨을 인식하고, 고정된 로컬 scroll script로 과거 날짜까지 이동한다.
- Google의 가상 grid가 클릭 중 DOM을 재배치하므로 매 검증 시점의 fresh UID만 사용한다. 클릭이 반영되지 않으면 이미 선택된 사진은 건드리지 않고 부족한 수만 제한적으로 재시도한다.
- Qwen step 상한은 대량 페이지 탐색을 위해 64, 절대 상한은 128이다. 모델 준비 실패, 개별 응답 timeout 또는 전체 선택 mission 300초 초과 시 동일 Picker session을 deterministic guard가 이어받는다. 전체 사진 작업 제한 6시간은 그대로 유지한다.
- 추천 저장 완료까지 background reconciler가 계속 추적한다.
- 수동 실행은 `publication_policy=none`이어서 Apple/Google 자동 앨범을 변경하지 않는다.
- 날짜 범위 Story fallback을 먼저 보장하고, 설정된 Hermes Story Director가 있으면 Linux Story target의 문장·chapter로 보강한다.

## API 계약

| Method | 경로 | 목적 |
|---|---|---|
| `POST` | `/mobile-client/v1/manual-curations/preview` | Apple 촬영일 수량·재사용 수량과 Google Picker 필요 상태 확인 |
| `POST` | `/mobile-client/v1/manual-curations` | 서명·nonce·idempotency 검증 뒤 비동기 작업 등록 |
| `GET` | `/mobile-client/v1/manual-curations/{operation_id}` | queue position, run count, 완료 Story 상태 조회 |
| `GET` | `/mobile-client/v1/stories` | 자동·직접 실행 Story 목록 |
| `GET` | `/mobile-client/v1/results?story_id=...` | 한 Story에 포함된 추천 사진만 조회 |
| `POST` | `/mobile-client/v1/web-exchange` | 선택 Story에 바인딩된 일회용 WebView session 발급 |

## 보안·개인정보 경계

- control/read는 Tailscale 443 + 승인 owner login + device-bound owner session을 모두 요구한다.
- 공개 Funnel 8443에는 GPS write-only receiver만 남고 수동 작업 API는 404다.
- owner origin은 등록된 GPS receiver와 동일한 MagicDNS host, HTTPS 443만 허용한다.
- Story WebView exchange와 cookie에 `story_id`를 묶는다.
- 파생 이미지 요청은 session Story membership을 확인하므로 임의 asset ID 열람이 불가능하다.
- exact GPS, 원본 경로, Google credential, Picker URI, 사진 파일명은 mobile DTO·명령·로그에 넣지 않는다.
- Android image cache key에도 Story scope를 포함하며 app-private cache만 사용한다.
- APK에는 사진 원본이나 고정 API token을 포함하지 않는다.

## 검증 결과

### 자동 테스트

```text
.venv/bin/python -m pytest -q
839 passed in 14.83s
```

추가 검증에는 날짜·미래·31일 제한, Apple exact query와 checkpoint 비변경, Google 명시 날짜 전달, 점 형태 날짜 라벨, bounded historical scroll, 가상 grid의 누락 click 복구, Qwen timeout fallback, 큐 idempotency·active hold·crash recovery, owner command signature, 실행별 Story 격리와 LLM Story 보강이 포함된다.

### 9월 초 실사진 자동 실행

게시·공유 부작용 없이 `publication_policy=none`으로 실제 Google Photos Picker와 분석 파이프라인을 검증했다. 사진 파일명, 사진 내용, 원본 경로, 위치 좌표는 검증 로그와 이 문서에 기록하지 않았다.

| 범위 | 경로 | 실제 결과 |
|---|---|---|
| 2026-09-04 | Apple Photos | Mac Photos MCP 권한 경로로 10장 조회, 기존 분석 결과 재사용, 수동 실행이 일일 checkpoint를 변경하지 않음 |
| 2026-09-04 | deterministic | 촬영일 범위까지 자동 scroll, 사진 8장 선택, 완료 버튼 자동 클릭, 기존 처리 8장 판정, `no_new_photos` 정상 완료 |
| 2026-09-04 | Qwen 우선 + fallback | Linux `linux-long-context` 6회 호출 후 deterministic 자동 승계, 동일 8장 선택·중복 판정까지 정상 완료 |
| 2026-09-07 | 신규 분석 | 사진 10장 선택·다운로드, 동영상 0장, 실제 VLM 분석 10장 완료, 약 150.91초 |
| 2026-09-07 | 추천·Story | 추천 2장, 로컬 추천 사본 2장, 실패 0건, 실행 전용 Story 2장·1 chapter 생성 |

Qwen 우선 실환경 실행의 모델 지표는 14,405 prompt token, 87 completion token, 합계 14,492 token, 모델 요청 누적 약 95.70초였다. 모델 경로가 선택 작업을 끝내지 못했지만 fallback 이후 전체 browser mission은 약 124.23초에 정상 종료됐다. 이 실행으로 워크스테이션 모델이 느리거나 멈춰도 Google Picker 작업이 함께 실패하지 않는 운영 경계를 확인했다.

9월 4일 첫 두 진단 실행은 날짜 범위 외 항목을 전혀 선택하지 않은 채 최종 확인 전에 안전 중단됐다. 그 과정에서 발견한 `현재 Google 날짜 라벨`, `과거 날짜 scroll target`, `가상 grid click 누락`을 위 회귀 테스트와 production 코드에 반영했다. 성공한 9월 4일 실행은 독립 실행 ID에 바인딩해 오래된 예약 action과 결과가 섞이지 않게 했다.

### Android 실기기 설치·수동 E2E

2026-09-08에 Samsung `SM-F966N`(Android 16)을 ADB로 연결해 Tailnet 배포 APK를 기존 앱 위에 설치하고, 앱 버튼에서 9월 7일 수동 Story 작업을 실제로 시작했다. `0.4.2`에서 `0.5.0`으로 in-place update됐고 `firstInstallTime`이 유지되어 기존 등록과 private app data가 보존됐다.

| 검증 항목 | 실제 결과 |
|---|---|
| 미리보기 | 2026-09-07, Apple·Google, 총 최대 10장. Apple 0장, Google Picker 선택 필요, Android 원본 104장은 GPS 참고치로만 표시 |
| 시작 확인 | 날짜·출처·최대 장수·6시간 제한·기존 결과 재사용·자동 앨범 미변경을 앱에서 다시 표시 |
| 통합 실행 | combined parent와 Apple·Google child 모두 `completed`; Google 할당량 5장 선택 후 완료 버튼 자동 클릭 |
| Qwen browser mission | `linux-long-context` 직접 경로, fallback 없음, 모델 요청 2회, prompt 6,060 / completion 72 / total 6,132 token, 모델 요청 누적 약 29.14초, browser mission 약 47.57초 |
| 안전 guard | 범위 밖 선택 0, 거부 action 0, 안정 상태 2회 확인, 최종 완료 자동 클릭 성공 |
| 재사용 결과 | 기존 처리 사진이어서 신규 분석·추천은 0장이지만 수동 실행 전용 Story에는 해당 날짜의 기존 추천 2장·chapter 1개를 재사용 |
| Story WebView | 실제 파생 이미지 2장 렌더링, 날짜·chapter 표시, system status/navigation bar 유지 |
| Story 추천 범위 | 수동 Story의 `추천 사진 보기`는 해당 Story 2장만 표시; 전역 추천 탭은 기존 30장 표시 |
| 사진 viewer | 숫자 indicator와 진행선 표시, 좌우 swipe 이동, double-tap 2.5배 확대, 확대 상태에서는 pan, 1.0배 복귀 뒤 다시 swipe 가능, 별도 `+`/`-` 버튼 없음 |
| 알림·설정 | 성공/오류 이력 표시, GPS Bridge 연결됨, 최근 동기화 시각 및 1일 주기 persisted job 확인 |
| 안정성 | 현재 앱 process의 fatal/exception/SSL/network 오류 0, Android crash buffer의 앱 관련 crash 0 |

첫 두 Android 진단 실행은 Google Picker session 생성 직후 중단됐고, 이 과정에서 운영 환경에서만 드러나는 두 경로를 수정했다.

1. 원격 Linux workstation 준비가 수 분 걸려도 Chrome의 허용된 Picker page를 잃지 않도록 **Picker를 먼저 열고 Qwen target을 준비**하도록 순서를 변경했다.
2. launchd의 최소 `PATH`에서도 절대 경로 `npx`가 같은 Homebrew 디렉터리의 `node`를 찾도록 MCP child environment를 보강했다. MCP task group 오류도 `chrome_mcp_unavailable`로 정규화한다.

두 수정 후 같은 Android UI 흐름으로 세 번째 실행을 반복해 Qwen 직접 경로로 끝까지 완료했으며, 관련 회귀 테스트를 전체 suite에 포함했다. 진단 실패 두 건은 삭제하지 않고 앱 작업·알림 이력에 감사 기록으로 남겼다.

### Google Picker 250장 전역 상한 보강

같은 날 Android에서 2026-09-02~09-08, Apple·Google 총 500장(각 250장) 수동 작업을 실행했다. Apple은 34장을 발견해 기존 처리 28장을 제외한 6장 분석을 완료했다. Google Qwen mission은 127개를 선택한 뒤 300초 mission timeout으로 deterministic fallback에 진입했고, Picker 가상 grid의 화면 밖 선택을 fallback이 세지 못해 전역 선택이 275장까지 증가했다. 완료 버튼은 비활성화됐고 기존 안전 검증이 session을 취소했으므로 Google 사진 다운로드·분석·앨범 변경은 0건이었다.

실제 Chrome 접근성 snapshot의 전역 문구는 `275장 선택함 항목 최대 250개 선택`이었다. 이를 기준으로 다음을 보강했다.

- Picker dialog의 전역 선택 수와 Picker 자체 최대 수를 파싱한다.
- 화면에 보이는 `checked` row 수보다 전역 선택 수를 우선한다.
- Qwen이 이미 성공시킨 누적 선택 수를 fallback 예산에 포함한다.
- fallback은 `전체 제한 - 현재 전역 선택 수`만 추가 선택한다.
- 각 batch와 최종 완료 직전에 요청 제한과 Picker 자체 제한 중 작은 값을 적용한다.
- 전역 수가 상한을 넘으면 추가 클릭과 완료를 모두 금지하고 `picker_selection_limit_exceeded`로 기록한다.
- 결과에는 Qwen click, fallback click, 합계와 최종 전역 선택 수를 분리해 남긴다.

가상 grid에서 Qwen이 선택한 127개 중 25개만 현재 DOM에 남는 장애 조건을 회귀 테스트로 재현했다. 수정 후 fallback은 정확히 123개만 추가해 `127 + 123 = 250`에서 완료했고 251번째 click은 발생하지 않았다. 이미 `275 / 250`인 snapshot에서는 선택 상태를 변경하지 않고 즉시 차단하는 테스트도 통과했다.

### Story·모바일 출력 검증

- 9월 7일 실행 전용 Story: 추천 사진 2장, chapter 1개
- private HTML: gallery tile 2개와 현재 사진 indicator 포함
- mobile projection: `source_path`, `relative_path`, 정밀 위도·경도, 원본 다운로드 필드 없음
- 자동 앨범 쓰기, 외부 Story 공유, Telegram 사용자 메시지는 이번 내부 검증에서 수행하지 않음

### Android release

```text
versionName: 0.5.0
versionCode: 7
package: com.photosmcp.locationbridge
minSdk / targetSdk: 26 / 36
R8 + shrinkResources: 통과
lintRelease: 통과
APK signature v2/v3: 통과
APK size: 104,721 bytes
SHA-256: d4f23cae55cafc47f29891d9ebbf19905618c53c116224f5948fa2f8119ca89b
```

기존 설치와 같은 Android debug certificate로 non-debuggable release 변형을 서명했다. 따라서 현재 개인용 설치에는 in-place update가 가능하지만, 장기 배포용 별도 private release key로 전환하려면 기존 앱 데이터 migration을 별도로 설계해야 한다.

### 운영 probe

- Tailnet 443 capability: `controls=true`, `manual_curation=true`, 최대 1,000장·21,600초
- Tailnet 443 unsigned manual POST: `401`
- public Funnel 8443 manual API: `404`
- Tailscale APK full download SHA-256: 로컬 게시 파일과 일치
- launchd: `PHOTOS_MCP_MOBILE_CONTROLS_ENABLED=1`, `PHOTOS_MCP_STORY_DIRECTOR_ENABLED=1`, loopback `127.0.0.1:18794`
- 기존 Tailscale `/mobile-client` proxy와 공개 `/mobile-location` 분리는 변경하지 않음

Android release의 `lintRelease`와 `assembleRelease`를 clean build에서 다시 통과시켰고, 서명 검증도 v2/v3 모두 통과했다. Tailnet 다운로드 page와 APK endpoint는 각각 HTTP 200을 반환했고, 내려받은 APK의 크기와 SHA-256이 게시 원본과 일치했다. 그 APK를 실기기에 다시 설치한 뒤 앱 재시작, Mac 연결 상태, 날짜 Story 진입점, 상단 알림·설정, 하단 navigation을 재확인했다. Google 로그인·MFA·CAPTCHA가 다시 요구되면 자동 클릭하지 않고 기존 사용자 조치/Telegram 오류 경계로 멈춘다.

## 설치

Tailnet 안에서 다음 페이지를 Chrome으로 열어 설치한다.

<https://byoungyoung-macmini.tail53bcc7.ts.net/mobile-client/download>

기존 앱 위에 설치할 때는 Android의 `업데이트`를 선택하면 등록 정보와 GPS outbox가 유지된다.
