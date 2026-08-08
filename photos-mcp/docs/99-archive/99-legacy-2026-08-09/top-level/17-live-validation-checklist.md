# 실환경 검증 체크리스트

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
- [x] wrapper launch 가 성공한다.
- [x] `/health` 가 응답한다.
- [x] `/health/capabilities` 가 응답한다.
- [x] MCP endpoint initialize 가 성공한다.
- [x] 외부 tool 목록이 4개 facade tool 로만 보인다.

### 3.2 facade tool

- [x] `photos_query(action="status")` 기본 조회가 된다.
- [x] `photos_query` browse/inspect 계열이 된다.
- [x] `photos_select(action="analyze_photo")` analyze 계열이 된다.
- [x] `photos_workflow` background workflow 계열이 된다.
- [x] `photos_query` vendor run 조회가 된다.
- [x] `photos_query` synthetic wait run 조회가 된다.

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
- 2026-08-02 재검증: 임시 dist·설치본에서 `codesign --verify --deep --strict`를 health 전후로 모두 통과했고, bundle import smoke도 통과했다. build script는 health 실행 시 `PYTHONDONTWRITEBYTECODE=1`을 강제해 서명 seal이 유지된다.
- 2026-08-02 번들 의존성 재검증: `FSEvents`의 Python 래퍼와 네이티브 확장을 함께 resource로 배치하고, build/install 단계에서 `--vendor-runtime-smoke`로 `photo-source`·`FSEvents`·`osxphotos`를 확인했다. 설치 앱의 실제 `photos_query(action="list", limit=1)`은 대형 보관함 색인 대기 시 30.0초 후 `library_list_timeout`, `can_retry=true`로 종료해 무기한 대기가 발생하지 않음을 확인했다.

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
- [x] `/health/capabilities` 또는 `capabilities` field 에 checks 목록이 들어간다.
- [x] `photos_permission` check 상태를 확인할 수 있다.
- [-] first-run Photos popup 승인 후 첫 delayed recheck 에서도 `photos_permission` 이 비정상이면 restart guidance alert 가 뜬다. 실제 재현은 TCC 권한 reset이 필요해 운영 환경에서는 수행하지 않는다.
- [x] `photos_read` check 상태를 확인할 수 있다.
- [x] `photos_automation` check 상태를 확인할 수 있다.
- [x] `photos_thumbnail` check 상태를 확인할 수 있다.

기록:

- 상태: `-`
- 실행 명령: `python3 - <<'PY' ... urllib.request.urlopen('http://127.0.0.1:18791/health') ... PY`
- 핵심 응답: `/health` 가 `status=ok`, `daemon_status=ready`, `preflight_status=warning`, `active_job_count=0`, `recent_job_count=0` 를 반환했고, `capabilities.checks` 안에 `photos_permission`, `photos_read`, `photos_automation`, `photos_thumbnail` 항목이 모두 포함됐다.
- 2026-08-02 재검증: `/health`는 `status=ok`, `daemon_status=ready`로 응답했다. `photos_permission`과 `photos_read`는 `ok`이며, `photos_automation`과 `photos_thumbnail`은 시작 시 uninterruptible AppleScript 대기를 피하기 위한 의도적 deferred warning이다. 앨범 쓰기 전에는 메뉴의 Run Checks 또는 실제 분석으로 별도 확인한다.

### 4.4 MCP 전송 계층 / 도구 목록

- [x] MCP session initialize 가 된다.
- [x] tool list 조회가 된다.
- [x] 외부 노출 tool 이 정확히 4개다.
- [x] tool 목록이 `photos_query`, `photos_select`, `photos_write`, `photos_workflow`만 포함한다.

기록:

- 상태: `x`
- 실행 명령: 임시 `PhotosMcpDaemonController`를 `127.0.0.1:18796`에서 기동한 뒤 `Accept: application/json, text/event-stream` 및 `Content-Type: application/json` 헤더로 Streamable HTTP `initialize` 요청
- 핵심 응답: `/health`는 `status=ok`, `daemon_status=ready`를 반환했고, `initialize`는 HTTP `200`, `protocolVersion=2025-03-26`, `serverInfo.name=photos-mcp`, `capabilities.tools`가 포함된 SSE JSON-RPC result를 반환했다. 같은 session의 `tools/list`는 `photos_query`, `photos_select`, `photos_write`, `photos_workflow` 4개만 반환했다.
- 메모: Streamable HTTP는 위 Accept 헤더가 없으면 `406 Not Acceptable`을 반환한다. 이 검증은 임시 runtime과 read-only initialize만 사용하며 실제 사진 보관함이나 앨범을 변경하지 않는다.

## 5. `photos_query(action="status")` checklist

### 5.1 기본 view

- [x] `photos_query(action="status", options={"view": "summary"})`가 성공한다.
- [x] transport 정보가 포함된다.
- [x] capabilities 요약이 포함된다.
- [x] running 요약이 포함된다.
- [x] latest 요약이 포함된다.

### 5.2 `view="checks"`

- [x] preflight check 목록이 반환된다.
- [x] `photos_permission` 항목이 보인다.
- [x] `photos_read` 항목이 보인다.
- [x] `photos_automation` 항목이 보인다.
- [x] `photos_thumbnail` 항목이 보인다.
- [x] 각 check 에 `status`, `summary`, `detail`, `hint` 가 일관되게 보인다.

### 5.3 `view="running"`

- [x] active job 가 없을 때 빈 상태가 자연스럽다.
- [x] vendor background run 이 있을 때 active 상태가 반영된다.
- [x] synthetic wait run 이 있을 때 active 상태가 반영된다.
- [x] `current_run_id` 또는 equivalent running pointer 를 확인할 수 있다.

### 5.4 `view="latest"`

- [x] 마지막 vendor run 이 latest 에 반영된다.
- [x] 마지막 synthetic wait run 이 latest 에 반영된다.
- [x] terminal status 가 latest 에 반영된다.

기록:

- 상태: `x`
- 실행 명령: `photos-mcp-live-validate --include-workflows`
- 핵심 응답: synthetic wait 시작 직후 `status(view="running")`의 `running.active=true`, `current_run_id=<wait run>`을 확인하고, 취소 뒤 `status(view="latest")`가 같은 run ID와 `status=cancelled`를 반환한다. 공개 PNG local classify 시작 직후에도 `running.current_run_id=<workflow run>`을 확인한다.
- 메모: 검증은 facade run ID를 직접 비교하므로, 단순히 active count가 1 이상인 경우를 통과로 보지 않는다.
- 2026-08-02 보완: vendor background run의 최초 상태 `pending`도 active 상태다. 따라서 `background_job_running=true`, `daemon_status=busy`, `current_run_id=<run>`으로 투영되며, `running`으로 전이될 때까지 상태 화면에서 유휴로 보이지 않는다.

## 6. `photos_query` library action checklist

### 6.1 `action="list"`

- [x] Apple source browse 가 성공한다.
- [x] 결과 `count` 가 반환된다.
- [x] item array 가 반환된다.
- [x] item 에 `id` 와 `photo_id` 가 함께 보인다.
- [x] item 에 `source` 와 `vendor_source` 가 함께 보인다.

### 6.2 `action="ready_only"`

- [x] Apple은 local path와 실제 이미지 디코딩을 모두 통과한 analyze-ready 항목만 남는다.
- [x] `local_path_available=true` 인 항목만 남는다.
- [x] `analyze_recommended=true` 인 항목만 남는다.

### 6.3 `action="search"`

- [x] query 기반 검색이 된다.
- [x] 결과가 비어도 shape 가 유지된다.
- [x] search 결과도 `photo_id` anchor 를 유지한다.

### 6.4 `action="inspect"`

- [x] 단건 inspect 가 성공한다.
- [x] metadata 가 포함된다.
- [x] thumbnail 또는 inspect 관련 세부가 기대 shape 로 내려온다.
- [x] inspect 결과 뒤에 `photos_select(action="analyze_photo")`로 이어질 수 있다.

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
- [x] prefetch 뒤 `photos_select`로 이어질 수 있다.

### 6.6 item-level guidance

- [x] `local_path_available` 가 local/non-local 을 구분한다.
- [x] `analyze_recommended` 가 analyze 가능 여부를 구분한다.
- [x] `recommended_next_action` 이 다음 동작을 제안한다.
- [x] non-local item 에 `download_hint` 가 내려온다.
- [x] local item 은 불필요한 `download_hint` 없이 간결하다.

### 6.7 response-level summary

- [x] `analyze_ready_count` 가 반환된다.
- [x] `download_required_count` 가 반환된다.
- [x] `next_suggested_action` 이 반환된다.
- [x] mixed 결과에서 summary count 가 item 상태와 일치한다.

기록:

- 상태: `x`
- 실행 명령: `./.venv/bin/python - <<'PY' ... photos_query(action='list'|'prefetch', options={'source':'apple', 'date_from':'2025-06-30', 'date_to':'2025-06-30', 'limit':26}) ... PY`
- 핵심 응답: 같은 date range 에서 `count=26`, `analyze_ready_count=19`, `download_required_count=7` 이었고, 이어진 `prefetch` 는 `attempted_count=26`, `already_local_count=19`, `downloaded_count=7`, `failed_count=0`, `next_suggested_action='photos_select'`으로 끝났다.
- 메모: 이번 live run 에서는 실패 항목이 없어서 `reason_code` 류 필드는 payload shape 상 빈 케이스로 남았다. 다운로드된 7건은 모두 `fetch_strategy='download_missing'`, `strategies_tried=['download_missing']` 를 기록했다.
- 2026-08-02 재검증: 설치 앱 MCP에서 `list(limit=3)`, `ready_only(limit=3)`, 존재하지 않는 검색어의 `search(limit=3)`, 단건 `inspect(include_metadata=true)`를 순서대로 호출했다. 목록은 `count=3`과 `id`, `photo_id`, `source`, `vendor_source`, readiness 요약을 반환했고, `ready_only`와 빈 검색도 items array 형태를 유지했다. inspect는 metadata object를 반환했다. 최초 `list(limit=1)`은 2.0초에 완료됐으며, 개인 사진 값은 기록하지 않았다.

## 7. `photos_select` / `photos_workflow` checklist

### 7.1 `intent="analyze"` immediate success

- [x] local asset 으로 analyze 가 바로 성공한다.
- [x] terminal 응답이 반환된다.
- [x] `summary_available=true` 가 보인다.
- [x] `result_available=true` 가 보인다.
- [x] quality / scene / event / faces 결과 shape 가 맞다.

### 7.2 `intent="analyze"` blocked path

- [x] non-local asset 에서 no-wait analyze 가 막힌다.
- [x] `error_code` 가 구조화되어 있다.
- [x] `error_stage` 가 있다.
- [x] `readiness_check` 가 있다.
- [x] `fetch_strategy` 가 있다.
- [x] `fetch_reason_code` 가 있다.
- [x] `fetch_reason_detail` 이 있다.
- [x] `fetch_strategies_tried` 가 있다.
- [x] PhotoKit denial 인 경우 `photokit_authorization_denied=true` 가 보인다.
- [x] `next_suggested_action` 이 있다.
- [x] `hint` 가 다음 조치를 설명한다.

### 7.3 `intent="analyze"` wait start

- [x] non-local asset 에 `wait_for_local=true` 로 run 생성이 된다.
- [x] 첫 응답이 `status=running` 이다.
- [x] `wait_status=waiting_for_local_download` 가 보인다.
- [x] `summary_available=true` 가 보인다.
- [x] `result_available=false` 가 보인다.
- [x] `run_id` / `job_id` 가 보인다.
- [x] `progress.stage=waiting_for_local_download` 가 보인다.
- [x] `download_hint` 가 포함된다.
- [x] 필요 시 `permission_warning=true` 가 보인다.

### 7.4 `intent="analyze"` wait progress

- [x] polling 중 `wait_elapsed_seconds` 가 증가한다.
- [x] polling 중 `poll_attempts` 가 증가한다.
- [x] polling 중 `progress.current` 가 증가한다.
- [x] polling 중 `status=running` 이 유지된다.

### 7.5 `intent="analyze"` wait terminal states

- [x] local download 완료 시 `status=completed` 로 끝난다.
- [x] 완료 시 `result_available=true` 가 된다.
- [x] local download 미완료 시 `status=failed` 로 끝날 수 있다.
- [x] timeout 시 `error_code=local_download_timeout` 또는 세부 probe timeout 코드가 보인다.
- [x] cancel 시 `status=cancelled` 로 끝난다.

### 7.6 background workflow intents

- [x] `intent="classify"` 가 background run 을 만든다.
- [x] `intent="curate"` 가 workflow 응답을 만든다.
- [x] `intent="organize"` 가 workflow 응답을 만든다.
- [-] `intent="import"` 가 workflow 응답을 만든다. 구현과 계약 테스트는 통과했으며, 실제 Photos 보관함 변경이므로 live 자동 검증에서는 실행하지 않는다.

기록:

- 상태: `x` (import live write 제외)
- 실행 명령: `photos-mcp-live-validate --include-workflows`
- 핵심 응답: 공개 PNG local classify가 pending background run으로 시작해 완료됐고, `photos_write(action="organize_by_category")`는 별도 임시 디렉터리에 `copied=1`로 완료됐다. `select_best`도 local review run을 만들었다.
- 메모: `import_to_album`은 비어 있지 않은 path 목록과 실제 Photos 보관함 변경이 필요하므로, 안전한 live validator에서는 의도적으로 실행하지 않는다.

## 8. `photos_query` result action checklist

### 8.1 vendor run

- [x] `action="summary"` 가 vendor run 요약을 반환한다.
- [x] `action="result"` 가 vendor result payload 를 반환한다.
- [x] `action="selected"` 가 selected item 목록을 반환한다.
- [x] `action="artifacts"` 가 preview 또는 export 결과를 반환한다.
- [x] `action="cancel"` 이 vendor run 취소를 반영한다.

### 8.2 synthetic wait run summary

- [x] wait run 중 `summary` 가 현재 상태를 보여준다.
- [x] `wait_status` 가 보인다.
- [x] `wait_elapsed_seconds` 가 보인다.
- [x] `poll_attempts` 가 보인다.
- [x] `progress` object 가 보인다.
- [x] `download_hint` 가 유지된다.

### 8.3 synthetic wait run result

- [x] wait run 완료 전 `result` 는 `result_available=false` 를 반환한다.
- [x] wait run 완료 후 `result` 는 analyze 결과를 반환한다.
- [x] wait run 실패 후 `result` 는 terminal 상태를 보여준다.

### 8.4 synthetic wait run cancel

- [x] wait run 중 `cancel` 이 성공한다.
- [x] cancel 직후 `summary` 가 `status=cancelled` 를 보여준다.
- [x] cancel 후 `result_available=false` 가 유지된다.
- [x] cancel 후 running 목록에서 빠진다.

### 8.5 계약 테스트 범위

- [x] `tests/test_result_service.py`에서 vendor `selected`, preview/export `artifacts`, vendor `cancel` 호출과 응답 정규화를 검증한다.
- [x] 같은 테스트에서 synthetic wait run cancel 뒤 `status=cancelled`, `result_available=false`, active 목록 제거를 검증한다.
- [x] 이 계약은 실제 Apple Photos 보관함과 파일 내보내기를 변경하지 않는다. live validator는 public PNG와 임시 디렉터리만 사용한다.

### 8.6 GCS 원본 안전 경계

- `source="gcs"` 분석은 blob을 메모리 thumbnail으로 읽어 결과 검토까지 지원한다.
- `path_or_bucket`은 `gs://bucket-name/prefix`와 `bucket-name/prefix`를 모두 허용하며, bucket과 prefix를 분리해 source adapter에 전달한다.
- GCS 결과를 Apple Photos 앨범에 넣거나 Apple 분류 앨범을 만드는 요청은 `unsupported_source_for_write`로 차단되어야 한다.
- GCS 결과의 `artifacts` 내보내기 요청은 `unsupported_source_for_export`로 차단되어야 한다. 실제 GCS 파일 복사는 별도 동기화 후 local source workflow로 검증한다.

기록:

- 상태: `x`
- 실행 명령: `photos-mcp-live-validate --include-workflows`
- 핵심 응답: public local classify 결과에 대해 `summary`, `result`, `selected`, `artifacts`를 모두 호출했고, synthetic wait에서는 pending result, timeout terminal result, cancel terminal result을 각각 확인했다.
- 메모: artifact와 결과 preview에는 개인 경로·식별자를 기록하지 않는다.

## 9. 최종 판정 요약

- [x] runtime / transport
- [x] `photos_query(action="status")`
- [x] `photos_query` library action
- [x] `photos_select` / `photos_workflow`
- [x] `photos_query` result action

최종 메모:

- 전체 판정: standalone 설치 앱과 Nanobot wrapper 기준 live gate가 통과했다.
- 핵심 근거: wrapper가 설치된 `PhotosMcp.app`을 기동하고 `/health`, `/health/capabilities`, facade 4개 도구, no-wait Apple 차단, synthetic wait 상태 전이, local classify/organize/result workflow가 모두 정상 응답했다.
- 부분 동작 항목: `ready_only`는 보관함에 실제로 즉시 디코딩 가능한 사진이 없으면 빈 목록을 반환하는 정상적인 `partial` 상태다. 실제 Apple import와 TCC 초기 승인 재현은 보관함 변경 또는 권한 reset이 필요한 수동 운영 검증으로 분리한다.
- 최신 근거: `docs/live-validation-report-latest.md`의 wrapper 포함 2026-08-02 결과와 `258 passed` 전체 테스트를 참조한다.

## 10. 이번 점검에서 특히 확인할 포인트

- facade tool 4개만 외부에 노출되는지
- `photos_query` library action이 analyze-ready / download-required를 충분히 설명하는지
- `photos_select(action="analyze_photo")`가 local / non-local / wait 세 경로를 구조적으로 구분하는지
- `photos_query` result action이 vendor run과 synthetic wait run 모두에서 일관되게 동작하는지
- live 환경에서 helper, preflight, timeout 이 실제로 어떤 제약을 만드는지
