# PhotosMcp App Streamable HTTP Daemon Redesign Phase 1

> 2026-05-19 기준 이 문서는 기존 `Nanobot/docs/planning/execution-backlog/17-photos-mcp-app-python-packaging-phase1.md` 에서 `photos-mcp/docs/planning/` 으로 이동한 canonical planning 문서다.

## 왜 다시 구조를 바꾸나

직전 구조는 `PhotosMcp.app` 을 만들었지만, 실제 연결 방식은 여전히 `nanobot gateway` 가 `PhotosMcp` 를 stdio child 로 띄우는 형태였다. live 재현 결과, 이 방식에서는 app binary 가 분리돼 있어도 macOS TCC attribution 의 `responsible` 주체가 계속 Homebrew `python3.14` 로 남았다. 즉 bundle packaging 만으로는 이번 문제의 핵심인 "권한 책임 주체 분리"가 해결되지 않았다.

이번 문서는 전제를 바꾼다. `PhotosMcp.app` 은 사용자가 먼저 실행하는 독립 daemon-like app 이고, `nanobot` 은 그 앱이 여는 localhost MCP endpoint 에 client 로만 붙는다. 이 재정의의 목적은 아래 두 가지다.

- Apple Photos 접근의 운영 주체를 `PhotosMcp.app` 으로 더 강하게 고정한다.
- `nanobot` 이 PhotosMcp lifecycle 을 소유하지 않도록 하여 TCC 책임 경로를 단순화한다.

## 목표

1. `PhotosMcp.app` 을 수동 실행 기반의 독립 서버 app 으로 재정의한다.
2. app 내부 transport 는 stdio 가 아니라 `streamable HTTP` 로 고정한다.
3. `nanobot` 은 더 이상 `command/args` 로 app 을 실행하지 않고, localhost endpoint 에만 연결한다.
4. app UI 안에서 현재 server 상태, readiness, background `photo-ranker` 실행 여부, 종료 동작을 확인할 수 있게 한다.
5. `photo-source`, `photo-ranker`, unified `photos-mcp` tool surface 는 최대한 유지하되, 실제 tool/job ownership 은 app 쪽으로 수렴시킨다.
6. progress, health, stale-job repair, runtime/cache 경로의 소유권을 app 기준으로 다시 정리한다.
7. `nanobot` 쪽은 app 전용 health orchestration 을 추가하지 않고, MCP client 연결 설정 변경만으로 수렴시킨다.
8. job 생성형 MCP 응답이 `job_id`, `status`, terminal 여부, 완료 시각, follow-up summary/result 경로를 공통 envelope 로 제공하도록 정리한다.
9. `photos-mcp` 작업 디렉터리의 build/venv/dist/cache artifact 는 git 추적 대상에서 제외한다.

## 비범위

- Swift/XPC native reimplementation
- App Store sandbox / entitlement 재설계
- WebUI 전용 PhotosMcp settings 화면 추가
- full auto-launch fallback
- notarization / auto-update 완성

## phase-1 고정 결정

이번 문서의 확정 전제는 아래와 같다.

- app 이름: `PhotosMcp.app`
- executable 이름: `PhotosMcp`
- bundle identifier: `com.nanobot.photos-mcp`
- packaging 기본 경로: `py2app`
- server bind: `127.0.0.1:18791`
- MCP endpoint: `http://127.0.0.1:18791/mcp`
- health endpoint: `http://127.0.0.1:18791/health`
- transport: `streamable HTTP`
- launch ownership: user manual launch
- app UI: menu bar status item + menu window/popover + start/stop + quit
- nanobot health probe: 없음
- nanobot auto-launch: 금지

즉 phase 1 에서는 app 이 항상 먼저 떠 있어야 하며, `nanobot` 은 app 이 제공하는 MCP HTTP server 에만 연결한다.

## 권장 운영 모델

### 1. PhotosMcp app lifecycle

사용자는 Finder, Dock, Applications, 또는 launchd/로그인 아이템 성격의 사용자 영역 자동 시작 방식으로 `PhotosMcp.app` 을 실행한다. phase 1 에서는 "사용자가 app 을 켠다" 를 기본 모델로 둔다.

앱이 뜨면 내부적으로 다음을 수행한다.

- packaged Python runtime bootstrap
- runtime/cache/config path resolve
- stale job repair
- Apple Photos 접근 준비
- menu bar status item 등록
- localhost 전용 HTTP server 시작 또는 operator 선택 상태 반영
- app menu window/popover 준비

phase 1 UI 기본값은 아래로 둔다.

- menu bar 아이콘에서 현재 server 상태를 간접적으로 드러낸다.
- 클릭 시 menu window/popover 에서 server 상태, start/stop action, 최근 job 상태를 보여준다.
- 현재 background `photo-ranker` job 이 `idle` 인지 `running` 인지 표시한다.
- 최근 active job 목록과 마지막 completed/failed job 요약 표시한다.
- 명시적 `Start`, `Stop`, `Quit` action 을 둔다.

### 1-1. menu bar menu window 기준 UI 결정

phase 1 에서는 status surface 를 regular window 가 아니라 macOS menu bar status item + menu window/popover 로 고정한다.

이 결정의 이유는 아래와 같다.

- app 이 daemon/utility 성격이라는 점을 macOS 상단 우측 UX 와 더 잘 맞출 수 있다.
- 사용자는 Tailscale 류 menubar 유틸리티처럼 빠르게 상태 확인과 종료를 할 수 있다.
- 항상 큰 창을 띄우지 않고도 start/stop, 상태, 최근 job 을 확인하기 쉽다.
- 필요하면 phase 2 에서 별도 상세 window 를 추가해도 baseline contract 를 유지할 수 있다.

phase 1 menu 동작 기본값:

- app launch 시 menu bar status item 이 나타난다.
- status item 클릭 시 menu window/popover 가 열린다.
- `Stop` 은 app process 종료가 아니라 MCP daemon 만 내린다.
- `Quit` 은 status item 과 app process 전체를 종료한다.
- phase 1 에서는 Dock foreground app 보다는 menubar utility 형태를 우선한다.

### 1-2. menu window 정보 구조

menu window/popover 는 최소한 아래 4개 영역으로 나눈다.

1. 상단 server summary 영역
   - app 이름
   - server 상태 badge: `stopped`, `starting`, `ready`, `busy`, `degraded`, `stopping`
   - bind endpoint: `127.0.0.1:18791/mcp`
   - 마지막 상태 갱신 시각

2. server action 영역
   - `Start` 또는 `Stop` action
   - 현재 상태에 따라 한 번에 하나의 주 action 만 활성화
   - 선택적으로 `Open Logs` 또는 `Open Details` 는 phase 2 후보로 남긴다

3. middle active jobs 영역
   - 현재 running 또는 pending job 목록
   - 각 row 는 `job_id`, tool/action name, source, progress, started_at, 현재 status badge 표시
   - running job row 에는 필요 시 `Cancel` action 을 둘 수 있게 계약만 열어 둔다

4. 하단 recent terminal jobs 영역
   - 최근 `completed`, `failed`, `cancelled` job 목록
   - 각 row 는 `job_id`, finished_at, terminal status, result/summary availability 를 표시
   - 마지막 실패 job 은 error summary 한 줄을 함께 표시할 수 있다

menu footer action:

- `Settings...` 또는 `Open Details` 는 phase 2 후보
- `Quit`

phase 1 에서는 `Hide` 보다 menu dismiss 가 기본 close semantics 이다.

### 2. nanobot lifecycle

`nanobot` 은 PhotosMcp 를 child 로 실행하지 않는다. phase 1 에서 `nanobot` 이 추가로 맡는 역할은 app 전용 health orchestration 이 아니라 MCP client 연결뿐이다.

즉 `nanobot` 쪽 변경은 아래 수준으로 제한한다.

1. `type: streamableHttp` client 설정 적용
2. 기존 stdio command 경로 제거
3. app 전용 status/health/poll UI 는 추가하지 않음

### 3. app-owned status UX

상태 확인과 운영 안내는 `nanobot` 이 아니라 `PhotosMcp.app` 화면이 우선 담당한다.

- app 이 현재 `starting`, `ready`, `busy`, `degraded` 중 어디인지 드러낸다.
- app 이 현재 `stopped`, `starting`, `ready`, `busy`, `degraded`, `stopping` 중 어디인지 드러낸다.
- background `photo-ranker` 가 실행 중이면 분명히 표시한다.
- 사용자가 daemon 을 직접 올리고 내릴 수 있도록 `Start` / `Stop` action 이 있어야 한다.
- app 전체를 닫고 싶은 경우 `Quit` action 이 있어야 한다.
- health/readiness 정보는 app 내부와 app UI 가 직접 소비한다.

예시 UI 항목 방향:

```text
PhotosMcp.app
Server: Ready
Endpoint: 127.0.0.1:18791/mcp

Action: [Stop]

Active Jobs
- classify_and_organize | job_20260518_001 | running | 43%

Recent Jobs
- job_20260518_000 | completed | result available
- job_20260517_014 | failed | source timeout

Actions: [Quit]
```

## 상위 아키텍처

### A. app 내부 계층

1. app launcher / runtime supervisor
   - app startup
   - bind port ownership
   - health/readiness 상태
   - graceful shutdown
   - menu bar state publish
   - daemon start/stop transition 관리

2. packaged Python runtime
   - `photo-source`, `photo-ranker`, shared dependency 포함
   - app-owned interpreter 만 사용

3. shared photos service layer
   - Apple Photos query
   - export/fetch helper
   - album writeback
   - permission-sensitive path 집중

4. shared runtime core
   - job DB
   - artifacts/cache
   - stale job repair
   - progress state

5. MCP HTTP layer
   - `streamable HTTP` server
   - `/mcp`
   - `/health`
   - optional `/status` or `/debug/config`

6. app status UI layer
   - 현재 server 상태 표시
   - background job indicator
   - active/completed job 목록 표시
   - 최근 job summary
   - menu bar item / popover lifecycle
   - start / stop / quit action

### B. client/server ownership

- `PhotosMcp.app`: server, progress owner, job owner, health owner, status UI owner
- `nanobot`: MCP client owner

이 ownership 분리가 이번 redesign 의 핵심이다.

## tool surface 원칙

phase 1 에서는 tool schema churn 을 최소화한다.

- unified external server id 는 `photos-mcp` 하나를 기본값으로 둔다.
- tool names 는 가능한 한 현행 contract 를 유지한다.
- app 내부 job state 를 읽는 status tool 들은 그대로 유지하거나 보강한다.
- 기존 `list_jobs`, `get_job_status`, `get_job_summary`, `get_job_result`, `cancel_job` surface 를 PhotosMcp 단일 server 에서 유지한다.

즉 transport 와 lifecycle 은 크게 바꾸되, agent prompt / workflow 가 기대하는 tool semantics 는 흔들지 않는 방향이다.

## progress 와 job 상태 설계

이번 구조에서는 long-running organize/classify 의 진행 상태를 transport 가 아니라 app-owned runtime state 로 다룬다.

원칙:

- request 수명보다 job 수명을 app 이 더 오래 소유할 수 있다.
- job progress 는 app runtime DB 또는 state store 에 기록한다.
- `nanobot` 은 tool response 와 follow-up status tool 로 상태를 읽는다.
- `nanobot` restart 와 분리해서 app 이 stale running job repair 를 수행한다.

추가 원칙:

- job 생성형 tool 은 공통 job envelope 를 반환한다.
- 공통 envelope 는 최소한 `job_id`, `status`, `terminal`, `finished_at`, `result_available`, `summary_available` 를 포함한다.
- 이미 완료형 작업은 첫 MCP 응답만으로 완료 여부를 판별할 수 있어야 한다.
- background 작업은 첫 응답에서 `terminal=false` 를 주고, 이후 `get_job_summary` 또는 `get_job_result` 로 이어진다.
- app UI 는 동일한 job state store 를 읽어 active/running/completed/failed 를 표시한다.
- app UI row 는 MCP envelope 와 같은 terminal 판정 규칙을 사용한다.
- daemon 이 `stopped` 상태여도 recent terminal jobs 는 마지막 known state 를 유지해 보여줄 수 있다.

job row 최소 표시 필드:

- `job_id`
- `request_kind` 또는 tool name
- `status`
- `progress.current` / `progress.total` 또는 progress label
- `started_at`
- terminal job 의 경우 `finished_at`
- `result_available`
- `summary_available`
- failed/cancelled 일 때 짧은 reason

효과:

- `nanobot` reconnect 와 무관하게 Photos job 상태를 계속 유지할 수 있다.
- streamable HTTP 연결이 재수립돼도 app 내부 progress 는 사라지지 않는다.
- MCP 응답만으로도 job 완료 여부를 더 빨리 판별할 수 있다.

## permission / TCC 관점의 기대 효과

이번 구조에서 기대하는 변화는 아래다.

- `nanobot` 이 더 이상 PhotosMcp process 의 parent/owner 역할을 하지 않는다.
- TCC attribution 상 `PhotosMcp.app` 의 책임 비중이 커질 가능성이 높다.
- 권한 prompt 라벨이 `python3.14` 대신 app 중심으로 수렴할 가능성이 생긴다.

주의:

- 이 문서는 "반드시 0 prompt" 를 약속하지 않는다.
- bundle id, signing identity, 권한 DB 상태에 따라 재프롬프트는 여전히 가능하다.
- 목표는 권한 책임 주체를 app 쪽으로 명확히 옮기고, `nanobot` Python 책임 노출을 줄이는 것이다.

## runtime / env contract

### app 기준 값

- `NANOBOT_PHOTOS_MCP_BUNDLE_PATH`
- `NANOBOT_PHOTOS_MCP_RUNTIME_ROOT`
- `NANOBOT_PHOTOS_MCP_CACHE_ROOT`
- `NANOBOT_PHOTOS_MCP_HOST`
- `NANOBOT_PHOTOS_MCP_PORT`

선택적으로 고려할 UI 관련 값:

- `NANOBOT_PHOTOS_MCP_SHOW_WINDOW_ON_LAUNCH`
- `NANOBOT_PHOTOS_MCP_ALLOW_HIDE_TO_BACKGROUND`

### 유지할 값

- `PHOTO_RANKER_VLM_*`
- `LOCAL_LLM_*`
- `OPENAI_API_KEY`
- Apple helper 관련 timeout/path 값

### 금지할 값

- `nanobot` 이 stdio child launch 를 위해 내부적으로만 쓰던 wrapper-specific 실행 경로 값

phase 1 이후에는 wrapper 가 app executable 을 직접 실행하는 역할을 가지지 않아야 한다.

## Nanobot integration 방향

### config 변경

기존:

- `type: stdio`
- `command: .../run-photos-mcp-app.sh`

새 구조:

- `type: streamableHttp`
- `url: http://127.0.0.1:18791/mcp`
- 필요 시 `tool_timeout` 보정

### 상태 확인 처리

phase 1 에서는 app readiness 와 health 표시는 `PhotosMcp.app` 이 직접 담당한다.

- app 내부 self-check 결과를 window/status UI 에 반영
- 필요 시 `/health` 는 operator/debug 용으로 유지
- `nanobot` 은 app 상태를 알기 위해 별도 health polling 을 추가하지 않음

## migration 단계

### phase 1

- app 에 `streamable HTTP` server 추가
- manual-launch app model 정리
- app menu bar status item / start-stop / quit / background job indicator 추가
- structured job envelope 와 active/completed job 표시 추가
- `nanobot` client config 를 HTTP transport 로 전환
- `photos-mcp` artifact 디렉터리 `.gitignore` 와 cleanup 원칙 정리
- live validation: popup 책임 주체, health, progress, stale-job repair, UI 상태 동기화

### phase 2

- launch-at-login 또는 user-space auto-start 옵션 문서화
- status/quit/debug surface 개선
- app health/status 표시 보강

### phase 3

- notarization/signing 안정화
- optional lightweight status UI 고도화

## 검증 기준

### local

1. `PhotosMcp.app` 실행 후 `GET /health` 가 OK 반환
2. `streamable HTTP` MCP initialize / list_tools 성공
3. menu window 가 `ready` 상태와 bind 정보, 현재 job 상태를 올바르게 표시
4. background `photo-ranker` job 실행 중 indicator 가 `running` 으로 바뀌고 종료 후 `idle` 로 돌아옴
5. job 생성형 MCP 응답에서 `terminal` 및 완료 여부 판단에 필요한 필드가 일관되게 반환됨
6. menu window 의 active/recent job 목록이 MCP summary/result availability 와 일치함
7. `Start` 와 `Stop` action 이 daemon 상태를 의도대로 전환함

### live

1. WebUI fresh thread 에서 Apple Photos organize 요청이 app 연결 기반으로 시작
2. app restart 와 `nanobot` restart 를 분리해도 stale job repair 가 app 쪽에서 유지
3. organize 요청 중 app UI 에서 background job 상태와 최근 job summary 가 반영되는지 확인
4. 최근 재현 팝업과 같은 시점에 TCC attribution 의 `responsible` 주체가 app 중심으로 이동하는지 재확인
5. completed/failed job 이 app UI 와 MCP summary surface 에서 같은 상태로 보이는지 확인

## 리스크와 완화

### 1. app manual launch 누락

완화:

- guided retry
- app status window 기본 표시
- operator guide 문서화

### 2. HTTP transport 추가로 코드 경계가 넓어짐

완화:

- bind localhost only
- endpoint 최소화 (`/health`, `/mcp`)
- phase 1 에는 인증보다 localhost confinement 를 우선

### 3. tool progress 와 HTTP disconnect 관계 불명확

완화:

- app-owned job state 고정
- existing status tool 을 우선 재사용

### 4. UI 상태와 실제 runtime state 불일치

완화:

- status UI 는 app 내부 runtime state store 만 읽는다.
- server bind 상태와 background job 상태를 하나의 source of truth 에서 계산한다.

### 5. generated artifact 가 repo 에 남아 working tree noise 를 만드는 문제

완화:

- `photos-mcp/.gitignore` 로 build/dist/venv/framework cache/egg-info 를 제외한다.
- 현재 생성된 불필요 artifact 는 cleanup 후 재생성 가능한 것으로만 관리한다.

## 완료 기준

- `PhotosMcp.app` 이 stdio child 가 아니라 user-launched streamable HTTP server 로 문서화된다.
- `nanobot` 은 app client 로만 동작하는 구조로 재정의된다.
- app UI 에 status, start/stop, quit, background job indicator 가 포함된다.
- app UI 가 menu bar menu window 기준으로 정보 구조와 row 필드를 명확히 가진다.
- structured job envelope 와 completed 여부 확인 구조가 계획에 포함된다.
- generated artifact ignore/cleanup 원칙이 계획에 포함된다.
- phase 1 검증 기준에 health, progress, stale-job repair, UI 상태, TCC attribution 재확인이 포함된다.
