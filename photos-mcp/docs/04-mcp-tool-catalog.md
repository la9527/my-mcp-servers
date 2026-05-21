# MCP tool catalog

## 1. 개요

현재 `photos-mcp` 가 기본 MCP public surface 로 노출하는 tool 은 4개다.

- `photos_status`
- `photos_library`
- `photos_run`
- `photos_result`

기존 `photo-source`, `photo-ranker` 의 세부 tool 은 내부 구현 detail 로 유지되지만 기본 `list_tools` 에서는 직접 노출하지 않는다.

이 문서는 먼저 public tool 4개를 설명하고, 그 뒤에 내부 legacy 기능이 어떤 층으로 내려갔는지를 요약한다. 상세 매핑은 `08-legacy-to-facade-mapping.md` 를 본다.

## 2. public facade tools

### `photos_status`

- 역할: app 상태, transport health, capability readiness, current/latest 실행 요약 반환
- 대표 view: `summary`, `checks`, `running`, `latest`
- 언제 쓰나: 최초 연결 진단, 현재 실행 상태 확인, latest run 존재 여부 확인

### `photos_library`

- 역할: 사진 browse, search, inspect
- 대표 action: `list`, `search`, `inspect`
- 내부 계층: 필요에 따라 `photo-source` 함수 호출
- 언제 쓰나: source access, `photo_id` 선택, 분석 전 입력 anchor 확보

### `photos_run`

- 역할: analyze, classify, curate, organize, import 같은 high-level workflow 실행
- 대표 intent: `analyze`, `classify`, `curate`, `organize`, `import`
- 내부 계층: `photo-source` 와 `photo-ranker` 호출을 app 이 orchestration
- 언제 쓰나: 실제 작업 실행 전반

### `photos_result`

- 역할: current/latest 실행 summary, result, artifacts, selected items 확인과 간단한 cancel
- 대표 action: `summary`, `result`, `artifacts`, `selected`, `cancel`
- 내부 계층: `photo-ranker` 의 job/result 계층을 facade 형태로 노출
- 언제 쓰나: 실행 후 결과 조회와 후속 산출물 확인

## 3. internal legacy tool groups

### `photo-source` internal functions

- `list_photos`
- `get_metadata`
- `get_thumbnail`
- `search_photos`
- `export_photos`

현재 public surface 에서는 주로 `photos_library` 와 일부 `photos_run` 경로로 흡수된다.

### `photo-ranker` internal functions

분석:

- `score_quality`
- `detect_faces`
- `describe_scene`
- `classify_event`
- `find_duplicates`
- `rank_best_shots`

jobs:

- `start_classify_job`
- `get_job_status`
- `get_job_summary`
- `get_job_result`
- `cancel_job`

review / write-back / workflow:

- `get_review_items`
- `export_selected_photos`
- `curate_best_photos`
- `organize_results`
- `organize_results_to_directory`
- `import_photos`
- `import_and_organize`
- `classify_and_organize`

이 함수들은 현재 public tool 이름이 아니라 `photos_run` 과 `photos_result` 의 내부 substep 으로 주로 사용된다.

## 4. public 과 internal 의 경계

### public facade 가 직접 책임지는 것

- 4개 tool schema 유지
- default option 적용
- current/latest 중심 상태 요약
- internal workflow orchestration
- job payload normalization

### `photo-source` 가 직접 책임지는 것

- source browse
- metadata
- thumbnail
- search
- export

### `photo-ranker` 가 직접 책임지는 것

- 분석
- 분류
- review
- write-back
- workflow orchestration

즉, `photos-mcp` 의 핵심 구조는 “작은 public surface + 넓은 internal implementation” 이다.

## 5. 처음 호출해 볼 때 추천하는 순서

1. `photos_status`
2. `photos_library(action="list")`
3. `photos_library(action="inspect")`
4. `photos_run(intent="analyze")`
5. `photos_run(intent="classify")`
6. `photos_result(action="summary")`
7. `photos_result(action="result")`
8. `photos_run(intent="organize")`

이 순서는 진단, source access, 단건 분석, classify workflow, result 조회, write-back 을 차례로 검증하기에 좋다.