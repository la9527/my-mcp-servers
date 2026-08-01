# MCP tool catalog

## 1. 개요

현재 `photos-mcp` 가 기본 MCP public surface 로 노출하는 tool 은 4개다.

- `photos_query(action, options)`
- `photos_select(action, options)`
- `photos_write(action, options)`
- `photos_workflow(action, options)`

이 4개 tool 은 역할 group 을 뜻한다. 모든 tool 은 `action` 과 `options` 두 입력만 받으며, 서버는 action 별로 허용되는 `options` key 를 강제한다. action 에 맞지 않는 option 은 vendor 호출 전에 `status="blocked"`, `error_code="invalid_options_for_action"` payload 로 거부한다.

기존 public tool 이름인 `photos_status`, `photos_library`, `photos_run`, `photos_result` 는 더 이상 MCP public surface 로 노출하지 않는다. 내부 facade/service 함수명은 구현 detail 로 남을 수 있지만, LLM client 는 새 4개 group tool 만 사용한다.

상세 설계 배경은 `planning/03-mcp-public-tool-surface-redesign-phase1.md` 를 본다.

## 2. public facade tools

### `photos_query(action, options)`

역할: 진단/조회 전용 read-only tool

대표 action:

- `guide`
- `status`
- `list`
- `ready_only`
- `search`
- `inspect`
- `prefetch`
- `result_summary`
- `result_detail`
- `selected`
- `artifacts`
- `cancel`

언제 쓰나:

- 목적별 권장 호출 순서, action catalog와 현재 VLM 상태 확인
- app/transport/capability 상태 확인
- Apple Photos 목록 조회
- 특정 사진 inspect
- run summary/result/selected/artifact 조회
- synthetic wait run 취소

예:

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

### `photos_select(action, options)`

역할: 분석/선별 전용 tool. Apple Photos album write-back 이나 local export 를 하지 않는다.

대표 action:

- `analyze_photo`
- `classify_range`
- `select_best`
- `select_best_person`

언제 쓰나:

- 단건 분석
- 날짜/앨범/인물 범위 classify job 시작
- 잘 나온 사진 selected set 생성
- 특정인 중심 selected set 생성

예:

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

### `photos_write(action, options)`

역할: 이미 있는 selected/result/photo id 를 외부 출력이나 Apple Photos 에 반영하는 쓰기 tool

대표 action:

- `add_selected_to_album`
- `add_photo_ids_to_album`
- `export_selected`
- `organize_by_category`
- `import_to_album`
- `cleanup_album`

중요한 경계:

- `add_selected_to_album`, `add_photo_ids_to_album`, `import_to_album` 은 단일 target album 만 건드린다.
- `organize_by_category` 만 `album_prefix` 기반 다중 분류 앨범 생성을 허용한다.
- 단일 앨범 action 은 `album_prefix` 를 받지 않는다.
- 카테고리 organize action 은 `target_album_name` 을 받지 않는다.

예:

```python
photos_write(
    action="add_selected_to_album",
    options={
        "run_id": "55b94169",
        "target_album_name": "2025년 6월 30일 - PhotosMCP"
    }
)
```

### `photos_workflow(action, options)`

역할: 사용자 의도를 한 번에 수행하는 one-shot workflow tool

대표 action:

- `curate_to_album`
- `curate_to_directory`
- `classify_then_organize_by_category`
- `import_then_curate_to_album`

가장 중요한 action 은 `curate_to_album` 이다. 이 action 은 잘 나온 사진을 선별해 정확히 하나의 Apple Photos 앨범에 저장한다. public option 에 `writeback_mode` 를 받지 않으며, 내부적으로는 single-album write-back 경로만 사용한다.

예:

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

성공 응답은 아래 invariant 를 만족해야 한다.

```text
touched_album_names == [target_album_name]
classification_album_created == false
```

## 3. internal legacy tool groups

기존 `photo-source`, `photo-ranker` 의 세부 tool 은 내부 구현 detail 로 유지된다. 기본 MCP `list_tools` 에서는 직접 노출하지 않는다.

### `photo-source` internal functions

- `list_photos`
- `get_metadata`
- `get_thumbnail`
- `search_photos`
- `prefetch_photos`
- `export_photos`

현재 public surface 에서는 주로 `photos_query` 와 일부 `photos_select` 경로로 흡수된다.

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
- `add_to_album`
- `organize_results`
- `organize_results_to_directory`
- `import_photos`
- `import_and_organize`
- `classify_and_organize`
- `delete_photo_album`

이 함수들은 현재 public tool 이름이 아니라 새 group tool 의 내부 substep 으로 사용된다.

## 4. 처음 호출해 볼 때 추천하는 순서

진단, source access, 단건 분석, classify, result 조회, write-back 을 차례로 검증하려면 아래 순서가 좋다.

1. `photos_query(action="guide", options={"goal": "overview"})`
2. `photos_query(action="status", options={"view": "summary"})`
3. `photos_query(action="list", options={"source": "apple", "limit": 20})`
4. `photos_query(action="inspect", options={"source": "apple", "photo_id": "..."})`
5. `photos_select(action="analyze_photo", options={"source": "apple", "photo_id": "..."})`
6. `photos_select(action="classify_range", options={"source": "apple", "limit": 100})`
7. `photos_query(action="result_summary", options={"run_id": "..."})`
8. `photos_query(action="result_detail", options={"run_id": "..."})`
9. `photos_write(action="organize_by_category", options={"run_id": "...", "album_prefix": "AI 분류"})`로 plan 확인
10. 같은 options에 `approval_token`을 추가해 승인된 쓰기 실행

단일 앨범에 바로 저장하려면 위 sequence 대신 `photos_workflow(action="curate_to_album")` 을 우선 사용한다.

모든 `photos_write`와 `photos_workflow`는 첫 호출에서 `status="awaiting_approval"`과 `mutation_plan`을 반환한다. 사용자가 승인한 경우에만 변경되지 않은 options에 반환된 `approval_token`을 추가해 다시 호출한다.
