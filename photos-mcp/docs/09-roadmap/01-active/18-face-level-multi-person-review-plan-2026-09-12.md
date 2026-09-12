# 얼굴 단위 다인물 사진 검토·Android 이름 입력 구현 계획

작성일: 2026-09-12

상태: 사용자 승인 후 첫 운영 수직 기능과 확정 얼굴 기반 이름 추천 구현·배포 완료 — 무인 자동 확정은 calibration gate 통과 후 후속

적용 범위: PhotosMcp private identity 원장, 얼굴 인덱싱, Apple Photos 이름 후보, macOS 인물관리, Android Companion, Story 인물 evidence

관련 문서: [운영 인물 인덱싱·재검증·통합 관리 UX 계획](17-operational-people-indexing-and-review-ux-plan-2026-09-12.md), [인물 이름 연계 Story·인물별 사진 탐색 개선 계획](16-person-linked-story-and-people-gallery-plan-2026-09-11.md)

## 1. 결론

현재 Apple Photos 이름 후보 검토는 사진 전체를 한 인물에 연결하는 것처럼 보이므로 단체 사진에서 잘못된 결정을 유도한다. 수정 기준 단위는 사진이 아니라 **사진 안의 각 얼굴 관측**이어야 한다.

```text
현재
  Apple 이름 후보 → 사진 전체 → 인물 한 명

권장
  사진 전체 문맥
    ├─ 얼굴 1 crop → 기존 인물 A
    ├─ 얼굴 2 crop → 새 인물 B + 사용자 입력 이름
    ├─ 얼굴 3 crop → 기존 인물 C
    ├─ 얼굴 4 crop → 얼굴 아님
    └─ 얼굴 5 crop → 나중에

  Apple 이름 후보는 어느 얼굴에 해당하는지 고를 때 쓰는 보조 힌트
```

한 사진의 모든 얼굴을 강제로 이름 붙이지는 않는다. 각 얼굴은 `기존 인물`, `새 인물`, `얼굴 아님`, `나중에` 중 하나로 독립 처리할 수 있고, 확정된 여러 인물만 같은 사진의 Story evidence에 함께 들어간다.

기존 DB를 전면 교체하거나 Story 형식을 다시 만들 필요는 없다. 현재 구조는 이미 한 얼굴에 확정 인물 하나, 한 사진에 여러 얼굴과 여러 인물을 표현할 수 있고 Story도 사진별 여러 `person_refs`를 받을 수 있다. 필요한 것은 다음 연결 계층이다.

1. Apple alias 사진 우선 얼굴 인덱싱
2. 정규화 얼굴 위치와 고해상도 검토 crop
3. 사진별 얼굴 검토 projection
4. Apple alias와 사용자가 선택한 얼굴의 versioned 연결
5. 한 사진의 여러 결정을 한 번에 적용하는 원자적 application service
6. macOS와 Android의 동일한 얼굴 검토 queue

## 2. 세 에이전트 독립 검토 종합

| 검토 관점 | 독립 확인 | 통합 결정 |
|---|---|---|
| 얼굴 파이프라인·DB | 사진 하나에 여러 identity를 저장할 수 있으나 alias 확인은 얼굴 membership이 아니라 사진 전체 association만 생성한다. alias 사진과 현재 69개 얼굴 사진의 교집합도 0장이다. | alias 사진 지정 색인, alias-face assignment, 사진별 인덱스 상태, 원자적 batch transaction을 추가한다. |
| macOS AppKit UX | 얼굴 crop과 context preview는 만들어졌지만 얼굴 검토 목록·해결 UI가 없고 실제 bbox 좌표도 보존되지 않는다. | 전체 사진+bbox, 얼굴 crop strip, 선택 얼굴 inspector의 3영역 검토 화면을 만든다. |
| Android Material UX | 0.7.4는 Apple alias 전체 사진만 보여주며 69개 face review는 개수만 표시한다. 회전 시 얼굴·이름 draft도 보존되지 않는다. | 별도 `PeopleReviewActivity`, crop/context 전환, 이름 입력, 저장 후 다음 얼굴, draft 복구, Snackbar undo를 구현한다. |

세 검토가 공통으로 권장한 원칙은 다음과 같다.

- 얼굴 모델은 후보와 crop만 만들고 동일인·이름을 자동 확정하지 않는다.
- Apple Photos 이름은 신원 정답이 아니라 “이 사진에 이 이름이 있을 수 있음”이라는 provider hint다.
- 단체 사진은 사진별 queue로 열고 그 안에서 모든 얼굴을 각각 검토한다.
- 얼굴별 결정은 stable face membership이 권위 데이터다.
- 얼굴을 특정할 수 없는 경우에만 사용자가 명시한 asset-level association을 fallback으로 사용한다.
- Mac과 Android는 같은 review revision과 같은 application service를 사용한다.
- Story 갱신 실패가 이미 저장된 인물 결정을 되돌리면 안 된다.

## 3. 최신 운영 데이터로 확인한 문제 규모

2026-09-12 private 저장소를 개인정보 문자열 없이 집계한 결과다.

| 항목 | 값 | 의미 |
|---|---:|---|
| 논리 Apple alias | 13건 | 후보·확정을 합한 현재 이름 힌트 |
| alias 대상 사진 | 10장 | 같은 사진에 여러 alias가 존재할 수 있음 |
| 현재 후보 alias | 10건 / 7장 | 아직 사용자 검토가 필요한 이름 힌트 |
| 이미 확정된 alias | 3건 | 기존 사진 단위 association을 보존해야 함 |
| 한 사진의 최대 alias | 2건 | 실제로 이름 후보가 여러 개인 사진 존재 |
| 활성 face observation | 69건 / 19장 | 얼굴 단위 후보 기반은 이미 생성됨 |
| 다인물 사진 | 14장 | 얼굴이 2개 이상인 사진 |
| 한 사진의 최대 얼굴 | 11개 | 모바일에서도 사진별 얼굴 navigation 필요 |
| candidate identity | 43개 | 실명이 아닌 익명 자동 후보 그룹 |
| pending face review | 69건 | 현재 앱에서는 건수만 보이고 열 수 없음 |
| owner-confirmed face membership | 0건 | 얼굴 단위 사용자 확정은 아직 없음 |
| legacy membership hold | 37건 | 과거 결정의 별도 복구 대상 |

가장 중요한 발견은 **Apple alias 13건이 속한 10장과 기존 69개 얼굴이 속한 19장의 교집합이 0장**이라는 점이다. 지금 `인물 찾기`를 최근 추천 50장으로 다시 실행해도 alias 사진이 반드시 포함되지 않는다. 단체 사진 alias UX를 만들기 전에 alias 대상 `local_asset_id`를 우선순위 scope로 지정 색인해야 한다.

기존 인덱싱 얼굴 수 분포는 다음과 같다.

| 사진별 얼굴 수 | 사진 수 |
|---:|---:|
| 1개 | 5장 |
| 2개 | 5장 |
| 3개 | 3장 |
| 5개 | 2장 |
| 6개 | 1장 |
| 9개 | 2장 |
| 11개 | 1장 |

따라서 얼굴 1개인 간단한 사진뿐 아니라 5~11명 단체 사진까지 첫 실제 검증 자료가 이미 존재한다.

## 4. 현재 구조에서 유지할 부분과 바꿀 부분

### 4.1 유지

- `face_observations`: 한 사진 안의 각 얼굴을 별도 안정 관측으로 표현
- `membership_state_versions`: 한 얼굴과 identity의 versioned 연결
- `current_owner_confirmed_memberships`: 한 얼굴에 확정 identity 하나만 허용
- `asset_person_association_versions`: 사진 하나에 여러 identity 허용
- `person_review_item_versions`: 얼굴별 검토 항목 기반
- `face_artifact_refs`: private crop과 context 참조
- Story의 `build_story_person_evidence()`: face membership과 asset association을 합쳐 사진별 여러 사람을 중복 없이 구성
- mobile owner session, ECDSA 서명, nonce, idempotency key, opaque handle

### 4.2 변경

- 사진 전체를 보여주고 즉시 identity에 연결하는 현재 alias UI
- alias 확인 시 얼굴 선택 없이 asset association만 만드는 기본 동작
- 최근 추천 최대 50장만 보는 고정 인덱싱 범위
- 얼굴 bbox의 hash만 저장하고 실제 좌표를 버리는 구조
- 112px 정렬 crop을 사용자 검토용 이미지로 그대로 쓰는 구조
- face review를 숫자로만 표시하는 mobile overview
- HTTP handler에서 여러 repository mutation을 순차 호출하는 구조
- 프로세스 메모리에만 보존되는 mobile command 성공 receipt
- `나중에`를 아무 write 없이 처리해 다른 기기와 동기화되지 않는 구조

## 5. 목표 데이터 모델: schema v4~v5 additive migration

기존 row를 수정·삭제하지 않고 다음 원장을 추가한다.

### 5.1 사진별 얼굴 인덱스 상태

```text
asset_face_index_versions
  local_asset_id
  model_fingerprint
  index_revision
  index_run_id
  index_state
    pending / running / completed / no_face / failed / stale
  detected_face_count
  error_code
  created_at / completed_at
```

이를 통해 UI가 다음 상태를 구분한다.

- 아직 얼굴 분석하지 않음
- 얼굴이 실제로 0개
- 얼굴 1개
- 얼굴 여러 개
- 이미지 디코딩 실패
- 모델 준비 실패
- 이전 모델 관측만 존재
- 재분석 필요

### 5.2 얼굴 geometry

현재 bbox fingerprint는 안정 observation ID에 유지하고, UI용 좌표는 별도 private 원장에 저장한다.

```text
face_observation_geometry
  face_observation_id
  geometry_revision
  x_norm / y_norm / width_norm / height_norm
  oriented_source_width / oriented_source_height
  orientation_revision
  state / created_at
```

좌표는 방향 보정이 끝난 이미지 기준 `0.0~1.0` 정규화 값으로 저장한다. Mac과 Android의 다른 화면 크기, 확대·축소에서도 같은 얼굴 위치를 표시할 수 있다.

### 5.3 검토용 얼굴 artifact

SFace embedding용 112px aligned crop과 사람이 확인하기 위한 crop을 분리한다.

```text
face_review_artifact_versions
  face_observation_id
  artifact_revision
  review_crop_ref
  context_preview_ref
  highlighted_context_ref
  state / created_at
```

검토 crop 정책:

- bbox보다 상하좌우 25~35% 여유를 둔 정사각형
- 작은 얼굴도 확인할 수 있도록 320~512px JPEG
- EXIF와 불필요 metadata 제거
- 얼굴이 작거나 흐리면 `확인 어려움` 품질 배지와 전체 문맥을 함께 표시
- embedding 값이나 embedding 파일은 API에 노출하지 않음

### 5.4 Apple alias와 얼굴 연결

alias 자체에 face ID를 직접 추가하지 않고 provider hint와 사용자 결정을 분리한다.

```text
provider_alias_face_assignment_versions
  alias_id / reviewed_alias_revision
  face_observation_id
  assignment_revision
  person_identity_id
  assignment_state
    candidate / owner_confirmed / rejected / conflicted / superseded
  provenance / decision_policy_version
  decision_group_id
  created_at / resolved_at
```

규칙:

- alias 하나는 한 사진 안의 선택 얼굴 하나에만 owner-confirmed 할 수 있다.
- 같은 face는 하나의 owner-confirmed identity만 가질 수 있다.
- 같은 사진에는 여러 alias와 여러 identity가 존재할 수 있다.
- alias 수와 얼굴 수가 같아도 배열 순서로 자동 연결하지 않는다.
- 이름 문자열이 같아도 자동 병합하지 않는다.
- alias 없는 얼굴도 기존 인물·새 인물로 직접 연결할 수 있다.

### 5.5 command receipt와 Story refresh outbox

```text
identity_command_receipts
  device_fingerprint / idempotency_key
  request_hash / command_kind
  result_json / completed_at

story_people_refresh_outbox
  outbox_id / decision_group_id
  affected_asset_ids_hash
  state / attempt_count / last_error
  created_at / completed_at
```

서버가 재시작되어도 같은 idempotency key와 body에는 이전 성공 결과를 반환하고, Story 갱신은 인물 transaction과 분리하되 유실되지 않게 재시도한다.

## 6. 단일 application service와 transaction 경계

Mac과 Android가 공통으로 사용하는 `PeopleWorkspaceService`에 다음 명령을 둔다.

```text
list_asset_people_reviews(cursor, state)
asset_people_review_detail(review_handle)
apply_asset_people_review(...)
undo_people_decision(...)
```

`apply_asset_people_review`는 한 사진에 대한 현재 draft를 한 번의 `BEGIN IMMEDIATE` transaction으로 적용한다.

검증 순서:

1. review, alias, face, identity handle의 기기·만료·revision 확인
2. 모든 alias와 face observation이 같은 사진에 속하는지 확인
3. 현재 모델 generation의 active observation인지 확인
4. 한 face가 요청 안에서 두 identity에 연결되지 않았는지 확인
5. 같은 사진의 다른 얼굴에 같은 identity를 지정하지 않았는지 기본 검사
6. 새 identity와 사용자 입력 이름 생성
7. 기존 candidate membership 종료
8. owner-confirmed face membership 생성
9. alias-face assignment와 alias 상태 갱신
10. `얼굴 아님` observation과 review 상태 갱신
11. `나중에` 상태와 다시 노출할 시점 저장
12. 비어 버린 candidate identity의 hidden/superseded 처리
13. 대표 얼굴이 없으면 첫 user-confirmed crop을 대표 후보로 등록
14. 하나의 `decision_group_id`로 audit과 undo 근거 저장
15. 같은 transaction에서 Story refresh outbox 등록
16. persistent command receipt 기록

transaction 밖에서는 Story presentation을 갱신한다. Story 갱신에 실패해도 얼굴·이름 결정은 성공으로 유지되고 outbox가 재시도한다.

## 6.1 확정 얼굴 기반 이름 후보와 일치 가능성

사용자가 얼굴을 이름에 직접 연결하면 그 얼굴의 private embedding을 해당 인물의 **확정 anchor**로 사용한다. 새 얼굴은 같은 모델 family·fingerprint의 anchor에만 비교한다. 사진 한 장에 여러 crop이 생긴 경우 과대평가하지 않도록 서로 다른 사진 수를 별도 지지 표본으로 센다.

schema v5는 기존 원장을 수정하지 않고 다음 versioned 원장을 추가한다.

```text
face_identity_suggestion_versions
  face_observation_id / suggestion_revision
  person_identity_id
  confidence_estimate
  top_similarity / robust_similarity
  runner_up_similarity / similarity_margin
  supporting_face_count / supporting_asset_count
  suggestion_tier
    insufficient / suggested / ready_to_confirm
  policy_version / created_at
```

UI에 표시하는 값은 raw cosine을 그대로 백분율로 바꾼 값이 아니다. 최고 후보의 상위 확정 얼굴 점수, 두 번째 인물 후보와의 차이, 서로 다른 확정 사진 수, 검출 품질을 합성한 `모델 추정 일치 가능성`이다. 실제 확률로 보정됐다고 주장하지 않으며 raw embedding과 similarity는 API·Story·공유 HTML에 노출하지 않는다.

### 현재 승격 정책

| 단계 | 조건과 동작 |
|---|---|
| 표본 부족 | 확정 anchor 또는 후보 간 차이가 부족하면 이름을 제시하지 않는다. |
| 추천 | 절대 유사도와 최소 지지가 있으면 `이 사람일 가능성 N%`를 표시하고 사용자가 추천 적용 또는 다른 이름을 선택한다. |
| 확인 준비 | 서로 다른 확정 사진 3장 이상, 강한 절대 유사도, 2순위와 충분한 차이, 종합 추정 90% 이상이면 검토 draft에 이름을 미리 선택한다. 사용자는 사진 단위 `저장하고 다음`만 누르면 되며 선택 변경·모르는 사람 무시가 가능하다. |
| 무인 자동 확정 | 현재는 사용하지 않는다. 독립 holdout에서 false-match 상한 5% 이하, 클래스별 100쌍 이상, 실제 사용자 교정률 기준을 통과하고 사용자가 설정에서 명시적으로 켠 뒤에만 고려한다. |

높은 신뢰도의 이름도 저장 전에는 `owner_confirmed` membership이 아니다. 따라서 잘못된 추천이 다른 얼굴의 학습 anchor가 되는 연쇄 오염을 만들지 않고 Story 이름에도 들어가지 않는다. 사용자가 저장하면 기존 원자 transaction·audit·Story outbox를 그대로 사용한다.

### 저장 버튼 정책

Mac과 Android는 얼굴별 선택을 로컬 draft로 관리하고 서버에는 사진 단위 batch로 보낸다.

- `중간 저장`: 현재 결정한 얼굴만 한 batch로 원자 저장하고 나머지는 pending 유지
- `저장하고 다음`: 모든 얼굴이 확정, 얼굴 아님 또는 명시적 나중에일 때 사진을 완료하고 다음 사진으로 이동
- 충돌이 있으면 사진 완료를 막고 충돌 resolver로 이동
- 미사용 Apple 이름 hint는 경고하지만 사진 완료를 강제로 막지 않음

## 7. 인덱싱 및 기존 데이터 전환

### 7.1 인덱싱 우선순위

`index_recommendation_assets(limit)` 고정 대신 명시적인 asset scope를 추가한다.

```text
index_assets(
  local_asset_ids=[...],
  scope_kind="provider_alias_review",
  model_fingerprint=...,
  force_generation=false
)
```

처리 우선순위:

1. 미해결 Apple alias가 있는 사진
2. 사용자가 Mac/Android에서 직접 연 사진
3. 현재 Story에 있지만 얼굴 인덱스가 없는 사진
4. 최신 추천 사진
5. 아직 인덱싱하지 않은 나머지 사진

### 7.2 기존 13 aliases

- 기존 확정 alias 3건의 asset-level association은 Story 회귀를 막기 위해 보존한다.
- 새 얼굴이 발견돼도 기존 association을 자동 제거하거나 자동 이전하지 않는다.
- `얼굴 연결 정밀화` review로 제시하고 사용자가 확인하면 face membership을 권위 근거로 추가한다.
- 후보 alias 10건은 현재 상태로 유지한 채 대상 사진 7장을 우선 색인한다.
- 기존 sentinel alias는 삭제하지 않고 versioned `invalid_provider_label` 또는 quarantined 상태로 전환해 반복 등록을 막는다.

### 7.3 기존 69 observations

- identity, observation, membership, review row를 삭제하지 않는다.
- 같은 모델로 19장을 다시 읽어 normalized geometry와 고해상도 review crop만 보강한다.
- stable observation ID가 같으면 기존 candidate membership과 review를 그대로 사용한다.
- 사진별 review group projection에 69건을 묶는다.
- 후보 그룹 전체를 한 사람으로 자동 확정하지 않는다.

### 7.4 얼굴 0개 또는 얼굴을 특정할 수 없는 사진

asset-level association은 다음 경우에만 별도 명시 액션으로 허용한다.

- 얼굴이 검출되지 않음
- 뒷모습·가림·너무 작은 얼굴로 특정 불가능
- 사용자가 전체 사진을 보고 `사진에 이 사람이 있음`을 직접 선택

얼굴이 여러 개인 사진에서는 기존 alias confirm/create endpoint의 즉시 실행을 차단하고 `face_selection_required`를 반환한다. 얼굴 분석 전인 사진은 `얼굴 분석 후 연결` 상태로 바꾼다.

## 8. macOS AppKit UX

macOS는 전체 검토·충돌·대량 수정의 기준 화면으로 둔다.

```text
┌─────────────────────────────────────────────────────────────────────────┐
│ 인물 검토     사진 4/19 · 얼굴 2/5 · 전체 미검토 67   [저장하고 다음] │
├───────────────────────────────────────┬─────────────────────────────────┤
│ 전체 사진 문맥                        │ 선택한 얼굴                      │
│                                       │                                 │
│  [1 확정]     [2 선택]                │       [큰 review crop]           │
│                                       │                                 │
│                 [3 미검토]            │ Apple 이름 힌트: [이름A] [이름B]│
│                                       │ [기존 인물 선택 ▾] [연결]       │
│                                       │ [새 이름 입력_______] [등록]    │
│                                       │ [얼굴 아님] [나중에]            │
├───────────────────────────────────────┴─────────────────────────────────┤
│ [얼굴1 ✓] [얼굴2 선택] [얼굴3] [얼굴4 얼굴 아님] [얼굴5 나중에]      │
└─────────────────────────────────────────────────────────────────────────┘
```

구조:

- `NSSplitViewController`: queue / 전체 문맥 / 선택 얼굴 inspector
- `NSCollectionView`: 얼굴 crop strip과 identity 선택 목록
- custom `NSView`: normalized bbox overlay와 hit testing
- `NSUndoManager`: decision group 단위 `⌘Z`
- `NSPopover` 또는 sheet: 새 이름, batch 확인, 충돌 해결
- 전체 화면 rebuild 대신 selection, zoom, scroll, 이름 draft를 유지하는 상태형 controller

상태는 색상만으로 구분하지 않는다.

- 확정: 체크 아이콘 + 이름
- 선택: 굵은 테두리 + `선택됨`
- 미검토: 점선 + 번호
- 충돌: 경고 아이콘 + `충돌`
- 얼굴 아님: 취소선 아이콘 + `얼굴 아님`
- 나중에: 시계 아이콘 + `나중에`

## 9. Android UX

Android도 얼굴 구분, 기존 인물 연결, 새 이름 입력까지 완전한 첫 검토 경로를 제공한다. macOS의 3열 화면을 축소하지 않고 세로형 단계 UI로 구성한다.

```text
인물 확인                              사진 4 / 19
이 사진의 얼굴 2 / 5 확인

┌──────────────────────────────────────┐
│ 전체 사진 / 선택 얼굴 크게 보기      │
│ 선택 얼굴 위치 또는 번호가 강조됨    │
└──────────────────────────────────────┘

[얼굴1 ✓] [얼굴2 선택] [얼굴3] [얼굴4] [얼굴5]

Apple Photos 이름 힌트: [이름A] [이름B]

[기존 인물에 연결]
[새 인물로 등록]
[얼굴 아님]                         [나중에]

[중간 저장]                    [저장하고 다음]
```

### 9.1 화면 구조

- 기존 `MainActivity.java`에 계속 누적하지 않고 `PeopleReviewActivity.java`를 분리한다.
- `PeopleApiRepository`, typed `ReviewPhoto`, `ReviewFace`, `IdentityChoice` 모델을 분리한다.
- 첫 단계는 Java/View 구조를 유지하고 Material Components, RecyclerView, ViewModel/SavedStateHandle을 추가한다.
- 전체 Compose 재작성은 선행조건으로 두지 않는다.
- 현재 `ZoomableImageView`를 전체 문맥 확대·축소 기반으로 재사용하고 bbox overlay 동기화를 추가한다.
- 좁은 휴대폰은 single-column, tablet·landscape는 context와 inspector의 two-pane으로 전환한다.

### 9.2 얼굴 선택

- crop 시각 크기 64~72dp, 실제 터치 영역 최소 48dp
- crop strip을 좌우 스크롤하고 선택 시 큰 이미지와 전체 문맥 위치를 동기화
- `전체 사진 / 얼굴 크게` toggle 제공
- 결정 성공 후 TalkBack focus와 시각 선택을 다음 미확인 얼굴로 이동
- 얼굴이 11개 이상이어도 현재 번호, 완료 수, 남은 수를 항상 표시

### 9.3 기존 인물 연결

searchable bottom sheet에 다음을 보여준다.

- 대표 얼굴
- 사용자 확정 이름 또는 가족 호칭
- 연결된 사진 수
- 이 사진의 다른 얼굴에 이미 선택된 인물은 비활성화하고 이유 표시

### 9.4 새 이름 입력

Material `TextInputLayout` sheet에서 선택 얼굴 crop과 전체 문맥을 다시 보여준다.

- 이름·가족 호칭 1~80자
- 앞뒤·중복 공백 정규화
- 제어문자 거절
- 입력 중 회전·백그라운드 전환에도 draft 보존
- bitmap과 만료 handle은 saved state에 넣지 않고 화면 복구 시 새 handle 발급
- 사용자 확인 뒤 identity, 이름, face membership, review 상태를 한 transaction으로 생성

### 9.5 뒤로가기와 복구

```text
열린 bottom sheet 닫기
  → 얼굴 확대 닫기
  → 사진 상세에서 queue
  → 인물 허브
  → 이전 앱 화면
```

404 만료 handle이나 409 stale revision이 발생해도 입력한 이름과 draft 결정을 지우지 않는다. 최신 review를 다시 가져와 바뀐 상태를 보여주고 사용자가 다시 확인하면 새 action handle로 제출한다.

### 9.6 접근성

- `단체사진 얼굴 2, 선택됨, 확인 전`
- `얼굴 1, 인물 A로 연결됨`
- `얼굴 4, 얼굴 아님으로 처리됨`
- 진행률 `5개 중 2개 확인`을 live region으로 안내
- 상태를 색상, 아이콘, 문구로 동시에 표현
- font scale 200%에서도 고정 높이 때문에 잘리지 않게 `minHeight` 사용
- 모든 interactive target 48dp 이상
- dark mode, 작은 휴대폰, tablet, landscape 검증

## 10. Mobile API 계약

alias, photo navigation, face navigation, mutation, image handle을 서로 다른 타입으로 분리한다.

```text
prp_*  photo review navigation
prf_*  face review navigation
pra_*  revision-bound mutation
pim_*  crop/context image
pah_*  confirmed identity target
aal_*  Apple alias hint
```

### 10.1 사진별 queue

```http
GET /mobile-client/v1/people/face-review/photos?state=pending&cursor=...
GET /mobile-client/v1/people/face-review/photos/{photo_review_handle}
GET /mobile-client/v1/people/identity-choices?photo_review_handle=...
GET /mobile-client/v1/people/review-images/{image_handle}
```

상세 응답 예:

```json
{
  "review_revision": 3,
  "capture_date_local": "2026-09-08",
  "context_image_handle": "pim_...",
  "aliases": [
    {"display_label": "이름A", "alias_action_handle": "aal_..."},
    {"display_label": "이름B", "alias_action_handle": "aal_..."}
  ],
  "faces": [
    {
      "face_review_handle": "prf_...",
      "review_action_handle": "pra_...",
      "crop_image_handle": "pim_...",
      "box": {"x": 0.17, "y": 0.22, "w": 0.19, "h": 0.28},
      "state": "pending",
      "position_label": "얼굴 1"
    }
  ]
}
```

응답에 내부 observation ID, identity ID, local asset ID, provider asset ID, 파일 경로, embedding, raw similarity를 넣지 않는다.

### 10.2 사진 단위 저장

```http
POST /mobile-client/v1/people/face-review/photos/{photo_review_handle}/assignments
```

```json
{
  "schema_version": 1,
  "expected_review_revision": 3,
  "assignments": [
    {
      "alias_action_handle": "aal_...",
      "face_action_handle": "pra_...",
      "target": {
        "kind": "existing",
        "identity_action_handle": "pah_..."
      }
    },
    {
      "face_action_handle": "pra_...",
      "target": {
        "kind": "new",
        "display_name": "가족 호칭"
      }
    }
  ],
  "face_decisions": [
    {"face_action_handle": "pra_...", "decision": "not_a_face"},
    {"face_action_handle": "pra_...", "decision": "defer"},
    {"face_action_handle": "pra_...", "decision": "ignore_unknown"}
  ]
}
```

성공 응답:

- 새 review revision
- 사진의 완료·대기 얼굴 수
- 전체 대기 수
- 다음 사진 handle
- 새 people overview revision
- `story_refresh_incomplete`
- 단기 `undo_action_handle`

## 11. Story 다인물 반영

Story 저장 구조는 이미 여러 사람을 지원한다.

```text
사진 A
  face 1 → 인물 A
  face 2 → 인물 B
  face 3 → 인물 C

Story evidence
  사진 A.person_refs = [A, B, C]
```

표시 gate는 유지한다.

```text
active face observation
  → owner-confirmed membership
  → user-confirmed identity
  → user-confirmed name
  → audience별 consent allowed
```

- 한 사진에 같은 identity가 중복 근거로 들어와도 이름은 한 번만 표시한다.
- 얼굴 아님과 나중에는 Story evidence에서 제외한다.
- legacy asset-only association은 face refinement가 끝날 때까지 보존한다.
- face membership을 추가해도 legacy association과의 중복은 `DISTINCT` projection에서 제거한다.
- 이름·membership 변경은 사진 VLM을 다시 호출하지 않고 Story presentation만 갱신한다.

## 12. 보안·개인정보·동기화

- 얼굴 계산과 원본 접근은 계속 Mac mini에서만 수행한다.
- crop·context는 owner session용 metadata-free derivative만 전송한다.
- Linux workstation, 외부 LLM, Story 공개 package에는 얼굴 crop·embedding을 보내지 않는다.
- image handle은 device, observation, artifact revision, kind, TTL에 결속한다.
- action handle은 device, review revision, identity revision, TTL에 결속한다.
- 응답은 `Cache-Control: no-store, private`를 유지한다.
- Android 첫 구현은 review 이미지를 memory cache로만 보관한다.
- disk cache가 필요해지면 24시간 이하 TTL, 결정 직후 purge, 기기 해제 시 전체 삭제를 적용한다.
- `나중에`는 versioned durable 상태와 `resurface_after`를 저장해 Mac·Android queue가 일치하게 한다.
- overview revision은 얼굴 review, alias assignment, identity, membership 변화를 모두 포함한다.

## 13. 단계별 구현 순서

### Phase A — 안전 차단과 schema v4

- 얼굴 여러 개인 사진의 기존 사진 전체 alias 즉시 연결 차단
- 얼굴 분석 전 alias에는 `얼굴 분석 후 연결` 표시
- schema v4 additive migration
- geometry, asset index state, alias-face assignment, receipt, outbox 추가
- 기존 3 확정 identity, 13 alias, 69 observations, 43 candidate, 37 hold 보존 검증

완료 기준: 기존 수량과 audit을 잃지 않고 다인물 사진을 사진 전체 한 명으로 잘못 연결할 수 없다.

### Phase B — 지정 색인·projection·private image API

- alias 사진 우선 지정 인덱싱
- 기존 69 observation geometry와 review crop 보강
- 사진별 review group과 paging
- 얼굴 crop·전체 문맥·highlight context endpoint
- macOS와 Android 공통 read-only queue

완료 기준: alias 대상 사진과 기존 69개 얼굴을 사진별로 열고 얼굴 번호·crop·전체 문맥을 일치시켜 볼 수 있다.

### Phase C — 원자적 얼굴 결정 service

- 기존 인물 연결
- 새 identity와 이름 입력
- 얼굴 아님
- 나중에
- alias-face 선택 연결
- 사진 단위 batch transaction
- persistent idempotency receipt
- Story refresh outbox
- decision-group undo

완료 기준: 한 사진의 여러 얼굴 결정은 모두 적용되거나 전부 rollback되고 서버 재시작 후 재시도에도 중복이 없다.

### Phase D — macOS 얼굴 검토 UI

- 사진 queue, bbox overlay, crop strip, inspector
- 사진별 중간 저장·완료
- identity 검색, 새 이름, 충돌 resolver, undo
- zoom·scroll·selection·이름 draft 상태 보존
- keyboard와 VoiceOver

완료 기준: macOS 앱만으로 얼굴 0·1·다수 사진을 정확히 검토하고 한 사진에 여러 identity를 확정할 수 있다.

### Phase E — Android 얼굴 검토 UI

- 별도 `PeopleReviewActivity`
- Material component와 RecyclerView
- 전체 사진/crop 전환과 얼굴 navigation
- 기존 인물 bottom sheet, 새 이름 입력
- 얼굴 아님, 나중에, 중간 저장, 저장하고 다음
- 회전·백그라운드·stale revision draft 복구
- Snackbar undo, TalkBack, 48dp, adaptive two-pane

완료 기준: Android에서 한 단체 사진의 모든 얼굴을 각각 선택해 서로 다른 기존·새 인물로 연결하고 Mac에 같은 revision이 표시된다.

### Phase F — 실제 데이터 E2E·운영 승격

1. 얼굴 1개 + alias 1개 사진
2. 얼굴 여러 개 + alias 1개 사진
3. 얼굴 여러 개 + alias 여러 개 사진
4. 기존 69개 face review paging
5. 얼굴 11개 사진 Android 검토
6. 한 사진에 기존 인물 2명 + 새 인물 1명 + 얼굴 아님 + 나중에
7. Mac→Android, Android→Mac revision
8. Story의 다인물 이름·사진 수
9. owner/family-share 이름 동의 분리
10. 서버 재시작·handle 만료·stale revision·Story 갱신 실패

## 14. 필수 테스트

### DB·transaction

- 한 사진의 얼굴 3개를 서로 다른 identity 3개에 연결
- 얼굴 3개 중 2개만 확정하면 Story에는 2명만 표시
- 같은 얼굴을 두 확정 identity에 연결하면 전체 batch 거절
- 같은 사진의 여러 얼굴과 여러 identity는 허용
- 다른 사진의 alias·face handle을 섞으면 전체 batch 거절
- inactive observation, 구모델 generation, stale revision 거절
- transaction 중간 예외 주입 시 identity·name·membership·alias·review가 모두 원복
- candidate membership을 확정 identity로 이동하면 기존 review와 빈 candidate 정리
- 얼굴 0개 사진은 명시적 asset-only 명령에서만 연결

### 멱등성·복구

- 같은 idempotency key와 body는 서버 재시작 뒤에도 같은 성공 결과
- 같은 key와 다른 body는 409
- crop 임시 파일 write 후 DB 실패 복구
- observation은 있으나 geometry·review가 없는 중간 상태 복구
- 동일 모델 재색인 시 observation·review·candidate 수 불변
- 새 모델 generation은 기존 사용자 확정을 즉시 폐기하지 않음

### API·보안

- 내부 face, person, asset ID와 경로·embedding 미노출
- 다른 기기, 만료, 변조 handle 거절
- image path traversal 차단
- crop/context `no-store, private`
- 목록 paging 중 revision 변경 처리

### macOS·Android UX

- 얼굴 0·1·2·5·11개 사진
- bbox와 crop 번호·선택 동기화
- EXIF orientation 세로 사진과 확대·축소 뒤 bbox 정렬
- 작은 얼굴, 흐린 얼굴, 누락 crop placeholder
- 이름 입력 중 이동·회전·백그라운드 draft 보존
- 일부 얼굴 중간 저장과 나머지 보류
- stale 409에서 이름과 선택 보존
- Mac/Android 동일 review revision
- Android TalkBack, font scale 200%, dark mode, 작은 휴대폰, tablet

### Story 회귀

- 사용자 확인 전 후보 이름 미노출
- 한 사진의 모든 확정 인물이 중복 없이 표시
- 이름 OFF인 audience에는 title, caption, alt, HTML attribute까지 이름 미노출
- 기존 확정 alias 3건의 Story 표시 보존
- refinement 뒤 legacy association과 face membership의 중복 이름 제거
- Google-only 사진에서 face review 미생성

## 15. 승인된 첫 구현 범위

2026-09-12 사용자 승인에 따라 다음을 하나의 첫 수직 기능으로 구현했다.

```text
Phase A 전체
  + Phase B 전체
  + Phase C 전체
  + Phase D의 단체 사진 얼굴 검토
  + Phase E의 Android 얼굴 선택·기존 인물 연결·새 이름·얼굴 아님·나중에
  + Phase F의 실제 운영 색인·API·배포 E2E
```

대규모 identity 병합·분리, 여러 사진의 대량 batch, 대표 얼굴 고급 편집, 결정 그룹의 사용자 노출 undo는 후속으로 남긴다. 그러나 사용자가 요청한 핵심 흐름인 **한 사진의 여러 얼굴을 crop으로 구분하고 Android에서도 각 얼굴에 기존 이름을 연결하거나 새 이름을 입력해 Story에 모든 사람을 반영하는 기능**은 첫 수직 기능에 포함했다.

## 16. 구현 결과

### 16.1 공통 원장과 application service

- private identity 원장을 schema v4로 additive migration했다. 기존 identity, 이름, consent, alias, face observation, audit row는 삭제하거나 덮어쓰지 않는다.
- 사진별 인덱스 상태, 얼굴 geometry, 고해상도 검토 crop, 강조 문맥, alias-face assignment, 사진별 review revision, 영구 command receipt, Story refresh outbox를 추가했다.
- Mac과 Android가 함께 쓰는 `PeopleWorkspaceService`를 추가했다.
- 한 사진의 여러 얼굴에 서로 다른 기존·신규 인물을 한 transaction으로 적용한다.
- 같은 얼굴을 두 번 결정하거나 같은 사진의 서로 다른 얼굴을 같은 identity로 중복 지정하면 전체 요청을 거절한다.
- 기존 사진 단위 alias API는 활성 얼굴이 2개 이상이면 저장소 계층에서도 `face_selection_required`로 거절한다. 단일 얼굴과 얼굴 미검출 사진의 기존 호환성은 유지한다.
- Story 갱신은 identity transaction 뒤 별도 outbox로 수행한다. 갱신 실패 시 확정한 이름과 얼굴 연결은 보존되고 클라이언트에 `story_refresh_incomplete`로 구분된다.

### 16.2 실제 얼굴 색인

설치 앱과 같은 YuNet·SFace 모델로 현재 추천 사진을 다시 색인했다.

| 항목 | 결과 |
|---|---:|
| 처리 사진 | 40장 |
| 검출 얼굴 | 109개 |
| embedding | 109개 |
| 새 candidate group | 22개 |
| 새 review item | 40개 |
| 분석 실패 | 0장 |
| Google-only 제외 | 172건 |
| geometry 보유 얼굴 | 109개 |
| review crop 보유 얼굴 | 109개 |
| highlight 보유 얼굴 | 109개 |

대기 Apple 이름 후보는 10건·7장이다. 이 중 6장은 얼굴이 검출됐고 4장은 얼굴이 둘 이상인 단체 사진이다. 얼굴을 찾지 못한 1장은 모델이 임의로 사람을 만들지 않으며 명시적 사진 연결 fallback 대상으로 남는다.

### 16.3 macOS 앱

- Apple 이름 후보 사진을 열면 전체 문맥과 사진 안의 얼굴 crop strip을 함께 표시한다.
- 얼굴이 여러 개면 crop 하나를 먼저 선택해야 `선택 얼굴에 연결` 또는 `선택 얼굴 새 인물`을 실행할 수 있다.
- 선택한 alias는 정확한 얼굴 observation에 연결되고 Story 인물 evidence를 갱신한다.
- `인물 찾기`는 최근 추천보다 미처리 Apple alias 사진을 먼저 색인한다.

### 16.4 Android 0.8.1

- 전용 `PeopleReviewActivity`를 추가했다.
- 한 사진의 전체 문맥·강조 표시와 각 얼굴 crop을 함께 보여주고 얼굴별로 `기존 인물`, `새 이름`, `얼굴 아님`, `나중에`를 지정한다.
- Apple 이름 후보는 사진 전체의 정답이 아니라 선택한 얼굴에만 붙이는 힌트로 표시한다.
- 일부 얼굴만 중간 저장하거나 저장 후 다음 사진으로 이동할 수 있다.
- 화면 회전·백그라운드 전환 때 현재 사진, 선택 얼굴, 이름 입력, 각 얼굴 draft를 복구한다.
- 모든 private ID와 파일 경로는 opaque handle로 치환하며 검토 이미지는 owner session을 통과한 JPEG만 제공한다.
- 앱 버전은 `0.8.1`(`versionCode 20`)이다.

### 16.5 검증 결과

- Python 전체 회귀: `1020 passed`.
- 얼굴 원장·mobile API·인덱싱 집중 검증: `55 passed`.
- Android release assemble 및 lint: 성공, lint 오류 0건.
- macOS standalone bundle: 코드 서명, health, runtime import, Apple Photos runtime, vision runtime smoke 모두 통과.
- Tailnet 설치 페이지와 APK: HTTP 200, 게시 파일 hash 일치.
- 인증 없는 owner API: HTTP 401, loopback 직접 접근: HTTP 403.
- 공개 Funnel 8443의 얼굴 검토 API: HTTP 404.
- Android 기기가 작업 시점에 ADB에서 분리되어 실제 기기 설치만 수행하지 못했다. 동일 파일은 Tailnet 설치 페이지에서 바로 설치할 수 있다.

상세 검증 기록은 [얼굴 단위 다인물 검토 구현·운영 검증](../../08-reports/01-validation/48-face-level-multi-person-review-implementation-2026-09-12.md)에 남긴다.

## 17. 후속 범위

다음 항목은 핵심 다인물 연결을 막지 않으며 별도 개선으로 관리한다.

1. 결정 직후 사용자에게 노출하는 durable undo와 서버 재시작 뒤 undo
2. candidate identity 자동 정리와 사용자용 병합·분리 resolver
3. 얼굴 11개 사진의 실제 Android TalkBack·200% 글자·tablet 육안 UAT
4. 모델이 얼굴을 찾지 못한 Apple alias 사진의 명시적 사진 연결 UX
5. 여러 사진을 한 번에 선택하는 대량 검토와 대표 얼굴 고급 편집

## 18. 모르는 인물 영구 무시 보강

2026-09-12 후속 요청에 따라 `얼굴 아님`, `나중에`, `모르는 사람`을 서로 다른 결정으로 분리했다.

| 결정 | 얼굴 관측 | 인물 후보 | Story | 재색인 |
|---|---|---|---|---|
| 기존·새 인물 연결 | 유지 | 확정 membership | 이름 표시 조건 충족 시 포함 | 확정 유지 |
| 모르는 사람 · 무시 | 유지 | 해당 candidate membership 거절 | 제외 | `ignored` 유지, 재후보화 금지 |
| 얼굴 아님 | invalid 처리 | membership 제외 | 제외 | 얼굴 관측 자체 제외 |
| 나중에 | 유지 | 현재 후보 유지 | 미확정 상태로 제외 | 추후 검토 가능 |

`ignore_unknown`은 지나가던 행인처럼 실제 얼굴이지만 사용자가 관리할 사람이 아닌 경우에 사용한다. 하나의 candidate identity에서 이 얼굴을 제외한 뒤 활성 후보 얼굴이 하나도 남지 않으면 빈 익명 identity도 `hidden`으로 전환한다. 얼굴 crop·좌표·관측은 모델 디버깅과 결정 감사에 필요하므로 삭제하지 않는다.

Android에는 `모르는 사람 · 무시`를 이름 등록과 `얼굴 아님` 사이의 독립적인 52dp 버튼으로 추가했다. macOS Apple 후보 화면에도 얼굴 crop을 선택한 뒤 같은 결정을 적용하는 버튼과 확인 설명을 추가했다. 두 앱 모두 같은 원자적 application service와 persistent command receipt를 사용한다.
