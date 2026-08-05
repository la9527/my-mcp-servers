# photos-mcp planning docs

이 디렉터리는 `photos-mcp` 자체에서 소유하는 계획 문서를 모은다.

2026-05-19 기준으로 `PhotosMcp` 전용 backlog 문서는 기존 `Nanobot/docs/planning/execution-backlog/` 에서 이 위치로 옮겼다. 이유는 구현 source of truth 가 `photos-mcp` repo 쪽에 있고, Nanobot backlog 와 섞여 있으면 제품 고유 계획과 client-side integration workstream 을 분리해 읽기 어렵기 때문이다.

## 문서 목록

- `01-streamable-http-daemon-redesign-phase1.md`: `PhotosMcp.app` 을 user-launched localhost `streamable HTTP` MCP daemon 으로 재정의하는 구조 변경 문서
- `02-streamable-http-daemon-implementation-phase1.md`: 위 구조 변경안을 실제 구현 작업 묶음으로 내린 phase-1 실행 계획
- `03-mcp-public-tool-surface-redesign-phase1.md`: `photos-mcp` public MCP tool surface 를 `photos_query`, `photos_select`, `photos_write`, `photos_workflow` 4개 group tool 로 재설계하는 phase-1 구조 변경 문서
- `04-functional-improvement-roadmap-2026-08-01.md`: 현재 기능 구조를 다시 분석하고 작업 영속성, 사진 원본 준비, 안전한 쓰기, VLM 독립화와 설명 품질 검증을 중심으로 정리한 개선 로드맵
- `05-menu-popover-ux-redesign-2026-08-01.md`: 메뉴 팝오버의 상태 의미, 정보 위계, 변경 승인 UX와 단계별 native AppKit 개선 계획
- `06-direct-photo-classification-app-2026-08-02.md`: 앨범과 기간을 선택해 LLM 없이 직접 사진 분류를 실행하는 AppKit UX, 공통 서비스 구조와 안전성 계획
- `07-main-app-window-and-status-menu-redesign-2026-08-02.md`: 메뉴 막대 작업 팝오버를 일반 메인 앱 창으로 전환하고 status item은 서버 운영 메뉴로 축소하는 구조·UX 계획
- `08-photo-results-gallery-and-viewer-redesign-2026-08-02.md`: 사진 결과를 화면 비율에 따라 자동 조정되는 3~6열 연속 스크롤 갤러리로 전환하고 고해상도 전체 화면 뷰어를 제공하는 구조·UX 계획
- `09-scene-top2-and-personal-preference-ranking-2026-08-03.md`: 같은 인물·배경의 촬영 장면을 묶어 최대 2장만 추천하고 Apple 사진 즐겨찾기에서 개인 취향을 안전하게 학습하는 품질 개선 계획
- `10-face-quality-and-group-photo-selection-2026-08-04.md`: 인물이 대체로 모두 잘 나온 사진을 우선하는 얼굴 품질 계측과 대표 사진 선택 계획
- `11-result-reselection-and-apple-album-save-2026-08-05.md`: 분석 결과를 다시 고른 뒤 기존 또는 새 Apple 사진 앨범에 안전하게 보관하는 UX·write-back 계획
- `12-selection-and-dual-destination-original-export-2026-08-05.md`: 전체 선택·해제, Apple 사진 앨범 추가, 분류별 원본 복사, 파일명·XMP 메타데이터·부분 실패 재개를 하나의 내보내기 흐름으로 정리한 확장 계획
- `13-local-person-identification-and-human-confirmation-2026-08-05.md`: 얼굴을 로컬에서 묶어 인물 후보를 만들고 사용자가 이름을 최종 확인하는 UI, 암호화 인물 사전, 모델 검증·임계값 보정 계획
- `../../experiments/phase1_5_preflight_2026-08-03/`: Phase 1.5 구현 전의 macOS Vision 런타임·거리 방식·후보 계측 사전 실험과 결론

## 읽는 순서

1. `01-streamable-http-daemon-redesign-phase1.md`
2. `02-streamable-http-daemon-implementation-phase1.md`
3. `03-mcp-public-tool-surface-redesign-phase1.md`
4. `04-functional-improvement-roadmap-2026-08-01.md`
5. `05-menu-popover-ux-redesign-2026-08-01.md`
6. `06-direct-photo-classification-app-2026-08-02.md`
7. `07-main-app-window-and-status-menu-redesign-2026-08-02.md`
8. `08-photo-results-gallery-and-viewer-redesign-2026-08-02.md`
9. `09-scene-top2-and-personal-preference-ranking-2026-08-03.md`
10. `10-face-quality-and-group-photo-selection-2026-08-04.md`
11. `11-result-reselection-and-apple-album-save-2026-08-05.md`
12. `12-selection-and-dual-destination-original-export-2026-08-05.md`
13. `13-local-person-identification-and-human-confirmation-2026-08-05.md`
14. top-level `../01-architecture.md`, `../11-feature-map.md`, `../15-refactor-direction.md`

설계 변경 배경과 목표는 redesign 문서를 먼저 보고, 실제 코드/검증 순서는 implementation 문서를 기준으로 읽는 편이 맞다.
