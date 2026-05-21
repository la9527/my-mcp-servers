# MCP tool surface simplification direction

## 1. 왜 이 문서가 필요한가

현재 `photos-mcp` 는 `photo-source` 와 `photo-ranker` 의 세부 tool 을 거의 그대로 외부 MCP surface 에 노출한다. 구현 재사용에는 유리하지만, LLM 이 tool 을 선택하고 안정적으로 호출하는 관점에서는 복잡도가 너무 높다.

현재 상태 요약:

- diagnostic tool 1개
- `photo-source` tool 5개
- `photo-ranker` tool 32개
- 총 38개 MCP tool 노출

이 구조는 고성능 LLM 에서는 다룰 수 있어도, 일반적인 모델에서는 아래 문제가 커진다.

- 유사한 tool 사이에서 선택이 흔들린다.
- 단계형 workflow 를 모델이 직접 조합해야 한다.
- job 관리 개념이 과도하게 전면 노출된다.
- 결과적으로 tool 사용 성공률보다 tool 설명 길이와 schema 복잡도가 더 커진다.

이 문서의 목표는 MCP 표면을 3~4개 수준의 facade tool 로 줄이는 기본 방향을 고정하는 것이다.

## 2. 핵심 원칙

### app-owned orchestration 으로 바꾼다

외부 MCP client 는 세부 vendor tool 조합을 몰라도 된다. 대신 `PhotosMcp.app` 이 내부에서 필요한 `photo-source` 와 `photo-ranker` 호출 순서를 한 번에 orchestrate 한다.

### 기본값을 강하게 준다

source, limit, selection profile, write-back mode 같은 값은 가능한 한 앱 내부 기본값을 사용한다. 외부 tool 입력은 intent 와 소수의 옵션만 받는 방향이 맞다.

### job 은 내부 구현으로 내린다

job queue 와 DB 는 그대로 유지할 수 있지만, 외부 MCP 표면에서는 상세 lifecycle 관리보다 “실행 중인가”, “마지막 결과는 무엇인가”, “취소가 필요한가” 정도만 보이게 한다.

### 세부 vendor tool 은 internal-only 로 돌린다

`photo-source`, `photo-ranker` 의 현재 tool 들은 삭제보다 internal service layer 로 내리는 쪽이 안전하다.

## 3. 권장 public MCP surface

1차 기본안은 4개 tool 이다.

### `photos_status`

역할:

- app 상태
- transport health
- capability readiness
- 현재 실행 중 작업 요약
- 마지막 실행 결과 요약

대체 대상:

- `health_status`
- 일부 job status 확인 흐름

### `photos_library`

역할:

- 사진 목록 조회
- 검색
- 특정 사진 inspect

권장 action:

- `list`
- `search`
- `inspect`

대체 대상:

- `list_photos`
- `get_metadata`
- `get_thumbnail`
- 일부 `search_photos`

원칙:

- 기본 source 는 `apple`
- 기본 limit 는 작게 둔다
- thumbnail/metadata 포함 여부는 옵션으로 켠다

### `photos_run`

역할:

- 분석
- 분류
- curate
- organize
- import

권장 intent:

- `analyze`
- `classify`
- `curate`
- `organize`
- `import`

대체 대상:

- 분석 tool 전반
- `start_classify_job`
- `organize_results`
- `classify_and_organize`
- `curate_best_photos`
- `import_and_organize`

원칙:

- 모델은 low-level tool sequence 대신 intent 만 고른다
- app 이 내부에서 필요한 source read, classify, write-back 단계를 조합한다

### `photos_result`

역할:

- 현재 또는 마지막 실행 결과 조회
- 요약 / 상세 결과 / 산출물 / 선택 결과 확인
- 필요한 경우 간단한 cancel

권장 action:

- `summary`
- `result`
- `artifacts`
- `selected`
- `cancel`

대체 대상:

- `get_job_summary`
- `get_job_result`
- `get_review_items`
- `export_selected_photos` 일부 흐름
- `cancel_job` 일부 흐름

## 4. 왜 3개가 아니라 4개부터 시작하는가

처음부터 3개로 줄일 수도 있지만, `status/result` 와 `library/browse` 를 너무 일찍 합치면 응답 shape 가 지나치게 커진다. 그러면 오히려 LLM 이 어떤 action 과 어떤 필드를 써야 하는지 다시 혼란스러워진다.

그래서 1차는 4개 tool 이 안정적이다.

- `photos_status`
- `photos_library`
- `photos_run`
- `photos_result`

이 구조가 안정화되면 2차로 `photos_result` 를 `photos_status` 로 흡수해 3개로 줄일지 판단할 수 있다.

## 5. app UI 와 MCP 의 역할 분리

이미 `PhotosMcp.app` UI 는 아래 기능을 갖고 있다.

- daemon start/stop
- preflight checks
- active job refresh
- cancel job
- delete job
- clear recent jobs

따라서 MCP 쪽에서 상세 job 관리 tool 을 계속 전면 노출할 이유가 약하다. 운영성 action 은 app UI 에 남기고, MCP 는 사용자 요청 수행과 결과 확인에 집중하는 편이 맞다.

## 6. 내부 구조 변경 방향

### 현재

- `server.py` 가 vendor tool 을 거의 그대로 re-export 한다.
- MCP surface 와 internal service layer 경계가 얇다.

### 목표

- `server.py` 는 facade tool 만 export 한다.
- 새 app-owned orchestration layer 가 내부 workflow 를 조합한다.
- vendor tool 은 internal implementation detail 로 내려간다.

권장 내부 층:

1. public MCP facade layer
2. app orchestration service layer
3. vendor adapters
4. state / job projection

## 7. 기능 매핑 기본안

### `photo-source` 5개 tool

- 전부 `photos_library` 로 흡수

### `photo-ranker` 분석 tool

- `photos_run(intent="analyze")` 로 흡수

### `photo-ranker` background job tool

- 실행은 `photos_run(intent="classify")`
- 결과 확인은 `photos_result`

### review / write-back / workflow tool

- `photos_run(intent="curate"|"organize"|"import")`
- 후속 결과 확인은 `photos_result`

### known face / detailed review editing

- 1차 public surface 에서는 제외
- 필요하면 이후 `advanced` profile 또는 debug surface 로 분리

## 8. 단계별 추진 순서

1. 새 public tool contract 4개를 확정한다.
2. 기존 38개 tool 을 새 facade 로 매핑하는 표를 만든다.
3. app-owned orchestration layer 를 추가한다.
4. `server.py` 를 facade export 방식으로 전환한다.
5. legacy tool 은 internal-only 또는 feature flag 뒤로 숨긴다.
6. docs, tests, call flow 문서를 새 표면에 맞게 갱신한다.
7. 실제 `list_tools` 와 대표 시나리오로 검증한다.

## 9. 이 문서를 기준으로 다음에 만들어야 할 산출물

이 방향이 컨펌되면 다음 산출물을 먼저 만드는 것이 맞다.

1. `07-facade-tool-contracts.md`: 4개 facade tool 의 input/output contract 초안
2. `08-legacy-to-facade-mapping.md`: 기존 38개 tool 에 대한 기능 매핑표
3. `09-orchestration-layer-design.md`: orchestration layer 파일 구조 제안
4. `10-implementation-and-validation-plan.md`: 구현 단계별 검증 계획

이 문서는 “어떤 방향으로 단순화할 것인가”를 고정하는 기준 문서다. 실제 API schema 와 파일 구조는 다음 단계 문서에서 더 구체화한다.