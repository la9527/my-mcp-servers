# 예외 중심 인물 자동 인식·Mac/Android 통합 계획

작성일: 2026-09-12

상태: 1차 운영 구현·Mac/Android 배포 검증 완료, 실제 교정률 관찰 중

적용 범위: PhotosMcp private identity 원장, 얼굴 품질 평가, 반복 인물 승격, 자동 인물 연결, Story 인물 근거, macOS 인물 관리, Android Companion 인물 관리

관련 문서: [운영 인물 인덱싱·재검증·통합 관리 UX 계획](17-operational-people-indexing-and-review-ux-plan-2026-09-12.md), [얼굴 단위 다인물 사진 검토·Android 이름 입력 구현 계획](18-face-level-multi-person-review-plan-2026-09-12.md)

## 1. 결론

인물 관리의 기본 작업을 검출한 모든 얼굴의 수동 분류에서 **예외 검토**로 전환한다.

```text
검출한 얼굴
  ├─ 품질 부족                         → 조용히 제외
  ├─ 새 얼굴 1~2회                     → 관찰 중, UI 숨김
  ├─ 새 얼굴 3회 이상                  → 새 인물 묶음 1건 검토
  ├─ 확정 인물과 매우 강한 일치         → 자동 연결
  ├─ 한 인물이 유력하지만 자동 기준 미달 → 빠른 확인
  └─ 두 인물 사이에서 애매함            → 비교 검토
```

사용자가 보는 기본 항목은 다음 두 종류로 제한한다.

1. 서로 다른 좋은 사진에서 3회 이상 등장한 새 사람에게 이름 붙이기
2. 기존 확정 인물 중 두 후보가 가깝거나 자동 적용 기준에 조금 못 미친 얼굴 확인하기

자동 연결은 Story와 사진별 인물 표시에 사용할 수 있지만, 다음 얼굴을 판단하는 학습 anchor에는 사용하지 않는다. 학습 anchor는 계속 사용자가 직접 확정한 `owner_confirmed` 얼굴만 사용한다.

## 2. 핵심 정책

### 2.1 기존 인물의 성숙도

| 좋은 사용자 확정 얼굴 | 동작 |
|---:|---|
| 0~2장 | 학습 중. 자동 연결하지 않고 필요 시 사용자 확인 |
| 독립 얼굴 3장 이상 | 빠른 확인 추천 가능 |
| 독립 얼굴 5장 이상, 촬영 문맥 3개 이상 | 초고신뢰 일치에 한해 자동 연결 가능 |
| 최근 사용자 교정 발생 | 자동 연결 일시 중지 후 shadow 재검증 |

`3장`은 최소 profile maturity이며, 초기 무인 자동 연결은 오염을 막기 위해 `5장`부터 허용한다. 운영 검증이 충분하면 versioned policy에서 기준을 낮출 수 있다.

### 2.2 새 인물의 반복 관측

```text
첫 관측  → observing, UI 숨김
둘째 관측 → observing, UI 숨김
셋째 독립 관측 + 품질/결속도 통과 → promoted_new_person
```

독립 관측은 다음 조건으로 계산한다.

- 서로 다른 content hash와 canonical local asset
- 동일 Apple/Google 원본은 1회
- 같은 연사·중복 묶음은 1회
- 최소 2개의 촬영 문맥 또는 날짜
- 같은 사진의 여러 crop은 반복 증거가 아님
- 동일 model family와 fingerprint의 embedding만 비교

새 인물은 얼굴별 검토 항목 3개가 아니라 품질이 좋은 대표 얼굴 3장을 포함한 묶음 검토 1개로 만든다.

### 2.3 얼굴 품질

| 등급 | 초기 정책 | 사용 |
|---|---|---|
| `auto_eligible` | detector ≥ 0.92, 짧은 변 ≥ 160px, 선명도·노출·pose·잘림 통과 | 자동 연결 후보 |
| `review_eligible` | detector ≥ 0.82, 짧은 변 ≥ 96px, 사람이 식별 가능한 수준 | 빠른/비교 검토 |
| `quality_suppressed` | 작은 얼굴, 심한 흐림·가림·측면·잘림, 낮은 검출 점수, screenshot | 기본 UI와 Story 이름에서 제외 |

정확한 선명도와 pose 임계값은 224px 정규화 crop과 실제 가족 사진 shadow 결과로 보정한다. 품질 제외는 `not_a_face`와 다르다. 원본이나 observation을 삭제하지 않고 정책 개선 시 재평가할 수 있게 reason code와 policy version을 남긴다.

### 2.4 자동 연결

초기 `auto_apply` 조건은 다음을 모두 만족해야 한다.

- identity/name이 사용자 확정 상태
- 인물별 자동 연결 설정이 켜짐
- 좋은 `owner_confirmed` anchor 5장 이상
- 서로 다른 촬영 문맥 3개 이상
- 대상 얼굴이 `auto_eligible`
- 최고 유사도 ≥ 0.82
- 상위 3개 robust 유사도 ≥ 0.76
- 2순위 후보와 margin ≥ 0.12. 비교 가능한 다른 확정 인물이 전혀 없으면 robust ≥ 0.86
- 고품질 지지 asset 3개 이상
- 현재 model fingerprint와 policy version 일치
- 같은 사진 안 동일 identity 중복 없음
- 사용자 교정, provider hint, 기존 결정과 충돌 없음

자동 결과는 `owner_confirmed`가 아니라 `auto_accepted`로 기록한다.

```text
다음 인식 anchor = owner_confirmed만
Story 유효 인물 = owner_confirmed + 유효한 auto_accepted
```

자동 결과가 잘못됐다고 사용자가 수정하면 연결을 해제하고 negative evidence를 기록하며 해당 인물 자동 profile을 일시 중지한다. Story refresh outbox와 Mac/Android revision도 함께 갱신한다.

## 3. 사용자 화면

### 3.1 인물 관리 홈

기본 CTA를 검출량이 아니라 예외 수로 표시한다.

```text
확인할 내용이 2개 있어요
애매한 얼굴과 새로 자주 보이는 사람만 확인하면 됩니다.
[빠르게 확인하기]
```

인물 카드는 대표 얼굴 하나, 이름, 연결 사진 수, 최근 자동 연결 수, 자동 인식 상태만 표시한다. 익명 singleton/duo, 저품질 얼굴, 내부 cluster 수는 기본 화면에서 숨긴다.

### 3.2 빠른 확인

현재 얼굴 1장과 확정 참고 얼굴 최대 3장만 보여준다. 전체 사진은 `사진 전체 보기`에서만 로드한다. 기본 동작은 `맞아요` 하나이며 다른 사람, 모르는 사람, 나중에를 보조 동작으로 둔다.

### 3.3 애매한 두 후보 비교

두 후보를 같은 시각적 무게로 표시하고 어느 쪽도 미리 선택하지 않는다. 선택 전에는 사진이나 Story에 이름이 반영되지 않는다.

### 3.4 새로 자주 보이는 사람

서로 다른 사진의 대표 얼굴 3장을 한 번에 표시한다. 이름 등록, 기존 인물 연결, 아는 사람 아님, 나중에를 제공한다. `아는 사람 아님`은 얼굴 한 장이 아니라 반복 cluster 전체를 억제한다.

### 3.5 인물 상세

대표 얼굴 한 장과 최근 연결 사진만 기본 노출한다. 자동 인식 토글, 직접 확인 anchor 수, 최근 자동 연결, `이 사람 아님`, Story 이름 표시 동의를 제공한다. 전체 얼굴 grid와 병합·분리는 고급 정리로 이동한다.

## 4. Android 동등 기능

Android는 서버가 제공하는 동일 projection과 command를 사용한다. 기기에서 별도 얼굴 인식이나 독립 상태 판단을 수행하지 않는다.

| 공통 기능 | macOS | Android |
|---|---|---|
| 예외 수와 관리 인물 | 인물 관리 홈 카드 | 인물 탭 요약 카드·Lazy/scroll list |
| 빠른 확인 | 표준 `NSWindowController` | 전용 Activity의 단일 카드 |
| 두 후보 비교 | 동일 검토 창 상태 | 세로 스크롤 2개 후보 카드 |
| 새 인물 3장 확인 | 검토 창 상태 | 가로 3장 evidence + 이름 입력 dialog/sheet |
| 대표 얼굴 포함 인물 선택 | `NSSheet` | Material-style full-screen dialog/list |
| 자동 인식 토글 | 인물 상세 | 인물 상세 dialog/activity |
| 최근 자동 연결 수정 | 인물 상세 activity | 인물 상세 activity |
| 실행 취소 | `NSUndoManager`, banner | Snackbar/Toast 대체 + 서버 undo command |

Android는 기존 Java programmatic UI와 하단 네비게이션을 유지한다. 최소 48dp touch target, system status/navigation bar 유지, 회전 시 review handle·선택·draft 보존, `contentDescription`을 필수로 한다.

Mac과 Android의 문구·상태·정렬 순서·revision은 동일하게 유지하고 화면 밀도만 플랫폼에 맞춘다.

## 5. 데이터·API

schema v6은 기존 이름, 동의, 확정 얼굴, 대표 얼굴, audit을 삭제하지 않는 additive migration으로 만든다.

주요 projection/원장:

```text
face_quality_versions
candidate_identity_evidence_versions
identity_automation_profile_versions
automatic_identity_assignment_versions
current_effective_memberships
```

공통 application service:

```text
people_dashboard()
list_exception_reviews()
exception_review_detail()
confirm_quick_match()
resolve_ambiguous_match()
name_promoted_identity()
set_identity_auto_enabled()
list_automatic_activity()
correct_automatic_assignment()
undo_people_decision()
```

모바일 API는 내부 ID, 파일 경로, embedding, raw similarity를 내보내지 않는다. 기기 바인딩 opaque handle, expected revision, idempotency key, ECDSA 서명, nonce를 유지한다.

동기화 기준:

```text
people_generation
profile_revision
face_assignment_revision
review_revision
story_identity_evidence_hash
```

## 6. Story 반영

처리 순서를 다음으로 고정한다.

```text
사진 materialize
→ Apple/Google 중복 통합
→ GPS·날짜 동기화
→ screenshot 제외
→ 얼굴 품질 평가
→ 얼굴 인덱싱
→ 확정 인물 자동 match
→ 예외 검토 큐 생성
→ effective person evidence
→ Story 생성·렌더링
→ Mac/Android/공유 Story 동기화
```

Story에 허용되는 인물은 사용자 확정 이름과 audience consent를 보유한 identity의 `owner_confirmed` 또는 현재 정책에서 유효한 `auto_accepted` membership뿐이다. 후보, 관찰 중, 품질 제외, deferred, ignored는 이름을 표시하지 않는다.

공유 Story에는 얼굴 crop, confidence, 내부 ID를 포함하지 않는다. 이름 동의만 반영한다.

## 7. 반복 실행과 오염 방지

- 자동 연결 얼굴은 anchor count를 증가시키지 않는다.
- 동일 face/target/model/policy/anchor-set 판단은 새 revision과 outbox를 만들지 않는다.
- 모델 fingerprint가 바뀌면 자동 profile을 shadow 재검증한다.
- 같은 Apple/Google 원본은 content hash로 1개 자산으로 본다.
- 같은 burst는 1개 문맥으로 본다.
- 사용자 수정은 모든 자동 판단보다 우선한다.
- ignored unknown profile은 Story와 known-person anchor에 사용하지 않는다.
- 사진/Story 삭제는 별도의 `인물 정보도 삭제`가 없는 한 stable identity와 anchor를 삭제하지 않는다.

## 8. 단계별 구현

1. 기존 확정 인물의 대표 얼굴·실제 얼굴 수 projection과 지도 referrer 정상화
2. 얼굴 품질 측정·등급·제외 reason의 schema/service 구현
3. cross-run 잠복 candidate와 독립 관측 3회 승격
4. 더 강한 `auto_apply` 판정과 `auto_proposed` shadow 기록
5. 예외 수 중심 Mac 인물 홈과 세 상태 검토 창
6. Android 인물 홈·빠른 확인·애매함·새 인물·인물 상세 동등 UI
7. `auto_accepted`와 effective Story membership, 자동 연결 교정·undo
8. Mac/Android/Story 통합 회귀, 실제 앱·APK 빌드
9. 개인 Story shadow 결과 확인 뒤 자동 적용 활성화
10. 실제 교정률 gate 통과 뒤 가족 공유 Story로 확대

## 9. 수용 기준

- 95px 이하 또는 심하게 흐린 얼굴은 기본 검토 큐에 나타나지 않는다.
- 같은 연사 10장은 반복 관측 1회로 계산한다.
- Apple과 Google의 같은 원본은 반복 관측 1회로 계산한다.
- 새 얼굴 1~2회는 UI에 나타나지 않는다.
- 새 얼굴 3회·2개 문맥 이상이면 얼굴별 3건이 아니라 묶음 검토 1건이 생긴다.
- 충분히 성숙한 인물의 초고신뢰 얼굴은 자동 연결되고 검토 큐에 나타나지 않는다.
- 품질 미달 또는 후보 margin 부족은 자동 연결되지 않는다.
- 한 사진의 두 얼굴에 같은 identity가 자동 배정되지 않는다.
- `auto_accepted` 얼굴은 owner-confirmed anchor query에 포함되지 않는다.
- 자동 연결을 수정하면 profile이 일시 중지되고 Story와 양 앱이 갱신된다.
- Mac과 Android가 같은 예외 수, 이름, 자동 상태, 최근 기록을 표시한다.
- 재분석해도 동일 후보·자동 결정·outbox가 중복 생성되지 않는다.

운영 목표는 기존 대비 검토 큐 60~80% 감소, 자동 연결 false positive 수용 테스트 0건, 자동 연결 얼굴의 anchor 유입 0건, 사용자 수정 후 같은 오인식 재발 0건이다.

## 10. 2026-09-12 구현 반영

### 10.1 공통 원장과 정책

- private identity 원장을 additive schema v6으로 올렸다. 기존 이름·동의·대표 얼굴·직접 확인 얼굴·감사 이력은 삭제하지 않는다.
- 얼굴 품질을 `auto_eligible`, `review_eligible`, `quality_suppressed`로 버전 기록하고 screenshot·작은 얼굴·심한 흐림·노출·pose·잘림을 기본 큐에서 제외한다.
- 1~2회 새 얼굴은 latent observation으로 유지하고 UI에서 숨기며, 서로 다른 사진 3장·문맥 2개 이상에서만 `promoted_new_person` 한 건으로 승격한다.
- 승격된 묶음의 이름을 한 번 확인하면 대표 얼굴만이 아니라 묶음의 모든 적격 얼굴을 `owner_confirmed` anchor로 전환한다.
- 자동 판단은 별도 `auto_accepted` 원장에 저장한다. Story에는 유효한 자동 판단을 사용할 수 있지만 다음 인식의 anchor query에는 포함하지 않는다.
- v6 이전에 확정된 인물은 삭제·재등록하지 않고 기존 직접 확인 얼굴의 주 model fingerprint로 자동 인식 profile을 한 번 파생한다.
- 구형 `new_face_candidate` 단발 항목은 감사·재인덱싱을 위해 보존하되 현재 예외 큐와 숫자에는 포함하지 않는다.

### 10.2 macOS 앱

- 상단의 기본 행동을 후보 전체 보기에서 `빠르게 확인`으로 바꾸고 현재 유효한 예외 수만 표시한다.
- 빠른 확인은 얼굴 crop을 우선 보여주며, 새로 자주 보이는 사람은 서로 다른 사진의 근거 얼굴을 최대 3장 나란히 보여준다.
- 한 번의 이름 입력으로 반복 얼굴 묶음 전체가 연결되고 Story 인물 projection이 즉시 갱신된다.
- 확정 인물 상세에 대표 얼굴, 직접 확인 수와 성숙도 기반 자동 인식 토글을 추가했다.
- 내장 Story URL은 loopback 대신 `PHOTOS_MCP_OWNER_STORY_URL`의 Tailnet HTTPS 주소를 우선한다. Google Maps Embed 키의 운영 referrer와 일치해 `127.0.0.1` 거부 화면을 피한다.

### 10.3 Android 앱 0.8.3

- 설정의 `인물 확인 현황`에서 Mac과 동일한 예외 수·빠른 확인·애매한 후보·반복 새 인물 수를 표시한다.
- `PeopleReviewActivity`는 얼굴 crop 우선, 원본 사진 접기/펴기, 한 사진의 여러 얼굴 전환, 기존 인물 선택, 새 이름, 모르는 사람 무시, 얼굴 아님, 나중에를 제공한다.
- 반복 새 인물은 서버가 제공한 서로 다른 근거 얼굴 3장을 표시하고 한 번 저장하면 묶음 전체가 연결된다.
- 기존 인물 선택 dialog에는 이름만 두지 않고 대표 얼굴과 연결 사진 수를 함께 표시한다.
- 인물 카드에는 실제 대표 얼굴, 연결 사진 수, 자동 인식 성숙도·직접 확인 장수·토글, 개인 Story/30일 가족 공유 이름 동의를 표시한다.
- 자동 인식 변경은 기기 바인딩 opaque handle, identity/profile revision, ECDSA 서명, nonce, idempotency key를 사용하는 owner command로만 허용한다.
- Android는 별도의 얼굴 DB를 만들지 않는다. Mac과 같은 서버 projection을 다시 불러오므로 양쪽에서 이름·예외 수·자동 상태가 같아진다.

### 10.4 운영 확인 결과

- 기존 운영 DB는 schema v6으로 열렸고 확정 인물 3명과 직접 확인 얼굴은 보존됐다.
- 기존 3명 모두 직접 확인 얼굴 5장 이상으로 `auto_ready` profile이 파생됐으며 Android 실기기에서 `자동 인식 · 안정됨`과 직접 확인 장수가 표시됐다.
- 구형 단발 후보 67건은 삭제되지 않았지만 새 예외 홈에서는 0건으로 숨겨졌다.
- Android 0.8.3(versionCode 22)을 SM-F966N에 `adb install -r`로 설치해 기존 등록을 유지했고 홈·설정·인물 관리 진입과 crash buffer 무오류를 확인했다.
- Tailnet Story는 지도 iframe을 포함해 HTTP 200으로 열렸고 같은 Embed 요청은 Tailnet referrer에서 허용, loopback referrer에서 403 거부됨을 확인했다.
- 상세 증거는 [예외 중심 인물 인식 Mac/Android 구현 검증](../../08-reports/01-validation/49-exception-only-people-recognition-mac-android-implementation-2026-09-12.md)에 기록한다.

### 10.5 운영 중 관찰할 항목

구현이 아니라 실제 개인 사진 표본과 사용자 판단이 필요한 다음 항목은 운영 gate로 남긴다.

- 새 분석에서 자동 연결된 얼굴의 오인식 여부와 인물별 교정률
- 1~2회 latent 얼굴이 다른 날짜의 셋째 사진에서 한 묶음으로 승격되는지
- 모델 fingerprint 교체 시 기존 profile을 자동 적용하지 않고 재검증하는지
- 사용자 교정 후 동일한 오인식이 재발하지 않는지

이 관찰 결과가 없는데 임계값을 낮추거나 자동 결과를 학습 anchor로 승격하지 않는다.
