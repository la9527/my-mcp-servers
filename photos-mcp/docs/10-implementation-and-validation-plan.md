# implementation and validation plan

## 1. 목적

이 문서는 facade tool 4개 체계로 넘어가기 위한 실제 구현 순서와 검증 기준을 정리한다. 이번 작업의 핵심은 기능 삭제가 아니라 public MCP surface 를 단순화하면서 내부 동작은 유지하는 것이다.

## 2. 구현 원칙

- 1차 목표는 “4개 facade tool 이 기본 public surface 가 되는 것”이다.
- vendor runtime 과 app UI 는 가능한 한 그대로 유지한다.
- legacy tool 은 즉시 삭제보다 hidden/internal 단계로 옮긴다.
- live 검증은 `PhotosMcp.app` 기준으로 한다.

## 3. 단계별 구현 계획

### Phase A. facade contract 와 scaffold 추가

작업:

- `src/photos_mcp/facade/` 디렉터리 추가
- facade input/output contract 모델 추가
- `photos_status`, `photos_library`, `photos_run`, `photos_result` scaffold 추가

완료 기준:

- `server.py` 에서 facade tool 을 import 할 수 있다.
- unit level 에서 action/intent validation 이 동작한다.

### Phase B. `photos_status` 와 `photos_library` 구현

작업:

- existing health/state payload 를 `photos_status` 에 연결
- `list`, `search`, `inspect` 를 `photos_library` 에 연결
- default source/limit/include 동작 정의

완료 기준:

- MCP `list_tools` 에 facade tool 이 노출된다.
- `photos_status` 로 health 요약을 볼 수 있다.
- `photos_library` 로 Apple Photos browse 와 inspect 가 된다.

### Phase C. `photos_run` / `photos_result` 구현

작업:

- `intent=analyze` 구현
- `intent=classify` 구현
- `summary/result/cancel` 중심의 `photos_result` 구현
- `run_id` 와 current/latest lookup 규칙 확정

완료 기준:

- 단건 analyze 시나리오가 된다.
- classify 실행 후 summary/result 를 facade 경로로 조회할 수 있다.

### Phase D. organize / curate / import workflow 흡수

작업:

- `intent=curate`
- `intent=organize`
- `intent=import`
- result artifact normalization

완료 기준:

- 기존 end-to-end workflow 대표 시나리오가 facade 경로로 재현된다.

### Phase E. legacy surface 축소

작업:

- legacy tool export 를 default hidden 또는 env flag 뒤로 이동
- docs 를 facade 기준으로 갱신
- tests 와 call flow 기준을 facade 우선으로 전환

완료 기준:

- 기본 MCP `list_tools` 에 4개 facade tool 만 보인다.
- 필요 시 debug/legacy 모드에서만 old surface 를 확인할 수 있다.

## 4. 검증 계획

### 문서/계약 검증

- facade contract 문서와 code schema 가 일치하는지 확인
- old-to-new mapping 과 실제 구현 intent 가 어긋나지 않는지 확인

### unit 검증

- action/intent validation
- default option normalization
- run_id/current/latest resolution
- result payload normalization

### integration 검증

- `build_server()` 기준 tool 등록 확인
- MCP `initialize` / `list_tools` 확인
- `photos_status` 응답 확인
- `photos_library(list/inspect)` 응답 확인
- `photos_run(analyze/classify)` 응답 확인
- `photos_result(summary/result)` 응답 확인

### live smoke 검증

- `PhotosMcp.app` 실행
- `curl /health`
- MCP client 로 facade tool 4개만 보이는지 확인
- Apple Photos browse 1회
- analyze 1회
- classify 1회
- organize 또는 curate 1회

## 5. 대표 검증 시나리오

### 시나리오 1. 앱 상태 확인

1. `photos_status(view="summary")`
2. `photos_status(view="checks")`

기대 결과:

- transport 와 capabilities 를 한 번에 확인 가능

### 시나리오 2. browse 후 analyze

1. `photos_library(action="list", source="apple", limit=10)`
2. `photos_library(action="inspect", photo_id=..., include=["thumbnail", "metadata"])`
3. `photos_run(intent="analyze", photo_id=...)`
4. 필요 시 `photos_result(action="summary")`

### 시나리오 3. classify 후 결과 확인

1. `photos_run(intent="classify", source="apple", scope={...})`
2. `photos_status(view="running")`
3. `photos_result(action="summary", run_id="latest")`
4. `photos_result(action="result", run_id="latest")`

### 시나리오 4. organize workflow

1. `photos_run(intent="organize", target={...})`
2. `photos_result(action="artifacts", run_id="latest")`

## 6. 리스크와 대응

### 리스크 1. facade input shape 가 다시 커질 수 있음

대응:

- action/intent 는 4개 tool 내에서 좁게 유지
- default option 을 강하게 둠
- 1차에는 advanced 기능 제외

### 리스크 2. legacy tool 제거가 너무 빠를 수 있음

대응:

- hidden/internal 단계 거침
- env flag 기반 legacy export 유지 가능

### 리스크 3. result model 이 job model 과 어긋날 수 있음

대응:

- facade-level `run_id` 규칙을 먼저 고정
- current/latest 중심으로 단순화

## 7. 구현 착수 전에 확정해야 할 것

실제 코드 작업에 들어가기 전에 최소 아래 세 가지는 고정해야 한다.

1. facade tool 이름 최종 확정
2. `run_id` 규칙 확정
3. legacy tool 을 숨기는 방식 확정

이 세 가지가 고정되면, 이후 구현은 비교적 기계적으로 진행할 수 있다.