# LLM integration sample tests

## 1. 목적

이 문서는 `photos-mcp` 의 LLM-facing sample validation 을 설명한다. validator 는 LLM reasoning 자체를 평가하지 않는다. 대신 실제 사용자 프롬프트가 planner 에 의해 현재 public MCP surface 인 4개 group tool 호출로 번역되었다고 가정하고, endpoint 가 그 요구를 수행할 수 있는지 확인한다.

현재 public surface:

- `photos_query(action, options)`
- `photos_select(action, options)`
- `photos_write(action, options)`
- `photos_workflow(action, options)`

## 2. 실행 파일과 테스트

validator 구현:

- `src/photos_mcp/llm_sample_validation.py`

관련 regression tests:

- `tests/test_llm_sample_validation.py`
- `tests/test_public_tools.py`
- `tests/test_mcp_client.py`
- `tests/test_run_service.py`
- `tests/test_photo_ranker_selection.py`

권장 focused gate:

```bash
cd /Volumes/ExtData/my-mcp-servers/photos-mcp
uv run pytest tests/test_llm_sample_validation.py tests/test_public_tools.py tests/test_mcp_client.py tests/test_run_service.py tests/test_photo_ranker_selection.py -q
```

## 3. sample catalog

validator 는 기본적으로 4개 sample 을 실행한다.

### 3.1 status-summary

사용자 prompt:

```text
연결 상태 알려줘
```

Expected route:

```text
photos_query(action="status", options={"view": "summary"})
```

PASS 기준:

- 응답이 structured object
- `transport` status 정보를 포함

### 3.2 apple-apr16to30-best-to-album

사용자 prompt:

```text
iCloud 사진 중 작년 4월 16일~작년 4월30일 사진들만 잘 나온 사진들만 앨범을 따로 저장해 만들어줘.
```

Expected route:

```text
photos_workflow(action="curate_to_album") -> photos_write(action="cleanup_album")
```

핵심 options:

- `source="apple"`
- `date_from`, `date_to`
- `limit=20`
- `selection_profile="general"`
- `target_album_name=<validation album>`
- `exclude_screenshots=true`

PASS 후보 기준:

- selected item 이 1개 이상
- single-album invariant 충족
- validation album cleanup 성공

single-album invariant:

```text
action == "curate_to_album" or writeback_mode == "album"
target_album_name == validation album name
touched_album_names == [target_album_name]
classification_album_created == false
```

이 sample 은 과거 Nanobot WebUI route selection 실패를 직접 겨냥한다. `organize_by_category` 나 `album_prefix` 경로로 빠져 `AI 분류 - ...` 앨범이 생기면 FAIL 이다.

### 3.3 local-samplephotos-best-to-album

사용자 prompt:

```text
로컬 ~/SamplePhotos 디렉토리에 잘 나온 사진들을 골라서 iCloud 에 적절한 이름으로 앨범을 만들어 저장해줘.
```

Expected route:

```text
photos_select(action="select_best") -> photos_query(action="selected") -> photos_write(action="import_to_album") -> photos_write(action="cleanup_album")
```

핵심 options:

- `source="local"`
- `source_path=<samplephotos_dir>`
- `limit=20`
- `selection_profile="general"`
- `exclude_screenshots=true`
- `target_album_name=<validation album>`

PASS 후보 기준:

- selected local paths 존재
- screenshot-like item 제외
- import count > 0
- validation album cleanup 성공

sample directory 가 없거나 import 가능한 selected path 가 없으면 SKIP 할 수 있다.

### 3.4 apple-apr16to30-person-best-to-local-dir

사용자 prompt:

```text
iCloud 사진 중 작년 4월 16일~작년 4월30일 사진들 중 특정인의 사진만 뽑아서 잘 나온 사진들을 로컬의 특정(~/temp) 디렉토리에 저장해줘.
```

Expected route:

```text
photos_select(action="select_best_person") -> photos_write(action="export_selected")
```

핵심 options:

- `source="apple"`
- `person=<configured or discovered person>`
- `date_from`, `date_to`
- `limit=20`
- `selection_profile="general"`
- `exclude_screenshots=true`
- `output_dir=<temporary validation directory>`

PASS 후보 기준:

- target person resolved
- selected count > 0
- copied/exported count > 0

target person 이 설정되지 않았고 metadata 에서도 발견되지 않으면 SKIP 할 수 있다.

## 4. blocked route regression

새 public surface 의 핵심은 설명 문자열에만 의존하지 않고 schema 구조로 잘못된 route 를 막는 것이다.

필수 regression:

- `photos_select(action="select_best")` 에 `target_album_name` 이 들어오면 blocked
- `photos_select(action="select_best")` 에 `writeback_mode` 가 들어오면 blocked
- `photos_workflow(action="curate_to_album")` 에 `album_prefix` 가 들어오면 blocked
- `photos_write(action="organize_by_category")` 에 `target_album_name` 이 들어오면 blocked
- `photos_workflow(action="curate_to_album")` 은 내부 single-album write-back 으로 강제

이 regression 은 `tests/test_public_tools.py` 와 `tests/test_mcp_client.py` 에서 고정한다.

## 5. 결과 해석

validator status 의미:

- `PASS`: endpoint 가 sample 요구를 충족했다.
- `PARTIAL`: 핵심 side effect 는 성공했지만 cleanup 등 보조 확인이 실패했다.
- `SKIP`: 입력 환경이 부족해 검증하지 않았다. 예: sample photos directory 없음, target person 없음.
- `FAIL`: endpoint 응답이 오류이거나 route invariant 를 위반했다.

특히 `apple-apr16to30-best-to-album` 에서 아래 결과는 FAIL 이다.

- `touched_album_names` 에 target album 외 다른 album 포함
- `classification_album_created=true`
- `target_album_name` mismatch
- selected count 가 0인데 성공처럼 보고
- cleanup action 이 실패했는데 PASS 로 처리

## 6. live validation 주의사항

이 validator 는 실제 Photos library 와 Apple Photos write-back 을 건드릴 수 있다. live 실행 전에는 아래를 확인한다.

- `PhotosMcp.app` 또는 MCP server 가 `http://127.0.0.1:18791/mcp` 에 떠 있는지 확인
- `/health` 와 `/health/capabilities` 확인
- validation album 은 test 전용 이름을 사용
- cleanup 실패 시 수동으로 Apple Photos album 을 삭제할 수 있어야 함
- iCloud-only 사진은 Photos 앱에서 원본 다운로드가 필요할 수 있음

## 7. 새 sample 추가 절차

새 LLM-facing sample 을 추가할 때는 아래 순서로 진행한다.

1. `sample_catalog()` 에 사용자 prompt 와 expected route 를 추가한다.
2. public group tool 중 어떤 action 을 써야 하는지 먼저 정한다.
3. action/options schema 에 필요한 option 이 없다면 `src/photos_mcp/facade/action_options.py` 를 업데이트한다.
4. public router 동작이 필요하면 `src/photos_mcp/facade/public_tools.py` 를 업데이트한다.
5. `tests/test_llm_sample_validation.py` 와 public contract tests 를 추가한다.
6. 이 문서와 `04-mcp-tool-catalog.md`, `05-mcp-call-flows.md`, `07-facade-tool-contracts.md` 를 동기화한다.

새 sample 은 기존 vendor function 을 직접 public contract 로 문서화하지 않는다. LLM-facing route 는 항상 4개 group tool 의 action/options 형태로 표현한다.