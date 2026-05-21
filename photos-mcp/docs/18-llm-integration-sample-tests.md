# LLM 연동 목표 프롬프트 검증

## 1. 목적

이 문서는 `photos-mcp` 를 LLM client 에 연결할 때 실제 목표로 삼는 자연어 프롬프트 3개를 기준으로, 어떤 facade tool route 로 실행해야 하는지와 현재 validator 가 어떤 내부 정책값으로 그 흐름을 재현하는지를 정리한다.

이번 기준은 generic smoke 샘플이 아니다. 아래 3개 프롬프트가 실제 처리 의도다.

1. `iCloud 사진 중 작년 4월 16일~작년 4월30일 사진들만 잘 나온 사진들만 앨범을 따로 저장해 만들어줘.`
2. `로컬 ~/SamplePhotos 디렉토리에 잘 나온 사진들을 골라서 iCloud 에 적절한 이름으로 앨범을 만들어 저장해줘.`
3. `iCloud 사진 중 작년 4월 16일~작년 4월30일 사진들 중 특정인의 사진만 뽑아서 잘 나온 사진들을 로컬의 특정(~/temp) 디렉토리에 저장해줘.`

validator 는 LLM reasoning 자체를 평가하지 않는다. 대신 위 프롬프트가 planner 에 의해 적절한 `photos_status`, `photos_run`, `photos_result` 조합으로 번역되었다고 가정할 때, 실제 MCP endpoint 가 그 요구를 수행할 수 있는지를 검증한다.

## 2. 내부 정책값

사용자 프롬프트에는 드러나지 않지만 validator 와 orchestration 이 내부적으로 아래 값을 사용한다.

### 2.1 잘 나온 사진의 기본 정의

- 기본 selection profile: `general`
- 인물 중심 시나리오 profile: `person`
- 기본 selection rule: 상위 `30%`
- score 기준:
  - `general` 인 경우 `quality_score`
  - `person` 인 경우 `total_score`

즉 “잘 나온 사진”은 현재 구현 기준으로 `curate_best_photos` 의 top-percent selection 과 profile-aware scoring 결과를 따른다.

### 2.2 스크린샷 제외 정책

세 목표 프롬프트는 모두 내부적으로 `exclude_screenshots=true` 로 실행한다.

현재 exclusion heuristic 은 아래 단서를 기준으로 화면 캡처 가능성이 높은 결과를 제외한다.

- source path
- `scene_description`
- review note
- `photo_id`

대표 keyword 예시는 아래와 같다.

- `screenshot`
- `screen shot`
- `screen capture`
- `screen recording`
- `browser window`
- `application window`

### 2.3 특정인 값

특정인은 alias 가 아니라 실제 person 이름을 사용한다.

우선순위는 아래와 같다.

1. `--target-person`
2. `PHOTOS_MCP_LLM_TARGET_PERSON`
3. target date 의 Apple Photos 목록에서 자동 발견한 첫 번째 실제 person 이름

해당 날짜에 person metadata 가 없으면 세 번째 시나리오는 skip 처리한다.

## 3. 시나리오별 tool route

### scenario 0. 연결 상태 요약

- user prompt:
  `photos-mcp 연결 상태와 현재 준비 상태를 알려줘.`
- expected tools:
  `photos_status`
- 목적:
  transport 와 capability 상태를 먼저 확인한다.

### scenario 1. 작년 4월 16일~4월 30일 iCloud best photo 를 별도 앨범에 저장

- user prompt:
  `iCloud 사진 중 작년 4월 16일~작년 4월30일 사진들만 잘 나온 사진들만 앨범을 따로 저장해 만들어줘.`
- expected tools:
  `photos_run(intent="curate")` -> `photos_run(intent="cleanup_album")`
- 내부 실행값:
  - `source="apple"`
  - `date_from=<target-year>-04-16`
  - `date_to=<target-year>-04-30`
  - `writeback_mode="album"`
  - `selection_profile="general"`
  - `exclude_screenshots=true`
- 검증 목표:
  Apple Photos write-back 이 실제로 성공하고, validation 용 앨범이 바로 cleanup 되는지 확인한다.

### scenario 2. `~/SamplePhotos` 에서 best photo 를 골라 iCloud 앨범에 저장

- user prompt:
  `로컬 ~/SamplePhotos 디렉토리에 잘 나온 사진들을 골라서 iCloud 에 적절한 이름으로 앨범을 만들어 저장해줘.`
- expected tools:
  `photos_run(intent="curate")` -> `photos_result(action="selected")` -> `photos_run(intent="import")` -> `photos_run(intent="cleanup_album")`
- 내부 실행값:
  - `source="local"`
  - `source_path=~/SamplePhotos`
  - `writeback_mode="review"`
  - `selection_profile="general"`
  - `exclude_screenshots=true`
- 검증 목표:
  local 분류 결과에서 selected set 을 만들고, 그 selected source path 만 Apple Photos 앨범으로 import 한 뒤, validation 앨범을 cleanup 한다.

### scenario 3. 작년 4월 16일~4월 30일 특정인 best photo 를 로컬 디렉토리에 저장

- user prompt:
  `iCloud 사진 중 작년 4월 16일~작년 4월30일 사진들 중 특정인의 사진만 뽑아서 잘 나온 사진들을 로컬의 특정(~/temp) 디렉토리에 저장해줘.`
- expected tools:
  `photos_run(intent="curate")` -> `photos_result(action="artifacts")`
- 내부 실행값:
  - `source="apple"`
  - `person=<actual person name>`
  - `date_from=<target-year>-04-16`
  - `date_to=<target-year>-04-30`
  - `selection_profile="person"`
  - `exclude_screenshots=true`
  - `output_dir=~/temp/<generated-subdir>`
- 검증 목표:
  특정인 필터 + best-shot 선별 결과 중 selected photo 를 local directory 로 export 한다.

## 4. validator 사용법

기준 스크립트:

- `scripts/validate_llm_samples.py`

기본 실행:

```bash
cd /Volumes/ExtData/my-mcp-servers/photos-mcp
./.venv/bin/python scripts/validate_llm_samples.py \
  --report-path docs/llm-sample-validation-report-latest.md
```

실행 중 progress 관측:

- 기본값으로 단계별 progress 가 stderr 에 즉시 출력된다.
- 같은 내용이 날짜별 로그 파일 `~/.photos-mcp/logs/YYYY-MM-DD/llm-sample-validation.log` 에도 누적된다.
- 별도 로그 위치가 필요하면 `--log-path <path>` 를 준다.
- 조용한 실행이 필요하면 `--quiet-progress` 를 준다.

필요한 override:

- `--target-person <실제 person 이름>`
- `--samplephotos-dir <local input dir>`
- `--local-output-dir <local export root>`
- `--target-year`, `--target-start-month`, `--target-start-day`, `--target-end-month`, `--target-end-day`

동일한 값은 env 로도 줄 수 있다.

- `PHOTOS_MCP_LLM_TARGET_PERSON`
- `PHOTOS_MCP_LLM_SAMPLEPHOTOS_DIR`
- `PHOTOS_MCP_LLM_OUTPUT_DIR`

## 5. live 실행 전제

아래 전제가 맞아야 3개 시나리오가 모두 pass 후보가 된다.

1. `PhotosMcp.app` 이 최신 source 변경을 반영한 설치본이어야 한다.
2. `http://127.0.0.1:18791/health` 가 `status=ok` 여야 한다.
4. target date range 인 `작년 4월 16일~4월 30일` 에 실제 Apple Photos asset 이 있어야 한다.
4. `~/SamplePhotos` 가 존재해야 한다.
5. target date asset 에서 person metadata 를 발견할 수 있거나 `--target-person` 이 지정되어야 한다.

앱 최신화가 필요하면 아래 순서가 기준이다.

```bash
cd /Volumes/ExtData/my-mcp-servers/photos-mcp
./scripts/build_framework_standalone.sh
/Volumes/ExtData/Nanobot/infra/scripts/run-photos-mcp-app.sh
```

## 6. 최신 실행 결과

최신 report:

- `docs/llm-sample-validation-report-latest.md`

2026-05-21 현재 기준에서 validator 는 아래 시나리오를 대상으로 한다.

- `status-summary`: pass
- `apple-apr16to30-best-to-album`
- `local-samplephotos-best-to-album`
- `apple-apr16to30-person-best-to-local-dir`

최신 pass/skip/fail 상세는 `docs/llm-sample-validation-report-latest.md` 를 source of truth 로 본다. 문서 본문에는 고정 결과를 복제하지 않고, validator 재실행 후 report 를 갱신하는 방식을 기준으로 유지한다.

## 7. 유지 기준

새로운 목표 프롬프트나 내부 정책을 추가하면 아래를 같이 맞춘다.

1. 이 문서의 시나리오와 내부 정책값을 갱신한다.
2. `src/photos_mcp/llm_sample_validation.py` 의 scenario catalog 와 runner 를 같이 갱신한다.
3. 필요한 facade/runtime parameter 를 `photos_run` 또는 `photos_result` 에 반영한다.
4. `tests/test_llm_sample_validation.py` 와 관련 단위 테스트를 갱신한다.
5. live endpoint 에 대해 validator 를 다시 실행하고 `docs/llm-sample-validation-report-latest.md` 를 갱신한다.