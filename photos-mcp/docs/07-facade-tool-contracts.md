# Facade tool contracts

## 1. 목적

이 문서는 `photos-mcp` 의 MCP public facade contract 를 정의한다. 현재 public surface 는 아래 4개 group tool 만 노출한다.

- `photos_query(action, options)`
- `photos_select(action, options)`
- `photos_write(action, options)`
- `photos_workflow(action, options)`

모든 tool 은 `action` 과 `options` 두 입력만 받는다. `options` 는 action 별 schema 에 의해 allowlist, required, forbidden key 검증을 통과해야 한다. 검증은 vendor 호출 전에 끝나며, 실패 시 side effect 없이 structured blocked payload 를 반환한다.

기존 public tool 이름인 `photos_status`, `photos_library`, `photos_run`, `photos_result` 는 더 이상 LLM-facing MCP tool 로 노출하지 않는다. 내부 service 함수와 vendor function 은 유지될 수 있지만 public contract 로 간주하지 않는다.

## 2. 공통 입력 규칙

공통 top-level shape:

```json
{
  "action": "<action-name>",
  "options": {}
}
```

공통 규칙:

- `action` 은 tool 별 등록 action 중 하나여야 한다.
- `options` 는 object 이거나 생략 가능하다.
- action 에서 허용하지 않은 key 는 거부한다.
- required key 가 빠지면 거부한다.
- forbidden key 가 들어오면 거부한다.
- 거부 응답은 `status="blocked"`, `tool`, `action`, `error_code`, `message` 를 포함한다.

예상 blocked payload:

```json
{
  "status": "blocked",
  "tool": "photos_select",
  "action": "select_best",
  "error_code": "invalid_options_for_action",
  "message": "photos_select(action=select_best) does not accept option keys: target_album_name",
  "invalid_keys": ["target_album_name"]
}
```

## 3. `photos_query`

역할: status, browse, result 조회처럼 side effect 없는 read-only 동작을 담당한다.

Actions:

| action | 설명 | 주요 options |
| --- | --- | --- |
| `status` | transport/capability/job status | `view` |
| `list` | source photo 목록 | `source`, `album`, `date_from`, `date_to`, `limit`, `include_metadata` |
| `ready_only` | local path 가 준비된 항목만 조회 | `source`, `album`, `date_from`, `date_to`, `limit` |
| `search` | source photo 검색 | `source`, `query`, `album`, `date_from`, `date_to`, `limit` |
| `inspect` | 단건 metadata/thumbnail 조회 | `source`, `photo_id`, `include_thumbnail` |
| `prefetch` | source item prefetch | `source`, `photo_ids`, `limit` |
| `result_summary` | run summary 조회 | `run_id` |
| `result_detail` | run detail/result 조회 | `run_id`, `limit` |
| `selected` | selected review item 조회 | `run_id`, `top_n` |
| `artifacts` | result artifact/export 조회 | `run_id`, `output_dir` |
| `cancel` | 대기 중인 synthetic run 취소 | `run_id` |

`status` 예:

```python
photos_query(action="status", options={"view": "summary"})
```

`list` 예:

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

Apple Photos live 응답에서 `path` 가 비어 있으면 `local_path_available=false` 로 내려올 수 있다. 이 경우 `download_hint` 와 `recommended_next_action` 을 읽고, 필요하면 Photos 앱에서 원본 다운로드를 유도한 뒤 `photos_select(action="analyze_photo", options={"wait_for_local": true})` 로 이어간다.

## 4. `photos_select`

역할: 분석과 선별을 담당한다. 이 tool 은 Apple Photos album write-back, local directory export, import, cleanup 을 하지 않는다.

Actions:

| action | 설명 | 주요 options |
| --- | --- | --- |
| `analyze_photo` | 단건 photo 분석 | `source`, `photo_id`, `source_path`, `wait_for_local` |
| `classify_range` | 범위 classify job 시작 | `source`, `source_path`, `album`, `date_from`, `date_to`, `limit`, `selection_profile`, `exclude_screenshots`, `wait_for_local` |
| `select_best` | 잘 나온 사진 selected set 생성 | `source`, `source_path`, `album`, `date_from`, `date_to`, `limit`, `selection_profile`, `exclude_screenshots`, `wait_for_local` |
| `select_best_person` | 특정인 중심 selected set 생성 | `source`, `person`, `album`, `date_from`, `date_to`, `limit`, `selection_profile`, `exclude_screenshots`, `wait_for_local` |

금지되는 option 예:

- `target_album_name`
- `album_prefix`
- `writeback_mode`
- `output_dir`
- `photo_paths`

`select_best` 예:

```python
photos_select(
    action="select_best",
    options={
        "source": "local",
        "source_path": "~/SamplePhotos",
        "limit": 20,
        "selection_profile": "general",
        "exclude_screenshots": True
    }
)
```

성공 응답은 `run_id` 또는 `job_id`, `selected_count`, `status` 를 포함할 수 있다. 이후 결과는 `photos_query(action="selected")`, `photos_query(action="result_summary")`, `photos_query(action="result_detail")` 로 조회한다.

## 5. `photos_write`

역할: 이미 선택되었거나 명시된 photo id/path 를 외부 destination 에 쓴다.

Actions:

| action | 설명 | required options |
| --- | --- | --- |
| `add_selected_to_album` | selected item 을 단일 Apple Photos album 에 추가 | `run_id`, `target_album_name` |
| `add_photo_ids_to_album` | explicit photo ids 를 단일 Apple Photos album 에 추가 | `photo_ids`, `target_album_name` |
| `export_selected` | selected item 을 local directory 로 export | `run_id`, `output_dir` |
| `organize_by_category` | classify result 를 category album/directory 로 정리 | `album_prefix` 또는 `folder` |
| `import_to_album` | local photo paths 를 Apple Photos album 으로 import | `photo_paths`, `target_album_name` |
| `cleanup_album` | validation/temporary album 삭제 | `target_album_name` |

단일 앨범 write action invariant:

```text
touched_album_names == [target_album_name]
classification_album_created == false
```

`add_selected_to_album` 예:

```python
photos_write(
    action="add_selected_to_album",
    options={
        "run_id": "job-123",
        "target_album_name": "가족 베스트"
    }
)
```

`organize_by_category` 예:

```python
photos_write(
    action="organize_by_category",
    options={
        "run_id": "job-123",
        "album_prefix": "AI 분류"
    }
)
```

`organize_by_category` 는 category album 생성을 위한 action 이므로 `target_album_name` 을 받지 않는다. 반대로 단일 앨범 write action 은 `album_prefix` 를 받지 않는다.

## 6. `photos_workflow`

역할: 하나의 사용자 목표를 end-to-end 로 수행한다. 모델이 여러 단계 조합을 잘못 선택하기 쉬운 요청은 workflow action 을 우선 사용한다.

Actions:

| action | 설명 | 주요 required options |
| --- | --- | --- |
| `curate_to_album` | 잘 나온 사진을 골라 단일 Apple Photos album 에 저장 | `target_album_name` |
| `curate_to_directory` | 잘 나온 사진을 골라 local directory 로 export | `output_dir` |
| `classify_then_organize_by_category` | classify 후 category album/directory 로 정리 | `album_prefix` 또는 `folder` |
| `import_then_curate_to_album` | local photos import 중심 workflow | `photo_paths`, `target_album_name` |

`curate_to_album` 예:

```python
photos_workflow(
    action="curate_to_album",
    options={
        "source": "apple",
        "date_from": "2025-06-30",
        "date_to": "2025-06-30",
        "limit": 26,
        "selection_profile": "general",
        "target_album_name": "2025년 6월 30일 베스트",
        "exclude_screenshots": True,
        "wait_for_local": True
    }
)
```

`curate_to_album` 은 public option 으로 `writeback_mode` 를 받지 않는다. 내부 route 는 항상 single-album write-back 이며, `album_prefix` 나 category organize 관련 option 을 받으면 blocked 된다.

## 7. LLM routing guidance

모델이 선택해야 하는 기본 routing rule 은 아래와 같다.

- "상태", "연결", "목록", "검색", "결과 확인" 요청은 `photos_query` 를 사용한다.
- "분석", "분류", "잘 나온 사진 고르기" 요청은 `photos_select` 를 사용한다.
- "선택된 결과를 앨범에 추가", "내보내기", "import", "cleanup" 요청은 `photos_write` 를 사용한다.
- "잘 나온 사진을 골라 하나의 앨범으로 만들어줘" 는 `photos_workflow(action="curate_to_album")` 를 사용한다.
- "AI 분류 앨범들로 나눠줘" 는 `photos_workflow(action="classify_then_organize_by_category")` 또는 `photos_write(action="organize_by_category")` 를 사용한다.

혼동 방지 rule:

- single target album 요청에 `organize_by_category` 를 쓰지 않는다.
- category organize 요청에 `target_album_name` 을 넣지 않는다.
- select 단계에 write destination option 을 넣지 않는다.
- workflow 단계에 내부 vendor option 인 `writeback_mode` 를 넣지 않는다.

## 8. 구현 위치

현재 구현의 주요 파일은 아래와 같다.

- `src/photos_mcp/server.py`: FastMCP tool registration
- `src/photos_mcp/facade/action_options.py`: action/options validation registry
- `src/photos_mcp/facade/public_tools.py`: public group tool router
- `src/photos_mcp/facade/library_service.py`: query/browse backend
- `src/photos_mcp/facade/run_service.py`: 기존 intent backend, public router 의 내부 dependency

계약을 바꿀 때는 `tests/test_public_tools.py`, `tests/test_mcp_client.py`, `tests/test_llm_sample_validation.py` 를 함께 갱신한다.