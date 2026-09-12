# 운영 인물 인덱싱·재검증·통합 관리 UX 계획

작성일: 2026-09-12

상태: 첫 수직 기능 구현·운영 파일럿 완료 — 사용자 인물 확인 대기

적용 범위: PhotosMcp macOS 앱, Android Companion, Apple Photos·로컬 추천 자산, private identity 원장, Story 인물 표시

관련 문서: [인물 중심 추천·동일인 확인·Story 인명 반영 계획](13-person-centric-curation-and-story-identity-plan-2026-09-09.md), [인물 이름 연계 Story·인물별 사진 탐색 개선 계획](16-person-linked-story-and-people-gallery-plan-2026-09-11.md)

## 1. 결론

현재 인물관리에서 새 인물과 얼굴 이미지가 늘지 않는 것은 사용 방법의 문제가 아니다. **운영 사진 분석이 안정 인물 저장소에 얼굴 관측과 후보 인물을 생성하지 않고, 배포된 macOS 앱에도 실제 얼굴 분석 backend가 포함되지 않은 상태**이기 때문이다.

현재 이름 3명과 표시 동의는 보존돼 있다. 그러나 현재 사진에 연결된 얼굴 관측, 얼굴-인물 membership, 사진-인물 association이 모두 0건이어서 대표 얼굴, 인물별 사진, Story 인명이 나타날 수 없다. Apple Photos에서 수집한 이름 후보 13건과 이전 얼굴 결정 37건도 검토 가능한 사진 UI와 안정적인 연결 경로가 없어 대기 상태로만 남아 있다.

따라서 다음 두 경로를 순서대로 구현한다.

1. **현재 자료로 즉시 사용할 수 있는 인물 검토 경로**
   - Apple 이름 후보 13건을 실제 사진과 함께 보여준다.
   - 기존 인물에 연결하거나 새 인물로 등록하고, 거절·보류할 수 있게 한다.
   - 얼굴 모델을 먼저 완성하지 않아도 exact asset 단위의 사용자 확인 결과를 Story에 반영한다.
2. **앞으로 인물이 계속 축적되는 인물 인덱싱 경로**
   - Apple/local 자산을 Mac에서 얼굴 검출·특징화한다.
   - 안정 face observation과 익명 후보 묶음을 만든다.
   - macOS와 Android의 공통 검토 큐에서 사용자가 동일인과 이름을 확정한다.

인물 인덱싱은 Story·추천 분석과 실행 수명주기를 분리한다. 날짜별 분석 완료 뒤에는 별도 후속 작업으로 자동 예약할 수 있지만, 진행률·실패·재시도는 독립적으로 표시한다. 사용자는 `인물 다시 찾기`로 명시적으로 실행할 수도 있다. 자동 모델 결과만으로 실명이나 동일인을 확정하지 않는다.

## 2. 세 에이전트 독립 검토 종합

이번 계획은 세 에이전트가 파이프라인, macOS UX, Android UX를 독립적으로 읽기 전용 감사한 결과를 통합했다.

| 관점 | 공통 확인 | 설계 결정 |
|---|---|---|
| 인물 파이프라인·저장소 | 일반 실행이 stable observation·candidate identity·membership을 생성하지 않으며 설치 앱의 얼굴 backend도 비어 있다. | `PersonIndexingService`와 명시적인 인덱스 작업 원장을 추가한다. |
| macOS 인물관리 | UI는 최근 작업의 실험용 `measurements-private.json`에 의존하고 Apple 후보는 숫자만 표시한다. | 안정 DB 기반 3열 인물 작업공간과 상태별 빈 화면, 인물 찾기·복구 CTA를 만든다. |
| Android Companion | 현재 화면은 네 API의 텍스트 요약과 동의·기존 인물 연결만 제공하며 이미지, 새 인물, 재검증이 없다. | 기존 하단 4탭은 유지하고 owner 전용 인물 허브·검토 카드·원격 인덱싱 명령을 추가한다. |

세 검토의 공통 결론은 다음과 같다.

- 정상 실행을 반복해도 새 인물이 생성되지 않는 것이 현재 코드의 예상 동작이다.
- 이름이 삭제된 것이 아니라 이름과 현재 사진의 연결이 없는 상태다.
- macOS와 Android는 같은 stable DB를 사용하지만 서로 다른 임시·부분 projection을 보여준다.
- `화면 새로 고침`, `사진 분석`, `인물 찾기`, `인물 재검증`을 서로 다른 기능으로 구분해야 한다.
- 후보 사진을 보지 않고 이름 문자열만 연결하게 해서는 안 된다.
- 복잡한 다중 선택·병합·분리는 macOS에서 먼저 완성하고 Android는 빠른 검수부터 제공하는 것이 안전하다.

## 3. 구현 전 운영 상태 기준선

아래 표는 구현 착수 직전인 2026-09-12 운영 private 저장소와 배포 런타임을 개인정보 값 없이 확인한 기준선이다. 구현 후 결과는 17절에 별도로 기록한다.

| 항목 | 현재 값 | 의미 |
|---|---:|---|
| 사용자 확정 identity | 3명 | 기존 인물과 이름은 보존됨 |
| 활성 face observation | 0건 | 현재 사진에서 얻은 안정 얼굴 관측 없음 |
| membership version | 0건 | 얼굴과 인물을 연결한 결정 없음 |
| 확정 asset-person association | 0건 | Story가 사용할 사진별 인물 근거 없음 |
| Apple provider alias 후보 | 13건 | 사진 단위 이름 힌트는 있으나 검토되지 않음 |
| legacy lineage hold | 37건 | 과거 수동 얼굴 결정의 현재 자산 연결이 미해결 |
| legacy 수동 인물 | 3명 | 기존 JSON 설정도 보존됨 |
| legacy 얼굴 override | 34건 | 과거 이동·분리·병합 결정 보존 |
| legacy 얼굴 제외 | 3건 | 과거 얼굴 아님 결정 보존 |
| 배포 앱 얼굴 backend | 0개 | InsightFace·MediaPipe·face-recognition·OpenCV 모두 미포함 |

현재 readiness는 `확정된 이름은 있지만 현재 사진 연결이 필요함`이다. macOS 앱 로그에도 얼굴 분석 backend가 없다는 경고가 반복된다. 개발 `.venv`에는 MediaPipe, face-recognition, OpenCV가 있지만 설치된 `.app` Python runtime에는 없다.

과거 검증에서는 YuNet + SFace를 이용해 1,573개의 128차원 embedding을 생성한 자료가 있으나, 2026-08-23 이후 일반 실행과 연결되지 않은 shadow 산출물이다.

## 4. 왜 현재 UI로는 인물을 추가할 수 없는가

### 4.1 macOS 인물 화면은 운영 인물 인덱스가 아니라 실험 산출물을 읽는다

macOS `인물 관리` 화면은 최근 완료 작업별로 다음 파일을 찾는다.

```text
~/.photos-mcp/validation/person-aware-scene-shadow/<job-id>/measurements-private.json
```

이 파일이 존재하고 crop과 embedding이 있어야 얼굴 묶음과 썸네일을 만든다. 그러나 이 파일은 별도 shadow 분석 스크립트가 만들며 새벽 자동 실행, Android 날짜 Story, 일반 수동 분석에서는 생성하지 않는다.

### 4.2 안정 인물 저장소의 쓰기 기능이 운영 파이프라인에 연결되지 않았다

stable SQLite에는 identity 생성, face observation 등록, membership 결정, asset association 생성 기능이 이미 있다. 하지만 일반 분석에서 이 기능들을 호출하는 production service가 없다. 그 결과 새 얼굴 관측, 익명 인물 후보, 기존 인물 일치 후보, 검토 항목, 대표 얼굴이 생성되지 않는다.

### 4.3 Apple 이름 후보는 인물 후보 화면으로 완성되지 않았다

추천으로 materialize된 Apple 사진의 `persons`·`known_persons` 문자열은 private alias 후보로 저장돼 현재 13건이 존재한다. 그러나 macOS는 건수만 보여주고, Android는 후보 이름과 기존 인물 버튼만 보여준다. 사진 확인, 새 인물 등록, 거절, 보류, 연결 해제가 없다. `_UNKNOWN_` 같은 무의미한 sentinel도 후보가 될 수 있다.

### 4.4 레거시 결정과 stable 원장이 이중화돼 있다

macOS의 일부 이름·동의는 stable SQLite에 기록되지만 얼굴 병합·분리·제외는 legacy `people-private.json`을 수정한다. 장기적으로 legacy JSON은 읽기 전용 migration·복구 입력으로만 유지하고 모든 신규 결정은 stable application service를 통해 기록해야 한다.

### 4.5 Mac과 Android의 동기화 문제는 DB보다 projection 문제다

두 앱은 동일한 `~/.photos-mcp/people/person-identities-private.sqlite3`를 읽는다. 다만 Mac은 shadow 얼굴 catalog와 stable identity를 병합하고 Android는 stable identity·alias를 별도 API 네 번으로 읽는다. 하나의 revision을 갖는 `people overview` projection으로 통합해야 한다.

## 5. 목표 인물 수명주기

```text
Apple Photos / 로컬 관리 자산
        ↓
PersonIndexingService — Mac 로컬 처리
        ├─ 얼굴 검출
        ├─ 얼굴 정렬·crop
        ├─ embedding 생성
        ├─ stable observation 등록
        └─ 후보 identity·membership 생성
        ↓
공통 owner review queue
        ├─ 기존 인물과 같음
        ├─ 새 인물로 등록
        ├─ 다른 사람·분리
        ├─ 얼굴 아님
        └─ 나중에
        ↓
owner-confirmed identity / membership / asset association
        ↓
인물별 사진·추천·Story presentation 갱신
```

| 사용자 상태 | 저장 의미 | Story 이름 사용 |
|---|---|---|
| 확인 전 | candidate identity 또는 candidate membership | 사용 안 함 |
| 확인 필요 | alias, 모호한 lineage, 낮은 결속도 후보 | 사용 안 함 |
| 확정 | user-confirmed identity/name + owner-confirmed 연결 | 동의가 있으면 사용 |
| 충돌 | 이름·연결·사용자 결정 충돌 | 해결 전 사용 안 함 |
| 사진 연결 없음 | 확정 이름은 있으나 활성 사진 0장 | 이름은 보존, 사진 표시 안 함 |
| 제외됨 | 얼굴 아님 또는 관리 제외 | 사용 안 함 |
| 재색인 필요 | 모델 세대 변경 또는 오래된 관측 | 기존 이름·동의 보존 |

## 6. 인덱싱 정책

### 6.1 별도 작업으로 만들되 분석 완료 후 선택적으로 자동 연결한다

- 수동: `인물 다시 찾기`에서 날짜, 출처, 최대 사진 수, 모드를 선택한다.
- 자동: Apple/local 추천 materialization 성공 뒤 새 자산이 있으면 별도 후속 job을 예약한다.
- 자동 job 실패가 추천·Story 성공을 실패로 바꾸지 않는다.
- Mac과 Android에 독립 진행률, 실패 이유, 재시도, 중지·재개를 제공한다.
- 일반 분석 결과에는 `인물 후보 N건이 준비됨`만 안내하고 이름을 자동 확정하지 않는다.

지원 범위:

- 현재 추천 사진
- 특정 Story의 사진
- 선택한 날짜 범위 또는 최근 30일
- 아직 인덱싱하지 않은 사진만
- 연결 안 됐거나 충돌한 사진만
- 선택 인물의 비슷한 얼굴 다시 찾기
- 모델 변경 뒤 재검증 대상만

첫 운영 승격은 현재 추천 20~50장으로 제한한 뒤 100장, 250장, 최대 1,000장과 6시간 실행으로 확대한다. checkpoint/resume와 idempotency를 기본 계약으로 둔다.

### 6.2 출처 정책

- Apple Photos와 Mac이 관리하는 로컬 자산: 얼굴 후보 생성 허용
- Google Photos Picker 자산: 기존 정책대로 얼굴 군집·신원 추론 대상에서 제외
- Apple 이름 alias: 사용자 확인 전에는 사진 단위 후보일 뿐 identity 근거로 확정하지 않음
- 이름 문자열이 같다는 이유로 서로 다른 사진이나 인물을 자동 병합하지 않음
- `unknown`, `_UNKNOWN_`, `unnamed person`류 sentinel은 후보 생성 전에 제거

### 6.3 얼굴 backend 권장안

첫 운영 backend는 과거 이 코드베이스에서 실제 embedding을 생성한 **YuNet + SFace**를 후보 생성 전용으로 제품화한다.

- 현재 모델 캐시와 분석 코드가 이미 존재한다.
- 현재 MediaPipe 경로는 얼굴 box만 반환해 동일인 embedding 용도로 충분하지 않다.
- InsightFace를 새로 도입하기 전 기존 128차원 SFace 자료와 일관된 model fingerprint를 유지할 수 있다.
- 얼굴 발견·익명 후보 군집·기존 인물 제안은 자동화하되 동일인·이름 최종 확정은 사용자만 한다.
- 서로 다른 model family의 embedding은 직접 비교하지 않는다.

배포 방식은 `PhotosMcp.app`에 모델과 native dependency를 포함한 별도 face worker/helper를 번들하고 앱이 관리하는 로컬 IPC로 실행하는 방식을 권장한다. 개발 `.venv`를 운영 필수조건으로 삼지 않는다. helper 시작 시 backend, 모델 파일, fingerprint, embedding dimension, 테스트 이미지의 검출·embedding까지 preflight하고 실패 원인을 UI에 표시한다.

## 7. 저장소와 application service

### 7.1 단일 쓰기 경계

`PeopleWorkspaceService`를 macOS와 mobile API가 함께 사용하는 application façade로 둔다. UI가 SQLite나 legacy JSON을 직접 수정하지 않는다.

주요 조회:

```text
people_dashboard()
list_people(filter, cursor)
person_detail(identity_handle)
list_review_items(kind, cursor)
review_item_detail(review_handle)
index_run_status(run_handle)
```

주요 명령:

```text
start_person_index(scope, source, date_range, max_assets, mode)
pause_person_index(run_id)
resume_person_index(run_id)
cancel_person_index(run_id)
confirm_candidate(candidate_id, identity_id)
create_identity_from_candidate(candidate_id, name)
reject_or_defer_candidate(candidate_id, reason)
mark_invalid_face(observation_id)
split_or_move_observations(...)
merge_identities(...)
select_representative_face(...)
rename_identity(...)
undo(...)
```

모든 mutation은 expected revision, idempotency key, owner audit, 사용자 결정 우선, Story refresh outbox를 사용한다.

### 7.2 schema 추가안

기존 identity, observation, membership, alias, association 테이블은 보존하고 다음 운영 원장을 추가한다.

```text
face_index_runs
  index_run_id / scope_kind / scope_fingerprint
  model_family / model_version / model_fingerprint
  asset_count / detected_face_count / embedding_count
  candidate_count / review_count / failure_count
  status / checkpoint / started_at / completed_at / error_code

person_review_item_versions
  review_item_id / review_kind
  candidate_person_identity_id / face_observation_id
  suggested_person_identity_id / review_state
  policy_version / revision / created_at / resolved_at

representative_face_versions
  person_identity_id / face_observation_id / state / revision / created_at

face_artifact_refs
  face_observation_id / crop_ref / context_preview_ref / artifact_revision / state
```

얼굴 crop과 embedding은 DB blob이나 일반 로그에 넣지 않고 `~/.photos-mcp/people/{observations,crops,previews,embeddings}` 아래 owner-only 파일로 저장한다. 디렉터리 `0700`, 파일 `0600`을 적용하고 DB와 API에는 opaque ref만 사용한다.

### 7.3 재색인과 기존 정보 보존

- 새 모델 세대를 먼저 완성한 뒤에만 활성 세대를 전환한다.
- 전체 재색인은 기존 identity, 이름, consent, 사용자 확인, audit을 삭제하지 않는다.
- 다른 모델의 embedding은 직접 비교하지 않는다.
- 기존 observation은 즉시 삭제하지 않고 stale/inactive 세대로 전환한다.
- 과거 37개 hold는 dry-run에서 deterministic unique인 것만 복구한다.
- 근거가 부족한 hold는 대표 사진 재선택 또는 owner-assisted relink로 보낸다.

## 8. macOS 인물관리 UX

macOS 앱을 전체 인물관리의 기준 화면으로 둔다. AppKit native component와 상태 보존을 사용하고 현재처럼 선택 하나마다 전체 subview를 제거·재생성하는 구조는 점진적으로 교체한다.

```text
┌────────────────────────────────────────────────────────────────────────────┐
│ 인물 관리   확정 3 · 연결 사진 0 · 확인 13 · 이전 연결 37   [인물 찾기 ▾]│
│ [확인 필요] [인물] [연결 안 됨] [충돌] [제외됨] [인덱스 상태]           │
├──────────────┬──────────────────────────────────┬──────────────────────────┤
│ 인물/후보     │ 얼굴·사진 표본                  │ 선택 항목 Inspector       │
│ Apple 후보 13│ [대표] [다른 날짜] [문맥 사진]  │ 기존 인물에 연결          │
│ 이전 연결 37 │ [최근] [측면] [단체]             │ 새 인물로 등록            │
│ 확정 인물 3  │ 원본 사진 보기                   │ 분리/얼굴 아님/나중에     │
└──────────────┴──────────────────────────────────┴──────────────────────────┘
```

권장 AppKit 구조:

- `NSSplitViewController`: 목록, 썸네일 작업영역, Inspector
- `NSTableView` 또는 `NSOutlineView`: 인물·후보·상태 목록
- `NSCollectionView`: 얼굴 crop·문맥 사진·인물별 사진
- `NSSegmentedControl`: 상태 필터
- `NSProgressIndicator`: 인물 찾기·재검증 진행
- sheet/popover: 이름 지정, 병합, 파괴적 동작 확인
- 항목 단위 갱신: 포커스·스크롤·편집 내용 보존

상단 액션:

- `화면 새로 고침`: 현재 저장소만 다시 읽음
- `인물 찾기`: 아직 인덱싱하지 않은 Apple/local 사진 처리
- `날짜를 선택해 찾기`: 기간·출처·최대 장수 지정
- `선택 인물 재검증`: 해당 인물과 연결된 후보만 검사
- `연결 안 된 사진 재검증`: 미연결·모호·충돌만 처리
- `전체 재색인`: 모델 변경 때만 사용, 기존 이름·확정은 보존

현재 상태에는 이니셜 avatar와 `사진 연결 없음` 배지를 표시하고 다음 CTA를 제공한다.

```text
저장된 인물 이름은 3명입니다.
현재 사진과 연결된 얼굴이 없어 대표 사진을 표시할 수 없습니다.

Apple Photos 이름 후보 13건
이전 사진 연결 복구 필요 37건

[Apple 이름 후보 확인] [최근 사진에서 인물 찾기]
[날짜를 선택해 찾기]   [이전 연결 복구]
```

backend 미설치, 아직 스캔 안 함, 대상에 얼굴 없음, 모두 검토함, 저장소 오류를 각각 다른 상태로 표시한다.

검토 큐는 서로 다른 날짜·각도·조명의 표본 3~8장과 문맥 사진을 보여주고 다음을 제공한다.

- 이 인물이 맞아요
- 기존 인물에 연결
- 새 인물로 등록
- 서로 다른 사람으로 분리
- 이 사진만 다른 사람
- 얼굴 아님
- 나중에
- 원본 사진 보기
- 실행 취소

유사도를 `97% 정확`처럼 표시하지 않는다. 기본 UI에는 `확실한 후보`, `확인 필요`, `충돌`만 보여주며 raw score는 접힌 진단 영역에 둔다.

## 9. Android Companion UX

Android는 얼굴을 계산하거나 DB를 직접 변경하는 앱이 아니라 Mac의 owner 검토·원격 실행 클라이언트로 유지한다. Android Material 3 원칙을 적용하되 현재 Java/View 앱을 한 번에 Compose로 재작성하지 않는다.

기존 하단 `홈 / 실행 / 결과 / 스토리` 네 탭은 유지한다.

- 홈: `인물 확인이 필요해요` 카드, 대표 썸네일 최대 3개, 대기 건수, 진행률
- 실행: 날짜 범위 아래 `인물도 함께 찾기` 옵션과 독립 작업 상태
- 결과: `전체 / 인물` 보기 전환과 인물 카드
- Story: 확정 인물 chip과 사진 필터
- 설정: `인물 관리` 진입, 이름 표시·보존·삭제 정책

인물 허브는 `[인물] [확인 대기] [재검증]` 세 segment로 구성한다.

- 인물: 대표 얼굴, 이름, 연결 사진 수, Story 수
- 확인 대기: alias·새 얼굴·기존 일치·충돌·legacy 복구 후보
- 재검증: 범위, 진행 단계, 모델 상태, 오류·재시도

후보 카드:

```text
[얼굴 crop] [전체 문맥 사진]
Apple Photos · 촬영일
사진에서 찾은 이름 또는 새 얼굴 묶음 · 사진 N장

[기존 인물에 연결] [새 인물로 등록]
[다른 사람] [얼굴 아님] [나중에]
```

첫 구현은 후보 확인·거절·보류, 기존 인물 연결, 새 인물 이름 입력, 이름·표시 동의 수정, 인물 찾기 실행·진행상태·중지·재개, undo까지 제공한다. 병합·분리는 macOS를 먼저 완성한 뒤 비교 bottom sheet와 grid 다중 선택 방식으로 추가한다.

현재 큰 단일 `MainActivity.java`는 다음 순서로 점진 개선한다.

1. typed People DTO와 `PeopleRepository`
2. loading/cached/fresh/mutating/error 상태 모델
3. People 전용 Activity 또는 Fragment
4. RecyclerView/Grid, Material filter chip, bottom sheet
5. `GET /people/overview` 한 번으로 동일 revision 로드
6. `onResume`, pull-to-refresh, mutation 뒤 자동 재조회

Compose 전환은 인물 기능의 선행조건으로 삼지 않는다.

## 10. owner API 계약

조회:

```text
GET /mobile-client/v1/people/overview
GET /mobile-client/v1/people/identities?cursor=
GET /mobile-client/v1/people/{gallery_handle}/photos?cursor=
GET /mobile-client/v1/people/review-queue?kind=alias|face|match|conflict|legacy&cursor=
GET /mobile-client/v1/people/review-items/{review_handle}
GET /mobile-client/v1/people/review-assets/{asset_handle}/thumb
GET /mobile-client/v1/people/review-assets/{asset_handle}/preview
GET /mobile-client/v1/people/index-jobs/{job_handle}
```

변경:

```text
POST /mobile-client/v1/people/index-jobs
POST /mobile-client/v1/people/index-jobs/{job_handle}/pause
POST /mobile-client/v1/people/index-jobs/{job_handle}/resume
POST /mobile-client/v1/people/index-jobs/{job_handle}/cancel
POST /mobile-client/v1/people/reviews
POST /mobile-client/v1/people/identities/create
POST /mobile-client/v1/people/identities/rename
POST /mobile-client/v1/people/identities/merge
POST /mobile-client/v1/people/identities/split
POST /mobile-client/v1/people/representative
POST /mobile-client/v1/people/undo
POST /mobile-client/v1/people/consent
```

검토 command:

```text
connect_existing
create_identity
same_person
different_person
move_to_identity
split_to_new_identity
unlink_name
not_a_face
defer
reject_alias
```

`overview`는 readiness, review summary, people, alias/legacy, backend, 마지막 index job, 공통 revision을 한 응답에 제공한다.

인증·이미지 정책:

- 기존 device signature, nonce, idempotency, optimistic revision 재사용
- identity, gallery, review, image, index-job handle 타입 분리
- handle을 device/session/revision에 묶고 짧은 TTL과 revoke 제공
- 검토 이미지는 512~768px metadata 제거 derivative만 제공
- 원본 경로, provider ID, 내부 identity ID, embedding, raw similarity 미노출
- `Cache-Control: private, no-store`
- Android review cache에 TTL과 기기 해제 시 삭제 적용
- 오프라인에는 마지막 동기화 정보만 표시하고 mutation은 최신 revision 확인 뒤 수행

## 11. Story와 추천 연결 규칙

Story 이름은 다음 gate를 모두 통과할 때만 표시한다.

```text
Story의 local_asset_id
  → active face observation 또는 owner-confirmed exact asset association
  → 최신 owner-confirmed membership
  → user-confirmed identity
  → user-confirmed name
  → audience별 Story consent allowed
```

- candidate identity는 인물관리에는 표시하지만 Story 실명에 사용하지 않는다.
- identity 변경 뒤 사진 VLM을 다시 실행하지 않고 Story presentation revision만 갱신한다.
- owner와 family-share projection은 별도로 렌더링한다.
- 이름 표시 OFF 공유에는 title, chapter, caption, alt, HTML attribute까지 이름이 없어야 한다.
- 특정 인물 추천은 owner-confirmed association이 충분할 때만 활성화한다.
- 인물 카드 장수, Story 필터 결과, viewer indicator는 같은 projection에서 계산한다.

## 12. 삭제·초기화·재분석 정책

| 동작 | 삭제/교체 | 반드시 보존 |
|---|---|---|
| 작업 목록 비우기 | run 표시·임시 결과 | identity, 이름, 동의, observation, 사용자 결정 |
| Story 삭제 | Story·공유 파생물 | 인물 원장과 사진 연결 |
| 날짜 재분석 | 새 recommendation/Story generation | 인물 원장과 owner-confirmed 결정 |
| 인물 다시 찾기 | 새 face index generation | 확정 이름·동의·사용자 결정 |
| 얼굴 인덱스 초기화 | 자동 후보·모델 산출물 | identity/name/consent/audit |
| 인물 개인정보 완전 삭제 | 관련 identity와 파생물 | 강한 별도 확인과 영향 미리보기 필요 |

재분석 시 동일 content identity와 lineage가 증명되면 기존 association을 재사용한다. 그렇지 않으면 검토 후보로 보내며 이름을 강제로 계승하지 않는다.

## 13. 단계별 구현 계획

### Phase 0 — 현재 상태와 blocker 표시 (첫 수직 기능 완료)

- macOS와 Android에 동일 revision의 `people overview` 제공
- 현재 `3 / 0 / 0 / 13 / 37` 상태를 사용자 문장으로 표시
- backend 미설치, 미실행, 얼굴 0, 저장소 오류, 검토 완료 구분
- `새로 고침`과 `인물 찾기` 분리
- 현재 3명에 placeholder와 `사진 연결 없음` 배지 제공

완료 기준: Mac과 Android 수량·revision이 일치하며 backend 실패가 로그에만 남지 않는다.

### Phase 1 — Apple 후보 13건 즉시 검수 (기능 완료, 사용자 판단 대기)

- sentinel 후보 제거
- alias 후보에 전체 사진 thumbnail과 출처·촬영일 연결
- Mac·Android 동일 queue
- 기존 인물 연결, 새 인물 생성, 거절, 보류
- exact asset association과 Story presentation refresh

완료 기준: 후보 사진 확인 없이는 연결할 수 없고, 사용자 확인 전에는 Story에 이름이 나오지 않는다.

### Phase 2 — 배포 face runtime과 인덱싱 기반 (번들·20장 파일럿 완료)

- YuNet + SFace worker/helper 번들
- backend·모델·fingerprint·dimension·샘플 preflight
- index run·checkpoint/resume
- stable observation과 candidate identity/membership
- owner-only crop·문맥 preview
- 현재 추천 20~50장 read-only shadow

완료 기준: 설치된 `.app`만으로 얼굴과 embedding을 만들고, 중복 실행이 중복 observation을 만들지 않는다.

### Phase 3 — macOS 통합 인물 작업공간

- stable DB 기반 3열 작업공간과 상태 필터
- 인물 찾기·날짜 찾기·선택 인물 재검증·전체 재색인
- 후보 crop·문맥·다른 날짜 표본
- 이름, 연결, 새 인물, merge/split/move, 얼굴 아님, 대표 얼굴, undo
- legacy JSON 신규 쓰기 중단
- 37개 hold dry-run·복구 화면

완료 기준: macOS 제품 UI만으로 새 인물과 대표 얼굴을 만들고 기존 이름·결정을 보존한다.

### Phase 4 — Android 빠른 검수와 원격 인덱싱

- Home 대기 카드와 People 허브
- 인증된 crop·문맥 thumbnail
- 후보 확인, 기존 연결, 새 인물, 거절, 보류, undo
- 범위별 인물 찾기 명령
- 진행률, 오류, 중지·재개, foreground 동기화

완료 기준: Android 변경이 Mac에 같은 revision으로 보이며 앱 종료 뒤에도 Mac 작업이 지속된다.

### Phase 5 — 인물 갤러리·Story·특정 인물 추천

- 인물별 사진·Story
- Story 인물 chip과 사진 필터
- owner-confirmed association만 이름과 특정 인물 추천에 사용
- 이름·병합·분리·동의 변경 시 presentation refresh
- owner/family-share 분리 검증

완료 기준: 인물 사진 수·필터 grid·viewer indicator가 일치하고 공유 이름 OFF 누출이 0건이다.

### Phase 6 — 점진 운영 승격

1. Apple 후보 13건 중 소수 검수
2. 현재 추천 20~50장 인덱싱
3. 확정 인물 1명과 새 인물 후보 1명 E2E
4. 잘못된 얼굴 1건 제외·undo
5. Android→Mac, Mac→Android revision 동기화
6. Story 인명과 공유 OFF/ON
7. 재분석 뒤 이름·대표 얼굴 보존
8. 100장, 250장, 최대 1,000장·6시간·중단 재개

## 14. 필수 테스트

### 파이프라인·저장소

- 설치 앱 preflight와 정상 embedding 생성
- 같은 asset·bbox·model fingerprint의 observation idempotency
- 같은 사진의 서로 다른 얼굴 자동 병합 방지
- 모델 변경 때 기존 사용자 확인을 보존한 새 세대 생성
- Google Picker provenance 차단
- 1,000장 checkpoint 재개와 손상 복구
- migration rollback·idempotency·파일 권한
- legacy 3명·34 override·3 exclude 및 stable 3명·consent·13 alias·37 hold 보존

### macOS

- 완전 빈 상태, 이름만 있는 현재 상태, 후보, 충돌, backend 미설치, DB 오류
- 진행·중지·재개·실패·재시도
- thumbnail 누락 placeholder
- 이름 편집·선택·스크롤·resize 상태 보존
- keyboard, VoiceOver, high contrast, 작은 창
- merge/split/move/representative/undo

### Android

- handle 없음·만료·다른 기기 접근 거부
- 원본 경로·embedding·provider ID 미노출
- 새 인물, 기존 연결, 거절, 보류, undo
- stale revision에서 입력·선택 보존
- 회전·백그라운드·foreground 복귀
- Mac offline, Tailscale 단절, 모델 실패, 대상 없음, 서버 재시작 안내

### Story·공유

- candidate 이름 미노출
- identity/name/membership/association/consent gate별 누락 이유
- 이름·병합·분리·동의 변경 후 새 revision
- owner/family 동의 독립 적용
- 이름 OFF 공유의 텍스트·alt·HTML attribute 누출 0건

## 15. 의도적으로 하지 않는 것

- 얼굴 유사도만으로 동일인·실명 자동 확정
- 이름 문자열 일치만으로 인물 자동 병합
- Google Photos 얼굴 군집 정책 우회
- 얼굴·나이·공동 등장으로 가족관계 추론
- Linux 워크스테이션이나 외부 LLM으로 얼굴 crop·embedding 전송
- 개발 `.venv`에 의존하는 설치 앱 배포
- 전체 재색인 전에 기존 이름·동의·사용자 확인 삭제
- 과거 37개 hold의 무근거 강제 연결
- Android 전체 Compose 재작성을 인물 기능의 선행조건으로 설정

## 16. 승인된 첫 구현 범위

다음 범위를 첫 수직 기능으로 승인받아 구현했다.

```text
Phase 0 전체
  + Phase 1 Apple 후보 사진 검수
  + Phase 2의 배포 face runtime 기반과 20~50장 shadow
```

첫 결과물 수용 기준:

1. macOS와 Android에서 현재 이름 3명, 연결 0건, 후보 13건, 이전 연결 37건을 같은 의미로 확인한다.
2. Apple 후보를 실제 사진으로 확인해 기존 인물에 연결하거나 새 인물로 등록한다.
3. 설치된 PhotosMcp 앱만으로 소수 Apple/local 사진에서 얼굴 후보와 대표 crop을 생성한다.
4. 사용자 확인 전에는 Story 인명에 반영하지 않는다.
5. 기존 인물 이름·동의·수동 결정은 모두 보존한다.
6. 통합 테스트 뒤 macOS 전체 편집, Android 고급 편집, 최대 1,000장으로 확대한다.

이 범위가 비어 있던 인물관리 화면을 실제 기능으로 바꾸면서 향후 자동 누적과 Story 인명 연결의 기반을 만든다.

## 17. 2026-09-12 구현·배포·운영 검증 결과

### 17.1 완료한 기능

- stable private 원장을 schema v3으로 올리고 인물 인덱스 실행, 얼굴 검토 항목, 대표 얼굴 세대, private artifact 참조를 추가했다.
- Apple Photos 이름 후보를 촬영일과 실제 preview로 확인한 뒤 기존 인물 연결, 새 인물 등록, 후보 제외, 나중에 보기로 처리할 수 있게 했다.
- macOS 인물관리의 `새로 고침`, `후보 사진 확인`, `인물 찾기`를 분리하고 얼굴 backend 준비 여부와 최근 인덱스 결과를 실제 저장소 기준으로 표시한다.
- Android 0.7.4 인물 화면을 단일 `people overview` revision으로 통합하고 연결 사진 수, Apple 후보 preview·촬영일, 기존 인물 연결, 새 인물 등록, 후보 제외를 제공한다. foreground 복귀 때 자동 갱신한다.
- mobile API는 owner session, device signature, nonce, idempotency key, opaque handle을 유지한다. 원본 파일 경로, provider asset id, embedding, 얼굴 artifact ref는 응답에 노출하지 않는다.
- 인물 결정 저장 뒤 Story presentation 갱신만 실패한 경우 인물 결정을 실패로 되돌리지 않는다. 응답의 `story_refresh_incomplete`로 후속 갱신 필요 여부를 분리했다.
- 추천 자산의 sentinel 인물 이름을 제거하고 Google-only Picker provenance는 얼굴 군집 입력에서 제외한다.
- YuNet 2023mar detector와 SFace 2021dec recognizer, OpenCV native runtime을 `PhotosMcp.app`에 포함했다. 빌드 산출물과 설치본 모두 모델 fingerprint와 실제 import를 검사해야 배포가 완료된다.
- 얼굴 crop·문맥 preview·128차원 embedding은 `~/.photos-mcp/people/index-private` 아래에만 두고 디렉터리 `0700`, 파일 `0600`을 검증했다.

### 17.2 실제 20장 운영 파일럿

| 항목 | 최초 실행 | 같은 범위 재실행 |
|---|---:|---:|
| 입력 자산 | 20장 | 20장 |
| 검출 얼굴·embedding | 69개 | 69개 |
| 신규 익명 후보 그룹 | 43개 | 0개 |
| 신규 검토 항목 | 69개 | 0개 |
| 자산 처리 실패 | 0장 | 0장 |

최초 실행 뒤 stable 원장은 기존 사용자 identity 3명을 그대로 보존한 채 익명 candidate identity 43명, 활성 face observation 69건, candidate membership 69건, 검토 항목 69건을 추가했다. 동일 범위를 다시 실행했을 때 identity·observation·membership 수가 변하지 않아 observation과 검토 생성의 idempotency를 확인했다. 익명 후보는 사용자 확인 전에는 Story 인명에 사용하지 않는다.

현재 사용자 판단을 기다리는 항목은 Apple 이름 후보 13건, 얼굴 검토 69건(익명 후보 그룹 43개), legacy 사진 연결 hold 37건이다. 이 결정들은 자동 처리하지 않았다.

### 17.3 배포 및 회귀 결과

| 검증 | 결과 |
|---|---|
| Python 전체 회귀 | `1,015 passed` |
| Android release | compile, R8, lintRelease, zipalign, APK signature v2/v3 통과 |
| Android 버전 | 0.7.4, 117,009 bytes |
| APK SHA-256 | `a5e35ff780383357708e3fc84b0ba0a788aec0dd7b3bfde04ebd0eabd7644e3e` |
| macOS 설치 앱 | health, runtime import, photo-source, scene runtime, person runtime smoke 통과 |
| macOS 코드 서명 | deep/strict 검증 통과, bundle id `com.nanobot.photos-mcp` |
| 얼굴 모델 fingerprint | `4b54cc7ef9477ab64415a9806e7d46764345f791cff694967a849c3341572d00` |
| mobile service | 0.7.4 capabilities 정상, owner header 없음은 HTTP 403 |

### 17.4 이번 단계에서 의도적으로 남긴 범위

- 실명과 동일인 확정, Apple 별칭 연결·제외, 익명 후보 선택은 반드시 사용자가 사진을 보고 결정한다.
- Android에 연결된 실제 기기가 없어 이번 배포본은 설치하지 않고 Tailnet 다운로드 위치에 게시했다.
- 20~50장 파일럿 범위에서는 앱 내부 background thread로 처리한다. 최대 1,000장 운영 승격 전에는 face helper process 분리, 자산 단위 checkpoint/resume, 중지·재개 API를 Phase 3·4에서 완성한다.
- 익명 face candidate의 crop 중심 병합·분리·대표 얼굴 선택 화면, legacy hold 복구, Android 원격 인덱싱 명령은 Phase 3·4의 다음 수직 기능이다.
