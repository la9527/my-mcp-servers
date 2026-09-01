# 작업 기록 신뢰성 있는 삭제와 진행 상태

## 배경

작업 기록은 두 저장소를 함께 사용한다.

- photo-ranker의 `jobs`와 결과·검토·checkpoint 테이블
- Photos MCP workflow의 `workflow_runs`와 `run_events` 테이블

앱 재시작 뒤에는 아직 사용자 승인을 받지 않은 workflow가 `awaiting_resume_approval`로 전환된다. 이 항목은 실행 중인 작업이 아니지만, 이전 구현은 종료 상태(`completed`, `failed`, `cancelled`)만 삭제 대상으로 보아 재개 확인 필요 기록을 영구히 남겼다.

## 삭제 정책

`기록 삭제`와 `전체 기록 삭제`는 다음 상태를 과거 기록으로 간주한다.

| 상태 | 삭제 가능 | 이유 |
| --- | --- | --- |
| `completed` | 예 | 결과 확인이 끝난 작업 |
| `failed` | 예 | 실패 원인 확인 뒤 폐기 가능한 작업 |
| `cancelled` | 예 | 취소되어 더 이상 실행되지 않는 작업 |
| `awaiting_resume_approval` | 예 | 재개할 수 있지만, 사용자가 폐기할 수 있는 복구 대기 기록 |
| `pending`, `running`, `waiting_*`, `writing` | 아니오 | 실제 실행 중이거나 실행 재개 중인 작업 |

개별 삭제와 전체 삭제는 같은 coordinator를 사용한다. 같은 ID에 workflow와 vendor 작업이 모두 있으면 두 저장소를 모두 제거한다.

## 정리 범위와 안전 경계

삭제 coordinator는 다음을 같은 작업 단위로 정리한다.

- vendor job, 결과, 선택 상태, 얼굴 검토, checkpoint
- workflow run과 run event
- `runtime/photo-ranker/artifacts/<job-id>` 결과 JSON, preview, face crop
- 해당 job의 `cache/google-photos-imports` 임시 다운로드 lease
- job asset이 참조하던 `runtime/photo-ranker/terminal-cache` 파일 중 다른 남은 job이 참조하지 않는 파일

전체 기록 삭제에서는 남은 활성 job과 workflow가 참조하지 않는 artifact/terminal-cache 고아 파일도 추가로 정리한다. 모든 파일 삭제는 Photos MCP 관리 root 내부인지 확인하고, 심볼릭 링크와 외부 경로는 삭제하지 않는다.

Apple 사진 원본, 사용자가 선택한 로컬 원본, Google Photos 원본·기존 앨범, 내보낸 디렉터리, 모델 캐시는 삭제 대상이 아니다.

## 대량 삭제 UX

1000건처럼 시간이 걸리는 삭제는 UI 스레드가 아닌 background worker에서 배치로 실행한다. 별도 진행 창에는 다음을 표시한다.

- 현재 단계: 기록과 결과 정리, 미리보기와 임시 파일 정리, 남은 결과 캐시 확인
- `완료 / 전체` 건수와 백분율
- 삭제된 생성 파일 수
- 완료 후 확보한 용량과 부분 실패 항목

삭제가 시작된 뒤에는 중단 버튼을 제공하지 않는다. 중단 시 DB와 파일 정리 범위가 달라질 수 있기 때문이다. 진행 중인 작업은 언제나 삭제 대상에서 제외한다.

## 검증 기준

1. 실패 작업과 재개 확인 필요 workflow가 개별 삭제 뒤 목록·DB·event에서 모두 사라진다.
2. 전체 삭제는 종료·복구 대기 기록을 모두 지우되 활성 job은 유지한다.
3. 1000건 삭제 progress는 `0`에서 `1000`까지 감소 없이 갱신된다.
4. 다른 job이 참조하는 terminal cache와 활성 job artifact는 유지된다.
5. 고아 artifact와 참조 없는 관리 cache만 정리하며 원본·모델·외부 경로는 유지된다.

## 완료 판정 — 2026-09-01

- `DaemonController.delete_job_history()`가 workflow run과 vendor job을 통합 삭제하고, 진행 callback·삭제 파일 수·회수 byte를 보고한다.
- `awaiting_resume_approval` 기록 삭제, 활성 작업 제외, 공유 terminal cache 보존, 고아 artifact/cache 정리를 `tests/test_daemon.py`와 `tests/test_job_state.py`에서 검증한다.
- AppKit 작업 기록 화면은 같은 coordinator를 background worker에서 호출하고 진행 상태를 표시한다.
- 최신 전체 회귀 654건을 통과했으며 원본 사진·모델·외부 경로는 삭제 대상에 포함되지 않는다.
