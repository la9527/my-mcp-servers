# photos-mcp 코드 재정리 방향

## 1. 현재 결론

현재 `photos-mcp` 의 가장 큰 문제는 기능 하나가 깨진 것이 아니라, 실행 모델과 코드 구조가 서로 다른 전제를 동시에 갖고 있다는 점이다.

문서와 목표는 `photos-mcp` 를 self-contained app 으로 두고 있다. Phase 1, Phase 2 이후에도 아직 아래 성격은 남아 있다.

- standalone bundle 은 dependency graph 기반 package build 보다 resource copy + bootstrap 보정에 의존한다.
- app 본체와 Terminal helper subprocess 가 같은 runtime bootstrap 계약을 공유하지 않는다.
- `/health` 안에 daemon readiness 와 Apple Photos capability readiness 가 섞여 있다.

이미 정리된 축:

- `photo-source`, `photo-ranker` 의 vendor-local top-level import 는 package namespace 로 전환했다.
- Nanobot wrapper 와 app 기본 runtime/cache root 는 `~/.photos-mcp` 기준으로 수렴했다.

따라서 다음 수정은 개별 preflight 오류를 바로 땜질하기보다, import/runtime/package boundary 를 먼저 안정화하는 순서로 진행한다.

## 2. 근본 문제 목록

### 문제 1. vendor runtime 이 package namespace 를 갖지 않는다

Phase 1 이전에는 `photo-source` 와 `photo-ranker` 가 둘 다 `models`, `sources` 같은 top-level module 이름을 사용했다. 그래서 `src/photos_mcp/vendor_loader.py` 는 tool call 전마다 아래 작업을 했다.

- vendor root 를 `sys.path` 맨 앞에 삽입
- `sources`, `models` 계열 모듈을 `sys.modules` 에서 제거
- `server.py` 를 파일 경로로 직접 import

이 구조에서는 어떤 vendor root 가 현재 `sys.path[0]` 인지가 코드 의미를 바꿨다. Phase 1 에서 `photos_mcp_vendor_photo_source`, `photos_mcp_vendor_photo_ranker` alias 와 package-relative import 로 전환해 이 문제를 닫았다.

### 문제 2. packaging 이 resource copy 와 runtime bootstrap 에 과하게 의존한다

`src/photos_mcp/packaging.py` 는 현재 `site-packages` 아래 child 들을 resource 로 복사한다. 그 후 `main.py`, `daemon.py`, helper script 가 각자 bundle path 를 다시 보정한다.

이 방식은 빠르게 bundle 을 만들 수는 있지만, 다음 문제가 생긴다.

- py2app 이 실제 import graph 를 이해하지 못한다.
- dynamic import 계열 dependency 가 누락되기 쉽다.
- bundle 에 파일이 있어도 package `__path__` 가 맞지 않아 import 가 실패할 수 있다.
- source 에서는 통과하지만 bundle 에서만 깨지는 문제가 반복된다.

### 문제 3. self-contained 목표와 live wrapper 가 충돌한다

Phase 2 이전에는 Nanobot wrapper 가 `photos-mcp` bundle 을 실행하면서도 runtime/cache/model state 는 sibling repo 인 `mcp-my-photos` 아래를 기본으로 썼다.

이 상태에서는 `photos-mcp` 디렉터리 하나만으로 운영된다고 말하기 어려웠다. Phase 2 에서 기본 home 을 `~/.photos-mcp` 로 옮기고, Nanobot wrapper 도 같은 app-owned root 를 사용하도록 정리했다.

### 문제 4. app 본체와 Terminal helper 의 bootstrap 계약이 다르다

Apple Photos read/write 경로는 permission 문제 때문에 Terminal helper subprocess 를 사용한다. 그런데 helper 는 app 본체와 같은 import/bootstrap 진입점을 공유하지 않는다.

결과적으로 아래 문제가 반복된다.

- app 본체에서는 import 되지만 helper 에서는 import 안 됨
- helper 에서 vendor package root 는 잡히지만 app bundle site-packages 는 빠짐
- helper 마다 parent 탐색과 `sys.path` 보정이 따로 구현됨

### 문제 5. health 와 capability check 가 섞여 있다

현재 `/health` 는 daemon 상태와 preflight 결과를 함께 반환한다. MCP server 가 정상이어도 Apple Photos library read 가 실패하면 `preflight_status=error` 가 된다.

이 자체는 유용한 정보지만, 운영 판단에서는 두 readiness 를 분리해야 한다.

- transport readiness: app process, daemon bind, MCP initialize 가능 여부
- capability readiness: Apple Photos read/write capability, permission, helper 상태

## 3. 목표 아키텍처

### 3.1 vendor runtime 은 명시적 package namespace 를 가져야 한다

목표는 `sys.path` 전환 없이 import 가능한 구조다.

예상 방향:

- `photos_mcp.vendor_runtime.photo_source`
- `photos_mcp.vendor_runtime.photo_ranker`

또는 동등하게 명확한 package namespace 를 둔다.

중요한 것은 `models`, `sources`, `db`, `jobs`, `pipeline` 같은 top-level import 를 제거하는 것이다.

### 3.2 vendor adapter 를 둔다

unified server 가 vendor 내부 파일을 직접 만지기보다, 작은 adapter layer 를 둔다.

예상 역할:

- photo-source tool export
- photo-ranker tool export
- job DB / queue 접근
- Apple Photos read/write capability probe
- runtime/cache root resolve

`server.py`, `daemon.py`, `preflight.py` 는 이 adapter 를 통해 접근하고 vendor 내부 import 구조에 덜 의존해야 한다.

### 3.3 runtime/cache root 는 `~/.photos-mcp` 앱 전용 root 로 옮긴다

기본 runtime/cache 는 Nanobot 하위 경로가 아니라 `photos-mcp` 앱 전용 home 인 `~/.photos-mcp` 아래로 수렴한다. `photos-mcp` 는 Nanobot 에서도 쓸 수 있지만, Nanobot 전용 하위 기능이 아니라 독립 MCP server app 이므로 state ownership 도 앱 전용 경로가 기준이어야 한다.

목표 기본 구조:

- `~/.photos-mcp/runtime/`: lock, app runtime state, job runtime data
- `~/.photos-mcp/cache/`: VLM cache, derived metadata cache, 재생성 가능한 data
- `~/.photos-mcp/logs/`: app 또는 helper 실행 로그가 필요할 때의 기본 위치

예상 env 기본값:

- `PHOTOS_MCP_HOME=~/.photos-mcp`
- `PHOTO_RANKER_RUNTIME_ROOT=~/.photos-mcp/runtime/photo-ranker`
- `PHOTO_RANKER_VLM_CACHE_ROOT=~/.photos-mcp/cache/vlm`

`mcp-my-photos` sibling repo 경로는 migration 전용 fallback 이거나 명시적 override 로만 남긴다.

### 3.4 packaging 은 dependency contract 를 명시해야 한다

장기 방향은 `site-packages` 전체 복사가 아니라 명시적 package/dependency/resource 포함이다.

단기적으로 py2app 을 유지하더라도 아래는 명시해야 한다.

- 포함해야 할 packages
- dynamic import 가 필요한 hidden imports
- vendored package data
- helper script data
- bundle Python 이 사용할 runtime root

### 3.5 helper bootstrap 은 공통 모듈로 통합한다

helper script 마다 `sys.path` 탐색을 따로 두지 않는다.

예상 방향:

- `photos_mcp.runtime_bootstrap` 같은 공통 모듈 추가
- app 본체와 helper subprocess 가 같은 bootstrap 함수를 사용
- helper 는 vendor package root 를 직접 추측하지 않고 공통 계약을 사용

### 3.6 health 는 readiness 계층을 분리한다

권장 구조:

- `/health`: process / daemon / MCP transport readiness
- `/health/capabilities` 또는 health payload 내부 `capabilities`: Apple Photos read/write readiness

최소한 문서와 UI 에서 `daemon_status` 와 `preflight_status` 의 의미를 명확히 분리한다.

## 4. 작업 순서와 완료 체크리스트

이 섹션은 앞으로의 작업 보드 역할을 한다. 각 문제를 해결할 때는 코드와 테스트만 고치지 않고, 해당 checkbox 를 `[x]` 로 바꾸고 완료 메모에 검증 결과를 함께 남긴다.

진행 규칙:

- 위에서 아래 순서로 진행한다. 순서를 바꿔야 하면 해당 phase 아래에 이유를 적는다.
- 완료 표시는 실제 코드/설정/문서 반영과 검증이 끝난 뒤에만 `[x]` 로 바꾼다.
- 진행 중인 항목은 `[ ]` 로 두고, 바로 아래에 `진행 메모:` 를 추가한다.
- 완료 메모에는 날짜, 변경 범위, 실행한 검증 명령 또는 확인 방법을 적는다.
- 한 phase 안의 완료 조건이 모두 충족되지 않았으면 phase checkbox 는 완료 처리하지 않는다.

전체 진행 상태:

- [x] Phase 0. 현재 구조 문서와 검증 기준 고정
- [x] Phase 1. vendor package namespace 정리
- [x] Phase 2. `~/.photos-mcp` runtime/cache ownership 정리
- [x] Phase 3. bundle packaging contract 정리
- [x] Phase 4. helper runtime bootstrap 통합
- [x] Phase 5. health / capability readiness 분리
- [x] Phase 6. state ownership 정리

### [x] Phase 0. 현재 구조 문서와 검증 기준 고정

목표:

- [x] 지금 문서화한 문제를 기준으로 리팩터링 범위를 고정한다.
- [x] 이후 수정은 preflight 증상부터 임의로 고치지 않고, 아래 phase 순서를 따른다.

완료 조건:

- [x] `docs/refactor-direction.md` 가 현재 진단과 작업 순서를 담는다.
- [x] `docs/architecture.md`, `docs/debugging-guide.md` 가 이 문서를 기준 문서로 연결한다.
- [x] runtime/cache root 목표를 `~/.photos-mcp` 앱 전용 home 으로 문서화한다.

완료 메모:

- 2026-05-19: 아키텍처 진단, `~/.photos-mcp` runtime/cache 방향, phase 순서, 완료 체크리스트를 문서화했다. Markdown diagnostics 기준 오류 없음.

### [x] Phase 1. vendor package namespace 정리

목표:

- [x] `sys.path` 전환과 `sys.modules` 삭제 의존도를 줄인다.

주요 작업:

- [x] `photo-source` 와 `photo-ranker` 의 top-level import 목록을 inventory 로 고정한다.
- [x] `photo-source` runtime server/source modules 를 package namespace 아래로 이동 또는 import alias 정리한다.
- [x] `photo-source` 직접 실행용 script import 를 package namespace 기준으로 정리한다.
- [x] `photo-ranker` MCP server runtime modules 를 package namespace 아래로 이동 또는 import alias 정리한다.
- [x] `photo-ranker` 직접 실행용 CLI/script/review app import 를 package namespace 기준으로 정리한다.
- [x] top-level import 를 package-relative import 로 전환한다.
- [x] `vendor_loader.py` 를 adapter/registry 성격으로 축소한다.

완료 조건:

- [x] MCP runtime 기준으로 `prepare_vendor_runtime()` 이 vendor root 를 `sys.path` 에 넣지 않아도 tool load 가 가능하다.
- [x] MCP runtime 기준으로 `models`, `sources` 충돌을 `sys.modules` 삭제로 해결하지 않는다.
- [x] source test 와 mock MCP client test 가 통과한다.
- [x] 직접 실행용 CLI/script/review app 경로도 package namespace 기준으로 정리한다.

진행 메모:

- 2026-05-19: `docs/vendor-import-inventory.md` 와 `tests/test_vendor_import_inventory.py` 로 현재 top-level local import 기준선을 고정했다.
- 2026-05-19: `photo-source` 의 운영 runtime 경로(`server.py`, `sources/*.py`)를 package-relative import 로 전환하고, loader 에 `photos_mcp_vendor_photo_source` package alias 를 추가했다. `photo-source` script 와 `photo-ranker` 는 아직 남아 있으므로 Phase 1 전체 완료는 아님.
- 2026-05-19: `photo-ranker` 의 MCP server runtime 경로(`server.py`, `pipeline.py`, `db.py`, `scoring.py`, `engines/*.py`)를 package-relative import 로 전환하고, loader 에 `photos_mcp_vendor_photo_ranker` package alias 를 추가했다. 직접 실행용 CLI/script/review app import 는 아직 남아 있으므로 Phase 1 전체 완료는 아님.
- 2026-05-19: `uv run pytest -q` 기준 전체 source test 34개 통과. 남은 top-level local import 는 `batch_classify.py`, `review_app.py`, `scripts/*` 로 제한됨.
- 2026-05-19: 직접 실행용 CLI/script/review app import 를 `photos_mcp_vendor_*` alias 로 전환하고, `vendor_script_bootstrap.py` 와 script-local `_script_bootstrap.py` 를 추가했다. `rg` 기준 vendor-local top-level import 없음. `uv run python -m py_compile ...` 기준 직접 실행용 파일 compile 통과.

### [x] Phase 2. `~/.photos-mcp` runtime/cache ownership 정리

목표:

- [x] live wrapper 의 sibling `mcp-my-photos` runtime/cache 기본값을 끊고, client 와 무관한 app 전용 root 인 `~/.photos-mcp` 를 기본값으로 만든다.

주요 작업:

- [x] `PHOTOS_MCP_HOME` 기본값을 `~/.photos-mcp` 로 정의한다.
- [x] `PHOTO_RANKER_RUNTIME_ROOT` 기본값을 `~/.photos-mcp/runtime/photo-ranker` 로 이동한다.
- [x] `PHOTO_RANKER_VLM_CACHE_ROOT` 기본값을 `~/.photos-mcp/cache/vlm` 로 이동한다.
- [x] app lock 과 app-owned runtime state 기본 위치를 `~/.photos-mcp/runtime` 아래로 이동한다.
- [x] 현재 남아 있는 `NANOBOT_PHOTOS_MCP_*` env 이름은 compatibility alias 로 유지하고, `PHOTOS_MCP_*` 를 우선한다.
- [x] 기존 sibling runtime 은 기본값에서 제거하고 명시적 env override 로만 남긴다.
- [x] Nanobot wrapper dry-run/test 를 갱신한다.

완료 조건:

- [x] wrapper dry-run 이 sibling repo 경로 없이도 실행 경로를 설명한다.
- [x] live runtime/cache 기본값이 `~/.photos-mcp` 기준으로 닫힌다.
- [x] Nanobot 이 아닌 다른 MCP client 로 연결해도 runtime/cache 경로가 바뀌지 않는다.

완료 메모:

- 2026-05-19: `photos_mcp.runtime_paths` 를 추가하고 `PHOTOS_MCP_HOME`, `PHOTOS_MCP_RUNTIME_ROOT`, `PHOTOS_MCP_CACHE_ROOT` 를 app-owned 기본 env 로 정의했다. legacy `NANOBOT_PHOTOS_MCP_*` 는 fallback alias 로 유지한다.
- 2026-05-19: `photo-ranker` DB/artifact/model cache 와 Terminal fetch cache, `photo-source` Terminal fetch cache 를 `~/.photos-mcp` 하위 기본값으로 이동했다.
- 2026-05-19: Nanobot `run-photos-mcp-app.sh` 와 install script 의 runtime/cache 기본값을 `~/.photos-mcp` 로 변경하고 dry-run payload 에 실제 경로를 노출했다.
- 2026-05-19: 검증: `uv run pytest -q` 38개 통과, `PYTHONPATH=$PWD ./.venv/bin/pytest tests/test_photos_mcp_wrapper.py tests/test_local_model_scripts.py -q` 13개 통과.

### [x] Phase 3. bundle packaging contract 정리

목표:

- [x] resource copy + runtime patch 의 비중을 줄이고, py2app 포함 계약을 명시화한다.

주요 작업:

- [x] dependency include 목록을 명시화한다.
- [x] dynamic import hidden imports 를 문서화/테스트화한다.
- [x] vendored resource inclusion 범위를 축소한다.
- [x] build artifact cleanup 경계를 재확인한다.

완료 조건:

- [x] standalone bundle 에 필요한 package 가 왜 포함되는지 코드와 문서에서 설명 가능하다.
- [x] bundle-only import failure 를 재현하는 smoke test 또는 script 가 있다.

완료 메모:

- 2026-05-19: `src/photos_mcp/packaging.py` 에 `PY2APP_PACKAGES`, `PY2APP_INCLUDES`, `PY2APP_EXCLUDES`, site-packages resource allowlist 를 명시했다. `site-packages` 전체 복사 대신 app runtime 에 필요한 package/dist-info 중심으로 제한한다.
- 2026-05-19: `src/photos_mcp/packaging_contract.py` 로 runtime-safe packaging 상수를 분리했다. `APP_PACKAGES` 는 setup package 목록, `PY2APP_PACKAGES` 는 py2app runtime package 목록으로 분리해 `src/anyio` 같은 잘못된 package directory 탐색을 막는다.
- 2026-05-19: `osxphotos`, `mcp`, `requests`, PyObjC framework wrapper, `charset_normalizer` mypyc helper 처럼 bundle import 에 필요한 transitive dependency 를 allowlist 에 추가했다. generated native helper 는 suffix allowlist 로 포함한다.
- 2026-05-19: vendor resource staging 에서 `.gitignore`, `.python-version`, `pyproject.toml`, `uv.lock`, `tests` 같은 개발 메타/테스트 산출물을 제외하도록 정리했다.
- 2026-05-19: `scripts/smoke_bundle_imports.py` 를 추가해 source 또는 bundle resource path 에서 py2app 계약에 포함된 package/include import 를 검증할 수 있게 했다. bundle smoke 는 `Contents/Resources/lib/python*/lib-dynload` 를 포함하되 `python312.zip` 을 package root 로 주입하지 않는다.
- 2026-05-19: bundle import smoke 가 signed app 안에 `.pyc` 를 쓰면 codesign seal 이 깨질 수 있어 `sys.dont_write_bytecode = True` 를 적용했다. 검증 시에도 `PYTHONDONTWRITEBYTECODE=1` 사용을 권장한다.
- 2026-05-19: 검증: `uv run pytest tests/test_packaging.py -q` 7개 통과, `uv run python scripts/smoke_bundle_imports.py` 통과, `PYTHONDONTWRITEBYTECODE=1 dist-framework-standalone/PhotosMcp.app/Contents/MacOS/python scripts/smoke_bundle_imports.py --bundle dist-framework-standalone/PhotosMcp.app` 통과, `codesign --verify --deep --strict dist-framework-standalone/PhotosMcp.app` 통과.

### [x] Phase 4. helper runtime bootstrap 통합

목표:

- [x] app 본체와 Terminal helper subprocess 가 같은 runtime bootstrap 계약을 사용한다.

주요 작업:

- [x] 공통 bootstrap helper 를 추가한다.
- [x] `apple_photos_terminal_runner.py`, `apple_photos_terminal_fetch.py` 에서 공통 bootstrap 을 사용한다.
- [x] helper env contract 를 정리한다.

완료 조건:

- [x] helper 별도 실행에서도 `osxphotos`, `photoscript`, `apple_terminal_helper`, vendor modules import 경로가 일관된다.

완료 메모:

- 2026-05-19: `src/photos_mcp/runtime_bootstrap.py` 를 추가해 source/bundle import path 보정과 Terminal helper Python 선택 계약을 공통화했다.
- 2026-05-19: `main.py`, `vendor_script_bootstrap.py`, `photo-ranker` album/source helper, `photo-source` Apple Photos helper 가 공통 bootstrap 함수를 사용하도록 정리했다.
- 2026-05-19: `apple_photos_terminal_runner.py`, `apple_photos_terminal_fetch.py` 에 남아 있던 중복 bundle lib 탐색과 `sys.path` 보정을 제거했다.
- 2026-05-19: 검증: `uv run python -m py_compile ...` 통과, `uv run pytest tests/test_runtime_bootstrap.py tests/test_main.py tests/test_vendor_import_inventory.py -q` 16개 통과, `uv run pytest -q` 44개 통과.

### [x] Phase 5. health / capability readiness 분리

목표:

- [x] daemon transport readiness 와 Apple Photos capability readiness 를 구분한다.

주요 작업:

- [x] health payload schema 를 정리한다.
- [x] 필요하면 capability endpoint 를 추가한다.
- [x] UI 에서 daemon failure 와 capability warning/error 를 다르게 표시한다.

완료 조건:

- [x] MCP initialize/list_tools 성공 여부와 preflight 실패 여부를 별도로 판단할 수 있다.

완료 메모:

- 2026-05-19: `build_health_payload()` 에 `transport` 와 `capabilities` nested payload 를 추가하고, 기존 top-level `daemon_status`, `preflight_status`, `preflight_checks` 는 compatibility 필드로 유지했다.
- 2026-05-19: `/health/capabilities` endpoint 를 추가해 Apple Photos capability 상태를 transport readiness 와 분리해 조회할 수 있게 했다.
- 2026-05-19: standalone bundle live smoke 기준 `/health` 는 `status=ok`, `transport.status=ok`, `daemon_status=ready` 를 반환했고, `/health/capabilities` 는 macOS Automation 권한 미허용 상태를 `warning` 으로 분리해 반환했다. MCP `initialize/list_tools/list_resources/list_prompts` 는 같은 bundle 에서 성공했다.
- 2026-05-19: 검증: `uv run pytest tests/test_main.py tests/test_mcp_client.py -q` 14개 통과, `uv run pytest -q` 45개 통과.

### [x] Phase 6. state ownership 정리

목표:

- [x] `PhotosMcpStateStore` 가 단순 in-memory UI cache 인지, app-owned runtime state 인지 역할을 명확히 한다.

주요 작업:

- [x] job DB / queue 접근을 adapter 로 감싼다.
- [x] state store snapshot 과 persisted job state 경계를 문서화한다.
- [x] menu UI, `/health`, MCP job response 가 같은 terminal 판정 규칙을 사용하도록 고정한다.

완료 조건:

- [x] job 상태 문제를 볼 때 source of truth 가 어디인지 명확하다.

완료 메모:

- 2026-05-19: `src/photos_mcp/job_state.py` 의 `PhotoRankerJobStore` adapter 를 추가해 vendored `photo-ranker` DB/queue 접근을 `daemon.py` 에서 분리했다. job source of truth 는 vendored DB/queue 이고, `PhotosMcpStateStore` 는 menu/health projection 으로 정의한다.
- 2026-05-19: `state.py` 에 `job_status_value`, `is_terminal_job_status`, `is_active_job_status`, `is_running_job_status` 를 추가해 menu UI, `/health`, MCP job response 가 같은 상태 판정 규칙을 쓰도록 고정했다.
- 2026-05-19: 검증: `uv run pytest tests/test_job_state.py tests/test_state.py tests/test_daemon.py -q` 13개 통과, `uv run pytest -q` 50개 통과.

## 4.1 standalone bundle 재검증 결과

Phase 1-6 1차 구현 뒤 다음 bundle 검증을 완료했다.

- [x] standalone bundle rebuild: `PHOTOS_MCP_INSTALL_BUNDLE_PATH= ./scripts/build_framework_standalone.sh`
- [x] codesign 검증: `codesign --verify --deep --strict dist-framework-standalone/PhotosMcp.app`
- [x] source import smoke: `uv run python scripts/smoke_bundle_imports.py`
- [x] bundle import smoke: `PYTHONDONTWRITEBYTECODE=1 dist-framework-standalone/PhotosMcp.app/Contents/MacOS/python scripts/smoke_bundle_imports.py --bundle dist-framework-standalone/PhotosMcp.app`
- [x] bundle CLI health: `dist-framework-standalone/PhotosMcp.app/Contents/MacOS/PhotosMcp --health`
- [x] live `/health`: 임시 `PHOTOS_MCP_PORT=18792`, 임시 `PHOTOS_MCP_HOME` 에서 `status=ok`, `daemon_status=ready`, `transport.status=ok` 확인
- [x] live `/health/capabilities`: `photos_read=ok`, `photos_automation=warning` 확인. warning 사유는 코드 오류가 아니라 macOS Automation 권한 `-1743` 이다.
- [x] live MCP smoke: `initialize`, `list_tools`, `list_resources`, `list_prompts` 성공. tool 38개 노출 확인.
- [x] Nanobot wrapper dry-run: `launch_mode=bundle`, `bundle_variant=framework-standalone`, `terminal_helper_python_bin=.../Contents/MacOS/python`, runtime/cache root `~/.photos-mcp` 확인
- [x] Nanobot wrapper live smoke: `./run-photos-mcp-app.sh` 경유로 default `http://127.0.0.1:18791/mcp` 실행, `/health=ok`, `/health/capabilities=ok`, MCP `initialize/list_tools/list_resources/list_prompts` 성공. tool 38개 노출 확인.
- [x] Nanobot MCP client registry smoke: `~/.nanobot/config.json` 의 `tools.mcpServers.photos-mcp.url=http://127.0.0.1:18791/mcp` 기준으로 `nanobot.agent.tools.mcp.connect_mcp_servers()` 실행. `photos-mcp` connected, Nanobot tool wrapper 38개 등록, `mcp_photos-mcp_health_status` 실행 결과 `status=ok` 확인.
- [x] Nanobot config 확인: gateway config 와 OpenAI-compatible API 용 `~/.nanobot/config.api.json` 모두 `photos-mcp` MCP server (`http://127.0.0.1:18791/mcp`) 를 포함한다.
- [x] 사용자 앱 폴더 기본화: app 기본 bundle 경로와 Nanobot wrapper 기본 경로를 모두 `~/Applications/PhotosMcp.app` 로 맞췄다. local build bundle 이 선택되면 explicit override 가 없는 한 사용자 앱 폴더 설치본을 자동 갱신한 뒤 그 경로로 실행한다.
- [x] wrapper 실행 후 cleanup: PhotosMcp process, Terminal helper subprocess, 18791 listener 모두 정리 확인.
- [x] wrapper 실행 후 codesign 유지: `codesign --verify --deep --strict dist-framework-standalone/PhotosMcp.app` 통과.
- [x] final source suite: `uv run pytest -q` 56개 통과.

추가로 bundle preflight 에서 발견된 vendored Terminal mode `sys` import 누락을 수정하고 `tests/test_vendor_terminal_modes.py` 로 회귀 테스트를 추가했다. Terminal helper timeout 시 subprocess 가 남지 않도록 PID cleanup 을 추가했고, Terminal.app 새 shell 에서 signed bundle 내부에 `.pyc` 를 쓰지 않도록 helper env 에 `PYTHONDONTWRITEBYTECODE=1` 을 명시한다.

## 5. 당장 하지 않을 일

- preflight 메시지만 바꿔서 error 를 숨기지 않는다.
- bundle 내부 파일을 직접 수정하지 않는다.
- sibling repo 의 코드를 동시에 리팩터링하지 않는다.
- Nanobot 에 photos-mcp 전용 orchestration 을 추가하지 않는다.
- Apple Photos 권한 문제와 MCP transport 문제를 한 커밋에서 섞지 않는다.

## 6. 다음 실제 작업의 시작점

Phase 1-6 의 1차 정리와 standalone bundle 재검증은 완료됐다. 다음 작은 단위는 기능 확장보다 운영 연결 검증과 권한 상태 정리 쪽이 적절하다.

1. Nanobot MCP client 설정에서 `http://127.0.0.1:18791/mcp` 연결을 실제 Nanobot request flow 경유로 확인한다.
2. wrapper를 launchd 또는 운영 시작 흐름에 붙일 때는 `run-photos-mcp-app.sh` dry-run payload 와 `/health` payload 를 같이 남긴다.
3. 이후 새 기능은 `PhotoRankerJobStore` 같은 adapter 경계를 먼저 만든 뒤 UI/health 에 projection 하는 순서로 추가한다.
