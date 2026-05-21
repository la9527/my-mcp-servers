# MCP call flows

## 1. 목적

이 문서는 `photos-mcp` 가 실제로 어떻게 호출되고, 내부적으로 어떤 subsystem 을 지나며, 어떤 결과를 기대할 수 있는지를 시나리오별로 설명한다. transport 세부 프레임보다 호출 순서와 책임 분리를 이해하는 데 집중한다.

## 2. 가장 먼저 확인할 흐름

### 흐름 A: 앱 기동과 health 확인

1. `PhotosMcp.app` 실행
2. `GET /health`
3. `GET /health/capabilities`
4. MCP tool `photos_status`

내부 흐름:

1. app 시작
2. daemon 이 `127.0.0.1:18791` 에 MCP server 노출
3. `server.py` 가 state store 기반 health payload 반환

성공 신호:

- `/health` 응답 성공
- `status` 가 `ok` 또는 transport 기준 정상 상태
- `capabilities.checks` 에서 Apple Photos readiness 판단 가능

이 흐름은 “서버가 떴는가”와 “Photos readiness 가 있는가”를 분리해서 볼 수 있다는 점이 중요하다.

## 3. source access 에서 analysis 로 이어지는 흐름

### 흐름 B: Apple Photos 목록 조회 후 단건 분석

1. `photos_library(action="list", source="apple", album="최근", limit=20)`
2. 결과 중 `photo_id` 하나 선택
3. `photos_library(action="inspect", source="apple", photo_id=..., include_thumbnail=true)`
4. `photos_run(intent="analyze", source="apple", photo_id=...)`
5. 필요하면 `photos_result(action="summary")`

내부 흐름:

1. `photos_library` 는 내부적으로 `photo-source` 의 Apple Photos adapter 로 간다.
2. `inspect` 단계에서 thumbnail 과 metadata 가 준비된다.
3. `photos_run(intent="analyze")` 가 내부적으로 `photo-ranker` 분석 함수를 조합한다.

이 흐름은 `photo-source` 와 `photo-ranker` 가 어떻게 연결되는지를 가장 간단하게 보여준다.

예상 결과 예시:

```json
{
  "photo_id": "...",
  "aesthetic_score": 72.4,
  "technical_score": 81.0,
  "total_score": 76.7
}
```

정확한 필드 값은 입력과 모델 상태에 따라 달라질 수 있다.

### 흐름 B-1: iCloud-only Apple 사진을 기다렸다가 analyze 이어가기

1. `photos_library(action="ready_only", source="apple", limit=20)` 로 바로 analyze 가능한 항목을 먼저 확인한다.
2. 원하는 사진이 `local_path_available=false` 이면 Photos 앱에서 해당 사진을 연다.
3. `photos_run(intent="analyze", source="apple", photo_id=..., wait_for_local=true)` 호출
4. 첫 응답에서 `status=running`, `wait_status=waiting_for_local_download`, `next_suggested_action=photos_result` 확인
5. 이후 `photos_result(action="summary", run_id=...)` 로 대기 상태를 본다.
6. 다운로드가 끝나면 같은 `run_id` 로 `photos_result(action="result", run_id=...)` 를 호출해 analyze 결과를 읽는다.
7. 더 이상 기다릴 필요가 없으면 `photos_result(action="cancel", run_id=...)` 로 대기를 취소한다.

내부 흐름:

1. facade 가 현재 asset 이 로컬 경로를 아직 만들지 못했는지 확인한다.
2. `PhotosMcpStateStore` 에 synthetic run 을 저장한다.
3. background task 가 Apple Photos local download 가능 여부를 polling 한다.
4. 준비되면 facade 가 내부적으로 analyze vendor call 들을 이어서 실행한다.
5. 취소되면 synthetic run 이 즉시 `cancelled` 로 전이되고 `photos_status` 와 `photos_result` 에 terminal 상태로 반영된다.

성공 신호:

- waiting 중에는 `wait_status=waiting_for_local_download`
- 완료 후에는 `status=completed`, `result_available=true`
- 취소 후에는 `status=cancelled`, `error_code=cancelled`
- 로컬 다운로드가 끝나지 않으면 terminal 상태가 `status=failed`, `error_code=local_download_timeout` 으로 끝날 수 있다.

## 4. background classify job 흐름

### 흐름 C: classify job 시작부터 결과 조회까지

1. `photos_run(intent="classify", source="apple", source_path="최근", limit=100, selection_profile="general")`
2. 반환된 `run_id` 확보
3. `photos_status(view="running")` 또는 `photos_result(action="summary", run_id=...)` 확인
4. 완료 후 `photos_result(action="result", run_id=...)` 조회

내부 흐름:

1. facade wrapper 가 `photo-ranker` classify 경로를 호출한다.
2. `photo-ranker` queue 가 job 을 생성하고 submit 한다.
3. DB 와 queue 가 source of truth 가 된다.
4. `daemon.py` poller 가 상태를 읽어 `PhotosMcpStateStore` 를 갱신한다.
5. menu UI 와 `/health` 가 active/recent jobs 를 반영한다.

성공 신호:

- `photos_run` 결과에 `run_id` 와 초기 `status` 가 있다.
- `photos_status(view="running")` 또는 `photos_result(action="summary")` 에 current/latest 상태가 보인다.
- `photos_result(action="summary")` 에 `photo_count`, `selected_count`, `preview_path` 같은 요약이 생긴다.
- `photos_result(action="result")` 가 ranked result array 를 반환한다.

대표 응답 형태 예시:

```json
{
  "job_id": "job_123",
  "status": "running",
  "terminal": false,
  "summary_available": false,
  "result_available": false
}
```

job 완료 뒤에는 `summary_available`, `result_available` 이 의미 있게 바뀐다.

## 5. review 흐름

### 흐름 D: 분류 결과 검토 후 선택 결과 export

1. `photos_result(action="selected", run_id=..., top_n=50)`
2. 사용자가 selected 할 사진을 결정
3. 필요하면 app UI 또는 advanced/internal review 경로에서 세부 선택을 조정
4. `photos_result(action="artifacts", run_id=..., output_dir=...)`

내부 흐름:

1. `photo-ranker` DB 에 저장된 result 와 review asset cache 를 읽는다.
2. facade 는 selected 결과 요약 또는 export 경로를 노출한다.
3. export 단계에서 selected=true 인 항목만 뽑아 local directory writer 로 내보낸다.

이 흐름은 “자동 분류 결과를 사람이 최종 선택한다”는 사용자 경험을 설명할 때 핵심이다.

## 6. Apple Photos write-back 흐름

### 흐름 E: classify 결과를 Apple Photos album 으로 정리

1. `photos_run(intent="classify", ...)` 또는 기존 완료 run 확보
2. `photos_run(intent="organize", run_id=..., album_prefix="AI 분류", folder="AI 분류/2026-05")`

또는,

1. `photos_run(intent="organize", source="apple", source_path="최근", album_prefix="AI 분류")`

내부 흐름:

1. `photo-ranker` 가 완료된 결과를 읽는다.
2. Album writer 가 Apple Photos automation 경로를 통해 album 생성/추가를 수행한다.
3. 필요한 경우 Terminal helper mode 가 개입한다.

성공 신호:

- `albums_created`
- `photos_organized`
- `skipped`

대표 응답 형태 예시:

```json
{
  "job_id": "job_123",
  "ranked_count": 84,
  "albums_created": [
    "AI 분류 - travel",
    "AI 분류 - family"
  ],
  "photos_organized": 62,
  "skipped": 22
}
```

이 경로가 실패하면 transport 문제보다 permission 또는 helper/bootstrap 문제일 가능성이 높다.

## 7. end-to-end workflow 흐름

### 흐름 F: 한 번에 분류하고 정리하기

대표 tool:

- `photos_run(intent="organize")`
- `photos_run(intent="curate")`
- `photos_run(intent="import")`

예:

1. `photos_run(intent="organize", source="apple", source_path="최근", album_prefix="AI 분류", selection_profile="general")`

내부적으로는 아래 단계가 이어진다.

1. source 에서 사진 읽기
2. classify/rank 수행
3. 결과 요약 생성
4. Apple Photos 또는 local destination 으로 organize

문제가 생기면 이 workflow 를 그대로 디버깅하지 말고 아래처럼 쪼개는 것이 낫다.

1. `photos_library(action="list")`
2. `photos_library(action="inspect")`
3. `photos_run(intent="analyze")` 또는 `photos_run(intent="classify")`
4. `photos_result(action="result")`
5. `photos_run(intent="organize")`

## 8. `photos-mcp` wrapper 가 추가로 해 주는 일

이 문서의 모든 흐름에서 공통으로 들어가는 wrapper 동작이 있다.

### vendor runtime 준비

- 각 tool 호출 전에 `prepare_vendor_runtime()` 이 실행된다.
- source 실행과 bundle 실행에서 같은 vendor import 이름을 유지한다.

### job 응답 정규화

- `job_id`
- `status`
- `terminal`
- `finished_at`
- `summary_available`
- `result_available`

이 필드들은 job 계열 응답을 UI 와 health 가 일관되게 이해하도록 맞춰 준다.

### state store 갱신

- active jobs
- recent jobs
- background job running 여부

즉, `photos-mcp` 는 tool 전달만 하는 게 아니라 “보여 주기 좋은 시스템 상태”를 함께 유지한다.

## 9. 실패를 분해하는 기준

시나리오가 실패했을 때는 아래 순서로 좁히는 것이 가장 빠르다.

1. `/health` 는 되는가
2. `/health/capabilities` 에서 Photos readiness 는 되는가
3. `photos_library(action="list")` 는 되는가
4. `photos_library(action="inspect")` 또는 단건 분석은 되는가
5. `photos_run(intent="classify")` 는 되는가
6. write-back 단계만 실패하는가

이 순서대로 보면 transport, source access, ranking, write-back 중 어디가 문제인지 빠르게 갈린다.