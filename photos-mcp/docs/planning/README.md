# photos-mcp planning docs

이 디렉터리는 `photos-mcp` 자체에서 소유하는 계획 문서를 모은다.

2026-05-19 기준으로 `PhotosMcp` 전용 backlog 문서는 기존 `Nanobot/docs/planning/execution-backlog/` 에서 이 위치로 옮겼다. 이유는 구현 source of truth 가 `photos-mcp` repo 쪽에 있고, Nanobot backlog 와 섞여 있으면 제품 고유 계획과 client-side integration workstream 을 분리해 읽기 어렵기 때문이다.

## 문서 목록

- `01-streamable-http-daemon-redesign-phase1.md`: `PhotosMcp.app` 을 user-launched localhost `streamable HTTP` MCP daemon 으로 재정의하는 구조 변경 문서
- `02-streamable-http-daemon-implementation-phase1.md`: 위 구조 변경안을 실제 구현 작업 묶음으로 내린 phase-1 실행 계획
- `03-mcp-public-tool-surface-redesign-phase1.md`: `photos-mcp` public MCP tool surface 를 `photos_query`, `photos_select`, `photos_write`, `photos_workflow` 4개 group tool 로 재설계하는 phase-1 구조 변경 문서

## 읽는 순서

1. `01-streamable-http-daemon-redesign-phase1.md`
2. `02-streamable-http-daemon-implementation-phase1.md`
3. `03-mcp-public-tool-surface-redesign-phase1.md`
4. top-level `../01-architecture.md`, `../11-feature-map.md`, `../15-refactor-direction.md`

설계 변경 배경과 목표는 redesign 문서를 먼저 보고, 실제 코드/검증 순서는 implementation 문서를 기준으로 읽는 편이 맞다.
