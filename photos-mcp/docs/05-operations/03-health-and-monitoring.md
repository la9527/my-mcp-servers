# 상태와 모니터링

## 상태 계층

Photos MCP 상태는 세 층으로 나눠 확인한다.

| 계층 | 확인 값 | 의미 |
| --- | --- | --- |
| 프로세스·전송 | `/health`의 `status`, `daemon_status` | 요청을 받을 수 있는가 |
| 기능 준비 | `/health/capabilities`의 checks | 사진 읽기·권한·미리보기가 가능한가 |
| 개별 작업 | `photos_query(status/result_summary)` | 특정 실행이 어디까지 진행됐는가 |

`daemon_status=busy`는 장애가 아니라 작업 실행 중 상태다.

## HTTP 점검

```bash
curl -fsS http://127.0.0.1:18791/health | python3 -m json.tool
curl -fsS http://127.0.0.1:18791/health/capabilities | python3 -m json.tool
```

capability의 주요 검사는 다음과 같다.

- `photos_permission`: macOS 사진 접근 권한
- `photos_read`: 사진 보관함 메타데이터 읽기
- `photos_automation`: Apple Events 기반 쓰기 준비
- `photos_thumbnail`: 분석용 이미지 바이트 내보내기

## 로그

앱 로그는 날짜별 디렉토리에 기록된다.

```bash
tail -F "$HOME/.photos-mcp/logs/$(date +%F)/photos-mcp-app.log"
```

최근 오류만 확인할 때:

```bash
rg -n '\[(E|W)\]' "$HOME/.photos-mcp/logs/$(date +%F)"
```

## 작업 상태

MCP에서는 다음 순서로 확인한다.

```text
photos_query(action="status", options={"view":"summary"})
photos_query(action="result_summary", options={"run_id":"<run_id>"})
photos_query(action="result_detail", options={"run_id":"<run_id>"})
photos_query(action="artifacts", options={"run_id":"<run_id>"})
```

앱의 작업 기록 화면은 같은 실행 저장소를 읽는다. UI와 MCP의 결과 수가 다르면 먼저 같은 `run_id`를 보고 있는지 확인한다.

## 저장소 점검

기본 실행 DB는 `~/.photos-mcp/runtime/photo-ranker/jobs.db`다. 운영 중 직접 수정하지 말고 읽기 전용 조회만 사용한다.

```bash
sqlite3 "$HOME/.photos-mcp/runtime/photo-ranker/jobs.db" '.tables'
```

원본 DB를 대상으로 수동 마이그레이션이나 삭제를 수행하지 않는다. 장애 분석용 복사본이 필요하면 앱을 종료한 뒤 DB와 `-wal`, `-shm` 파일을 함께 보존한다.
