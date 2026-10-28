# Story 기능·도메인 재설계 독립 감사

작성일: 2026-10-06 · 담당: Story · 목적: 코드 수정 전 증거 기반 전반 재설계 및 개선 계획

## 1. 감사 경계와 판단 기준

대상 저장소는 `/Volumes/ExtData/01_Projects/PhotosMcp/photos-mcp`이다. 이 문서만 새로 작성했다. 시작 시 수정되어 있던 `src/photos_mcp/interfaces/appkit/people/controller.py`는 읽기 대상에만 포함했고 수정·되돌리지 않았다. 사진 라이브러리·개인 사진·운영 DB·키 파일을 열거나 출력하지 않았고, 실제 AI 요청, 사진 쓰기, 클라우드·키체인 변경, 설치·서비스 재시작을 수행하지 않았다. `docs/99-archive`는 현재 계약의 근거로 사용하지 않았다.

증거 표기는 다음과 같다.

- **실행 확인**: 이번 감사에서 합성 데이터와 임시 SQLite DB로 직접 실행했거나 기존 테스트를 실행한 결과. 설치 앱·실기기 결과와 다르다.
- **코드 확인/추론**: 현재 checkout의 호출·저장·렌더링 경로에서 확인한 동작 또는 그 경로가 만드는 조건부 결과. 운영에서 이미 발생했다고 단정하지 않는다.
- **문서 보고**: 기존 검증 문서에 기록된 과거 결과. 이번에 재현하지 않았다.
- **미검증**: 설치 앱, 브라우저, 실제 Android, 권한 철회, 대규모·동시성 등 이번 범위에서 실행하지 않은 부분.

심각도는 P0=잘못된 사진 공개 대상 등 최우선 개인정보/핵심 계약 문제, P1=범위·보존·복구·사실성·핵심 작업 완결성 문제, P2=발견성·상태 전달·확장성·제한 안내 문제로 사용한다. 설계 대안은 향후 구현 제안이며 승인 또는 구현 완료를 뜻하지 않는다. 아래 줄번호는 이번 감사 시점의 소스 기준이다.

## 2. 실제 제품 객체와 도달 경로

현재 Story는 사용자가 직접 쓰고 관리하는 편집 문서보다 **추천 보관소의 사진을 날짜별로 묶어 보여 주는 manifest와 감상 presentation**에 가깝다. `story_manifests`는 story_id당 현재 JSON 하나, `story_presentations`는 시각 테마의 현재 JSON 하나, `shared_story_packages`는 공유 당시 JSON을 저장한다. 추천 세대 이력은 별도 존재하지만 Story 편집 이력과 같지 않다.

| 사용자 목표 | 현재 실제 진입/호출 경로 | 상태와 한계 |
|---|---|---|
| 날짜로 생성 | Mac Story WKWebView → `/photos/story/manual`; Android native 날짜 입력 → preview/GPS → signed manual operation | 양쪽 구현. Mac 폼은 preview 없이 바로 queue; 공통 작업·GPS 대기 경로 존재 |
| 완료 분석에서 생성 | AppKit 결과 `이 결과로 Story 만들기` → `createStoryFromResult_` → `create_story_from_completed_result` | 현재 구현·연결 있음. 미구현으로 판정하면 틀림. 선택 충돌·내구성·완료 안내 문제는 별도 |
| 추천/사진 선택 | AppKit 결과에서 장면 베스트 또는 명시 선택; Android 날짜 생성에서 구성 선택 | 생성 전 선택 있음. Story 안에서 사진 추가/제거하는 편집은 확인되지 않음 |
| 감상 | Mac Story 탭 WKWebView/브라우저; Android 목록 → bootstrap → WebView; 공개 공유 → 코드 입력 | 실제 코드 연결. 설치본·네트워크·실기기 이번 미검증 |
| 스타일 선택·저장 | 공통 renderer → 테마 preview → presentation POST | 5개 현재 테마, 별도 revision 및 낙관적 충돌 보호. 본문 편집 저장과 다름 |
| 제목/본문/표지/장면 편집 | 생성된 title/subtitle/closing/chapters 렌더링 | 사용자 편집 화면·명령·초안 모델을 확인하지 못함 |
| 순서 변경 | build_story_evidence 날짜/해시 정렬; AI chapter refs; 장소 그룹 렌더링 | 사용자 재정렬 없음. 생성 전 순서 보존도 불완전 |
| 삭제 | Mac 상세 POST delete; Android 목록 확인창 → signed delete → soft_delete_story | 원본 보존·공유 폐기 구현. 복원 없음; 자동 재생성 및 500개 공유 상한 취약점 |
| 저장/재개 | 생성 후 manifest 자동 저장; 테마 별도 저장; manual operation/GPS 대기 영속화 | 편집 초안/읽던 위치 복구 없음. 완료 결과 생성은 별도 메모리 thread |
| 재분석 | 원래 manual reanalysis_spec으로 새 작업 생성 | 날짜 작업은 복원 가능. 완료 결과 Story까지 manual로 잘못 표시되는 경로 있음 |
| 공유 | Mac owner 상세의 `/photos/share`; 공유 종료·기간·코드·이름 opt-in | 구현됨. 선택 Story와 실제 공유 대상 불일치가 가장 큰 결함 |
| 내보내기 | AppKit 완료 결과 JSON/사진 내보내기; 공유 뷰어에서 개별 JPEG 저장 | Story 전체 문서/순서/캡션/출처를 포함하는 export는 확인되지 않음 |
| 모바일 관리 | native 목록 삭제/재분석 + 공통 WebView 테마 저장 | 공유 관리·편집 parity 없음. 읽기 세션은 Story ID에 묶여 있어 경계 자체는 존재 |

핵심 경로 증거:

- `src/photos_mcp/interfaces/appkit/results/controller.py:383-486,852-858`: 완료 결과 생성 확인창·worker·Story 탭 이동·버튼 연결.
- `src/photos_mcp/interfaces/appkit/main/controller.py:670-684,724-758`: Story는 실제 PyObjC WKWebView와 새로고침/브라우저 열기이다. SwiftUI 앱으로 간주하지 않는다.
- `src/photos_mcp/interfaces/mcp/server.py:465-559,615-723,741-841`: owner 목록·상세·날짜 생성·재분석·삭제·공유·전역 refresh route.
- `src/photos_mcp/interfaces/http/mobile_client.py:2712-2739,2809-2867,2870-2915`: 모바일 목록/상세·bootstrap·WebView·테마 저장.
- `android/location-bridge/app/src/main/java/com/photosmcp/locationbridge/MainActivity.java:1491-1519,1615-1627,1783-1833`: Java Android View 목록과 WebView 연결. Compose 전환을 전제하지 않는다.
- `src/photos_mcp/infrastructure/persistence/run_repository.py:369-417,1965-2190`: 현재 저장 구조.

## 3. 이번 직접 검증

### 3.1 기존 테스트

실제 AI가 켜지지 않도록 `PHOTOS_MCP_STORY_DIRECTOR_ENABLED=0`을 강제했다. pytest cache provider를 비활성화했다. 합성 fixture/임시 파일 기반 테스트이며 실제 사진·운영 Story를 대상으로 하지 않았다.

```bash
PHOTOS_MCP_STORY_DIRECTOR_ENABLED=0 .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_story_generation.py tests/test_story_sharing.py tests/test_story_presentation.py \
  tests/test_story_gallery.py tests/test_story_spatial.py tests/test_story_book.py \
  tests/test_result_story_service.py
# 71 passed in 1.83s

PHOTOS_MCP_STORY_DIRECTOR_ENABLED=0 PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_manual_curation.py tests/test_mobile_client.py -k 'story or webview or prefetch or handoff'
# 15 passed, 34 deselected in 0.42s
```

전체 회귀, Android build/lint, AppKit 실제 클릭, 브라우저 제스처, 설치본 검증으로 확대 해석하지 않는다.

### 3.2 독립 합성 실험

TemporaryDirectory에 새 RunRepository를 생성하고 가짜 추천 asset/member만 넣었다. 원본 파일을 열지 않았다. `_persist_revision`, `ensure_scoped_story`, `soft_delete_story`, `list_story_manifests`, `_model_manifest`를 직접 호출했다. AI client는 호출하지 않았다. 출력은 다음 boolean/count만 남겼다.

| 실험 | 결과 | 연결 ID |
|---|---|---|
| 같은 evidence_hash/source/schema/scope에서 제목만 바꾼 manifest 저장 | `same_evidence_new_title_ignored=True` | STORY-06 |
| scoped Story 삭제 후 같은 collection에 합성 asset 추가, ensure 다시 호출 | `changed_evidence_revives_deleted=True` | STORY-07 |
| 205개 이상의 합성 Story 저장 후 `list_story_manifests(limit=500)` | `list_limit_500_actual_count=200` | STORY-08 |
| refs/date/coverage는 유효하고 근거 없는 방문·관계·행동 문장을 가진 direction을 `_model_manifest`에 입력 | `unsupported_prose_accepted=True` | STORY-10 |

재현 절차: 임시 DB에 collection `c`와 날짜 동일 asset `a`를 등록 → ensure story `s` → 제목만 변경하여 persist → soft delete → asset `b`를 동일 collection에 추가 → ensure → 합성 manifest 205개 추가 → list 500 → build_story_evidence의 refs/date를 유지하고 자유 텍스트만 근거 없는 문장으로 바꿔 `_model_manifest` 호출. 임시 DB는 실험 종료 후 삭제되었다. 이는 해당 함수의 현재 동작 증명이며 실제 앱에서의 자동 호출 시점은 아래 코드 추론과 구분한다.

## 4. 발견 사항: 객체·범위·생성

### STORY-01 · P0 · 다른 Story를 공유할 수 있는 대상 결합 오류

**코드 확인.** `story_web.py:836-840`의 상세 공유 폼은 `/photos/share`로 보내며 story_id/revision이 없다. `interfaces/mcp/server.py:757-758`는 항상 `list_story_manifests(limit=1)`의 첫 항목을 선택한다. 상세 A를 보고 있더라도 최근 갱신 B가 있으면 B가 공유된다. 상세 GET은 A를 정확히 읽는다(`server.py:541-547`), 따라서 UI 선택과 POST 대상이 실제로 분리되어 있다.

재현 조건: 사진이 있는 Story A/B를 저장하고 B를 마지막 갱신 → `/photos/stories/A` 열기 → 공유 생성. 실제 공개 공유 생성은 이번에 하지 않았다.

대안: `POST /stories/{story_id}/shares` + `expected_story_revision` + 대상 사진/기간/이름/위치 요약. latest fallback 금지. 수용: A 화면에서 B가 갱신되어도 A의 확정 revision만 공유하며, 삭제·revision 충돌은 409/명확한 재확인으로 끝나고 공유 패키지 0개를 만든다.

### STORY-02 · P1 · 상세의 이야기 재구성도 전역 객체를 변경

**코드 확인.** 상세에 항상 삽입되는 `story_web.py:841` 폼은 `/photos/story/refresh`; `server.py:836-840`는 `refresh_recommendation_story(force=True)`를 부른다. 대상은 `story_generation.py:17`의 `recommendations-latest`이며 현재 상세 scoped Story가 아니다.

재현: scoped Story에서 ‘Linux Qwen으로 이야기 새로 구성’ 선택. 현재 상세는 그대로인데 전역 Story가 생성·갱신될 수 있다. 이번 실제 AI 미실행.

대안: ‘이 Story의 글 다시 제안’과 ‘원본 다시 분석’을 별도 scoped command로 제공. 전역 추천 모음은 독립 화면/객체로 취급. 수용: 대상 Story ID/근거 revision/수행 종류가 UI와 작업에 일치, 기존 본문은 새 제안 승인 전 유지.

### STORY-03 · P1 · 선택 변경이 다른 초안이 아니라 같은 collection/Story로 수렴

**코드 추론.** `recommendation_storage.py:289-294`의 collection ID는 analysis_run_id+policy_version이고 실제 명시 선택 사진 목록을 포함하지 않는다. 같은 작업의 명시 선택은 항상 같은 policy(`:796-797`). member는 upsert만 한다(`run_repository.py:1187-1232`); 이번 선택에서 빠진 기존 member를 교체하는 삭제 경로는 materialize에 없다. `result_story_service.py:38-58` snapshot hash도 job/collection IDs/selection_policy만 포함하고 사진 ID·순서·결과 revision은 없다. Story ID는 그 hash에서 나온다(`:97-98`).

재현: 같은 완료 작업에서 {A,B} 선택 생성 → {C} 선택 생성. 두 명시 선택의 collection/snapshot/Story ID가 같고, 잔존 member 때문에 A/B/C가 섞일 가능성이 있다. 이번 materialize의 실제 파일 복사는 실행하지 않았으므로 전체 경로 결과는 추론이다.

대안: 선택 snapshot에 ordered source photo refs, mapped asset refs, result revision, 정책 version, 실패 목록과 hash를 고정. collection은 세대 단위 불변 membership; 사용자가 별도 문서 생성/기존 문서 갱신을 선택. 수용: {A,B}와 {C}는 서로 다른 snapshot; 이전 Story 비파괴; 재시도는 같은 command ID에서만 동일 객체 반환.

### STORY-04 · P1 · 사용자/촬영 순서 대신 해시·장소 묶음 순서

**코드 확인.** `story_generation.py:444-453`는 날짜와 photo_ref hash로 정렬한다. 명시 선택은 `recommendation_storage.py:783-795`에서 순서 있게 전달해도 Story에는 ordered source snapshot이 없다. renderer는 chapter 안을 location_groups 순서로 다시 묶는다(`story_web.py:488-511`); 장소 A/B/A가 A/A/B가 될 수 있다. 공유 photos sequence(`story_sharing.py:152-164`)와 화면 chapter refs 순서도 별도이다.

대안: StoryItem.position을 단일 정렬 원천으로 만들고 촬영 시각/원본 선택 순서/수동 순서를 명시. 장소는 facet 또는 heading이며 자동 재배치하지 않는다. 수용: 같은 날 역순 선택, A/B/A, 동일 날짜·중복 content, 날짜 미상에서 Mac/mobile/viewer/export/share 순서 일치; 자동 정렬 변경은 미리보기와 명시 적용.

### STORY-05 · P1 · ‘Story 만들기’ 이후 편집·초안·선택 수정 작업이 빠짐

**코드 확인/기능 공백.** `story_web.py:730-894` 관리 도구는 생성/테마/공유/재분석/삭제에 한정된다. title/subtitle/closing/chapter는 `story_generation.py:543-564,629-660`에서 자동 생성 후 표시하며 제목/본문/표지/사진 추가·제거/장면 분할·병합/순서 편집 route가 확인되지 않았다. generation status ready는 편집 검토 완료가 아니다.

대안: 생성 결과를 초안으로 시작, 편집/감상 별도 모드. AI 문장은 제안, 사용자가 확정한 문장은 보호. 최소 MVP는 제목·표지·caption·사진 제외·키보드/버튼 재정렬·undo·초안 저장. 수용: AI 없이 수동 Story 완성 가능; 사진 제거는 Story 참조만 바꾸고 원본·다른 Story에 영향 없음; 미저장/저장중/저장됨/충돌 표시와 앱 재시작 복구.

### STORY-06 · P1 · revision 숫자는 있지만 편집 버전·복구 저장소가 아님

**실행 확인 + 코드 확인.** `_persist_revision`은 evidence/source/schema/scope가 같으면 본문 변경 여부를 보지 않고 기존 manifest를 반환(`story_generation.py:671-680`). `run_repository.py:1975-1981`는 story_id의 현재 JSON을 덮어쓴다. 기존 revision JSON을 조회·복구하는 테이블/명령이 없다. presentation에 있는 expected_revision 보호(`run_repository.py:2073-2121`)와 Story 본문 보호는 다르다.

대안: append-only StoryRevision, parent_revision, content_hash, author_kind, expected_revision/CAS. AI 재생성·위치 갱신·사용자 편집을 분리하고 이전 버전 복구는 새 revision 생성. 수용: 같은 근거에서 제목 변경 저장; 동시 편집 한쪽 409; 원문/사진 순서 복구; 기존 revision 및 공유가 보존됨.

### STORY-07 · P1 · 삭제 tombstone을 생성 경로가 보호하지 않음

**실행 확인.** `soft_delete_story`는 status deleted를 저장(`manual_curation.py:272-282`). 그러나 `ensure_scoped_story`는 기존 status를 검사하지 않고 evidence 변경 시 ready fallback을 persist(`story_generation.py:805-835`). 합성 실험에서 삭제 후 collection 자산 추가 시 ready로 되살아났다. `rebuild_all_stories_after_asset_change`는 deleted를 건너뛰지만(`:894-898`), 동일 ID 생성 재요청·직접 ensure·전역 refresh 전체가 tombstone을 존중한다는 보장은 없다.

대안: 삭제는 독립 tombstone 및 revision guard; 생성/upsert로 복원 불가; 복원 command만 허용. 수용: 삭제 후 늦은 생성 응답·재시도·근거 변경·자동 refresh에도 숨김 유지, 명시 복원에서만 새 revision; 복원 시 폐기된 공유는 재활성화하지 않음.

### STORY-08 · P1 · 전체 갱신 500개 요청이 200개로 잘림

**실행 확인.** `refresh_all_story_location_projections`와 `rebuild_all_stories_after_asset_change`는 limit=500(`story_generation.py:848,894`)이지만 repository는 최대 200으로 clamp(`run_repository.py:2054`). 합성 205개 저장 후 실제 200개 반환. 인물/위치 변경 시 오래된 Story가 갱신 대상에서 빠질 수 있다. 인물 철회 outbox 완료를 실제로 처리하는 `interfaces/http/mobile_client.py:1946-1959` 같은 호출과 함께 검토해야 한다.

대안: 변경 asset/person 기반 affected-story index + cursor batch + 실패 재처리 outbox. 수용: 201개/1,001개 Story의 이름 철회·GPS 수정 모두 처리, 처리 수 대조, 일부 실패 시 outbox 완료로 판정하지 않음. 이 문제를 단순 화면 페이지 수 개선으로 끝내지 않는다.

### STORY-09 · P1 · 삭제/동의 철회/만료의 공유 열거 상한

**코드 추론.** Story 삭제는 공유 최대 500개만 순회(`manual_curation.py:284-295`); repository는 실제 최대 500(`run_repository.py:2173-2189`). 공유 접근 `StoryShareService.get_active`는 package status/expiry만 검사하고 부모 Story deleted를 확인하지 않는다(`story_sharing.py:351-371`). 501번째 이하의 오래된 active package가 남으면 삭제 후에도 열람 가능할 수 있다. 동의 철회 `revoke_for_person(:443-463)`, 만료 `expire_due(:375)`도 전수 처리 계약을 검토해야 한다.

대안: SQL 조건부 전수 revoke 및 person tag 역색인, access 때 parent tombstone/consent_epoch 검증. 수용: active 501개+다수 expired 섞인 합성 데이터에서 누락 0; 삭제/철회 직후 기존 session/asset request 차단. 실제 대량 공유 생성은 이번 미실행.

## 5. 발견 사항: AI 사실성·출처·개인정보

### STORY-10 · P1 · refs/date 검증과 사실 검증을 혼동

**실행 확인.** `_model_manifest`는 theme/refs/date/전수 coverage를 검증(`story_generation.py:583-627`)하지만 자유 텍스트는 `_safe_model_text`의 URL/일부 HTML 검사(`:567-570`, `_UNSAFE_TEXT :21`)만 통과하면 받는다. 근거 없는 방문·관계·감정·행동 텍스트를 합성 입력해 수락됨을 확인했다. prompt는 금지(`hermes_router.py:196-200`)하지만 서버 검증이 아니다. 서버 유래 confirmed_people 보호 테스트(`test_story_generation.py:313-340`)도 title/summary에 임의 이름을 쓰는 것을 막지는 않는다.

대안: CaptionClaim마다 evidence refs/field paths, source_kind(user/metadata/vision/inferred), confidence, factuality status를 저장. 날짜/장소/인물 facts는 서버 템플릿; 자유 문장은 ‘AI 제안’으로 사용자 검토. share projection은 prose도 공유 consent에 맞게 재구성/검사. 수용: 알려지지 않은 이름/관계/장소·감정은 ready fact로 확정되지 않음; 출처 클릭 가능; 거짓 사실·프롬프트 주입·철회 이름 포함 텍스트 fixture를 통과시킬 수 없음.

### STORY-11 · P1 · opaque ID만으로 AI 입력의 개인정보 위험이 제거되지는 않음

**코드 확인, 운영 미검증.** AI evidence에는 scene_description, 날짜, owner location label, quality/선택 이유가 포함(`story_generation.py:365-410,475-483`), 이름은 opaque refs로 제한(`:471-474`). director는 전체 evidence를 router로 전송(`hermes_router.py:202-225`). 입력 문장 자체의 이름·주소·민감 설명 redaction, 해당 Story별 송신 동의/미리보기는 이 경로에서 확인되지 않았다. 실제 송신 대상·설정값·보관 정책은 열지 않았다.

대안: 생성 모드 `수동/규칙 기반/AI 제안` 구분, data minimization projection, 원문 redaction·필드별 송신 정책, 실행 전 설명 및 취소. 수용: AI disabled는 실제 네트워크/준비 명령 0회; 민감 fixture는 송신 envelope에서 제거; 모델·prompt·input hash·privacy policy version·사용자 선택 기록. 로컬 router 사용과 외부 cloud 송신을 같은 것으로 단정하지 않는다.

### STORY-12 · P1 · 상세 GPS 공유 정책은 고정되어 있고 선택 가능한 공개 수준이 없음

**코드 확인, 현재 정책 위반으로 단정하지 않음.** `get_recommendation_asset_location(audience='share')`도 exact 좌표 반환(`run_repository.py:1773-1788`). 공유 패키지는 latitude/longitude/place_id를 복사(`story_sharing.py:169-177,235-240`)하고 public projection에 포함(`:500-505`). owner 공유 UI는 기간/다운로드/이름만 선택(`story_web.py:836-840`). 고정 `family_detailed` 정책(`story_sharing.py:291`)과 개인정보 안내(`story_web.py:909-912`)는 있으므로 ‘비밀 좌표가 우연히 새었다’고 기술하지 않는다. 개선 문제는 사용자에게 해당 Story의 정확 위치 공유를 선택·검토할 수단이 없다는 것이다.

대안: 공유별 `위치 숨김/지역만/상세 지도`와 날짜 정밀도·사진별 제외 옵션; 초기 미리보기에서 좌표 포함 여부 요약. 수용: 위치 숨김에서는 JSON/HTML/map URL에 좌표·place_id·상세 label 0개; 기존 detailed 공유는 자동 덮어쓰기 없이 명시 새 공유/폐기.

### STORY-13 · P2 · 지도 설명과 실제 로딩 시점 계약 불명확

**코드 추론.** 안내는 ‘지도를 열면’ Google에 전달한다고 표현(`story_web.py:911`). iframe src는 render 때 이미 들어가며 loading=lazy만 설정(`:431-436`), 위치 정보는 접힌 details 안에 있다(`:580-581`). 접힘 상태의 실제 lazy load는 브라우저별로 검증하지 않았다. 코드 수준에서 명시 사용자 클릭 뒤에만 src를 설정하는 계약은 없다.

대안: `지도 보기` 후 src 설정, 공유 privacy projection과 같은 policy 사용. 수용: 지도 미동의/미열람 때 third-party request 0; 클릭 후 전달 필드 설명; WKWebView/Android WebView/브라우저 네트워크 증거. browser용 Maps key와 서버 secret을 같은 종류로 취급하거나 키 값을 문서에 넣지 않는다.

### STORY-14 · P2 · 완료 결과 생성 호출에서 인물 저장소가 빠짐

**코드 확인.** 서비스는 identity_repository를 받지만(`result_story_service.py:61-69,104-119`), AppKit caller는 해당 인자를 전달하지 않는다(`results/controller.py:443-449`). build_story_evidence는 없으면 빈 identity를 사용(`story_generation.py:455-464`). 완료 결과에서 만든 Story는 첫 생성 시 확인된 인물 이름/facet이 날짜 작업 경로와 다를 수 있다. 나중 refresh로 보완되는지는 운영 미검증.

대안: 공통 StoryCommandService가 identity/privacy projection을 주입; UI가 저장소 구성을 알아야 하는 구조 제거. 수용: 동일 사진·동의의 날짜/완료 결과 Story에서 인물 facet 일치; AI에는 이름을 전달하지 않음; 동의 철회 즉시 반영.

## 6. 발견 사항: 저장·재개·플랫폼·관리

### STORY-15 · P1 · 완료 결과 생성은 durable operation이 아닌 daemon thread

**코드 확인.** AppKit은 메모리 `_story_worker`/daemon Thread만 사용(`results/controller.py:385,433-463`); 완료 결과 서비스는 materialize → snapshot → ensure → optional AI 순차 실행(`result_story_service.py:81-121`). 날짜 operation과 같은 영속 진행/취소/재개 명령이 없다. 앱 종료/창 교체/중간 오류 시 만들어진 fallback·snapshot·복사 자산과 UI 완료 인식이 분리될 수 있다.

대안: 공통 StoryOperation(command_id, idempotency_key, source_snapshot_id, phase, retryable_error, result_revision)와 단계 checkpoint. 수용: 각 단계 직후 프로세스 종료 → 재시작 시 이어가기/정리 선택, 복사 재사용, 중복 Story 0, 기존 Story 유지; AI 오류는 사진 복사 재실행 없이 재시도.

### STORY-16 · P1 · 일부 사진 저장 실패도 최종 완료 안내

**코드 확인.** 서비스는 storage `partial`을 성공 입력으로 받아(`result_story_service.py:88-90`) 반환 status를 무조건 completed(`:123-128`). AppKit은 실패 사진 수·누락 목록 없이 ‘Story를 만들었습니다’(`results/controller.py:475-485`). manual reconciliation은 partial/partial_timeout에서도 Story를 만들고 별도 operation 결과에 unfinished_count를 둔다(`manual_curation.py:594-627`); Story 본문 ready가 완전성 보증은 아니다.

대안: source completeness와 narrative readiness를 별도 상태로 관리. ‘요청 N/포함 M/실패 K’ 및 빠진 항목·재시도 제공; 부분본 게시·공유는 사용자 선택. 수용: 3개 중 1개 누락 합성에서 완전 성공으로 표시하지 않으며 실패 항목만 재시도, 원 선택 snapshot 보존.

### STORY-17 · P1 · 완료 결과 Story를 Android에서 재분석 가능 manual로 오인

**코드 확인/추론.** 모바일 projection은 scope.origin_run_id만 있으면 origin=manual(`application/mobile_client.py:379`). 완료 결과 service도 origin_run_id를 기록(`result_story_service.py:104-118`)하지만 reanalysis_spec은 없다. Android는 origin manual이면 재분석 버튼 생성(`MainActivity.java:1499-1513`). 서버는 spec 또는 manual operation 없으면 `story_scope_not_reanalyzable`(`manual_curation.py:220-231`). 날짜 없는 완료 결과 Story에서 잘못된 기간/실행 동작을 안내할 수 있다.

대안: origin_kind와 capabilities(can_reanalyze_source, can_regenerate_text, can_edit, can_share) 서버 파생. 수용: 완료 결과 Story는 ‘글 다시 제안’ 또는 명시된 결과 범위 재분석만 제공, 원본 날짜 범위 전체 재분석 버튼은 숨김; 버튼/서버 권한·범위 일치.

### STORY-18 · P2 · 모바일 오류 재시도와 뒤로가기에서 선택 Story/목록 상태 손실

**코드 확인.** openStory(storyId)는 ID로 bootstrap하지만(`MainActivity.java:1615-1627`) 오류 재시도는 `showStory()` → 빈 ID의 최신 Story(`:1446-1447,1841`). WebView에서 history 없으면 `showHome()`(`:2511-2519`), 직전 Story 목록 선택/스크롤로 돌아가는 경로가 아니다. 사진 viewer dialog 닫기 fallback은 구현되어 있다.

대안: selected_story_id/revision, list cursor/scroll, reader anchor를 native navigation state에 보존. 수용: 오래된 A 네트워크 실패 후 재시도는 A; viewer → 본문 → 직전 목록의 예측 가능한 Back; 회전·앱 재생성에도 상태 복구. 실기기 gesture/TalkBack 미검증.

### STORY-19 · P1 · Mac 삭제·재분석의 범위 확인/복구 UX 부족

**코드 확인.** owner delete/reanalyze는 단일 submit(`story_web.py:845-852`) 후 즉시 서버 mutation(`server.py:687-719`). Android는 삭제와 재분석에 설명·확인창을 제공(`MainActivity.java:1534-1600`). soft delete지만 테마까지 제거(`manual_curation.py:282`)하고 복구 command는 없다. 빈 Story에서는 controls 전체를 숨겨 삭제도 사라짐(`story_web.py:854-855`).

대안: 제목/포함 수/공유 종료 영향이 보이는 삭제 확인 및 복원 기간; 재분석은 기존 범위/새 분석 대상/예상 자원 설명. 빈 초안도 관리 가능. 수용: 취소는 DB mutation 0, 삭제는 참조·테마 복원 가능, 원본 불변, 공유 복원은 새 발행만.

### STORY-20 · P2 · 생성 성공 후 생성된 ID로 바로 열지 않음

**코드 확인.** completion은 story_id를 얻지만 `showTab_('story')`만 호출(`results/controller.py:475-485`). main WebView는 base portal URL(`main/controller.py:680-684`), 서버는 updated_at 최신 첫 Story를 보여 준다(`server.py:470-471`). 동시에 다른 Story가 갱신되면 사용자 작업과 다른 Story를 열 수 있다. 현재 저장 창의 보존/탭 재구성 동작은 직접 클릭 미검증.

대안: native openStory(story_id, revision) 명령·직접 detail URL. 수용: 동시 완료 A/B에서 A 알림을 누르면 A, 최신 목록 순서와 무관; URL fallback 없이 해당 ID 오류 표시.

### STORY-21 · P2 · 저장된 Story가 플랫폼별 고정 목록 제한 뒤로 사라짐

**코드 확인.** owner 목록 50(`server.py:470,553`), mobile 목록 20(`http/mobile_client.py:2718`), repository 최대 200(`run_repository.py:2054`). cursor/search가 이 경로에 없다. 직접 ID route는 있어도 UI에서 오래된 Story를 발견하기 어렵다. 모든 Story를 한 화면에 그리는 해결책은 STORY-08 전수 갱신과 분리한다.

대안: cursor 목록·검색·기간/출처/상태 필터 및 명시 정렬. 수용: 1,001개 목록에서 끝까지 도달/검색 가능, 중복·누락 없이 pagination, Mac/mobile 같은 filter contract.

### STORY-22 · P2 · 사진 availability가 실제 파일/권한 상태와 분리

**코드 확인.** mobile projection은 recommended_copy/preview를 항상 true(`application/mobile_client.py:286-290`). 실제 asset derivative는 실패 시 404(`http/mobile_client.py:2947-2955`), viewer는 닫고 다시 열라는 메시지(`story_experience.py:327-333`). 원본 Photos 권한, Google 접근 상태, 이미 복사한 managed asset availability를 구분하는 Story 도메인이 없다.

대안: SourceAccessState와 ManagedAssetState 분리; 원본 접근 철회 시 기존 복사본 보존/삭제 정책을 명시하고 미리보기 누락은 missing/corrupt/retryable로 표시. 수용: Apple 권한 거부/선택 권한 변경, Google 만료, 파일 손상, 복사본 존재를 각각 구분; 중복 AI 실행 없이 복구. 이번 실제 권한 변경 미실행.

### STORY-23 · P2 · 사진/결과 export와 Story export를 혼동할 수 있음

**코드 확인/공백.** AppKit JSON 내보내기는 result payload(`results/controller.py:362-373`), 공개 저장은 개별 공유 JPEG(`story_web.py:1059-1075`). Story title/chapter/순서/caption/출처/버전을 포함하는 export command/화면은 확인되지 않았다. owner Story의 download_enabled 기본은 false(`story_web.py:449-456`)이다.

대안: export 종류를 `사진 묶음/Story HTML/인쇄용 PDF/복원 가능한 문서 JSON`으로 명시; MVP는 offline HTML+파생본+manifest index. 수용: 합성 Story를 offline 열어 순서/본문 일치, 비밀·내부 절대 경로·정확 위치는 선택 정책에 따라 제거, 취소/부분 실패/용량 안내, export는 원본·공유 상태를 바꾸지 않음.

### STORY-24 · P2 · 문서 완료 판정과 현재 소스·앞부분 설명의 충돌

**문서/코드 확인.** 9월 24일 계획의 앞부분은 완료 결과 생성·PC 관리가 없다고 설명하지만 후속 단락은 구현 완료를 기록한다. 특히 `docs/09-roadmap/01-active/23-pc-mobile-feature-parity-and-result-to-story-plan-2026-09-24.md:389-403`는 선택 Story share/refresh 정합과 완료 조건을 언급하지만 STORY-01/02의 코드가 남아 있다. `56-approved-story-ux-implementation-2026-09-15.md`는 두 테마 범위를 설명하고 뒤의 `58-five-story-themes-physical-quality-validation-2026-09-15.md:3-7` 및 현재 `story_presentation.py:15-17`은 다섯 테마다. 시간순 scope 확대 자체는 결함이 아니며 최신 계약을 한곳에서 판별하기 어렵다는 문제다.

대안: 현재 기능 계약/상태 표와 과거 검증 기록 분리, 기능별 구현 SHA·검증 환경·누락 수용조건 링크. 수용: 같은 항목에 ‘미구현/완료’가 병존하지 않고 `구현/자동 검증/실기기 검증/잔여`를 분리; share/refresh 미충족 조건은 완료에서 제외.

### STORY-25 · P1 · 테스트 통과가 Story 작업 완결성과 개인정보 대상 정합을 보증하지 않음

**실행/코드 확인.** 86개 선택 테스트는 통과. `test_result_story_service.py:50-76,144-174`는 materialize를 mock하고 snapshot 링크/선택 정책만 검사하여 반복 선택·선택 순서·실제 누락을 증명하지 않는다. `test_story_gallery.py:10-44` 등 일부 테마 테스트는 문자열 계약이고 일부 geometry JS 실제 실행도 존재한다. `test_story_sharing.py:382-397`는 service와 renderer 연결이며 서로 다른 A/B Story의 owner route 선택 대상을 검사하지 않는다. FakeDirector 검증은 실제 모델 사실성 검증과 다르다.

대안/수용: 아래 회귀 매트릭스의 사용자 목표·상태 전이를 검증. P0/P1 반례를 먼저 자동화하고 synthetic browser/native tests와 실제 설치본 acceptance를 구분. 합계 passed 숫자로 결함을 닫지 않는다.

### STORY-26 · P2 · 공유 패키지는 준비 완료 전에 active로 저장

**코드 추론.** `StoryShareService.create`는 active package를 먼저 저장(`story_sharing.py:285-348`), owner handler는 그 뒤 모든 파생본 생성(`server.py:769-791`). 알려진 이미지 오류는 revoke/purge(`:792-794`)하지만 process crash/예외 누락 시 preparing 상태가 없어 부분 active package를 남길 수 있다. 공유 생성 전체가 동기 작업이라 큰 Story에서는 UI 대기도 길다.

대안: SharePublication preparing → ready(active), 단계별 asset manifest와 outbox; 완성된 스냅샷만 발행. 수용: 파생본 생성 중 종료 시 public 404/준비중, 재시도 중복 패키지 없음, 실패 정리, 활성화는 한 번만.

### STORY-27 · P2 · fallback 실패 상태와 재시도 결과가 숨거나 저장되지 않을 수 있음

**코드 추론.** scoped refresh 예외는 fallback에 error_code만 넣고 `_persist_revision`로 보냄(`story_generation.py:986-997`); 같은 evidence/source/schema/scope면 이전 값을 그대로 반환(`:671-680`). 이전 fallback과 source가 같으면 새 error_code가 유실될 수 있다. 이미 hermes source이면 scoped refresh는 즉시 반환(`:973-974`). 모바일 generation에는 source/model만 projection(`application/mobile_client.py:382-385`), 오류/ready/fallback 상태가 없다.

대안: AI attempt를 Story 본문 revision과 독립 저장, 최신 실패·원래 성공본·재시도 가능 여부 표시. 수용: 모델 실패 후 기존 내용 유지와 실패 표시를 동시에 보장; 같은 근거로 사용자가 재생성 요청하면 명시 새 attempt 수행; UI에 규칙 기반/AI 제안/검토 완료 구분.

### STORY-28 · P2 · 선택 모드에서 선택 사진 Story CTA를 숨김

**코드 확인.** 버튼 자체는 명시 선택을 처리(`results/controller.py:395-424`)하지만 선택 workspace에서는 숨김(`:1135`). 선택 후 감상 workspace로 돌아가야 생성 버튼을 발견한다. `완료 결과로 Story`가 주동작인데 실제 선택 완료 흐름에서 다음 행동이 끊긴다.

대안: selection workspace에 ‘선택한 N장으로 Story 초안’ CTA, 추천 모드는 별도 명칭. 수용: 선택 직후 1동작으로 확인 sheet, 선택 수·범위 일치, 저장중이면 실행 대기/오류 이유 명확, 키보드 접근 가능.

### STORY-29 · P1 · AI 1회 출력 한도가 대용량 사진/날짜 계약과 맞지 않음

**코드 추론, 성능 미측정.** director는 모든 사진 evidence를 한 번 전송하고 `max_tokens=1800`(`hermes_router.py:210,223`)으로 모든 refs를 정확히 한 번 반환하도록 요구(`:198`). 10,000장 입력의 refs만 해도 출력 한도를 크게 넘을 수 있는데 모델 manifest는 누락 시 전체 fallback(`story_generation.py:626-627`). 대용량 UI 지원과 AI narrative 지원을 같은 완료 조건으로 취급할 수 없다.

대안: 서버가 날짜·장면·참조 coverage를 확정하고 모델에는 장면별 제한 증거/짧은 prose만 요청; budget 추정·chunk attempt·재시도. 수용: 0/1/100/1,000/10,000장 합성에서 refs coverage는 모델 출력과 무관하게 보장, token overflow 사전 차단, AI 부분 실패는 해당 문장만 fallback, 장면별 출처 보존.

### STORY-30 · P2 · 날짜/시간대 계약이 Story 출처·표시 범위와 혼재

**코드 확인.** scoped Story는 결과 기반도 kind=capture_date_bounded/timezone=Asia/Seoul로 저장(`story_generation.py:791-799`); 결과 기반은 실제 date_from/to가 빈 값이다. manual은 Asia/Seoul 외 timezone 거부 및 30일 제한(`manual_curation.py:149-157`). 날짜 선택 UI는 source timezone·촬영 timezone·표시 timezone을 분리하지 않는다. 대한민국 기준이라는 현 제한 자체를 버그로 단정하지 않는다.

대안: source_kind별 scope union(`date_range/result_selection/manual_items`) 및 display_timezone/capture timezone 원천 별도. 수용: 여행·자정·DST·날짜 미상·혼합 source의 경계 fixture에서 포함 이유 재현; 지원하지 않는 범위/시간대를 입력 전에 안내.

### STORY-31 · P2 · Mac 폼은 서버가 금지하는 구성 조합을 선택 가능하게 함

**코드 확인.** Apple/Google 기본 동시 체크와 ‘인물 위주’를 모두 제공(`story_web.py:768-769`). normalize는 Google 포함 people_present와 specific_person을 금지(`manual_curation.py:131-134`), 날짜 30일 제약은 폼에 표시되지 않는다(`:154-155`). 서버 오류는 generic notice로 돌아옴(`server.py:650-665`), 입력 맥락 보존도 제한된다.

대안: 서버 capabilities 기반 선택 옵션과 inline validation/preview. 수용: 금지 조합 사전 설명, 기간 초과/권한 없음/Google Picker 필요를 확인 sheet에서 제시, 오류 시 입력 날짜·출처·구성을 보존, 지원되지 않는 ‘특정 인물 Story’ 기능을 작동하는 선택지로 홍보하지 않음.

## 7. 새 도메인 계약 제안

기존 추천·인물·GPS 저장소를 폐기하거나 Swift/Compose로 전환하는 계획이 아니다. 공통 application 도메인을 먼저 보강하고 PyObjC/native Android View/web renderer가 같은 명령과 projection을 소비한다.

| 객체 | 핵심 필드와 책임 |
|---|---|
| StoryDocument | id, owner, lifecycle, current_revision_id, draft_revision_id, title, tombstone_epoch. 사용자 편집 문서의 정체성 |
| StorySourceSnapshot | source_kind, source_run/result_revision, ordered_items, selection_policy/version, mapping, availability at selection, hash. immutable insert-only |
| StoryRevision | id, document_id, parent_revision_id, content_hash, editor_kind, created_at, expected_base; 과거 내용 영속 보존 |
| StoryChapter / StoryItem | stable IDs, position, asset_ref, caption, chapter_ref, crop/view setting; asset metadata와 편집 텍스트 분리 |
| CaptionClaim / EvidenceRef | claim text, fact/inference/user_note, evidence field path/hash, source/provider, confidence, validation/review status |
| StoryOperation / AIAttempt | durable command ID, source revision, phase/checkpoint, progress counts, retries, consent/policy version, error code; 네트워크 실행 기록 |
| StoryPresentation | 감상 테마/레이아웃 revision. 원문 StoryRevision과 독립, 명시적 content revision 참조 |
| StorySharePublication | story revision+presentation revision+privacy policy snapshot, preparing/active/expired/revoked, expiry, download, consent epoch, public IDs |
| StoryExport | exact revision, format, privacy options, missing assets, manifest/checksum, preparing/ready/failed/cancelled |
| StoryReadState | 기기별 selected_story/revision, chapter/item anchor, scroll, active facet. 읽기 상태이며 문서 편집 revision을 증가시키지 않음 |
| SourceAccess / ManagedAssetState | 원본 Photos/Google 권한과 이미 복사한 자산 접근을 구분. 사진 쓰기/앨범 publish는 별도 명령 |

하나의 `revision`에 추천 세대·본문·감상 테마·AI 시도·동의를 모두 넣지 않는다. command에 target ID와 expected revision을 반드시 포함하고 latest는 목록 정렬에만 쓴다. DB transaction/CAS는 잠금의 존재만으로 대신하지 않는다.

## 8. 상태 머신과 화면 흐름

```text
입력 선택 → SourceSnapshot 고정 → 자산 확인/복사 → Story 초안(규칙 기반)
                         └ 누락 → 부분 초안 + 누락 검토/재시도
초안 → 사용자 편집 ↔ 자동저장/충돌해결 → 검토 완료 revision → 감상
          └ AI 제안 준비/실행/검증 → 제안 비교 → 적용 또는 버리기
검토 완료 revision → 공유 준비 → privacy preview → 명시 발행 → active
                                                  └ expired / revoked
검토 완료 revision → export 준비 → ready 또는 partial/failed/cancelled
문서 → trash(tombstone, 모든 공유 즉시 차단) → 명시 restore → 새 revision
```

StoryOperation: `queued → checking_source → waiting_permission|waiting_picker|waiting_mobile_location → materializing → building_draft → awaiting_review → completed`. 실행 중 `failed_retryable/failed_terminal/cancelled` 전이를 허용하고 checkpoint를 보존한다. AI 상태는 `disabled/not_requested/pending/running/validation_failed/proposed/accepted/rejected`; Story 전체 ready와 독립이다. 네트워크 단절은 문서 실패와 같지 않다.

권장 화면:

1. **Story 라이브러리**: 초안/완성/휴지통, 검색·기간·출처, 작업 상태. 최신 Story 본문 안에 전체 목록/생성 폼을 삽입하는 현재 구조를 분리.
2. **만들기 sheet**: 날짜/완료 분석/선택 사진 선택 → 포함 순서·장수·출처/권한·GPS/AI 옵션 → command 실행. 사진 쓰기/앨범 publish는 별도 기능.
3. **작업 상세**: 단계, 요청/복사/실패 수, 기다리는 기기/권한, 취소·재개·재시도; 완료 시 정확한 Story ID 열기.
4. **초안 편집**: 사진·장면 목차와 본문, 표지, 순서, 제거/추가, 출처 패널, AI 제안 비교, undo, 저장/충돌. drag 외 이동 버튼·키보드 제공.
5. **감상**: 기존 다섯 테마/비율 보존 viewer 재사용, 읽던 위치 복원, 관리 버튼으로 편집·공유·export 접근.
6. **공유 sheet**: 대상 제목/revision/수, 위치·날짜·이름·다운로드·기간, 공유본 preview, 발행 및 종료. 오래된 공개 링크의 스냅샷과 현재 편집본 차이 표시.
7. **휴지통/버전 이력**: 원본 보존 범위, 공유 폐기 영향, 복원할 revision; 복원이 예전 링크를 되살리지 않음.

Mac은 현재 NSAlert/WKWebView shell을 유지하되 command 성공을 정확한 detail URL로 연결하고 native toolbar/단축키/저장 상태·실패 회복을 제공한다. Android는 Java View 목록/확인창/WebView를 유지하되 native Back·선택 ID·회전 상태·48dp 조작 목표·큰 글자/TalkBack 순서를 검증한다. 화면을 동일 픽셀로 맞추기보다 객체·순서·상태·명령 의미를 맞춘다. 모바일에서 Mac Finder export를 억지로 복제하지 않고 가능 형식과 작업 상태를 제공한다.

## 9. 개선 순서와 수용 기준

| 단계 | 작업/연결 ID | 완료 기준 |
|---|---|---|
| S0 현재 계약 고정 | STORY-01/02/24/25 | A/B 대상 정합 regression; 현재 기능/검증 상태 문서 일원화. 공개 대상 잘못 선택하는 경로 먼저 닫기 |
| S1 도메인 보존·범위 | STORY-03/04/06/07/08/09/30 | immutable ordered snapshot, revision/CAS/tombstone, 전수 영향 갱신·공유 차단. 기존 데이터 dry-run migration 계획·카운트 대조 |
| S2 생성 완결성 | STORY-14/15/16/17/20/27/31 | durable 작업, exact result open, partial 표시, capability 맞는 재분석, 플랫폼 입력 preview |
| S3 편집 MVP | STORY-05/19/28 | 수동 제목·표지·본문·선택·순서·undo·저장·재개·휴지통; AI 없이 완성 |
| S4 AI/공유 개인정보 | STORY-10/11/12/13/26/29 | claim/evidence와 사용자 검토, 송신 최소화, 공유 공개 수준/준비 상태/정확 revision, 대용량 bounded AI |
| S5 발견·감상·export | STORY-18/21/22/23 | cursor library, read state, 원본/복사본 권한·availability 구분, offline artifact, 실제 native/browser acceptance |

부모 통합 계획에서는 S0/S1을 단순 테마 polish보다 앞에 둔다. 대규모 저장소/권한 변경은 별도 승인된 구현·migration 단계에서 수행한다. 과거 Story가 새 도메인으로 옮겨질 때 scope/source provenance를 추정 사실로 메우지 말고 `legacy_unknown`으로 기록하며 원문·공유 스냅샷을 보존한다.

## 10. 검증 매트릭스와 잔여 미검증

| 영역 | 필요한 검증 | 이번 상태 |
|---|---|---|
| 대상 공유/refresh | A/B, 상세 열린 동안 B 갱신, 삭제 직후 POST, expected revision | 코드로 불일치 확인; 실제 route mutation 미실행 |
| 선택/snapshot | {A,B}→{C}, 순서 역전, 추천→명시→추천, 중복 content/5 Picker 세션, 누락 | 기존 mock 테스트 통과; 전체 materialize 반복 선택 미검증 |
| 버전/삭제 | 같은 근거 제목 변경, 동시 CAS, 늦은 worker, 삭제+근거 변경, 복원 | 제목 무시/삭제 재생성 합성 확인; 동시 프로세스 미검증 |
| 전수 개인정보 갱신 | 201/1,001 Story와 501+ 공유, consent revoke/outbox 실패 재처리 | 200 cap 합성 확인; 실규모 철회·공유 누락 미검증 |
| AI | 자유 문장 허위 사실/임의 이름, field provenance, prompt injection, disabled/no network, output budget | 허위 prose 수락 합성 확인, FakeDirector 테스트 통과; 실제 AI 미실행 |
| 공유 이미지/보안 | 세션/코드/만료/revoke/다운로드 off/EXIF 제거/부모 tombstone | 기존 합성 테스트 통과; 운영 도메인/Maps/referrer 미검증 |
| 생성 재개 | 각 단계 crash/restart/cancel/partial/retry/idempotency, 창 교체/다른 작업 완료 | 코드 추론; 실제 종료/재시작 미실행 |
| 권한·자산 | Apple denied/limited/revoked, Google 만료/Picker 재선택, managed copy missing/corrupt | 실제 권한·원본 라이브러리 접근 미실행 |
| macOS | 실제 결과 선택→CTA→정확 Story, WKWebView 네트워크 실패/저장충돌/VoiceOver/키보드 | 소스 경로 확인; 설치 앱 UI 미검증 |
| Android | Java View 실제 크기·큰 글자·회전·Back·제스처·TalkBack, 오래된 Story 실패 재시도 | 소스 경로 확인; 실기기 미검증 |
| 웹/모바일 감상 | 5테마·0/1/10k사진, zoom/pan/filter/순서/모션 감소/지도 요청 시점 | 기존 구조/geometry 테스트 통과; 이번 browser render 미실행 |
| export | offline HTML/PDF/manifest 일치·privacy·부분 실패·취소 | 기능 공백, 미구현 수용조건 |

과거 문서의 브라우저/설치본 검증은 보고된 과거 증거다. 예를 들어 `docs/08-reports/01-validation/56-approved-story-ux-implementation-2026-09-15.md`는 desktop/mobile 폭 viewer 검사 및 Safari/Android 하드웨어 미검증을 구별한다. `58-five-story-themes-physical-quality-validation-2026-09-15.md:3`도 Android 실기기 미완료를 명시한다. 이번 감사에서 그 결과를 새 검증으로 재사용하지 않았다.

## 11. 부모 통합 계획 재검토 기준

이 문서는 구현 작업 지시가 아니라 재설계의 입력이다. 부모가 통합 계획을 보내면 다음을 Story 관점에서 다시 확인한다.

- STORY-01~31 각각이 작업·수용조건·검증·책임 영역에 연결되는지, 제외 사유가 있는지.
- UX 새 화면이 immutable 선택/본문 버전/공유 대상/삭제·복구/부분 결과·재개를 실제 domain command로 완결하는지.
- 추천/사진 asset/인물 동의/정확 위치 정책과 충돌하거나 원본·공유 스냅샷을 자동 변형하지 않는지.
- macOS PyObjC/Android Java View/web 실제 구조에 맞는 단계인지, 근거 없는 전면 프레임워크 전환을 전제하지 않는지.
- 코드 추론·합성 실행·기존 보고·운영/실기기 미검증을 유지하는지; 자동 테스트 합계로 P0/P1 수용조건을 대체하지 않는지.

재검토는 아직 수행하지 않았다. 통합 계획 수신 후 이 담당 범위를 기준으로 수행한다.
