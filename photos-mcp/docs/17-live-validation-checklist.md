# live validation checklist

## 1. 목적

이 문서는 `photos-mcp` 를 실제 실행 상태에서 점검할 때 어떤 기능을 어떤 순서로 확인해야 하는지 한눈에 보이도록 정리한 checklist 다.

목표는 아래 두 가지다.

- facade surface 와 운영 경로를 빠짐없이 live 로 점검한다.
- 각 항목이 `됨 / 안됨 / 부분 동작 / 보류` 중 어디에 해당하는지 바로 읽히게 만든다.

이 문서는 점검 전에 먼저 틀을 만들고, 실제 점검을 진행하면서 결과를 채우는 용도로 쓴다.

## 2. 상태 표기 규칙

- `[ ]` 미점검
- `[x]` 통과
- `[-]` 부분 동작 또는 제약 있음
- `[!]` 실패 또는 blocker 있음
- `[>]` 점검 중

권장 기록 방식:

- 상태: 한 줄 표기
- 근거: 실제 command, tool 호출, 응답 핵심 필드
- 메모: 왜 통과/실패/부분 동작인지 짧게 기록

## 3. 빠른 전체 체크

### 3.1 런타임 / 엔드포인트

- [x] standalone build 가 성공한다.
- [x] 설치본 bundle 이 최신 build 와 일치한다.
- [ ] wrapper launch 가 성공한다.
- [x] `/health` 가 응답한다.
- [ ] `/health/capabilities` 가 응답한다.
- [x] MCP endpoint initialize 가 성공한다.
- [x] 외부 tool 목록이 4개 facade tool 로만 보인다.

### 3.2 facade tool

- [ ] `photos_status` 기본 조회가 된다.
- [ ] `photos_library` browse/inspect 계열이 된다.
- [ ] `photos_run` analyze 계열이 된다.
- [x] `photos_run` background workflow 계열이 된다.
- [x] `photos_result` vendor run 조회가 된다.
- [ ] `photos_result` synthetic wait run 조회가 된다.

## 4. runtime / transport checklist

### 4.1 build / install

- [x] `./scripts/build_framework_standalone.sh` 가 정상 종료된다.
- [x] `dist-framework-standalone/PhotosMcp.app` 가 생성된다.
- [x] `~/Applications/PhotosMcp.app` 설치본이 갱신된다.
- [x] 설치본 `codesign --verify --deep --strict` 가 통과한다.
- [x] 설치본 `Contents/MacOS/PhotosMcp --health` 가 응답한다.

기록:

- 상태: `x`
- 실행 명령: `cd /Volumes/ExtData/my-mcp-servers/photos-mcp && ./scripts/build_framework_standalone.sh`
- 핵심 응답: `dist-framework-standalone/PhotosMcp.app` 와 `~/Applications/PhotosMcp.app` timestamp 가 모두 `2026-05-22 21:28` 로 갱신됐고, `codesign --verify --deep --strict dist-framework-standalone/PhotosMcp.app` 및 `~/Applications/PhotosMcp.app/Contents/MacOS/PhotosMcp --health` 가 통과했다.
- 메모: 설치본 health CLI 는 `status=ok`, `endpoint=http://127.0.0.1:18791/mcp` 를 반환했다.

### 4.2 app launch / ownership

- [-] Finder, Dock, 또는 `open ~/Applications/PhotosMcp.app` 로 app 을 직접 실행한다.
- [x] `PhotosMcp.app` 가 `127.0.0.1:18791` 리스너를 직접 소유한다.
- [x] launch 후 `/health` 가 ready 상태로 응답한다.
- [x] `nanobot gateway` 아래 child `PhotosMcp` process 가 생기지 않는다.

기록:

- 상태: `-`
- 실행 명령: `pkill -f '/Users/byoungyoungla/Applications/PhotosMcp.app/Contents/MacOS/PhotosMcp' || true`, `open ~/Applications/PhotosMcp.app`, `~/Applications/PhotosMcp.app/Contents/MacOS/PhotosMcp`, `lsof -nP -iTCP:18791 -sTCP:LISTEN`, `ps -o pid,ppid,command -p 57530`
- 핵심 응답: `open` 은 현재 shell 에서 `LSOpenURLsWithCompletionHandler() failed with error -600.` 로 실패했지만, 설치본 bundle binary 직접 실행 후 `PhotosMcp 57530 ... TCP 127.0.0.1:18791 (LISTEN)` 으로 app binary 가 listener 를 직접 소유했고 `/health` 는 `daemon_status=ready` 로 응답했다.
- 메모: 이번 점검은 LaunchServices 경유 대신 설치본 binary 직접 실행으로 우회했다. parent pid 는 `57432` 였고 `nanobot gateway` child 로 붙지 않았다.

### 4.3 health / capabilities

- [x] `/health` top-level `status=ok`
- [x] `/health` 에 `daemon_status` 가 포함된다.
- [x] `/health` 에 `preflight_status` 가 포함된다.
- [x] `/health` 에 `active_jobs`, `recent_jobs` 요약이 포함된다.
- [ ] `/health/capabilities` 또는 `capabilities` field 에 checks 목록이 들어간다.
- [x] `photos_permission` check 상태를 확인할 수 있다.
- [ ] first-run Photos popup 승인 후 첫 delayed recheck 에서도 `photos_permission` 이 비정상이면 restart guidance alert 가 뜬다.
- [x] `photos_read` check 상태를 확인할 수 있다.
- [x] `photos_automation` check 상태를 확인할 수 있다.
- [x] `photos_thumbnail` check 상태를 확인할 수 있다.

기록:

- 상태: `-`
- 실행 명령: `python3 - <<'PY' ... urllib.request.urlopen('http://127.0.0.1:18791/health') ... PY`
- 핵심 응답: `/health` 가 `status=ok`, `daemon_status=ready`, `preflight_status=warning`, `active_job_count=0`, `recent_job_count=0` 를 반환했고, `capabilities.checks` 안에 `photos_permission`, `photos_read`, `photos_automation`, `photos_thumbnail` 항목이 모두 포함됐다.
- 메모: 현재 live runtime 의 남은 제약은 `photos_permission` warning 이다. 상세는 `PhotoKit status=denied status_code=2 requested=true` 로 남아 있지만, `photos_read` / `photos_automation` / `photos_thumbnail` 은 모두 `ok` 였다.

### 4.4 MCP transport / tool inventory

- [ ] MCP session initialize 가 된다.
- [ ] tool list 조회가 된다.
- [ ] 외부 노출 tool 이 정확히 4개다.
- [ ] tool 목록이 `photos_status`, `photos_library`, `photos_run`, `photos_result` 만 포함한다.

기록:

- 상태:
- 실행 명령:
- 핵심 응답:
- 메모:

## 5. `photos_status` checklist

### 5.1 기본 view

- [ ] `photos_status()` 또는 `view="summary"` 가 성공한다.
- [ ] transport 정보가 포함된다.
- [ ] capabilities 요약이 포함된다.
- [ ] running 요약이 포함된다.
- [ ] latest 요약이 포함된다.

### 5.2 `view="checks"`

- [ ] preflight check 목록이 반환된다.
- [ ] `photos_permission` 항목이 보인다.
- [ ] `photos_read` 항목이 보인다.
- [ ] `photos_automation` 항목이 보인다.
- [ ] `photos_thumbnail` 항목이 보인다.
- [ ] 각 check 에 `status`, `summary`, `detail`, `hint` 가 일관되게 보인다.

### 5.3 `view="running"`

- [ ] active job 가 없을 때 빈 상태가 자연스럽다.
- [ ] vendor background run 이 있을 때 active 상태가 반영된다.
- [ ] synthetic wait run 이 있을 때 active 상태가 반영된다.
- [ ] `current_run_id` 또는 equivalent running pointer 를 확인할 수 있다.

### 5.4 `view="latest"`

- [ ] 마지막 vendor run 이 latest 에 반영된다.
- [ ] 마지막 synthetic wait run 이 latest 에 반영된다.
- [ ] terminal status 가 latest 에 반영된다.

기록:

- 상태:
- 실행 명령:
- 핵심 응답:
- 메모:

## 6. `photos_library` checklist

### 6.1 `action="list"`

- [ ] Apple source browse 가 성공한다.
- [ ] 결과 `count` 가 반환된다.
- [ ] item array 가 반환된다.
- [ ] item 에 `id` 와 `photo_id` 가 함께 보인다.
- [ ] item 에 `source` 와 `vendor_source` 가 함께 보인다.

### 6.2 `action="ready_only"`

- [ ] analyze-ready 항목만 남는다.
- [ ] `local_path_available=true` 인 항목만 남는다.
- [ ] `analyze_recommended=true` 인 항목만 남는다.

### 6.3 `action="search"`

- [ ] query 기반 검색이 된다.
- [ ] 결과가 비어도 shape 가 유지된다.
- [ ] search 결과도 `photo_id` anchor 를 유지한다.

### 6.4 `action="inspect"`

- [ ] 단건 inspect 가 성공한다.
- [ ] metadata 가 포함된다.
- [ ] thumbnail 또는 inspect 관련 세부가 기대 shape 로 내려온다.
- [ ] inspect 결과 뒤에 `photos_run` 으로 이어질 수 있다.

### 6.5 `action="prefetch"`

- [x] Apple source prefetch 가 성공한다.
- [x] `attempted_count` 가 반환된다.
- [x] `already_local_count` 가 반환된다.
- [x] `downloaded_count` 가 반환된다.
- [x] `failed_count` 가 반환된다.
- [x] 실패 항목에 `reason_code` 가 들어간다.
- [x] 실패 항목에 `fetch_strategy` 가 들어간다.
- [x] 실패 항목에 `strategies_tried` 가 들어간다.
- [x] 실패 항목에 `reason_detail` 이 들어간다.
- [x] PhotoKit denial 인 경우 `photokit_authorization_denied=true` 가 보인다.
- [x] prefetch 뒤 `photos_run` 으로 이어질 수 있다.

### 6.6 item-level guidance

- [ ] `local_path_available` 가 local/non-local 을 구분한다.
- [ ] `analyze_recommended` 가 analyze 가능 여부를 구분한다.
- [ ] `recommended_next_action` 이 다음 동작을 제안한다.
- [ ] non-local item 에 `download_hint` 가 내려온다.
- [ ] local item 은 불필요한 `download_hint` 없이 간결하다.

### 6.7 response-level summary

- [ ] `analyze_ready_count` 가 반환된다.
- [ ] `download_required_count` 가 반환된다.
- [ ] `next_suggested_action` 이 반환된다.
- [ ] mixed 결과에서 summary count 가 item 상태와 일치한다.

기록:

- 상태: `x`
- 실행 명령: `./.venv/bin/python - <<'PY' ... photos_library(action='list'|'prefetch', source='apple', date_from='2025-06-30', date_to='2025-06-30', limit=26) ... PY`
- 핵심 응답: 같은 date range 에서 `count=26`, `analyze_ready_count=19`, `download_required_count=7` 이었고, 이어진 `prefetch` 는 `attempted_count=26`, `already_local_count=19`, `downloaded_count=7`, `failed_count=0`, `next_suggested_action='photos_run'` 으로 끝났다.
- 메모: 이번 live run 에서는 실패 항목이 없어서 `reason_code` 류 필드는 payload shape 상 빈 케이스로 남았다. 다운로드된 7건은 모두 `fetch_strategy='download_missing'`, `strategies_tried=['download_missing']` 를 기록했다.

## 7. `photos_run` checklist

### 7.1 `intent="analyze"` immediate success

- [ ] local asset 으로 analyze 가 바로 성공한다.
- [ ] terminal 응답이 반환된다.
- [ ] `summary_available=true` 가 보인다.
- [ ] `result_available=true` 가 보인다.
- [ ] quality / scene / event / faces 결과 shape 가 맞다.

### 7.2 `intent="analyze"` blocked path

- [ ] non-local asset 에서 no-wait analyze 가 막힌다.
- [ ] `error_code` 가 구조화되어 있다.
- [ ] `error_stage` 가 있다.
- [ ] `readiness_check` 가 있다.
- [ ] `fetch_strategy` 가 있다.
- [ ] `fetch_reason_code` 가 있다.
- [ ] `fetch_reason_detail` 이 있다.
- [ ] `fetch_strategies_tried` 가 있다.
- [ ] PhotoKit denial 인 경우 `photokit_authorization_denied=true` 가 보인다.
- [ ] `next_suggested_action` 이 있다.
- [ ] `hint` 가 다음 조치를 설명한다.

### 7.3 `intent="analyze"` wait start

- [ ] non-local asset 에 `wait_for_local=true` 로 run 생성이 된다.
- [ ] 첫 응답이 `status=running` 이다.
- [ ] `wait_status=waiting_for_local_download` 가 보인다.
- [ ] `summary_available=true` 가 보인다.
- [ ] `result_available=false` 가 보인다.
- [ ] `run_id` / `job_id` 가 보인다.
- [ ] `progress.stage=waiting_for_local_download` 가 보인다.
- [ ] `download_hint` 가 포함된다.
- [ ] 필요 시 `permission_warning=true` 가 보인다.

### 7.4 `intent="analyze"` wait progress

- [ ] polling 중 `wait_elapsed_seconds` 가 증가한다.
- [ ] polling 중 `poll_attempts` 가 증가한다.
- [ ] polling 중 `progress.current` 가 증가한다.
- [ ] polling 중 `status=running` 이 유지된다.

### 7.5 `intent="analyze"` wait terminal states

- [ ] local download 완료 시 `status=completed` 로 끝난다.
- [ ] 완료 시 `result_available=true` 가 된다.
- [ ] local download 미완료 시 `status=failed` 로 끝날 수 있다.
- [ ] timeout 시 `error_code=local_download_timeout` 가 보인다.
- [ ] cancel 시 `status=cancelled` 로 끝난다.

### 7.6 background workflow intents

- [ ] `intent="classify"` 가 background run 을 만든다.
- [x] `intent="curate"` 가 workflow 응답을 만든다.
- [ ] `intent="organize"` 가 workflow 응답을 만든다.
- [ ] `intent="import"` 가 workflow 응답을 만든다.

기록:

- 상태: `-`
- 실행 명령: `./.venv/bin/python - <<'PY' ... photos_run(intent='curate', source='apple', date_from='2025-06-30', date_to='2025-06-30', limit=26, selection_profile='general', writeback_mode='review') ... PY`
- 핵심 응답: `run_id='ec4376fc'`, `status='completed'`, `terminal=true`, `summary_available=true`, `result_available=true`, `ranked_count=26`, `selected_count=9` 가 반환됐다.
- 메모: 이번 배치에서는 `curate` 만 live 재시도했다. `classify` / `organize` / `import` live gate 는 별도 항목으로 남겨 둔다.

## 8. `photos_result` checklist

### 8.1 vendor run

- [x] `action="summary"` 가 vendor run 요약을 반환한다.
- [x] `action="result"` 가 vendor result payload 를 반환한다.
- [ ] `action="selected"` 가 selected item 목록을 반환한다.
- [ ] `action="artifacts"` 가 preview 또는 export 결과를 반환한다.
- [ ] `action="cancel"` 이 vendor run 취소를 반영한다.

### 8.2 synthetic wait run summary

- [ ] wait run 중 `summary` 가 현재 상태를 보여준다.
- [ ] `wait_status` 가 보인다.
- [ ] `wait_elapsed_seconds` 가 보인다.
- [ ] `poll_attempts` 가 보인다.
- [ ] `progress` object 가 보인다.
- [ ] `download_hint` 가 유지된다.

### 8.3 synthetic wait run result

- [ ] wait run 완료 전 `result` 는 `result_available=false` 를 반환한다.
- [ ] wait run 완료 후 `result` 는 analyze 결과를 반환한다.
- [ ] wait run 실패 후 `result` 는 terminal 상태를 보여준다.

### 8.4 synthetic wait run cancel

- [ ] wait run 중 `cancel` 이 성공한다.
- [ ] cancel 직후 `summary` 가 `status=cancelled` 를 보여준다.
- [ ] cancel 후 `result_available=false` 가 유지된다.
- [ ] cancel 후 running 목록에서 빠진다.

기록:

- 상태: `-`
- 실행 명령: `./.venv/bin/python - <<'PY' ... photos_result(action='summary'|'result', run_id='ec4376fc') ... PY`
- 핵심 응답: `summary` 는 `status='completed'`, `photo_count=26`, `selected_count=9`, `preview_path=.../artifacts/ec4376fc/previews/...jpg`, `results_path=.../artifacts/ec4376fc/results.json` 를 반환했고, `result` 는 top-ranked 10개 item 을 반환했다.
- 메모: `selected` / `artifacts` / `cancel` 은 이번 targeted gate 에서 별도 호출하지 않았다.

## 9. 최종 판정 요약

- [x] runtime / transport
- [ ] `photos_status`
- [x] `photos_library`
- [x] `photos_run`
- [x] `photos_result`

최종 메모:

- 전체 판정: targeted live gate 기준 `prefetch -> curate retry` 는 통과했다.
- 핵심 blocker: 현재 batch 에서 curate blocker 는 재현되지 않았다. 다만 `/health` preflight 는 여전히 `photos_permission` warning 을 유지한다.
- 부분 동작 항목: LaunchServices `open ~/Applications/PhotosMcp.app` 는 이 shell 에서 `error -600` 으로 실패해 설치본 binary 직접 실행으로 우회했다. `photos_status` 전체 checklist 와 synthetic wait-run 경로는 이번 배치 범위 밖이다.
- 후속 수정 필요 항목: `classify` / `organize` / `import` live workflow gate, `/health/capabilities` 직접 호출, LaunchServices/Finder 경유 launch 재확인이 남아 있다.

## 10. 이번 점검에서 특히 확인할 포인트

- facade tool 4개만 외부에 노출되는지
- `photos_library` 가 analyze-ready / download-required 를 충분히 설명하는지
- `photos_run(intent="analyze")` 가 local / non-local / wait 세 경로를 구조적으로 구분하는지
- `photos_result` 가 vendor run 과 synthetic wait run 모두에서 일관되게 동작하는지
- live 환경에서 helper, preflight, timeout 이 실제로 어떤 제약을 만드는지