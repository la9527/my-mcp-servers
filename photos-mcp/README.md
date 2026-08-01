# photos-mcp

`photos-mcp` 는 macOS 에서 Apple Photos 중심의 사진 조회, 분석, 분류, 정리 작업을 하나의 MCP server 로 제공하는 독립 실행형 앱이다. 내부적으로는 `photo-source` 와 `photo-ranker` 를 vendor tree 로 포함하고, 외부에는 `PhotosMcp.app` 하나와 `http://127.0.0.1:18791/mcp` 하나만 노출한다.

핵심 포인트는 단순하다.

- 사용자는 `PhotosMcp.app` 을 실행한다.
- 앱은 localhost 에 MCP daemon 을 연다.
- MCP client 는 `photos-mcp` 하나에만 붙는다.
- 내부에서는 조회 계층 `photo-source` 와 분석/분류 계층 `photo-ranker` 가 호출된다.

## 이 프로젝트로 할 수 있는 일

- Apple Photos, local folder, Google Photos, GCS 에서 사진 목록을 읽는다.
- 사진 메타데이터와 thumbnail 을 가져온다.
- 품질 점수, 얼굴, 장면, 이벤트, 중복, best shot 분석을 수행한다.
- background classify job 을 시작하고 상태와 결과를 조회한다.
- 검토용 review metadata 를 만들고 선택 결과를 내보낸다.
- Apple Photos album 생성, 사진 추가, 분류 결과 write-back 을 수행한다.
- `classify_and_organize`, `curate_best_photos` 같은 end-to-end workflow 를 실행한다.

현재 기본 MCP public surface 는 4개 facade tool 이다.

- `photos_query`
- `photos_select`
- `photos_write`
- `photos_workflow`

어떤 도구와 action을 써야 할지 모르면 `photos_query(action="guide", options={"goal": "overview"})`로 시작한다. Apple Photos에서 선택한 사진이 iCloud-only이면 `photos_select(action="analyze_photo", options={"wait_for_local": true, ...})`로 background wait run을 만들고, 이후 `photos_query(action="result_summary"|"result_detail")`로 조회하거나 `photos_query(action="cancel")`로 중단한다.

사진 분석 기본 VLM은 Linux 워크스테이션의 `Qwen3.6-35B-A3B-Q4_K_M.gguf`다. 첫 분석 요청이 Linux 깨우기, llama.cpp 준비와 SSH 터널 연결을 자동 수행한다. 원격 전송을 금지하려면 PhotosMcp 앱을 `PHOTOS_MCP_VLM_POLICY=local_only` 환경으로 다시 실행한다.

기존 `photo-source`, `photo-ranker` 의 세부 tool 은 내부 implementation detail 로 유지되며, 기본 `list_tools` 에서는 직접 노출하지 않는다.

## 전체 구조를 한 문장으로 보면

`PhotosMcp.app` 이 실행 주체이고, `server.py` 가 unified MCP layer 를 만들며, `vendor_loader.py` 가 `photo-source` 와 `photo-ranker` 를 적재하고, `daemon.py` 와 `state.py` 가 앱 UI와 health 상태를 유지한다.

자세한 흐름은 아래 문서를 먼저 보면 된다.

- 전체 구조: `docs/01-architecture.md`
- 조회 계층: `docs/02-photo-source.md`
- 분석/분류 계층: `docs/03-photo-ranker.md`
- 전체 tool 목록: `docs/04-mcp-tool-catalog.md`
- 실제 호출 흐름 예시: `docs/05-mcp-call-flows.md`
- MCP surface 축소 방향: `docs/06-tool-surface-simplification-direction.md`

## 가장 빠른 성공 기준

성공 기준은 아래 3개다.

1. `PhotosMcp.app` 이 정상 실행된다.
2. `http://127.0.0.1:18791/health` 가 응답한다.
3. MCP client에서 `photos_query(action="status")`와 `photos_query(action="guide")`가 성공한다.

기본 endpoint 는 아래와 같다.

- MCP endpoint: `http://127.0.0.1:18791/mcp`
- health endpoint: `http://127.0.0.1:18791/health`
- capabilities endpoint: `http://127.0.0.1:18791/health/capabilities`

빠른 로컬 확인:

```bash
./.venv/bin/python -m photos_mcp.main --health
curl -fsS http://127.0.0.1:18791/health
curl -fsS http://127.0.0.1:18791/health/capabilities
```

health 해석 기준:

- top-level `status` 는 transport readiness 다.
- Apple Photos 접근 가능 여부는 `/health/capabilities` 와 `preflight_checks` 를 함께 봐야 한다.
- `ready` 또는 `busy` 면 daemon transport 는 정상으로 본다.

## `photos-mcp` 가 내부에서 하는 일

대표 호출 흐름은 아래와 같다.

1. MCP client 가 facade tool 을 호출한다.
2. `server.py`가 `photos_query`, `photos_select`, `photos_write`, `photos_workflow` 중 하나를 받는다.
3. facade layer 가 필요한 vendor runtime 을 준비하고 내부 substep 을 결정한다.
4. `photo-source` 또는 `photo-ranker` 함수가 실제 작업을 수행한다.
5. job 관련 응답이면 facade layer 가 공통 envelope 로 정규화하고 `PhotosMcpStateStore` 를 갱신한다.
6. menu bar UI 와 health payload 가 같은 state snapshot 을 읽는다.

`wait_for_local=true`인 analyze는 예외다. 이 경우 vendor job queue 대신 facade synthetic run이 state store에 저장되고, app이 Apple Photos local download 가능 여부를 polling하다가 준비되면 analyze를 자동으로 이어서 수행한다.

`photos_write`와 `photos_workflow`는 첫 호출에서 실행하지 않고 `mutation_plan`과 `approval_token`을 반환한다. 사용자가 계획을 확인하고 승인한 경우에만 같은 action과 변경되지 않은 options에 token을 추가해 다시 호출한다. token은 15분 동안 한 번만 유효하며 options가 바뀌면 거부된다.

즉, `photos-mcp` 는 단순 proxy 가 아니라 아래 역할까지 같이 맡는다.

- vendor runtime bootstrap
- 4-tool facade MCP surface 노출
- internal workflow orchestration
- job 응답 정규화
- app UI 와 health state projection
- macOS app lifecycle 관리

## 문서 읽는 순서

처음 보는 사용자라면 아래 순서가 가장 빠르다.

1. `docs/01-architecture.md`
2. `docs/02-photo-source.md`
3. `docs/03-photo-ranker.md`
4. `docs/04-mcp-tool-catalog.md`
5. `docs/05-mcp-call-flows.md`
6. `docs/06-tool-surface-simplification-direction.md`

운영/구현 관점에서는 아래 문서가 기준이다.

- 문서 인덱스: `docs/README.md`
- runtime 상세: `docs/12-runtime-lifecycle.md`
- build와 smoke: `docs/13-build-and-validation.md`
- LLM 연결 샘플: `docs/18-llm-integration-sample-tests.md`
- 기능 참조: `docs/11-feature-map.md`
- 디버깅 순서: `docs/14-debugging-guide.md`
- 리팩터링 방향: `docs/15-refactor-direction.md`
- 개선된 사용법: `docs/20-usage-guide.md`

## 서브시스템 요약

### `photo-source`

사진을 읽어 오는 계층이다.

- 내부 주요 tool: `list_photos`, `get_metadata`, `get_thumbnail`, `search_photos`, `export_photos`
- 주요 소스: `apple`, `local`, `google`, `gcs`
- 역할: 사진 목록, 상세 메타데이터, thumbnail, 검색, export

### `photo-ranker`

사진을 분석하고 분류하고 정리하는 계층이다.

- 내부 analysis: `score_quality`, `detect_faces`, `describe_scene`, `classify_event`, `find_duplicates`, `rank_best_shots`
- 내부 jobs: `start_classify_job`, `get_job_status`, `get_job_summary`, `get_job_result`, `cancel_job`, `delete_job`, `clear_job_history`, `list_jobs`
- 내부 review/write-back: `get_review_items`, `set_photo_review`, `create_album`, `add_to_album`, `organize_results`, `list_photo_albums`
- 내부 end-to-end: `classify_and_organize`, `curate_best_photos`, `import_and_organize`

## macOS 앱으로서의 동작

`PhotosMcp.app` 은 `~/Applications/PhotosMcp.app` 에 설치되는 일반 macOS app 이다. Finder, Launchpad, Dock 에 나타날 수 있고, 동시에 menu bar status item UI 를 제공한다.

menu bar popover 에서는 아래를 다룬다.

- daemon 상태와 endpoint
- preflight 결과
- `Start`, `Stop`, `Run Checks`, `Refresh`, `Quit`
- active jobs 최대 2개
- recent terminal job history
- job cancel/delete/clear

앱 자체의 runtime ownership 은 `~/.photos-mcp` 아래로 통일된다.

- home: `~/.photos-mcp`
- runtime: `~/.photos-mcp/runtime`
- cache: `~/.photos-mcp/cache`
- logs: `~/.photos-mcp/logs`

## build 및 검증 기준

대표 검증 명령:

```bash
./.venv/bin/python -m photos_mcp.main --health
curl -fsS http://127.0.0.1:18791/health
./.venv/bin/pytest tests/test_main.py tests/test_config.py tests/test_state.py tests/test_packaging.py tests/test_preflight.py tests/test_daemon.py -q
uv run pytest -q
```

standalone build 와 bundle smoke 는 `docs/13-build-and-validation.md` 를 기준으로 본다.

## 코드 기준 빠른 맵

- `src/photos_mcp/server.py`: facade FastMCP server, `/health`, 4개 public tool export
- `src/photos_mcp/facade/`: facade action 검증, 조회, 실행, 결과와 사용 가이드
- `src/photos_mcp/vision_runtime.py`: Linux Qwen3.6 기본 VLM과 `local_only` 정책
- `src/photos_mcp/mutation_approval.py`: 쓰기 및 workflow plan 승인 gate
- `src/photos_mcp/vendor_loader.py`: `photo-source`, `photo-ranker` runtime loader
- `src/photos_mcp/daemon.py`: uvicorn daemon controller
- `src/photos_mcp/menu_app.py`: menu bar app UI
- `src/photos_mcp/state.py`: daemon/preflight/job snapshot store
- `src/photos_mcp/job_state.py`: `photo-ranker` DB/queue adapter
- `src/photos_mcp/runtime_bootstrap.py`: source/bundle 공용 bootstrap
- `src/photos_mcp/vendor/photo-source/`: 사진 조회 계층
- `src/photos_mcp/vendor/photo-ranker/`: 분석/분류/정리 계층

현재 문서의 자세한 분류와 읽는 순서는 `docs/README.md` 에 정리해 두었다.
