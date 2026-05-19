# photos-mcp docs

이 디렉터리는 `photos-mcp`의 구조, 실행 흐름, 기능 표면, 디버깅 기준점, 코드 재정리 방향을 코드 기준으로 정리한 문서 모음이다.

## 먼저 알아둘 점

- source of truth 는 `src/` 아래 코드다.
- `build/`, `build-framework-standalone/`, `dist/`, `dist-framework-standalone/`, `*.egg-info/` 는 생성 산출물이다.
- 실제 실행 구조는 `PhotosMcp.app` 이 menu bar app 으로 떠 있으면서 내부에서 localhost `streamable HTTP` MCP daemon 을 여는 방식이다.
- `nanobot` 은 `stdio` child launch 가 아니라 `http://127.0.0.1:18791/mcp` 에 붙는 MCP client 역할만 맡는다.

## 문서 목록

- `architecture.md`: 컴포넌트 구조, startup 흐름, MCP 요청 흐름, 상태 저장 모델
- `feature-map.md`: endpoint, UI, tool surface, 테스트 범위 등 기능 기준 문서
- `debugging-guide.md`: 디버깅 순서, generated artifact 구분, 자주 막히는 지점
- `refactor-direction.md`: 현재 아키텍처의 근본 문제와 단계별 코드 재정리 방향
- `vendor-import-inventory.md`: Phase 1 기준 top-level vendor import 목록과 전환 순서

## 현재 작업 기준

이슈 수정은 `refactor-direction.md` 의 phase 순서를 우선한다. 특히 현재는 개별 preflight 오류를 먼저 숨기기보다, vendor package namespace 와 `~/.photos-mcp` 앱 전용 runtime/cache ownership 문제를 먼저 정리하는 것이 기준이다.

작업이 완료될 때마다 `refactor-direction.md` 의 checkbox 를 `[x]` 로 바꾸고, 완료 메모에 날짜와 검증 결과를 남긴다. 코드만 고치고 문서의 진행 상태를 업데이트하지 않는 방식은 피한다.

## 빠른 코드 맵

- `PhotosMcp.py`: app bundle / source 실행 공용 bootstrap
- `src/photos_mcp/main.py`: CLI 모드와 app main entrypoint
- `src/photos_mcp/config.py`: app/runtime/endpoint 기본값과 env override
- `src/photos_mcp/server.py`: unified FastMCP server, `/health`, vendored tool 재등록
- `src/photos_mcp/daemon.py`: uvicorn 기반 HTTP daemon controller
- `src/photos_mcp/menu_app.py`: menu bar popover UI
- `src/photos_mcp/state.py`: daemon 상태, preflight 상태, job snapshot store
- `src/photos_mcp/preflight.py`: Photos library read / automation readiness check
- `src/photos_mcp/vendor_loader.py`: `photo-source`, `photo-ranker` vendored runtime loader
- `src/photos_mcp/packaging.py`: py2app resource staging / bundle naming / build option
- `scripts/build_framework_standalone.sh`: framework Python 기반 standalone bundle build

## 이 문서들의 목적

이 문서들은 단순 소개가 아니라, 이후 이슈를 추적할 때 다음 질문에 빠르게 답하기 위한 기준이다.

- 지금 수정해야 하는 source file 이 어디인가?
- bundle 문제인지, source 문제인지, Nanobot 연결 문제인지 어디서 갈리는가?
- health 는 되는데 MCP initialize 가 깨질 때 어느 계층을 봐야 하는가?
- preflight 실패와 MCP endpoint 실패는 어떻게 구분해야 하는가?
- 구조적 문제를 어느 순서로 줄여야 하는가?
