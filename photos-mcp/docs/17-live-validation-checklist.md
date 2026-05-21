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

- [ ] standalone build 가 성공한다.
- [ ] 설치본 bundle 이 최신 build 와 일치한다.
- [ ] wrapper launch 가 성공한다.
- [ ] `/health` 가 응답한다.
- [ ] `/health/capabilities` 가 응답한다.
- [ ] MCP endpoint initialize 가 성공한다.
- [ ] 외부 tool 목록이 4개 facade tool 로만 보인다.

### 3.2 facade tool

- [ ] `photos_status` 기본 조회가 된다.
- [ ] `photos_library` browse/inspect 계열이 된다.
- [ ] `photos_run` analyze 계열이 된다.
- [ ] `photos_run` background workflow 계열이 된다.
- [ ] `photos_result` vendor run 조회가 된다.
- [ ] `photos_result` synthetic wait run 조회가 된다.

## 4. runtime / transport checklist

### 4.1 build / install

- [ ] `./scripts/build_framework_standalone.sh` 가 정상 종료된다.
- [ ] `dist-framework-standalone/PhotosMcp.app` 가 생성된다.
- [ ] `~/Applications/PhotosMcp.app` 설치본이 갱신된다.
- [ ] 설치본 `codesign --verify --deep --strict` 가 통과한다.
- [ ] 설치본 `Contents/MacOS/PhotosMcp --health` 가 응답한다.

기록:

- 상태:
- 실행 명령:
- 핵심 응답:
- 메모:

### 4.2 wrapper / launch

- [ ] `run-photos-mcp-app.sh` 가 background launch 메시지를 출력한다.
- [ ] launcher log 경로가 생성된다.
- [ ] launch 후 health ready 를 기다린다.
- [ ] 기존 프로세스와 충돌하지 않는다.

기록:

- 상태:
- 실행 명령:
- 핵심 응답:
- 메모:

### 4.3 health / capabilities

- [ ] `/health` top-level `status=ok`
- [ ] `/health` 에 `daemon_status` 가 포함된다.
- [ ] `/health` 에 `preflight_status` 가 포함된다.
- [ ] `/health` 에 `active_jobs`, `recent_jobs` 요약이 포함된다.
- [ ] `/health/capabilities` 또는 `capabilities` field 에 checks 목록이 들어간다.
- [ ] `photos_read` check 상태를 확인할 수 있다.
- [ ] `photos_automation` check 상태를 확인할 수 있다.
- [ ] `photos_thumbnail` check 상태를 확인할 수 있다.

기록:

- 상태:
- 실행 명령:
- 핵심 응답:
- 메모:

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

### 6.5 item-level guidance

- [ ] `local_path_available` 가 local/non-local 을 구분한다.
- [ ] `analyze_recommended` 가 analyze 가능 여부를 구분한다.
- [ ] `recommended_next_action` 이 다음 동작을 제안한다.
- [ ] non-local item 에 `download_hint` 가 내려온다.
- [ ] local item 은 불필요한 `download_hint` 없이 간결하다.

### 6.6 response-level summary

- [ ] `analyze_ready_count` 가 반환된다.
- [ ] `download_required_count` 가 반환된다.
- [ ] `next_suggested_action` 이 반환된다.
- [ ] mixed 결과에서 summary count 가 item 상태와 일치한다.

기록:

- 상태:
- 실행 명령:
- 핵심 응답:
- 메모:

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
- [ ] `intent="curate"` 가 workflow 응답을 만든다.
- [ ] `intent="organize"` 가 workflow 응답을 만든다.
- [ ] `intent="import"` 가 workflow 응답을 만든다.

기록:

- 상태:
- 실행 명령:
- 핵심 응답:
- 메모:

## 8. `photos_result` checklist

### 8.1 vendor run

- [ ] `action="summary"` 가 vendor run 요약을 반환한다.
- [ ] `action="result"` 가 vendor result payload 를 반환한다.
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

- 상태:
- 실행 명령:
- 핵심 응답:
- 메모:

## 9. 최종 판정 요약

- [ ] runtime / transport
- [ ] `photos_status`
- [ ] `photos_library`
- [ ] `photos_run`
- [ ] `photos_result`

최종 메모:

- 전체 판정:
- 핵심 blocker:
- 부분 동작 항목:
- 후속 수정 필요 항목:

## 10. 이번 점검에서 특히 확인할 포인트

- facade tool 4개만 외부에 노출되는지
- `photos_library` 가 analyze-ready / download-required 를 충분히 설명하는지
- `photos_run(intent="analyze")` 가 local / non-local / wait 세 경로를 구조적으로 구분하는지
- `photos_result` 가 vendor run 과 synthetic wait run 모두에서 일관되게 동작하는지
- live 환경에서 helper, preflight, timeout 이 실제로 어떤 제약을 만드는지