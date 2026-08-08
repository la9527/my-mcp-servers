# 설정

Photos MCP는 별도 설정 파일보다 환경 변수를 우선 사용한다. 앱 bundle을 Finder에서 실행하면 셸의 일시적 환경이 전달되지 않을 수 있으므로, 운영 설정은 앱을 실행하는 주체에서 일관되게 주입해야 한다.

## 앱과 HTTP

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `PHOTOS_MCP_BUNDLE_PATH` | `~/Applications/PhotosMcp.app` | 설치 앱 경로 |
| `PHOTOS_MCP_HOST` | `127.0.0.1` | MCP bind host |
| `PHOTOS_MCP_PORT` | `18791` | MCP port |
| `PHOTOS_MCP_STREAMABLE_HTTP_PATH` | `/mcp` | MCP path |
| `PHOTOS_MCP_HEALTH_PATH` | `/health` | health path |
| `PHOTOS_MCP_START_DAEMON_ON_LAUNCH` | `true` | 앱 시작 시 daemon 자동 시작 |
| `PHOTOS_MCP_JOB_POLL_INTERVAL_SECONDS` | `2.0` | UI 작업 상태 갱신 간격 |

일부 항목은 이전 통합을 위한 `NANOBOT_PHOTOS_MCP_*` 별칭도 읽지만, 새 설정은 `PHOTOS_MCP_*` 이름을 사용한다.

## 데이터 경로

| 변수 | 기본값 |
| --- | --- |
| `PHOTOS_MCP_HOME` | `~/.photos-mcp` |
| `PHOTOS_MCP_RUNTIME_ROOT` | `~/.photos-mcp/runtime` |
| `PHOTOS_MCP_CACHE_ROOT` | `~/.photos-mcp/cache` |
| `PHOTOS_MCP_LOGS_ROOT` | `~/.photos-mcp/logs` |
| `PHOTO_RANKER_RUNTIME_ROOT` | `<runtime>/photo-ranker` |
| `PHOTO_RANKER_VLM_CACHE_ROOT` | `<cache>/vlm` |
| `PHOTO_RANKER_MODEL_CACHE_ROOT` | `<cache>/models/photo-ranker` |
| `PHOTO_SOURCE_CACHE_ROOT` | `<cache>/photo-source` |

## 권장 원칙

- port를 변경하면 Nanobot 연결 URL도 함께 변경한다.
- runtime과 cache를 같은 디렉토리로 지정하지 않는다.
- runtime의 SQLite DB와 승인 영수증은 임의로 삭제하지 않는다.
- 대용량 모델 캐시는 여유 공간이 있는 볼륨으로 옮길 수 있지만 앱 실행 계정이 읽고 쓸 수 있어야 한다.
- VLM 관련 설정은 [이미지 분석 런타임](../03-integration/04-vision-runtime.md)을 따른다.

## 임시 실행 예시

```bash
PHOTOS_MCP_PORT=18792 \
PHOTOS_MCP_HOME="$HOME/.photos-mcp-test" \
./.venv/bin/python -m photos_mcp.main
```

운영 앱과 테스트 앱을 동시에 실행하려면 port, home, bundle identity의 충돌 가능성을 모두 검토해야 한다. 단순히 port만 바꿔도 단일 인스턴스 잠금 경로가 같으면 두 번째 실행이 거부될 수 있다.
