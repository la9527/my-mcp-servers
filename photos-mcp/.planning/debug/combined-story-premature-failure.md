---
status: resolved
trigger: "Android에서 Apple Photos와 Google Photos 통합 Story 작업 실행 시 Google Picker가 chrome_mcp_unavailable로 실패하고 Apple 분석이 계속되는데도 부모 작업이 즉시 failed로 종결됨"
created: 2026-09-10
updated: 2026-09-10
---

# Symptoms

- Expected: Apple·Google 자식 작업이 실제 종료될 때까지 부모가 대기하고, 성공한 소스를 포함해 Story를 생성한다.
- Actual: Google Picker 실패 직후 부모와 수동 작업이 failed가 됐지만 Apple VLM 작업은 계속 실행된다.
- Error: `chrome_mcp_unavailable`
- Timeline: 2026-09-09 23:56 KST Android 수동 작업에서 재현됐다.
- Reproduction: 날짜 범위 2026-09-01~2026-09-09, Apple+Google, 각 250장, 균형/일반 프로필로 실행한다.

# Current Focus

- hypothesis: 확인 완료. 별도 프로세스가 공유 photo-ranker DB를 열 때 `running` 작업의 런타임 메타데이터를 완료 결과로 오인했다.
- test: 라이브 `vlm_runtime` 메타데이터가 있는 `running` 작업 보존, 부분 성공 부모 상태, Chrome 일시 오류 재시도를 회귀 테스트로 고정했다.
- expecting: Apple 분석은 `finished_at`이 기록될 때까지 `running`을 유지하고, Google 단독 실패 뒤 Apple이 정상 종료하면 부모는 `partial`로 종결된다.
- next_action: 해결 완료. 새 독립 실행 앱과 mobile-client 서비스를 재기동하고 운영 상태를 확인했다.

# Evidence

- timestamp: 2026-09-10T00:05:18+09:00
  observation: 부모 `combined-1740a7db102f4253b3b4`와 Apple 자식은 terminal로 저장됐지만 photo-ranker `10017f5a`는 VLM 37/69로 계속 진행 중이었다.
- timestamp: 2026-09-10T00:05:18+09:00
  observation: Google 자식은 Picker 세션 생성 8.022초 후 `chrome_mcp_unavailable`로 실패했다. 확인 시 Chrome CDP 9333과 chrome-devtools-mcp 프로세스는 실행 중이었다.

# Eliminated

- hypothesis: Apple 분석 자체가 정지했다.
  reason: 27→32→33→37장으로 진행량이 증가했다.

# Resolution

- root_cause: `JobDB._repair_stale_jobs()`가 `result_json IS NOT NULL`인 모든 `running` 행을 `completed`로 복구했다. 분석 시작 직후 기록되는 `vlm_runtime`도 `result_json`이므로, mobile-client 등 두 번째 프로세스가 DB를 열면 진행 중 작업이 조기 완료됐다. 동시에 Google Picker의 일시적인 Chrome MCP 접속 실패가 재시도 없이 즉시 자식 실패가 됐고, 추천 0장인 정상 Apple 소스가 있어도 부모를 `failed`로 판단했다.
- fix: stale 완료 복구 대상을 `finished_at IS NOT NULL`인 레거시 행으로 제한했다. Chrome MCP 연결/첫 탐색은 최대 3회 재시도하며, 한 소스가 정상 완료되고 다른 소스가 실패한 경우 추천 수가 0이어도 부모를 `partial`로 유지한다.
- cleanup: 문제 실행의 분석 작업 1건, 산출물 70개(3,018,447 bytes), 자동화 실행 3건, 브라우저 미션 1건, 사용자 액션 2건, 추천 컬렉션 1건, 처리 표시 69건, Hermes 전달 1건, Picker 세션/로그를 정확한 ID 기준으로 삭제했다. 기존 사진 원본과 이전 Story는 보존했다.
- verification: 관련 회귀 테스트 102개와 전체 테스트 936개가 통과했다. 독립 실행 앱을 재패키징·서명 검증하고 재기동했으며 `/health`에서 `active_job_count=0`, `recent_job_count=0`, `background_job_running=false`를 확인했다.
- files_changed: `src/photos_mcp/vendor/photo-ranker/db.py`, `src/photos_mcp/application/combined_curation.py`, `src/photos_mcp/infrastructure/browser_assist/qwen_browser_mission.py`, `tests/test_photo_ranker_db.py`, `tests/test_combined_curation.py`, `tests/test_qwen_browser_mission.py`
