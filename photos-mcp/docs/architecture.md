# photos-mcp 아키텍처

## 1. 한 줄 요약

`photos-mcp` 는 `photo-source` 와 `photo-ranker` 를 내부 vendor tree 로 포함하고, 이를 하나의 `PhotosMcp.app` / 하나의 `FastMCP` server / 하나의 localhost HTTP endpoint 로 묶는 macOS 전용 Photos MCP runtime 이다.

## 2. 설계 목표

- 외부 sibling repo 없이 `photos-mcp` 디렉터리 하나만으로 실행, 테스트, 빌드 가능해야 한다.
- `photo-source` 와 `photo-ranker` 의 tool surface 는 유지하되, 외부에는 `photos-mcp` 하나의 MCP server 로 노출한다.
- `PhotosMcp.app` 이 실행 주체가 되고, Nanobot 을 포함한 외부 MCP client 는 HTTP client 로만 연결한다.
- menu bar UI 와 MCP server 가 같은 runtime state 를 읽어야 한다.
- build 산출물과 source tree 를 명확히 분리해야 한다.

## 2.1 현재 아키텍처 진단

현재 구현은 목표 상태에 도달하는 중간 단계다. 가장 큰 미해결 문제는 `photo-source` 와 `photo-ranker` 가 아직 명시적 package namespace 로 통합되지 않았고, `sys.path` / `sys.modules` 조작으로 vendor runtime 을 전환한다는 점이다.

따라서 이 문서의 구조 설명은 현재 상태를 설명하는 것이며, 코드 재정리 방향은 `refactor-direction.md` 를 우선 기준으로 삼는다.

현재 우선 해결해야 할 축은 아래 순서다.

1. vendor package namespace 정리 완료
2. sibling `mcp-my-photos` runtime/cache 의존 제거 및 `~/.photos-mcp` 앱 전용 root 로 수렴 완료
3. packaging dependency/resource contract 명시화 완료
4. app 본체와 Terminal helper bootstrap 통합 완료
5. transport health 와 Apple Photos capability readiness 분리 완료
6. job state ownership adapter 정리 완료

## 3. source of truth 와 generated artifact

### source of truth

- `src/photos_mcp/**`
- `src/apple_terminal_helper/**`
- `tests/**`
- `scripts/**`
- `PhotosMcp.py`
- `pyproject.toml`
- `setup.py`

### generated artifact

- `build/**`
- `build-framework-standalone/**`
- `dist/**`
- `dist-framework-standalone/**`
- `*.egg-info/**`

디버깅이나 수정은 기본적으로 `src/` 기준으로 해야 한다. `build/` 또는 app bundle 내부의 복사본을 직접 고치면 다음 build 에 덮어써진다.

## 4. 주요 컴포넌트

### 4.1 bootstrap / entrypoint

- `PhotosMcp.py`
- `src/photos_mcp/main.py`

역할:

- source tree 실행 시 `src/` 를 `sys.path` 에 넣는다.
- bundle runtime 에서는 `main.py` 가 bundled Python library root 를 먼저 `sys.path` 에 올린다.
- `--health`, `--version`, `--help` 같은 lightweight CLI 모드를 제공한다.
- 일반 실행 경로에서는 single-instance lock 을 잡고 menu app + daemon lifecycle 을 시작한다.

핵심 포인트:

- bundle import 문제를 추적할 때는 `main.py` 가 호출하는 `runtime_bootstrap.ensure_runtime_import_paths()` 가 가장 앞단의 bootstrap 지점이다.

### 4.2 config contract

- `src/photos_mcp/config.py`

기본 계약:

- app name: `PhotosMcp`
- bundle id: `com.nanobot.photos-mcp`
- host: `127.0.0.1`
- port: `18791`
- MCP path: `/mcp`
- health path: `/health`

runtime/cache ownership 목표:

- app home: `~/.photos-mcp`
- runtime root: `~/.photos-mcp/runtime`
- cache root: `~/.photos-mcp/cache`
- photo-ranker runtime root: `~/.photos-mcp/runtime/photo-ranker`
- photo-ranker VLM cache root: `~/.photos-mcp/cache/vlm`

현재 env override 는 `PHOTOS_MCP_*` 계열을 우선하고, 기존 `NANOBOT_PHOTOS_MCP_*` 계열은 compatibility fallback 으로만 남긴다.

env override:

- `PHOTOS_MCP_HOME`
- `PHOTOS_MCP_BUNDLE_PATH`
- `PHOTOS_MCP_RUNTIME_ROOT`
- `PHOTOS_MCP_CACHE_ROOT`
- `PHOTOS_MCP_HOST`
- `PHOTOS_MCP_PORT`
- `PHOTOS_MCP_STREAMABLE_HTTP_PATH`
- `PHOTOS_MCP_HEALTH_PATH`
- `PHOTOS_MCP_START_DAEMON_ON_LAUNCH`
- `PHOTOS_MCP_JOB_POLL_INTERVAL_SECONDS`
- `NANOBOT_PHOTOS_MCP_BUNDLE_PATH`
- `NANOBOT_PHOTOS_MCP_RUNTIME_ROOT`
- `NANOBOT_PHOTOS_MCP_CACHE_ROOT`
- `NANOBOT_PHOTOS_MCP_HOST`
- `NANOBOT_PHOTOS_MCP_PORT`
- `NANOBOT_PHOTOS_MCP_STREAMABLE_HTTP_PATH`
- `NANOBOT_PHOTOS_MCP_HEALTH_PATH`
- `NANOBOT_PHOTOS_MCP_START_DAEMON_ON_LAUNCH`

### 4.3 unified MCP server

- `src/photos_mcp/server.py`

역할:

- `FastMCP("photos-mcp")` 인스턴스를 만든다.
- `health_status` tool 과 `/health` route 를 제공한다.
- `photo-source` 와 `photo-ranker` 의 vendored tool 을 읽어 unified server 에 재등록한다.
- job 관련 tool 응답을 state store 친화적인 공통 형태로 보정한다.

핵심 함수:

- `build_server()`
- `build_http_app()`
- `_wrap_tool()`
- `_ingest_tool_response()`
- `build_health_payload()`

### 4.4 vendored runtime loader

- `src/photos_mcp/vendor_loader.py`

역할:

- vendored `photo-source/server.py`, `photo-ranker/server.py` 를 동적으로 로드한다.
- `photos_mcp_vendor_photo_source`, `photos_mcp_vendor_photo_ranker` alias package namespace 를 준비한다.
- source 와 bundle layout 모두에서 vendor package 를 같은 module name 으로 import 할 수 있게 한다.

이 모듈은 현재 구조에서 가장 중요한 경계다. source 실행과 bundle 실행 모두 여기서 vendor import 조건이 맞아야 한다.

리팩터링 목표는 이 모듈이 vendor package alias 와 runtime adapter/registry 역할에 집중하도록 유지하고, top-level import 충돌을 `sys.modules` 삭제로 해결하는 구조로 돌아가지 않는 것이다.

### 4.5 daemon controller

- `src/photos_mcp/daemon.py`
- `src/photos_mcp/job_state.py`

역할:

- uvicorn server thread lifecycle 을 관리한다.
- job poller thread 를 돌며 `PhotoRankerJobStore` 를 통해 `photo-ranker` queue / DB 상태를 state store 에 반영한다.
- job cancel/delete/clear 같은 UI action 을 `PhotoRankerJobStore` adapter 로 위임한다.

구조:

- HTTP server thread 1개
- job poller thread 1개
- state store 1개 공유

핵심 포인트:

- bundle 런타임에서 `uvicorn` submodule import 가 깨질 수 있으므로, `uvicorn.__path__` 보정이 포함돼 있다.
- `uvicorn.Server.run()` 대신 `asyncio.run(self._server.serve())` 경로를 쓴다.
- job source of truth 는 vendored `photo-ranker` DB/queue 다. `PhotosMcpStateStore` 는 menu UI 와 health payload 를 위한 in-memory projection 이다.

### 4.6 menu bar UI

- `src/photos_mcp/menu_app.py`

역할:

- macOS status item / popover UI 를 제공한다.
- daemon 상태, preflight 결과, active jobs, recent jobs 를 보여준다.
- `Start`, `Stop`, `Run Checks`, `Refresh`, `Quit`, job cancel/delete, history clear action 을 노출한다.
- app 자체는 일반 macOS app 으로 실행되며 Finder, Launchpad, Dock 에서 보일 수 있다.

UI 는 별도 상태를 들고 있지 않고 `PhotosMcpStateStore` snapshot 을 렌더링한다.

### 4.7 runtime state store

- `src/photos_mcp/state.py`
- `src/photos_mcp/job_state.py`

역할:

- daemon 상태
- preflight 결과
- active jobs / recent jobs
- background job running 여부

핵심 타입:

- `JobSnapshot`
- `PhotosMcpSnapshot`
- `PreflightCheckSnapshot`
- `PhotosMcpStateStore`

상태 분류:

- active: `pending`, `running`
- terminal: `completed`, `failed`, `cancelled`
- daemon: `stopped`, `starting`, `ready`, `busy`, `degraded`, `stopping`

state ownership:

- persisted job state 와 queue state 의 source of truth 는 vendored `photo-ranker` 의 SQLite DB 와 in-memory queue 다.
- `PhotoRankerJobStore` 는 DB/queue 를 읽고 cancel/delete/clear 를 수행하는 adapter 다.
- `PhotosMcpStateStore` 는 app-owned persistent DB 가 아니라 menu UI, `/health`, MCP `health_status` 를 위한 snapshot cache 다.
- terminal/active/running 판정은 `state.py` 의 `is_terminal_job_status`, `is_active_job_status`, `is_running_job_status` 를 기준으로 공유한다.

### 4.8 preflight

- `src/photos_mcp/preflight.py`

현재 preflight 는 2개다.

- `photos_read`: Apple Photos library 를 읽을 수 있는가
- `photos_automation`: Apple Photos automation write path 가 준비됐는가

특징:

- thread 기반 timeout wrapper 사용
- permission 문제는 warning 으로 downgrade 가능
- preflight 실패와 MCP daemon 자체 실패는 다른 층의 문제다

## 5. startup 흐름

1. 사용자가 `PhotosMcp.app` 또는 `PhotosMcp.py` 를 실행한다.
2. `PhotosMcp.py` 가 `src/` 를 `sys.path` 에 추가한다.
3. `photos_mcp.main` 이 bundle lib root 를 먼저 bootstrap 한다.
4. `load_config()` 로 runtime/cache/endpoint 계약을 확정한다.
5. `acquire_single_instance_lock()` 가 `runtime_root/photos-mcp.lock` 을 잡는다.
6. `PhotosMcpStateStore` 를 생성한다.
7. `PhotosMcpDaemonController.start()` 가 uvicorn daemon thread 를 올린다.
8. `build_server()` 가 vendored tool 들을 unified MCP server 로 재등록한다.
9. `menu_app.py` 가 일반 앱 세션 안에서 status item 과 popover 를 띄운다.
10. job poller 가 `photo-ranker` 상태를 주기적으로 snapshot 에 반영한다.

## 6. MCP 요청 흐름

1. Nanobot 또는 다른 MCP client 가 `POST /mcp` 로 initialize / list_tools / call_tool 을 보낸다.
2. `FastMCP` app 이 요청을 받는다.
3. tool call 직전 `_wrap_tool()` 이 해당 server name 에 맞는 vendor runtime path 를 준비한다.
4. 실제 tool 함수는 vendored `photo-source` 또는 `photo-ranker` 코드에서 실행된다.
5. 응답이 job payload 면 `_ingest_tool_response()` 가 `job_id`, `status`, `terminal`, `result_available` 등을 normalize 한다.
6. normalize 된 결과가 state store 에 반영된다.
7. 같은 state snapshot 이 `/health` 와 menu UI 에서 같이 노출된다.

## 7. Nanobot 통합 구조

실행 ownership:

- `PhotosMcp.app`: server owner
- `nanobot`: MCP HTTP client
- Nanobot gateway 기준 MCP 설정: `tools.mcpServers.photos-mcp.url = http://127.0.0.1:18791/mcp`
- Nanobot OpenAI-compatible API 서버는 별도 config 파일을 사용한다. 현재 `~/.nanobot/config.api.json` 도 같은 `photos-mcp` endpoint 를 사용하도록 맞췄고, live 반영은 Nanobot 서비스 재기동 기준으로 적용한다.

Nanobot 쪽 wrapper script:

- `infra/scripts/run-photos-mcp-app.sh`

역할:

- bundle 후보를 고른다.
- terminal helper 용 Python binary env 를 설정한다.
- bundle 모드면 `Contents/MacOS/PhotosMcp` 를 직접 exec 한다.
- source fallback 도 남아 있지만 운영 기준은 bundle 우선이다.
- explicit `PHOTOS_MCP_BUNDLE_PATH`, `NANOBOT_PHOTOS_MCP_BUNDLE_PATH` override 가 없으면 기본 bundle 경로는 `~/Applications/PhotosMcp.app` 이다.
- local build bundle 을 먼저 찾더라도 운영 실행 전에는 `~/Applications/PhotosMcp.app` 설치본을 자동 갱신하고 그 경로를 우선 사용한다.
- dry-run 은 선택된 `launch_mode`, `bundle_variant`, `bundle_path`, `terminal_helper_python_bin`, `~/.photos-mcp` runtime/cache root 를 JSON 으로 보여준다.

## 8. packaging 구조

### build definition

- `pyproject.toml`: 런타임 dependency 와 pytest path 설정
- `setup.py`: py2app build 진입점
- `src/photos_mcp/packaging_contract.py`: runtime-safe py2app package/include/resource contract
- `src/photos_mcp/packaging.py`: py2app options, resource staging, bundle naming normalization

py2app 계약은 `packaging_contract.py` 의 코드 상수로 고정한다.

- `APP_PACKAGES`: `setup.py` 가 `src/` 아래에서 찾을 app-owned package 목록
- `PY2APP_PACKAGES`: py2app 가 package 로 추적해야 하는 app/runtime package 목록
- `PY2APP_INCLUDES`: py2app 가 정적 분석으로 놓치기 쉬운 dynamic import 또는 macOS bridge module 목록
- `PY2APP_EXCLUDES`: bundle 에 넣지 않을 표준 GUI/test 불필요 module 목록
- `SITE_PACKAGES_RESOURCE_NAMES`, `SITE_PACKAGES_RESOURCE_PREFIXES`, `SITE_PACKAGES_RESOURCE_SUFFIXES`: resource fallback 으로 복사할 site-packages allowlist

site-packages resource staging 은 전체 child 복사가 아니라 allowlist 기반이다. `osxphotos`, `mcp`, PyObjC framework wrapper, `requests`/`charset_normalizer` 계열처럼 py2app 정적 분석이 놓치기 쉬운 transitive runtime package 는 이 allowlist 로 명시한다. vendored resource staging 도 `.venv`, cache, test, pyproject, lock file, 개발 메타 파일을 제외한다.

bundle import smoke:

- source 환경: `uv run python scripts/smoke_bundle_imports.py`
- bundle resource 확인: `PYTHONDONTWRITEBYTECODE=1 dist-framework-standalone/PhotosMcp.app/Contents/MacOS/python scripts/smoke_bundle_imports.py --bundle dist-framework-standalone/PhotosMcp.app`

bundle smoke 는 `Contents/Resources/lib/python*/lib-dynload` 를 함께 `sys.path` 에 올린다. 단, py2app 의 `python312.zip` 은 package root 로 주입하지 않는다. zip importer 가 resource fallback tree 보다 먼저 잡히면 일부 dynamic submodule 이 빠진 py2app 분석본을 import 할 수 있기 때문이다. signed app bundle 을 smoke 할 때는 bytecode 생성을 막아 codesign seal 을 유지한다.

## 9. runtime bootstrap 구조

`src/photos_mcp/runtime_bootstrap.py` 는 app 본체와 Terminal helper subprocess 가 공유하는 import/runtime bootstrap 계약이다.

역할:

- source tree 또는 bundle resource layout 에서 `photos_mcp` package parent 를 `sys.path` 에 추가한다.
- bundle 내부 `Contents/Resources/lib/python*` root 를 찾아 helper import path 에 추가한다.
- `PHOTO_RANKER_TERMINAL_PYTHON_BIN`, `PHOTO_SOURCE_TERMINAL_PYTHON_BIN` override 를 우선하고, bundle 실행 중이면 `Contents/MacOS/python` 을 Terminal helper Python 으로 선택한다.
- source fallback 에서는 vendored app dir 아래 `.venv/bin/python` 을 사용한다.
- Terminal.app helper 는 새 shell 에서 실행되므로 bytecode write 방지 env 를 명시적으로 넘긴다. `PYTHONDONTWRITEBYTECODE=1` 이 빠지면 signed bundle 내부에 `__pycache__` 가 생겨 codesign seal 이 깨질 수 있다.
- timeout 이 발생하면 helper PID 를 기록한 뒤 직접 종료해 permission prompt 또는 Apple Events 대기 상태의 subprocess 가 남지 않게 한다.

직접 import 대상:

- `main.py`: app entrypoint bootstrap
- `vendor_script_bootstrap.py`: vendored direct script/helper bootstrap
- `photo-ranker` album/source helper
- `photo-source` Apple Photos helper

### standalone build script

- `scripts/build_framework_standalone.sh`

역할:

- framework Python 3.12 runtime 탐색
- site-packages 재사용
- py2app 실행
- bundle name 정규화
- `liblzma.5.dylib` repair
- ad-hoc codesign
- `~/Applications/PhotosMcp.app` 설치본 갱신

## 10. 디버깅 관점에서 중요한 구분

health/readiness schema:

- `/health`: transport readiness 기준 응답. top-level `status` 는 daemon bind/MCP transport 상태를 반영한다.
- `/health/capabilities`: Apple Photos read/write capability 기준 응답.
- compatibility 이유로 top-level `preflight_status`, `preflight_checks`, `last_preflight_at` 는 유지하지만, 새 소비자는 `transport` 와 `capabilities` nested field 를 우선 사용한다.

### health 는 되는데 MCP initialize 가 실패한다

가능성:

- `uvicorn` / `FastMCP` startup 은 성공했지만 vendored tool import 또는 request path 에서 실패
- `vendor_loader.py` 또는 helper script 의 `sys.path` bootstrap 문제

### `/health` 의 preflight 는 실패하지만 MCP list_tools 는 된다

이 경우는 대개 transport 문제보다 Apple Photos read/automation path 문제다.

- `photos_read` 실패: `photo-source` / `osxphotos` / Photos DB access 경로 확인
- `photos_automation` 실패: terminal helper, Apple Events permission, `photo-ranker` write path 확인

### direct source 실행은 되는데 bundle 에서만 실패한다

우선 확인할 파일:

- `main.py`
- `daemon.py`
- `vendor_loader.py`
- vendored helper scripts
- `packaging.py`
- `scripts/build_framework_standalone.sh`
