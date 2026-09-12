---
status: resolved
trigger: "좀전 3시와 그 이전에 수동으로 진행한 내용에 오류가 발생했다는데 원인 및 그에 따른 수정을 진행해줘봐"
created: 2026-09-12
updated: 2026-09-12
---

# Symptoms

- Expected: 새벽 3시 예약 실행과 그 이전 수동 실행이 각 요청 범위를 정상 처리하고, 성공·부분 성공·실패 상태와 원인을 정확히 표시한다.
- Actual: 두 실행 모두 사용자에게 오류가 발생한 것으로 표시됐다.
- Error: 로그와 상태 저장소에서 수집 예정.
- Timeline: 2026-09-12 03:00 KST 예약 실행과 그 직전 수동 실행.
- Reproduction: Android 또는 PhotosMcp 수동 실행 후, 이어서 매일 03:00 KST 예약 자동화를 실행한다.

# Current Focus

- hypothesis: 세 개의 독립 결함(공유 VLM 준비 경쟁, Apple 증분 어댑터 누락, 앨범 helper 제한시간 부족)이 수동·예약 실행의 서로 다른 단계를 실패시켰다.
- test: 어댑터·동시 prepare·앨범 timeout 계약 회귀 테스트 후 앱과 mobile-client를 재배포하고 실제 loopback MCP로 Apple 증분 조회를 검증한다.
- expecting: Apple 예약 child가 정상 시작되고, 동시에 시작한 VLM prepare가 직렬화되며, 앨범 helper가 600초 예산을 사용한다.
- next_action: 전체 테스트, 설치 앱 재빌드, mobile-client 재시작, 운영 smoke test를 수행한다.

# Evidence

- Manual combined run `combined-88604c8344d1479fb5fd` completed Google analysis
  (`f838f3b7`) but Apple analysis `58fda61f` failed while both jobs entered the
  remote VLM prepare path together. The second prepare command received repeated
  HTTP 503 responses and then failed to bind the already-owned loopback forward
  `127.0.0.1:12801`.
- The same tunnel race previously appeared between jobs `a31edb32` and
  `56aafde6`, confirming a repeatable shared-runtime concurrency defect.
- Scheduled parent `combined-b4a1986ced7b41928947` successfully materialized and
  analyzed 37 Google photos in job `59487656`. Its deferred Apple child failed
  to start with `AttributeError` because `_LocalMcpPhotoSourcePort` implemented
  capture-date `list_photos` but not incremental `list_added_photos`.
- Google recommendation publication then tried to import 18 paths into
  `Photos MCP/2026-09 추천` and exceeded AlbumWriter's fixed 240-second Terminal
  helper timeout. The app remained healthy afterward; subsequent
  `ClosedResourceError` entries were terminated MCP streams, not the initiating
  failure.
- Added a read-only `photos_query(action="added")` boundary and wired the mobile
  local-MCP adapter to it, serialized VLM prepare commands, raised the bounded
  album helper timeout to 600 seconds, and persisted bounded child-start detail.
- Targeted regression suite: 90 tests passed.
- The shared operational script `/Users/byoungyoungla/bin/ensure-linux-llm` now
  also holds an inter-process lock, covering Story generation or any second
  process outside the PhotosMcp app. Two simultaneous safe health-path
  invocations both completed and left no stale lock.

# Eliminated


# Resolution

- root_cause: The manual run raced two callers onto one fixed SSH tunnel while
  the VLM returned 503 during preparation. The scheduled run used a mobile
  Apple adapter without the incremental method required after the Google-first
  gate. A later recommendation import had only 240 seconds for Photos/iCloud.
- fix: Added the Apple added-time query route and adapter method, serialized
  runtime preparation both in-process and in the shared prepare script, raised
  the album helper budget to 600 seconds, and retained bounded child exception
  detail for Android diagnostics.
- verification: 1,008 repository tests passed; the rebuilt signed app reports
  healthy with zero active jobs; installed MCP `photos_query(action="added")`
  returned two items and a cursor; an end-to-end scheduled Apple empty-window
  smoke run completed as a no-op without `AttributeError`; both macOS app and
  mobile-client were restarted on the new code.
- files_changed: MCP action contract, library/query handlers, mobile source
  adapter, combined child error projection, vision broker, AlbumWriter timeout,
  tests, operations/tool docs, and the local shared Linux LLM prepare script.
