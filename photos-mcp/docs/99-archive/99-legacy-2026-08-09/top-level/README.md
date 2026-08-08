# photos-mcp docs

이 디렉터리는 `photos-mcp` 를 처음 이해하는 사람과 실제로 수정/운영하는 사람 모두를 위한 문서 모음이다. 문서는 입문, 구조 이해, tool 사용, 운영 reference 의 네 층으로 나눠 둔다.

## 먼저 알아둘 점

- source of truth 는 `src/` 아래 코드다.
- `build/`, `build-framework-standalone/`, `dist/`, `dist-framework-standalone/`, `*.egg-info/` 는 생성 산출물이다.
- 실제 실행 구조는 `PhotosMcp.app` 이 menu bar app 으로 떠 있으면서 내부에서 localhost `streamable HTTP` MCP daemon 을 여는 방식이다.
- `nanobot` 은 `stdio` child launch 가 아니라 `http://127.0.0.1:18791/mcp` 에 붙는 MCP client 역할만 맡는다.

## 처음 보는 사람에게 추천하는 읽기 순서

1. `../README.md`
2. `01-architecture.md`
3. `02-photo-source.md`
4. `03-photo-ranker.md`
5. `04-mcp-tool-catalog.md`
6. `05-mcp-call-flows.md`
7. `20-usage-guide.md`
8. `07-facade-tool-contracts.md`

이 순서면 `photos-mcp` 가 무엇인지, 왜 `photo-source` 와 `photo-ranker` 가 필요한지, 실제 호출이 어떻게 흘러가는지까지 한 번에 따라갈 수 있다.

## 문서 맵

### 입문 / 개요

- `../README.md`: 제품 개요, 빠른 성공 기준, 전체 문서 진입점
- `01-architecture.md`: 전체 처리 구조, 앱/daemon/vendor/state 관계, 대표 데이터 흐름

### 서브시스템 설명

- `02-photo-source.md`: 사진 조회 계층, 지원 소스, 주요 tool, helper 동작
- `03-photo-ranker.md`: 분석/분류 계층, jobs, review, write-back, end-to-end workflow

### MCP 표면과 사용 흐름

- `04-mcp-tool-catalog.md`: 현재 4개 public tool과 action별 역할 및 내부 매핑
- `05-mcp-call-flows.md`: health, source access, classify job, organize workflow 등 성공 흐름 예시
- `07-facade-tool-contracts.md`: 현재 4개 facade tool의 입력/출력과 승인 계약
- `11-feature-map.md`: endpoint, UI, 테스트 범위, 제약을 요약한 기능 참조 문서
- `18-llm-integration-sample-tests.md`: LLM client 연결 시 사용할 자연어 샘플, expected tool route, 실행 validator 와 최신 report 기준
- `20-usage-guide.md`: 처음 호출하는 순서, Linux Qwen3.6과 안전한 쓰기 방법

이전 surface의 설계 이력은 `06-tool-surface-simplification-direction.md`, `08-legacy-to-facade-mapping.md`, `09-orchestration-layer-design.md`, `10-implementation-and-validation-plan.md`에 남아 있다. 이 문서의 옛 도구명은 현재 사용법이 아니라 전환 당시 기록이다.

### 운영 / 구현 reference

- `12-runtime-lifecycle.md`: bootstrap, single-instance, daemon/state/preflight, terminal helper 계약 상세
- `13-build-and-validation.md`: py2app packaging contract, framework standalone build, smoke/test 검증 흐름
- `14-debugging-guide.md`: 디버깅 순서, generated artifact 구분, 자주 막히는 지점
- `15-refactor-direction.md`: 현재 아키텍처의 근본 문제와 단계별 코드 재정리 방향
- `16-vendor-import-inventory.md`: phase-1 기준 top-level vendor import 목록과 전환 순서
- `17-live-validation-checklist.md`: live endpoint, facade tool, intent/action 별 실검증 체크리스트와 기록 템플릿
- `18-llm-integration-sample-tests.md`: LLM client 연결 시 실제로 호출해 볼 샘플과 sample validator 문서
- `19-linux-vlm-benchmark-2026-08-01.md`: Linux llama.cpp 멀티모달 모델의 동일 사진 집합 비교, 운영 모델 판정과 재현 절차
- `20-usage-guide.md`: Linux Qwen3.6 기본 VLM, `guide` action, 조회·분석과 2단계 쓰기 승인 사용법

### planning

- `planning/README.md`: `PhotosMcp` 전용 planning 문서 인덱스와 읽기 순서
- `planning/01-streamable-http-daemon-redesign-phase1.md`: localhost `streamable HTTP` daemon 구조 재정의 문서
- `planning/02-streamable-http-daemon-implementation-phase1.md`: 위 구조 변경의 phase-1 구현 계획
- `planning/03-mcp-public-tool-surface-redesign-phase1.md`: 현재 4개 group tool로 전환한 설계와 완료 기록
- `planning/04-functional-improvement-roadmap-2026-08-01.md`: 작업 영속성, 안전한 쓰기, VLM과 품질 평가 개선 로드맵

## 어떤 문서를 언제 보면 되는가

### 기능을 이해하고 싶을 때

- `01-architecture.md`
- `02-photo-source.md`
- `03-photo-ranker.md`
- `05-mcp-call-flows.md`
- `06-tool-surface-simplification-direction.md`
- `07-facade-tool-contracts.md`
- `08-legacy-to-facade-mapping.md`
- `09-orchestration-layer-design.md`
- `10-implementation-and-validation-plan.md`

### MCP client 연결이나 tool 사용법을 보고 싶을 때

- `04-mcp-tool-catalog.md`
- `05-mcp-call-flows.md`
- `11-feature-map.md`
- `18-llm-integration-sample-tests.md`
- `20-usage-guide.md`

### 소스 수정 전에 현재 제약과 구조를 파악하고 싶을 때

- `12-runtime-lifecycle.md`
- `13-build-and-validation.md`
- `14-debugging-guide.md`
- `15-refactor-direction.md`

## 현재 작업 기준

기존 packaging과 namespace 리팩터링은 `15-refactor-direction.md`에 완료 기록이 있다. 새 기능은 `planning/04-functional-improvement-roadmap-2026-08-01.md`의 우선순위를 기준으로 진행하고 완료 상태와 검증 결과를 함께 갱신한다.

## 빠른 코드 맵

- `PhotosMcp.py`: app bundle / source 실행 공용 bootstrap
- `src/photos_mcp/main.py`: CLI 모드와 app main entrypoint
- `src/photos_mcp/config.py`: app/runtime/endpoint 기본값과 env override
- `src/photos_mcp/runtime_paths.py`: `~/.photos-mcp` home, runtime/cache/logs 기본 경로와 하위 cache path 계약
- `src/photos_mcp/server.py`: 4개 facade FastMCP tool, `/health`, 쓰기 승인 경계
- `src/photos_mcp/vision_runtime.py`: Linux Qwen3.6 기본 VLM과 실행 정책
- `src/photos_mcp/mutation_approval.py`: write/workflow plan과 일회성 승인 token
- `src/photos_mcp/daemon.py`: uvicorn 기반 HTTP daemon controller
- `src/photos_mcp/menu_app.py`: menu bar popover UI
- `src/photos_mcp/state.py`: daemon 상태, preflight 상태, job snapshot store
- `src/photos_mcp/preflight.py`: Photos library read / automation readiness check
- `src/photos_mcp/vendor_loader.py`: `photo-source`, `photo-ranker` vendored runtime loader
- `src/photos_mcp/packaging.py`: py2app resource staging / bundle naming / build option
- `scripts/build_framework_standalone.sh`: framework Python 기반 standalone bundle build
