# facade tool contracts

## 1. 목적

이 문서는 `photos-mcp` 의 public MCP surface 를 4개 facade tool 로 줄일 때의 1차 계약 초안을 정의한다. 목표는 LLM 이 가능한 적은 수의 tool 과 가능한 안정적인 입력 shape 로 대부분의 사용자 요청을 처리하게 만드는 것이다.

이 문서는 구현용 schema 코드가 아니라 계약 문서다. 실제 Python 타입과 MCP tool signature 는 이 문서를 기준으로 다음 단계에서 좁힌다.

## 2. 공통 설계 원칙

- 기본 source 는 `apple` 이다.
- 입력은 action 또는 intent 중심으로 받는다.
- default option 을 강하게 둔다.
- 세부 job id 조작보다 current/latest 실행 흐름을 우선한다.
- 복잡한 low-level tool sequence 는 앱 내부 orchestration 으로 숨긴다.

## 3. `photos_status`

### 역할

- 앱 상태 확인
- transport health 확인
- capability readiness 확인
- 현재 실행 중 작업 요약 확인
- 마지막 실행 결과 요약 확인

### 권장 입력

```json
{
  "view": "summary"
}
```

허용 `view`:

- `summary`
- `checks`
- `running`
- `latest`

기본값:

- `view=summary`

### 권장 출력

```json
{
  "status": "ok",
  "transport": {
    "status": "ok",
    "endpoint": "http://127.0.0.1:18791/mcp"
  },
  "capabilities": {
    "status": "ok",
    "checks": []
  },
  "running": {
    "active": false,
    "count": 0,
    "current_run_id": ""
  },
  "latest": {
    "run_id": "",
    "intent": "",
    "status": "idle"
  }
}
```

### 설계 메모

- 기존 `health_status` 보다 app workflow 상태를 더 전면에 둔다.
- `list_jobs` 같은 detailed inventory 는 여기서 대체하지 않는다.
- current/latest 중심으로 요약해서 LLM 의 분기 수를 줄인다.

## 4. `photos_library`

### 역할

- 사진 목록 조회
- 키워드 검색
- 특정 사진 inspect

### 권장 입력

```json
{
  "action": "list",
  "source": "apple",
  "album": "최근",
  "limit": 20,
  "include": ["thumbnail"]
}
```

허용 `action`:

- `list`
- `ready_only`
- `search`
- `inspect`

핵심 입력 필드:

- `source`
- `query`
- `album`
- `person`
- `date_from`
- `date_to`
- `photo_id`
- `limit`
- `include`

`include` 예시:

- `thumbnail`
- `metadata`

기본값:

- `source=apple`
- `limit=20`
- `include=[]`

### 권장 출력

```json
{
  "action": "list",
  "source": "apple",
  "count": 3,
  "analyze_ready_count": 0,
  "download_required_count": 3,
  "items": [
    {
      "id": "E110221C-1753-4C25-B993-43605377B6B2",
      "filename": "E110221C-1753-4C25-B993-43605377B6B2.jpeg",
      "date_taken": "2011-12-10T15:07:00+00:00",
      "source": "apple",
      "path": "",
      "photo_id": "E110221C-1753-4C25-B993-43605377B6B2",
      "local_path_available": false,
      "analyze_recommended": false,
      "recommended_next_action": "download_in_photos_then_run",
      "download_hint": "Open the asset in Photos and wait for the original to download locally, then rerun photos_library and confirm local_path_available=true before photos_run(intent=\"analyze\").",
      "vendor_source": "apple_photos"
    }
  ],
  "next_suggested_action": "inspect_or_download"
}
```

### 설계 메모

- `list_photos`, `get_metadata`, `get_thumbnail`, `search_photos` 를 하나로 묶는다.
- `export_photos` 는 browse 계열이 아니라 output action 이므로 `photos_run(intent="export")` 또는 `photos_result(action="artifacts")` 로 흡수하는 편이 낫다.
- `photo_id` 를 다음 `photos_run` 의 입력 anchor 로 이어 주는 것이 중요하다.
- `local_path_available` 는 facade 가 `path` 존재 여부를 정규화한 힌트로, analyze 전에 현재 선택 사진이 로컬 export 후보인지 빠르게 판단할 때 쓴다.
- Apple Photos live 응답에서 `path` 가 비어 있으면 `local_path_available=false` 로 내려오며, 이 경우 현재 설치본에서는 `photos_run(intent="analyze")` 가 `selected_photo_not_local` 로 막힐 수 있다.
- `analyze_ready_count` 와 `download_required_count` 는 browse 결과에서 바로 analyze 가능한 항목과 Photos 앱에서 먼저 로컬 다운로드가 필요한 항목을 빠르게 구분하기 위한 요약이다.
- item-level `recommended_next_action` 은 `photos_run` 또는 `download_in_photos_then_run` 으로 내려오며, Apple Photos 에서는 `download_hint` 를 함께 읽으면 다음 운영 동작이 더 명확하다.
- `action="ready_only"` 는 `local_path_available=true` 인 항목만 남겨서 analyze-ready 후보만 바로 보게 하는 facade 필터다.

## 5. `photos_run`

### 역할

- 단건 분석 실행
- 분류 workflow 실행
- curate workflow 실행
- organize workflow 실행
- import workflow 실행

### 권장 입력

```json
{
  "intent": "analyze",
  "source": "apple",
  "photo_id": "E110221C-1753-4C25-B993-43605377B6B2",
  "wait_for_local": true,
  "wait_timeout_seconds": 120,
  "wait_poll_interval_seconds": 3
}
```

허용 `intent`:

- `analyze`
- `classify`
- `curate`
- `organize`
- `import`

핵심 입력 필드:

- `source`
- `photo_id` 또는 `photo_ids`
- `scope`
- `options`
- `target`

권장 기본값:

- `source=apple`
- `selection_profile=general`
- `wait=false` for long-running intents
- `wait=true` for short analyze intents
- `wait_for_local=false`
- `wait_timeout_seconds=120`
- `wait_poll_interval_seconds=3`

### intent 별 기대 동작

`analyze`

- 단건 또는 소수 사진 분석
- 내부적으로 `score_quality`, `describe_scene`, `classify_event`, 필요 시 `detect_faces` 조합
- Apple Photos 에서 현재 선택 사진이 iCloud-only 라면 `wait_for_local=true` 로 background wait run 을 만들 수 있다.
- 이 경우 첫 응답은 `status=running`, `wait_status=waiting_for_local_download`, `next_suggested_action=photos_result` 형태로 내려오고, 이후 `photos_result(action="summary"|"result")` 로 상태를 조회한다.
- startup preflight 가 warning 이어도 wait run 자체는 시작될 수 있으며, 이 경우 summary payload 에 `permission_warning=true` 가 함께 내려올 수 있다.

`classify`

- background classify 실행
- 내부적으로 source read + `start_classify_job` + state update

`curate`

- 잘 나온 사진 선택 또는 review-ready 결과 생성
- 내부적으로 `curate_best_photos` 또는 classify + selection 조합

`organize`

- Apple Photos album 또는 local directory 구조로 결과 반영
- 내부적으로 `organize_results`, `organize_results_to_directory`, `create_album`, `add_to_album` 조합

`import`

- 외부 파일 import 와 후속 정리
- 내부적으로 `import_photos`, `import_and_organize` 조합

### 권장 출력

```json
{
  "run_id": "run_123",
  "intent": "analyze",
  "status": "running",
  "wait_status": "waiting_for_local_download",
  "permission_warning": true,
  "result_available": false,
  "next_suggested_action": "photos_result"
}
```

### 설계 메모

- 이 tool 이 facade 구조의 중심이다.
- 기존에는 모델이 여러 tool 을 조합했지만, 앞으로는 앱이 내부에서 필요한 하위 tool 을 조합한다.
- `wait_for_local=true` 인 `analyze` 는 vendor job 이 아니라 facade synthetic run 으로 저장된다. 따라서 `photos_status` 와 `photos_result` 에서는 일반 job 처럼 보이지만, 실제 내부에서는 Apple Photos local download poll 후 analyze 로 이어지는 앱-owned orchestration 이다.
- `run_id` 는 job id 와 1:1 일 수도 있고, short analyze 에서는 synthetic id 일 수도 있다.

## 6. `photos_result`

### 역할

- 현재 또는 마지막 실행 결과 확인
- 요약 결과, 상세 결과, 산출물 확인
- 필요 시 간단한 cancel 수행

### 권장 입력

```json
{
  "action": "summary",
  "run_id": "latest"
}
```

허용 `action`:

- `summary`
- `result`
- `artifacts`
- `selected`
- `cancel`

기본값:

- `run_id=latest`
- `action=summary`

### 권장 출력

```json
{
  "run_id": "run_123",
  "intent": "classify",
  "status": "completed",
  "summary": {
    "photo_count": 84,
    "selected_count": 18
  },
  "artifacts": {
    "preview_path": "..."
  }
}
```

### 설계 메모

- `get_job_summary`, `get_job_result`, `get_review_items`, 일부 `cancel_job` 흐름을 흡수한다.
- `delete_job`, `clear_job_history` 는 public facade 에 포함하지 않는다.
- 운영성 청소 작업은 app UI 로 남긴다.

## 7. v1 에서 public surface 에 넣지 않을 범위

아래 기능은 1차 facade 에서 제외하는 것이 맞다.

- `register_face`
- `list_known_faces`
- `register_face_from_job`
- `delete_known_face`
- `list_photo_faces`
- `label_face_in_job`
- `set_photo_review`
- `delete_job`
- `clear_job_history`
- `list_jobs`

제외 이유:

- 일반 사용자 요청보다 운영/debug/review 고급 흐름에 가깝다.
- tool 개수보다 schema 복잡도를 크게 늘린다.
- app UI 에 이미 일부 대체 제어가 있다.

## 8. 이후 결정이 필요한 항목

- `export_photos` 를 `photos_library` 에 둘지 `photos_run` 으로 옮길지
- `photos_result(action="selected")` 가 review item 을 얼마나 자세히 줄지
- `photos_run(intent="analyze")` 의 short sync 응답과 `photos_result` 조회를 어떻게 구분할지
- `run_id` 를 job id 와 통합할지, facade-level synthetic id 로 둘지

이 문서는 1차 기준선을 정의한다. 실제 구현에서는 먼저 단순하고 좁은 JSON shape 로 시작하는 편이 낫다.