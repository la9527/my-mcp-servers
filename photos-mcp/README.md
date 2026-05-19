# photos-mcp

`PhotosMcp` 는 Apple Photos 관련 read/write MCP 기능을 하나의 macOS app bundle 과 하나의 Python entrypoint 로 수렴시키기 위한 새 코드베이스다.

전반적인 구조 설명과 디버깅 기준 문서는 `docs/` 아래에 정리했다.

- `docs/README.md`
- `docs/architecture.md`
- `docs/feature-map.md`
- `docs/debugging-guide.md`
- `docs/refactor-direction.md`

현재 이 디렉터리는 phase-1 기준으로 아래를 포함한다.

- vendored `photo-source`, `photo-ranker` tool 을 하나의 FastMCP server 로 재등록하는 unified MCP layer
- localhost `streamable HTTP` daemon 경로
- `/health` endpoint
- macOS menu bar popover 기반 app shell
- job 상태 store 와 active/recent job summary 반영
- `src/` 기반 package layout 과 내부 vendor tree

포함 내용:

- `src/photos_mcp/config.py`: app 이름, bundle id, runtime/cache 경로 결정
- `src/photos_mcp/vendor_loader.py`: vendored `photo-source`, `photo-ranker` server module 로드
- `src/photos_mcp/legacy_loader.py`: 기존 import 호환용 shim
- `src/photos_mcp/packaging.py`: `py2app` build kwargs 와 distribution override 정의
- `src/photos_mcp/server.py`: FastMCP unified server + `/health` app 구성
- `src/photos_mcp/daemon.py`: uvicorn 기반 streamable HTTP daemon controller
- `src/photos_mcp/menu_app.py`: macOS menu bar status item / popover UI
- `src/photos_mcp/main.py`: `--health`, `--version`, app entrypoint
- `src/photos_mcp/state.py`: daemon/preflight/job 상태 projection cache
- `src/photos_mcp/job_state.py`: vendored `photo-ranker` DB/queue 접근 adapter
- `src/photos_mcp/single_instance.py`: runtime lock 기반 single-instance guard
- `src/photos_mcp/runtime_bootstrap.py`: source/bundle import path 와 Terminal helper Python 선택 공통 계약
- `src/photos_mcp/vendor/photo-source/`: vendored photo-source runtime
- `src/photos_mcp/vendor/photo-ranker/`: vendored photo-ranker runtime
- `src/apple_terminal_helper/`: vendored Terminal helper package
- `scripts/generate_app_icon.py`: warm photo-tool 성격의 app icon 생성기
- `scripts/build_framework_standalone.sh`: framework standalone build + ad-hoc codesign 자동화
- `tests/`: bootstrap 단위 테스트

현재 기본값:

- app 이름: `PhotosMcp`
- executable 이름: `PhotosMcp`
- bundle identifier: `com.nanobot.photos-mcp`

runtime/cache 경로의 정리 방향은 Nanobot 하위 경로가 아니라 앱 전용 root 인 `~/.photos-mcp` 아래로 수렴하는 것이다. `PhotosMcp` 는 Nanobot 을 포함한 여러 MCP client 에 연결될 수 있는 독립 app 이므로, 기본 runtime/cache ownership 은 client 가 아니라 app 기준으로 둔다.

현재 `build_server()` 는 `src/photos_mcp/vendor/photo-source/server.py` 와 `src/photos_mcp/vendor/photo-ranker/server.py` 의 MCP tool 을 읽어 와 하나의 `PhotosMcp` FastMCP instance 에 재등록한다. 외부 sibling repo 없이 `photos-mcp` 디렉터리 하나만으로 실행/테스트/빌드할 수 있는 구조를 기준으로 유지한다.

현재 재사용 대상인 주요 job/status surface 는 아래와 같다.

- `classify_photos`: background job 시작, `job_id` 와 초기 `status` 반환
- `list_jobs`: 저장된 job 목록 반환
- `get_job_status`: 현재 job 상태 반환
- `get_job_summary`: UI/chat 소비용 summary 반환
- `get_job_result`: 완료된 결과 반환
- `cancel_job`: running/pending job 취소
- `delete_job`: terminal job 의 DB/queue 기록 삭제
- `clear_job_history`: 완료/실패/취소 job history 일괄 정리

현재 구현에서는 `PhotosMcp.app` status UI 와 MCP 응답이 같은 job state store 를 읽는다. job 관련 MCP 응답은 wrapper 단계에서 가능한 범위까지 공통 envelope 로 보강되어 `job_id`, `status`, `terminal`, `finished_at`, `summary_available`, `result_available` 같은 필드를 일관되게 유지하려고 한다.

## Menu bar popover UI

`PhotosMcp.app` 의 메뉴바 항목은 `PM`, `PM*`, `PM!`, `PM-` 로 daemon 상태를 짧게 노출하고, 클릭 시 transient popover 를 연다.

Popover 는 아래 정보를 한 화면에서 다룬다.

- daemon/preflight 요약과 MCP endpoint
- `Stop` 또는 `Start` server action
- `Run Checks` 수동 preflight action
- refresh / quit 보조 action
- Photos library read, Photos automation check 상태
- active job 최대 2개와 진행률, `Stop` job action
- recent terminal job history, per-job delete, 전체 `Clear`

자동 startup preflight 는 popover 상태만 갱신하고 modal alert 를 띄우지 않는다. Photos Automation warning 이 있어도 daemon 자동 시작을 막지 않기 위해서다. 사용자가 `Run Checks` 를 직접 누른 경우에는 기존처럼 alert 로 상세 warning/success 를 확인할 수 있다.

Recent Jobs 는 terminal 상태(`completed`, `failed`, `cancelled`)만 표시한다. 완료/실패/취소 job 삭제는 vendored `photo-ranker` queue 와 SQLite DB 를 함께 정리하며, running/pending job 삭제는 거부한다. 실행 중 job 은 `Stop` 으로 cancel 한 뒤 terminal history 로 내려간다.

`PhotosMcp.app` 은 `~/Applications/PhotosMcp.app` 에 설치되는 일반 macOS app 이다. Finder, Launchpad, Dock 에서 보일 수 있고, 동시에 menu bar status item UI 도 유지한다.

현재 검증된 app build 경로:

- 개발용 alias mode build: `uv run --extra app python setup.py py2app -A`
- 생성 bundle: `dist/PhotosMcp.app`
- bundle 실행 확인: `dist/PhotosMcp.app/Contents/MacOS/PhotosMcp --health`
- standalone build: python.org macOS framework Python 3.12 계열을 기준으로 build env 를 만든 뒤 `python setup.py py2app` 로 생성 가능
- 반복 build 기본 경로: `./scripts/build_framework_standalone.sh`
- standalone 생성 bundle: `dist-framework-standalone/PhotosMcp.app`
- py2app 가 `photos-mcp.app` 로 생성하더라도 build script 가 최종 산출물을 `PhotosMcp.app` 로 정규화한다.
- standalone build script 는 `PHOTOS_MCP_FRAMEWORK_RUNTIME_DIR` 가 비어 있으면 `.framework-python-runtime`, `/Library/Frameworks`, Homebrew `python@3.12` framework 경로를 순서대로 탐색한다.
- standalone build script 는 `PHOTOS_MCP_SITE_PACKAGES_DIR` 가 비어 있으면 `.venv-framework312/.../site-packages` 와 현재 `.venv/.../site-packages` 를 순서대로 재사용한다.
- py2app 포함 계약은 `src/photos_mcp/packaging.py` 의 `PY2APP_PACKAGES`, `PY2APP_INCLUDES`, site-packages resource allowlist 로 관리한다.
- bundle import smoke 는 `uv run python scripts/smoke_bundle_imports.py` 로 source 환경에서 먼저 확인하고, bundle 생성 후에는 `--bundle dist-framework-standalone/PhotosMcp.app` 로 확인한다.
- build script 는 기본적으로 signed bundle 을 `~/Applications/PhotosMcp.app` 에 복사한다.
- Nanobot wrapper 기본 bundle 경로도 `~/Applications/PhotosMcp.app` 를 기준으로 맞춘다. local build bundle 을 찾았고 explicit override 가 없으면 사용자 앱 폴더 설치본을 자동 갱신한 뒤 그 경로를 사용한다.
- standalone bundle 은 `resources/PhotosMcp.icns` 를 app icon 으로 포함한다.
- standalone 후처리: Homebrew framework 경로에서 들어온 `liblzma.5.dylib` 는 clean copy 로 교체한 뒤 `py2app.util.codesign_adhoc` depth-first signing 을 다시 적용해야 macOS 가 embedded Python / extension module 을 정상 로드한다.
- standalone 검증: `dist-framework-standalone/PhotosMcp.app/Contents/MacOS/PhotosMcp --health` 와 MCP `initialize` / `list_tools` smoke 통과

런타임 동작:

- `PhotosMcp` 는 기본적으로 `runtime_root/photos-mcp.lock` 파일 잠금으로 single-instance 를 강제한다.
- 기본 entrypoint 는 일반 macOS app 이며, launch 시 daemon 을 자동 시작하고 menu bar status item 도 함께 띄운다.
- menu UI 에서 `Start`, `Stop`, `Run Checks`, `Refresh`, `Quit`, active job `Stop`, recent job `Delete`, recent history `Clear` action 을 제공한다.
- `Stop` 은 MCP daemon 만 내리고, `Quit` 은 app 전체를 종료한다.
- 테스트/디버그에서만 `PHOTOS_MCP_SINGLE_INSTANCE=0` 으로 lock 을 끌 수 있다.

health/runtime 기본값:

- MCP endpoint: `http://127.0.0.1:18791/mcp`
- health endpoint: `http://127.0.0.1:18791/health`
- capabilities endpoint: `http://127.0.0.1:18791/health/capabilities`
- app status 는 `stopped`, `starting`, `ready`, `busy`, `degraded`, `stopping` 으로 관리된다.

health payload 해석:

- top-level `status` 는 transport readiness 기준이다.
- top-level `preflight_*` 필드는 compatibility 용으로 유지된다.
- 새 client 나 운영 확인은 `transport` 와 `capabilities` nested field 를 우선 사용한다.

repo hygiene:

- top-level build/dist/venv/framework cache/egg-info 산출물은 `.gitignore` 로 제외한다.
- generated artifact 는 working tree 에 남기지 않고 필요 시 다시 build 하는 것을 기본 원칙으로 둔다.
- 현재 정리 대상에는 `build*`, `dist*`, `.venv*`, `.framework-python-*`, `*.egg-info`, `__pycache__` 가 포함된다.

운영 cutover 방향:

- `PhotosMcp.app` 을 먼저 실행한다.
- app 은 menu bar 에 상주하면서 localhost `streamable HTTP` MCP daemon 으로 동작한다.
- `nanobot` 은 stdio child launch 가 아니라 HTTP MCP client 로만 연결한다.

기본 검증:

- `./.venv/bin/python -m photos_mcp.main --health`
- app 실행 후 `curl -fsS http://127.0.0.1:18791/health`
- focused test: `./.venv/bin/pytest tests/test_main.py tests/test_config.py tests/test_state.py tests/test_packaging.py tests/test_preflight.py tests/test_daemon.py -q`
- 전체 repo test: `uv run pytest -q`