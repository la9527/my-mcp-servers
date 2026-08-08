# PhotosMcp Streamable HTTP Integration Implementation Phase 1

> 2026-05-19 기준 이 문서는 기존 `Nanobot/docs/planning/execution-backlog/18-photos-mcp-app-implementation-phase1.md` 에서 `photos-mcp/docs/planning/` 으로 이동한 canonical implementation plan 문서다.

이 문서는 redesign 문서를 실제 구현 작업으로 옮기기 위한 phase-1 구현 계획 문서다. 핵심 목표는 `PhotosMcp.app` 을 user-launched localhost MCP daemon 으로 만들고, app 안에 menu bar status item 기반 상태 UI와 start/stop/quit UX 를 두며, `nanobot` 은 그 app 의 client 로만 동작하게 바꾸는 것이다.

## 한 줄 목표

`PhotosMcp.app` 이 `127.0.0.1:18791` 에서 `streamable HTTP` MCP server 를 제공하고, menu bar menu window 에서 상태/시작/중지/종료 UI 를 제공하며, `nanobot` 은 `type: streamableHttp` client 설정만 바꾸는 수준으로 연결한다.

## phase-1 산출물

1. `PhotosMcp.app` manual-launch daemon contract
2. localhost `streamable HTTP` MCP endpoint
3. app menu bar status item and menu window
4. `/health` endpoint
5. app-owned stale job repair
6. `nanobot` MCP config 의 stdio 제거
7. structured job envelope 와 completed-state 확인 contract
8. `photos-mcp` artifact ignore/cleanup 정리
9. 문서와 live migration 절차

## 변경 대상 축

### A. PhotosMcp app repo

주요 작업:

- app startup 경로 재정의
- stdio entrypoint 에서 HTTP server entrypoint 로 이동
- host/port/config 계약 추가
- readiness/health 상태 추가
- menu bar status item / start-stop / quit / background job indicator 추가
- graceful shutdown 및 single-instance ownership 재검토

예상 touched area:

- `photos_mcp/main.py`
- `photos_mcp/server.py`
- `photos_mcp/config.py`
- app packaging script / README

### B. shared photo logic

주요 작업:

- 현재 MCP tool surface 를 HTTP transport 위에서도 유지
- long-running job state 를 request lifecycle 과 분리
- stale job repair 를 app startup 단계로 이동 또는 강화
- existing `list_jobs`, `get_job_status`, `get_job_summary`, `get_job_result`, `cancel_job` surface 를 unified server 기준으로 재정렬
- job 생성형 응답에 terminal/completed 판별용 공통 envelope 추가

예상 touched area:

- unified tool registration
- job runtime/persistence code
- Apple Photos access adapter

### D. repo hygiene

주요 작업:

- `photos-mcp/.gitignore` 추가
- generated artifact cleanup
- build 결과물과 source 입력물 분리 원칙 문서화

예상 touched area:

- `photos-mcp/.gitignore`
- `photos-mcp/README.md`
- top-level generated directories

### C. Nanobot client side

주요 작업:

- live config 를 `streamableHttp` 로 전환
- wrapper/command execution 경로 제거
- app-specific health orchestration 추가 없이 config-only cutover 유지

예상 touched area:

- live `~/.nanobot/config.json`
- Nanobot docs

## 구현 작업 묶음

### Task Group 1: Daemon contract 와 menu UI contract 고정

목적:

- app 이 manual-launch daemon 으로서 어떤 포트, 어떤 endpoint, 어떤 start/stop/quit 규칙과 menu window 상태를 가지는지 확정

완료 조건:

- `GET /health` 와 `POST/stream /mcp` 의 contract 가 문서화됨
- menu window 에 어떤 상태와 action 이 보일지 문서화됨
- host/port/path 기본값이 코드와 문서에서 일치함

### Task Group 2: PhotosMcp HTTP server 추가

목적:

- 기존 stdio MCP server 대신 streamable HTTP server 를 app 내부에 붙임

완료 조건:

- app 실행 후 localhost bind 성공
- MCP initialize / list_tools 가 HTTP transport 로 동작

### Task Group 3: app-owned status UI 추가

목적:

- 사용자가 app 자체에서 현재 server 상태와 background job 실행 여부를 확인하고 app 을 종료할 수 있게 함

완료 조건:

- menu bar status item 이 항상 접근 가능함
- `stopped`, `starting`, `ready`, `busy`, `degraded`, `stopping` 상태가 구분돼 보임
- background `photo-ranker` job 이 `idle/running` 으로 표시됨
- active/completed/failed job summary 가 보임
- `Start`, `Stop`, `Quit` action 이 각각 올바른 lifecycle 의미를 가짐

menu window 상세 기준:

- phase 1 UI 는 regular window 가 아니라 menu bar status item + menu window 를 기본값으로 둔다.
- status item 클릭으로 menu window/popover 를 연다.
- `Start` 는 daemon 을 올리고, `Stop` 은 daemon 만 내리며, `Quit` 은 app 전체를 종료한다.
- 상단 summary 영역에는 server badge, endpoint, readiness 를 둔다.
- 중간에는 server action 영역과 active jobs list 를 둔다.
- 하단에는 recent terminal jobs list 를 둔다.

job row 최소 표시 항목:

- `job_id`
- tool/action name
- source 또는 request kind
- status badge
- progress 표시
- `started_at`
- terminal 상태일 때 `finished_at`
- `result_available` / `summary_available`
- failed 상태일 때 짧은 error summary

### Task Group 4: structured job management 와 completion contract 정리

목적:

- app UI 와 MCP 응답이 같은 job state 를 읽고, 클라이언트가 job 완료 여부를 빠르게 확인할 수 있게 함

완료 조건:

- `list_jobs`, `get_job_status`, `get_job_summary`, `get_job_result`, `cancel_job` 재사용 경로가 정리됨
- job 생성형 tool 응답이 `job_id`, `status`, `terminal`, `finished_at`, `result_available`, `summary_available` 를 일관되게 포함함
- completed/failed 여부가 app UI 와 MCP summary 에서 같은 값으로 보임
- active list 와 recent terminal list 로의 이동 규칙이 문서화됨
- `stopped` 상태에서도 recent terminal job 확인이 가능함

active/recent 분류 규칙:

- `pending`, `running` 은 active jobs 영역에 표시
- `completed`, `failed`, `cancelled` 는 recent terminal jobs 영역에 표시
- status 변경 시 같은 source of truth 에서 두 영역 간 이동한다
- recent terminal list 는 최신순 정렬을 기본값으로 둔다

### Task Group 5: app-owned progress 와 stale-job repair 정리

목적:

- 연결이 끊겨도 app 내부 job state 가 유지되도록 정리

완료 조건:

- organize/classify job 상태를 app restart 이후에도 일관되게 복구
- status/list job tool 이 app runtime state 를 기준으로 동작

### Task Group 6: Nanobot client cutover

목적:

- `stdio` 기반 MCP 등록 제거
- `streamableHttp` config 로 단일 `photos-mcp` 연결 전환
- app-specific health polling 이나 상태 UI 를 Nanobot 에 추가하지 않음

완료 조건:

- gateway restart 후 child `PhotosMcp` process 가 없어짐
- `nanobot` 은 HTTP client 연결만 수행
- source/runtime 구조 변경 없이 config 중심으로 수렴함

### Task Group 7: app-owned health/readiness surface 정리

목적:

- app 내부 self-check 결과를 UI 와 diagnostic endpoint 에 일관되게 반영

완료 조건:

- `/health` payload 가 app 상태와 일치함
- app UI 와 diagnostic surface 가 같은 source of truth 를 사용함

### Task Group 8: repo hygiene 와 artifact cleanup

목적:

- `photos-mcp` 작업 디렉터리의 generated output 이 git noise 가 되지 않도록 정리

완료 조건:

- build/dist/venv/framework cache/egg-info/__pycache__ 가 `.gitignore` 로 제외됨
- 현재 불필요 generated directory 가 cleanup 됨
- source 입력물과 재생성 artifact 의 경계가 README 또는 운영 문서에 남음

### Task Group 9: packaging/install/operator docs

목적:

- manual-launch app model 과 live 운영 절차를 문서화

완료 조건:

- build/install/run/status/quit/runbook 이 문서화됨
- job completion 확인 경로와 artifact cleanup 원칙이 문서화됨

## 검증 순서

### 1. local app gate

- app launch 성공
- `curl http://127.0.0.1:18791/health`
- MCP HTTP initialize / list_tools 성공
- app UI 에서 `ready` 상태와 bind 정보 표시
- job 생성형 MCP 응답에 completion 판단 필드가 포함되는지 확인

### 2. app UI gate

- menu window 에서 `stopped/starting/ready/busy/degraded/stopping` 이 구분되는지 확인
- background `photo-ranker` job 실행 중 indicator 가 `running` 으로 바뀌는지 확인
- 최근 completed/failed job 이 상태창에 반영되는지 확인
- `Start`, `Stop`, `Quit` action 이 각각 올바르게 동작하는지 확인
- active/recent job row 에 표시되는 필드가 job envelope 와 일치하는지 확인

### 3. live integration gate

- gateway health OK
- app health OK
- WebUI Photos request 가 HTTP transport 로 시작
- progress/status tool 이 유지됨
- app UI 와 실제 job 실행 상태가 일치함
- job 완료 후 MCP summary/result surface 와 app UI 표시가 일치함

### 4. TCC regression gate

- 이전처럼 popup 이 재현되는 요청을 다시 실행
- TCC attribution 상 `responsible` 주체가 어떻게 바뀌는지 비교

## rollout 순서

1. 문서와 contract 먼저 확정
2. local prototype 에서 HTTP transport 성공
3. local prototype 에서 app menu UI/status/start-stop-quit 확인
4. local prototype 에서 structured job envelope 확인
5. `photos-mcp` generated artifact cleanup
6. live config 를 temporary branch/config 에 반영
7. Photos organize 실검증
8. 기존 stdio 경로 제거 또는 legacy fallback 으로만 남김

## rollback 전략

phase-1 rollback 은 transport 수준에서만 허용한다.

- app packaging 결과를 유지한 채 live `nanobot` config 만 이전 MCP 연결로 되돌릴 수 있어야 한다.
- 다만 이번 redesign 의 목적상 rollback 은 일시적인 운영 보호 수단일 뿐, 목표 구조는 아니다.

## 의사결정 체크포인트

구현 전에 아래 항목이 다시 확인돼야 한다.

1. port/path 기본값
2. status surface (`/health` 외 별도 `/status` 필요 여부)
3. single-instance app 와 HTTP bind ownership 관계
4. `nanobot` source 수정 범위를 config-only 로 묶을 수 있는지
5. existing tool names 와 job status schema 유지 범위
6. job 완료 신호를 tool 응답 envelope 로 충분히 줄지, 별도 app-local event surface 가 필요한지

## phase-1 완료 기준

- user-launched `PhotosMcp.app` 이 localhost MCP daemon 으로 동작한다.
- app UI 에서 상태, start/stop, background job, 종료 action 을 확인할 수 있다.
- app UI 가 menu bar menu window 기준으로 active jobs / recent terminal jobs 를 구분해 표시한다.
- job 완료 여부를 MCP 응답과 app UI 에서 일관되게 확인할 수 있다.
- `nanobot` 은 PhotosMcp 를 child process 로 실행하지 않는다.
- generated artifact 가 git 추적 대상에서 제외된다.
- live Photos request 가 HTTP transport 기준으로 검증된다.
- TCC attribution 재검증 절차가 계획에 포함된다.
