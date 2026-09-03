# PhotosMcp 일일 사진 큐레이션 자동화 설계·구현 계획

- 작성일: 2026-09-03 (Asia/Seoul)
- PhotosMcp 기준 revision: `5e9ae04`
- Hermes 운영 문서 기준 revision: `bcf4a34`
- 범위: Google Photos·Apple Photos 신규 사진의 일일 선별, 검토, 앨범 정리, Hermes cron·Telegram 연동
- 비범위: 원본 자동 삭제, 기존 앨범 자동 삭제, Google Photos 전체 라이브러리 무인 열람, Google Photos 데이터의 얼굴 군집화

## 1. 결론

권장안은 **Apple Photos부터 비파괴적 일일 큐레이션을 시작하고, Google Photos는 사용자 선택형 inbox로 분리**하는 것이다.

첫 운영 단계에서 원본을 삭제하거나 이동하지 않는다. 매일 새로 들어온 Apple Photos 사진을 찾고, 현재 PhotosMcp의 품질·중복·장면 분석으로 장면별 Top-1 또는 서로 다른 Top-2를 만든 뒤, 결과만 Telegram과 PhotosMcp 결과 화면에 제시한다. 14일 shadow 운영이 통과하면 사용자가 승인한 Apple Photos 앨범 멤버십 추가를 활성화한다.

Google Photos는 동일한 자동 수집원으로 취급하지 않는다. 2025-03-31 이후 Google Photos Library API는 앱이 만든 콘텐츠 중심으로 제한됐고, 사용자의 기존 보관함은 Picker에서 사용자가 직접 선택해야 한다. 따라서 Google Photos의 현실적인 자동화 단위는 다음과 같다.

```text
사용자가 Picker에서 최근 사진 선택
  -> PhotosMcp가 선택 결과를 자동 다운로드
  -> 얼굴 분석 없이 품질·장면 분석
  -> 검토 결과 생성
  -> 사용자가 별도로 승인한 경우에만 결과를 내보냄
```

Hermes와 PhotosMcp의 책임은 다음처럼 나눈다.

| 구성 요소 | 책임 |
|---|---|
| PhotosMcp | 신규 자산 발견, 처리 ledger, 품질·장면 분석, 승인 정책, 앨범 쓰기, 영수증·재조정 |
| Hermes cron | 하루 1회 실행, 상태 조회, 실패 감지, 결과 digest 예약 |
| Telegram | 완료·실패·검토 필요 알림과 명시적 승인 진입점 |
| PhotosMcp.app | 사진 원본·대안 비교, 대량 검토, 최종 선택 수정 |
| Linux VLM broker | 로컬 prefilter를 통과한 후보가 있을 때만 한 번 준비하고 배치 분석 |
| Open WebUI | 일반 질의와 상태 확인; 야간 자동화의 제어면으로 사용하지 않음 |

핵심 원칙은 다음 다섯 가지다.

1. **정리는 삭제가 아니라 앨범 멤버십으로 시작한다.**
2. **새 사진은 촬영일만으로 찾지 않고 라이브러리 추가 시각과 provider 자산 ID로 추적한다.**
3. **모델은 후보와 설명을 제안하고, 실제 쓰기는 결정론적 정책 엔진이 허용 범위를 검사한다.**
4. **Google과 Apple을 같은 capability로 추상화하되 동일한 권한을 가진 저장소처럼 취급하지 않는다.**
5. **Hermes core에 사진 전용 도구를 추가하지 않고 기존 Photos MCP 네 도구와 cron 경계를 확장한다.**

## 2. 검토 방법과 다중 에이전트 합의

요청에 따라 네 개의 독립 전문 sub-agent와 주 에이전트의 교차 검토를 합쳐 총 다섯 개 관점으로 재검토했다.

| 검토 트랙 | 중점 |
|---|---|
| PhotosMcp 구현 감사 | source adapter, 공개 MCP action, ranking, persistence, 테스트 결손 |
| Hermes 자동화 | cron, toolset 경량화, Telegram, retry, Linux on-demand runtime |
| 사진 큐레이션 제품 설계 | 잘 나온 사진의 정의, 장면별 Top-2, 이벤트·다양성, 검토 UX |
| 개인정보·안전 | mutation approval, Google 정책, 얼굴 데이터, 삭제·중복 업로드 위험 |
| 통합 아키텍처 교차 검토 | 책임 분리, 단계별 rollout, 대안 비교, acceptance gate |

다섯 검토 트랙의 공통 결론은 다음과 같다.

- 현재 추천 품질은 **후보 앨범**에는 충분히 유용하지만 **원본 자동 삭제**의 근거로는 부족하다.
- Apple Photos는 거의 현재 기능 그대로 시작할 수 있지만, 증분 처리 ledger와 안정 pagination이 먼저 필요하다.
- Google Photos 전체 보관함의 일일 무인 수집은 현재 공식 API로 구현할 수 없다.
- 처음에는 읽기 전용 shadow가 맞고, 자동 쓰기는 add-only standing approval을 별도로 구현한 뒤에만 허용해야 한다.
- 사진 자동화의 durable state와 안전 정책은 Hermes 대화 세션이 아니라 PhotosMcp가 소유해야 한다.
- 정상적인 날의 분석은 LLM agent보다 결정론적 worker가 적합하고, Hermes는 scheduler와 전달 계층으로 남기는 것이 장기적으로 안정적이다.

## 3. 현재 구현에서 재사용할 기능

### 3.1 공개 MCP 도구

PhotosMcp는 새 core tool을 추가할 필요 없이 다음 네 도구를 이미 제공한다.

| 도구 | 재사용 범위 |
|---|---|
| `photos_query` | 상태, 가이드, 사진 목록, prefetch, 실행 결과·artifact 조회 |
| `photos_select` | 단일 분석, 범위 분류, 우수 사진 선별 |
| `photos_write` | 승인된 Apple 앨범 추가, 로컬 export, category 정리 |
| `photos_workflow` | 선별 후 앨범·디렉터리 계획, 재개 |

현재 공식 action 계약은 [`application/action_options.py`](../../../src/photos_mcp/application/action_options.py)에 있고, 사용자용 목록은 [MCP 도구 참조](../../../docs/03-integration/02-tool-reference.md)에 문서화되어 있다.

Apple Photos 일일 선별에 바로 재사용할 수 있는 흐름은 다음이다.

```text
photos_select(action="select_best", source="apple", ...)
  -> accepted run_id
  -> photos_query(action="result_summary", run_id=<명시적 run_id>)
  -> PhotosMcp.app 또는 Telegram에서 검토
  -> photos_write(action="add_selected_to_album", run_id=<같은 run_id>)
  -> mutation plan
  -> 사용자 승인 뒤 동일 options + approval_token
  -> mutation receipt
```

자동화에서는 `run_id="latest"`를 사용하지 않는다. 여러 작업이 겹치거나 사용자가 수동 분류를 실행해도 잘못된 결과를 가져오지 않도록 시작 응답의 `run_id`를 끝까지 보존한다.

### 3.2 Apple Photos source와 앨범 쓰기

현재 Apple Photos source는 다음을 지원한다.

- 앨범·인물·촬영 날짜 범위 조회
- 사진 UUID와 메타데이터·thumbnail 조회
- iCloud-only 원본의 로컬 준비 요청
- 비디오 제외
- Apple Photos 앨범 목록 조회
- 기존 자산을 새 또는 기존 앨범에 추가

읽기 구현은 [`vendor/photo-source/sources/apple_photos.py`](../../../src/photos_mcp/vendor/photo-source/sources/apple_photos.py), 앨범 쓰기는 [`vendor/photo-ranker/album_writer.py`](../../../src/photos_mcp/vendor/photo-ranker/album_writer.py)에 있다.

Apple Photos에서 기존 사진을 앨범에 추가하는 동작은 원본 파일을 복제하거나 다른 앨범에서 제거하지 않는 비파괴적 멤버십 추가다. 이것이 초기 자동화의 유일한 허용 mutation이어야 한다.

### 3.3 현재 사진 품질·장면 분석

현재 pipeline은 이미 다음 신호를 사용한다.

- EXIF와 촬영 시각
- 흔들림·초점, 노출, 노이즈, 해상도, 색상 다양성
- LAION 계열 aesthetic score
- pHash 기반 유사·중복 탐지
- Apple Vision FeaturePrint 기반 시각 거리
- burst group
- 얼굴 수와 알려진 인물 신호
- VLM 장면 설명과 이벤트 유형
- 장면 군집과 장면별 대표 사진

현재 `general` profile의 기본 종합 가중치는 다음과 같다.

```text
품질 35% + 가족성 15% + 이벤트 의미 20% + 고유성 30%
```

선별 profile은 `general`, `person`, `landscape` 세 가지다. 초기 일일 자동화는 `general`을 사용하고, 자동으로 profile을 바꾸지 않는다. 사용자가 특정 인물이나 여행 앨범을 명시한 작업에서만 `person` 또는 `landscape`를 선택한다.

기존 사람 검토 결과는 다음과 같다.

| 지표 | 현행 결과 |
|---|---:|
| 검토 완료 장면 | 116 |
| 자동 Top-1이 사람 1순위와 일치 | 68.97% |
| 사람 1순위가 자동 Top-2 안에 포함 | 93.10% |
| MRR | 0.8302 |

근거는 [추천 품질 사람 기준선 및 shadow 점수 검증](../../../docs/08-reports/01-validation/09-recommendation-shadow-score-2026-08-10.md)이다. 이 결과는 “한 장을 자동 확정”하기보다 “장면별로 가장 유력한 한두 장을 검토 후보로 제시”하는 제품이 현재 정확도와 더 잘 맞는다는 뜻이다.

### 3.4 영속 작업, 승인, 재개

현재 PhotosMcp에는 다음 상태가 이미 있다.

- `workflow_runs`, `run_events`
- `mutation_plans`, `mutation_receipts`
- `photo_assets`
- photo-ranker `jobs`, `photo_results`, `job_assets`, `stage_checkpoints`
- Google `picker_sessions`, import lease, upload receipt

쓰기 승인은 [`application/mutation_approval.py`](../../../src/photos_mcp/application/mutation_approval.py)가 다음을 강제한다.

- 첫 호출은 실제 변경 없이 mutation plan만 생성
- 기본 900초 유효 1회성 approval token
- 도구·action·options·확정 자산 목록의 fingerprint 검증
- 대상이나 목적지가 바뀌면 승인 무효
- 실행 전후 receipt
- 부분 성공·결과 불확실 상태의 재조정

이 계약은 제거하지 않는다. 자동화를 위한 standing approval도 매일의 실제 하위 plan이 사전 승인 정책의 부분집합인지 검사한 뒤 짧은 child token을 발급하는 방식으로 확장해야 한다.

### 3.5 Hermes 기반

현재 운영 Hermes에는 다음 기반이 이미 있다.

- `photos-mcp-read`: `photos_query`, `photos_select`만 노출
- `photos-mcp`: query, select, write, workflow 노출
- 자동 선택 가능한 `photos-read` capability profile
- 자동 선택할 수 없는 privileged `photos-write` profile
- private Telegram DM의 명시적 `/route photos-write` 전환
- 안정적으로 동작 중인 cron ticker와 Telegram delivery
- 작업별 model, provider, continuity, `enabled_toolsets` 저장 계약

따라서 첫 단계에는 Hermes core 수정이 필요 없다. 다만 현재 `hermes cron create` CLI는 `enabled_toolsets`를 직접 받는 플래그를 노출하지 않으므로, 초기 photo cron은 Dashboard API 또는 Hermes의 `cronjob` 경로로 생성해야 한다. CLI 플래그 추가는 편의 개선이며 필수 조건이 아니다.

## 4. 현재 구현의 핵심 결손

### 4.1 촬영 날짜와 “새로 들어온 날짜”가 다르다

현재 Apple 목록 필터는 촬영일인 `photo.date`를 사용한다. 하지만 일일 자동화의 “새 사진”은 다음 상황을 포함해야 한다.

- 며칠 전에 촬영했지만 iCloud 동기화가 오늘 끝난 사진
- 과거 사진을 오늘 Apple Photos로 import한 경우
- 휴대폰 시간대나 여행지 시간대가 다른 사진
- 앱 또는 Photos DB가 일시적으로 준비되지 않아 전날 놓친 사진

설치된 `osxphotos`의 `PhotoInfo`에는 `date_added`가 있지만 현재 PhotosMcp 공개 source model과 필터에는 노출되지 않는다. 따라서 `date_from/date_to`만으로 전일을 조회하는 구현은 늦게 들어온 과거 사진을 영구 누락시킬 수 있다.

해결책은 `date_added`를 provider metadata로 올리고 다음 두 축을 분리하는 것이다.

```text
discovery time = 라이브러리에 추가된 시각 또는 PhotosMcp first_seen_at
capture time   = 앨범 정렬·이벤트 군집·표시용 촬영 시각
```

### 4.2 증분 처리 ledger가 없다

현재 `photo_assets` 테이블은 준비 상태 cache 성격이며, 일일 자동화를 위한 다음 정보를 보장하지 않는다.

- 어떤 provider 자산을 언제 처음 봤는지
- 어떤 policy/model version으로 분석했는지
- 지연 원본 때문에 deferred 되었는지
- 어떤 일일 실행에서 최종 선택됐는지
- 어떤 destination에 쓰였고 receipt가 무엇인지
- 재실행 시 다시 분석해야 하는지 건너뛰어야 하는지

따라서 별도 automation ledger가 필요하다.

### 4.3 Apple pagination과 안정 정렬이 없다

Apple source는 필터 뒤 `limit`로 자르며 이어받기 cursor가 없다. 많은 사진을 한꺼번에 import한 날에는 일부가 누락될 수 있다. 자동화용 discovery는 `(date_added, uuid)` 안정 정렬과 cursor pagination을 가져야 한다.

### 4.4 현재 approval token은 야간 알림에 적합하지 않다

900초짜리 token을 새벽에 만들고 아침에 Telegram에서 승인하도록 하면 만료된다. 야간에는 분석과 결과 저장까지만 하고, 사용자가 실제로 검토하는 시점에 fresh mutation plan을 생성해야 한다.

### 4.5 Google Picker와 Google upload는 MCP 공개 action이 아니다

Google OAuth, Picker, temporary download, 결과 업로드 구현은 현재 AppKit 흐름에 연결되어 있다. 공개 MCP 네 도구에는 Picker session 생성·poll·취소·materialize·Google upload plan action이 없다. Hermes가 현재 네 도구만으로 Google 흐름 전체를 구동할 수 없으므로, Google phase에서 기존 도구에 action을 추가해야 한다. 새 MCP tool을 추가할 필요는 없다.

### 4.6 Google 업로드는 “기존 사진 정리”가 아니라 새 사본 업로드다

Picker로 고른 기존 Google Photos 자산을 기존 ID 그대로 다른 Google 앨범에 넣을 수 없다. 현재 구현은 선택한 bytes를 다시 업로드하고 PhotosMcp가 만든 새 album에 새 media item으로 저장한다.

그 결과 다음 위험이 있다.

- Google 저장공간 추가 사용
- 같은 사진의 눈에 보이는 중복 사본
- 새 job으로 재시도할 때 별도 앨범·사본 중복
- Google -> Apple -> Google 순환 import

따라서 Google 결과의 기본 목적지는 Google 재업로드가 아니라 **PhotosMcp local review queue**로 두고, Google app-created album 업로드는 명시적 선택 기능으로 남긴다.

## 5. 공식 플랫폼 제약

### 5.1 Google Photos

Google은 2025-03-31부터 `photoslibrary.readonly`, `photoslibrary.sharing`, `photoslibrary` scope를 제거하고, Library API의 목록·검색·조회 범위를 앱이 만든 콘텐츠로 제한했다. 사용자의 기존 보관함에서 사진을 고르는 기능은 Picker API를 사용해야 한다.

또한 Google Photos API 정책은 Photos API 데이터를 이용한 얼굴 군집 생성을 금지한다. 현재 PhotosMcp의 Google source에서 `face_quality=False`, `face_clustering=False`를 강제하는 구현은 유지해야 하며 사용자가 설정으로 해제할 수 있게 만들지 않는다.

공식 근거:

- [Google Photos API 변경 사항](https://developers.google.com/photos/support/updates)
- [Google Photos Picker session lifecycle](https://developers.google.com/photos/picker/guides/sessions)
- [Google Photos authorization scope](https://developers.google.com/photos/overview/authorization)
- [Google Photos API User Data and Developer Policy](https://developers.google.com/photos/support/api-policy)

### 5.2 Apple Photos

Apple PhotoKit은 사용자 승인을 받은 앱이 사진 자산을 조회하고 user-created album을 만들거나 자산을 앨범에 추가하는 기능을 제공한다. 현재 PhotosMcp는 macOS Photos DB의 읽기 경로와 Photos automation/PhotoKit 쓰기 경로를 함께 사용한다.

공식 근거:

- [Apple PhotoKit Fetching Assets](https://developer.apple.com/documentation/photokit/fetching-assets)
- [Browsing and Modifying Photo Albums](https://developer.apple.com/documentation/photokit/browsing-and-modifying-photo-albums)
- [PHAssetCollectionChangeRequest](https://developer.apple.com/documentation/photos/phassetcollectionchangerequest)
- [VNDetectFaceCaptureQualityRequest](https://developer.apple.com/documentation/vision/vndetectfacecapturequalityrequest)

PhotoKit capability가 있다는 사실과 무인 변경이 안전하다는 판단은 별개다. PhotosMcp의 기존 mutation approval과 managed destination 정책을 그대로 적용한다.

## 6. 목표 아키텍처

```text
Hermes cron
  |
  | schedule / delivery / incident
  v
PhotosMcp daily automation API
  |
  +-- Discovery policy
  |     +-- Apple date_added + UUID cursor
  |     +-- Google Picker-selected session only
  |     +-- Local synced inbox (optional)
  |
  +-- Automation ledger
  |     +-- first_seen / processed / deferred
  |     +-- policy_version / scoring_version
  |     +-- destination receipt / reconciliation
  |
  +-- Two-stage curation
  |     +-- local metadata / screenshot / hash / technical quality
  |     +-- scene shortlist
  |     +-- Linux VLM acquire once, batch describe
  |     +-- scene Top-1 / diverse Top-2
  |
  +-- Review and policy gate
  |     +-- shadow: no write
  |     +-- manual approval: existing mutation contract
  |     +-- standing policy: add-only subset validation
  |
  +-- Destinations
        +-- Apple managed review/best album
        +-- local review queue
        +-- Google app-created album (explicit opt-in only)
```

### 6.1 상태 머신

```text
DISCOVERED
  -> CONTENT_DEFERRED ---------> READY
  -> READY
  -> LOCAL_SCORED
  -> SCENE_CLUSTERED
  -> VLM_SCORED 또는 VLM_DEFERRED
  -> SELECTED
  -> SHADOW_COMPLETE
  -> AWAITING_REVIEW
  -> APPROVED
  -> WRITE_PLANNED
  -> WRITING
  -> WRITTEN
  -> RECONCILED
```

실패를 모두 `failed` 하나로 합치지 않는다.

- `CONTENT_DEFERRED`: iCloud 원본 미준비. 다음 실행에서 동일 자산을 재시도한다.
- `VLM_DEFERRED`: Linux off, 준비 timeout, model error. 로컬 결과는 보존한다.
- `AWAITING_REVIEW`: 정상 상태다. incident로 올리지 않는다.
- `RECONCILING`: 쓰기 성공 여부가 불확실하므로 새 run을 만들지 않는다.
- `FAILED_PERMANENT`: 지원하지 않는 파일, 손상 원본, 정책 위반처럼 자동 재시도하지 않는다.

### 6.2 권장 저장 모델

```text
photo_automation_runs
- automation_run_id              PRIMARY KEY
- policy_id
- policy_version
- provider
- discovery_window_start
- discovery_window_end
- status
- discovered_count
- ready_count
- deferred_count
- analyzed_count
- selected_count
- written_count
- source_job_id
- hermes_execution_id            opaque, optional
- created_at
- completed_at

processed_photo_assets
- provider
- source_id
- provider_asset_id
- content_fingerprint
- capture_time
- library_added_time
- first_seen_at
- last_seen_at
- processing_state
- scoring_version
- last_automation_run_id
- last_source_job_id
- decision
- destination_receipt_id
- provenance_chain
UNIQUE(provider, source_id, provider_asset_id)

daily_automation_policies
- policy_id                      PRIMARY KEY
- version
- enabled
- source_scope
- destination_scope
- limits
- allowed_mutations
- forbidden_mutations
- expires_at
- created_by_user_at
```

자산 key는 provider가 안정된 ID를 제공하면 `(provider, source_id, provider_asset_id)`를 우선한다. cross-provider 중복 판단은 별도로 `content SHA-256 + pHash + 촬영 시각 허용 오차 + 해상도 + EXIF 카메라`를 사용한다. pHash만으로는 연사 사진을 중복으로 오판할 수 있으므로 “동일 사본”과 “비슷한 장면”을 분리한다.

### 6.3 신규 사진 discovery

Apple의 기본 처리 범위는 다음처럼 구성한다.

```text
후보 = date_added가 마지막 성공 cursor 이후인 자산
     + 최근 72시간 overlap에서 새로 발견된 UUID
     + 이전 CONTENT_DEFERRED 자산
     - 완료 ledger에 같은 scoring_version으로 기록된 자산
```

촬영 시각은 discovery cursor로 사용하지 않는다. 촬영 시각은 다음에만 사용한다.

- 이벤트와 장면 군집
- 앨범 안의 시간순 표시
- 날짜·여행지 기반 제목 후보
- 자정 경계를 넘는 이벤트 결합

### 6.4 2단계 모델 사용과 Linux 기동

현재 PhotosMcp VLM은 on-demand Linux Qwen3.8 경로이며 broker가 필요 시 워크스테이션을 깨운다. 일일 자동화는 이 경계를 그대로 사용하되 다음 정책을 추가한다.

1. 신규 자산이 0장이면 Linux를 깨우지 않는다.
2. 스크린샷·지원하지 않는 media·정확 중복은 Mac에서 먼저 제외한다.
3. 기술 품질과 FeaturePrint로 장면 후보를 줄인다.
4. VLM 후보가 하나 이상일 때만 runtime을 한 번 acquire한다.
5. 같은 daily run에서 후보를 batch 처리하고 매 사진마다 WOL을 재실행하지 않는다.
6. prepare timeout이면 로컬 분석 결과를 보존하고 `VLM_DEFERRED`로 다음 실행에 넘긴다.
7. 분석 완료 후 activity를 기록하고 Linux의 기존 idle power-off 정책에 맡긴다.

outer Hermes agent는 `mac-general`로 고정한다. 사진 내용을 보는 모델은 PhotosMcp broker가 선택하므로 Hermes model router가 같은 작업 때문에 Linux를 중복 준비하지 않게 한다.

## 7. “잘 나온 사진” 의사결정 정책

### 7.1 절대 점수보다 장면 안의 상대 선택

야간 가족사진, 풍경, 음식, 흑백 사진을 하나의 절대 점수로 비교하면 의미 있는 장면이 사라질 수 있다. 다음 순서를 사용한다.

1. screenshot, document, QR, meme, RAW/JPEG pair, video/Live Photo를 별도 유형으로 분리한다.
2. 정확 중복과 파생 사본을 분리한다.
3. burst, 촬영 시각, FeaturePrint, 인물 신호로 같은 장면을 묶는다.
4. 장면 안에서 현재 `total_score` 기준 Top-1을 고른다.
5. Top-2가 Top-1과 충분히 다르고 점수 차가 허용 범위 안이면 함께 제시한다.
6. 이벤트 전체에서 인물·풍경·디테일 다양성을 보정한다.

### 7.2 결함 veto

흐림이나 눈 감김은 단순 가산·감점보다 다음 조건을 모두 만족할 때만 보수적으로 제외한다.

```text
명백한 결함 신뢰도 >= 0.90
AND 같은 장면에 더 나은 대안 존재
AND 그 사진이 유일한 의미 있는 장면이 아님
```

의도적 motion blur, 실루엣, 야간·공연 사진, 흑백·저채도 사진은 기술 점수만으로 제외하지 않는다.

### 7.3 자동 확정 수준

| 상태 | 처리 |
|---|---|
| 장면 군집 신뢰 높음, Top-1과 Top-2 차 큼 | Top-1을 강한 후보로 표시 |
| Top-1과 Top-2가 비슷함 | 서로 다른 Top-2를 검토 후보로 표시 |
| 단독 사진 | 의미·최소 품질 통과 시 검토 후보로 보존 |
| 명백한 대체 가능 결함 | 추천에서는 제외하되 원본과 분석 결과는 보존 |
| 장면 경계 불명확 | `검토 필요` |
| favorite 또는 사용자가 고른 사진 | 자동 제외 금지 |

### 7.4 개인화

기존 35개 가중치 조합은 현행 순위를 개선하지 못했다. 처음부터 개인화 점수를 운영에 넣지 않는다. 사용자의 실제 행동을 로컬 shadow feature로만 축적한다.

- Top-1 승인
- Top-2로 교체
- 둘 다 유지
- 장면 전체 제외
- 이벤트 재분류
- 앨범 이름 변경
- 야간·흑백·의도적 흐림 승인

개인화 승격 최소 조건은 다음으로 둔다.

- 실제 사용자 판단 300건 이상
- 독립 holdout 100장면 이상
- 기존 대비 Top-1 +5%p 이상
- Top-2 포함률 감소 1%p 이내
- 이벤트 누락률 증가 없음
- 얼굴 수, 야간, 실내·실외 같은 subgroup 격차 악화 없음
- 운영 점수 보정폭 최대 ±5

Google Photos 입력은 얼굴 개인화와 범용 모델 학습에서 영구 제외한다.

## 8. 앨범 전략

매일 날짜 앨범을 하나씩 만들면 연간 365개가 쌓인다. 초기 기본값은 **월별 검토 앨범 + 확정 이벤트 앨범**으로 둔다.

```text
Photos MCP/
  2026/
    2026-09 검토
    2026-09 베스트
    2026-09-03 가족 식사       # 사용자가 확정하거나 충분히 안정된 이벤트
    2026-09-12 한강 산책
```

정책은 다음과 같다.

- 일일 digest는 촬영 날짜별로 보여 준다.
- 초기 자동 쓰기 대상은 `YYYY-MM 검토` 한 곳뿐이다.
- `YYYY-MM 베스트`는 사용자가 승인한 사진만 받는다.
- 이벤트 앨범은 장면 수가 충분하고 사용자가 제목을 확인한 경우에만 만든다.
- VLM caption이나 사진 파일명을 그대로 앨범 이름으로 쓰지 않는다.
- 앨범 이름은 고정 template과 안전한 문자 규칙으로 정규화한다.
- 같은 이름 앨범이 여러 개면 이름만으로 쓰거나 삭제하지 않고 UUID를 요구한다.
- `cleanup_album`, album delete, asset delete는 자동화에서 금지한다.

사용자가 날짜별 앨범을 선호하면 `Photos MCP/2026/2026-09-03 베스트` template을 선택 옵션으로 제공하되 기본값으로 두지 않는다.

## 9. Google Photos 운영안

### 9.1 지원하는 자동화

Google의 기본 지원 모드는 다음 흐름으로 자동화한다.

1. Hermes가 “Google Photos 최근 사진을 정리할까요?” 알림 또는 Picker 시작 링크를 제공한다.
2. 사용자가 Google Photos 화면에서 항목을 직접 고르고 완료한다.
3. PhotosMcp가 session을 poll하고 선택 자산을 임시로 materialize한다.
4. 얼굴 분석 없이 품질·중복·장면 분석을 실행한다.
5. 결과를 local review queue 또는 PhotosMcp.app에 표시한다.
6. 사용자가 선택하면 Apple Photos로 import하거나 Google app-created album에 새 사본으로 업로드한다.

### 9.2 기본 목적지

기본 목적지는 Google 재업로드가 아니다.

| 목적지 | 기본 여부 | 이유 |
|---|---|---|
| PhotosMcp local review queue | 기본 | 추가 cloud 사본·저장공간 사용 없음 |
| Apple Photos managed album | 선택 | Apple을 기준 라이브러리로 쓰는 경우 명시적 cross-import |
| Google app-created album | 선택·개별 승인 | 새 사본과 저장공간 사용, job 간 중복 방지 필요 |

### 9.3 대체 자동 입력 경로

Android 사진까지 완전 자동으로 받고 싶다면 Google Photos API를 우회해 무인 열람하려 하지 말고, 휴대폰에서 Mac/NAS의 관리 폴더로 직접 동기화하는 **local inbox**를 별도 source로 둔다. 이 경로는 Google Photos 보관함을 읽는 기능이 아니라 촬영 장치가 PhotosMcp가 관리하는 폴더에 새 원본을 전달하는 구조다.

```text
Android/iPhone managed upload folder
  -> PhotosMcp local source
  -> provider-neutral processed ledger
  -> Apple과 같은 품질·장면 pipeline
```

Google Takeout은 상시 동기화가 아니라 최초 이관이나 사용자가 시작한 대량 import에만 사용한다.

### 9.4 로컬 Chrome·Chrome DevTools MCP 보조안

2026-09-03 실제 검증 결과, PhotosMcp 전용 영구 Chrome 프로필을 일반 Chrome 프로세스로 먼저 실행하고 공식 Chrome DevTools MCP 1.8.0이 loopback `--browser-url`로 연결해 Google Photos와 Picker 화면을 제어할 수 있다. 개인용 기본 Chrome의 `--auto-connect`는 연결마다 승인이 필요하고 MCP가 WebDriver로 직접 실행한 Chrome은 Google 로그인이 차단될 수 있으므로 둘 다 운영 경로에서 제외한다. 사용자가 요청한 운영 경로는 Playwright가 아니라 Chrome DevTools MCP로 고정한다. 다만 다음 두 권한은 구분해야 한다.

| 권한 | 지속 가능성 | 의미 |
|---|---|---|
| Google OAuth 연결 | refresh token이 유효한 동안 재사용 가능 | PhotosMcp가 Picker session을 만들고 선택 결과를 읽을 수 있음 |
| Picker의 사진 선택 | session마다 새로 확정 | 해당 session에서 선택 완료된 항목만 앱에 전달됨 |

따라서 최초 OAuth를 한 번 승인했다고 해서 Google Photos 전체 보관함의 현재·미래 사진을 계속 읽을 수 있는 권한이 생기지는 않는다. Picker는 매번 새 `pickerUri`와 만료 시각을 가진 session을 만들고, `mediaItemsSet=true`가 된 뒤에만 선택된 항목을 나열한다. [Picker session lifecycle](https://developers.google.com/photos/picker/guides/sessions)

브라우저 보조 방식은 다음처럼 나눈다.

| 방식 | 기술 가능성 | 안정성·정책성 | 채택 |
|---|---:|---:|---|
| 사용자가 Picker에서 직접 선택·완료 | 높음 | 공식 흐름 | 기본 지원 |
| visible browser가 최근 10일의 개별 사진을 선택하고 검증 뒤 완료 | 중간 | 전용 profile·100장 상한·fail-closed 조건부 자동화 | **현재 운영 기본값** |
| 검증 없이 standing consent만으로 Picker를 무인 클릭 | 중간 | UI 변화·재인증·잘못된 선택 위험 | 제외 |
| `photos.google.com` 일반 보관함 UI를 scraping하고 browser download | 낮음 | private UI·lazy loading·압축 archive·DOM 변화·중복 추적 문제 | 제외 |

권장 보조 흐름은 다음이다.

```text
PhotosMcp가 권한 0700의 전용 영구 프로필로 일반 Chrome을 loopback debug port에 실행
  -> MCP 연결 전에 최초 OAuth와 전용 프로필 Google 로그인을 사용자가 직접 완료
  -> Chrome DevTools MCP가 --browser-url로 실행 중인 전용 Chrome에 연결
  -> Hermes가 일일 Picker session 생성
  -> Chrome DevTools MCP가 전용 Chrome에서 안전한 Google Photos 탭을 bootstrap
  -> allowlist로 검증한 Google pickerUri를 전용 Chrome에서 엶
  -> Chrome DevTools MCP가 실행일 포함 최근 10일의 개별 사진을 최대 100장 선택
  -> 날짜·개수·개별 checkbox·고유 완료 버튼을 재검증하고 "완료" 자동 클릭
  -> 로그인·MFA·CAPTCHA·UI 불일치에서는 멈추고 Telegram으로 사용자 조치 요청
  -> Picker REST polling에서 mediaItemsSet=true 확인
  -> PhotosMcp가 mediaItems.list와 baseUrl로 직접 다운로드
  -> 기존 분석·ledger·임시 파일 정리 실행
```

브라우저는 다운로드 엔진으로 쓰지 않는다. 선택 완료 뒤에는 현재 [`GooglePhotosPickerAdapter`](../../../src/photos_mcp/infrastructure/sources/google_photos/picker.py)가 pagination과 Bearer token을 사용해 선택 결과를 가져오므로, browser의 다운로드 버튼을 누르거나 다운로드 폴더를 감시할 이유가 없다. Picker `baseUrl`은 60분 동안만 유효하며 권한이 철회되면 더 빨리 만료될 수 있다. [Picker 선택 항목 조회와 다운로드](https://developers.google.com/photos/picker/guides/media-items)

MCP client는 연결된 profile의 열린 창·쿠키·계정 상태에 접근할 수 있으므로 개인용 기본 Chrome과 연결하지 않는다. 자동화는 `~/.photos-mcp/chrome/google-picker-profile`만 사용하고 별도 LLM turn 없이 deterministic MCP client를 호출한다. 최초 로그인 상태는 전용 프로필 안에만 유지한다. Chrome 공식 문서도 기존 세션 연결 시 로그인 계정과 쿠키를 상속한다는 점을 경고한다. [Chrome DevTools MCP configuration](https://developer.chrome.com/docs/devtools/agents/get-started/configuration)

현재 구현에는 다음 제한을 적용한다.

- headed·visible browser만 사용하고 headless 모드는 사용하지 않는다.
- 일반 Chrome은 `--remote-debugging-address=127.0.0.1 --remote-debugging-port=9333 --user-data-dir=~/.photos-mcp/chrome/google-picker-profile`로 실행한다.
- `chrome-devtools-mcp@1.8.0`을 pin하고 `--browser-url=http://127.0.0.1:9333 --no-page-id-routing`으로 연결한다. 날짜 tree와 실제 입력에 필요한 `take_snapshot`, `click`, `navigate_page`를 사용하므로 slim mode는 사용하지 않는다.
- 전용 프로필 디렉터리는 생성·사용 때마다 `0700`을 강제하고 개인용 기본 Chrome 프로필을 복사하거나 재사용하지 않는다.
- `photos.google.com`, `accounts.google.com`과 Picker에 필요한 Google API·정적 host만 허용한다.
- 네트워크 header redaction을 켜고 usage statistics·CrUX 조회를 끈다.
- 최초 Google 로그인, 2단계 인증, OAuth scope 동의는 자동 클릭하지 않는다.
- 정확한 `pickerUri` host·session binding을 확인하고 일반 Google Photos URL 탐색은 bootstrap 이외에는 금지한다.
- Picker 접근성 tree의 날짜 heading을 실행일 기준 날짜로 해석하고 최근 10일 범위의 항목만 선택한다.
- 개별 사진 checkbox 뒤의 `description` 보유 preview button 구조를 확인해 날짜 그룹 복수 선택 checkbox를 제외한다.
- 최근 10일에 보이는 개별 사진을 Picker session 상한인 최대 100장까지 선택하고 이미 선택된 항목은 건너뛴다. 매 실제 `click(uid)` 뒤 선택 수가 정확히 1 증가하지 않으면 중단한다.
- 선택 수와 날짜 범위를 재검증한 뒤 유일하게 활성화된 최종 완료 button만 실제 `click(uid)`로 누른다.
- 사진 grid는 최대 20초만 기다리며 후보가 없거나 DOM 계약이 바뀌면 세션을 취소한다.
- CAPTCHA, 계정 선택, 재인증, 추가 동의 화면이 나오면 사용자에게 넘긴다.
- screenshot, trace, HAR, DOM snapshot은 기본적으로 저장하지 않으며 진단 시 사용자가 별도로 허용한다.
- browser cookie와 Picker URI를 Git, 일반 로그, Hermes prompt에 넣지 않는다.
- 중단·연결 실패·timeout에서는 미완료 Picker session을 취소한다.
- 선택 수, 촬영일 범위, 하루 실행 횟수의 budget과 kill switch를 둔다.

최종 선택 버튼 자동 클릭은 production cron에 포함하되, 위 fail-closed 검증이 하나라도 실패하면 클릭하지 않고 사용자 조치 상태로 전환한다. 이후 운영 관찰에서 다음을 확인한다.

- 같은 날짜를 다시 선택하지 않는 provider asset ledger가 동작하는지
- Google 계정·Picker UI·session 만료 변화가 있을 때 fail closed 하는지
- 브라우저 선택 결과와 REST API가 반환한 자산 수·ID가 일치하는지
- Google Photos 정책의 affirmative consent와 limited-content 원칙에 부합한다고 운영자가 판단할 수 있는지

2026-09-03 구현에서는 Picker가 반환한 `(source_id, provider_asset_id)`를 `processed_photo_assets`에 저장한다. 다음 실행에서 같은 최근 10일 창이 선택되더라도 `submitted/completed` 자산은 공식 콘텐츠 다운로드 전에 제외하며, 신규 자산이 하나도 없으면 분석 job을 만들지 않고 `completed/no_new_photos`로 끝낸다. 날짜가 아니라 provider 자산 ID를 기준으로 하므로 날짜 경계가 겹쳐도 동일 항목 재분석을 막을 수 있다.

Google Photos 정책은 제한된 콘텐츠·제한된 기간의 사용 사례에서 Picker를 사용하고, 데이터 접근 목적을 명확히 알린 뒤 affirmative consent를 받도록 요구한다. Google 문서가 agent의 Picker 자동 클릭을 안정적인 UI 계약으로 보증하지는 않으므로, 전용 프로필에서 사용자가 이미 승인한 범위와 최근 10일 정책 안에서만 실행하고 예외 화면은 항상 사용자에게 넘긴다. [Google Photos API 데이터 정책](https://developers.google.com/photos/support/api-policy)

### 9.5 사용자 액션 요청과 메시지 알림

브라우저 자동화가 사람의 동의나 조작을 필요로 하면 실패로 끝내거나 무한 재시도하지 않고, PhotosMcp run을 `AWAITING_USER_ACTION`으로 영속화한 뒤 알림을 보낸다. PhotosMcp가 상태·만료·재개 조건을 소유하고 Hermes는 전달만 담당한다.

사용자 액션 사유는 자유 텍스트가 아니라 다음 enum으로 제한한다.

| reason | 필요한 사용자 행동 | 자동화 처리 |
|---|---|---|
| `OAUTH_CONSENT_REQUIRED` | Mac에서 Google scope 확인·동의 | browser 자동 클릭 금지, 완료 callback 대기 |
| `ACCOUNT_SELECTION_REQUIRED` | 연결한 Google 계정 선택 | 현재 run 정지, 계정 확인 뒤 재개 |
| `MFA_REQUIRED` | 2단계 인증 완료 | credential 입력·OTP 읽기 금지 |
| `PICKER_REVIEW_REQUIRED` | 자동 검증이 실패한 선택의 날짜·수량 확인 후 완료 | 자동 완료를 중단한 예외 상태 |
| `CAPTCHA_OR_CHALLENGE` | Google 보안 challenge 직접 해결 | 자동 우회 금지 |
| `BROWSER_UI_CHANGED` | 화면을 확인하고 수동 선택으로 전환 | selector 재시도 금지, diagnostic opt-in 제공 |
| `SESSION_EXPIRED` | 새 Picker session 시작 승인 | 기존 session 정리 후 새 session 생성 |

현재 Hermes에서 실제 연결·검증된 Telegram private DM을 1순위 채널로 사용한다.

| 채널 | 역할 | 판정 |
|---|---|---|
| Telegram private DM | 원격 action-required 알림, 재개·건너뛰기·취소 | **기본** |
| macOS local notification | Mac 앞에 있을 때 Chrome 확인 안내 | 보조 fallback |
| Tailscale-only PhotosMcp action center | 상태·만료·선택 수 확인과 안전한 action 수행 | 실제 제어 화면 |
| Open WebUI/Hermes Dashboard | 최근 상태와 장애 확인 | 조회용 |

Telegram 메시지에는 Picker URI, Google 계정, 사진 ID·파일명·thumbnail, OAuth·browser cookie, `baseUrl`, 로컬 경로를 넣지 않는다. 메시지는 다음처럼 집계와 opaque action request ID만 포함한다.

```text
Google Photos 작업에 확인이 필요합니다.
- 이유: 최근 사진 선택 확인
- 선택 예정: 43장
- 세션 만료까지: 약 18분
- 작업: gpa-7F2A

[안전하게 열기] [오늘 건너뛰기] [취소]
```

`안전하게 열기`는 raw `pickerUri`를 Telegram에 보내지 않고, Tailnet 안에서만 접근 가능한 PhotosMcp action center의 짧은 1회용 링크를 사용한다. action token은 다음에 바인딩한다.

- 정확한 `action_request_id`, `automation_run_id`, 허용 action
- Telegram의 허용된 principal hash
- 최대 5분 만료와 1회 사용
- 서버에는 token 원문이 아니라 hash만 저장
- `Referrer-Policy: no-referrer`, analytics·access log의 query redaction

action center에서 현재 상태를 다시 확인한 뒤 살아 있는 session이면 Picker를 연다. 이미 만료됐으면 사용자가 `새 세션 시작`을 누른 뒤에만 기존 상태를 정리하고 새 session을 만든다. 만료된 링크나 다른 run의 callback으로 자동화를 재개하지 않는다.

알림과 재시도 정책은 다음과 같다.

- 같은 `action_request_id + reason`은 한 번만 최초 알림한다.
- 사용자가 진행 중인 browser-assist run에서 차단되면 즉시 알린다.
- 일일 Google 작업을 새벽에 미리 만들어 만료시키지 않는다. 기본 알림 시간인 08:00 KST에 시작 여부를 묻고, 사용자가 누른 뒤 Picker session을 생성한다.
- 진행 중 session의 reminder는 Google `pollingConfig.timeoutIn`을 기준으로 최대 두 번만 보낸다.
- quiet hours에는 새 session을 만들지 않고 `PENDING_USER_START`로 유지한다.
- session이 만료되면 `SESSION_EXPIRED`로 한 번 알리고 자동 session 재생성 loop를 금지한다.
- Telegram delivery 실패 시 macOS notification과 Dashboard pending badge를 남긴다.
- 사용자가 `오늘 건너뛰기`를 누르면 다음 정기 실행까지 알리지 않는다.
- `취소`는 Picker session과 임시 cache를 정리하지만 OAuth 연결과 처리 완료 ledger는 삭제하지 않는다.

이 알림은 LLM이 문장을 자유롭게 생성하거나 tool을 선택하는 agent job으로 만들지 않는다. PhotosMcp가 구조화된 `UserActionRequiredEvent`를 만들고, Hermes의 no-agent delivery가 고정 template으로 보내야 prompt에 사진 정보가 섞이거나 같은 알림이 반복되는 것을 막을 수 있다.

## 10. standing approval 설계

14일 shadow가 통과하기 전에는 standing approval을 만들지 않는다. 이후에도 전역 “사진 쓰기 허용”이 아니라 다음처럼 범위를 고정한다.

```yaml
policy_id: apple-daily-curation-v1
provider: apple_photos
enabled: true
expires_after_days: 30

discovery:
  library_added_lookback_hours: 72
  max_candidates_per_run: 300

destination:
  operation: add_existing_assets_to_album
  managed_folder: "Photos MCP"
  album_name_pattern: "{year}-{month} 검토"
  pinned_folder_id: "<사용자가 확인한 opaque UUID>"

budgets:
  max_runs_per_day: 1
  max_new_albums_per_day: 1
  max_added_assets_per_day: 50

allowed_mutations:
  - create_album_under_managed_folder
  - add_existing_assets_to_album

forbidden_mutations:
  - delete_asset
  - delete_album
  - remove_from_album
  - cleanup_album
  - edit_original
  - import_external_file
  - export_original
  - write_google_photos
```

매일의 실제 mutation은 다음 검사를 통과해야 한다.

1. provider가 정확히 Apple Photos인지 확인한다.
2. 대상 folder UUID가 사전 승인 값과 같은지 확인한다.
3. 앨범 이름이 허용 template 안인지 확인한다.
4. 모든 자산이 discovery run에서 확정된 기존 Apple UUID인지 확인한다.
5. 현재 앨범 멤버십을 읽고 이미 들어간 UUID를 제거한다.
6. 수량·run/day budget을 검사한다.
7. exact 하위 plan에 짧은 1회용 child token을 발급한다.
8. 실행 전 receipt를 저장한다.
9. 실행 후 실제 멤버십과 receipt를 재조정한다.
10. 하나라도 벗어나면 기존 개별 승인 화면으로 fail closed 한다.

LLM이나 Hermes cron agent는 standing approval의 주체가 아니다. 승인 대상은 provider, operation, destination UUID, name pattern, 수량, 유효기간이다.

## 11. 자동화 대안 비교

| 대안 | 장점 | 단점 | 판정 |
|---|---|---|---|
| Hermes agent cron이 매일 MCP 호출 | 현재 기능으로 가장 빨리 shadow 시작, Telegram 연결 쉬움 | LLM 호출 변동성, hard daily idempotency 없음, 장기 polling 비용 | Phase 1 임시안 |
| Hermes no-agent script가 MCP automation action 호출 | 결정적, 도구 prompt 비용 없음, stdout digest 간단 | PhotosMcp automation API와 ledger가 먼저 필요 | **목표 운영안** |
| PhotosMcp 내부 scheduler | 데이터와 상태가 한곳, 가장 강한 복구 | 스케줄·알림·incident 기능을 Hermes와 중복 구현 | scheduler는 채택하지 않음 |
| PhotosMcp automation service + Hermes scheduler | 상태·정책은 PhotosMcp, 일정·알림은 Hermes | 두 서비스 계약 테스트 필요 | **최종 권장** |
| Google Photos 전체 자동 poll | 사용자는 편해 보임 | 공식 API로 불가, 정책 위반 위험 | 제외 |
| 휴대폰 -> local inbox 자동 동기화 | Google API 제약 없이 무인 처리, provider-neutral | 별도 동기화 운영과 저장공간 필요 | Google-only 사진의 선택 대안 |

따라서 전환 경로는 다음이다.

```text
초기: Hermes agent cron + photos-mcp-read
  -> PhotosMcp automation action/ledger 구현
  -> Hermes no-agent deterministic worker
```

## 12. 단계별 구현 계획

### Phase 0 — 기준선과 정책 고정

목표: 원본과 운영 설정을 바꾸지 않고 현재 성능과 scope를 확정한다.

- 현재 654개 전체 회귀를 기준선으로 고정한다.
- Apple test album 또는 5~20장 비민감 범위에서 read-only smoke를 수행한다.
- 현재 `general` ranking과 Top-2 정책을 변경하지 않는다.
- Google 얼굴 분석 차단을 invariant test로 고정한다.
- 앨범 naming, 최대 하루 수량, Telegram quiet hours를 사용자 설정으로 정의한다.

완료 조건:

- source·write·Google 정책 계약 테스트 통과
- Apple 권한과 PhotosMcp daemon health 정상
- 사진·경로·토큰이 테스트 로그와 문서에 남지 않음
- 원본·앨범 변경 0건

### Phase 1 — Apple 증분 discovery와 automation ledger

목표: 늦게 동기화된 사진까지 누락 없이, 같은 자산을 반복 분석하지 않는다.

- Apple asset model에 `library_added_time`을 추가한다.
- `(date_added, uuid)` 안정 정렬과 cursor pagination을 구현한다.
- `photo_automation_runs`, `processed_photo_assets`를 추가한다.
- 72시간 overlap과 deferred 재시도를 구현한다.
- 정책·scoring version을 ledger에 저장한다.
- 기존 `photos_query`에 automation status/preview action을 추가한다.
- 기존 `photos_workflow`에 read-only `daily_curate` action을 추가한다.

완료 조건:

- 동일 window 재실행에서 완료 자산 재분석 0건
- 늦게 나타난 과거 촬영 사진 누락 0건
- 한 번에 limit를 넘는 입력을 모두 page 처리
- 0장, 1장, 300장, 지원하지 않는 media의 결정적 결과
- 중단 후 같은 automation run을 재개하고 새 run을 만들지 않음

### Phase 2 — 14일 read-only shadow + Telegram digest

목표: 쓰기 없이 실제 일일 운영 품질과 비용을 측정한다.

- Hermes에서 `photos-mcp-read`만 허용한 cron을 만든다.
- outer model은 `mac-general`로 고정한다.
- 초기에는 agent cron이 `daily_curate`와 명시적 run ID polling을 수행한다.
- 신규 자산 0이면 silent 처리한다.
- Telegram에는 집계와 opaque run ID만 보낸다.
- PhotosMcp.app에서 장면별 Top-1/Top-2 검토를 연결한다.
- 2단계 local shortlist 뒤 필요할 때만 Linux VLM을 깨운다.

권장 시간:

- 분석: 04:30 KST
- 사용자 digest: quiet-hours 정책을 적용해 08:00 KST 이후
- 실패 알림: 민감정보 없이 즉시 또는 quiet-hours 종료 후 전달

분석과 알림을 한 cron으로 먼저 시작할 수 있지만, 안정화 후 분석 job과 digest job을 분리하면 새벽 알림을 피할 수 있다.

완료 조건:

- 14일 동안 schedule 중복 0건
- 확인 표본에서 신규·지연 자산 누락 0건
- 성공률 95% 이상
- 자동 write·delete·cleanup 0건
- Telegram token, local path, picker URI, photo ID 노출 0건
- 사람이 고른 1순위의 Top-2 포함률 93% 이상 유지
- 사용자의 일일 검토 시간 중앙값 3분 이하

### Phase 3 — Apple 승인 기반 월별 검토 앨범

목표: 사용자가 검토한 결과를 비파괴적으로 Apple Photos 앨범에 반영한다.

- 야간 run은 token을 만들지 않는다.
- 사용자가 검토하는 시점에 fresh mutation plan을 생성한다.
- 대상 album UUID와 추가 사진 수·썸네일을 표시한다.
- 기존 1회성 approval token으로 실행한다.
- 동일 exact 요청의 duplicate suppression과 receipt reconciliation을 검증한다.
- Telegram private DM의 `/route photos-write`와 PhotosMcp.app 승인 경로를 둘 다 검증한다.

완료 조건:

- token 없는 album write 0건
- 잘못되거나 만료된 token 거부
- 승인 대상 UUID와 실제 추가 UUID 100% 일치
- 같은 요청 재호출 시 중복 멤버십 0건
- timeout/partial 뒤 새 앨범·중복 추가 없이 재조정
- 원본 삭제·수정·이동 0건

### Phase 4 — 제한형 자동 album publish

목표: 충분히 안정된 범위만 사용자 사전 정책으로 자동 반영한다.

- 30일 만료 standing policy를 도입한다.
- add-only, managed folder UUID, 월별 album pattern, 일일 수량 budget을 강제한다.
- 정책을 통과한 exact child plan에만 1회용 token을 발급한다.
- policy version 변경 시 자동 승인을 중지하고 재승인을 요구한다.
- Hermes agent cron을 no-agent deterministic script로 전환한다.

완료 조건:

- policy 밖 mutation 100% 차단
- 하루 최대 실행·사진·앨범 budget 초과 100% 차단
- 같은 날짜와 policy version 재실행에서 추가 쓰기 0건
- 모든 write에 receipt와 post-write reconciliation 존재
- standing policy 만료 후 자동 write 0건

### Phase 5 — 이벤트 앨범과 개인 선호 shadow

목표: 월별 후보 목록을 날짜·장소·장면 기반 이벤트로 묶되 과도한 앨범 생성을 막는다.

- 72시간 범위에서 자정 경계 이벤트를 결합한다.
- 이벤트당 최소 한 장과 최대 장수 budget을 둔다.
- VLM title은 제안일 뿐이며 사용자가 확인한 안전 template만 쓴다.
- 사용자의 선택 행동을 비식별 local feature로 저장한다.
- 개인화는 shadow 평가와 holdout gate를 통과하기 전 운영 ranking에 반영하지 않는다.

완료 조건:

- 이벤트 재분류율 15% 미만
- 자정 경계 분할 오류 5% 미만
- 같은 구도·인물이 전체 결과를 독점하지 않음
- 개인화 승격 gate 미통과 시 현행 ranking byte-for-byte 유지

### Phase 6 — Google Picker MCP bridge

목표: 사용자 선택 경계를 유지하면서 Hermes에서 Google intake를 시작하고 추적한다.

새 tool을 추가하지 않고 기존 action surface를 확장한다.

- `photos_query`: Google connection status, Picker session status
- `photos_workflow`: Picker session start/cancel, selected content prepare, prepared selection classify
- `photos_write`: Google app-created album upload plan/execute

공개 MCP bridge가 안정된 뒤 선택적 `GooglePickerBrowserAssistant` spike를 추가한다.

- PhotosMcp가 발급한 현재 `pickerUri`만 전용 headed Chrome에서 연다.
- 최근 날짜 그룹의 preselection까지만 수행하고 기본 모드에서는 최종 완료를 사용자에게 맡긴다.
- browser가 아니라 Picker REST adapter가 선택 결과를 page 처리하고 다운로드한다.
- 브라우저 계정·쿠키·화면 artifact는 MCP payload와 Hermes session으로 전달하지 않는다.
- UI selector 불일치, 계정 선택, CAPTCHA, 재인증에서는 fail closed 한다.
- 완전 무인 완료는 별도 feature flag와 정책 검토를 통과하기 전에는 지원하지 않는다.

같은 phase에서 `UserActionRequiredEvent`와 알림·재개 경계를 추가한다.

- PhotosMcp run에 `AWAITING_USER_ACTION`, reason, action request ID, 만료 시각, notification 상태를 저장한다.
- Telegram private DM은 고정 template과 opaque ID만 전송한다.
- action은 Tailscale-only action center의 5분 1회용 token으로 처리한다.
- 기본 08:00 KST prompt에 사용자가 응답한 뒤 Picker session을 생성한다.
- 계정 선택·MFA·CAPTCHA·OAuth 동의·UI 변경은 즉시 정지하고 원격 알림한다.
- Telegram 장애 시 macOS notification과 Dashboard badge를 fallback으로 사용한다.

보안 invariant:

- Picker URI, access/refresh token, base URL, upload URL을 일반 run payload와 Telegram에 넣지 않는다.
- Google origin에서는 face quality, face clustering, face personalization을 항상 차단한다.
- Picker에서 사용자가 고르지 않은 자산을 다운로드하지 않는다.
- session 종료·만료 뒤 temporary URI·cache·lease를 정리한다.
- Google 재업로드는 개별 승인과 global content manifest가 준비된 뒤에만 허용한다.

완료 조건:

- 사용자가 Picker를 완료하지 않으면 다운로드 0건
- 선택한 자산 외 다운로드 0건
- 얼굴 embedding·cluster 생성 0건
- Picker 취소·만료·OAuth 철회·재연결 E2E 통과
- 네트워크 단절 후 partial upload 재개에서 새 album·사본 중복 0건
- Google app-created album이 아닌 기존 콘텐츠 mutation 0건
- browser-assist 모드에서 사용자가 확인한 날짜·선택 수와 API 반환 자산 수가 일치
- 기본 Chrome profile remote debugging 0건
- 로그인·2단계 인증·OAuth 동의 화면 자동 클릭 0건
- UI 변화나 계정 재확인이 발생한 run의 무인 진행 0건
- 같은 action-required event의 Telegram 중복 알림 0건
- quiet hours 중 만료될 Picker session 선생성 0건
- 만료·다른 run·다른 principal action token의 재개 0건
- Telegram 메시지의 Picker URI·Google 계정·사진 ID·파일명·thumbnail·token 노출 0건
- Telegram delivery 장애 뒤 pending 상태 유실 0건

### 후순위 — 정리·삭제 후보

삭제는 이번 roadmap의 구현 목표가 아니다. 필요하면 별도의 강한 승인 프로젝트로 다룬다.

- 자동 삭제 금지
- 삭제 후보만 별도 review 화면에 표시
- 최소 30일 quarantine
- 원본·편집본·RAW/JPEG 관계 표시
- 앨범 UUID와 자산 UUID를 사용한 exact plan
- 사용자가 Photos 앱에서 직접 삭제하는 경로 우선

## 13. 테스트 전략

### 13.1 단위·계약 테스트

- `date_added` 없는 구버전/특수 자산 fallback
- 같은 `date_added`의 UUID 안정 정렬
- cursor pagination과 page boundary
- KST 자정, 여행지 offset, DST가 있는 원본 timezone
- 0장, 1장, 최대 한도 초과
- iCloud-only 원본의 deferred와 다음 날 복구
- policy/scoring version 변경 후 선택적 재평가
- screenshot, document, video, RAW/JPEG pair 분리
- exact hash, pHash, burst, FeaturePrint의 중복·장면 구분
- Google origin의 모든 얼굴 기능 차단
- 파일명·EXIF·caption의 prompt injection 문자열이 destination이나 action으로 사용되지 않음
- album name normalization과 UUID pinning
- standing policy의 provider, operation, destination, budget, expiry fail-closed

### 13.2 통합 테스트

- Hermes execution ID와 PhotosMcp automation run ID 상호 연결
- accepted 이후 항상 같은 run ID만 poll
- Gateway 재시작, PhotosMcp 재시작, worker crash
- Linux off -> WOL -> model ready -> batch 분석
- Linux prepare timeout -> `VLM_DEFERRED` -> 다음 실행 재개
- 신규 0장일 때 Linux WOL 0회와 Telegram silent
- 같은 daily run을 동시에 두 번 claim했을 때 한쪽만 실행
- write 직전·중간·직후 crash와 receipt reconciliation
- 동일 이름 Apple 앨범이 둘 이상일 때 UUID 없이 차단
- Google import 후 Apple/Google 순환 provenance 차단

### 13.3 실환경 E2E

Apple은 비민감 test album 또는 작은 날짜 범위에서 다음을 확인한다.

1. PhotosMcp health와 Apple 권한
2. 신규 discovery와 run ID 생성
3. local prefilter와 on-demand Linux 준비
4. result summary와 PhotosMcp gallery
5. Telegram digest
6. mutation plan의 사진 수·목적지 확인
7. 사용자 승인 후 Apple 앨범 멤버십
8. 동일 요청 재실행의 중복 억제
9. Photos 앱에서 원본 수와 기존 앨범이 변하지 않았는지 확인

Google은 실제 계정에서 사용자가 OAuth와 Picker 선택을 직접 수행하는 작은 범위로 검증한다. 테스트 automation이 OAuth 허용 버튼이나 Picker 사진을 대신 클릭하지 않는다.

## 14. 관측성과 운영

공통 correlation key는 PhotosMcp `automation_run_id`와 `source_job_id`다.

### Hermes에서 볼 항목

- `hermes cron status`
- `hermes cron runs <job>`
- execution status: claimed, running, completed, failed, unknown
- `failure_streak`, incident, delivery error
- 실행 결과에 포함된 opaque PhotosMcp run ID

### PhotosMcp에서 볼 항목

- `/health`, `/health/capabilities`
- active/recent job count
- automation run state와 stage
- 신규, deferred, analyzed, selected, written count
- VLM provider, prepare 횟수, inference time
- pending plan과 receipt/reconciliation state

### 유지할 지표

| 범주 | 지표 |
|---|---|
| Discovery | 신규 수, late-arrival 수, deferred 수, 재발견 수 |
| 품질 | Top-1 채택률, Top-2 회수율, 장면 경계 수정률, 이벤트 누락률 |
| 성능 | total time, local stage, Linux prepare, VLM inference, photos/sec |
| 비용·전력 | Linux wake/run, 0장 run의 wake 수, VLM 호출 사진 수 |
| 안전 | 정책 거부, token 거부, duplicate suppression, reconciliation |
| 운영 | cron 성공률, retry, incident, Telegram delivery 성공률 |

Telegram에는 다음만 보낸다.

```text
9월 3일 사진 정리 결과
- 새로 발견: 74장
- 분석 완료: 68장
- 원본 준비 대기: 6장
- 추천 후보: 12장 / 검토 필요 장면: 4개
- 실행: <opaque run id>
```

Telegram, 일반 로그, Git 문서에는 다음을 넣지 않는다.

- 원본 절대 경로와 전체 파일명 목록
- Apple/Google photo ID
- GPS와 상세 EXIF
- 얼굴 crop, embedding, 인물 registry
- Google Picker URI, session URL, OAuth token, upload URL/token
- approval token
- raw image payload와 raw VLM prompt

권장 보존 기간은 다음과 같다.

| 데이터 | 권장 보존 |
|---|---:|
| Picker content URL | 메모리만 |
| Picker session/URI | 완료·만료 후 즉시 정리 |
| Google temporary original/sidecar | 작업 종료 후 24시간 이내 |
| resumable upload URL/token | 완료 즉시, 실패도 최대 24시간 |
| 상세 run·mutation receipt | 30일 |
| 비식별 일일 통계 | 장기 보존 가능 |
| 일반 로그 | 14일 |
| Apple face data | 명시적 동의, 별도 삭제 기능과 최소 보존 |

## 15. 현재 검증 기준선

이번 문서 작성 중 실제 운영과 코드를 읽기 전용으로 점검했다.

| 점검 | 결과 |
|---|---|
| PhotosMcp daemon | `ready` |
| Apple Photos permission/read | 정상 |
| Apple Photos automation/thumbnail | startup probe 지연 warning; 첫 실제 작업 전 명시 검사 필요 |
| PhotosMcp VLM | on-demand remote Linux, 현재 idle |
| Hermes cron | gateway/ticker 정상, 기존 뉴스 작업 4개 active |
| Hermes Photos MCP | read/write alias 등록 상태 확인 |
| 관련 집중 회귀 | 85 passed |
| 전체 PhotosMcp 회귀 | 654 passed |

이번 점검에서는 사진 원본, 앨범, Google OAuth, Picker session, Hermes cron 설정을 변경하지 않았다.

## 16. 최종 권고 순서

실제 다음 작업은 아래 순서가 가장 좋다.

1. **PhotosMcp에 Apple `date_added` discovery, pagination, automation ledger를 구현한다.**
2. **기존 ranking을 그대로 사용해 14일 read-only shadow를 실행한다.**
3. **Hermes `photos-mcp-read` 전용 cron과 quiet-hours Telegram digest를 연결한다.**
4. **사람 검토에서 Top-2 회수율, 누락, 시간, Linux 기동 비용을 측정한다.**
5. **사용자 승인 기반 월별 Apple 검토 앨범을 활성화한다.**
6. **필요할 때만 add-only standing approval을 도입하고 no-agent worker로 전환한다.**
7. **이벤트 앨범과 개인 선호는 충분한 표본 뒤 shadow로 추가한다.**
8. **Google은 Picker action을 MCP에 노출하되 사용자 선택을 유지한다.**
9. **Google 결과의 기본값은 local review로 두고 재업로드는 명시 승인으로 제한한다.**
10. **원본 삭제와 기존 앨범 cleanup은 별도 프로젝트로 남긴다.**

현재 코드와 운영 환경을 기준으로 바로 착수할 1차 구현 단위는 다음 네 항목이다.

- `ApplePhotoAsset.library_added_time`
- provider cursor와 `processed_photo_assets` ledger
- read-only `daily_curate` action
- Hermes `photos-mcp-read` shadow cron + Telegram digest

이 네 항목까지가 “자동 사진 정리”의 안전하고 검증 가능한 첫 vertical slice다.

## 17. 2026-09-03 구현·실환경 결과

위 1차 vertical slice는 다음 상태로 구현·배포됐다.

| 항목 | 실환경 결과 |
|---|---|
| Apple 증분 탐색 | `date_added` + `(date_added, UUID)` cursor와 처리 ledger 구현 |
| Apple 1차 live | 최근 24시간 신규 0장, `completed/no_op`, Linux VLM·write 미호출 |
| Google 사용자 조치 | Tailscale action page와 Hermes Telegram 전송 성공 |
| Google Picker | 사용자 수동 선택 19장, 공식 다운로드·준비 완료 |
| Google 분석 | job `8c51ca73`, 291.94초, 19장 결과, 중복 후보 2장, 추천 8장 |
| 완료 연결 | Picker job handoff 시 action·automation run `completed`, `analysis_run_id` 연결 |
| Telegram 신뢰성 | SQLite lease/audit 뒤 `hermes send`, 성공 ack 후에만 `notified`, 실패 retry |
| 정기 실행 | 매일 03:00 read-only trigger, 5분 간격 action notifier 활성화 |
| 회귀 | PhotosMcp `680 passed`, Hermes bridge `5 passed` |
| 설치본 | standalone 재빌드·서명·배포, health `ok/ready` |

실환경 검증 중 사진 원본과 Apple/Google 앨범은 변경하지 않았다. Google에서 내려받은 원본 사본은 결과 job과 연결된 관리 캐시에 있고, 별도 preview artifact가 생성돼 있다. 현재 캐시 해제는 결과 기록 삭제와 연결되어 있으므로 14일 shadow 운영 중 용량을 관찰하고, 자동 24시간 정리를 도입하려면 이후 내보내기 가능 시간과 함께 별도 정책으로 확정한다.

다음 운영 gate는 신규 Apple 사진이 실제로 들어온 날의 자동 job 확인과 14일 shadow 결과 검토다. 자동 앨범 추가와 원본 삭제는 여전히 비활성 상태다.
