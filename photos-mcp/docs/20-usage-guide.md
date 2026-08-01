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

모든 `photos_write`와 `photos_workflow`는 두 단계로 호출한다.

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
    "target_album_name": "가족 베스트"
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

token은 15분 동안 한 번만 유효하다. 앨범 이름, 사진 목록, run 또는 다른 option이 바뀌면 `mutation_plan_changed`로 거부되므로 새 plan을 확인해야 한다.

현재 단계의 workflow plan은 실행 범위와 destination을 먼저 승인하는 scope plan이다. 분석 후 확정된 개별 사진 목록까지 다시 승인하는 상세 `MutationPlan`은 로드맵 Phase 4의 후속 작업이다.

## 5. 권장 사용자 흐름

### 사진을 보기만 할 때

`photos_query(list/search/inspect)`만 사용한다. 이 경로는 앨범을 변경하지 않는다.

### 잘 나온 사진을 고를 때

`photos_select(select_best)` 후 `photos_query(selected)`로 결과를 검토한다. 선별과 앨범 반영을 분리하면 잘못된 쓰기를 줄일 수 있다.

### 단일 앨범에 넣을 때

`select_best → selected 확인 → photos_write(add_selected_to_album) plan → 사용자 승인 → 적용` 순서를 권장한다.

### 카테고리별로 정리할 때

`classify_range → result_detail 확인 → photos_write(organize_by_category) plan → 사용자 승인 → 적용` 순서를 권장한다. 단일 앨범 요청에는 `organize_by_category`를 사용하지 않는다.

## 6. 추가 사용성 개선 후보

현재 `guide`, VLM 상태 노출과 2단계 쓰기 승인은 구현됐다. 다음 개선 효과가 크다.

1. 메뉴 앱에서 mutation plan을 사진 thumbnail과 함께 승인하거나 거부하는 UI
2. Linux 부팅, 원본 다운로드, VLM 분석, 앨범 쓰기 단계를 실시간으로 보여주는 progress timeline
3. “지난 주말 가족 사진”, “여행 베스트 20장” 같은 저장 가능한 recipe와 반복 실행
4. workflow 분석 후 실제 선택된 사진 목록을 대상으로 하는 최종 2차 승인
5. 실패 workflow의 재개 가능한 stage와 예상 변경을 보여주는 재개 승인 화면
6. 대규모 보관함을 위한 cursor pagination과 예상 처리 시간 표시

우선순위는 상세 mutation plan과 메뉴 승인 UI, persistent job 재개 승인, progress timeline 순서가 적절하다.
