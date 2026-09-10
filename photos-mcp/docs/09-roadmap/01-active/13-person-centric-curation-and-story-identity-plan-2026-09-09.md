# 인물 중심 추천·동일인 확인·Story 인명 반영 계획

작성일: 2026-09-09
상태: Phase A·B·E 핵심 및 Phase F 안전 공유 기반 구현·자동 검증 완료, Phase C·D·G 후속 진행 중
적용 범위: PhotosMcp 추천 엔진, Apple Photos·로컬 사진, People 관리, Story 생성·공유, Android Companion
관련 계획: [인물 구성 기반 장면 분리와 얼굴 품질 추천](02-person-aware-scene-ranking-shadow-2026-08-10.md), [Android Companion 앱 로드맵](11-photosmcp-android-companion-app-architecture-roadmap-2026-09-08.md)

## 1. 이번 검토의 결론

PhotosMcp에는 이미 인물 사진 우대 점수, Apple Photos의 사람 이름 필터, 얼굴 임베딩 기반 동일인 후보 묶음, 사용자가 이름·병합·분리를 관리하는 AppKit 화면이 있다. 따라서 다음 두 가지 제품 기능은 새로 처음 만드는 것이 아니라 기존 기반을 연결해 구현할 수 있다.

1. 사람이 잘 나온 사진을 우선 추천하는 `인물 위주` 모드
2. 사용자가 확인한 인물 이름을 개인 Story에 정확하게 표시하는 기능

다만 현재의 자동 동일인 판정은 **사용자에게 후보를 제안하는 용도**로만 사용한다. 자동으로 얼굴에 실명을 확정하고 Story에 쓰는 기능은 아직 운영 승격하지 않는다.

이유는 명확하다.

- 독립 holdout은 같은 사람 5건, 관측 오병합 0건이지만 표본이 너무 작아 false merge의 Wilson 95% 상한이 `43.45%`다.
- 현재 프로젝트의 readiness 판정도 `shadow_only`이고 운영 추천은 바뀌지 않았다.
- 단일 유사도 `0.35`는 다른 사람 false match가 `57.25%`였고, 보수적인 `0.725`는 false match 관측은 없었지만 같은 사람 recall이 `73.86%`였다.
- 얼굴 결함 strict veto는 87개 비교 장면에서 Top-1이 `+1.1495%p` 개선되어 승격 기준 `+5%p`에 미달했다.
- production의 기존 known-face 경로는 cosine `> 0.4`이면 이름을 매칭하는데, 이는 현재 보정 결과의 모호 구간에 있으므로 Story의 실명 근거로 쓸 수 없다.

따라서 권장 제품 경계는 다음과 같다.

```text
사진 수집
  → 얼굴 검출·품질 측정
  → 익명 동일인 후보 묶음
  → 소유자가 같은 사람인지 확인
  → 소유자가 이름 및 Story 사용 범위 확인
  → 대상 인물 포함 여부를 hard gate로 판정
  → 대상 얼굴 품질 + 전체 사진 품질로 장면 내 순위 결정
  → 날짜·장면·구성 다양성을 반영해 추천 세트 구성
  → 서버가 확인된 인물 참조만 Story에 결정적으로 표시
  → 가족 공유본은 별도 이름 공개 동의를 적용해 생성
```

핵심 원칙은 `얼굴이 비슷함`, `같은 사람임`, `그 사람의 이름`, `Story에 이름을 써도 됨`, `공유 Story에 이름을 써도 됨`을 서로 다른 상태로 다루는 것이다.

## 2. 세 에이전트 검토 종합

이번 계획은 서로 다른 관점의 세 에이전트가 현재 코드와 검증 결과를 독립적으로 검토한 뒤 통합했다.

| 검토 관점 | 주요 발견 | 계획에 반영한 결정 |
|---|---|---|
| 코드·데이터 흐름 감사 | `person` 프로필, Apple `person` 필터, AppKit People UI는 존재하지만 추천 저장소·Story와 연결되지 않는다. AppKit registry와 vendor `known_faces`가 서로 다른 이름 원장이다. | 하나의 application-layer 인물 원장을 먼저 만들고 vendor known-face 등록 경로는 직접 제품 기능으로 쓰지 않는다. |
| 동일인·추천 품질 정책 | 현재 자동 동일인 근거는 사용자 검토 후보로 충분하지만 실명 자동 귀속에는 부족하다. 기존 `person` 점수는 특정 인물 보장 없이 얼굴이 많은 단체사진을 선호할 수 있다. | 인물 존재 우대와 특정 인물 추천을 분리하고, 특정 인물 포함 여부를 점수 보너스가 아닌 hard gate로 둔다. 자동 match는 `suggested`로만 저장한다. |
| Android·Story·공유 UX | 이름 저장과 그룹 확인, 개인 Story 이름 사용, 가족 공유 이름 사용을 각각 확인해야 한다. 현재 공유 package는 개인 Story 문구를 snapshot으로 복사하므로 이름이 그대로 노출될 수 있다. | Android에 인물 검토 진입점을 만들고, Story 이름은 LLM 자유 문장이 아니라 서버 renderer가 넣는다. 가족 공유 이름은 인물별 opt-in이고 기본은 꺼짐이다. |

세 검토 모두 다음 사항에 합의했다.

- 자동 동일인 판정은 당분간 `후보 제안 → 소유자 확인` 방식으로 운영한다.
- `인물 위주`와 `특정 인물 중심`은 다른 선택 모드다.
- Story가 사용할 이름은 소유자가 확인한 이름만 허용한다.
- 가족관계, 호칭, 감정은 얼굴·이름·공동 등장만으로 추론하지 않는다.
- 개인 Story와 30일 공유 Story의 이름 노출 동의를 분리한다.
- Google Photos Picker 자산은 현재의 얼굴 품질·얼굴 군집 차단 정책을 유지한다.

## 3. 현재 구현에서 재사용할 수 있는 기반

### 3.1 인물 사진 점수 프로필

`src/photos_mcp/vendor/photo-ranker/scoring.py`에는 이미 `general`, `person`, `landscape` 프로필이 있다. `person`은 quality `0.30`, family `0.40`, event `0.10`, uniqueness `0.20` 비중과 얼굴 수·알려진 인물 수 보너스를 사용한다.

이 프로필은 특정 이름의 사람을 찾는 기능이 아니라 **사람이 등장하는 사진 전반을 우대하는 모드**로 재사용한다. 현재처럼 얼굴 수가 많을수록 보너스를 주는 방식만으로는 큰 단체사진이 과도하게 올라갈 수 있으므로 대상 얼굴 품질과 포트폴리오 다양성 규칙을 추가한다.

### 3.2 특정 인물 요청 계약

`application/action_options.py`, `application/selection_service.py`, `application/run_service.py`에는 이미 다음 개념이 존재한다.

- `select_best_person`
- `person`
- `selection_profile="person"`

하지만 Android 수동 Story 요청에는 `person`, `person_identity_id`, `selection_profile` 필드가 없고, `combined_curation.py`는 child run을 `selection_profile="general"`로 고정한다. 따라서 새 알고리즘보다 먼저 요청 계약을 전체 경로에 관통시켜야 한다.

### 3.3 Apple Photos 사람 이름

Apple Photos adapter는 `person_info`에서 사진별 `persons`를 읽고 특정 이름으로 후보를 필터링할 수 있다. Apple Photos의 People 앨범은 초기 `특정 인물 중심` 기능에서 가장 유용한 discovery source다.

다만 Apple이 제공한 이름은 처음에는 `provider_asserted`로 취급한다. 사용자가 PhotosMcp에서 한 번 확인해야 `user_confirmed`가 되며, 그 뒤에만 Story 이름으로 사용할 수 있다.

### 3.4 동일인 후보 묶음

`application/face_identity_grouping.py`의 constrained grouping은 다음 보수적 규칙을 갖고 있다.

| 구간 | 현재 기준 | 제품에서의 의미 |
|---|---:|---|
| 다른 사람 쪽 증거 | `similarity <= 0.35` | 자동 병합 금지. 영구적인 다른 사람 확정은 아님 |
| 모호 구간 | `0.35 < similarity < 0.725` | 단일 유사도로 결정하지 않음 |
| 익명 core 후보 | `similarity >= 0.725` | same-photo 및 coherence 제약을 통과할 때만 동일인 후보 |
| 복수 지지 attachment | `>= 0.363` 지지 2개 이상, 한 지지 `>= 0.55` | 충돌이 없는 익명 그룹 후보에만 부착 |
| 실명 자동 귀속 | 비활성 | 별도 독립 holdout 승격 전까지 사용자 확인 필요 |

같은 사진에 함께 나온 두 얼굴을 자동으로 같은 사람으로 묶지 않는 현재 제약은 유지한다. 여기에 confirmed 이름 충돌, 낮은 cluster coherence, 단일 burst에서만 나온 반복 근거를 병합 veto로 추가한다.

### 3.5 AppKit People 관리 기능

`application/person_identity_management.py`와 `interfaces/appkit/people/controller.py`에는 다음 기능이 있다.

- 이름 지정
- 얼굴을 새 인물로 분리
- 다른 인물로 이동
- 두 인물 그룹 병합
- 얼굴 아님 처리와 복원
- 한 단계 실행 취소

현재 private registry는 `~/.photos-mcp/people/people-private.json`이고 디렉터리 `0700`, 파일 `0600`, atomic replace를 사용한다. 2026-09-09 확인 기준 schema version 3, 수동 인물 3개, 이름 있는 인물 3개, face override 34개, 제외 얼굴 3개가 있다. 이 문서에는 실제 이름과 얼굴·사진 식별자를 기록하지 않는다.

현재 이름 저장은 자동 그룹 전체를 수동 identity로 만드는 효과가 있어 `이 얼굴들이 같은 사람임을 확인`과 `이름 지정`이 분리되어 있지 않다. 새 모델에서는 이를 별도 단계로 나눈다.

### 3.6 추천 자산과 Story revision

- `RecommendationStorageService`의 content-addressed `local_asset_id`는 job별 face ID보다 안정적이므로 사진-인물 association의 주 anchor로 재사용할 수 있다.
- Story는 evidence hash가 바뀌면 새 revision을 만들 수 있으므로, identity revision과 이름 동의 revision을 evidence hash에 포함하면 이름 변경·철회 후 안전하게 새 Story를 만들 수 있다.
- Story 공유는 immutable package, 만료, passcode, session version, revoke 기반이 이미 있으므로 인물 이름 공개 정책과 cascade revoke만 추가하면 된다.

## 4. 현재 구현의 핵심 단절점

### 4.1 인물 이름 원장이 두 개다

현재는 다음 두 저장소가 동기화되지 않는다.

1. AppKit People의 `people-private.json`
   - opaque identity ID
   - 이름과 수동 membership override
   - embedding 미저장
2. photo-ranker DB의 `known_faces`
   - plaintext 이름
   - raw embedding
   - vendor MCP의 register/list/delete 도구로 관리

AppKit에서 이름을 붙여도 ranker의 known face가 되지 않고, ranker에 등록한 이름은 People 화면의 identity가 되지 않는다. Story는 둘 다 직접 참조하지 않는다. 이를 계속 병행하면 이름 충돌과 삭제 누락이 생기므로 application layer의 private identity repository를 단일 기준 원장으로 만든다.

기존 `known_faces`는 read-only 마이그레이션 입력으로만 사용한다. 이름이 같다는 이유로 자동 병합하지 않으며, 사용자가 연결을 확인한 뒤 새 person ID에 귀속한다. 기존 cosine `> 0.4` known-face 결과는 `suggested` 이상의 권한을 갖지 않는다.

### 4.2 face ID와 자동 identity ID가 재분석에 안정적이지 않다

현재 face ID는 `job_id + photo_id + face_index`를 해시하고, 자동 identity ID는 현재 face ID 집합을 다시 해시한다. 같은 원본을 재분석해도 job이나 검출 순서가 바뀌면 ID가 달라지고, 그룹에 얼굴이 추가되어도 automatic identity ID가 바뀐다.

Story가 이름에 의존하기 전에 다음 안정 lineage가 필요하다.

```text
source_asset_key = provider + provider_asset_id
또는
local_asset_id = content-addressed managed asset

face_observation_id = source_asset_key/local_asset_id
                      + model fingerprint
                      + bbox/crop fingerprint
```

재분석 시 같은 asset 안에서 bbox IoU, 같은 embedding space의 similarity, landmark와 face order를 보조 근거로 old/new observation을 연결한다. 결과는 `matched`, `ambiguous`, `missing`, `new`로 구분하며 `ambiguous`와 `new`에는 이름을 자동 계승하지 않는다.

### 4.3 shadow 측정이 전체 라이브러리 인덱스가 아니다

People 카탈로그는 최근 완료 job과 별도 `measurements-private.json`이 있는 자료에 의존하며, shadow 분석은 주로 여러 사진으로 구성된 scene을 측정했다. 독립 portrait와 singleton 추천 사진이 빠질 수 있다.

운영 기능을 위해서는 Apple/local 전체 라이브러리를 한 번에 무제한 스캔하는 방식이 아니라 다음 증분 인덱스를 둔다.

- 일일 또는 수동 작업의 preselected 후보
- 새로 추가된 Apple/local 자산
- Story 재분석에 포함된 날짜 범위
- 사용자가 명시적으로 인물 검색한 범위

최대 1,000장·6시간 작업 예산 안에서 얼굴 관측을 증분 생성하고, 중단되면 checkpoint 이후부터 재개한다.

### 4.4 추천 저장 과정에서 인물 association이 사라진다

`RankedPhoto`에는 `faces_detected`, `known_persons`가 있지만 materialized recommendation member에는 안정 person ID와 confirmation provenance가 없다. Story evidence도 날짜·장면·위치·추천 사유만 포함하고 인물 정보를 의도적으로 제외한다.

raw 이름을 일반 recommendation JSON에 복사하지 않는다. 별도 private association projection에 다음만 연결한다.

- `local_asset_id` 또는 안정 source asset key
- `person_identity_id`
- membership state
- provenance와 policy version
- identity revision
- photo 안에서의 face role

Story 생성기는 이 private projection에서 현재 audience에 허용되는 정보만 조회한다.

### 4.5 개인 Story와 공유 Story가 분리되어 있지 않다

현재 공유 package는 개인 Story의 title, summary, closing, photo title·alt를 snapshot으로 복사한다. 개인 Story 본문에 이름을 바로 넣으면 가족 공유에도 자동으로 들어갈 수 있다.

이름 기능을 켜기 전에 owner projection과 public projection을 분리한다. 개인 Story의 실명이 공유 문구에 있는지 문자열 치환으로 지우는 방식은 금지한다. 공유 audience에 허용된 person refs로 public narrative/caption을 새로 구성한다.

## 5. 출처별 허용 범위

| 출처 | 인물 등장 중심 선택 | 얼굴 품질·익명 그룹 | 특정 인물 확인 | Story 이름 |
|---|---|---|---|---|
| Apple Photos | 허용 | 로컬 private 처리 허용 | Apple 이름은 후보, 사용자 확인 후 사용 | `user_confirmed` + audience 동의 시 허용 |
| 로컬 managed asset / Android 원본 | 허용 | 로컬 private 처리 허용 | 사용자가 확인한 PhotosMcp identity만 사용 | `user_confirmed` + audience 동의 시 허용 |
| Google Photos Picker | 일반 장면/VLM의 사람 등장 신호도 정책 검토 전 보수적으로 사용 | 얼굴 quality/clustering 금지 유지 | 금지 | Google 경로에서 얻은 얼굴 identity 이름 사용 금지 |

Google Photos API 정책은 Photos API로 얼굴 cluster를 생성하는 사용을 금지한다. Picker는 사용자가 선택한 항목을 앱에 제공하는 흐름이며, 선택 자산이라고 해서 얼굴 군집 정책이 사라지는 것은 아니다. 현재 PhotosMcp도 `SourceCapabilities`와 `SourcePolicy`에서 Google에 대해 `face_quality=false`, `face_clustering=false`를 강제하므로 이 경계를 유지한다.

참고:

- [Google Photos API User Data and Developer Policy](https://developers.google.com/photos/support/api-policy)
- [Google Photos Picker API](https://developers.google.com/photos/picker/reference/rest)

Google 자산과 Apple/local 원본이 content hash로 동일하더라도, Google provenance의 자산을 얼굴 분석 대상으로 자동 승격하지 않는다. 동일 원본의 Apple/local 사본을 독립적으로 소유·처리하는 경로가 있을 때만 그 로컬 provenance에서 만들어진 private association을 사용하며, 구현 전에 provider-policy 회귀 테스트를 추가한다.

## 6. 권장 인물 도메인 모델

기존 JSON을 직접 확장하기보다 owner-only SQLite repository 또는 동등한 private repository를 application layer에 둔다. raw embedding과 face crop은 일반 run·Story DB 및 모바일 DTO와 분리한다.

### 6.1 `person_identities`

```text
person_identity_id              # 이름과 무관한 불변 opaque ID
display_name
name_status                     # unlabeled | provider_asserted | user_confirmed | revoked
identity_status                 # candidate | user_confirmed | conflicted | hidden | deleted
identity_revision
private_story_name_allowed
shared_story_name_allowed
created_at / updated_at / deleted_at
```

동명이인은 서로 다른 `person_identity_id`를 가진다. 이름 문자열은 identity key가 아니다.

### 6.2 `face_observations`

```text
face_observation_id
provider
provider_asset_id 또는 local_asset_id
model_family / model_version / embedding_dimension
bbox / crop_fingerprint
embedding_ref                    # private blob reference
quality_summary
observation_status
created_at / invalidated_at
```

서로 다른 model family의 embedding을 직접 비교하지 않는다. 현재 production InsightFace와 shadow SFace처럼 embedding space가 다른 경우 model fingerprint가 반드시 일치해야 한다.

### 6.3 `person_memberships`

```text
face_observation_id
person_identity_id
membership_state                # candidate | owner_confirmed | rejected | conflicted
provenance                      # owner | apple_people | auto_policy_version
similarity_private
evidence_count
decision_policy_version
membership_revision
created_at / invalidated_at
```

모바일과 Story에는 similarity를 보내지 않는다. 사용자가 한 merge/split/move/exclude 결정은 모델 재분석 결과보다 항상 우선한다.

### 6.4 그룹·관계·Story 동의

```text
person_groups
  person_group_id, owner_label, group_revision

person_group_members
  person_group_id, person_identity_id

story_name_consents
  person_identity_id, audience(owner|family_share), allowed, consent_revision

identity_decisions
  opaque event, before_hash, after_hash, actor, policy_version, created_at
```

`우리 가족`, `친구` 같은 그룹명과 `엄마`, `딸` 같은 관계는 사용자가 직접 입력한 경우에만 사용한다. 얼굴 수나 나이, 공동 등장 빈도로 가족관계를 추론하지 않는다.

## 7. 인물 중심 추천 정책

### 7.1 세 가지 선택 모드

Android·Telegram·MCP·수동 작업에서 공통으로 다음 모드를 제공한다.

| 사용자 모드 | 내부 계약 | 동작 |
|---|---|---|
| 균형 있게 | `selection_mode=balanced`, `selection_profile=general` | 현재 일반 추천 유지 |
| 인물 위주 | `selection_mode=people_present`, `selection_profile=person` | 특정 이름 없이 사람이 잘 나온 사진 우대 |
| 특정 인물 | `selection_mode=specific_person`, `person_identity_ids=[...]` | 확인된 대상 인물이 포함된 사진만 추천 후보 |

`인물 위주`는 1차 제한 베타에서 Apple/local에만 제공한다. `특정 인물`은 user-confirmed association이 충분한 인물만 선택 목록에 보인다. Google이 포함된 작업에서 지원하지 않는 모드를 선택하면 명확한 inline 오류와 출처 변경 동작을 제공하며 일반 추천으로 몰래 fallback하지 않는다.

### 7.2 특정 인물 eligibility hard gate

특정 인물 추천은 점수 보너스가 아니라 먼저 대상이 실제로 포함됐는지 판정한다.

초기 허용 근거:

- PhotosMcp에서 사용자가 해당 얼굴 membership을 직접 확인함
- Apple Photos의 `persons` 이름을 PhotosMcp identity와 사용자가 한 번 연결·확인함

`suggested`, `ambiguous`, legacy cosine `> 0.4`만 있는 사진은 특정 인물 추천에 넣지 않는다. 후보가 부족해도 일반 사진으로 채우지 않고 `확인된 대상 인물 사진이 부족함`을 결과에 표시한다.

### 7.3 대상 얼굴 품질과 사진 품질

특정 인물 모드에서는 그룹 전체 얼굴의 최저 점수만 보지 않고 대상 얼굴을 별도로 평가한다.

- 대상 얼굴 capture quality
- 눈 감음
- sharpness와 motion blur
- 큰 pose와 얼굴 가림·잘림
- 얼굴 크기와 화면 중심 거리
- 전체 사진 technical/aesthetic score
- 장면 의미와 구도
- camera gaze와 smile은 약한 선호 신호

기존 검증에서 모든 face bonus는 기준선을 악화시킨 사례가 있으므로 표정·미소를 강한 가산점으로 쓰지 않는다. 먼저 선택된 사진에 명확한 눈 감음·흐림이 있고, 전체 품질 차이가 작은 대체 사진이 충분히 좋을 때만 strict defect veto로 교체한다.

### 7.4 추천 세트 다양성

인물 중심 결과가 유사한 얼굴 클로즈업만 반복되지 않게 포트폴리오 단계의 규칙을 둔다.

- 동일 burst와 near-duplicate는 기본 1장
- 같은 scene은 기본 1장, 표정·구성이 의미 있게 다를 때만 최대 2장
- 날짜·장소·행사가 다른 사진 우선
- 단독, 소그룹, 확인된 가족 그룹, 큰 단체 구성을 분산
- 배경 얼굴은 주인공으로 승격하지 않음
- `인물만`, `가족과 함께`, `균형 있게`의 후속 구성 옵션을 제공

초기 quota 후보는 대상 단독/소그룹 최소 50%, 확인된 그룹 동반 사진 가능 시 최소 25%, 큰 단체/미확인 인물 다수 최대 25%다. 이는 하드코딩된 영구 정책이 아니라 실제 사용자 검토로 조정하는 초기값이다.

## 8. 동일인 확인 정책

### 8.1 상태 전이

```text
auto candidate cluster
  → suggested membership
  → 소유자: 같은 사람 / 다른 사람 / 판단 어려움 / 얼굴 아님
  → owner_confirmed membership
  → 이름 확인
  → 개인 Story 이름 허용
  → 가족 공유 이름 허용
```

유사도 수치는 검토 화면에서 숨긴다. 모델 점수가 소유자의 육안 판단을 유도하지 않게 하기 위함이다.

### 8.2 false merge 방지

- 같은 source asset의 서로 다른 얼굴은 cannot-link다.
- 이름이 다른 두 confirmed identity는 자동 병합하지 않는다.
- `A-B`, `B-C`만 높고 `A-C`가 낮은 chain merge를 허용하지 않는다.
- 병합 후 모든 member의 medoid similarity 또는 component 하위 분위 coherence를 검사한다.
- 같은 burst의 거의 동일한 두 사진은 복수 독립 지지로 세지 않는다.
- 닮은 가족, 쌍둥이, 어린이의 성장, 마스크·안경·측면·저조도·작은 얼굴을 위험 strata로 별도 평가한다.
- named identity 후보는 서로 다른 사진의 confirmed template 최소 2개, 두 번째 후보와 충분한 margin, 얼굴 품질 gate, 이름·same-photo 충돌 없음이 필요하다.

충분한 독립 holdout 전에는 위 조건을 충족해도 자동 이름 귀속하지 않고 review queue에만 넣는다.

### 8.3 false split과 수동 결정

- 자동으로 잘게 나뉜 그룹은 병합 제안만 제공한다.
- 사용자가 병합하면 기존 stable person ID 중 하나를 명시적으로 유지한다.
- 양쪽 이름이 다르면 유지할 이름을 사용자가 선택해야 한다.
- 분리한 새 그룹은 이름 없는 미확인 상태로 시작한다.
- identity가 conflicted 상태가 되면 Story 실명 사용을 일시 중단한다.
- 수동 결정은 모델 재분석보다 우선하며 lineage가 `matched`일 때 계승한다.

## 9. Story 이름 반영 설계

### 9.1 LLM과 이름 판정을 분리

LLM이 얼굴을 보고 이름을 추정하거나, 자유 문장 안에 임의 이름을 만들게 하지 않는다. 가장 안전한 MVP는 LLM evidence에 실제 이름을 넣지 않는 것이다.

Story v3 private evidence 예시는 다음과 같다.

```json
{
  "photo_ref": "p_opaque",
  "person_refs": ["person_opaque"],
  "confirmed_group_refs": ["group_opaque"],
  "identity_evidence_hash": "sha256:..."
}
```

서버 renderer는 다음 조건을 모두 통과한 person ref만 이름으로 표시한다.

1. identity의 이름 상태가 `user_confirmed`
2. 사진 membership이 `owner_confirmed`
3. identity와 membership이 현재 revision에서 stale/conflicted가 아님
4. 해당 audience의 이름 사용 consent가 true
5. 제외되거나 숨긴 인물이 아님

초기에는 자유 문장 안에 이름을 섞기보다 결정적인 caption/chip으로 표시한다.

```text
소유자가 확인한 등장인물: 인물 A, 인물 B
```

실제 화면에서는 사용자가 저장한 이름으로 렌더링한다. 미확인 후보는 `등장인물 확인 필요`로만 표시한다. 향후 자연스러운 문장에 이름을 넣더라도 `검증된 person_ref + 허용된 문장 template` 방식만 사용한다.

### 9.2 Story factuality 규칙

- evidence에 없는 이름은 출력할 수 없다.
- 같은 chapter에 각각 등장했을 뿐인 두 사람을 `함께 있었다`고 쓰지 않는다.
- 같은 asset의 confirmed co-occurrence가 있을 때만 공동 등장 표현을 허용한다.
- 확인된 관계가 없으면 `엄마와 딸`, `형제`, `친구` 같은 관계를 만들지 않는다.
- 확인된 group label이 있을 때만 `가족` 같은 표현을 사용한다.
- LLM timeout, 잘못된 person ref, 임의 인명 생성이 감지되면 이름 없는 deterministic Story로 fallback한다.

### 9.3 Story schema와 revision

Story schema를 `recommendation-story-v3`로 올리고 다음을 추가한다.

```text
story.people[]
  person_ref
  identity_revision
  name_snapshot
  name_provenance = owner_confirmed
  audience

chapter.person_refs[]
photo.person_refs[]
identity_evidence_hash
person_rendering = deterministic-v1
```

`name_snapshot`은 owner-only Story manifest에만 저장한다. 이름, membership, consent revision을 evidence hash에 포함한다. 이름 변경·병합·분리·숨김·철회는 기존 Story를 덮어쓰지 않고 새 Story revision을 생성한다.

## 10. 개인 Story와 30일 가족 공유

개인 Story와 공유 Story는 동일 문장을 복사하지 않고 audience별 projection을 별도로 만든다.

### 개인 Story

- `private_story_name_allowed=true`인 confirmed identity만 이름 표시
- 기본 동의는 초기 이름 확인 화면에서 명시적으로 받음
- 미확인 얼굴과 숨긴 인물은 이름 없이 처리

### 가족 공유 Story

- 인물별 `shared_story_name_allowed=true`와 해당 공유 작업의 `이름 포함` 선택이 모두 필요
- 기본값은 이름 제외
- 내부 person ID, embedding, similarity, 원본 경로, provider asset ID는 public DTO에서 제거
- 공유 화면에는 포함될 이름, 상세 위치·지도, 다운로드, 사진 수, 만료일을 함께 disclosure
- 공개본에는 `표시된 이름은 Story 소유자가 직접 확인했습니다.` 안내

### 이름 변경·숨김·철회

공유 package는 immutable snapshot이므로 silently mutate하지 않는다.

- 이름 변경·숨김 전에 영향을 받는 활성 공유 수를 표시한다.
- 기본 동작은 `기존 공유 종료 후 변경`이다.
- Story 이름 사용 철회는 해당 identity를 포함한 활성 share를 revoke한다.
- `session_version`을 올려 기존 12시간 viewer session도 즉시 무효화한다.
- 파생 이미지 cache purge는 retry 가능한 outbox와 영수증으로 추적한다.
- 새 이름으로 공유하려면 새 Story revision과 새 package를 발행한다.

## 11. Android Companion UX

현재 하단 `홈`, `실행`, `결과`, `스토리` 구조를 유지한다. 인물 기능은 별도 fifth tab을 추가하기보다 다음 경로로 진입한다.

1. Story 카드: `등장인물 2명 · 확인 필요 3건`
2. 설정 → 개인정보 → 인물 관리

현재 대형 단일 `MainActivity.java`에 화면을 계속 붙이지 않고 `people/` feature, repository, DTO, view model을 분리한다.

### 11.1 날짜로 Story 만들기

출처 아래에 `사진 구성` single-choice를 추가한다.

- 균형 있게 — 기본
- 인물 위주
- 풍경 위주

후속 단계에서 `특정 인물` chip을 추가한다. 프로필을 바꾸면 기존 사진 수 preview를 무효화하고 다시 계산한다. 48dp 이상 touch target, TalkBack label, 큰 글자와 화면 회전에서도 내용이 잘리지 않는 adaptive layout을 적용한다.

### 11.2 인물 검토 큐

얼굴 두 장을 같은 크기로 보여주고 네 동작을 제공한다.

- 같은 사람
- 다른 사람
- 판단 어려움
- 얼굴 아님

그룹 확인 뒤에만 이름 입력을 활성화한다. 이름 dialog에는 다음을 둔다.

- 표시할 이름
- `내 Story에서 이 이름 사용`
- `가족 공유 Story에 이 이름 포함 허용` — 기본 꺼짐

### 11.3 인물 상세

- 대표 얼굴과 얼굴 grid
- 같은 사람 확인 상태
- 이름 지정·변경
- 선택 얼굴 분리·이동
- 그룹 병합
- 얼굴 아님
- 숨기기와 복원
- Story 이름 사용 중지
- 인물 데이터 삭제

`숨기기`, `Story 이름 사용 중지`, `인물 데이터 삭제`의 의미를 구분한다. 인물 데이터 삭제도 원본 사진은 삭제하지 않는다.

## 12. API·권한·감사

### 12.1 private API

초기 endpoint 계약은 다음과 같다.

```text
GET  /people
GET  /people/review-queue
GET  /people/{person_id}
GET  /people/faces/{derivative_id}
POST /people/{person_id}/confirm
POST /people/{person_id}/name
POST /people/{person_id}/merge
POST /people/{person_id}/split
POST /people/{person_id}/hide
POST /people/{person_id}/restore
POST /people/{person_id}/revoke-story-use
GET  /stories/{story_id}/identity-preview
POST /stories/{story_id}/refresh
POST /stories/{story_id}/shares
POST /shares/{share_id}/revoke
```

모든 mutation은 현재 Android device signature, body hash, nonce, `Idempotency-Key` 패턴을 재사용하고 `If-Match: identity_revision`을 추가한다. 충돌하면 `409`로 최신 상태와 비교 화면을 제공한다.

### 12.2 권한

- `people:read`
- `people:write`
- `story:share`

민감한 인물 변경을 기존 `curation:write` 하나로 처리하지 않는다. 가족 공유 수신자는 관리 API scope를 갖지 않는다. Web owner의 identity mutation에는 Tailnet/same-origin 검사 외에 CSRF token과 재인증을 추가한다.

### 12.3 개인정보 감사 원장

append-only event에는 다음만 기록한다.

- confirm/name/merge/split/hide/restore/revoke/share-create/share-revoke
- opaque entity ID와 revision
- actor device fingerprint 또는 owner login hash
- idempotency/request ID
- before/after hash
- 영향받은 Story/share ID
- 시각과 결과

이름 문자열, 얼굴 crop, embedding, 원본 경로, raw request body는 감사 로그에 남기지 않는다.

## 13. 마이그레이션과 재분석

현재 private registry의 수동 결정을 잃지 않으면서 다음 순서로 전환한다.

1. schema v3 registry와 legacy known-face DB의 read-only snapshot을 만든다.
2. 새 stable person ID와 private repository를 생성한다.
3. 기존 수동 identity 3개와 face override 34개를 가져오되 실제 이름은 로그·보고서에 출력하지 않는다.
4. 기존 face ID를 원본 asset, bbox, crop fingerprint와 대조해 lineage를 만든다.
5. 명확히 대응되는 `matched` face만 manual membership을 계승한다.
6. ambiguous/new/missing face는 검토 큐에 넣고 이름을 자동 계승하지 않는다.
7. legacy known face는 별도 후보로 보여주며 이름 문자열만 같다고 병합하지 않는다.
8. 새·기존 엔진을 side-by-side로 replay하고 preservation·ambiguity·conflict 수를 보고한다.
9. 사용자 확인 후 atomic index swap한다.
10. 새 recommendation collection과 Story revision을 만들고 기존 Story·공유 snapshot은 보존한다.

재분석은 기존 결과를 파괴하는 작업이 아니라 새 analysis/identity evidence revision을 만드는 작업이다. 실패하면 이전 private identity index로 복귀할 수 있게 migration snapshot만 보존한다. 성공적인 전환 검증 후 불필요한 임시 migration 사본은 제거할 수 있다.

## 14. 품질 평가와 승격 기준

### 14.1 평가 데이터

최소 네 층의 데이터를 별도로 만든다.

1. 얼굴 pair: same, different, uncertain, invalid detection
2. identity cluster: 전체 cluster purity와 fragmentation
3. 같은 장면의 대상 인물 사진 선호: best photo, closed eyes, blur, occlusion, poor crop, duplicate
4. Story factuality: 이름 근거, photo/chapter 연결, 공동 등장, 관계, 공유 누출

다음 어려운 strata를 의도적으로 포함한다.

- 닮은 가족과 쌍둥이
- 어린이의 성장에 따른 시간 차
- 안경·마스크·모자·측면 얼굴
- 저조도·역광·흐림·작은 얼굴
- 같은 날과 수년 차이
- 단체사진의 배경 얼굴
- 같은 사진에 함께 나온 서로 다른 사람
- 카메라·해상도·입력 source 차이

burst와 near-duplicate는 같은 split에 넣고 calibration과 final holdout을 분리한다. 쉬운 random negative만으로 표본 수를 채우지 않고 실제 병합 경계 후보를 포함한다.

### 14.2 동일인 gate

- anonymous multi-support merge의 false merge Wilson 95% 상한 `<= 5%`
- named 자동 귀속의 false attribution Wilson 95% 상한 `<= 1%`
- named false attribution 관측 오류 `0`
- same-photo conflict `0`
- confirmed identity 간 자동 merge `0`
- model/version별 독립 holdout

현재 5개의 독립 same-person holdout으로는 이름 자동 귀속을 열지 않는다. 관측 오류 0을 가정하면 5% 상한을 입증하려면 총 73건 수준, 1% 상한은 약 381건 수준이 필요하다. 실제 표본은 same/different, 사람·시간·촬영 조건별로 균형 있게 수집한다.

### 14.3 인물 추천 gate

- target-person precision: `100%` 또는 미확인 대상 포함 `0`
- target coverage/recall
- paired human review의 Top-1 개선 95% bootstrap 하한 `> 0`
- general 추천 Top-2 손실 `<= 1%p`
- clear-defect escape rate 감소
- near-duplicate·동일 scene 반복률 감소
- 날짜·장소·행사·구성 coverage
- 최소 100개 장면, 운영 승격은 200개 이상과 여러 인물 strata 권장

### 14.4 Story gate

- unsupported exact-name rate: `0`
- wrong-person name rate: `0`
- unsupported relationship rate: `0`
- false co-occurrence claim rate: `0`
- owner-to-share name leakage rate: `0`
- 모델이 임의 인명 또는 알 수 없는 person ref를 반환할 때 deterministic fallback 성공률: `100%`
- 이름 변경·병합·분리·철회 후 새 revision과 공유 revoke 회귀 통과

이름 오류는 평균 점수로 상쇄하지 않는다. 하나라도 잘못된 실명이 나오면 해당 이름 자동화 release를 차단한다.

## 15. 단계별 구현 로드맵

### Phase A — 단일 인물 원장과 안정 ID

- private identity repository와 migration schema 추가
- immutable person ID와 stable face observation lineage 도입
- registry v3 마이그레이션 dry-run 및 preservation 보고서
- vendor known-face는 read-only migration source로 격리
- raw embedding·crop을 일반 Story/run DB와 분리
- manual merge/split/move/exclude 우선순위와 identity revision 구현
- 모든 feature flag는 기본 off

완료 조건:

- 기존 수동 identity/override의 보존·보류·충돌 수가 설명 가능함
- 재분석해도 matched face의 manual membership이 유지됨
- 이름·embedding·경로가 로그나 모바일 DTO에 나오지 않음

### Phase B — 인물 위주 요청의 end-to-end 연결

- manual request schema에 `selection_mode`, `selection_profile` 추가
- combined child run의 `general` 하드코딩 제거
- Telegram/MCP/Android에서 `균형/인물/풍경` 선택 지원
- Apple/local의 `people_present` 제한 베타
- Google이 포함된 인물 모드의 명시적 unsupported 처리
- 기존 general 결과와 person shadow 결과 병행 저장

완료 조건:

- Android 요청값이 child `photos_run`까지 손실 없이 전달됨
- unsupported source가 일반 모드로 silently fallback하지 않음
- 기존 새벽 3시 general 작업의 결과와 Telegram 통합 메시지가 회귀하지 않음

### Phase C — 동일인 후보·확인 UX

- 전체 후보 또는 preselected 후보의 증분 face observation 생성
- constrained grouping을 global candidate identity service로 감쌈
- `suggested/owner_confirmed/rejected/conflicted` 상태 구현
- People read/write API와 review queue 추가
- Android `people/` feature와 Story 카드 진입점 구현
- AppKit 기존 작업을 새 원장에 연결
- merge/split/hide/restore/name conflict 및 stale version UX

완료 조건:

- 자동 그룹은 이름을 확정하지 않음
- 같은 사진의 두 얼굴·서로 다른 confirmed 이름이 자동 병합되지 않음
- Android와 AppKit의 변경이 같은 identity revision에 보임

### Phase D — 특정 인물 중심 추천

- user-confirmed person 선택 chip과 hard eligibility 추가
- Apple person filter는 discovery 최적화로만 사용하고 로컬 association으로 최종 확인
- 대상 얼굴 단위 strict defect veto
- 날짜·장면·burst·인물 구성 다양성 quota
- 추천 저장소에 private person association projection 연결
- 결과에 대상 확인 수, 미확인 제외 수, 후보 부족 상태 표시

완료 조건:

- 특정 인물 precision gate 통과
- 미확인 사진이 대상 인물 결과에 포함되지 않음
- 사람이 많은 단체사진이 특정 인물보다 무조건 우선되지 않음

### Phase E — 개인 Story 인명

- Story v3의 person refs와 identity evidence hash
- owner-confirmed name 및 consent projection
- LLM name-free invariant 또는 opaque refs 계약
- server-side deterministic caption renderer
- 이름·관계·공동 등장 validator와 fallback
- rename/merge/split/hide/revoke 시 새 Story revision

완료 조건:

- 모델이 임의 이름을 출력해도 manifest·HTML에 들어가지 않음
- 사진과 연결되지 않은 사람 이름이 chapter에 나오지 않음
- 확인된 관계가 없는 가족 호칭이 생성되지 않음

### Phase F — 가족 공유 이름과 철회

- owner/public Story projection 분리
- 공유 전 포함 이름·위치·다운로드·만료 disclosure
- 인물별 가족 공유 이름 opt-in, 기본 off
- 선택 `story_id/revision` 기반 Android share API
- 이름 변경·숨김·철회 시 영향 share 탐색과 cascade revoke
- session version 증가, derivative purge outbox와 receipt
- public DTO에서 내부 identity metadata 제거

완료 조건:

- 공유 package에 opt-in 되지 않은 이름이 없음
- 이름 철회 직후 기존 URL/session으로 이름을 볼 수 없음
- 기존 immutable package가 silently mutate하지 않음

### Phase G — shadow 확대와 자동 매칭 승격 심사

- 독립 same/different·named holdout 확대
- 위험 strata·cluster·ranking·Story factuality 평가
- 자동 match 후보 acceptance/reject 비율 수집
- 익명 grouping과 named auto-attach를 별도로 승격 심사
- 기준 미달 시 review-only 유지

완료 조건:

- 이 문서의 동일인·추천·Story gate를 모두 통과함
- Google provider 정책 회귀 `0`
- 전체 test suite, 실제 Apple/local 날짜 Story, Android 실기기, 30일 공유 E2E 통과

## 16. 주요 구현 접점

예상 변경 파일과 책임은 다음과 같다.

| 영역 | 주요 파일 |
|---|---|
| identity domain/repository | `application/person_identity_management.py`, 새 private repository, `infrastructure/persistence/` migration |
| grouping/calibration | `application/face_identity_grouping.py`, `face_identity_calibration.py`, `person_shadow_readiness.py` |
| selection contract | `application/manual_curation.py`, `combined_curation.py`, `selection_service.py`, `run_service.py` |
| ranking | `vendor/photo-ranker/scoring.py`, `pipeline.py`, `scene_selection.py` |
| source policy | `domain/models/source.py`, `domain/policies/source_policy.py`, Apple/Google adapters |
| recommendation association | `application/recommendation_storage.py`, `infrastructure/persistence/run_repository.py` |
| Story | `application/story_generation.py`, Story director adapter, Story HTML/WebView projection |
| sharing | `application/story_sharing.py`, owner/public routes, privacy disclosure |
| Android | `application/mobile_client.py`, `interfaces/http/mobile_client.py`, Android `people/` feature 및 manual Story form |
| tests | identity migration, grouping, target selection, Story factuality, sharing revoke, provider policy, Android contract |

## 17. 구현 승인 시 권장 첫 범위

한 번에 자동 실명까지 열지 않고 다음 MVP를 먼저 진행한다.

1. stable person ID와 단일 private identity repository
2. 기존 People registry의 안전한 dry-run 마이그레이션
3. Android 수동 Story의 `균형 있게 / 인물 위주 / 풍경 위주` 선택
4. Apple/local `인물 위주` shadow와 실제 결과 비교
5. People review queue 및 같은 사람·다른 사람·판단 어려움·얼굴 아님 확인
6. 사용자 확인 이름을 개인 Story의 결정형 등장인물 caption에만 표시
7. 공유 Story의 이름은 기본 제외

이 MVP가 안정화된 다음 특정 인물 선택과 가족 공유 인명 opt-in을 연다. 신규 사진의 자동 이름 부착은 충분한 독립 holdout이 쌓여 별도 승격 gate를 통과할 때까지 계속 비활성화한다.

## 18. 최종 판단

사용자가 기대하는 목표는 현실적으로 달성 가능하다. 현재 기반을 가장 잘 활용하는 방법은 얼굴 인식 모델을 곧바로 운영에 연결하는 것이 아니라, 이미 구현된 Apple 사람 필터·person scoring·constrained grouping·People 확인 UI·Story revision·안전한 공유 패키지를 **하나의 확인 가능한 identity 흐름**으로 잇는 것이다.

구현 우선순위는 다음 한 줄로 정리된다.

```text
단일 인물 원장 → 안정적인 사진-인물 연결 → 인물 위주 추천
→ 사용자 확인 이름 → 개인 Story → 별도 동의한 가족 공유
→ 충분한 독립 검증 후에만 자동 이름 매칭 승격
```

이 순서를 지키면 인물 중심 추천은 비교적 빠르게 제공하면서도, 잘못된 동일인 판정이 잘못된 이름·관계·30일 공유 정보로 확대되는 것을 구조적으로 차단할 수 있다.
