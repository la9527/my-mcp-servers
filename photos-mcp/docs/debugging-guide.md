# photos-mcp 디버깅 가이드

## 1. 가장 먼저 구분할 질문

이슈를 볼 때 아래 순서대로 층을 나눠야 한다.

1. app process 가 실제로 떠 있는가?
2. `/health` 는 열리는가?
3. `/mcp` initialize / list_tools 는 되는가?
4. preflight 가 깨지는가, 아니면 MCP transport 가 깨지는가?
5. source 실행만 깨지는가, bundle 실행만 깨지는가?

이 순서를 건너뛰면 서로 다른 문제를 한 덩어리로 섞어서 보게 된다.

현재는 개별 증상보다 구조적 import/runtime 문제가 더 큰 원인이다. 원인 수정의 장기 순서는 `refactor-direction.md` 를 먼저 따른다.

## 2. 기본 체크리스트

### process / lock

- `PhotosMcp is already running. Lock file: ...` 가 나오면 먼저 stale lock 여부를 본다.
- 목표 lock 경로는 `~/.photos-mcp/runtime/photos-mcp.lock` 이다.
- 실제 프로세스가 없는데 lock 만 있으면 해당 lock 이 stale 한 상태다.
- 구버전 wrapper 또는 명시적 legacy env override 때문에 `~/.nanobot/runtime/photos-mcp/photos-mcp.lock` 이 보일 수 있다. 기본 경로는 아니다.

### health

- `PhotosMcp --health` 는 lightweight self-report 다.
- `curl http://127.0.0.1:18791/health` 는 실제 HTTP daemon 이 떠 있는지 확인하는 단계다.

두 검사는 의미가 다르다. 전자는 app binary/entrypoint 확인, 후자는 daemon bind 와 app runtime 확인이다.

### MCP handshake

다음 단계는 `initialize`, `list_tools`, `list_resources`, `list_prompts` 다.

여기까지 통과하면 MCP server 자체는 정상이다. 이후 남는 문제는 대체로 Apple Photos read/write path 또는 helper/bootstrap 경로다.

## 3. 계층별로 봐야 할 파일

### startup / import bootstrap

- `PhotosMcp.py`
- `src/photos_mcp/main.py`

### bundle-only import 문제

- `src/photos_mcp/main.py`
- `src/photos_mcp/daemon.py`
- `src/photos_mcp/vendor_loader.py`
- `src/photos_mcp/vendor/photo-source/scripts/*`
- `src/photos_mcp/vendor/photo-ranker/scripts/*`
- `src/photos_mcp/packaging.py`

### health / MCP endpoint 문제

- `src/photos_mcp/server.py`
- `src/photos_mcp/daemon.py`
- `src/photos_mcp/state.py`

### Apple Photos read 문제

- `src/photos_mcp/preflight.py`
- `src/photos_mcp/vendor/photo-source/server.py`
- `src/photos_mcp/vendor/photo-source/sources/apple_photos.py`

### Apple Photos automation / write 문제

- `src/photos_mcp/preflight.py`
- `src/photos_mcp/vendor/photo-ranker/album_writer.py`
- `src/photos_mcp/vendor/photo-ranker/scripts/apple_photos_terminal_runner.py`
- `src/apple_terminal_helper/**`

## 4. generated artifact 와 source 를 혼동하지 않기

app bundle 안에도 `photos_mcp/*.py` 가 복사돼 있고, `build/` 아래에도 복사본이 생긴다. 하지만 수정 대상은 항상 `src/` 다.

잘못 보기 쉬운 경로:

- `build/lib/photos_mcp/**`
- `build-framework-standalone/lib/photos_mcp/**`
- `dist-framework-standalone/PhotosMcp.app/Contents/Resources/lib/**`

이 경로들은 진단할 때는 유용하지만, 수정은 `src/` 에서 해야 한다.

## 5. 자주 헷갈리는 증상

### 증상 A: `--health` 는 되는데 `curl /health` 는 안 된다

의미:

- binary / entrypoint 는 살아 있다.
- daemon thread 가 안 떴거나 startup 중간에 죽었다.

우선 볼 곳:

- `daemon.py`
- wrapper launch 로그

### 증상 B: `/health` 는 되는데 `/mcp` initialize 가 500 난다

의미:

- HTTP server 는 떠 있다.
- FastMCP request handling, lifespan, vendored runtime import, helper bootstrap 중 하나가 깨졌다.

우선 볼 곳:

- `server.py`
- `daemon.py`
- wrapper 로그의 traceback

### 증상 C: MCP tool registration 은 되는데 preflight 만 실패한다

의미:

- transport 문제는 아니다.
- Apple Photos read path 또는 Apple Events automation path 문제다.

`photos_read`:

- `osxphotos` import
- bundle site-packages bootstrap
- Apple Photos DB 접근

`photos_automation`:

- Terminal helper 경로
- `apple_terminal_helper` import
- Apple Events permission prompt / timeout

### 증상 D: direct source 실행은 되는데 app bundle 에서만 깨진다

의미:

- 거의 항상 path/bootstrap/packaging 문제다.

우선 확인:

- bundled `sys.path`
- vendored helper script bootstrap
- `packaging.py` resource inclusion
- `build_framework_standalone.sh` environment setup

## 6. Nanobot 연동 확인 포인트

`nanobot` 에서는 wrapper 또는 live config 기준으로 아래만 확인하면 된다.

- app 이 먼저 떠 있는가
- `http://127.0.0.1:18791/mcp` 가 열리는가
- `connect_mcp_servers()` 에서 tool 이 등록되는가

여기서 통과하면 Nanobot 측 transport 설정은 대체로 정상이고, 남은 문제는 `photos-mcp` 내부다.

## 7. 현재 문서화된 중요한 관찰

- stale lock 은 실제 프로세스 부재와 구분해야 한다.
- MCP registration 성공과 preflight 성공은 다른 신호다.
- helper subprocess 는 bundle Python 과 vendored package root bootstrap 이 모두 맞아야 한다.
- `vendor_loader.py` 는 source 실행과 bundle 실행을 연결하는 핵심 접점이다.

## 8. 구조적 수정 전 판단 기준

아래 증상은 한 줄짜리 import patch 로 끝내기보다 아키텍처 리팩터링 범주로 본다.

- `models`, `sources`, `album_writer`, `db`, `jobs` 같은 top-level import 충돌
- source 에서는 되지만 bundle 에서만 깨지는 dependency import
- app 본체에서는 되지만 Terminal helper subprocess 에서만 깨지는 import
- Nanobot wrapper 가 sibling `mcp-my-photos` 경로 또는 Nanobot 전용 runtime/cache 경로를 기본값으로 사용하는 문제
- `/health` 는 error 이지만 MCP list_tools 는 통과하는 readiness 혼동

이 경우 우선순위는 다음과 같다.

1. package namespace 정리
2. `~/.photos-mcp` runtime/cache ownership 정리
3. packaging contract 정리
4. helper bootstrap 통합
5. health/capability readiness 분리
