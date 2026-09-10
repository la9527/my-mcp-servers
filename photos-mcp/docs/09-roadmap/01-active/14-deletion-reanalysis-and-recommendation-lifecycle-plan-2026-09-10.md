# 삭제·재분석·추천 앨범 수명주기 재설계

- 작성일: 2026-09-10
- 대상: PhotosMcp macOS 앱, Android 앱, Apple Photos, Google Photos, 로컬 추천 보관소, Story·공유, 인물·위치 데이터
- 검토 방식: 저장소·DB 감사, 추천 게시 경로 감사, UX·복구 정책 감사의 독립 에이전트 3개 결과와 운영 DB read-only 실측을 통합
- 상태: Phase 0 안전장치 구현·운영 데이터 교정·회귀 검증 완료, generation/current-head 전환은 후속 단계

## 1. 결정 요약

PhotosMcp에서 `작업 목록 비우기`, `Story 삭제`, `기간 전체 재분석`, `추천 앨범 반영`은 서로 다른 작업이어야 한다. 하나의 “전체 삭제” 동작으로 합치지 않는다.

채택 정책은 다음과 같다.

1. 작업 목록은 표시 이력과 작업 임시 파일만 정리한다.
2. 인물 이름·동의·감사 기록, Android GPS 원장, 원본, 로컬 추천 파일, Story, 외부 앨범 영수증은 작업 목록 정리와 무관하게 보존한다.
3. 기간 전체 재분석은 기존 결과를 먼저 삭제하지 않고 새 generation을 side-by-side로 만든다.
4. 새 수동 Story는 이번 실행에서 생성된 recommendation collection만 사용한다. 같은 날짜의 과거 추천을 섞지 않는다.
5. 수동 재분석은 기본적으로 로컬 추천 결과와 run-scoped Story만 만든다. 사용자가 승인하기 전에는 기존 월별 Apple/Google 추천 앨범을 바꾸지 않는다.
6. 로컬 추천 파일을 제거해도 Apple/Google 외부 게시 영수증은 삭제하지 않는다. 영수증은 원격 사본 소유권, 중복 방지, Google managed-output 제외의 근거다.
7. “앨범 교체”라는 모호한 기능명은 사용하지 않는다. `기존 앨범에 새 추천만 추가`와 `새 버전 앨범으로 게시`를 구분한다.
8. 정확한 교체는 장기적으로 active generation의 desired set과 실제 앨범 membership의 diff로 구현한다. 원본 또는 provider library asset 자체는 자동 삭제하지 않는다.

## 2. 운영 데이터 실측

2026-09-10 KST에 운영 DB와 보관소를 read-only로 확인했다. 이 감사 과정에서는 사진, DB row, Story, 앨범을 삭제하거나 변경하지 않았다.

| 항목 | 현재 수량 | 의미 |
|---|---:|---|
| 사진별 처리 원장 | 408 | Apple 103, Google 305, 모두 completed |
| 추천 collection | 14 | 분석 run별 추천 결과 generation에 가까운 단위 |
| 추천 member | 126 | collection별 추천 provenance |
| 로컬 추천 자산 | 96 | content hash로 중복 통합된 물리 파일 기준 |
| 월별 추천 group | 1 | 2026-09 월 그룹 |
| 월별 group member | 96 | 현재는 최신 집합이 아니라 누적 합집합 |
| destination receipt | 190 | local_store 96, Apple album 94 |
| Story manifest | 7 | ready 1, soft-deleted 6 |
| 공유 package | 4 | Story와 별도 수명주기 |
| 로컬 추천 보관소 | 약 227MB | 이미지와 날짜별 manifest 포함 |

인물 저장소는 위 작업 DB와 분리되어 있다.

- `~/.photos-mcp/people/person-identities-private.sqlite3`
- `~/.photos-mcp/people/people-private.json`
- `~/.photos-mcp/people/crops/`

확인 결과 확정 인물 3명, 이름 상태 `user_confirmed` 3명, owner·family_share 동의 각 3건, 레거시 얼굴 수동 매핑 34건, 제외 얼굴 3건, 감사 결정 9건이 남아 있다. 인물 설정은 작업 목록 정리로 사라지지 않았다.

새 lineage 테이블의 face observation과 confirmed membership projection은 현재 0건이다. 파일 수정 시각도 최근 작업 목록 정리 이전이므로 이번 삭제로 0건이 된 것이 아니다. 수동으로 설정한 이름·동의와 레거시 매핑은 보존되어 있다.

## 3. 현재 구현의 실제 동작

### 3.1 작업 목록 비우기

현재 macOS 정리 기능은 terminal 작업, 교차 클라이언트 작업 표시, 알림, 작업용 결과·미리보기, 관리형 Google 임시 다운로드를 정리한다.

다음은 유지된다.

- `processed_photo_assets`
- 추천 collection/member/local asset/group/receipt
- Story와 공유 package
- 인물 DB와 consent
- Android MobileLocationLedger
- Apple/Google 원본과 관리 앨범

따라서 이 기능은 분석 초기화가 아니라 UI·작업 임시 데이터 정리다. 버튼명은 `작업 목록 비우기`가 맞다.

숨은 종속성도 있었다. Story의 재분석 조건이 terminal `curation_operations.request`에만 있으면 작업 목록을 비운 뒤 Story는 남지만 같은 조건 재분석은 불가능해진다. normalized reanalysis spec을 Story scope에도 보존해야 한다.

### 3.2 Story 삭제

현재 Story 삭제는 hard delete가 아니다.

- Story manifest에 `status=deleted` tombstone 기록
- revision 증가와 `deleted_at` 기록
- 연결된 활성 공유를 revoked로 변경하고 session version 증가
- 원본, 추천 파일, 분석 이력, 인물·위치 정보, provider 앨범은 유지

현재 구현에는 복원 UI가 없고 mobile 삭제 경로의 공유 파생 이미지 purge가 완전한 durable cascade로 묶이지 않는다. 장기적으로 30일 휴지통, 전체 share revoke, derivative purge outbox가 필요하다.

### 3.3 기간 전체 재분석

재분석은 새 analysis run과 새 recommendation collection을 만든다. 그러나 기존 구조에는 같은 범위의 이전 collection을 가리키는 `scope_id`, `generation`, `supersedes`, `current head`가 없다.

더 중요한 결함은 수동 Story 생성 시 `origin_run_id`와 날짜만 기록하고 정확한 `collection_ids`를 전달하지 않았다는 점이다. Story evidence는 collection 제한이 없으면 해당 날짜의 모든 과거 로컬 추천을 읽으므로 1차 추천 A와 재분석 추천 B가 섞일 수 있었다.

### 3.4 로컬 추천 보관소

로컬 파일 저장은 SHA-256 content hash로 중복을 막는다.

- 같은 바이트: 같은 `local_asset_id`와 파일 재사용
- 다른 바이트: 새 파일과 새 `local_asset_id`
- 기본 경로: `YYYY/YYYY-MM-DD/`
- 촬영일 없음: `undated/`

이 구조는 물리 파일 중복은 막지만 최신 추천 여부를 표현하지 않는다. 날짜별 `manifest.json`과 월별 group은 현재까지 한 번이라도 추천된 파일의 archive에 가깝다.

### 3.5 추천 앨범

현 publisher는 completed receipt가 없는 새 local asset만 기존 월별 앨범에 추가한다.

```text
1차 추천 A → 월별 앨범 A
재분석 추천 B → 월별 앨범 A ∪ B
```

이전 추천 A가 새 결과에서 탈락해도 월별 group, Apple 앨범, Google 앨범에서 제거하지 않는다.

Apple 경로는 Apple 원본 UUID를 앨범에 추가하거나 Google/로컬 파일을 Apple Photos로 import한 뒤 앨범에 추가한다. 현재 publisher에는 per-item membership 제거가 없다.

Google 경로는 app-created 앨범에 로컬 추천 파일을 새 media item으로 업로드한다. 현재 OAuth는 `photoslibrary.appendonly`이고 제거 기능은 구현하지 않았다. Google은 `photoslibrary.edit.appcreateddata` 동의가 있으면 앱이 만든 album과 media item에 한해 `albums.batchRemoveMediaItems`를 제공하지만, 이는 앨범 membership만 제거하고 media item 자체를 library에서 삭제하지 않는다.

- [Google Photos Manage albums](https://developers.google.com/photos/library/guides/manage-albums)
- [albums.batchRemoveMediaItems](https://developers.google.com/photos/library/reference/rest/v1/albums/batchRemoveMediaItems)

## 4. 목표 데이터 모델

### 4.1 archive와 current를 분리한다

기존 recommendation collection과 로컬 파일은 감사·복구 가능한 archive로 유지한다. 사용자가 기본 화면과 Story에서 보는 최신 결과는 별도 current head로 표현한다.

```text
RecommendationScope
  ├─ generation 1: collection A (superseded)
  ├─ generation 2: collection B (current)
  └─ head ────────────────────────┘
```

권장 신규 구조:

```text
recommendation_scope_heads
- scope_id PK
- current_collection_id
- generation
- result_set_hash
- updated_at

recommendation_collection_generations
- collection_id
- scope_id
- generation
- supersedes_collection_id
- result_set_hash
- state: staging/current/superseded/equivalent/failed

recommendation_destination_attempts
- attempt_id
- scope_id/generation/collection_id
- destination_type/destination_id
- action: add/remove/verify
- local_asset_id/content_hash/provider_media_item_id
- state/error_code/request_hash/result_hash

recommendation_destination_item_state
- destination_type/destination_id/content_hash PK
- present
- provider_media_item_id
- current_generation
- last_verified_at
```

`scope_id`는 run ID가 아니라 provider/source, 촬영일 범위, timezone, selection mode/profile, 화면 캡처 제외 정책, 추천 정책 version 등 사용자가 같은 작업으로 인식하는 입력에서 계산한다.

새 generation의 result set이 이전과 같으면 `equivalent`로 기록하고 provider mutation을 하지 않는다.

### 4.2 Story는 정확한 collection과 revision을 가진다

수동 Story는 반드시 해당 parent run의 child recommendation storage에서 나온 collection ID만 사용한다. 빈 collection 집합도 “필터 없음”이 아니라 “이 run에는 추천 0장”으로 유지한다.

장기 구조:

```text
story_manifest_versions(story_id, revision, manifest_json, evidence_hash)
story_scope_heads(story_scope_id, story_id, revision)
story_reanalysis_specs(story_scope_id, normalized_request)
```

재분석이 실패·취소·timeout이면 기존 Story head를 유지한다. 성공한 새 Story는 이전 Story를 `supersedes_story_id`로 연결한다. 과거 Story를 자동 삭제하지 않는다.

### 4.3 처리 원장도 generation history를 가진다

현재 `processed_photo_assets`는 provider asset별 한 행을 덮어쓴다. 장기적으로 다음처럼 분리한다.

```text
processed_photo_asset_versions(..., generation_id, status, analysis_run_id, ...)
processed_photo_asset_heads(..., current_generation_id)
```

일일 자동화는 head를 이용해 중복 분석을 막고, 명시적 재분석은 새 version을 추가한다. 과거 어느 모델·generation에서 성공했는지 복원할 수 있어야 한다.

## 5. 재분석 결과의 앨범 저장 정책

### 5.1 기본 정책

Android/macOS의 수동 날짜 재분석은 다음을 기본으로 한다.

- 통합 로컬 보관소에 content hash dedupe 저장
- 해당 실행의 exact collection으로 새 Story 생성
- 기존 Apple/Google 추천 앨범 변경 없음
- 기존 Story와 앨범은 새 결과가 완성될 때까지 유지
- 새 결과가 실패하면 기존 current 결과 유지

이 정책은 현재 수동 request의 `publication_policy=none`과 일치한다.

### 5.2 사용자가 새 추천을 앨범에 반영할 때

UI에서 두 모드를 명확히 구분한다.

#### 기존 앨범에 새 추천만 추가

- completed receipt가 있는 사진은 건너뜀
- 기존 앨범 사진은 제거하지 않음
- 부분 실패 시 남은 항목만 재시도
- 일일 자동화의 기본 월별 누적 앨범에 적합

#### 새 버전 앨범으로 게시

- 현재 generation의 추천 snapshot으로 새 album ID 생성
- 이름 예: `2026-09 추천 · v2`
- 이전 앨범과 receipt는 archive로 유지
- 앱의 current album pointer만 새 앨범을 가리킴
- 재분석으로 추천 집합을 새롭게 보여 줄 때 기본 권고

현재는 provider별 안전한 제거·검증이 완성되지 않았으므로 “새 버전 앨범으로 게시”가 가장 정직한 교체 방식이다.

### 5.3 향후 정확한 current 앨범 동기화

provider 기능과 권한이 준비되면 다음 desired-state reconciliation을 제공한다.

```text
desired = active scope heads의 월별 추천 집합
observed = PhotosMcp가 관리하는 실제 album membership
to_add = desired - observed
to_remove = observed_managed - desired
```

안전 순서:

1. 새 추천 추가
2. 실제 membership 검증
3. 더 이상 추천되지 않는 기존 항목의 album membership만 제거
4. 제거 검증
5. append-only attempt와 current item state 기록
6. scope head 전환
7. current Story 재생성

Apple은 exact album UUID와 per-item remove가 필요하다. Google은 `edit.appcreateddata` 재동의, provider media item ID, 최대 50개 batch와 all-or-nothing 실패 처리가 필요하다. 어느 경우에도 원본이나 provider library asset 자체를 자동 삭제하지 않는다.

## 6. 통합 삭제·재분석 수명주기

| 사용자 작업 | 변경 | 유지 | 복구/다음 단계 |
|---|---|---|---|
| 작업 목록 비우기 | terminal 작업 표시, 알림, preview/result, 관리형 임시 Google 파일 | 진행 중 작업, 원본, 처리 원장, 추천, Story, 외부 receipt, 인물, GPS | 되돌리기 없음. 최소 감사 receipt 보존 |
| Story 삭제 | 목록에서 숨김, 공유 session revoke, 파생 cache purge | 원본, 추천, 분석, 앨범, 인물, GPS, 재분석 spec | 30일 휴지통 도입. 공유 URL은 복원하지 않음 |
| 같은 기간 전체 재분석 | 새 job/collection/Story candidate | 기존 current Story/앨범/head, 인물·동의·GPS | 성공 시 새 current 선택, 실패 시 기존 유지 |
| 로컬 추천 자산 정리 | 참조 없는 로컬 파일과 local projection | 외부 receipt와 managed-output exclusion evidence | quarantine 후 지연 GC |
| 기존 앨범에 추가 | 새 추천 membership만 추가 | 기존 album membership | 미완료만 재시도 |
| 새 버전 앨범 게시 | 새 album ID와 snapshot 게시 | 이전 album/receipt | 앱 current pointer 전환, 이전 앨범 수동 보관/정리 |

## 7. 인물·위치 보존 정책

### 인물

다음 작업은 person identity DB를 변경하면 안 된다.

- 작업 목록 비우기
- Story 삭제
- 날짜 전체 재분석
- 추천 앨범 추가/새 버전 게시
- 로컬 추천 파일의 단순 quarantine

재분석 시 확정 identity/name/consent/audit를 재사용한다. 새 얼굴 관측은 새 generation observation으로 만들고 deterministic lineage가 확인될 때만 기존 owner-confirmed membership을 승계한다. 인물 데이터 영구 삭제는 별도 개인정보 기능으로 분리한다.

### 위치

Google Picker 임시 metadata sidecar와 Android 암호화 GPS 원장은 다르게 다룬다.

- Picker 임시 sidecar: job-scoped cache이므로 작업 정리 시 삭제 가능
- Android MobileLocationLedger: durable private evidence이므로 위 일반 작업에서 보존
- 추천 자산의 위치 projection: 새 generation에 다시 연결하거나 갱신
- 위치 영구 삭제: 별도 기기/개인정보 purge 기능에서만 수행

## 8. 즉시 반영하는 Phase 0 안전장치

이번 작업에서 다음을 우선 반영한다.

1. Android에 `해당 기간 전체 다시 분석`을 명시적으로 추가하고 payload의 `reanalyze`를 실제 선택값으로 전달
2. Google Chrome 확정 선택 수와 Picker API 반환 수가 다르면 제한된 재조회 후 `picker_item_count_mismatch`로 fail-closed
3. 수동 Story가 parent child의 exact recommendation collection ID만 사용
4. 빈 collection 집합을 과거 전체 추천으로 확대하지 않음
5. normalized reanalysis spec을 Story scope에 저장해 terminal 작업 목록 정리 후에도 같은 조건 재분석 가능
6. `publication_policy=none`인 수동 materialization을 월별 cloud publish group에 자동 편입하지 않음
7. 로컬 추천 자산 제거 시 `local_store` receipt만 제거하고 Apple/Google receipt는 보존
8. macOS `전체 기록 삭제`를 `작업 목록 비우기`로 바꾸고 보존 항목 명시

Phase 0은 기존 로컬 파일이나 앨범을 migration하지 않는다. 현재 96개 누적 group member와 94개 Apple album receipt는 그대로 유지한다. 과거 데이터를 자동 정리하면 사용자가 보려던 추천 앨범이 바뀔 수 있으므로 current-head backfill과 dry-run 보고서가 먼저다.

## 9. 후속 구현 단계

### Phase 1 — 무결성 진단과 안전한 backfill

- completed job인데 recommendation collection이 없는 수
- collection/member/local asset orphan
- Story가 참조하는 없는 asset
- deleted Story의 active share와 남은 derivative
- local DB와 날짜 manifest 차이
- 외부 receipt가 있는데 local asset이 없는 수
- identity observation이 참조하는 없는 local asset
- released Google lease의 남은 경로·metadata
- completed upload receipt의 남은 upload URL/token
- vendor job이 없는데 남은 exact GPS

보고서는 count와 opaque hash만 사용하고 인물 이름, 실제 경로, provider asset ID, exact GPS를 출력하지 않는다.

### Phase 2 — generation/current head

- recommendation scope/generation/head schema
- result set hash와 equivalent 처리
- 수동/일일 실행의 scope 계산
- archive manifest와 current manifest 분리
- global Story를 active heads로 전환
- 동시 재분석 CAS

### Phase 3 — Story 수명주기

- immutable revision history
- 30일 Story 휴지통과 복원
- reanalysis spec 영구 분리
- 전체 share revoke + derivative purge outbox
- macOS/Android 동일한 삭제·재분석 UX

### Phase 4 — destination history와 새 버전 앨범

- append-only destination attempts
- current item state projection
- 새 버전 album plan/preview/approval
- 외장 볼륨 unavailable과 missing/corrupt 분리
- provider write 성공 후 app receipt 저장 전 crash reconciliation

### Phase 5 — provider membership exact sync

- Apple exact UUID per-item membership remove와 before/after 검증
- Google `edit.appcreateddata` 선택적 재동의
- Google batch remove 50개 분할·all-or-nothing 복구
- library asset 삭제 0회 검증

### Phase 6 — 참조 기반 지연 GC

- Story/share/identity/external receipt reference index
- quarantine → tombstone → grace period → physical delete
- 날짜 manifest 원자 재생성
- Google lease/session/upload secret redaction
- job별 exact GPS·face measurement provenance 정리

## 10. UI 문구 계약

모든 확인창은 같은 순서로 보여 준다.

```text
변경: 이번 작업이 바꾸는 것
유지: 건드리지 않는 것
되돌리기: 복구 가능 여부와 방법
```

권장 명칭:

- `완료된 작업 목록 비우기…`
- `Story 삭제…`
- `같은 기간 전체 재분석…`
- `기존 앨범에 새 추천만 추가`
- `새 버전 앨범으로 게시`

사용하지 않을 명칭:

- `전체 초기화`
- `모든 기록 삭제`
- `추천 앨범 교체` — 실제 provider membership 제거가 확인되기 전까지 금지

## 11. 검증 기준

- 작업 목록 비우기 전후 처리 원장, 추천 자산, Story, 외부 receipt, 인물 DB, GPS 원장 수가 동일
- 작업 목록 정리 후에도 저장된 Story에서 동일 조건 재분석 request 복구
- 수동 실행 Story에 이번 run의 collection 밖 asset이 0개
- 수동 `publication_policy=none` 결과가 approved monthly group에 0개 편입
- Google 선택 수 mismatch에서 analysis/Story 성공 결과 0개
- 로컬 asset 제거 후 외부 Apple/Google receipt와 Google managed-output ID 유지
- 같은 결과 재분석에서 로컬 파일 중복 0개
- 새 버전 게시 실패 시 기존 앨범과 current Story 유지
- provider membership 교체 경로에서 source/library asset delete 호출 0회
- 인물 confirmed name/consent/audit row가 모든 일반 lifecycle 작업 전후 동일

## 12. 이번 결정에서 보류한 것

다음은 데이터 모델과 dry-run 없이 즉시 적용하지 않는다.

- 현재 월별 Apple 앨범의 기존 94개 항목 자동 제거
- 누적 월별 group 96개 member의 자동 재분류
- 로컬 추천 96개 자산의 물리 삭제
- deleted Story 6개의 hard delete
- Google OAuth scope의 강제 확대
- 기존 Google/Apple 앨범의 자동 삭제
- 인물 DB, GPS 원장 또는 확정 이름의 reset 연동

이 보류는 기능 포기가 아니라, 현재 추천과 과거 archive를 구분할 head가 없을 때 사용자 사진과 provenance를 손상하지 않기 위한 순서 제어다.
