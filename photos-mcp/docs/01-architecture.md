# photos-mcp 아키텍처

## 1. 한 줄 요약

`photos-mcp` 는 `photo-source` 와 `photo-ranker` 를 내부 vendor tree 로 포함하고, 이를 하나의 `PhotosMcp.app`, 하나의 `FastMCP` server, 하나의 localhost HTTP endpoint 로 묶는 macOS 전용 Photos MCP runtime 이다.

외부에서 보면 단일 MCP server 이지만, 내부에서는 아래 세 층이 협력한다.

- app shell: `PhotosMcp.app`, menu bar UI, single-instance, daemon lifecycle
- source layer: `photo-source`, 사진 조회와 export
- ranking layer: `photo-ranker`, 분석, 분류, review, write-back

## 2. 왜 이렇게 나뉘는가

이 구조의 목표는 세 가지다.

1. 외부에는 단일 MCP surface 만 노출한다.
2. 내부에서는 조회 계층과 분석 계층을 분리해 유지보수성을 확보한다.
3. app UI, health, background jobs 가 같은 runtime state 를 공유하게 한다.

즉, `photos-mcp` 는 단순한 tool 묶음이 아니라 아래를 함께 해결하는 통합 런타임이다.

- vendor tool 통합 노출
- macOS app packaging
- localhost daemon 실행
- health/preflight 진단
- job 상태 projection 과 UI 반영

## 3. 큰 그림

```text
MCP client
  -> PhotosMcp.app
	  -> facade FastMCP server
		  -> photos_status
		  -> photos_library
		  -> photos_run
		  -> photos_result
	  -> facade orchestration layer
		  -> photo-source
		  -> photo-ranker
	  -> PhotosMcpStateStore
	  -> menu bar UI / health endpoints
```

대표적인 요청 흐름은 아래 둘로 나뉜다.

### 조회 중심 요청

`photos_library(action="list"|"search"|"inspect")`

이 요청들은 대부분 `photo-source` 로 내려가고, Apple Photos 또는 다른 source adapter 가 실제 데이터를 읽는다.

### 분석/분류 중심 요청

`photos_run(intent="analyze"|"classify"|"curate"|"organize"|"import")`

이 요청들은 `photo-ranker` 로 내려가고, 필요한 경우 `photo-source` 를 다시 호출해 원본 사진을 읽은 뒤 분석/정리 결과를 만든다.

## 4. 핵심 데이터 흐름

### 4.1 앱 시작 흐름

1. `PhotosMcp.py` 또는 bundle executable 이 `src/photos_mcp/main.py` 로 진입한다.
2. `runtime_bootstrap.ensure_runtime_import_paths()` 가 source/bundle 환경에 맞는 import path 를 준비한다.
3. single-instance lock 을 획득한다.
4. `PhotosMcpStateStore` 와 `PhotosMcpDaemonController` 를 만든다.
5. menu bar UI 를 띄우고, 필요하면 daemon 과 preflight 를 자동 시작한다.

### 4.2 MCP tool 호출 흐름

1. MCP client 가 facade tool 을 호출한다.
2. `server.py` 가 facade service 로 요청을 넘긴다.
3. facade layer 가 필요한 vendor runtime 과 internal substep 을 결정한다.
4. 실제 vendor 함수가 실행된다.
5. 응답이 job payload 이면 facade layer 가 공통 envelope 로 정규화한다.
6. `PhotosMcpStateStore` 가 갱신되고, menu UI 와 `/health`, `photos_status` 가 같은 snapshot 을 읽는다.

### 4.3 background job 반영 흐름

1. `start_classify_job` 가 `photo-ranker` queue 에 job 을 등록한다.
2. `photo-ranker` DB 와 queue 가 source of truth 로 job 상태를 가진다.
3. `daemon.py` 의 poller thread 가 `PhotoRankerJobStore` 를 통해 상태를 읽는다.
4. `state.py` 가 active/recent job snapshot 을 유지한다.
5. popover UI 와 health payload 는 이 snapshot 을 그대로 렌더링한다.

이 때문에 `photos-mcp` 는 job 관련 응답을 단순 전달하지 않고 일부 보정한다.

## 5. 주요 컴포넌트

### 5.1 bootstrap / entrypoint

- `PhotosMcp.py`
- `src/photos_mcp/main.py`

역할:

- source tree 실행 시 `src/` 를 `sys.path` 에 넣는다.
- bundle runtime 에서는 bundled Python library root 를 먼저 `sys.path` 에 올린다.
- `--health`, `--version`, `--help` 같은 lightweight CLI 모드를 제공한다.
- 일반 실행 경로에서는 single-instance lock 을 잡고 menu app + daemon lifecycle 을 시작한다.

### 5.2 config contract

- `src/photos_mcp/config.py`
- `src/photos_mcp/runtime_paths.py`

기본 계약:

- app name: `PhotosMcp`
- bundle id: `com.nanobot.photos-mcp`
- host: `127.0.0.1`
- port: `18791`
- MCP path: `/mcp`
- health path: `/health`

runtime/cache ownership:

- app home: `~/.photos-mcp`
- runtime root: `~/.photos-mcp/runtime`
- cache root: `~/.photos-mcp/cache`
- logs root: `~/.photos-mcp/logs`

이 ownership 모델은 중요하다. `photos-mcp` 는 Nanobot 의 부속이 아니라 독립 app 이므로 runtime 과 cache 도 app 기준으로 소유한다.

### 5.3 unified MCP server

- `src/photos_mcp/server.py`

역할:

- `FastMCP("photos-mcp")` 인스턴스를 만든다.
- `photos_status`, `photos_library`, `photos_run`, `photos_result` tool 을 제공한다.
- `/health`, `/health/capabilities` route 를 유지한다.
- facade service 를 통해 내부 `photo-source`, `photo-ranker` 호출을 orchestration 한다.
- job 계열 응답을 정규화해 state store 와 UI 에서 일관되게 사용할 수 있게 한다.

중요한 점은 `photos-mcp` 가 더 이상 vendor tool 을 그대로 public surface 에 노출하지 않고, 작은 facade 표면으로 transport 와 상태 해석을 단일화한다는 점이다.

### 5.4 vendored runtime loader

- `src/photos_mcp/vendor_loader.py`

역할:

- vendored `photo-source/server.py`, `photo-ranker/server.py` 를 동적으로 로드한다.
- `photos_mcp_vendor_photo_source`, `photos_mcp_vendor_photo_ranker` alias package namespace 를 준비한다.
- source 와 bundle layout 모두에서 vendor package 를 같은 module name 으로 import 할 수 있게 한다.

이 모듈은 source 실행과 bundle 실행의 차이를 흡수하는 가장 중요한 경계다.

### 5.5 `photo-source`

- `src/photos_mcp/vendor/photo-source/`

역할:

- 사진 목록 조회
- 메타데이터 조회
- thumbnail 생성
- 검색
- export

대표 tool:

- `list_photos`
- `get_metadata`
- `get_thumbnail`
- `search_photos`
- `export_photos`

지원 source:

- `apple`
- `local`
- `google`
- `gcs`

조회 계층에 대한 상세 설명은 `02-photo-source.md` 를 본다.

### 5.6 `photo-ranker`

- `src/photos_mcp/vendor/photo-ranker/`

역할:

- quality, face, scene, event, duplicate, ranking 분석
- background classify job 관리
- review metadata 생성과 선택 상태 저장
- Apple Photos album write-back
- end-to-end photo workflow 제공

대표 tool 그룹:

- analysis: `score_quality`, `detect_faces`, `describe_scene`, `classify_event`, `find_duplicates`, `rank_best_shots`
- jobs: `start_classify_job`, `get_job_status`, `get_job_summary`, `get_job_result`, `cancel_job`, `delete_job`, `clear_job_history`, `list_jobs`
- review/write-back: `get_review_items`, `set_photo_review`, `create_album`, `add_to_album`, `organize_results`, `list_photo_albums`
- workflows: `curate_best_photos`, `classify_and_organize`, `import_and_organize`

분석 계층에 대한 상세 설명은 `03-photo-ranker.md` 를 본다.

### 5.7 daemon controller

- `src/photos_mcp/daemon.py`
- `src/photos_mcp/job_state.py`

역할:

- uvicorn server thread lifecycle 관리
- job poller thread 관리
- `photo-ranker` DB/queue 와 state store 사이 adapter 제공

핵심 포인트:

- bundle 런타임에서 `uvicorn` submodule import 가 깨질 수 있어 경로 보정이 들어간다.
- `asyncio.run(self._server.serve())` 경로를 사용한다.
- persisted job state 의 source of truth 는 `photo-ranker` DB/queue 이고, `PhotosMcpStateStore` 는 projection cache 다.

### 5.8 menu bar UI

- `src/photos_mcp/menu_app.py`

역할:

- macOS status item / popover UI 제공
- daemon 상태, preflight 결과, active jobs, recent jobs 표시
- `Start`, `Stop`, `Run Checks`, `Refresh`, `Quit`, job cancel/delete, history clear action 제공

UI 는 별도 source of truth 를 갖지 않고 `PhotosMcpStateStore` snapshot 을 렌더링한다.

### 5.9 runtime state store

- `src/photos_mcp/state.py`
- `src/photos_mcp/job_state.py`

역할:

- daemon 상태
- preflight 결과
- active jobs / recent jobs
- background job running 여부

상태 분류:

- active: `pending`, `running`
- terminal: `completed`, `failed`, `cancelled`
- daemon: `stopped`, `starting`, `ready`, `busy`, `degraded`, `stopping`

## 6. health 와 capabilities 를 왜 분리하는가

`/health` 의 top-level `status` 는 transport readiness 기준이다. 즉, daemon 이 떠 있고 MCP endpoint 가 응답 가능한지를 보여준다.

반면 Apple Photos 실제 readiness 는 아래에서 본다.

- `preflight_status`
- `preflight_checks`
- `/health/capabilities`

이 구분은 중요하다. transport 는 살아 있지만, Photos read 나 automation permission 은 실패할 수 있기 때문이다.

## 7. source of truth 와 generated artifact

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

디버깅이나 수정은 기본적으로 `src/` 기준으로 해야 한다. `build/` 또는 app bundle 내부 복사본을 직접 고치면 다음 build 에 덮어써진다.

## 8. 현재 민감한 경계

현재 구현은 목표 상태에 가까워졌지만, 여전히 재정리 중인 중간 단계다. 남은 민감 구간은 아래다.

1. source/bundle 공용 import bootstrap 안정화 유지
2. `~/.photos-mcp` 앱 전용 runtime/cache/logs ownership 유지
3. packaging dependency/resource contract 검증 유지
4. app 본체와 Terminal helper bootstrap 일관성 유지
5. transport health 와 Apple Photos capability readiness 분리 유지
6. job state projection 과 adapter 경계 단순화

구조 재정리 방향은 `15-refactor-direction.md` 를 우선 기준으로 삼는다.
- `PhotosMcpStateStore` 는 app-owned persistent DB 가 아니라 menu UI, `/health`, MCP `photos_status` 를 위한 snapshot cache 다.
- terminal/active/running 판정은 `state.py` 의 `is_terminal_job_status`, `is_active_job_status`, `is_running_job_status` 를 기준으로 공유한다.

### 4.8 preflight

- `src/photos_mcp/preflight.py`

현재 preflight 는 3개다.

- `photos_read`: Apple Photos library 를 읽을 수 있는가
- `photos_automation`: Apple Photos automation write path 가 준비됐는가
- `photos_thumbnail`: Apple Photos analyze 에 필요한 thumbnail byte export 가 준비됐는가

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
7. `PhotosMcpDaemonController` 를 만들고 `run_menu_app()` 로 일반 앱 세션을 시작한다.
8. `menu_app.py` 가 status item 과 popover 를 설치하고 startup timer 를 예약한다.
9. startup sequence 가 preflight 를 먼저 실행하고, `start_daemon_on_launch=true` 면 `PhotosMcpDaemonController.start()` 를 호출한다.
10. `PhotosMcpDaemonController.start()` 내부에서 `build_server()` / `build_http_app()` 가 unified MCP server 와 HTTP app 을 만들고 uvicorn daemon thread 를 올린다.
11. daemon bind 가 성공하면 job poller 가 `photo-ranker` 상태를 주기적으로 snapshot 에 반영한다.

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
- `photos_thumbnail` 실패: sample asset local path, iCloud download 상태, Photos export permission, `photo-source get_thumbnail` 경로 확인

### direct source 실행은 되는데 bundle 에서만 실패한다

우선 확인할 파일:

- `main.py`
- `daemon.py`
- `vendor_loader.py`
- vendored helper scripts
- `packaging.py`
- `scripts/build_framework_standalone.sh`
