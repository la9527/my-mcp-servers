# orchestration layer design

## 1. 목적

이 문서는 `photos-mcp` 의 public MCP surface 를 facade tool 4개로 줄이기 위해, 앱 내부에 어떤 orchestration layer 를 추가해야 하는지 설명한다. 핵심은 `server.py` 가 vendor tool 재등록기가 아니라 facade exporter 가 되도록 경계를 세우는 것이다.

## 2. 현재 구조의 문제

현재는 아래 흐름이 너무 직접적이다.

1. `server.py` 가 vendor tool 을 거의 그대로 노출한다.
2. MCP surface 가 internal implementation detail 과 거의 같은 수준이다.
3. 결과적으로 LLM 이 low-level vendor workflow 를 직접 조합해야 한다.

이 구조는 tool 수와 schema 복잡도를 동시에 키운다.

## 3. 목표 구조

```text
MCP client
  -> facade tools in server.py
     -> facade service layer
        -> vendor adapters
           -> photo-source
           -> photo-ranker
     -> state / result projection
     -> PhotosMcp.app UI
```

핵심은 public MCP layer 와 vendor implementation 사이에 app-owned orchestration layer 를 두는 것이다.

## 4. 권장 파일 구조

권장 새 디렉터리:

```text
src/photos_mcp/facade/
  contracts.py
  status_service.py
  library_service.py
  run_service.py
  result_service.py
  mapping.py
```

각 파일의 역할:

### `contracts.py`

- facade tool input/output 모델
- default option normalization
- action/intent validation

### `status_service.py`

- `photos_status` 구현
- transport, capability, running/latest 요약 생성
- 기존 `build_health_payload()` 와 state snapshot 재사용

### `library_service.py`

- `photos_library` 구현
- `list`, `search`, `inspect` action 처리
- 내부적으로 `photo-source` tool 조합 또는 직접 adapter 호출

### `run_service.py`

- `photos_run` 구현
- `analyze`, `classify`, `curate`, `organize`, `import` orchestration
- 내부적으로 `photo-source` 와 `photo-ranker` 호출 순서 결정

### `result_service.py`

- `photos_result` 구현
- current/latest 실행 결과와 artifacts 반환
- 간단한 cancel 제공

### `mapping.py`

- legacy tool 과 facade intent/action 의 내부 매핑표
- 초기 이행 단계에서 fallback bridge 역할 가능

## 5. 기존 모듈과의 연결 방식

### `server.py`

현재 역할:

- `health_status` 생성
- vendor tool 전체 재등록

목표 역할:

- facade tool 4개만 export
- `/health`, `/health/capabilities` 유지
- facade service 초기화

### `vendor_loader.py`

현재 역할은 유지한다.

- vendor import bootstrap
- `photo-source`, `photo-ranker` runtime 준비

단, 새 구조에서는 `iter_vendor_tools()` 결과를 public tool registry 로 그대로 쓰기보다, facade service 내부 adapter 호출을 위한 준비 계층으로 사용한다.

### `state.py` / `job_state.py`

계속 유지한다.

- running/latest 상태
- active/recent jobs
- job projection

단, public facade 에서는 detailed list 대신 current/latest 중심으로 가공해서 노출한다.

### `menu_app.py`

app UI 의 역할은 유지한다.

- start/stop
- preflight checks
- refresh
- cancel/delete/clear

MCP facade 는 사용자 요청 수행과 결과 조회에 집중하고, 운영성 제어는 UI 쪽에 남긴다.

## 6. facade service 의 책임 분리

### `photos_status`

- state snapshot 요약
- health payload 재사용
- current/latest 실행 상태 요약

### `photos_library`

- source read 계층 전용
- browse 결과 normalization
- `photo_id` anchor 제공

### `photos_run`

- 단건 분석과 long-running workflow 분기
- 내부 substep sequencing
- default option 적용
- run_id 생성 또는 job_id 래핑

### `photos_result`

- current/latest lookup
- result/summary/artifact normalization
- cancel delegation

## 7. internal adapter 전략

1차 구현에서는 vendor source code 를 크게 바꾸지 않는 편이 낫다. 따라서 facade service 는 두 가지 방식 중 하나로 vendor 기능을 사용할 수 있다.

### 방식 A: tool function 직접 호출

- 장점: 기존 코드 재사용이 쉽다.
- 단점: MCP tool 함수 시그니처와 internal service 경계가 섞일 수 있다.

### 방식 B: vendor runtime helper/adapters 추가

- 장점: long-term 구조가 더 깨끗하다.
- 단점: 초기 변경량이 늘어난다.

권장안은 A 로 시작하고, facade 가 안정화되면 B 로 이동하는 것이다. 즉, 1차는 tool function 호출을 wrapping 해서 public surface 만 먼저 줄이고, 2차에 vendor adapter 를 분리한다.

## 8. run_id 전략

facade 구조에서는 `job_id` 를 외부에 그대로 노출할지, facade-level `run_id` 를 둘지 결정해야 한다.

권장안:

- long-running classify 는 `run_id == job_id` 로 시작한다.
- short analyze 는 synthetic `run_id` 를 허용한다.
- 외부에는 항상 `run_id` 를 보여 준다.

이렇게 하면 facade contract 는 일관되게 유지되고, 내부 job model 은 필요할 때만 사용된다.

## 9. legacy compatibility 전략

한 번에 38개 tool 을 없애면 MCP client 나 문서가 동시에 깨질 수 있다. 따라서 이행 단계가 필요하다.

권장 단계:

1. facade tool 4개 추가
2. 문서와 테스트를 facade 우선으로 전환
3. legacy tool 을 default hidden 또는 env flag 뒤로 이동
4. 충분한 검증 뒤 완전 제거 여부 결정

즉, 1차 목표는 “tool 수를 바로 삭제”가 아니라 “default public surface 를 줄이는 것”이다.

## 10. 최소 변경 원칙

이번 구조 변경은 큰 리라이트가 아니라 public surface 경계 재설정으로 보는 것이 맞다. 따라서 아래 원칙을 지킨다.

- vendor runtime 동작은 최대한 유지
- app UI 와 preflight 흐름은 유지
- state/job persistence 는 유지
- public MCP export 와 docs/test 를 먼저 단순화

이 원칙을 지키면 기능 보존과 surface 단순화를 동시에 달성할 수 있다.