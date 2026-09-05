# 추천 사진 통합 보관과 그룹 앨범 이중 저장 계획 — 2026-09-04

## 1. 최종 목표와 결정

Google Photos와 Apple Photos에서 수집·분석한 사진 중 PhotosMcp가 장면별 추천으로
확정한 사진만 다음 두 목적지에 보존한다.

1. **필수 1차 목적지:** 한곳의 PhotosMcp 로컬 관리 보관소
2. **선택된 2차 목적지:** 그룹별로 지정한 Apple Photos 또는 Google Photos 앨범

로컬 보관소는 추천 사진의 실제 바이트를 보유하는 기준 사본이다. Apple Photos
자산도 UUID 참조만 남기지 않고 추천 확정 후 원본 리소스를 로컬로 내보낸다.
따라서 원본 서비스의 앨범·계정 상태가 바뀌어도 추천본을 독립적으로 보존할 수
있다.

로컬 파일은 촬영 날짜별로 정리한다.

```text
/Volumes/ExtData/02_Services/PhotosMcp/recommendations/
  2026/
    2026-09-03/
      20260903_142233_google_a19c27ef.jpg
      20260903_180210_apple_4e82c901.heic
      manifest.json
    2026-09-04/
      ...
  undated/
    ...
```

앨범은 날짜 폴더와 별개의 **논리 그룹**이다. 모든 날짜마다 cloud 앨범을 만들지
않고, 기본 월별 그룹 또는 사용자가 지정한 행사·가족·여행 그룹을 사용한다.

```text
기본 그룹: 2026-09 추천
사용자 그룹 예: 2026 제주 가족여행
```

각 그룹의 cloud 목적지는 `apple_photos`, `google_photos`, `local_only` 중 하나로
고정한다. 기본값은 Apple Photos로 권장하며, 같은 그룹을 두 cloud에 동시에
복제하는 기능은 초기 범위에서 제외한다. 이 구조는 기본적으로 `로컬 + cloud 한
곳`의 두 사본을 만든다.

## 2. 이전 안에서 변경하는 사항

이전 계획은 Apple Photos 추천 사진을 UUID로만 참조하고 Google Photos 추천
파일만 로컬 관리 보관소로 승격하도록 잡았다. 개인 사진의 장기 보존이라는 현재
목표에는 부족하므로 다음과 같이 변경한다.

| 항목 | 이전 안 | 변경 안 |
|---|---|---|
| Apple 추천 사진 | Photos UUID 참조만 저장 | 추천된 실제 사진 리소스를 로컬 날짜 폴더에 export |
| Google 추천 사진 | 임시 cache에서 추천본만 승격 | 동일하게 유지하되 촬영 날짜별 통합 보관소로 승격 |
| 로컬 구조 | hash object와 manifest 중심 | 사용자가 바로 탐색할 수 있는 촬영 날짜별 실제 파일 + manifest |
| 2차 앨범 | Apple 월별 앨범 고정 | 그룹마다 Apple 또는 Google 중 한 목적지를 선택 |
| Google 앨범 | 기존 원본 재업로드 금지 | 명시적 목적지인 경우 로컬 추천본을 앱 생성 앨범에 재업로드 허용 |
| 완료 의미 | 소스별로 다름 | 로컬 hash 검증 후 2차 앨범까지 reconciliation되어야 완료 |

## 3. 현재 구현에서 재사용할 수 있는 기반

현재 photo-ranker는 `photo_results`에 다음 추천 근거를 이미 저장한다.

- `recommended_in_cluster`
- `recommendation_slot`
- `scene_cluster_id`, `scene_cluster_size`, `cluster_rank`
- `selection_reason_json`
- 품질·기술·종합 점수

실제 운영 DB의 최근 Google 분석 job도 19~23장 중 장면 추천 6~8장을 기록했다.
추천 판정 자체를 새로 만들 필요는 없다.

현재 구현에서 그대로 활용할 부분은 다음과 같다.

- Google Picker가 내려받은 media item의 임시 파일과 provider asset ID
- Apple Photos UUID 기반 조회와 기존 add-only album writer
- 외부 파일을 Apple Photos에 import한 뒤 앨범에 추가하는 경로
- Google Photos 앱 생성 앨범과 업로드 복구 backend
- 처리 원장, mutation plan, approval token, receipt, Telegram KST 알림

다만 현재 `photos_write(action="add_selected_to_album")`은
`get_review_items(..., selected_only=true)`를 사용한다. 여기서 `selected`는
사용자 검토 UI의 선택 상태이고 `recommended_in_cluster`와 같지 않다. 자동화는
이 action을 직접 재사용하지 않고 정확한 추천 스냅샷을 입력으로 하는 별도
publish action을 가져야 한다.

## 4. 정확한 추천 집합 계약

분석이 terminal 완료된 시점에 불변 추천 스냅샷을 만든다. 포함 조건은 다음으로
고정한다.

```text
recommended_in_cluster == true
AND recommendation_slot IN (1, 2)
AND photo_id가 비어 있지 않음
```

다음 값을 대체 조건으로 사용하지 않는다.

- UI의 `selected=true`
- 점수 상위 N%
- 단순히 가장 최근 run
- 파일명이나 날짜 패턴

같은 분석 run과 같은 추천 정책 버전은 항상 같은 추천 컬렉션을 가리켜야 한다.
정책 변경으로 과거 추천을 자동 해제하거나 로컬 파일·앨범 멤버를 제거하지 않는다.

## 5. 데이터 모델

### 5.1 `recommendation_collections`

```text
collection_id
automation_run_id
analysis_run_id
provider
source_id
local_run_date
selection_profile
scoring_version
recommendation_policy_version
status
recommended_count
created_at / completed_at
```

권장 고유 키:

```text
UNIQUE(analysis_run_id, recommendation_policy_version)
```

### 5.2 `recommendation_members`

```text
collection_id
provider
provider_asset_id
photo_id
scene_cluster_id
recommendation_slot
selection_reason_json
total_score / quality_score / technical_score
captured_at
capture_timezone
capture_date_local
capture_date_confidence
source_reference_kind
content_hash
local_asset_id
materialization_status
```

`capture_date_local` 결정 우선순위는 다음과 같다.

1. 원본 EXIF/Photos metadata의 timezone 포함 촬영 시각
2. provider creation time과 해당 timezone
3. timezone 없는 촬영 시각을 `Asia/Seoul`로 해석
4. 모두 없으면 `undated`에 저장하고 추정하지 않음

파일 mtime이나 다운로드 시각을 촬영 날짜로 사용하지 않는다.

### 5.3 `local_recommendation_assets`

```text
local_asset_id
content_hash
relative_path
mime_type
byte_size
original_filename_redacted
resource_role          # primary | paired_video | raw_sidecar | adjustment
source_count
verified_at
```

동일 바이트가 Google Photos와 Apple Photos 양쪽에서 들어오면 SHA-256으로 한 번만
저장하고 여러 `recommendation_members`가 같은 `local_asset_id`를 참조한다.

```text
UNIQUE(content_hash, resource_role)
```

### 5.4 `recommendation_groups`

```text
group_id
group_type             # monthly | event | manual
display_name
date_from / date_to
destination_provider   # apple_photos | google_photos | local_only
destination_album_id
destination_album_name
policy_state           # draft | approved_once | standing
created_at / updated_at
```

처음에는 모든 추천본을 월별 기본 그룹에 넣는다. 사용자가 특정 행사를 지정하거나
향후 grouping 결과를 승인하면 별도 그룹으로 연결할 수 있다. 날짜 폴더의 파일을
실제로 이동하지 않고 DB의 그룹 멤버십만 추가한다.

### 5.5 `recommendation_destination_receipts`

```text
receipt_id
collection_id
group_id
local_asset_id
destination_type       # local_store | apple_album | google_album
destination_id
provider_media_item_id
state                  # planned | writing | partial | completed | failed
attempt_count
error_code
created_at / updated_at / reconciled_at
```

권장 고유 키:

```text
PRIMARY KEY(receipt_id)
UNIQUE(group_id, local_asset_id, destination_type, destination_id)
```

`receipt_id`는 `group + local asset + destination type`으로 안정적으로 만든다.
첫 실패에서 임시 `managed:<group>` 목적지를 기록한 뒤 실제 album ID를 확보한
경우에도 같은 receipt를 갱신해야 하므로, upsert 충돌 기준은 `receipt_id`로 둔다.

## 6. 소스별 로컬 보관 방법

| 입력 | 로컬 기준 사본 생성 | 주요 검증 |
|---|---|---|
| Apple Photos | 추천 UUID의 원본/current resource를 관리 root로 export | export 완료, byte size, SHA-256, 자산 UUID 매핑 |
| Google Photos | Picker 임시 cache의 추천 파일만 관리 root로 원자적 승격 | base URL 만료 전 다운로드, byte size, SHA-256, media item ID 매핑 |
| Local source | 비관리 위치라면 추천 파일만 복사 | source path 안정성, byte size, SHA-256 |

관리 root는 다음 설정으로 분리한다.

```text
PHOTOS_MCP_RECOMMENDATION_ROOT=/Volumes/ExtData/02_Services/PhotosMcp/recommendations
```

외장 볼륨이 없거나 쓰기·여유 공간 검사가 실패하면 내장 디스크로 몰래
fallback하지 않는다. 로컬 목적지를 `failed/deferred`로 기록하고 Telegram으로
알리며 cloud 앨범 반영도 시작하지 않는다.

쓰기 순서는 `임시 파일 → fsync → SHA-256 검증 → 최종 경로 rename → DB receipt`
로 한다. 날짜 폴더의 파일명은 충돌과 개인정보 노출을 피하면서도 정렬 가능하게
만든다.

```text
{YYYYMMDD}_{HHMMSS}_{provider}_{hash8}.{ext}
```

`manifest.json`에는 추천 근거, source provider, provider asset ID의 비민감
fingerprint, content hash, 촬영 시각, 목적지 상태를 넣는다. OAuth token, Picker
URI, 임시 download URL, 얼굴 embedding은 넣지 않는다.

### 복합 사진 리소스

개인 사진의 보존 품질을 위해 확장자가 JPEG가 아니라고 임의 변환하지 않는다.

- HEIC/PNG/JPEG: 원본 형식을 유지한다.
- Live Photo: still image와 paired video를 같은 local asset bundle로 보존한다.
- RAW+JPEG: 가능한 경우 양쪽 리소스를 같은 local asset에 연결한다.
- 편집된 Apple 사진: 원본과 현재 렌더링 중 어떤 것을 내보냈는지 manifest에
  기록한다. 1차 구현은 current 렌더링과 원본 primary resource를 모두 보존하는
  것을 목표로 한다.

Google Photos 재업로드가 Live Photo/RAW의 원래 동작을 완전히 복원한다고
가정하지 않는다. 지원하지 못하는 리소스는 `partial_compatibility`로 표시하고
Apple Photos 목적지 또는 `local_only`를 권장한다.

## 7. 2차 앨범 목적지 전략

### 7.1 기본 권장: Apple Photos 앨범

Apple Photos를 기본값으로 권장하는 이유는 다음과 같다.

- Apple 원본 추천은 기존 asset UUID를 add-only로 앨범에 넣을 수 있다.
- Google 추천은 로컬 기준 사본을 Apple Photos에 한 번 import한 뒤 같은 앨범에
  넣을 수 있다.
- Google Picker로 선택한 기존 Google 자산을 Library API에서 기존 자산 그대로
  임의 앨범에 넣는 방식보다 중복과 제약이 적다.

Apple 원본은 `로컬 export + 기존 Apple asset의 앨범 멤버십`으로 두 위치에
존재한다. Google 원본은 `로컬 사본 + Apple import`로 두 위치에 존재한다.

### 7.2 선택형: Google Photos 앱 생성 앨범

현재 Google Photos Library API는 앱이 생성한 콘텐츠와 앨범을 중심으로
관리한다. Picker로 사용자가 고른 기존 Google 사진의 ID를 기존 자산 그대로 새
앨범에 추가하는 경로로 설계하지 않는다.

그룹 목적지가 Google Photos인 경우 다음 순서가 필요하다.

1. 로컬 기준 사본을 Google Photos에 앱 생성 항목으로 업로드한다.
2. PhotosMcp가 생성하고 ID를 보관한 앨범에 해당 항목을 추가한다.
3. 반환된 Google media item ID와 album ID를 receipt에 저장한다.
4. 재실행 시 같은 `group_id + local_asset_id`를 다시 업로드하지 않는다.

Google에서 가져온 사진도 이 경로에서는 새 앱 생성 항목으로 재업로드되므로
Google 계정 안에 원본과 추천 사본이 함께 존재할 수 있다. 이것은 장애가 아니라
Google 앨범을 2차 보관 대상으로 선택했을 때의 명시적인 복제 정책이다.

### 7.3 그룹과 앨범 매핑

초기 정책은 앨범 폭증을 피하도록 다음과 같이 한다.

```yaml
default_group:
  type: monthly
  name: "{year}-{month} 추천"
  destination: apple_photos

example_overrides:
  - group: "2026 제주 가족여행"
    destination: google_photos
  - group: "개인 문서 사진"
    destination: local_only
```

특정 그룹은 사용자가 이름과 목적지를 확정한 뒤에만 만든다. 자동 grouping이
후보를 제안할 수는 있지만 사람·장소를 근거로 임의의 cloud 앨범을 생성하지
않는다.

## 8. 시작부터 종료까지의 목표 흐름

```text
03:00 KST daily discovery
  -> Apple 증분 UUID / Google 최근 10일 Picker 입력
  -> 처리 원장으로 이미 분석한 자산 제외
  -> 품질·장면·중복·VLM 분석
  -> 분석 job terminal 완료 확인
  -> recommended_in_cluster=true exact member 조회
  -> recommendation collection + members 불변 저장
  -> 추천 0장
       -> 로컬/cloud write 없음
       -> Telegram 0건 정상 완료
  -> 추천 1장 이상
       -> Apple 추천 resource export
       -> Google 추천 cache 파일 승격
       -> 촬영 날짜별 최종 경로에 원자적 저장
       -> byte size + SHA-256 + DB receipt 검증
       -> 기본 월별 또는 승인된 특정 그룹 연결
       -> 그룹의 목적지 provider와 album UUID/ID 확인
       -> exact member로 add/import/upload plan 생성
       -> 1회 승인 또는 제한형 standing policy 확인
       -> Apple add/import 또는 Google upload/add 수행
       -> 실제 목적지 재조회와 receipt reconciliation
       -> Telegram에 분석/추천/로컬/앨범/실패 건수 요약
```

**로컬 성공이 cloud 쓰기의 선행 조건**이다. 로컬 저장에 실패한 사진은 cloud
앨범에만 먼저 넣지 않는다. 반대로 cloud 앨범 쓰기가 실패하면 이미 검증된 로컬
파일은 유지하고 실패한 2차 목적지만 재시도한다.

## 9. 안전 정책

### 허용 동작

- 추천 스냅샷의 exact member만 로컬로 복사/export
- PhotosMcp 관리 folder 아래 월별·승인된 그룹 앨범 생성
- 기존 Apple Photos UUID를 지정된 Apple 앨범에 add-only로 추가
- Google/local 추천 파일을 Apple Photos에 1회 import 후 추가
- 로컬 추천 파일을 Google 앱 생성 앨범에 1회 upload 후 추가
- 부분 실패 재개와 실제 목적지 재조회

### 금지 동작

- 원본 사진 삭제·수정·이동
- 다른 앨범에서 사진 제거
- 추천 해제에 따른 로컬 파일 또는 앨범 멤버 자동 제거
- `cleanup_album`
- 추천되지 않은 분석 결과의 export/import/upload
- 이름만 같은 여러 앨범 중 임의 선택
- 로컬 hash 검증 전 cloud upload
- 실제 사진 root를 WebUI, Hermes dashboard 또는 Tailscale serve로 직접 공개

관리 root와 DB는 현재 사용자만 읽을 수 있도록 권한을 제한한다. 외장 볼륨은
가능하면 암호화된 APFS를 사용한다. Telegram에는 파일명·얼굴·장소를 보내지 않고
집계 건수, group display name, 오류 단계만 보낸다.

## 10. 승인 단계

### Phase A — 통합 로컬 추천 보관소

- `recommended_in_cluster` exact filter 구현
- 추천 컬렉션·멤버·로컬 asset·receipt 원장 추가
- Apple 추천 resource를 촬영 날짜별로 export
- Google 추천 cache를 같은 날짜 구조로 승격
- SHA-256 기반 cross-provider 중복 제거
- 추천 0건 포함 Telegram KST 요약
- Photos 앱과 Google Photos mutation 없음

이 단계가 두 cloud 목적지보다 먼저 완료되어야 한다.

### Phase B — Apple test group 1회 승인

- 비민감 추천 사진 1~5장 사용
- Apple 출처와 Google 출처를 최소 1장씩 포함
- 관리 folder/앨범 plan을 사용자에게 표시
- 기존 1회용 approval token으로 실행
- Apple 원본은 UUID add, Google 원본은 local file import 후 add
- 같은 plan 재호출 시 추가 멤버십·재import 0건 확인

### Phase C — Google test group 1회 승인

- 사용자가 Google Photos 목적지를 원하는 경우에만 진행
- 추천 로컬 사진 1~3장을 앱 생성 앨범에 upload/add
- Google 원본도 새 사본이 생긴다는 점을 실행 plan에 표시
- 같은 plan 재호출 시 재upload 0건 확인
- 업로드 중단과 만료 token 뒤 실패 receipt만 재개

### Phase D — 그룹별 제한형 standing policy

각 provider의 live test가 통과한 뒤 그룹별로만 사전 승인한다.

```yaml
source_collection: recommendation_collection
required_member_flag: recommended_in_cluster
allowed_slots: [1, 2]
require_local_receipt: completed
group_id: fixed
destination_provider: fixed
destination_album_id: fixed
max_runs_per_day: 1
max_recommended_assets_per_day: 50
allowed_mutations:
  - create_managed_group_album
  - add_existing_apple_assets
  - import_local_asset_to_apple
  - upload_local_asset_to_google
forbidden_mutations:
  - delete_asset
  - delete_album
  - remove_from_album
  - cleanup_album
```

Apple add/import와 Google upload는 서로 다른 standing policy로 둔다. 한 provider의
승인이 다른 provider에 대한 쓰기 권한으로 확장되지 않는다.

## 11. 필요한 코드 변경

1. `RunRepository`
   - 추천 컬렉션·멤버·local asset·group·destination receipt 테이블 추가
2. photo-ranker query
   - `get_recommended_items(run_id, policy_version)` exact API 추가
   - 기존 `selected_only` 경로를 재사용하지 않음
3. Apple materializer
   - UUID별 원본/current/paired resource export와 iCloud download 대기
   - 촬영 날짜·timezone·resource role 추출
4. Google materializer
   - Picker base URL 만료 전 추천 파일만 관리 root로 승격
   - 이미 내려받은 cache의 source ID와 hash 연결
5. managed recommendation store
   - 촬영 날짜별 경로, atomic write, fsync, hash, 충돌·중복 억제
   - manifest 생성과 mount·용량 검사, partial resume
6. group policy service
   - 월별 기본 그룹과 사용자 지정 그룹
   - group별 단일 cloud provider/album ID 고정
7. mutation plan
   - `publish_recommendation_group` action 추가
   - collection/group/local asset ID를 plan에 고정
8. destination adapter
   - Apple 기존 UUID add 및 외부 파일 import
   - Google app-created upload/add와 upload receipt 복구
9. daily reconciliation
   - 분석 완료 → 로컬 저장 완료 → cloud publish의 상태 전이
10. Hermes notification
   - 분석 수, 추천 수, 로컬 신규/중복 수, Apple/Google 앨범 추가 수,
     재시도·실패 수를 KST로 전달

## 12. 테스트와 완료 조건

### 자동 테스트

- 추천 false 사진이 컬렉션·로컬 파일·앨범 plan에 들어가는 건수 0
- `selected=true`지만 추천 false인 사진도 모든 자동 목적지에서 제외
- 추천 true지만 UI selected=false인 사진은 추천 컬렉션에 포함
- 동일 analysis run 재처리 시 컬렉션·멤버 중복 0
- 날짜 결정 우선순위와 `undated` fallback 검증
- Apple과 Google의 동일 content hash가 로컬 파일 1개로 수렴
- 최종 rename 전 실패 시 불완전 파일이 날짜 폴더에 노출되지 않음
- 로컬 receipt 실패 사진의 cloud mutation 0
- 동일 Apple UUID의 앨범 재반영과 Google/local import 중복 0
- 동일 Google local asset의 재upload 0
- 추천 0장일 때 파일·앨범 생성 0, Telegram 성공 알림 1
- partial 실패 후 성공 목적지는 반복하지 않고 실패 목적지만 재개
- 잘못된 album ID, 만료 approval, 정책 밖 사진은 100% 차단
- 원본 삭제·수정·remove-from-album 호출 0

### live gate

1. Apple 추천 1장과 Google 추천 1장을 로컬 날짜 폴더에 저장한다.
2. 파일을 직접 열고 source metadata·byte size·SHA-256과 receipt를 대조한다.
3. 같은 collection을 재실행해 로컬 신규 파일 0건을 확인한다.
4. Apple test group에 두 출처를 각각 1장 추가하고 재실행 중복 0건을 확인한다.
5. Google 목적지가 필요하면 별도 test group에 1~3장 upload/add하고 중복 0건을
   확인한다.
6. PhotosMcp 재시작 후 collection, group, receipt, album ID가 복구되는지 확인한다.
7. 외장 볼륨 분리, iCloud 원본 미다운로드, Google base URL 만료, 네트워크 단절을
   각각 재현해 안전한 실패와 Telegram KST 알림을 확인한다.

### 완료 기준

- 모든 자동 저장 대상이 추천 스냅샷 exact member와 100% 일치
- Apple·Google 추천 모두 날짜별 로컬 기준 사본과 검증 hash 보유
- 동일 콘텐츠의 로컬 물리 중복 0
- 각 group은 최대 하나의 cloud 목적지와 안정적인 album ID 보유
- 완료된 항목은 `로컬 1차 + 선택 cloud 2차` 상태를 receipt로 증명
- 추천 0장인 날 빈 앨범 0개
- 원본 삭제·수정·이동 0건
- 모든 cloud write에 plan, 승인 근거, receipt, reconciliation 존재
- 오류와 정상 완료가 KST Telegram으로 구분 전달

## 13. 권장 구현 순서

1. 추천 컬렉션·멤버와 exact query를 구현한다.
2. 통합 로컬 보관소 및 hash/receipt를 구현한다.
3. Apple과 Google 추천 각 1장으로 로컬 materialization live gate를 통과한다.
4. 기본 월별 그룹과 사용자 지정 group→provider 매핑을 구현한다.
5. Apple test group의 1회 승인 E2E를 통과한다.
6. 사용자가 실제로 Google 목적지를 원하는 group이 있을 때만 Google test group
   E2E를 진행한다.
7. Telegram 집계와 실패 재개를 검증한다.
8. 안정화 뒤 Apple 기본 월별 group의 add-only standing policy를 활성화한다.
9. Google standing policy는 저장공간·중복 증가를 확인한 후 group별로 별도
   활성화한다.

현재 단계의 권장 목표는 **Phase A를 먼저 완성하고, 2차 목적지는 Apple test
group으로 검증한 뒤 Google은 필요한 특정 그룹에만 선택적으로 여는 것**이다.
cloud 계획 확정 전에는 Apple Photos 앨범 생성·추가나 Google Photos 업로드를
수행하지 않는다.

## 14. 2026-09-04 구현·배포 상태

### 완료된 기반

- 분석 결과의 UI 선택 상태와 독립된 `get_recommended_items` exact query를 추가했다.
  포함 조건은 `recommended_in_cluster=true`와 `recommendation_slot in (1, 2)`로
  고정된다.
- 추천 컬렉션, 멤버, 로컬 자산, 논리 그룹, 그룹 멤버, 목적지 receipt를 운영
  SQLite DB에 추가했다. 같은 분석 run과 정책 버전, 같은 콘텐츠 hash, 같은 그룹
  멤버십과 목적지 receipt는 재실행해도 중복 생성되지 않는다.
- Apple 원본 준비와 Google Picker lease 매핑을 통해 추천 파일만 관리 root로
  복사한다. 촬영일별 경로, 원자적 rename, fsync, SHA-256 검증, `0700/0600`
  권한, cross-provider hash 중복 제거를 적용했다.
- 날짜별 `manifest.json`에는 로컬 상대 경로, hash, 크기, 촬영일과 추천 근거를
  기록한다. provider 자산 ID는 원문 대신 16자리 SHA-256 fingerprint만 남긴다.
- 추천 파일은 기본 월별 그룹 `monthly:YYYY-MM`에 연결한다. 초기 cloud 목적지는
  Apple Photos이며 `PHOTOS_MCP_RECOMMENDATION_DEFAULT_DESTINATION`으로
  `google_photos` 또는 `local_only`를 선택할 수 있다.
- `photos_query(action="recommendation_groups")`와
  `photos_query(action="recommendation_group")`으로 로컬 파일 경로를 노출하지 않고
  그룹·hash prefix·receipt를 조회할 수 있다.
- `photos_write(action="configure_recommendation_group")`은 그룹별 cloud 목적지와
  앨범 이름/ID를 승인 후 고정한다. 완료된 cloud receipt가 있는 그룹을 다른
  provider나 album ID로 바꾸는 요청은 차단한다. Google을 선택한 계획에는 새
  사본을 업로드한다는 경고가 포함된다.
- `photos_write(action="publish_recommendation_group")`은 현재 로컬 hash를 다시
  검증한 exact plan을 먼저 반환한다. 승인 token과 동일한 fingerprint의 계획만
  Apple UUID add/외부 파일 import 또는 Google app-created upload/add를 실행한다.
- Google destination은 저장된 album ID를 재사용하며, 성공한 local asset별 media
  item ID를 receipt에 저장한다. 이미 완료된 asset은 다음 계획에서 제외한다.
- Hermes의 5분 알림 worker가 알림을 점유하기 전에 loopback reconciliation을
  실행한다. 추천 저장 성공·부분 실패는 KST 집계 알림으로 보내고, 분석 자체의
  terminal 실패와 중복되는 저장 실패 알림은 만들지 않는다. 저장 worker에
  연결할 수 없으면 시간 단위로 중복 억제한 redacted 오류를 보낸다.

### 검증 결과

- PhotosMcp 전체 회귀: `749 passed`
- Hermes 알림·reconciliation 브리지: `14 passed`
- 임시 standalone bundle: 코드서명, `--health`, runtime import smoke, vendor runtime
  smoke 통과
- 정식 설치본: `/Volumes/ExtData/02_Services/PhotosMcp/PhotosMcp.app`에 배포하고
  재기동, `127.0.0.1:18791` health `ok`
- 실제 loopback reconciliation: 과거 자동화에 연결된 완료 job 1건을 안전하게
  처리했고 추천 0장, 신규 파일 0장으로 종료했다. 과거 수동 분석 job들은 자동화
  run에 연결되지 않아 소급 복사하지 않았다.
- 실제 MCP query: 공개 도구 4개 유지, recommendation group 조회 성공, 현재 그룹
  0개 확인
- 실제 Apple/Google 앨범 생성·추가·업로드: 수행하지 않음. 현재 자동화 연결
  그룹에 추천 사진이 없어 승인할 exact cloud plan이 아직 없기 때문이다.

### 현재 구현 경계와 다음 live gate

현재 1차 구현은 각 추천 항목의 검증된 primary 사진 resource를 보존한다. Live
Photo paired video, RAW sidecar, Apple 원본과 편집본 동시 보존은 데이터 모델의
`resource_role` 확장 지점은 마련했지만 아직 여러 resource를 함께 materialize하지
않는다. 이 항목은 primary 저장 live gate 후 별도 호환성 단계에서 추가한다.

다음 03:00 KST 실행 또는 수동 일일 실행에서 추천이 1장 이상 생기면 다음 순서로
마무리한다.

1. 날짜 폴더 파일·manifest·SHA-256·local receipt를 대조한다.
2. 같은 collection을 재조정해 신규 파일 0장을 확인한다.
3. 월별 그룹 조회 결과의 사진 수, Apple 목적지, 앨범명을 사용자에게 제시한다.
4. 승인 후에만 `publish_recommendation_group`을 호출한다.
5. Apple 앨범과 receipt를 대조하고 같은 호출의 중복 억제를 확인한다.
6. Google 목적지가 실제로 필요한 그룹이 생기면 먼저
   `configure_recommendation_group` 계획의 재업로드 경고를 승인한 뒤 별도
   1~3장 live gate를 수행한다.

## 15. 2026-09-05 운영 전환 결과

- 운영 추천 root의 `2026/2026-09-04` 날짜 그룹에 추천 사진 10장과 비공개
  manifest가 저장돼 있으며 파일 hash를 다시 검증했다.
- 승인된 월별 그룹 `monthly:2026-09`의 10장을 Apple Photos
  `Photos MCP/2026-09 추천` 앨범으로 가져왔다.
- Apple Photos 읽기 대조 결과 album ID는
  `233317BA-3F01-46F9-9A8A-75B5DCF65ADE`, 앨범 사진 수는 10장이다.
- 초기 Terminal helper 실패 뒤 재실행에서 사진 가져오기는 성공했지만, 실패
  receipt의 임시 목적지 ID를 실제 album ID로 바꾸는 upsert가 primary-key
  충돌을 일으켰다. 충돌 기준을 안정적인 `receipt_id`로 변경하고 실제 앨범
  10장 및 vendor `imported=10` 로그를 대조해 receipt 10건을 `completed`로
  복구했다.
- 그룹은 실제 album ID와 `approved_once` 정책 상태를 보존한다. 완료된 10장은
  후속 계획에서 제외되며, 0건 계획은 새 approval token을 만들지 않는 terminal
  no-op으로 반환한다.
- Apple Photos 직접 호출이 무기한 대기하지 않도록 운영 기본값을 제한시간 있는
  Terminal helper로 바꿨고, 기본 timeout은 240초다. Google Picker의 원격
  워크스테이션 준비 제한시간은 Hermes worker에서 600초다.
- `~/.photos-mcp` runtime/cache/log root와 추천 보관소는 디렉터리 `0700`, 핵심
  DB·manifest·추천 사진은 `0600`을 적용한다.
- 최종 일일 진입점으로 Apple 최근 사진 10장을 실제 분석해 job `efafb939`가
  10/10 완료됐고, 추천 3장은 Google 쪽 추천본과 같은 hash라 신규 파일 없이
  3건 모두 중복 통합됐다. 이 후처리에서 기존 월별 그룹 정책을 기본 `draft`로
  되돌리는 문제를 발견해, 새 멤버 추가 시 기존 destination/album ID/policy를
  보존하도록 수정했다. 운영 그룹은 다시 `approved_once`와 실제 album ID를
  유지하며 pending event는 0건이다.

남아 있는 확장 범위는 Live Photo paired video, RAW sidecar, Apple 편집본/원본
동시 보존 및 실제 필요가 생긴 그룹의 Google Photos 1~3장 별도 live gate다.
현재의 기본 운영 목표인 `로컬 날짜별 기준 사본 + Apple 월별 그룹 앨범`은
실제 데이터로 완료했다.

## 16. 외부 API 근거

- Google Photos Picker API의 선택 media item 조회와 임시 base URL 수명:
  <https://developers.google.com/photos/picker/guides/media-items>
- Google Photos Library API의 앱 생성 콘텐츠·앨범 관리 범위:
  <https://developers.google.com/photos/library/guides/get-started-library>
- Google Photos 앱 생성 앨범 생성과 media item 추가:
  <https://developers.google.com/photos/library/guides/manage-albums>
- Google Photos 업로드와 앱 생성 앨범 지정:
  <https://developers.google.com/photos/library/guides/upload-media>
- Apple PhotoKit의 기존 asset을 album/collection에 추가하는 change request:
  <https://developer.apple.com/documentation/photokit/requesting-changes-to-the-photo-library>
