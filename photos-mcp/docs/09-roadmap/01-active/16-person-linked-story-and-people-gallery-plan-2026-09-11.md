# 인물 이름 연계 Story·인물별 사진 탐색 개선 계획

작성일: 2026-09-11

상태: Phase 0/1 수직 기능 및 Story v4 필터 구현 완료 — Apple 이름 후보 5건의 소유자 연결 확인과 Phase 2 얼굴 backend 품질 승격은 운영 검수 대기

적용 범위: PhotosMcp 인물 원장, Apple/local 분석, 추천 보관소, Story 생성·공유, Android Companion, macOS AppKit

관련 문서: [인물 중심 추천·동일인 확인·Story 인명 반영 계획](13-person-centric-curation-and-story-identity-plan-2026-09-09.md), [삭제·재분석·추천 수명주기 계획](14-deletion-reanalysis-and-recommendation-lifecycle-plan-2026-09-10.md)

## 1. 결론

현재 Story 화면에 인물 이름과 인물별 사진이 보이지 않는 원인은 렌더링 기능 부족만이 아니다. 이름과 동의 정보는 정상적으로 보존되어 있지만, 그 인물이 **현재 추천 사진의 `local_asset_id`에 연결됐다는 확정 근거가 한 건도 없기 때문**이다.

현재 구현에는 이미 다음 표시 기반이 있다.

- Story 전체의 확인된 인물 요약
- 날짜별 chapter의 `함께한 사람` 캡션
- 사진별 등장인물 캡션
- Android 홈·Story 목록·추천 카드와 owner WebView가 v3 인물 presentation field를 표시할 기반
- 개인 Story와 가족 공유 Story의 이름 표시 동의 분리

그러나 upstream 자산-인물 association이 비어 있으므로 모든 인물 UI가 자동으로 숨겨진다. 또한 현재 인물 chip은 표시용 `<span>`일 뿐 필터 버튼이 아니며, Story LLM에는 실제 이름이나 가족관계가 전달되지 않아 `OO와 함께한 가을 산책` 같은 자연스러운 문장을 만들 수 없다.

따라서 권장 방향은 다음 세 가지를 순서대로 완성하는 것이다.

1. **이름을 현재 추천 자산에 안정적으로 연결한다.**
2. **인물별 사진 모아보기와 수정·검수 흐름을 제공한다.**
3. **확인된 인물 참조만 사용하는 Story v4 구조화 서술을 도입한다.**

얼굴 유사도만으로 이름이나 가족관계를 자동 확정하지 않는다. 사용자가 확인한 이름·인물 연결·표시 범위만 Story에 사용한다. 다만 가족 전용 시스템이라는 사용 정책에 맞춰, 이미 한 번 확정한 이름과 표시 동의를 매번 다시 묻지는 않는다.

## 2. 세 에이전트 검토 종합

이번 계획은 서로 다른 관점의 세 에이전트가 실제 코드와 운영 데이터를 읽기 전용으로 독립 검토한 결과를 통합했다.

| 검토 관점 | 핵심 발견 | 계획에 반영한 결정 |
|---|---|---|
| 인물 원장·lineage 감사 | 확정된 이름 3명과 Story 동의는 남아 있지만 현재 자산에 연결된 face observation·membership이 0건이다. 레거시 얼굴 연결 37건은 모두 `pending`이다. | UI보다 먼저 안정적인 자산-인물 association projection과 백필 경로를 만든다. |
| Story·API·HTML 감사 | Story v3에는 인물 필드가 이미 있지만 엄격한 gate가 모두 충족될 때만 보인다. LLM은 opaque ref만 받아 이름을 본문에 쓸 수 없고, HTML chip은 클릭 기능이 없다. | Story v4에 story-scoped 인물 facet과 검증 가능한 서술 template을 추가한다. |
| Android·macOS·UX 감사 | Android는 설정에서 인물 현황·동의만 볼 수 있고 이름 지정·오인식 수정은 Mac으로 이동해야 한다. 인물 사진 탐색 동선이 없다. | 기존 하단 탭은 유지하고 홈·결과·Story 안에 인물 탐색과 검수 흐름을 배치한다. |

세 검토가 공통으로 권고한 사항은 다음과 같다.

- 이름은 사라진 것이 아니라 현재 사진과의 연결이 승격되지 않은 상태다.
- 이름 문자열이 같다는 이유만으로 Apple 인물과 PhotosMcp 인물을 자동 병합하지 않는다.
- 인물 존재, 동일인, 이름, 가족 그룹, Story 표시 동의는 서로 다른 상태로 관리한다.
- 인물 이름을 LLM 자유 문장에 직접 섞은 뒤 문자열로 지우는 방식은 사용하지 않는다.
- 인물별 사진 수는 얼굴 검출 수가 아니라 서로 다른 사진의 수로 계산한다.
- 100장 단위 분석을 하더라도 동일인·연사·인물별 대표사진 선정은 전체 실행 범위에서 한 번 더 통합한다.

## 3. 운영 데이터로 확인한 현재 상태

2026-09-11 최신 완료 작업과 private identity 원장을 읽기 전용으로 점검했다. 실제 인물 이름, 얼굴 ID, 원본 경로는 출력하거나 이 문서에 기록하지 않았다.

| 항목 | 현재 값 | 의미 |
|---|---:|---|
| 사용자 확정 identity | 3명 | 인물 정보 자체는 보존됨 |
| 사용자 확정 이름 | 3개 | 이름 정보 자체는 보존됨 |
| 개인 Story 이름 표시 동의 | 3명 허용 | owner 표시 정책은 준비됨 |
| 가족 공유 이름 표시 동의 | 3명 허용 | family 표시 정책도 준비됨 |
| 안정 face observation | 0건 | 현재 자산에 연결 가능한 얼굴 관측 없음 |
| membership version | 0건 | 신규 원장에서 사진-인물 연결 결정 없음 |
| 현재 owner-confirmed membership | 0건 | Story gate를 통과할 association 없음 |
| 레거시 lineage hold | 37건 pending | 기존 결정이 신규 자산으로 아직 연결되지 않음 |
| 최신 수동 Story 추천 사진 | 44장 | Google 41장, Apple 3장 |
| 최신 Story 인물 요약 | 0명 | association 부재로 정상적으로 빈 projection 생성 |
| 인물 캡션이 있는 최신 Story 사진 | 0장 | 화면 문제가 아니라 upstream 데이터 부재 |

추가로 최신 Apple 추천 3장의 ranker 결과에는 Apple metadata에서 복사된 `persons` 이름 문자열이 있다. 이는 후보 표시에 유용하지만 안정적인 provider person ID는 아니며, 추천 materialization과 Story evidence 사이에서도 소실된다. 따라서 owner 확인 전에는 identity 근거로 사용하지 않는다. Google 추천 41장은 현재 정책에 따라 얼굴 식별·군집 경로를 사용하지 않으며 인물 이름도 없다.

즉 최신 Story의 결과는 현재 코드 계약상 일관되지만, 사용자 기대 기능을 충족하지 못한다.

## 4. 현재 데이터 흐름과 단절점

### 4.1 이름을 관리하는 원장이 두 경로로 나뉘어 있다

macOS AppKit 인물 관리 화면은 레거시 파일인 `~/.photos-mcp/people/people-private.json`을 수정한다. 이름, 수동 얼굴 이동·병합·분리·제외가 이 원장에 저장된다.

반면 Story가 실제 조회하는 원장은 `~/.photos-mcp/people/person-identities-private.sqlite3`다. Story 이름이 나오려면 다음 조건이 모두 참이어야 한다.

```text
Story에 포함된 local_asset_id
  → active face observation
  → 최신 owner_confirmed membership
  → user_confirmed identity
  → user_confirmed name
  → 해당 audience의 최신 allowed consent
```

현재 AppKit의 편집 결과를 신규 stable SQLite 원장과 현재 `local_asset_id` association으로 연결하는 일상적인 쓰기 경로가 없다.

근거 코드:

- `src/photos_mcp/application/person_identity_management.py`
- `src/photos_mcp/interfaces/appkit/people/controller.py`
- `src/photos_mcp/application/person_identity_repository.py::build_story_person_evidence`

### 4.2 재분석에 안정적인 얼굴 lineage가 일상 분석에서 생성되지 않는다

레거시 face ID는 `job_id + photo_id + face_index`에서 만들어져 재분석 시 안정적이지 않다. 신규 원장은 stable observation 구조를 갖고 있지만, 일반 Apple/local 분석이 face observation과 membership을 routine하게 생성하지 않는다.

기존 reconciliation은 수동으로 측정 JSON을 넘기고 `job/photo → 정확히 하나의 local_asset_id`가 증명될 때만 레거시 결정을 승격한다. 운영 레거시 37건은 이 증거가 없어 모두 pending이다.

현재 실행 환경에서는 InsightFace가 설치돼 있지 않고 MediaPipe가 먼저 선택된다. 이 MediaPipe 경로는 얼굴 box 검출용이며 동일인 비교용 embedding을 반환하지 않는다. 실제 운영 DB의 face embedding은 0건이고 최신 Apple 분석에서도 얼굴 검출 결과가 0건이므로, backend를 명시적으로 분리·검증하기 전에는 routine 분석이 동일인 association을 자동 생성한다고 가정할 수 없다.

근거 코드:

- `src/photos_mcp/application/person_identity_lineage.py`
- `scripts/migrate_or_reconcile_person_identities.py`

### 4.3 Apple의 인물 후보 정보가 추천 자산에 전달되지 않는다

Apple metadata의 인물 정보와 ranker의 `known_persons`는 분석 결과 DB에 존재할 수 있다. 그러나 recommendation member와 coordinator의 Story/share-safe analysis projection은 안정 person ID 또는 provider person alias를 보존하지 않는다. 결국 Story가 `local_asset_id`별 인물 근거를 조회할 수 없다.

근거 코드:

- `src/photos_mcp/vendor/photo-ranker/models.py`
- `src/photos_mcp/vendor/photo-ranker/pipeline.py`
- `src/photos_mcp/application/recommendation_storage.py`
- `src/photos_mcp/infrastructure/persistence/run_repository.py::get_photo_analysis_result`

### 4.4 Story v3는 표시할 수 있지만 인물 탐색과 자연어 서사를 제공하지 않는다

`story_generation.py`는 `confirmed_people`, `people_caption`, `people_overview`를 생성한다. `story_web.py`와 모바일 projection도 이를 표시한다. 다만 다음 제약이 있다.

- 모델에는 실제 이름이 아니라 opaque `person_refs`만 전달된다.
- 서버는 `함께한 사람: 이름` 형식의 결정적 캡션만 만든다.
- 가족관계나 사용자 지정 호칭 모델이 없다.
- HTML의 `person-chip`은 클릭할 수 없는 `<span>`이다.
- 모바일 DTO는 내부 ref를 모두 제거하므로 동명이인 구분과 인물별 필터를 만들 수 없다.
- `schema_version == recommendation-story-v3` exact gate가 있어 v4 도입 시 호환 처리가 필요하다.

근거 코드:

- `src/photos_mcp/application/story_generation.py`
- `src/photos_mcp/application/mobile_client.py::mobile_story_projection`
- `src/photos_mcp/interfaces/http/story_web.py`
- `src/photos_mcp/infrastructure/story_director/hermes_router.py`

### 4.5 기존 Story는 인물 변경만으로 자동 재투영된다고 보장할 수 없다

Story 조회는 저장된 manifest를 그대로 projection한다. 이름, membership, alias, 그룹 관계가 바뀌면 identity evidence hash를 갱신하고 Story revision을 재구축하는 명시적인 invalidation이 필요하다. 동의 변경 경로에는 일부 refresh가 있지만 모든 인물 변경 이벤트가 동일한 재투영 경로를 사용한다고 보장되지 않는다.

수동 Story 초기 생성 일부 경로도 identity repository 없이 먼저 fallback manifest를 만들고 후속 dispatcher가 보정한다. 후속 단계가 실패하면 빈 인물 projection이 남을 수 있으므로 최초 생성부터 같은 의존성을 주입해야 한다.

## 5. 목표 사용자 경험

### 5.1 기존 네 개 하단 탭은 유지한다

새 하단 탭을 추가해 복잡도를 늘리지 않고 `홈`, `실행`, `결과`, `스토리` 안에서 인물 기능을 연결한다.

| 화면 | 개선안 |
|---|---|
| 홈 | `확인이 필요한 인물 N건` 카드와 대표 얼굴 최대 3개, `인물 확인` 진입 |
| 실행 | `균형 있게`, `인물 위주`, `특정 인물`을 구분하고 특정 인물은 확정 association이 충분한 경우만 선택 가능 |
| 결과 | `전체 사진 / 인물` 보기 전환, 인물 카드에 대표 썸네일·표시 이름·사진 수 |
| 인물 상세 | `사진`, `Story`, `확인 대기` 구역과 `이 인물로 Story 만들기` 동작 |
| Story | `전체 / 인물 A / 인물 B / 함께 나온 사진` 필터 chip과 필터된 장수 |
| 사진 확대 | 현재 사진의 확정 등장인물 badge, owner에게만 `인물 수정` |
| 설정 | 개인 Story·가족 공유 이름 표시 정책과 기본 공개 범위만 유지 |

### 5.2 인물 chip은 실제 필터로 동작해야 한다

Story 상단의 인물 chip을 `<button>`으로 바꾸고 story-scoped facet handle로 사진을 필터링한다.

- 한 명 선택: 그 사람이 등장한 사진만 표시
- `함께 나온 사진`: 선택된 사람들이 모두 등장한 사진만 표시
- 필터 후 thumbnail grid, 확대 viewer, 좌우 flick, 하단 indicator가 모두 같은 자산 목록 사용
- 브라우저 뒤로가기와 Android WebView 상태 복원을 지원
- 동명이인은 표시 이름이 같더라도 서로 다른 handle과 카드로 유지

### 5.3 인물 검수와 오인식 수정은 Android에서도 가능해야 한다

현재 Android는 인물 현황과 표시 동의만 보여주고 이름·그룹 수정은 Mac으로 안내한다. 장기 사용성을 위해 다음 동작을 owner 전용으로 제공한다.

- `같은 사람입니다`
- `다른 사람이 섞여 있어요`
- `다른 기존 인물로 이동`
- `새 인물로 분리`
- `이름 연결만 해제`
- `얼굴이 아니에요`
- `나중에`
- 저장 직후 `되돌리기`

내부 상태명인 `candidate`, `conflicted`, `lineage`를 그대로 보여주지 않는다. 대표 얼굴과 서로 다른 날짜의 예시 사진 3~6장을 먼저 보여주고 사용자가 판단하도록 한다.

그룹 전체에 영향을 주는 병합·분리에는 `사진 N장·Story M개에 반영`처럼 영향 범위를 표시한다. revision 충돌 시 사용자 입력을 지우지 않고 최신 상태를 다시 읽어 재확인한다.

### 5.4 이름이 자연스럽게 연결된 Story

목표 문구 예시는 다음과 같다.

- `민지와 함께한 가을 산책`
- `민지와 준호가 함께한 숲길의 하루`
- `이날의 산책에는 민지와 준호가 함께했습니다.`

단, 문장에 들어가는 사람은 해당 Story·chapter·사진에서 확인된 인물 ref의 부분집합이어야 한다. 가족관계나 호칭은 사용자가 직접 등록한 경우에만 쓴다. 얼굴, 나이, 공동 등장만으로 `엄마`, `딸`, `남매` 같은 관계를 추론하지 않는다.

## 6. 목표 아키텍처

```text
Apple/local 분석
  ├─ 사람 존재 신호
  ├─ 안정 face observation
  └─ Apple provider person alias 후보
          ↓
owner 검수·기존 결정 lineage 복구
          ↓
private identity repository
  ├─ identity / confirmed name
  ├─ owner-confirmed membership
  ├─ provider alias confirmation
  ├─ owner-defined family/group/relationship
  └─ owner / family-share consent
          ↓
asset-person association projection
  local_asset_id ↔ person_identity_id
          ↓
Story v4 evidence
  ├─ LLM: opaque story-scoped person refs + 허용된 template slot
  └─ server: audience별 이름·호칭 검증 및 최종 렌더링
          ↓
macOS / Android / owner HTML / 30일 가족 공유 HTML
```

핵심은 Story가 얼굴 테이블을 직접 복잡하게 JOIN하지 않고, 현재 유효한 **자산 단위 인물 projection**을 읽는 것이다.

## 7. 데이터 모델 개선안

기존 private SQLite를 schema v2로 순방향 migration한다. 확정 identity 3명, consent, 37개 pending hold와 감사 기록은 그대로 보존한다.

### 7.1 provider person alias

```text
provider_person_alias_versions
  provider
  provider_person_key_hash
  alias_key_quality        # stable_provider_id | name_only
  private_display_label
  source_asset_evidence
  person_identity_id
  alias_state              # candidate | owner_confirmed | rejected | conflicted
  provenance
  revision
  created_at
```

Apple Photos가 제공한 person 정보를 후보로 저장한다. 현재 Apple adapter의 값은 `name_only`이므로 동명이인을 자동 구분하거나 이름 문자열만으로 병합하지 않는다. 서로 다른 날짜의 예시 사진과 함께 보여 주고 사용자가 한 번 연결한 뒤에만 확정한다. provider의 실제 이름·키는 일반 run DB나 Story DTO에 복사하지 않는다.

### 7.2 자산-인물 association projection

```text
asset_person_association_versions
  local_asset_id
  person_identity_id
  association_state        # candidate | owner_confirmed | rejected | conflicted
  evidence_kind            # face | apple_alias | owner_review | legacy_lineage
  source_job_id
  identity_revision
  policy_version
  revision
  created_at / invalidated_at
```

Story는 이 projection의 최신 `owner_confirmed` 항목만 읽는다. 동일 `content_hash`이면 기존 `local_asset_id` association을 재사용한다. 인코딩·편집으로 content가 달라졌을 때만 provider/source lineage와 owner review로 새 자산 association 후보를 만든다. 모호하면 이름을 계승하지 않고 검토 큐로 보낸다.

### 7.3 인물 존재 신호와 신원은 분리한다

```text
asset_people_presence
  local_asset_id
  people_present
  people_count_bucket
  provenance
  model_version
  created_at
```

이 정보는 `인물이 들어간 사진만 보기`에 사용할 수 있지만 실명과 연결하지 않는다. Google은 현재 provider policy를 유지하고, 허용된 비생체 VLM 사람 존재 신호만 사용한다. Google 자산을 얼굴 군집이나 이름 자동 연결 경로로 우회시키지 않는다.

### 7.4 사용자 지정 그룹·호칭

```text
person_group_versions
  person_group_id
  display_label            # 예: 우리 가족
  group_revision

person_group_member_versions
  person_group_id
  person_identity_id
  membership_state
  revision

person_relationship_versions
  person_identity_id
  owner_label              # 예: 첫째, 할머니; 사용자 입력만 허용
  relationship_revision
```

법적 실명, 생년월일, 학교, 주소 같은 불필요한 개인정보 필드는 추가하지 않는다.

## 8. Story v4 계약

### 8.1 private owner manifest

내부 Story manifest에는 다음 정보를 추가한다.

```json
{
  "schema_version": "recommendation-story-v4",
  "people_facets": [
    {
      "facet_handle": "story_scoped_opaque_value",
      "display_name": "사용자 확인 이름",
      "relationship_label": "사용자 지정 호칭",
      "photo_count": 12,
      "cover_asset_id": "local_asset_id",
      "asset_ids": ["local_asset_id"]
    }
  ],
  "photos": [
    {
      "asset_id": "local_asset_id",
      "appearances": [
        {
          "facet_handle": "story_scoped_opaque_value",
          "verification": "owner_confirmed",
          "evidence_kind": "owner_review"
        }
      ]
    }
  ],
  "people_highlights": [
    {
      "facet_handle": "story_scoped_opaque_value",
      "asset_ids": ["local_asset_id"],
      "selection_reason_codes": ["expression", "sharpness", "variety"]
    }
  ]
}
```

모바일·HTML에는 DB의 내부 person ID 대신 Story와 revision에 묶어 파생한 불투명 handle을 제공한다. owner DTO의 `asset_handle`도 `local_asset_id`와 다른 Story-scoped 값으로 만든다. 가족 공유에는 별도로 생성한 `public_asset_id`만 제공한다. 얼굴 ID, 임베딩, 유사도, 원본 경로, provider asset ID는 제공하지 않는다.

### 8.2 검증 가능한 인물 서술

LLM이 실명을 자유 텍스트로 생성하도록 하지 않는다. 다음 방식으로 Story를 만든다.

1. LLM에는 Story 범위의 opaque person ref와 각 chapter에서 실제 등장한 ref만 전달한다.
2. LLM은 허용된 `narrative_template`과 person placeholder를 반환한다.
3. 서버는 placeholder가 해당 chapter의 확인된 인물 부분집합인지 검증한다.
4. 서버가 audience에 허용된 이름·호칭과 한국어 조사를 적용해 문장을 완성한다.
5. 검증 실패 시 인물 이름이 없는 중립 문장으로 fallback한다.

예시 내부 template:

```text
{{person:ref_a|and}} 함께한 가을 산책
```

`and`, `subject`, `object`처럼 의미가 정해진 particle role을 사용하고 서버 resolver가 이름의 받침에 맞춰 `과/와`, `이/가`, `을/를`을 선택한다. placeholder 뒤에 raw 조사 문자열을 직접 붙이지 않는다. 실제 이름과 관계를 모델 프롬프트 또는 자유 출력에 남기지 않으면서도, 정형 `함께한 사람:`보다 자연스러운 제목과 chapter 문구를 제공할 수 있다.

### 8.3 owner와 family-share는 별도로 렌더링한다

현재 owner Story의 title, subtitle, closing, chapter summary를 공유 package로 그대로 복사하면 자유문에 섞인 이름이 공유 이름 OFF 상태에서도 남을 수 있다.

v4에서는 중립 template과 구조화 person refs를 기준으로 다음 projection을 별도로 만든다.

- owner: 최신 `owner` 동의를 적용
- family-share: 최신 `family_share` 동의와 공유 생성 시 `이름 포함` 선택을 모두 적용

허용되지 않은 placeholder는 이름만 지우지 말고 문장 전체를 자연스러운 중립 문장으로 다시 렌더링한다. 동의 철회 시 기존 공유 revoke cascade를 유지한다.

## 9. 백필과 재분석 정책

### 9.1 1차: Apple alias 기반의 빠른 가치 제공

최신 Story의 Apple 추천 3장은 ranker 결과에 provider 인물 정보가 있다. 이를 이름으로 곧바로 공개하지 않고 다음 절차로 연결한다.

1. 기존 Apple 분석 결과에서 provider alias 후보를 private DB로 가져온다.
2. 이름·provider key를 일반 로그에 출력하지 않는다.
3. 사용자가 기존 PhotosMcp identity 중 하나와 연결하거나 새 인물로 확정한다.
4. 현재 Apple 추천 `local_asset_id`에 owner-confirmed association을 생성한다.
5. identity evidence hash를 갱신해 VLM 재분석 없이 Story revision을 새로 만든다.

이 경로가 가장 빠르게 실제 이름 badge와 Story 캡션을 확인할 수 있는 수직 기능이다. 인물 필터와 자연스러운 Story 본문은 Story v4를 도입하는 Phase 3에서 완성한다.

### 9.2 2차: Apple/local 안정 얼굴 관측과 기존 37건의 복구 가능성 분류

- 먼저 identity-capable backend preflight를 통과시킨다. detection-only MediaPipe는 사람 존재와 face box 신호용으로만 사용하고, 동일인 비교용 embedding backend·model fingerprint·dimension은 명시적으로 고정한다. 운영 표본의 얼굴 검출률 또는 embedding 생성률이 승인 기준에 미달하면 association 자동 승격을 중단하고 Apple alias와 owner review만 사용한다.
- preflight를 통과한 일반 Apple/local 분석의 stage 1에서 stable face observation을 private 저장소에 생성한다.
- 보관 완료 후 provider/photo lineage를 content-addressed `local_asset_id`와 연결한다.
- 과거 measurement와 과거 `job/photo → local asset` canonical lineage가 모두 남은 hold만 deterministic reconcile한다. 현재 증거가 없는 hold는 `missing`으로 유지한다. 보존된 대표 crop과 새 observation의 비교 결과는 이름 자동 계승이 아니라 owner-assisted relink 후보로만 제시한다.
- `ambiguous`, `missing`, `new`는 Android/macOS 검토 큐로 보낸다.
- 사용자가 한 exclude, split, move, merge 결정은 자동 모델 결과보다 우선한다.
- 다른 model family의 embedding을 직접 비교하지 않는다.

### 9.3 Story 삭제·재분석과의 관계

- 작업 기록 또는 Story를 지워도 identity, group, consent ledger는 지우지 않는다.
- 추천 로컬 사본이 제거되면 해당 local asset association을 `unavailable` 또는 `retired`로 전환한다. provider/source anchored observation과 identity 결정은 원본 부재, 모델 폐기 또는 사용자 삭제가 확인되지 않는 한 보존한다. local-only observation은 source availability를 `missing`으로 표시하되 lineage 감사 기록은 유지한다.
- 동일 기간 재분석은 새 recommendation collection과 Story를 만든다. 동일 content identity이면 기존 association을 재사용하고, content가 달라졌다면 lineage와 owner review를 거친다.
- 재결합이 확실하지 않으면 잘못된 이름을 표시하지 않고 `인물 연결 확인 필요`로 보여준다.
- Story의 identity evidence hash가 바뀌면 사진 VLM을 다시 돌리지 않고 presentation revision만 재생성할 수 있어야 한다.

## 10. 단계별 구현 계획

### Phase 0 — 진단과 생성 경로 일원화

목표: 왜 이름이 표시되지 않는지 사용자가 알 수 있고, 모든 Story 생성 경로가 같은 identity repository를 사용하도록 한다.

- owner-only readiness API 추가
  - 확정 identity 수
  - observation 수
  - 확정 association 수
  - pending/ambiguous lineage 수
  - Story별 인물 연결 성공/누락 사진 수
- 빈 인물 영역을 무조건 숨기지 않고 owner에게 `확정된 이름은 있지만 현재 사진 연결이 필요합니다` 안내와 검수 진입 제공
- 수동 Story 최초 생성, reconciliation, backfill, refresh 경로에 동일 identity repository 주입
- 이름·membership·alias·그룹·동의 변경 이벤트가 공통 Story refresh outbox를 사용하도록 일원화
- refresh 실패는 원래 Story를 깨뜨리지 않고 재시도 가능하게 기록

완료 기준:

- 현재 운영 데이터가 `이름 3 / observation 0 / association 0 / pending 37`로 안전하게 진단됨
- 새 Story가 일시적인 빈 identity manifest를 먼저 노출하지 않음
- identity 변경 후 Story revision이 한 번만 생성되고 조회 API·HTML·Android가 같은 revision을 봄

### Phase 1 — stable identity 원장과 Apple alias 수직 기능

목표: 최신 Apple 추천 사진에서 확인된 이름을 실제로 표시한다.

- private identity DB schema v2 migration
- migration은 `BEGIN IMMEDIATE` 단일 transaction으로 실행하고 성공 후에만 schema version을 올린다. 레거시 JSON은 read-only로 보존한다.
- provider alias 후보와 asset-person association tables 추가
- AppKit 레거시 JSON 직접 쓰기를 신규 SQLite application service로 전환
- 레거시 JSON은 read-only migration 입력과 비상 호환 용도로만 유지
- Apple `known_persons`를 private alias 후보로 전달
- macOS와 Android에 alias 연결·확정 화면 제공
- 최신 Apple 추천 자산 association 생성과 Story revision refresh

완료 기준:

- 최신 Apple 추천 중 사용자가 각 `name_only` alias와 사진 예시를 확인해 승인한 사진은 정확한 이름 badge와 Story 캡션에 등장
- 승인 전에는 실제 이름이 Story에 나오지 않음
- 이름이 같은 서로 다른 인물을 자동 병합하지 않음
- Google 추천 41장은 이 경로로 잘못 실명화되지 않음

### Phase 2 — routine Apple/local 얼굴 인덱스와 검수

목표: 이후 분석부터 인물 연결 자료가 자동으로 축적되고 사용자가 휴대폰에서도 정정할 수 있게 한다.

- identity backend availability, 고정 model fingerprint·dimension, 얼굴 검출률·embedding 생성률을 preflight와 운영 지표로 확인
- 승인된 Apple/local 분석에서 안정 observation 생성
- materialization 후 자산 association projection 생성
- 과거 canonical lineage가 남은 pending hold만 deterministic dry-run하고 증거 없는 hold는 owner-assisted relink 후보로 유지
- VLM `people_count`를 `PhotoCandidate.vlm_people_count → RankedPhoto.people_presence → photo_results schema → recommendation private projection`까지 전달하고, `is_family_photo`는 가족관계 확정 근거로 사용하지 않음
- Android 인물 확인 카드·얼굴 썸네일·원본 문맥 보기
- 같은 사람/다른 사람/분리/이동/제외/나중에/되돌리기 동작
- macOS와 Android가 같은 application service와 optimistic revision을 사용
- 사람 존재 사진 모아보기는 identity 확인 여부와 독립 제공

완료 기준:

- Android 수정 결과가 macOS와 Story에 같은 revision으로 반영
- 한 얼굴을 두 identity에 owner-confirmed로 연결할 수 없음
- 재분석 뒤 lineage가 확실하면 이름 유지, 모호하면 검토 상태
- 원본 사진은 인물 검수로 수정·삭제되지 않음

### Phase 3 — Story v4 인물 facet·필터·자연어 서사

목표: 인물을 Story의 실제 탐색과 서술 축으로 사용한다.

- `recommendation-story-v4` 및 v3 호환 projection 추가
- story-scoped facet handle과 사람별 asset index 생성
- HTML chip을 접근 가능한 filter button으로 변경
- Android 결과·Story·viewer에 인물 카드와 필터 추가
- LLM structured person template과 서버 audience resolver 구현
- 사용자 지정 가족 그룹·호칭을 owner 확인 정보로만 사용
- 인물별 대표사진은 가용한 얼굴 품질 신호를 사용한다. 표정·선명도·눈 상태는 신호가 검증된 경우에만 적용하고, 미가용하면 일반 quality·중복·날짜 다양성으로 fallback한다.
- 100장 batch 결과를 실행 전체 범위에서 통합해 같은 연사나 같은 인물 장면의 중복 추천 방지

완료 기준:

- 인물카드 장수, 필터 grid 장수, viewer indicator 장수가 모두 일치
- 사진에 없는 인물·미확정 관계·나이가 Story 문장에 생성되지 않음
- 특정 인물 Story가 일반 사람 등장 사진으로 몰래 채워지지 않음
- v3 Story는 기존 화면으로 정상 표시됨

### Phase 4 — 가족 공유·삭제·재분석 회귀 및 운영 승격

목표: 개인 Story의 이름이 공유 정책을 우회하지 않고 수명주기 전반에서 일관되게 동작하도록 한다.

- owner/family-share 별도 narrative projection
- 공유 전 이름·호칭 포함 미리보기
- 이름 OFF인 공유에서 title, subtitle, chapter, caption, alt, HTML data attribute까지 이름 누출 검사
- 이름 변경·merge·split·동의 철회 시 영향 Story refresh와 공유 revoke/reissue
- 삭제·재분석·작업 기록 비우기·추천 archive·외부 앨범 회귀 테스트
- 운영 DB dry-run, count-only 보고, 승인된 항목만 apply

완료 기준:

- family 이름 OFF 공유본의 이름 누출 0건
- 동의 철회 후 활성 공유 접근이 즉시 무효화됨
- 재분석 후 이전 이름 association을 잘못된 새 자산에 붙이지 않음
- 실패한 refresh가 기존 정상 Story나 원본·추천 자산을 삭제하지 않음

## 11. API와 클라이언트 변경안

### 11.1 owner API

```text
GET  /mobile-client/v1/people/readiness
GET  /mobile-client/v1/people/facets
GET  /mobile-client/v1/people/{gallery_handle}/photos
GET  /mobile-client/v1/stories/{story_id}/people/{facet_handle}/photos
GET  /mobile-client/v1/people/review-queue
GET  /mobile-client/v1/people/review-assets/{review_asset_handle}
POST /mobile-client/v1/people/review
POST /mobile-client/v1/people/undo
POST /mobile-client/v1/people/alias/confirm
POST /mobile-client/v1/stories/{story_id}/refresh-people
```

- 모든 mutation은 기존 device signature, nonce, idempotency, optimistic revision 규칙을 재사용한다.
- 전체 owner gallery에는 owner-session-bound `gallery_handle`, Story 조회에는 story/revision/session-bound `facet_handle`, mutation에는 TTL이 짧은 `action_handle`을 서로 다른 타입·정규식·권한으로 사용한다. 한 종류의 handle을 다른 범위에서 재사용하지 않는다.
- 얼굴 검수 파생 이미지는 owner 전용 서명 세션과 단기 `review_asset_handle`로만 제공하고 `Cache-Control: no-store`, 만료, revoke, path traversal 차단을 강제한다.
- 오류 응답은 `stale_revision`, `ambiguous_lineage`, `association_missing`, `unsupported_source_policy`처럼 사용자가 복구할 수 있는 단계 정보를 포함한다.

### 11.2 모바일 DTO

`schema_version == recommendation-story-v3` exact 비교를 capability 기반으로 바꾼다.

```text
capabilities:
  people_summary
  people_filter
  people_review
  structured_people_narrative
```

v4 owner DTO에는 다음 presentation 필드만 제공한다.

- story-scoped facet handle
- 표시 이름과 사용자 지정 호칭
- 대표 thumbnail URL
- 서로 다른 사진 수
- 해당 Story 안의 asset handles
- 확인 출처 표시: `직접 확인`, `Apple 정보 확인`, `연결 확인 필요`

유사도 값을 퍼센트 신뢰도로 표시하지 않는다. 보정되지 않은 거리 점수를 `97% 정확`처럼 보이게 만들지 않는다.

## 12. 테스트 전략

### 12.1 저장소·정책 단위 테스트

- 이름만 있음, association 없음, 동의 없음, membership 미확정 등 각 gate별 누락 이유
- owner-confirmed association 하나만 현재 projection에 포함되는지
- 동명이인과 동일 이름 alias가 별도 person ID로 유지되는지
- owner와 family-share 동의가 독립 적용되는지
- legacy hold unique/ambiguous/missing 분류와 사용자 결정 우선순위
- 자산 삭제·재생성·재분석 시 association revision 수명주기
- schema v2 migration의 transaction rollback, 반복 실행 idempotency, audit hash chain, DB `0600`·directory `0700` 권한
- 분석 중 이름 동시 수정, stale revision, 레거시 JSON cutover 중 장애에서 단일 원장 일관성 유지
- owner 검수 이미지 endpoint의 세션 범위, 만료·revoke, `no-store`, path traversal 차단

### 12.2 Story 계약 테스트

- repo → manifest → mobile DTO → owner HTML에 같은 이름·장수·자산 집합 노출
- LLM fallback 또는 Hermes 실패 시에도 서버 인물 caption 유지
- LLM이 허용되지 않은 ref나 관계 placeholder를 반환하면 거부
- 이름 변경·그룹 변경·동의 철회 후 identity evidence hash와 Story revision 갱신
- v3 legacy Story와 v4 Story의 병행 조회
- owner 이름이 family-share 중립 projection으로 누출되지 않는지

### 12.3 UI·통합 테스트

- 인물 chip 선택·해제·함께 나온 사진 필터
- 필터된 thumbnail → 확대 viewer → 좌우 flick → indicator 수 일치
- Android 수정 → 서버 revision → macOS/Story 반영
- stale revision 409에서 입력 보존과 재시도
- 오인식 수정 후 undo
- 100장 batch 경계를 넘는 동일인·동일 연사의 전역 중복 제거
- Story 삭제 후 identity 원장 보존, 재분석 후 올바른 재결합

### 12.4 운영 데이터 점진 검증

1. 최신 Apple 추천 3장만 read-only 후보 projection
2. 사용자 승인 1명·소수 사진으로 association 적용
3. owner Story 이름 badge와 결정적 Story 캡션 확인
4. family-share 이름 OFF/ON 각각 생성하고 누출 검사
5. 기존 37개 hold dry-run
6. 과거 canonical lineage가 남은 hold가 있는 경우에만 제한 apply하고, 증거 없는 항목은 owner-assisted 검토 상태 유지
7. Story v4에서 인물 필터와 자연어 문구 확인
8. 100장, 250장, 최대 1,000장 실행에서 전체 범위 재그룹 검증

## 13. 변경 예상 지점

| 영역 | 주요 파일 |
|---|---|
| private identity schema·projection | `src/photos_mcp/application/person_identity_repository.py` |
| 기존 얼굴 lineage | `src/photos_mcp/application/person_identity_lineage.py` |
| macOS 인물 관리 | `src/photos_mcp/application/person_identity_management.py`, `src/photos_mcp/interfaces/appkit/people/controller.py` |
| Apple/local 분석 전달 | `src/photos_mcp/vendor/photo-ranker/pipeline.py`, `src/photos_mcp/application/recommendation_storage.py` |
| Story v4 생성 | `src/photos_mcp/application/story_generation.py`, `src/photos_mcp/infrastructure/story_director/hermes_router.py` |
| 모바일 projection/API | `src/photos_mcp/application/mobile_client.py`, `src/photos_mcp/interfaces/http/mobile_client.py` |
| Story HTML·공유 | `src/photos_mcp/interfaces/http/story_web.py`, `src/photos_mcp/application/story_sharing.py` |
| Android | `android/location-bridge/app/src/main/java/com/photosmcp/locationbridge/MainActivity.java` 및 관련 모델·네트워크 계층 |
| 수명주기 | `src/photos_mcp/application/manual_curation.py`, recommendation/Story refresh outbox 경로 |

실제 구현 전 파일 책임을 더 작은 application service 단위로 나눠 `MainActivity.java`와 identity repository가 과도하게 비대해지지 않도록 한다.

## 14. 의도적으로 하지 않는 것

- 얼굴 유사도만으로 실명 자동 확정
- Apple의 이름 문자열과 PhotosMcp 이름 문자열이 같다는 이유만으로 자동 병합
- Google Photos 자산에서 얼굴 군집·신원 추론 정책 우회
- 사진 속 나이·성별·공동 등장으로 가족관계 추론
- owner 이름이 들어간 자유 문장을 문자열 치환해 공유본으로 전환
- 내부 identity ID, embedding, 유사도, 원본 경로를 Story·공유 API·로그에 노출
- 얼굴 crop을 Story 또는 공유 package에 포함. 단, owner 검수 API의 서명 세션·단기 opaque handle·`no-store` 파생 이미지는 허용
- 불확실한 lineage에 과거 이름 강제 계승
- 인물 기능 변경을 이유로 원본 사진 또는 정상 Story를 선삭제

## 15. 최종 수용 기준

1. 이름을 확정한 인물이 현재 추천 사진에 연결돼 있으면 Story 상단, chapter, 사진 상세에 일관되게 표시된다.
2. `인물이 담긴 사진`, 특정 인물, 함께 나온 사진을 별도로 모아볼 수 있다.
3. 사람 카드의 사진 수와 실제 필터 결과·viewer indicator가 일치한다.
4. 인물 이름이 자연스러운 Story 제목·문구에 들어가되, 해당 장면에 없는 인물이나 미확정 관계는 생성되지 않는다.
5. Android에서 오인식을 수정하고 되돌릴 수 있으며 macOS와 같은 결과를 본다.
6. 이름 또는 동의 변경만으로 사진 VLM을 재실행하지 않고 Story revision을 갱신한다.
7. Google 경로와 미확정 association에서 잘못된 실명이 생성되지 않는다.
8. 가족 공유 이름 OFF에서는 모든 텍스트·HTML 속성·다운로드 파생물에 이름이 없다.
9. 작업 기록·Story 삭제와 재분석 이후에도 identity·consent 원장은 보존되고, 잘못된 새 자산 연결은 발생하지 않는다.
10. 최신 운영 상태의 `확정 이름은 있으나 association 0건` 문제가 진단 화면에서 명확히 설명되고 검수 경로로 바로 이동할 수 있다.

## 16. 권장 다음 작업

첫 구현은 Phase 0 전체와 Phase 1의 Apple alias 수직 기능까지 하나의 작은 E2E로 묶는다.

```text
최신 Apple 추천 사진
  → Apple 인물 alias 후보 수집
  → 기존 PhotosMcp 인물과 사용자 연결
  → local_asset_id association 생성
  → Story revision refresh
  → Android/HTML 이름 badge 및 Story 캡션
```

이 흐름을 소수 사진으로 통과시킨 뒤 routine 얼굴 관측, Android 전체 검수, Story v4 자연어 서사로 확장한다. 이 순서가 현재 보존된 이름 정보를 가장 빨리 실제 사용자 가치로 전환하면서도 오인식과 공유 누출 위험을 제한한다.
