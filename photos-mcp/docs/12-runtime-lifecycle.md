# photos-mcp runtime lifecycle

이 문서는 `photos-mcp` 의 실제 runtime 제어 흐름과 세부 로직을 소스 기준으로 설명한다. 목표는 `PhotosMcp.app` 이 언제 어떤 상태로 바뀌고, 어떤 모듈이 source of truth 인지, 문제가 생기면 어느 계층을 봐야 하는지 빠르게 판단할 수 있게 하는 것이다.

## 1. runtime ownership 한 줄 요약

- app process ownership: `src/photos_mcp/main.py`, `src/photos_mcp/menu_app.py`
- HTTP daemon ownership: `src/photos_mcp/daemon.py`
- MCP routing/tool registration ownership: `src/photos_mcp/server.py`
- runtime/cache/logs path ownership: `src/photos_mcp/config.py`, `src/photos_mcp/runtime_paths.py`
- single-instance ownership: `src/photos_mcp/single_instance.py`
- UI/health projection ownership: `src/photos_mcp/state.py`
- persisted job source of truth: vendored `photo-ranker` DB/queue via `src/photos_mcp/job_state.py`
- preflight and permission semantics: `src/photos_mcp/preflight.py`
- source/bundle bootstrap and Terminal helper Python selection: `src/photos_mcp/runtime_bootstrap.py`

## 2. bootstrap 와 startup sequence

실제 startup 은 아래 순서로 진행된다.

1. `PhotosMcp.py` 또는 `photos-mcp` console script 가 `photos_mcp.main:main` 으로 들어간다.
2. `main.py` 는 import 초반에 `ensure_runtime_import_paths(__file__)` 를 호출해 source parent 또는 bundle Python root 를 `sys.path` 앞단에 올린다.
3. `run_cli()` 는 `load_config()` 로 endpoint, runtime root, cache root, bundle path, auto-start 여부를 확정한다.
4. `--health`, `--version`, `--help` 는 app event loop 와 daemon startup 없이 여기서 바로 종료한다.
5. 일반 실행은 `acquire_single_instance_lock()` 로 `runtime_root/photos-mcp.lock` 을 잡는다.
6. lock 확보 후 `PhotosMcpStateStore` 와 `PhotosMcpDaemonController` 를 만들고 `run_menu_app()` 로 AppKit event loop 를 시작한다.
7. `menu_app.py` 의 `install()` 이 status item, popover, refresh timer, startup timer 를 설치한다.
8. startup timer 가 `runStartupSequence_()` 를 한 번 호출하고, 여기서 preflight 를 먼저 실행한다.
9. `PHOTOS_MCP_START_DAEMON_ON_LAUNCH=true` 면 같은 startup sequence 안에서 daemon start 를 호출한다.
10. daemon bind 가 성공하면 state store 는 `ready` 로 바뀌고 job poller 가 활성화된다.

핵심 포인트:

- menu UI 가 먼저 올라오고 daemon 은 그 안의 startup sequence 에서 시작된다.
- 따라서 `--health` 성공, app launch 성공, daemon bind 성공은 서로 다른 단계다.
- startup 이후에도 `Start` / `Stop` 으로 daemon lifecycle 을 menu UI 에서 다시 제어할 수 있다.

## 3. single-instance 와 runtime root 계약

single-instance 는 `fcntl.flock()` 기반이다.

- lock 경로: `runtime_root/photos-mcp.lock`
- 기본 runtime root: `~/.photos-mcp/runtime`
- 기본 cache root: `~/.photos-mcp/cache`
- 기본 logs root: `~/.photos-mcp/logs`

lock file 에는 현재 PID 가 기록된다. 두 번째 프로세스가 lock 을 잡지 못하면 `AlreadyRunningError` 가 나고 exit code `75` 로 끝난다.

테스트/디버그 전용 우회:

- `PHOTOS_MCP_SINGLE_INSTANCE=0`: lock 사용 안 함

이 값은 운영 기본값이 아니다. live runtime 에서는 stale lock 여부를 먼저 확인하고, lock 자체를 끄는 방식은 마지막 수단으로만 쓴다.

## 4. daemon controller 상세 로직

`PhotosMcpDaemonController` 는 app process 안에서 HTTP daemon thread 와 job poller thread 를 함께 관리한다.

### 4.1 start

`start()` 의 실제 의미는 아래와 같다.

1. 이미 running 이면 `False` 반환
2. state store 를 `starting` 으로 변경
3. `build_server()` 와 `build_http_app()` 로 FastMCP app 구성
4. `uvicorn.Config` 를 `asyncio`, `h11`, `ws=none`, `lifespan=on` 기준으로 생성
5. 별도 daemon thread 에서 `asyncio.run(self._server.serve())` 실행
6. 최대 10초 동안 `self._server.started` 를 polling
7. 성공 시 `ready` 로 전환하고 `refresh_jobs_once()` + `_start_job_poller()`
8. 실패 시 `degraded`

### 4.2 stop

`stop()` 은 daemon thread 와 job poller 를 같이 내린다.

- running server 가 없으면 `stopped` 로 맞추고 `False` 반환
- running server 가 있으면 `stopping` 으로 전환
- poller stop
- `self._server.should_exit = True`
- server thread join 후 상태를 `stopped` 로 고정

### 4.3 degraded 의미

`degraded` 는 app process 가 완전히 죽은 상태가 아니라, daemon startup 또는 job refresh/cancel/delete/clear 중 예외가 발생해 runtime 이 신뢰할 수 없는 상태로 내려간 경우다.

운영상 의미:

- menu bar status item 은 계속 살아 있을 수 있다.
- `/health` 는 transport 기준 상태를 `degraded` 로 돌려줄 수 있다.
- 이 상태에서는 traceback 과 vendor import/bootstrap 문제를 먼저 봐야 한다.

## 5. state store 와 projection model

`PhotosMcpStateStore` 는 persisted DB 가 아니라 app-owned snapshot cache 다. MCP health payload 와 menu UI 가 모두 이 projection 을 읽는다.

### 5.1 status 분류

- daemon: `stopped`, `starting`, `ready`, `busy`, `degraded`, `stopping`
- preflight: `pending`, `ok`, `warning`, `error`
- active jobs: `pending`, `running`
- terminal jobs: `completed`, `failed`, `cancelled`

### 5.2 active / recent 계산 규칙

- active list: active status 인 job 만 포함
- recent list: terminal status 인 job 만 포함
- 정렬 기준: `finished_at`, 없으면 `started_at`, 없으면 `job_id`
- `background_job_running`: active jobs 중 `running` 이 하나라도 있으면 `True`

### 5.3 busy 자동 전환 규칙

`_sync_busy_state_locked()` 는 job snapshot 을 반영할 때 daemon status 를 다시 계산한다.

- 현재 status 가 `starting`, `stopping`, `degraded`, `stopped` 이면 덮어쓰지 않음
- running job 이 하나라도 있으면 `busy`
- 그 외에는 `ready`

즉 `busy` 는 별도 timer 나 외부 신호가 아니라, running job 존재 여부에서 계산되는 파생 상태다.

## 6. job adapter 와 source of truth 경계

`PhotoRankerJobStore` 는 vendored `photo-ranker` DB/queue 에 대한 adapter 다.

### 6.1 list_snapshots

- DB jobs 와 queue jobs 를 각각 `to_dict()` 로 읽는다.
- `{**db_jobs, **queue_jobs}` 병합을 쓰므로 같은 `job_id` 가 있으면 queue 쪽 payload 가 우선한다.
- 그 결과를 `job_snapshot_from_payload()` 로 정규화해 `JobSnapshot` 목록으로 바꾼다.

이 규칙은 queue 에서 더 최근 상태를 갖는 running/pending job 을 UI 쪽이 우선 보게 하려는 의도다.

### 6.2 cancel/delete/clear 의미

- `cancel_job(job_id)`: queue cancel 후 DB에도 저장해서 persisted state 를 따라가게 함
- `delete_terminal_job(job_id)`: terminal 상태일 때만 queue + DB 삭제 허용
- `clear_terminal_history(statuses)`: 기본값은 `completed`, `failed`, `cancelled` 전체

UI 상 의미:

- active job 은 `Stop` 으로 cancel 만 가능
- recent terminal jobs 만 개별 delete 가능
- Clear 는 recent terminal history 정리 의미다

## 7. MCP response normalization

`server.py` 는 vendored tool 응답을 그대로 통과시키지 않고, job payload 로 보이면 공통 envelope 로 보정한다.

보정 핵심:

- `id` 만 있으면 `job_id` 로 복사
- `request_kind` 가 없으면 tool name 으로 채움
- `terminal` 기본값은 status 기반으로 계산
- `finished_at`, `summary_available`, `result_available` 기본값 채움
- normalize 결과를 state store 에 즉시 반영

이 때문에 MCP response, `/health`, menu UI 가 같은 job 상태를 비교적 일관되게 보게 된다.

## 8. preflight 와 permission semantics

현재 startup preflight 는 네 개다.

- `photos_permission`: PhotosMcp.app 자체의 PhotoKit / `kTCCServicePhotos` 권한 상태와 startup prompt 가능 여부
- `photos_read`: Apple Photos library read 가능 여부
- `photos_automation`: Apple Photos album write/automation 가능 여부
- `photos_thumbnail`: analyze 에 필요한 thumbnail export 가능 여부

중요한 분리:

- `photos_permission` 은 PhotosMcp.app 자체가 Photos permission 을 받았는지 본다.
- `photos_automation` 은 Apple Events / Automation 경로를 본다.
- 둘은 같은 permission 이 아니다.

### 8.1 timeout model

각 check 는 daemon thread 를 막지 않기 위해 thread wrapper 로 실행되고 timeout 이 걸린다.

- timeout env: `NANOBOT_PHOTOS_MCP_PREFLIGHT_TIMEOUT_SECONDS`
- 기본값: `10`

주의:

- 이 timeout env 는 현재 legacy-style 이름만 구현돼 있다.
- `PHOTOS_MCP_*` 동등 이름은 아직 없다.

### 8.2 warning downgrade

automation check 는 아래 문자열이 보이면 fatal error 대신 `warning` 으로 낮춘다.

- `-1743`
- `not authorized to send apple events`
- `apple_events_permission_denied`
- `terminal.app`
- `automation`

즉 transport 는 정상인데 macOS Automation 권한만 대기 중인 상황을 UI 와 health 에서 구분하려는 설계다.

## 9. terminal helper 와 bootstrap contract

Apple Photos read/write 경로는 direct mode 와 terminal helper mode 를 둘 다 가진다.

### 9.1 Python 선택 규칙

`default_terminal_python()` 우선순위:

1. 명시적 env override
2. bundled app 실행 중이면 `Contents/MacOS/python`
3. 그 외에는 caller 디렉터리에서 상위로 올라가며 찾은 가장 가까운 `.venv/bin/python`
4. 그래도 못 찾으면 기존 fallback 으로 app dir 아래 `.venv/bin/python`

대표 env:

- `PHOTO_SOURCE_TERMINAL_PYTHON_BIN`
- `PHOTO_RANKER_TERMINAL_PYTHON_BIN`

### 9.2 mode env

- `PHOTO_SOURCE_APPLE_FETCH_MODE`: Apple Photos fetch path (`direct` / `terminal`)
- `PHOTO_RANKER_APPLE_FETCH_MODE`: `photo-ranker` 쪽 fetch helper mode
- `PHOTO_RANKER_APPLE_EVENTS_MODE`: album write / automation mode

현재 `terminal` mode 는 Terminal helper 를 우선 쓰지 않는다. app-owned `download_missing` / `download_missing_photokit` 를 먼저 시도하고, 둘 다 실패한 뒤 마지막 fallback 으로 Terminal helper 를 쓴다. 이 순서로 불필요한 Terminal 창 노출을 줄이고, PhotosMcp.app 권한만으로 해결되는 경우 helper 호출을 피한다.

terminal helper 를 실행할 때는 재귀 호출을 막기 위해 child env override 를 direct 로 되돌린다. 동시에 signed bundle 오염을 막기 위해 `PYTHONDONTWRITEBYTECODE=1` 도 강제로 넣는다.

### 9.3 import bootstrap 공통점

app 본체와 helper script 는 모두 `ensure_runtime_import_paths()` 계열 bootstrap 을 공유한다. 핵심은 bundle resource layout 에서는 `lib/python*` root 를, source tree 에서는 `src` parent 를 `sys.path` 에 넣는 것이다.

## 10. menu UI render/update model

menu UI 는 별도 model layer 없이 `PhotosMcpStateStore.snapshot()` 을 렌더링한다.

중요 규칙:

- 상단 header 는 daemon status, preflight aggregate, active job count, recent job count 를 같이 보여준다.
- active jobs 는 최대 2개, recent jobs 는 최대 8개만 렌더링한다.
- active progress 가 99.5% 이상이고 status 가 `running`/`busy` 면 `Finalizing` 표기를 쓴다.
- recent job detail 은 `reason` 우선, 없으면 `result_available` / `summary_available` 를 조합해 보여준다.
- refresh timer 는 `job_poll_interval_seconds` 마다 돌고, daemon 이 running 일 때만 `refresh_jobs_once()` 를 호출한다.

운영상 의미:

- UI 숫자와 `/health` payload 는 같은 snapshot 을 읽으므로 서로 어긋나면 state refresh 경로를 먼저 의심하면 된다.

## 11. 운영상 중요한 env 요약

- `PHOTOS_MCP_START_DAEMON_ON_LAUNCH`: app launch 직후 daemon 자동 시작 여부
- `PHOTOS_MCP_JOB_POLL_INTERVAL_SECONDS`: menu refresh / job poll 간격
- `PHOTOS_MCP_SINGLE_INSTANCE`: single-instance lock on/off
- `PHOTOS_MCP_PREFLIGHT_TIMEOUT_SECONDS`: preflight timeout, 기본 30초
- `NANOBOT_PHOTOS_MCP_PREFLIGHT_TIMEOUT_SECONDS`: 이전 이름과의 호환용 설정
- `PHOTO_SOURCE_APPLE_FETCH_MODE`: Apple Photos read helper mode
- `PHOTO_RANKER_APPLE_FETCH_MODE`: `photo-ranker` fetch helper mode
- `PHOTO_RANKER_APPLE_EVENTS_MODE`: Apple Events album write helper mode
- `PHOTO_SOURCE_TERMINAL_TIMEOUT_SECS`: source helper timeout
- `PHOTO_RANKER_TERMINAL_TIMEOUT_SECS`: ranker helper timeout

## 12. 어디를 먼저 봐야 하나

- app 는 뜨는데 daemon 이 안 뜬다: `main.py`, `menu_app.py`, `daemon.py`
- `/health` 는 되는데 `/mcp` 가 깨진다: `server.py`, `vendor_loader.py`, vendor `server.py`
- job status 가 UI 와 다르다: `server.py`, `state.py`, `job_state.py`
- album write 가 warning/error 다: `preflight.py`, `vendor/photo-ranker/album_writer.py`, terminal helper scripts
- bundle 에서만 import 가 깨진다: `runtime_bootstrap.py`, `packaging.py`, `scripts/smoke_bundle_imports.py`
