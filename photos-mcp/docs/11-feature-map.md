# photos-mcp 기능 맵

## 1. 외부에 노출되는 표면

### endpoint

- MCP: `http://127.0.0.1:18791/mcp`
- health: `http://127.0.0.1:18791/health`
- health capabilities: `http://127.0.0.1:18791/health/capabilities`

### facade tools

- `photos_query`
- `photos_select`
- `photos_write`
- `photos_workflow`

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

- `photos_query`: guide/status/list/search/inspect/result/cancel/resume_plan
- `photos_select`: analyze/classify/select
- `photos_write`: 승인 기반 album/export/import/cleanup
- `photos_workflow`: 승인 기반 curate/organize/import workflow와 중단 run 재실행

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

현재 public surface는 이 job 계열을 직접 노출하지 않고 `photos_select`, `photos_workflow`, `photos_query` 뒤로 숨긴다. MCP 응답과 menu UI가 같은 state store를 읽으면서도 LLM에는 단순한 facade surface만 보이기 위해서다.

### 2.7 Vision runtime과 쓰기 승인

- 기본 VLM: Linux `Qwen3.6-35B-A3B-Q4_K_M.gguf`
- 기본 정책: `remote_allowed`, 요청 시 Wake-on-LAN과 SSH tunnel 준비
- 로컬 강제: `PHOTOS_MCP_VLM_POLICY=local_only`
- 상태 확인: `photos_query(action="guide")` 응답의 `vision_runtime`
- 모든 `photos_write`: 실제 사진 대상 plan 확인 후 일회성 `approval_token` 필요
- 분석 workflow: 분석 완료 후 확정 대상 plan과 `next_action`을 승인해야 실제 쓰기 수행
- 실패 workflow: `resume_plan` 확인과 승인 후 같은 `run_id`의 checkpoint에서 재개

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

### 상태 아이콘

- macOS SF Symbol을 우선 사용하며, 지원하지 않는 시스템에서는 `PM` 텍스트로 대체한다.
- 준비됨, 작업 중, 확인 필요, 서버 중지 상태를 아이콘과 tooltip 문구로 함께 구분한다.
- 색상만으로 상태를 전달하지 않는다. 팝오버 첫 문장과 VoiceOver label에도 같은 상태 의미를 포함한다.

### 동적 팝오버

- 상태 요약, 사진 변경 승인, 진행 중인 작업, 최근 작업, 환경 검사 순으로 필요한 섹션만 표시한다.
- 빈 작업·승인·최근 작업 섹션은 표시하지 않으며, 높이는 콘텐츠에 따라 220~620pt 범위에서 조절한다.
- 최근 작업은 최대 3건을 보여 주고, 내부 run ID 대신 사용자 작업명·결과·상대 시각을 표시한다.
- 작업 중에는 단계, 진행률, 취소 동작을 제공한다. 완료된 결과는 읽기 전용 결과 창에서 비식별 preview와 판정 근거를 확인한다.
- 모든 버튼에는 tooltip, accessibility label, 키보드 focus 순서가 있다.

### 보조 화면과 관리 동작

- `···` 관리 메뉴: 서버 시작/중지, 새로 고침, 환경 검사, Photos 권한 설정, 완료·전체 기록 지우기, 종료를 제공한다.
- 환경 검사 창: 기본 권한·보관함 읽기와 선택 thumbnail·앨범 자동화 검사를 분리해 보여 준다. 선택 검사의 미실행 상태는 중립적으로 표현하며, 전체 재검사와 개인정보를 제외한 진단 정보 복사를 제공한다.
- 사진 변경 검토: 앨범 이름, 사진 수, 비식별 preview를 확인한 뒤에만 승인·거절할 수 있다.
- 사진 결과 창: 추천·보관·검토 수를 요약하고 filter, 항목 상세, Finder에서 preview 보기, 안전한 요약 복사를 제공한다. 여기서 Apple Photos 변경은 수행하지 않는다.

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
