# PhotosMcp Public MCP Tool Surface Redesign Phase 1

> 2026-05-23 기준 이 문서는 `photos-mcp` 의 LLM-facing MCP public surface 를 다시 설계하기 위한 canonical planning 문서다. 핵심 목표는 기존 `photos_status`, `photos_library`, `photos_run`, `photos_result` 를 더 명확한 4개 group tool 로 교체하고, 각 tool 이 `action`, `options` 두 입력만 받도록 하되 action 별 허용 option 을 서버가 강제하는 것이다.

## 왜 다시 tool surface 를 바꾸나

현재 public surface 는 tool 개수만 보면 4개로 작지만, 실제로는 `photos_run` 하나가 analyze, classify, curate, organize, import, cleanup 성격의 작업을 모두 받는다. 이 구조에서는 서로 다른 intent 의 파라미터가 한 평면에 놓인다.

특히 최근 Nanobot WebUI live 검증에서 아래 문제가 반복됐다.

- 사용자는 "잘 나온 사진만 단일 앨범에 저장"을 요청했다.
- 모델은 `target_album_name` 을 넣었지만 `writeback_mode="review"` 로 선별만 수행했다.
- 이후 `photos_run(intent="organize", writeback_mode="apply", album_prefix="AI 분류")` 를 호출했다.
- 결과적으로 요청한 단일 앨범 대신 `AI 분류 - travel`, `AI 분류 - outdoor` 같은 분류 앨범들이 생성됐다.

즉 문제는 단순히 prompt guidance 부족이 아니라, public tool schema 자체가 서로 섞이면 안 되는 실행 경로를 같은 호출 공간에 열어 둔 데 있다. 이번 redesign 은 LLM 이 실수할 수 있는 조합을 줄이는 것이 아니라, 서버 contract 상 불가능하게 만드는 데 초점을 둔다.

## 목표

1. public MCP tool 은 다시 4개로 유지하되 이름과 역할을 group 단위로 재정의한다.
2. 각 public tool 은 입력을 `action`, `options` 두 개로 고정한다.
3. `options` 는 action 별 allowlist 로 검증한다.
4. action 에 맞지 않는 option 은 실행 전에 structured error 로 거부한다.
5. 단일 앨범 write-back 과 카테고리 organize 는 서로 다른 action 으로 분리한다.
6. 원샷 workflow 는 앞으로 여러 개가 늘어날 수 있으므로 `photos_workflow(action, options)` 안에서 action catalog 로 확장한다.
7. 기존 `photos_status`, `photos_library`, `photos_run`, `photos_result` public tools 는 legacy 호환 없이 제거한다.
8. 내부 vendor 함수와 기존 facade 구현은 최대한 재사용하되, 외부 MCP contract 는 새 router/validator 를 기준으로 다시 고정한다.
9. 구현 뒤 `docs/04-mcp-tool-catalog.md`, `docs/05-mcp-call-flows.md` 를 포함한 기존 public contract 문서를 반드시 갱신한다.

## 비범위

- Apple Photos adapter 전체 재작성
- `photo-ranker` scoring 정책 변경
- Nanobot 내부에 PhotosMcp 전용 natural-language router 추가
- legacy `photos_run` 호환 shim 유지
- 기존 자동 분류 앨범 동작 자체 제거
- WebUI 전용 UI 변경

자동 분류 앨범 기능은 유지한다. 다만 단일 앨범 write-back 요청과 같은 action 에 공존하지 않게 한다.

## phase-1 고정 결정

새 public tool 이름은 아래 4개로 고정한다.

1. `photos_query`
2. `photos_select`
3. `photos_write`
4. `photos_workflow`

각 tool signature 는 아래 shape 를 따른다.

```python
photos_query(action: str, options: dict = {})
photos_select(action: str, options: dict = {})
photos_write(action: str, options: dict = {})
photos_workflow(action: str, options: dict = {})
```

`action` 은 string enum 처럼 취급한다. MCP schema 가 conditional object schema 를 안정적으로 표현하지 못하더라도, 서버는 action 별 option allowlist, required fields, type/default normalization 을 직접 수행한다.

잘못된 요청은 vendor 호출로 내려가지 않고 아래 형태의 structured error 를 반환한다.

```json
{
  "status": "blocked",
  "error_code": "invalid_options_for_action",
  "error": "Option is not allowed for this action",
  "tool": "photos_workflow",
  "action": "curate_to_album",
  "invalid_options": ["album_prefix"],
  "allowed_options": ["source", "date_from", "date_to", "limit", "selection_profile", "exclude_screenshots", "target_album_name", "wait_for_local"],
  "next_suggested_action": "retry_with_allowed_options"
}
```

## 상위 설계 원칙

### 1. group tool 은 역할 경계를 뜻한다

- `photos_query`: 읽기 전용 진단/조회
- `photos_select`: 분석과 선별, 쓰기 없음
- `photos_write`: 이미 있는 결과를 외부 출력이나 Apple Photos 에 반영
- `photos_workflow`: 여러 단계를 한 번에 수행하는 의도 단위 workflow

### 2. action 은 workflow 의미를 뜻한다

예를 들어 `photos_workflow(action="curate_to_album")` 은 "잘 나온 사진을 선별해 정확히 하나의 앨범에 저장"이라는 완결된 사용자 의도를 뜻한다. 이 action 은 내부적으로 `curate_best_photos(..., writeback_mode="album")` 로 매핑될 수 있지만, public contract 에 `writeback_mode` 를 노출하지 않는다.

### 3. 서로 다른 write-back 모델은 같은 action 에 공존하지 않는다

단일 앨범 저장과 분류 앨범 생성은 같은 `options` 안에서 동시에 표현될 수 없어야 한다.

- 단일 앨범 action 은 `target_album_name` 을 받는다.
- 카테고리 organize action 은 `album_prefix` 를 받는다.
- 단일 앨범 action 은 `album_prefix`, `group_by_date`, `classification` 개념을 받지 않는다.
- 카테고리 organize action 은 `target_album_name` 을 받지 않는다.

### 4. LLM 이 고르면 안 되는 low-level knob 은 숨긴다

`writeback_mode` 는 내부 구현 detail 로 내린다. phase 1 public contract 에서는 아래처럼 action 이 쓰기 의미를 직접 표현한다.

- `curate_to_album`: 내부 `writeback_mode="album"`
- `select_best`: 내부 `writeback_mode="review"`
- `organize_by_category`: 내부 `organize_results` 또는 `classify_and_organize`

### 5. 원샷 workflow 는 action catalog 로 확장한다

앞으로 workflow 가 늘어나도 tool 수를 늘리지 않는다. `photos_workflow` 안에 action 을 추가하고, action 별 option schema 를 추가한다.

## 새 public tool contract

### 1. `photos_query(action, options)`

역할:

- app 상태 확인
- capability readiness 확인
- 사진 목록 조회
- 사진 inspect
- 실행 결과 summary/result/selected/artifacts 조회
- 실행 취소

쓰기 작업은 하지 않는다.

초기 action catalog:

- `status`
- `list`
- `ready_only`
- `search`
- `inspect`
- `result_summary`
- `result_detail`
- `selected`
- `artifacts`
- `cancel`

대표 option allowlist:

```text
status:
  allowed: view
  required: none
  defaults: view="summary"

list, ready_only:
  allowed: source, album, person, date_from, date_to, limit, include_thumbnail, include_metadata, max_size
  required: none
  defaults: source="apple", limit=20, include_thumbnail=false, include_metadata=false, max_size=512

search:
  allowed: source, query, album, person, date_from, date_to, limit, include_thumbnail, include_metadata, max_size
  required: query
  defaults: source="apple", limit=20, max_size=512

inspect:
  allowed: source, photo_id, include_thumbnail, include_metadata, max_size
  required: photo_id
  defaults: source="apple", include_thumbnail=true, include_metadata=true, max_size=512

result_summary, result_detail, selected, artifacts, cancel:
  allowed: run_id, top_n, output_dir, min_score, group_by_date, mode
  required: none for latest-compatible actions, run_id optional default latest
  defaults: run_id="latest", top_n=20, min_score=0, group_by_date=false, mode="copy"
```

내부 매핑:

- `status` -> 기존 `facade_photos_status`
- `list`, `ready_only`, `search`, `inspect` -> 기존 `facade_photos_library`
- `result_summary`, `result_detail`, `selected`, `artifacts`, `cancel` -> 기존 `facade_photos_result`

### 2. `photos_select(action, options)`

역할:

- 단건 분석
- 범위 classify job 시작
- best-shot 선별
- 특정인 중심 best-shot 선별
- local directory 또는 Apple Photos source 에서 selected set 생성

Apple Photos album 쓰기나 local export 를 수행하지 않는다.

초기 action catalog:

- `analyze_photo`
- `classify_range`
- `select_best`
- `select_best_person`

대표 option allowlist:

```text
analyze_photo:
  allowed: source, photo_id, path_or_bucket, prompt, include_faces, max_size, wait_for_local, wait_timeout_seconds, wait_poll_interval_seconds, run_id
  required: photo_id
  defaults: source="apple", include_faces=false, max_size=512, wait_for_local=false, wait_timeout_seconds=120, wait_poll_interval_seconds=3

classify_range:
  allowed: source, source_path, album, person, date_from, date_to, limit, selection_profile
  required: none, but at least one scope field should normally be present for Apple Photos ranges
  defaults: source="apple", limit=50, selection_profile="general"

select_best:
  allowed: source, source_path, album, person, date_from, date_to, limit, selection_profile, exclude_screenshots, wait_for_local, wait_timeout_seconds, wait_poll_interval_seconds
  forbidden: target_album_name, album_prefix, writeback_mode, output_dir, folder, group_by_date, results_json
  defaults: source="apple", limit=50, selection_profile="general", exclude_screenshots=true, wait_for_local=false

select_best_person:
  allowed: source, source_path, person, album, date_from, date_to, limit, selection_profile, exclude_screenshots, wait_for_local, wait_timeout_seconds, wait_poll_interval_seconds
  required: person
  forbidden: target_album_name, album_prefix, writeback_mode, output_dir, folder, group_by_date, results_json
  defaults: source="apple", limit=50, selection_profile="person", exclude_screenshots=true, wait_for_local=false
```

내부 매핑:

- `analyze_photo` -> 기존 `photos_run(intent="analyze")` 경로
- `classify_range` -> 기존 `photos_run(intent="classify")` 경로
- `select_best` -> 기존 `photos_run(intent="curate", writeback_mode="review")` 경로
- `select_best_person` -> 기존 `photos_run(intent="curate", writeback_mode="review", selection_profile="person")` 경로

응답 원칙:

- selected set 이 생기는 action 은 `run_id`, `selected_count`, `selected_photo_ids`, `selection_policy`, `result_available` 를 반환한다.
- 쓰기 결과 필드인 `album_result`, `touched_album_names`, `albums_created` 는 없어야 한다.

### 3. `photos_write(action, options)`

역할:

- 기존 selected 결과를 Apple Photos 단일 앨범에 추가
- 명시 photo id 목록을 Apple Photos 단일 앨범에 추가
- selected 결과를 local directory 로 export
- completed classify 결과를 카테고리별 앨범으로 organize
- local files 를 Apple Photos 로 import
- validation/test album cleanup

초기 action catalog:

- `add_selected_to_album`
- `add_photo_ids_to_album`
- `export_selected`
- `organize_by_category`
- `import_to_album`
- `cleanup_album`

대표 option allowlist:

```text
add_selected_to_album:
  allowed: run_id, target_album_name, folder
  required: run_id, target_album_name
  forbidden: album_prefix, group_by_date, min_score, selection_profile, date_from, date_to, results_json

add_photo_ids_to_album:
  allowed: source, photo_ids, target_album_name, folder
  required: photo_ids, target_album_name
  forbidden: album_prefix, group_by_date, min_score, selection_profile, date_from, date_to, run_id
  defaults: source="apple"

export_selected:
  allowed: run_id, output_dir, top_n, min_score, group_by_date, mode
  required: run_id, output_dir
  defaults: top_n=50, min_score=0, group_by_date=false, mode="copy"

organize_by_category:
  allowed: run_id, album_prefix, folder, min_score, group_by_date
  required: run_id
  forbidden: target_album_name
  defaults: album_prefix="AI 분류", min_score=0, group_by_date=false

import_to_album:
  allowed: photo_paths, target_album_name, folder
  required: photo_paths, target_album_name
  forbidden: album_prefix, results_json, group_by_date

cleanup_album:
  allowed: target_album_name, folder
  required: target_album_name
```

내부 매핑:

- `add_selected_to_album` -> selected results 를 읽어 single album writer 경로로 전달
- `add_photo_ids_to_album` -> photo id 목록을 single album writer 경로로 전달
- `export_selected` -> 기존 `photos_result(action="artifacts")`
- `organize_by_category` -> 기존 `organize_results`
- `import_to_album` -> 기존 `import_photos`
- `cleanup_album` -> 기존 `delete_photo_album`

중요 invariant:

- `add_selected_to_album`, `add_photo_ids_to_album`, `import_to_album` 은 응답에서 `touched_album_names == [target_album_name]` 이어야 한다.
- 위 action 들은 `classification_album_created=false` 를 반환해야 한다.
- `organize_by_category` 는 `target_album_name` 을 받을 수 없고, 여러 category album 생성을 명시적으로 허용하는 action 이다.

### 4. `photos_workflow(action, options)`

역할:

- 사용자 의도 단위의 one-shot workflow 를 수행한다.
- 내부적으로 query, select, write 단계를 조합할 수 있다.
- 앞으로 workflow 가 늘어날 때 public tool 수를 늘리지 않고 action catalog 에 추가한다.

초기 action catalog:

- `curate_to_album`
- `curate_to_directory`
- `classify_then_organize_by_category`
- `import_then_curate_to_album`

대표 option allowlist:

```text
curate_to_album:
  allowed: source, source_path, album, person, date_from, date_to, limit, selection_profile, exclude_screenshots, target_album_name, folder, wait_for_local, wait_timeout_seconds, wait_poll_interval_seconds
  required: target_album_name
  forbidden: writeback_mode, album_prefix, results_json, output_dir, group_by_date
  defaults: source="apple", limit=50, selection_profile="general", exclude_screenshots=true, wait_for_local=false

curate_to_directory:
  allowed: source, source_path, album, person, date_from, date_to, limit, selection_profile, exclude_screenshots, output_dir, min_score, group_by_date, mode, wait_for_local, wait_timeout_seconds, wait_poll_interval_seconds
  required: output_dir
  forbidden: target_album_name, album_prefix, writeback_mode, results_json
  defaults: source="apple", limit=50, selection_profile="general", exclude_screenshots=true, min_score=0, group_by_date=false, mode="copy"

classify_then_organize_by_category:
  allowed: source, source_path, album, person, date_from, date_to, limit, selection_profile, album_prefix, folder, min_score, group_by_date
  required: none, but source scope should normally be present
  forbidden: target_album_name, writeback_mode, results_json, output_dir
  defaults: source="apple", limit=50, selection_profile="general", album_prefix="AI 분류", min_score=0, group_by_date=false

import_then_curate_to_album:
  allowed: photo_paths, target_album_name, selection_profile, exclude_screenshots, folder
  required: photo_paths, target_album_name
  forbidden: album_prefix, writeback_mode, results_json, group_by_date
  defaults: selection_profile="general", exclude_screenshots=true
```

내부 매핑:

- `curate_to_album` -> 기존 `curate_best_photos(..., writeback_mode="album", target_album_name=...)`
- `curate_to_directory` -> best-shot selected set 생성 후 local artifact/export 경로
- `classify_then_organize_by_category` -> 기존 `classify_and_organize` 또는 classify + `organize_results`
- `import_then_curate_to_album` -> import 후 selected/best set 을 target album 에 write-back

핵심 invariant:

- `curate_to_album` 은 단일 앨범만 건드린다.
- `curate_to_album` 응답은 최소한 아래를 포함한다.

```json
{
  "status": "completed",
  "action": "curate_to_album",
  "target_album_name": "...",
  "selected_count": 0,
  "touched_album_names": ["..."],
  "classification_album_created": false,
  "album_result": {
    "album": "..."
  }
}
```

- `touched_album_names != [target_album_name]` 이거나 `classification_album_created != false` 이면 phase-1 validator 는 FAIL 로 처리한다.

## action option validator 설계

새 public layer 에는 action registry 를 둔다.

각 action spec 은 아래 정보를 가진다.

```python
@dataclass(frozen=True)
class ActionSpec:
    tool: str
    action: str
    allowed: frozenset[str]
    required: frozenset[str]
    defaults: Mapping[str, object]
    forbidden: frozenset[str] = frozenset()
```

검증 순서:

1. `action` normalization: trim, lower, alias normalize
2. action 존재 확인
3. `options` object 여부 확인
4. unknown/forbidden option 확인
5. required option 확인
6. type/default normalization
7. semantic validation
8. facade/vendor 호출

semantic validation 예시:

- `date_from <= date_to`
- `limit` 은 양수
- `target_album_name` 은 빈 문자열 금지
- `photo_ids` 와 `photo_paths` 는 비어 있으면 안 됨
- `run_id` 는 required action 에서 `latest` 로 암묵 대체하지 않음
- Apple Photos write-back action 은 `source="apple"` 또는 Apple Photos target 의미가 분명해야 함

서버 validation 실패는 Python exception 이 아니라 structured blocked payload 로 반환한다.

## 기존 facade 와의 매핑 전략

phase 1 은 내부 구현을 한 번에 갈아엎지 않는다. 먼저 public MCP layer 를 새 router 로 바꾼 뒤 기존 facade service 를 재사용한다.

예상 계층:

```text
server.py MCP tool
  -> public_action_router.py
    -> action_specs.py
    -> query_service/select_service/write_service/workflow_service
      -> existing facade library/result/run service
        -> vendor photo-source/photo-ranker
```

초기 구현에서는 service 파일을 지나치게 쪼개지 않고 아래 정도가 적절하다.

- `facade/action_options.py`: action spec, validation, option normalization
- `facade/public_tools.py`: `photos_query`, `photos_select`, `photos_write`, `photos_workflow` router
- 기존 `facade/library_service.py`, `facade/result_service.py`, `facade/run_service.py` 재사용

이후 파일이 커지면 `query_service.py`, `select_service.py`, `write_service.py`, `workflow_service.py` 로 분리한다.

## 기존 public tool 제거 방침

legacy 호환은 phase 1 에서 제공하지 않는다.

MCP `list_tools` 기대값은 아래 4개가 된다.

```text
photos_query
photos_select
photos_write
photos_workflow
```

제거 대상:

- `photos_status`
- `photos_library`
- `photos_run`
- `photos_result`

문서와 테스트도 새 contract 기준으로 바꾼다.

기존 내부 함수명이나 Python service 함수는 당장 제거하지 않아도 된다. 다만 MCP public tool 로 노출하지 않는다.

## 대표 호출 흐름

### 흐름 A: 상태와 capability 확인

```python
photos_query(
    action="status",
    options={"view": "summary"}
)
```

### 흐름 B: 날짜 범위 사진 수 확인

```python
photos_query(
    action="list",
    options={
        "source": "apple",
        "date_from": "2025-06-30",
        "date_to": "2025-06-30",
        "limit": 500,
        "include_metadata": True
    }
)
```

### 흐름 C: 단일 사진 분석

```python
photos_select(
    action="analyze_photo",
    options={
        "source": "apple",
        "photo_id": "...",
        "include_faces": True,
        "wait_for_local": True
    }
)
```

### 흐름 D: 잘 나온 사진을 먼저 선별만 하기

```python
photos_select(
    action="select_best",
    options={
        "source": "apple",
        "date_from": "2025-06-30",
        "date_to": "2025-06-30",
        "limit": 26,
        "selection_profile": "general",
        "exclude_screenshots": True,
        "wait_for_local": True
    }
)
```

### 흐름 E: 기존 selected 결과를 단일 앨범에 쓰기

```python
photos_write(
    action="add_selected_to_album",
    options={
        "run_id": "55b94169",
        "target_album_name": "2025년 6월 30일 - PhotosMCP"
    }
)
```

### 흐름 F: 단일 앨범 저장 one-shot workflow

```python
photos_workflow(
    action="curate_to_album",
    options={
        "source": "apple",
        "date_from": "2025-06-30",
        "date_to": "2025-06-30",
        "limit": 26,
        "selection_profile": "general",
        "exclude_screenshots": True,
        "target_album_name": "2025년 6월 30일 - PhotosMCP",
        "wait_for_local": True
    }
)
```

이 흐름은 내부적으로 단일 앨범 경로만 사용해야 하며, 분류 앨범 organize 로 이어지지 않는다.

### 흐름 G: 카테고리별 자동 분류 앨범 생성

```python
photos_workflow(
    action="classify_then_organize_by_category",
    options={
        "source": "apple",
        "date_from": "2025-06-30",
        "date_to": "2025-06-30",
        "limit": 26,
        "selection_profile": "general",
        "album_prefix": "AI 분류"
    }
)
```

이 흐름만 여러 분류 앨범 생성을 허용한다.

## Nanobot 관점 변화

Nanobot 은 MCP wrapper 를 통해 새 tool 목록을 그대로 받는다. 별도 Nanobot router 를 추가하지 않는 것이 phase-1 원칙이다.

모델에게 보이는 선택지는 아래처럼 더 명확해진다.

- 조회해야 하면 `mcp_photos-mcp_photos_query`
- 선별해야 하면 `mcp_photos-mcp_photos_select`
- 이미 있는 결과를 써야 하면 `mcp_photos-mcp_photos_write`
- 사용자 요청을 한 번에 끝내야 하면 `mcp_photos-mcp_photos_workflow`

단일 앨범 요청의 preferred path 는 `photos_workflow(action="curate_to_album")` 이다. 이 action 에는 `album_prefix` 가 허용되지 않으므로 이전처럼 `AI 분류` 앨범이 생기는 route leakage 를 서버 contract 단계에서 막는다.

## 테스트 기준

### unit tests

- `list_tools` 가 정확히 4개 새 public tool 만 반환한다.
- legacy tool 이름이 public list 에 없어야 한다.
- 각 group tool 이 action unknown 을 structured blocked error 로 반환한다.
- action 별 forbidden option 이 vendor 호출 전에 거부된다.
- required option 누락이 structured blocked error 로 반환된다.
- `photos_workflow(action="curate_to_album")` 은 내부 `writeback_mode="album"` 으로만 호출된다.
- `curate_to_album` 에 `album_prefix` 가 들어오면 vendor 호출 없이 거부된다.
- `photos_write(action="organize_by_category")` 에 `target_album_name` 이 들어오면 vendor 호출 없이 거부된다.

### integration-style focused tests

- 기존 `test_mcp_client.py` 는 새 tool names 와 action/options contract 로 갱신한다.
- 기존 `test_llm_sample_validation.py` 는 scenario 1 expected route 를 `photos_workflow(action="curate_to_album")` 로 바꾼다.
- `test_run_service.py` 의 내부 facade coverage 는 유지하되 public tool coverage 는 새 router test 로 이동한다.
- single-album strict writeback test 는 `touched_album_names == [target_album_name]`, `classification_album_created=false` 를 계속 검증한다.

### live validation

1. PhotosMcp app health 확인
2. MCP `list_tools` 에서 새 4개 tool 만 확인
3. Nanobot WebUI 에서 단일 앨범 요청 실행
4. session JSONL 에서 실제 route 확인
5. 응답이 `photos_workflow(action="curate_to_album")` 또는 `photos_write(action="add_selected_to_album")` 계열인지 확인
6. Apple Photos 에서 생성된 앨범이 target album 하나뿐인지 확인
7. `AI 분류 - ...` 앨범이 새로 생기지 않았는지 확인

## 구현 작업 묶음 후보

### Task Group 1: public action contract 추가

- action spec 자료구조 추가
- action/options validator 추가
- structured blocked error helper 추가

### Task Group 2: 새 MCP tool 등록

- `server.py` 에 `photos_query`, `photos_select`, `photos_write`, `photos_workflow` 등록
- 기존 public tool 등록 제거
- state store ingest 는 새 tool 응답에도 적용

### Task Group 3: query/select/write/workflow router 구현

현재 상태: 완료

- 기존 facade service 로 매핑
- `curate_to_album` 은 내부적으로 album-mode curate 만 호출
- `organize_by_category` 는 category organize action 으로만 노출

### Task Group 4: tests 갱신

현재 상태: 완료

- public list_tools contract 변경
- action/options validation coverage 추가
- single-album route leakage regression 추가
- llm sample validator route 변경

### Task Group 5: live app rebuild 와 Nanobot 재검증

현재 상태: 완료

- [x] PhotosMcp app rebuild
- [x] running app restart
- [x] Nanobot launchd restart 또는 MCP connection refresh
- [x] WebUI live session route 확인

진행 메모:

- 2026-05-23: direct `list_tools` 와 `validate_llm_samples.py` 기준으로는 새 public tool 4개와 `photos_workflow(action="curate_to_album")` 경로가 확인됐다.
- 2026-05-23: Nanobot WebUI 재실행에서는 재기동 전 `mcp_photos-mcp_photos_status` 가 `McpError` 로 실패했다.
- 2026-05-23: 재기동 후 flushed websocket session 기준 실제 route 는 `mcp_photos-mcp_photos_query(action="status")` -> `mcp_photos-mcp_photos_query(action="list")` -> `mcp_photos-mcp_photos_select(action="select_best")` -> `mcp_photos-mcp_photos_workflow(action="curate_to_album")` 였다.
- 2026-05-23: 최종 응답은 `request_kind="photos_workflow"`, `action="curate_to_album"`, `classification_album_created=false`, `touched_album_names=[target_album_name]` 로 끝났고, Nanobot WebUI live route gate 가 통과했다.

### Task Group 6: 문서 동기화

현재 상태: 부분 완료

구현 완료 뒤 최소한 아래 문서를 갱신해야 한다.

- `docs/04-mcp-tool-catalog.md`
  - public tool 목록을 `photos_query`, `photos_select`, `photos_write`, `photos_workflow` 로 교체
  - 각 tool 의 action catalog 와 대표 options 설명
  - 기존 `photos_status`, `photos_library`, `photos_run`, `photos_result` 제거 사실 반영

- `docs/05-mcp-call-flows.md`
  - 기존 호출 예시를 새 action/options 형태로 전부 교체
  - 단일 앨범 저장은 `photos_workflow(action="curate_to_album")` 로 안내
  - 카테고리 분류 앨범은 `photos_workflow(action="classify_then_organize_by_category")` 또는 `photos_write(action="organize_by_category")` 로 분리

- `docs/07-facade-tool-contracts.md`
  - 새 public contract source of truth 로 갱신하거나, 이 planning 문서를 구현 완료 후 정식 contract 문서로 승격

- `docs/18-llm-integration-sample-tests.md`
  - expected route 를 새 tool/action 기준으로 갱신
  - single-album strict validator 의 expected tool 을 `photos_workflow(action="curate_to_album")` 로 변경

- `docs/llm-sample-validation-report-latest.md`
  - validator 재실행 뒤 최신 결과 반영
- `docs/live-validation-report-latest.md`
  - `--include-workflows` 재실행 뒤 최신 live 결과 반영

현재 반영 상태:

- [x] `docs/04-mcp-tool-catalog.md`
- [x] `docs/05-mcp-call-flows.md`
- [x] `docs/07-facade-tool-contracts.md`
- [x] `docs/18-llm-integration-sample-tests.md`
- [x] `docs/llm-sample-validation-report-latest.md`
- [x] `docs/live-validation-report-latest.md`

## rollout 순서

1. 이 redesign 문서를 확정한다.
2. 별도 implementation plan 문서를 만든다.
3. action/options validator 를 먼저 red-green 으로 구현한다.
4. 새 public tool 을 등록하고 legacy public tools 를 제거한다.
5. focused tests 를 새 contract 로 갱신한다.
6. PhotosMcp app 을 rebuild/restart 한다.
7. Nanobot WebUI live single-album gate 를 재검증한다.
8. public docs 를 새 surface 기준으로 갱신한다.

## 위험과 완화

### 위험 1: `options` object 가 LLM 에게 너무 추상적으로 보일 수 있음

완화:

- tool description 에 action catalog 와 대표 required options 를 짧게 넣는다.
- action unknown/invalid option 오류에 `allowed_options` 와 `example` 을 포함한다.
- sample validator 로 실제 자연어 route 를 반복 검증한다.

### 위험 2: legacy tool 제거로 기존 client 가 즉시 깨질 수 있음

완화:

- 이번 요청의 전제는 legacy 호환 불필요다.
- 대신 문서, tests, Nanobot live config/restart 검증을 같은 change set 에 포함한다.
- MCP `list_tools` 결과가 새 4개로 바뀌는 것을 의도된 breaking change 로 기록한다.

### 위험 3: 기존 facade 내부가 여전히 평면 파라미터를 기대함

완화:

- public router 에서 action 별로 내부 facade 호출 인자를 명시적으로 구성한다.
- public options 를 그대로 기존 `photos_run` 인자로 흘려보내지 않는다.
- 내부 facade service 는 후속 phase 에서 action-specific service 로 분리한다.

### 위험 4: 단일 앨범 write-back vendor path 가 충분히 독립돼 있지 않을 수 있음

완화:

- `curate_to_album` 테스트에서 vendor 호출 인자를 직접 검증한다.
- live response 에서 `touched_album_names`, `classification_album_created` 를 hard gate 로 확인한다.
- 필요하면 photo-ranker `curate_best_photos` 의 album mode 를 더 좁혀 category organize code path 와 분리한다.

## 완료 조건

phase 1 이 완료됐다고 보려면 아래가 모두 충족되어야 한다.

1. [x] MCP `list_tools` 는 `photos_query`, `photos_select`, `photos_write`, `photos_workflow` 만 반환한다.
2. [x] 단일 앨범 요청은 `photos_workflow(action="curate_to_album")` 한 번으로 성공한다.
3. [x] `curate_to_album` 에서는 `album_prefix`, `writeback_mode`, `results_json`, `group_by_date` 가 허용되지 않는다.
4. [x] 단일 앨범 write-back 응답은 `touched_album_names == [target_album_name]` 과 `classification_album_created=false` 를 만족한다.
5. [x] 카테고리 organize 는 `photos_workflow(action="classify_then_organize_by_category")` 또는 `photos_write(action="organize_by_category")` 에서만 가능하다.
6. [x] Nanobot WebUI live session 에서 `AI 분류 - ...` 앨범 leakage 가 재현되지 않는다.
7. [x] 구현 뒤 `docs/04-mcp-tool-catalog.md`, `docs/05-mcp-call-flows.md`, `docs/07-facade-tool-contracts.md`, `docs/18-llm-integration-sample-tests.md` 가 새 contract 와 일치한다.

완료 메모:

- 2026-05-23 rerun 기준으로 `photos-mcp` app 의 public MCP surface 는 새 4개 tool 만 노출한다.
- 같은 날 Nanobot WebUI session 은 재기동 후 grouped tool names 로 새 session route 를 구성했고, 최종 `photos_workflow(action="curate_to_album")` 가 단일 앨범 write-back 까지 성공했다.
- `photos-mcp` app 로그의 `photos_run.curate` 표기는 external tool name 이 아니라 internal workflow step log 로 해석해야 한다.

## 한눈에 보는 실행 체크리스트

아래 체크리스트는 phase-1 상태를 한 번에 확인하기 위한 운영 요약이다.

### 구현과 contract

- [x] public MCP tool surface 를 `photos_query`, `photos_select`, `photos_write`, `photos_workflow` 4개로 교체
- [x] action/options validator 추가
- [x] `curate_to_album` 을 single-album write-back 전용 route 로 고정
- [x] `organize_by_category` 를 category organize 전용 route 로 분리

### 테스트와 validator

- [x] public tool list contract test 갱신
- [x] invalid/forbidden option regression 추가
- [x] single-album leakage regression 추가
- [x] `llm_sample_validation.py` 를 새 route 로 이관
- [x] `live_validation.py` 를 새 route 로 이관
- [x] focused pytest slice 통과

### 문서 동기화

- [x] `docs/04-mcp-tool-catalog.md` 갱신
- [x] `docs/05-mcp-call-flows.md` 갱신
- [x] `docs/07-facade-tool-contracts.md` 갱신
- [x] `docs/18-llm-integration-sample-tests.md` 갱신
- [x] `docs/llm-sample-validation-report-latest.md` 최신 결과 반영
- [x] `docs/live-validation-report-latest.md` 최신 결과 반영

### live gate

- [x] PhotosMcp app rebuild
- [x] running PhotosMcp app restart
- [x] live `list_tools` 가 새 4개 tool 만 반환하는지 확인
- [x] live single-album sample 이 `photos_workflow(action="curate_to_album")` 로 성공하는지 확인
- [x] Nanobot MCP connection refresh 또는 관련 service restart
- [x] Nanobot WebUI 에서 `AI 분류 - ...` leakage 재현 여부 확인

최신 메모:

- 2026-05-23: Nanobot restart 뒤 `photos-mcp` app log 에 `ListToolsRequest` 와 `CallToolRequest` 가 다시 들어왔다.
- 2026-05-23: flushed websocket session 기준 WebUI single-album prompt 는 `mcp_photos-mcp_photos_query` / `mcp_photos-mcp_photos_select` / `mcp_photos-mcp_photos_workflow` 를 순서대로 사용했다.
- 2026-05-23: `photos_workflow(action="curate_to_album")` 의 첫 시도는 `selected_photo_ids` 를 넣어 structured blocked payload 를 받았고, 즉시 허용 option 만 남긴 재시도로 성공했다.
- 2026-05-23: 최종 결과는 `classification_album_created=false` 와 단일 `touched_album_names` 로 확인됐다.
- 2026-05-23: `validate_llm_samples.py` 재실행으로 `docs/llm-sample-validation-report-latest.md` 를 갱신했고, sample 4개가 모두 통과했다.
- 2026-05-23: `live_validate.py --include-workflows` 재실행으로 `docs/live-validation-report-latest.md` 를 갱신했고 `include_workflows=true` 기준 최신 runtime evidence 를 기록했다.
- 2026-05-23: 다만 live report 에서는 local/non-local analyze 계열이 현재 `mlx-vlm is not installed. Install with: uv pip install mlx-vlm` 때문에 여전히 실패로 남아 있다.
