# photos-mcp 사용 가이드

## 1. 가장 쉬운 시작 방법

`PhotosMcp.app`을 실행한 뒤 MCP client에서 다음 순서로 확인한다.

1. `photos_query(action="guide", options={"goal": "overview"})`
2. `photos_query(action="status", options={"view": "summary"})`
3. `photos_query(action="list", options={"source": "apple", "limit": 20})`
4. 목록의 `photo_id`로 `photos_select(action="analyze_photo", ...)` 실행

`guide`는 현재 action catalog, 목적별 권장 호출 순서, 쓰기 안전 정책과 VLM 상태를 기계 판독 가능한 JSON으로 반환한다. 지원 goal은 다음과 같다.

- `overview`: 전체 입문 흐름
- `browse`: 사진 조회와 상세 확인
- `analyze`: 단건 또는 범위 분석
- `select`: 우수 사진 선별
- `album`: 단일 앨범 반영
- `categories`: 카테고리 앨범 구성
- `troubleshoot`: 장애 구간 분리

### 1.1 앱 시작과 capability 검사

앱 시작 시 MCP 데몬은 Photos capability 검사보다 먼저 열린다. 시작 과정은 Photos 권한과 라이브러리 읽기만 메뉴 UI 밖에서 확인한다. 강제로 취소할 수 없는 AppleScript 자동화와 thumbnail export 실검사는 시작을 막지 않도록 지연하며, 메뉴의 `Run Checks` 또는 실제 기능 사용 시점에 검증한다. 수동 capability 검사의 기본 제한은 10초이고, timeout된 검사가 아직 끝나지 않은 동안에는 같은 검사를 중복 실행하지 않는다.

### 1.2 LLM 없이 앱에서 직접 분류

메뉴 바의 PhotosMcp 아이콘을 열고 `사진 분류 시작`을 누르면 MCP client나 LLM 대화 없이 분류할 수 있다.

1. `앨범`에서 전체 보관함 또는 특정 앨범을 선택한다.
2. 필요하면 `기간 지정`을 켜고 시작일과 종료일을 `YYYY-MM-DD`로 입력한다.
3. `사진 분류` 또는 `우수 사진 선별`을 선택한다.
4. 분류 기준과 최대 분석 수를 선택한다. 최대 1000장까지 한 작업으로 지정할 수 있다.
5. 후보 사진, 현재 분석 가능 사진, 다운로드 필요 사진 수를 확인한다.
6. `분류 시작`을 누르고 메뉴 팝오버의 진행 작업과 최근 작업에서 결과를 확인한다.

앨범과 기간은 각각 선택 사항이며 함께 사용할 수 있다. 앨범과 기간을 모두 비우거나 후보가 최대 분석 수를 넘는 넓은 범위는 실행 전에 한 번 더 확인한다. 직접 실행은 읽기 전용이며 사진이나 앨범을 변경하지 않는다. 앨범 반영은 기존 `MutationPlan` 검토와 승인을 별도로 거쳐야 한다.

## 2. Linux Qwen3.6 기본 VLM

환경변수를 지정하지 않으면 다음 설정이 적용된다.

| 항목 | 기본값 |
| --- | --- |
| 정책 | `remote_allowed` |
| 공급자 | `linux_qwen36` |
| backend | `openai_compat` |
| 모델 | `Qwen3.6-35B-A3B-Q4_K_M.gguf` |
| Mac endpoint | `http://127.0.0.1:12801/v1` |
| 준비 명령 | `~/bin/ensure-linux-llama-cpp` |
| 준비 timeout | 330초 |

첫 이미지 분석 요청에서 Linux가 꺼져 있으면 Wake-on-LAN으로 부팅하고 llama.cpp 서비스와 SSH 터널을 준비한다. 실제 inference 요청이 Linux idle watcher의 활동 신호가 되므로 photos-mcp는 작업 종료 시 Linux를 직접 끄지 않는다.

현재 상태 확인:

```python
photos_query(action="guide", options={"goal": "analyze"})
```

응답의 `vision_runtime`에서 `provider`, `model`, `status`, `ready`, `on_demand`를 본다. Linux가 꺼져 있어도 `status=on_demand`이면 요청 시 자동 준비할 수 있는 정상 상태다.

분석 또는 선별 작업이 완료된 뒤 `photos_query(action="result_summary")`의 `result_summary.vlm_runtime`에는 실제 실행에 사용한 `provider`, `policy`, `backend`, `model`, `target`, `prompt_version`, `input_max_dimension`, 처리 건수와 VLM 단계 시간이 남는다. API endpoint와 API key, 이미지 base64, 원본 경로는 이 metadata에 기록하지 않는다.

실행 중에는 메뉴 팝오버가 원본 준비·로컬 다운로드·이미지 모델 준비 단계를 분리해 보여 준다. 대기 중인 원인은 경로·사진 식별자 대신 안전한 안내 문구만 표시하며, 모델 공급자는 `Linux Qwen3.6`, `Mac MLX`처럼 사용자가 이해할 수 있는 이름으로 표시한다.

앨범 변경 중 Terminal helper가 시간 초과하거나 응답을 검증하지 못하면 응답의 `error_code`가 각각 `terminal_helper_timeout`, `terminal_helper_invalid_response`처럼 표시된다. 이 경우 변경이 일부 적용됐을 가능성이 있으므로 같은 변경을 즉시 반복하지 말고, 반환된 `mutation_receipt`와 대상 앨범의 실제 상태를 먼저 확인한다.

변경 영수증의 오류는 원본 경로, Terminal 표준 오류, 사진 식별자를 포함하지 않는다. helper 오류는 `terminal_helper_*`, 그 밖의 쓰기 오류는 `mutation_execution_failed` 코드로 재조정 필요 여부만 전달한다.

원격 이미지 전송을 금지할 때는 다음 환경으로 앱을 다시 실행한다.

```bash
PHOTOS_MCP_VLM_POLICY=local_only ~/Applications/PhotosMcp.app/Contents/MacOS/PhotosMcp
```

이 경우 Mac의 MLX VLM을 사용하므로 메모리 사용량과 모델 준비 상태를 별도로 확인해야 한다.

## 3. 조회와 분석

사진 목록 조회:

```python
photos_query(
    action="list",
    options={"source": "apple", "date_from": "2026-07-01", "limit": 50}
)
```

단일 사진 분석:

```python
photos_select(
    action="analyze_photo",
    options={"source": "apple", "photo_id": "...", "wait_for_local": True}
)
```

범위 분석과 결과 조회:

```python
photos_select(
    action="classify_range",
    options={"source": "apple", "album": "여름 휴가", "limit": 100}
)
photos_query(action="result_summary", options={"run_id": "..."})
photos_query(action="result_detail", options={"run_id": "..."})
```

Linux 첫 부팅이 필요한 호출은 평소보다 오래 걸릴 수 있다. background 응답의 `run_id`를 보관하고 같은 분석 요청을 반복하기보다 결과 조회 action을 사용한다.

## 4. 앨범 쓰기 승인

모든 실제 변경은 `MutationPlan` 확인과 승인의 두 단계로 처리한다. 분석 workflow는 먼저 읽기 전용 분석을 수행하고 확정된 대상 계획에서 멈춘다.

첫 호출:

```python
photos_write(
    action="add_selected_to_album",
    options={"run_id": "run-123", "target_album_name": "가족 베스트"}
)
```

첫 응답은 실제 변경 대신 다음 정보를 반환한다.

```json
{
  "status": "awaiting_approval",
  "approval_required": true,
  "approval_token": "...",
  "mutation_plan": {
    "action": "add_selected_to_album",
    "run_id": "run-123",
    "target_album_name": "가족 베스트",
    "photo_ids": ["photo-1", "photo-2"],
    "photo_targets": [
      {"photo_id": "photo-1", "thumbnail_path": "/.../preview/photo-1.jpg"}
    ]
  }
}
```

사용자가 plan을 승인한 뒤 같은 options에 token만 추가한다.

```python
photos_write(
    action="add_selected_to_album",
    options={
        "run_id": "run-123",
        "target_album_name": "가족 베스트",
        "approval_token": "첫 응답의 token"
    }
)
```

token은 15분 동안 한 번만 유효하다. 앨범 이름, 사진 목록, run 또는 다른 option이 바뀌면 `mutation_plan_changed`로 거부되므로 새 plan을 확인해야 한다. 메뉴 앱의 `Pending Photo Changes`에서도 같은 계획을 승인하거나 거절할 수 있다. 완료 응답의 `MutationReceipt`는 확정·미확정 photo ID와 재조정 필요 여부를 제공하며 동일 요청은 `idempotency_key`로 중복 실행되지 않는다. 부분 실패나 timeout 영수증이 있으면 다음 동일 요청은 앨범의 실제 photo ID를 조회해 결과를 재조정하고, 누락 ID가 남으면 새 plan이 필요한 목록을 반환한다.

`curate_to_album`, `curate_to_directory`, `classify_then_organize_by_category`는 승인 없이 분석만 시작한다. 분석 완료 후 `photos_query(action="result_summary")`에서 `status="awaiting_mutation_approval"`, 상세 plan, token과 `next_action`을 확인하고 실제 쓰기를 별도로 승인한다.

## 5. 권장 사용자 흐름

### 사진을 보기만 할 때

`photos_query(list/search/inspect)`만 사용한다. 이 경로는 앨범을 변경하지 않는다.

### 잘 나온 사진을 고를 때

`photos_select(select_best)` 후 `photos_query(selected)`로 결과를 검토한다. 선별과 앨범 반영을 분리하면 잘못된 쓰기를 줄일 수 있다.

### 단일 앨범에 넣을 때

`select_best → selected 확인 → photos_write(add_selected_to_album) plan → 사용자 승인 → 적용` 순서를 권장한다.

### 카테고리별로 정리할 때

`classify_range → result_detail 확인 → photos_write(organize_by_category) plan → 사용자 승인 → 적용` 순서를 권장한다. Apple 결과는 `album_prefix`로 앨범을 만들고, local 결과는 `folder`에 category별 파일을 정리한다. 단일 앨범 요청에는 `organize_by_category`를 사용하지 않는다.

### 중단되거나 실패한 workflow를 다시 실행할 때

앱 재시작으로 중단되었거나 실패한 background workflow는 자동으로 재개되지 않는다. 먼저 저장된 원요청을 확인한다.

```python
photos_query(action="resume_plan", options={"run_id": "중단된 run ID"})
```

내용을 확인한 뒤 `photos_workflow(action="resume", options={"run_id": "..."})`를 호출하면 재개 plan과 일회성 승인 token이 반환된다. 사용자가 명시적으로 승인한 경우에만 같은 options에 token을 추가해 다시 호출한다. 복구 작업은 새 ID를 만들지 않고 기존 `run_id`와 저장된 `filter`·`vlm` checkpoint를 사용해 중단 지점부터 이어진다.

## 6. 추가 사용성 개선 후보

현재 `guide`, VLM 상태 노출, 상세 쓰기 승인, 메뉴 승인 UI, 동일 ID checkpoint 재개와 영속 영수증이 구현됐다. 다음 개선 효과가 크다.

1. Linux 부팅, 원본 다운로드, VLM 분석, 앨범 쓰기 단계를 실시간으로 보여주는 progress timeline
2. “지난 주말 가족 사진”, “여행 베스트 20장” 같은 저장 가능한 recipe와 반복 실행
3. category organize와 import까지 확장한 범용 reconciliation worker
4. 실패 workflow의 완료 checkpoint와 예상 재실행 시간을 보여주는 상세 화면
5. 대규모 보관함을 위한 cursor pagination과 예상 처리 시간 표시

우선순위는 progress timeline, source 계층 통합, 범용 reconciliation 순서가 적절하다.
