# photos-mcp 기능 맵

## 1. 외부에 노출되는 표면

### endpoint

- MCP: `http://127.0.0.1:18791/mcp`
- health: `http://127.0.0.1:18791/health`
- health capabilities: `http://127.0.0.1:18791/health/capabilities`

### 진단 tool

- `photos_status`

### facade tools

- `photos_status`
- `photos_library`
- `photos_run`
- `photos_result`

### app shell

- `PhotosMcp.app`
- menu bar status item
- popover 기반 운영 UI

### CLI 성격 진단 모드

- `PhotosMcp --health`
- `PhotosMcp --version`
- `PhotosMcp --help`

## 2. 기능 범주

### 2.1 Public MCP facade

대표 기능:

- `photos_status`: 상태/health/current/latest 실행 요약
- `photos_library`: browse/search/inspect
- `photos_run`: analyze/classify/curate/organize/import
- `photos_result`: summary/result/artifacts/selected/cancel

### 2.2 Internal photos source access (`photo-source` 계열)

대표 기능:

- `list_photos`
- `get_metadata`
- `get_thumbnail`
- `search_photos`
- `export_photos`

지원 소스:

- Apple Photos
- local folder
- Google Photos
- GCS

실무상 현재 핵심은 Apple Photos read path 다.

### 2.3 Internal photo analysis / ranking (`photo-ranker` 계열)

대표 기능:

- quality scoring
- face detection
- scene description
- event classification
- duplicate detection
- best-shot ranking

대표 tool 예시:

- `score_quality`
- `detect_faces`
- `describe_scene`
- `classify_event`
- `find_duplicates`
- `rank_best_shots`

### 2.4 internal face / review / curation

대표 기능:

- known face 등록/조회/삭제
- review item 조회
- photo review 상태 저장
- face labeling

### 2.5 internal background job 관리

대표 기능:

- `start_classify_job`
- `get_job_status`
- `get_job_summary`
- `get_job_result`
- `cancel_job`
- `delete_job`
- `clear_job_history`
- `list_jobs`

현재 public surface 는 이 job 계열을 직접 노출하기보다 `photos_run` 과 `photos_result` 뒤로 숨긴다. 이유는 MCP 응답과 menu UI 가 같은 state store 를 읽으면서도, LLM 에게는 단순한 facade surface 만 보이게 하기 위해서다.

### 2.6 internal Apple Photos write / organize

대표 기능:

- `create_album`
- `add_to_album`
- `organize_results`
- `organize_results_to_directory`
- `import_photos`
- `import_and_organize`
- `list_photo_albums`
- `classify_and_organize`

이 계층은 permission, Terminal helper, bundle import bootstrap 문제와 가장 자주 엮인다.

## 3. menu bar UI 기능

### status text

- `PM`
- `PM*`
- `PM!`
- `PM-`

상태 의미:

- ready / ok 계열
- busy / running 계열
- degraded / error 계열
- stopped 계열

### popover 기능

- daemon / preflight 요약
- endpoint 표시
- `Start` / `Stop`
- `Run Checks`
- `Refresh`
- `Quit`
- active jobs 최대 2개 표시
- recent terminal jobs 표시
- active job cancel
- recent history delete
- recent history clear all

## 4. health payload 의미

health payload 는 단순 alive probe 이상이다. 아래를 같이 담는다.

- `status`
- `transport.status`
- `daemon_status`
- `preflight_status`
- `preflight_checks`
- `capabilities`
- `background_job_running`
- `active_job_count`
- `recent_job_count`
- endpoint / health_endpoint

따라서 `status=ok` 라고 해도 내부 `preflight_checks` 또는 `/health/capabilities` 를 별도로 읽어야 Apple Photos read/write readiness 를 판단할 수 있다.

## 5. 테스트 범위

### config

- HTTP host/port/path override
- 기본 bundle/runtime/cache path 계약과 `~/.photos-mcp` 앱 전용 root

### main/server

- `--health`
- single-instance lock
- health tool / health endpoint
- vendored tool registration
- vendor root fallback

### state

- active/recent job 분리
- progress field normalization
- stopped state preservation
- preflight snapshot aggregation

### daemon

- cancel / delete / clear history

### preflight

- photos read success/failure
- automation warning downgrade
- lightweight probe 우선 사용

### packaging

- bundle 이름 정규화
- stale build root cleanup
- resource staging 시 `.venv`, `__pycache__` 제외

### MCP client smoke

- mock MCP client 로 health tool 호출
- job payload normalization 과 state update 검증

## 6. 현재 구조의 제약

- macOS / AppKit / Apple Photos permission path 에 강하게 묶여 있다.
- vendored runtime 은 package alias 기반으로 정리됐지만, source/bundle 공용 import bootstrap 과 packaging contract 는 여전히 민감하다.
- bundle 문제는 source 실행에서 재현되지 않을 수 있다.
- preflight 의 `photos_read`, `photos_automation`, `photos_thumbnail` 은 transport success 와 별개의 문제다.
- single-instance lock 이 stale 하게 남으면 app 이 실행되지 않은 것처럼 보일 수 있다.
