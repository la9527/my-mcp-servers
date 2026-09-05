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

## 추천 사진 보관

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `PHOTOS_MCP_RECOMMENDATION_ROOT` | `/Volumes/ExtData/02_Services/PhotosMcp/recommendations` | 촬영일별 추천 primary 파일과 manifest를 보존하는 비공개 관리 root |
| `PHOTO_RANKER_APPLE_EVENTS_MODE` | `terminal` | Apple Photos 앨범 쓰기를 제한시간이 있는 Terminal helper로 격리. 진단 목적 외에는 `direct` 사용 금지 |
| `PHOTO_RANKER_ALBUM_TERMINAL_TIMEOUT_SECS` | `240` | Apple Photos 앨범 생성·가져오기 helper의 최대 대기 시간 |
| `PHOTOS_MCP_RECOMMENDATION_DEFAULT_DESTINATION` | `apple_photos` | 새 월별 그룹의 2차 목적지: `apple_photos`, `google_photos`, `local_only` |
| `PHOTOS_MCP_RECOMMENDATION_APPLE_FOLDER` | `Photos MCP` | Apple Photos에 생성하는 관리 앨범의 상위 folder |

추천 root가 `/Volumes/<name>/...` 아래에 있으면 해당 외장 볼륨이 연결되지 않은
경우 내장 디스크로 fallback하지 않고 저장을 실패 처리한다. root와 날짜 폴더는
`0700`, 사진과 manifest는 `0600` 권한을 적용한다. 이 경로를 HTTP, WebUI 또는
Tailscale serve 대상으로 직접 연결하지 않는다.

추천 앨범 발행 receipt는 실제 album ID가 생기기 전 `managed:<group>` 임시 ID를
가질 수 있다. 재시도 후 실제 ID가 생기면 동일한 안정적 `receipt_id`를 갱신한다.
사진 가져오기 이후 receipt 저장 중 오류가 나면 즉시 다시 가져오지 말고, 먼저
대상 앨범의 존재·album ID·사진 수와 앱 로그의 `imported` 수를 대조해
reconciliation한다.

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
