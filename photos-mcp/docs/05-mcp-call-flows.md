# MCP call flows

## 1. 목적

이 문서는 `photos-mcp` 가 새 4개 public MCP tool 로 어떻게 호출되고, 내부적으로 어떤 subsystem 을 지나며, 어떤 결과를 기대할 수 있는지를 시나리오별로 설명한다. transport 세부 프레임보다 호출 순서와 책임 분리를 이해하는 데 집중한다.

현재 public tool 은 아래 4개다.

- `photos_query(action, options)`
- `photos_select(action, options)`
- `photos_write(action, options)`
- `photos_workflow(action, options)`

## 2. 가장 먼저 확인할 흐름

### 흐름 A: 앱 기동과 health 확인

1. `PhotosMcp.app` 실행
2. `GET /health`
3. `GET /health/capabilities`
4. MCP tool `photos_query(action="status", options={"view": "summary"})`

내부 흐름:

1. app 시작
2. daemon 이 `127.0.0.1:18791` 에 MCP server 노출
3. `server.py` 가 state store 기반 health payload 반환
4. `photos_query` 가 `status` action 으로 app/transport/capability/current/latest 상태를 요약

성공 신호:

- `/health` 응답 성공
- MCP status 응답의 `transport.status` 가 `ok`
- `capabilities.checks` 에서 Apple Photos readiness 판단 가능

## 3. source access 에서 analysis 로 이어지는 흐름

### 흐름 B: Apple Photos 목록 조회 후 단건 분석

1. `photos_query(action="list", options={"source": "apple", "album": "최근", "limit": 20})`
2. 결과 중 `photo_id` 하나 선택
3. `photos_query(action="inspect", options={"source": "apple", "photo_id": "...", "include_thumbnail": true})`
4. `photos_select(action="analyze_photo", options={"source": "apple", "photo_id": "..."})`
5. 필요하면 `photos_query(action="result_summary", options={"run_id": "..."})`

내부 흐름:

1. `photos_query(action="list")` 는 내부적으로 `photo-source` 의 Apple Photos adapter 로 간다.
2. `inspect` 단계에서 thumbnail 과 metadata 가 준비된다.
3. `photos_select(action="analyze_photo")` 가 내부적으로 `photo-ranker` 분석 함수를 조합한다.

### 흐름 B-1: iCloud-only Apple 사진을 기다렸다가 analyze 이어가기

1. `photos_query(action="ready_only", options={"source": "apple", "limit": 20})` 로 바로 analyze 가능한 항목을 먼저 확인한다.
2. 원하는 사진이 `local_path_available=false` 이면 Photos 앱에서 해당 사진을 연다.
3. `photos_select(action="analyze_photo", options={"source": "apple", "photo_id": "...", "wait_for_local": true})` 호출
4. 첫 응답에서 `status=running`, `wait_status=waiting_for_local_download`, `next_suggested_action=photos_query` 확인
5. 이후 `photos_query(action="result_summary", options={"run_id": "..."})` 로 대기 상태를 본다.
6. 다운로드가 끝나면 `photos_query(action="result_detail", options={"run_id": "..."})` 로 analyze 결과를 읽는다.
7. 더 이상 기다릴 필요가 없으면 `photos_query(action="cancel", options={"run_id": "..."})` 로 대기를 취소한다.

## 4. background classify job 흐름

### 흐름 C: classify job 시작부터 결과 조회까지

1. `photos_select(action="classify_range", options={"source": "apple", "source_path": "최근", "limit": 100, "selection_profile": "general"})`
2. 반환된 `run_id` 확보
3. `photos_query(action="status", options={"view": "running"})` 또는 `photos_query(action="result_summary", options={"run_id": "..."})` 확인
4. 완료 후 `photos_query(action="result_detail", options={"run_id": "..."})` 조회

성공 신호:

- 결과에 `run_id` 와 초기 `status` 가 있다.
- summary 에 `photo_count`, `selected_count`, `preview_path`, `results_path` 같은 요약이 생긴다.
- classify job 이 terminal 이 되면 `results_path` 는 `~/.photos-mcp/runtime/photo-ranker/artifacts/<job_id>/results.json` 을 가리킨다.

## 5. review 흐름

### 흐름 D: 분류 결과 검토 후 선택 결과 export

1. `photos_query(action="selected", options={"run_id": "...", "top_n": 50})`
2. 사용자가 selected 할 사진을 결정
3. 필요하면 app UI 또는 advanced/internal review 경로에서 세부 선택을 조정
4. `photos_write(action="export_selected", options={"run_id": "...", "output_dir": "..."})`

이 흐름은 "자동 분류 결과를 사람이 최종 선택한다"는 사용자 경험을 설명할 때 핵심이다.

## 6. Apple Photos write-back 흐름

### 흐름 E: selected 결과를 단일 앨범에 추가

1. `photos_select(action="select_best", options={...})` 또는 기존 완료 run 확보
2. `photos_write(action="add_selected_to_album", options={"run_id": "...", "target_album_name": "..."})`로 plan 확인
3. 사용자가 승인하면 같은 options에 `approval_token`을 추가해 다시 호출

성공 신호:

- `touched_album_names == [target_album_name]`
- `classification_album_created == false`
- `added` 또는 `selected_count` 가 0보다 큼

### 흐름 F: classify 결과를 category album 으로 정리

1. `photos_select(action="classify_range", options={...})` 또는 기존 완료 run 확보
2. `photos_write(action="organize_by_category", options={"run_id": "...", "album_prefix": "AI 분류"})`로 plan 확인
3. 사용자가 승인하면 같은 options에 `approval_token`을 추가해 다시 호출

이 흐름만 여러 `AI 분류 - ...` 앨범 생성을 허용한다. 단일 앨범 요청에는 사용하지 않는다.

## 7. end-to-end workflow 흐름

### 흐름 G: 잘 나온 사진을 단일 앨범에 바로 저장

대표 tool:

- `photos_workflow(action="curate_to_album")`

첫 호출은 scope와 target album을 담은 plan만 반환한다. 사용자가 이를 승인한 뒤 같은 options에 `approval_token`을 추가한 두 번째 호출부터 background workflow가 시작된다.

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

내부적으로는 아래 단계가 이어진다.

1. source 에서 사진 읽기
2. classify/rank 수행
3. top-percent selected set 생성
4. selected photo id 만 target album 하나에 추가

이 workflow 는 `album_prefix`, `group_by_date`, `writeback_mode`, `results_json` 을 받지 않는다.

### 흐름 H: 한 번에 분류하고 category album 으로 정리하기

대표 tool:

- `photos_workflow(action="classify_then_organize_by_category")`

예:

```python
photos_workflow(
    action="classify_then_organize_by_category",
    options={
        "source": "apple",
        "date_from": "2025-06-30",
        "date_to": "2025-06-30",
        "album_prefix": "AI 분류",
        "selection_profile": "general"
    }
)
```

문제가 생기면 workflow 를 그대로 디버깅하지 말고 아래처럼 쪼갠다.

1. `photos_query(action="list")`
2. `photos_query(action="inspect")`
3. `photos_select(action="analyze_photo")` 또는 `photos_select(action="classify_range")`
4. `photos_query(action="result_detail")`
5. `photos_write(action="organize_by_category")`

## 8. wrapper 가 추가로 해 주는 일

이 문서의 모든 흐름에서 공통으로 들어가는 wrapper 동작이 있다.

- 각 tool 호출 전에 vendor runtime import path 가 준비된다.
- job 응답은 `job_id`, `status`, `terminal`, `finished_at`, `summary_available`, `result_available` 를 일관되게 포함하도록 정규화된다.
- state store 는 active jobs, recent jobs, background job running 여부를 유지한다.

## 9. 실패를 분해하는 기준

시나리오가 실패했을 때는 아래 순서로 좁히는 것이 가장 빠르다.

1. `/health` 는 되는가
2. `/health/capabilities` 에서 Photos readiness 는 되는가
3. `photos_query(action="status")` 는 되는가
4. `photos_query(action="list")` 는 되는가
5. `photos_query(action="inspect")` 또는 `photos_select(action="analyze_photo")` 는 되는가
6. `photos_select(action="classify_range"|"select_best")` 는 되는가
7. write-back 단계만 실패하는가

이 순서대로 보면 transport, source access, ranking, write-back 중 어디가 문제인지 빠르게 갈린다.
