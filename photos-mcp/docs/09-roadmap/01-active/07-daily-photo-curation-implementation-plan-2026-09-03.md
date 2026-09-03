# 일일 사진 큐레이션 단계별 구현 계획

> 작성일: 2026-09-03
>
> 상태: Chrome DevTools MCP 자동 Picker 실행·다운로드·분석 live E2E 완료
>
> 상위 설계: [06-daily-photo-curation-automation-2026-09-03.md](./06-daily-photo-curation-automation-2026-09-03.md)

## 1. 목표

Apple Photos와 Google Photos에서 새로 유입된 사진을 하루 한 번 발견하고, 중복 실행 없이 품질 분석한 뒤, 사용자가 확인할 수 있는 추천 결과를 만든다. Google Picker의 최근 10일 선택·완료는 검증된 전용 Chrome 경로에서 자동 수행하고, 로그인·MFA·CAPTCHA·UI 불일치처럼 사람의 동의나 판단이 필요한 경계에서는 멈춰 텔레그램으로 알린다. 앨범 쓰기는 명시적 승인 뒤에만 수행한다.

첫 릴리스의 기본 모드는 `review_only`다. 사진 삭제와 Apple Photos·Google Photos 앨범 쓰기는 자동 승인하지 않는다. Picker 완료는 최근 10일·100장 상한·개별 항목 구조·완료 버튼 고유성 검증을 모두 통과한 경우에만 자동화한다.

## 2. 구현 원칙

- 신규 사진 기준은 촬영일(`date_taken`)이 아니라 보관함 유입 시각(`date_added`)이다.
- `(date_added, provider_asset_id)` 순서의 불투명 커서와 겹침 구간을 함께 사용한다.
- 처리 이력은 SQLite에 영속화하고 `(provider, source_id, provider_asset_id)`를 고유 키로 삼는다.
- 자동화 실행과 외부 쓰기는 분리한다. 분석 완료 후에도 앨범 반영은 기존 mutation approval을 통과해야 한다.
- Google Picker 브라우저 보조는 Chrome DevTools MCP가 전용 visible Chrome에서 공식 Picker 화면을 열고, 실행일 포함 최근 10일의 개별 사진을 최대 100장 선택한다. 날짜·개수·개별 항목·고유 완료 버튼 검증을 모두 통과하면 완료, 공식 다운로드, 분석 제출까지 자동으로 이어진다.
- 사용자 조치 알림의 1차 채널은 텔레그램 개인 대화다. 알림에는 OAuth 토큰, Picker base URL, 로컬 파일 경로를 넣지 않는다.
- 조치 화면은 localhost 또는 Tailscale 경로로만 노출하고 공개 인터넷에는 직접 게시하지 않는다.

## 3. 단계와 완료 조건

| 단계 | 구현 범위 | 완료 조건 | 상태 |
| --- | --- | --- | --- |
| 0 | 기준선·계획 고정 | 기존 테스트 기준과 변경 파일을 기록하고 이 문서를 action checklist로 사용 | 완료 |
| 1 | Apple 증분 탐색 | `date_added` 범위, 안정 정렬, 불투명 cursor, 중복·경계 테스트 통과 | 완료 |
| 2 | Apple ID 직접 분석 | 랭커가 Apple UUID의 명시적 목록만 로드하며 local path 보안 검사는 local에만 적용 | 완료 |
| 3 | 자동화 원장 | checkpoint, 실행, 처리 자산, 사용자 조치 요청을 SQLite에서 재시작 후 복원 | 완료 |
| 4 | `daily_curate` | 신규·미처리 자산만 읽기 전용 분석 job으로 제출하고 실행 결과를 추적 | 완료 |
| 5 | Google 조치 상태 | 사용자 조치 필요/완료/만료/취소 원장과 중복 알림 억제 | 완료 |
| 6 | 브라우저 보조 | Chrome DevTools MCP 1.8.0, PhotosMcp 전용 영구 Chrome 프로필, 최근 10일 날짜 기반 UID 선택, Google URL allowlist, 자동 완료·다운로드·분석 제출 | 20장 live E2E 통과, 운영 상한 100장 |
| 7 | Hermes/Telegram 브리지 | agent turn 없이 이벤트 전달, 신규 Google 이벤트에서 Picker 워커 자동 기동 | 구현·cron 연결 완료 |
| 8 | 통합 검증 | fake source·fake Telegram·fake Picker, 전체 회귀, 독립 앱 빌드, 최종 경로 즉시 리허설 | 완료 |

## 4. 단계별 변경 예정 위치

### 단계 1: Apple `date_added` 증분 탐색

- `vendor/photo-source/models.py`: `date_added` 메타데이터 추가
- `vendor/photo-source/sources/apple_photos.py`: 범위 필터·정렬·페이지 cursor 구현
- `vendor/photo-source/server.py`: 내부 `list_added_photos` 도구 제공
- `infrastructure/vendor_adapter/photo_source.py`: provider-neutral page adapter 추가
- 테스트: 날짜 경계, 같은 시각의 UUID tie-break, cursor tampering, 영상 제외

### 단계 2: 정확한 Apple UUID 분석

- `vendor/photo-ranker/sources.py`: `_load_apple(..., selected_photo_ids=...)`
- `vendor/photo-ranker/server.py`: Apple UUID 목록 허용, local 경로 검증은 local source에만 적용
- 테스트: 지정하지 않은 Apple 자산이 분석 목록에 섞이지 않는지 확인

### 단계 3: 영속 상태

SQLite에 다음 논리 레코드를 추가한다.

- `photo_automation_checkpoints`: 마지막 성공 cursor와 겹침 구간
- `photo_automation_runs`: 자동화 실행과 연결된 분석 run
- `processed_photo_assets`: 발견/제출/완료/건너뜀 상태
- `user_action_requests`: 텔레그램 알림 가능한 사용자 조치 상태

원본 사진 바이트, OAuth token, Google Picker 임시 다운로드 URL은 저장하지 않는다.

### 단계 4: `daily_curate`

공개 MCP 도구 수는 늘리지 않고 `photos_workflow(action="daily_curate")`를 추가한다.

- 기본 `source=apple`, `mode=review_only`
- 새 자산이 없으면 성공적인 no-op
- 분석 대상은 발견 결과의 Apple UUID 명시 목록으로 제한
- 쓰기 옵션이나 승인 token을 받지 않음
- 반환값에 `automation_run_id`, `analysis_run_id`, 발견·제외·제출 개수를 포함

### 단계 5~7: Google Picker와 텔레그램

`UserActionRequiredEvent`의 최소 필드는 다음과 같다.

- `request_id`, `request_type`, `status`, `reason_code`
- `title`, `message`, `action_url`, `expires_at`
- `provider`, `automation_run_id`, `dedupe_key`

텔레그램 메시지는 이벤트의 안전한 표현만 전송한다. Telegram bot token과 chat ID는 기존 Hermes secret/config 경계에서만 읽고 PhotosMcp DB에 복제하지 않는다.

## 5. 테스트 계층

1. 단위 테스트: cursor, date 경계, ledger idempotency, event redaction
2. adapter 테스트: 가짜 Apple DB, 가짜 Picker API, 가짜 Telegram transport
3. MCP 계약 테스트: action registry와 문서 표의 일치
4. 통합 테스트: 동일 batch 재실행 시 분석 중복 0건, 재시작 후 checkpoint 복구
5. 전체 회귀: 기존 pytest 전체 통과
6. live gate: 사용자가 승인한 뒤에만 실제 Photos/Google/Telegram 대상으로 소량 검증

## 6. 외부 동작 승인 경계

다음 작업은 코드와 fake 통합 테스트가 끝나도 자동 승인하지 않는다.

- 실제 Google 계정에서 날짜·구조 검증 없는 Picker 선택 확정
- 실제 텔레그램 메시지 발송
- Apple Photos 앨범 생성·추가·삭제
- 매일 실행하는 launchd 또는 Hermes cron 활성화

각 live 검증은 대상, 예상 변경량, 되돌림 방법을 제시한 뒤 사용자의 승인을 받아 별도 수행한다.

## 7. 구현 기록

- 2026-09-03: 기존 구조 조사 완료. Apple source에 `date_added`/cursor가 없고 photo-ranker의 explicit selection이 local source에만 허용된 사실을 확인했다.
- 2026-09-03: 안전한 수직 구현 순서를 `증분 탐색 → UUID 직접 분석 → 원장 → daily_curate → 사용자 조치 → 브라우저 보조 → Hermes/Telegram`으로 확정했다.
- 2026-09-03: Apple `date_added` 증분 탐색과 `(date_added, UUID)` cursor, explicit UUID 분석, SQLite 처리 원장을 구현했다.
- 2026-09-03: `photos_workflow(action="daily_curate")`, loopback 전용 HTTP trigger, read-only action page와 Google 사용자 조치 outbox를 구현했다.
- 2026-09-03: Hermes custom integration에 no-agent trigger/notification bridge를 추가하고 `~/.hermes/scripts/`에는 canonical script를 실행하는 얇은 래퍼만 설치했다.
- 2026-09-03: 이 Mac 전용 내부 trigger가 `https://byoungyoung-macmini.tail53bcc7.ts.net/photos-actions`를 전달하도록 했고 PhotosMcp는 localhost/HTTPS `*.ts.net` 이외 URL을 거부한다.
- 2026-09-03: 초기 PhotosMcp 전체 `678 passed`, Hermes bridge `3 passed`, py_compile을 통과했다.
- 2026-09-03: 임시 경로에 standalone `PhotosMcp.app`을 빌드해 코드서명, `--health`, `--runtime-import-smoke`, `--vendor-runtime-smoke`를 모두 통과했다. py2app의 깊은 유한 dependency graph를 위해 build-time recursion limit을 10,000으로 올렸다.
- 2026-09-03: 검증 번들을 `/Volumes/ExtData/02_Services/PhotosMcp/PhotosMcp.app`에 배포하고 새 프로세스로 재시작했다. `/Applications/PhotosMcp.app` 링크도 이 정식 경로로 고정했으며 health와 코드서명을 재확인했다.
- 2026-09-03: Tailscale Serve의 기존 Open WebUI `/`와 Hermes Dashboard `:9119`를 유지하면서 `/photos-actions`만 PhotosMcp `/actions`에 추가했다. Serve 상태는 `tailnet only`다.
- 2026-09-03: Apple 최근 24시간 live 실행은 신규 유입 0건으로 `completed/no_op` 정상 종료했다. 분석·앨범 write는 발생하지 않았다.
- 2026-09-03: 최초 Google live trigger에서 `action_base_url`이 action 계약에 누락된 통합 결함을 발견했다. 외부 동작 없이 차단됐으며, 정식 옵션으로 수정하고 계약 테스트를 추가한 뒤 재빌드·재배포했다.
- 2026-09-03: 수정 후 Google 사용자 조치 이벤트 1건을 만들었다. Hermes trigger 재실행 시 같은 dedupe key의 행은 1건만 유지됐고, notification dry-run과 Tailscale action page 200/보안 헤더를 확인했다.
- 2026-09-03: Hermes Telegram home channel 실제 전송에 성공했고 사용자가 수신을 확인했다. `message_id=2369`이며 이벤트는 전송 뒤 `notified`가 됐다.
- 2026-09-03: 사용자가 Google Picker에서 수동으로 19장을 선택했다. 공식 다운로드와 local handoff 뒤 job `8c51ca73`이 291.94초에 완료됐고 19장 결과, 중복 후보 2장, 추천 8장을 만들었다. 원본·앨범 write는 발생하지 않았다.
- 2026-09-03: Picker에서 분석 job이 승인되면 최신 outstanding Google action과 자동화 실행을 `completed`로 닫고 `analysis_run_id`에 연결하도록 보완했다. 현재 action page도 완료 상태를 표시한다.
- 2026-09-03: 알림 bridge를 `SQLite lease → hermes send → 성공 ack` 순서로 개선했다. 실패 시 `pending/retry`, 프로세스 중단 시 lease 만료 후 재시도하며 event별 attempt audit를 남긴다. Hermes bridge `5 passed`, PhotosMcp 전체 `680 passed`를 통과했다.
- 2026-09-03: 갱신한 standalone 앱을 재빌드·서명·배포하고 health `ok/ready`를 확인했다.
- 2026-09-03: `PhotosMcp 일일 읽기 전용 선별`(`f164549b6993`, 매일 03:00 KST)과 `PhotosMcp 사용자 조치 알림`(`7805f898da3a`, 5분 간격) cron을 활성화했다. 두 작업 모두 강제 1회 실행의 durable 상태가 `completed`였다. 앨범 write cron은 등록하지 않았다.
- 2026-09-03: 14일 shadow 관찰을 선행 조건으로 두지 않고 03시 최종 경로를 고유 source id로 즉시 재현했다. 이 과정에서 최신 Google Picker 경로(`/integration/picker/auth/`) 미허용을 발견해 수정하고 회귀 테스트를 추가했다.
- 2026-09-03: `run_google_picker_assisted.py`를 추가했다. 신규 Picker 세션 생성, 현재 Chrome 기동 확인, Chrome DevTools MCP 연결, 조건부 자동 완료, 공식 다운로드, 분석 job의 terminal 상태 확인, action/automation 완료 연결을 한 프로세스로 수행하며 Picker URI와 OAuth 정보는 로그에 출력하지 않는다.
- 2026-09-03: Hermes 03시 trigger가 새로운 Google action을 만든 경우 위 워커를 detached process로 한 번만 시작하도록 연결했다. 워커는 파일 lock으로 중복 Chrome 실행을 차단한다.
- 2026-09-03: 사용자의 요청에 따라 Playwright 활성 경로와 선택 의존성을 제거하고 `chrome-devtools-mcp@1.8.0`으로 교체했다. 초기 연결 검증에서는 Chrome 152 `--auto-connect`와 `--slim`을 사용했지만, 실제 선택·승인 경로를 구현하면서 다음 항목처럼 loopback `--browser-url`과 전체 입력 도구 구성으로 교체했다.
- 2026-09-03: Chrome의 live-session 원격 디버깅 승인 창이 연결마다 나타나는 문제를 피하기 위해 개인용 Chrome `--auto-connect`를 운영 경로에서 제거했다. `~/.photos-mcp/chrome/google-picker-profile`의 `0700` 전용 영구 프로필을 일반 Chrome으로 실행하고 MCP는 loopback `--browser-url=http://127.0.0.1:9333`으로 연결하도록 전환했다. MCP가 WebDriver로 실행한 창에서는 Google 로그인이 차단됐지만, MCP를 붙이지 않은 일반 전용 Chrome에서 사용자가 로그인한 뒤 같은 프로필을 재사용하는 경로는 성공했다. 승인 창 없이 신규 Picker, 날짜 그룹, 오늘 사진 1장 실제 선택, 완료 버튼 활성화를 live 확인했다.
- 2026-09-03: 선택 정책을 실행일 포함 최근 10일로 확정했다. 접근성 날짜 heading을 실제 날짜로 변환하고, `description`이 있는 미리보기 button을 소유한 개별 사진 checkbox만 `take_snapshot`과 실제 `click(uid)`로 선택한다. 날짜 그룹 복수 선택 checkbox와 10일보다 오래된 사진은 제외한다.
- 2026-09-03: 최근 10일 live E2E에서 20장을 선택하고 완료 버튼을 실제 클릭했다. Picker API 다운로드 20/20, 영상 제외 0, 중복 후보 1, 랭킹 결과 20개를 만들었고 분석 job `78d7e657`이 386.82초에 완료됐다. 첫 실행에서 발견한 독립 워커의 background task 조기 종료 문제는 terminal 상태까지 폴링하도록 수정했다.
- 2026-09-03: Picker dialog 안의 `role=checkbox`와 접근성 label의 사진·선택 의미를 함께 검증해 날짜 그룹·전체 선택과 개별 사진을 구분했다. 초기 안전 검증에서는 5장 preselect만 수행했고, 이후 완료 전 재검증과 실제 클릭을 추가해 20장 live E2E를 통과했다. 운영 기본값은 최근 10일 내 개별 사진 전체, Picker 안전 상한 100장이다.
- 2026-09-03: 운영 기본값과 같은 `recent_days=10`, `preselect_count=100`, `limit=100`으로 최종 live 실행했다. Picker가 최근 10일 내 개별 사진 23장을 찾아 모두 선택·완료했고 공식 다운로드 23/23, 영상 제외 0을 확인했다. 분석 job `434857c8`은 268.59초에 `completed`가 되었으며 랭킹 23장, 중복 후보 1장, 장면 추천 6장을 기록했다. 원본·앨범 write는 발생하지 않았다.
- 2026-09-03: 동기 분류 경로가 분석 완료를 기다린 경우 workflow 결과와 진행 이벤트도 `completed`/`analysis_completed`로 표시하도록 보완했다. 비동기 호출 호환성을 위해 아직 terminal 상태가 아닌 adapter 결과는 기존 `analysis_submitted`를 유지한다.
- 2026-09-03: 매일 10일 창을 다시 선택해도 같은 사진을 재분석하지 않도록 Google Picker의 안정 자산 ID를 `processed_photo_assets` 원장과 연결했다. 이미 `submitted/completed`인 자산은 콘텐츠 다운로드 전에 제외하고, 모두 기처리 자산이면 분석 job 없이 `completed/no_new_photos`로 종료한다. 최종 live job `434857c8`의 23개 자산도 원장에 `completed`로 반영했으며 전체 회귀는 `689 passed`다.
- 2026-09-03: 같은 10일 창을 즉시 다시 실행해 실제 중복 방지를 검증했다. Picker 선택·완료 후 23개 모두 `previously_processed`로 판정됐고 다운로드 0건, 신규 분석 job 0건, 최종 `completed/no_new_photos`로 28초 안에 종료했다.
- 2026-09-03: 분리 Picker 워커가 Chrome·Picker·다운로드·분석 단계에서 비정상 종료하면 Hermes의 durable 사용자 조치 outbox에 redacted 오류 이벤트를 넣도록 연결했다. 기존 5분 Telegram bridge의 lease·ack·retry를 그대로 사용하며, 같은 component/exit code는 한 시간에 한 건으로 억제한다. outbox 자체가 불가능할 때만 안전한 고정 문구로 직접 발송을 1회 시도한다.
- 2026-09-03: 5분 알림 bridge가 Apple·Google background analysis job의 `failed/cancelled/interrupted`도 자동화 run과 연결해 감지하도록 확장했다. 실행별·terminal 상태별 한 건만 발송하며 job DB의 raw error message는 전송하지 않는다. Hermes 브리지 집중 테스트 `9 passed`, 운영 runtime 전체 `30 passed`를 확인했다.
- 2026-09-04: 03:00 KST cron은 실행됐지만 내부 UTC 날짜(`2026-09-03`)로 Google dedupe key를 계산해 전날 완료 action을 재사용했고, `notification_required=false`를 Picker 기동 조건으로 함께 사용해 워커가 시작되지 않은 결함을 확인했다. 저장 시각은 비교·만료를 위해 UTC로 유지하되 일일 작업 날짜와 dedupe key는 `Asia/Seoul`의 실행일을 사용하도록 수정했다.
- 2026-09-04: Google 일일 실행 결과에 `picker_worker_required`, `picker_worker_reason`, `local_run_date`를 명시했다. 신규 pending action만 워커를 시작하고, 이미 진행 중인 action과 같은 날 완료 action은 각각 명시적인 active/no-op 상태로 반환한다. Hermes는 이 신호가 없거나 상태와 모순되면 성공으로 숨기지 않고 실패 처리해 Telegram 오류 전달 경로에 태운다.
- 2026-09-04: 사용자에게 보이는 Telegram 만료 시각을 모두 `YYYY-MM-DD HH:MM KST`로 변환했다. DB의 ISO 시각과 lease·만료 비교는 UTC 기준을 유지해 시간대 표시 변경이 스케줄·재시도 의미를 바꾸지 않게 했다.
- 2026-09-04: PhotosMcp 전체 `692 passed`, Hermes runtime 전체 `32 passed`를 통과한 번들을 재빌드·서명·정식 경로에 배포하고 새 프로세스로 재기동했다. 누락 실행을 같은 Hermes entrypoint로 재호출해 KST `2026-09-04` action을 별도 생성했고, Picker가 최근 10일의 15장을 자동 선택한 뒤 완료 버튼까지 눌렀다. 15장 모두 처리 원장에 있어 다운로드·분석을 반복하지 않고 `completed/no_new_photos`로 종료했으며, UTC 충돌로 남은 이전 대기 run도 완료 action과 일치하도록 `completed`로 정합화했다.
- 2026-09-04: 실제 등록 job `f164549b6993`도 수동 트리거해 `succeeded`를 확인했다. 같은 KST 날짜 action은 1행만 유지됐고, 완료 run은 `already_completed_today` no-op으로 종료했으며 pending 알림·추가 Picker 워커·중복 Telegram 메시지는 생성되지 않았다. 다음 예약은 `2026-09-05 03:00 KST`다.
- 2026-09-04: 사용자 요청에 따라 신규 처리 사진이 0장인 정상 완료도 Telegram 성공 요약을 보내도록 확장했다. Apple은 `submitted_count=0`인 당일 no-op, Google은 최종 `result=no_new_photos`를 대상으로 하며 기존 5분 outbox의 lease·ack·retry를 재사용한다. Google 완료 결과와 기존 처리 건수는 같은 날 재실행 뒤에도 automation run에 보존한다.
- 2026-09-04: 0건 완료 알림의 중복 키는 실행 ID가 아니라 `provider + KST 실행일`로 확정했다. 첫 활성화 시 같은 날 존재하던 Apple no-op run 두 건이 각각 발송된 문제를 발견해 즉시 수정했으며, 실제 cron 재실행 후에는 추가 Apple/Google 알림, pending 이벤트, Picker 워커가 생기지 않았다. Google 0건 메시지는 실제 Telegram 전달과 `notified` ack까지 확인했다. 최신 전체 회귀는 PhotosMcp `693 passed`, Hermes `33 passed`다.

## 8. 현재 남은 운영 단계

기존 수동 Picker 경로와 Chrome DevTools MCP의 기존 Chrome 연결·Picker 이동은 live gate를 통과했다. 14일 관찰은 배포 판단의 선행 조건이 아니라 성공 후 운영 안정성을 확인하는 후속 관찰로 변경한다.

1. Apple Photos에 실제 신규 사진이 들어온 날 03:00 cron이 UUID 증분 분석 job을 시작하는지 확인한다. Google 경로는 2026-09-04 KST 누락분 재실행으로 Picker 자동 선택·완료와 중복 방지까지 재검증했다.
2. Google 알림의 실패 재시도가 실제 플랫폼 장애 뒤 정상 회복하는지 운영 incident로만 확인한다. 검증을 위한 고의 장애는 만들지 않는다.
3. 즉시 리허설이 통과하면 이후 운영 중 신규 사진 중복 처리, Telegram 과다 알림, Linux VLM on-demand 기동, 캐시 사용량을 관찰한다. 14일은 권장 관찰 창이지 기능 사용을 막는 대기 기간이 아니다.
4. 추천 결과를 사용자 검토와 비교해 장면별 Top-1/Top-2 정책을 확정한다.
5. 그 뒤에도 원본 삭제는 도입하지 않는다. Apple Photos 후보 앨범 add-only 자동화는 별도 standing approval 설계와 사용자 승인 후에만 연다.
