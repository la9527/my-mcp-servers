# Story 관점 통합 설계·실행 계획 peer review

작성일: 2026-10-06 · 담당: 기존 Story 감사 담당 · 상태: 현 문서 기준 검토 완료, 보강안 반영 후 재판정 필요

## 1. 검토 대상·판정 범위

검토 대상은 [05-product-and-feature-design.md](05-product-and-feature-design.md)와 [06-execution-and-validation-plan.md](06-execution-and-validation-plan.md)이다. 독립 감사 [03-story-audit.md](03-story-audit.md)의 STORY-01~31을 대조했다. 이번에 새 구현·실제 사진·AI·공유 생성·키체인·클라우드 작업을 실행하지 않았다. 이 리뷰 파일만 작성했고 05/06과 다른 담당 파일, 기존 `people/controller.py` 수정은 변경하지 않았다.

읽은 버전을 식별하기 위한 SHA-256:

| 파일 | 줄 수 | 검토 snapshot SHA-256 |
|---|---:|---|
| 05-product-and-feature-design.md | 162 | `6a7e7e536d2b134c6f0f3c509016cb1a5338a433e44e2fbf1f9cc678157fa6fb` |
| 06-execution-and-validation-plan.md | 165 | `212920412b232dce6441f0962ee98ae12b0d7d579fd4c1962021da6e7a8e4841` |

이후 부모가 수정하면 아래 줄번호·판정은 이 snapshot 기준이다. 사용자에게 전달받은 예정 보강은 **P0 wrong-story-share와 approval/privacy 설정 fail-open을 단계 0 예방 조치로 추가하고 P1 snapshot/version을 우선화하는 것**이다. 현재 읽은 문서에는 그 구체 예방 작업·negative gate가 아직 없으므로 반영을 가정하지 않았다. approval/privacy fail-open 자체의 새로운 코드 감사는 이 리뷰에서 수행하지 않았으며, 구조 담당의 근거 ID/경로를 부모가 연결해야 한다.

분류: **BLOCK**은 해당 위험 경로의 배포/활성화 전 계획에 반드시 명시할 조건, **HIGH**는 데이터·사실성·주요 기능 보존 계약을 잃을 위험, **MED**는 기능 범위·실현가능성·명확한 완료 판단의 부족, **LOW**는 선택적 문서/표현 개선이다. 전체 조사 문서 작성을 막는다는 뜻은 아니다. 안전한 읽기·fixture 검증·독립 UI 결함 수정은 계속할 수 있다.

**종합 판정: 방향은 적합하나 조건부 보완 필요. BLOCK 1 / HIGH 5 / MED 2 / LOW 0.** 기존 구현 재사용, framework 유지, snapshot 우선, 편집/작업 상태 분리, private/public projection 분리, partial 표시, 제한적인 polling, synthetic fixture와 실제 기기 구분은 타당하다. 아래 8개를 task와 gate로 보강하기 전에는 Story 공개·migration·자동 재생성까지 포함한 실행 사양으로 확정하지 않는다.

## 2. 핵심 개선 제안 — 최대 8개

### REVIEW-STORY-01 · BLOCK · 단계 0에 잘못된 공개 대상과 fail-open 예방을 실제 작업으로 고정

**연결:** STORY-01/02/12/24/25. approval/privacy fail-open은 구조 감사 ID를 별도 연결 필요.

**현 문서 근거:** 06:29-39 단계 0은 baseline·보존 전략만 기술한다. T1.4(06:50)는 route 제목·복귀이고 공유 대상 정합이 없다. T2.5(06:65)는 승인·정책 negative tests지만 G2에 위치한다. 실제 공개 정책 재강제는 단계 4(06:88-90)다. 05:38,127,137의 특정 버전/공개 preview 원칙만으로 현재 잘못된 대상을 공유하는 route를 먼저 막는 실행 계약은 확정되지 않는다.

**현재 코드 근거:** `interfaces/http/story_web.py:836-841` 공유/refresh 폼은 현재 Story ID를 보내지 않는다. `interfaces/mcp/server.py:757-758`는 최신 Story, `:836-840`은 전역 recommendations를 선택한다. 기존 감사에서 코드 확인했으며 이번 실제 공개는 실행하지 않았다.

**구체 수정 제안:** 단계 0에 `T0-SAFE-STORY`를 추가한다. 기존 route 안에서 target story_id와 expected content revision, 명시 공개 범위/정책 fingerprint를 결합하고 누락·삭제·충돌·허용되지 않은 정책은 mutation 전에 거절한다. latest fallback 제거. 대규모 편집 도메인 완성을 기다리지 않는 최소 예방 패치로 분리한다. approval/privacy의 missing/malformed/unknown 설정은 쓰기/발행 허용으로 해석하지 않도록 해당 구조 감사의 정확 경로를 명시한다. 기능을 끄는 예방 조치가 필요하면 범위·기존 링크 영향·복구 조건을 문서화한다.

**수용:** A 상세 열린 뒤 B 갱신 → 공유는 A 고정 revision 또는 409이며 B package 0개. target/revision 누락, 삭제된 A, 변조 공개 policy, 누락/잘못된 설정에서 **package 생성·파생본 생성·provider 쓰기·실제 AI 실행 횟수 0**. refresh도 현재 대상 또는 명시 전역 객체를 UI에서 구별. 기존 공개 읽기·만료·폐기 회귀 유지.

**조건부 판정:** 부모의 예정 보강이 이 task/negative fixture/배포 gate까지 포함하면 BLOCK 해소 가능. ‘단계 0 안전 강화’라는 문구만 추가하면 유지. 현재 판정은 현 snapshot 기준이다.

### REVIEW-STORY-02 · HIGH · 불변 선택·본문 revision·삭제 tombstone을 UI 확장 전에 하나의 저장 계약으로 묶기

**연결:** STORY-03/04/05/06/07/19/30.

**현 문서 근거:** 05:33-38의 snapshot/content_revision과 06:61의 T2.1은 좋은 출발이다. 그러나 과거 본문 revision 영속 저장/CAS/tombstone/복원은 객체 표에 명확하지 않고 본문 version 분리는 단계 4(06:85)로 미뤄진다. 05:78의 `draft → reviewed → saved`는 초안 자동 저장(06:84)과 의미가 겹친다. 06:153-159 migration/rollback 원칙은 있으나 새 모델의 독립 불변 조건이 부족하다.

**현재 코드 근거:** `result_story_service.py:38-58` hash에 사진 목록/순서가 없음; `recommendation_storage.py:289-294,796-797` 동일 작업·정책 collection 재사용; `run_repository.py:1187-1232` member upsert. `_persist_revision(story_generation.py:671-680)`는 같은 근거의 제목 변경을 무시, repository `:1975-1981`은 현재 JSON 덮어쓰기. `ensure_scoped_story(:805-835)`는 삭제 상태를 보호하지 않는다. 제목 무시/삭제 재생성은 독립 감사 합성 실험 확인이다.

**구체 수정 제안:** 단계 2를 `T2.1 immutable ordered membership`, `T2.1b append-only StoryRevision + expected base CAS`, `T2.1c tombstone/restore guard`로 나눈다. source scope는 date/result-selection/manual-items의 union으로 정의한다. 결과·선택·Story snapshot 각각의 권위와 mapping 책임을 표로 정하고 같은 목록을 여러 저장소가 독립 재계산하지 않게 한다. 재시도 idempotency는 같은 command_id에만 적용하고, 사용자의 ‘별도 Story 만들기’까지 hash 때문에 항상 기존 문서로 합치지 않는다.

`saved`는 편집 lifecycle과 분리해 save_state(`dirty/saving/saved/conflict/error`)로 표현하고 문서 검토 상태는 `draft/reviewed`, 삭제는 별도 tombstone/휴지통으로 정의한다. revision 복구는 과거 행 덮어쓰기가 아닌 새 revision을 만든다.

**수용:** 같은 작업 {A,B}→{C}→{B,A}는 불변 source snapshot 및 의도한 문서 분기 유지, 잔존 member 혼입 0. 장소 A/B/A와 viewer/share/export 순서 일치. 같은 근거 제목 변경 저장. 동시 base revision 충돌은 한쪽 409. 삭제 후 늦은 worker·근거 변경·재시도 모두 자동 부활 0. 명시 복원은 폐기된 공유를 살리지 않음. legacy 불확실 범위는 `legacy_scope_unverified` 유지.

**조건부 판정:** 예정된 snapshot/version 우선화에 tombstone과 append-only/CAS까지 포함하면 HIGH 해소 가능. content_revision 숫자·snapshot hash 필드 추가만으로는 해소되지 않는다.

### REVIEW-STORY-03 · HIGH · 전체 동의 갱신·공유 폐기를 화면 pagination/용량 회수와 구분

**연결:** STORY-08/09/12/13/21/26.

**현 문서 근거:** 05:38/127 및 06:90/150은 만료·폐기·동의 철회 차단을 요구하지만 전수 처리와 공개 접근 시 재강제, 준비 단계는 명시하지 않는다. 전체 Story 접근·union 계산은 단계 6(06:116)인데 개인정보 갱신 누락은 성능 polish 이전의 안전 계약이다. 05:78의 공유 lifecycle에도 `preparing`이 없다.

**현재 코드 근거:** `story_generation.py:848,894` limit=500 요청 대비 `run_repository.py:2054` cap 200(합성 확인). `manual_curation.py:284-295` 삭제 시 공유 최대 500; `story_sharing.py:351-371` active 검사에는 부모 Story tombstone이 없다. `story_sharing.py:285-348` active 저장 후 `interfaces/mcp/server.py:779-794` 파생본 준비.

**구체 수정 제안:** G2에 affected-story/person index 또는 cursor batch+재처리 outbox를 넣고 완료 카운트를 전체 영향 집합과 대조한다. 공유 삭제/동의 철회는 제한 없는 조건부 DB 갱신과 public access guard(tombstone/consent epoch)를 함께 적용한다. 공유 publication은 `preparing → active → expired/revoked`로 설계하며 partial/crash package를 공개하지 않는다. 공개 접근 즉시 차단과 캐시/파일 실제 회수는 서로 다른 영수증·상태다. 지도 src는 명시 요청 전 삽입하지 않는 정책을 별도 지정한다.

**수용:** 201/1,001 Story, 한 Story에 501+ shares 및 expired 혼합 fixture에서 누락 0. 일부 갱신 실패 때 outbox 완료 표시 금지. 철회 직후 기존 session과 사진 요청 차단. 이미지 준비 중 crash 후 active 0. revoke는 참조가 남은 owner/다른 Story 자산을 삭제하지 않는다. 지도 미열람 시 제3자 요청 0은 실제 WebView/browser network 검증으로 닫는다.

**안전경계:** 기존 exact 가족 공유 정책을 자동 축소하거나 기존 링크를 일괄 재작성하지 않는다. 새 공개 수준 선택·새 발행과 동의 철회는 구별한다. 이 제안은 운영 공유/위치 변경을 이번에 실행하라는 지시가 아니다.

### REVIEW-STORY-04 · HIGH · AI 사실성의 검증 단위·송신 정책·대용량 출력 전략을 고정

**연결:** STORY-10/11/12/14/27/29.

**현 문서 근거:** 05:40/121은 evidence 연결·모르는 사실 미생성, 06:147은 허위 사람/장소 fallback을 요구한다. 하지만 CaptionClaim/EvidenceRef, 문장 검증/검토 상태, AI 송신 필드, 독립 attempt 기록, 10k 출력 설계는 task가 없다. deterministic 초안 먼저 보이기(06:75)는 적절하지만 AI 시도와 문서 ready를 구분하는 저장 계약이 필요하다.

**현재 코드 근거:** `_safe_model_text(story_generation.py:567-570)`는 semantic 검증이 아니며 `_model_manifest(:583-627)`는 refs/date/coverage를 검증. 근거 없는 prose 수락은 합성 확인이다. evidence는 scene/date/owner location 포함(`:365-410,475-483`), director는 한 번에 전송·max_tokens=1800(`hermes_router.py:210,223`). AppKit 결과 생성은 identity_repository를 전달하지 않음(`results/controller.py:443-449`).

**구체 수정 제안:** Story 문장을 facts/inference/user_note/AI proposal로 구분하고 근거 field path/hash와 검토 상태를 기록한다. 서버가 사진 membership·chapter refs/date를 확정하고 모델은 bounded 장면 prose만 제안한다. AI disabled/no request·송신 최소화/redaction·사용자 선택·prompt/model/policy version을 attempt에 기록한다. public prose도 공유 이름/위치 정책을 적용해 facet만 익명화하고 자유 문장에 이름을 남기지 않는다. 모든 생성 진입이 같은 identity/privacy projection을 사용한다.

**수용:** 임의 이름·관계·감정·장소·시점·prompt injection fixture가 reviewed fact로 저장되지 않음. 출처 없는 문장은 제안/unknown. 0/1/100/1,000/10,000장에서 refs coverage는 AI token budget과 무관하게 보장. AI 실패 attempt가 유실되지 않고 기존 성공본 유지+재시도 가능. disabled는 준비 명령·네트워크 0. 사용자 편집본은 AI 제안 적용 전 불변.

**미검증:** 실제 모델 품질·router 송신 보관 정책·현재 운영 설정은 확인하지 않았다. 향후 fixture validator 성공과 실제 AI 품질 평가를 별도 gate로 기록해야 한다.

### REVIEW-STORY-05 · HIGH · 완료 결과 생성도 기존 durable 작업 위에 연결하고 증명 가능한 호출 계약으로 수정

**연결:** STORY-15/16/17/20/22/27/28/31.

**현 문서 근거:** JobProjection(05:32), 작업 상태(05:70-74), T2.4(06:64), G3(06:78)는 필요하다. 하지만 완료 결과 생성의 command 저장/claim/checkpoint/cancel·리소스 확정과 UI ID 열기는 명시 task가 아니다. 05:115의 ‘ranker 호출 0’과 06:75의 ‘ranker 재호출 없음’은 기존 summary/review/read 호출까지 금지하는 것으로 해석될 수 있다.

**현재 코드 근거:** 완료 결과 서비스는 `get_job_summary/get_review_items/get_recommended_items`를 읽는다(`recommendation_storage.py:723,755-761,799-804`); 이것은 분석 재실행과 다르다. AppKit은 daemon thread만 사용(`results/controller.py:437-463`)하고 성공 뒤 base Story 탭을 연다(`:475-485`). partial도 completed 반환(`result_story_service.py:88-90,123-128`). origin_run_id만으로 manual 판정(`application/mobile_client.py:379`)하여 완료 결과 Story에 잘못된 재분석 버튼이 생길 수 있다.

**구체 수정 제안:** 별도 두 번째 worker 인프라 대신 기존 curation operation/claim/nonce/idempotency 경로를 재사용 가능한 범위로 확장하고, Story-specific phase와 checkpoint를 composition root에 연결한다. 저장할 command 종류는 source snapshot에 따라 구분한다. operation result에 exact story_id/revision 포함, UI는 해당 detail로 열기. can_reanalyze_source/can_regenerate_text 등 capability를 서버 파생한다. partial completeness와 AI state를 별도 projection으로 유지한다.

호출 수용 문구는 **‘새 vision/ranking analysis job 시작 0; 완료 결과 read API는 허용 및 호출 목적 기록; optional Story AI는 명시 모드에서만 별도 attempt’**로 고친다. copy/materialize는 사진 읽기·로컬 자산 생성임을 명시하고 외부 앨범 publish command를 암묵적으로 연결하지 않는다.

**수용:** copy/snapshot/draft/AI 각 단계 직후 process death → 재시작에서 같은 command 재개, 새 분석 job 0/중복 초안 0, 실패분만 재시도. 요청3/포함2/실패1은 일부 완료 표시. 동시에 A/B 완료되면 A 완료 CTA는 A로 연결. result-source Story는 지원하지 않는 날짜 전체 재분석을 제공하지 않음. Google+people_present 등 불가 조합은 enqueue 전 안내.

### REVIEW-STORY-06 · HIGH · migration/rollback gate가 안전 우회를 되살리지 않도록 데이터 계약으로 묶기

**연결:** STORY-01/03/06/07/08/09/24/25.

**현 문서 근거:** 06:153-159는 legacy 불확실 상태 보존·token/키/share ID/GPS 유지·새 revision 보존을 잘 명시한다. 그러나 각 단계 feature flag로 이전 read/UI 경로 복귀한다는 원칙이 P0 route·tombstone 보호·revocation guard까지 되돌릴 수 있는지 경계가 없다. 기존/new snapshot·revision 이중 쓰기 원자성과 backfill 비교의 통과 기준도 아직 요약 수준이다.

**구체 수정 제안:** ‘되돌릴 수 있는 presentation/UI’와 ‘항상 유지할 target binding/approval/privacy fail-closed/tombstone/public guard’를 분리한다. schema 추가는 테스트 DB migration rehearsal에서 이전 reader 호환성, before/after row count/content hash/ref order, 공유 package identity, known/legacy_unverified 구분을 대조한다. 이중 쓰기는 transaction 또는 outbox 재처리로 한쪽 실패를 회복하고 종료 조건/단일 권위 reader를 지정한다. 운영 표본은 별도 권한 범위이며 fixture 성공을 운영 데이터 검증으로 확대하지 않는다.

**수용:** rollback flag 뒤에도 wrong-story 공유/삭제 부활/철회 공유 접근 0. backfill은 현재 recommendation head로 과거 Story를 재생성하지 않음. 중간 실패 후 반복 migration은 중복 revision 0·누락 0. 이전 public share ID/패키지 본문 유지, 새 renderer는 referenced version 제공. backup restore rehearsal의 사진·권한·키 보존 경계를 기록.

**순서:** G2 데이터 gate 전에 G3 새 생성·G4 새 공유 활성화 금지. 본문 편집 UI 완성까지 모든 safety gate를 미룰 필요는 없다.

### REVIEW-STORY-07 · MED · Story 내보내기·원본 권한·읽던 위치·오래된 목록의 완료 범위 명시

**연결:** STORY-05/18/19/21/22/23/30/31.

**현 문서 근거:** 05:39 ExportPlan/Receipt와 도식 05:58은 results export 중심이다. Story 기능 표(05:114-127)에 Story export/버전 복구/휴지통 복원 계약이 없다. 05:108-110 및 06:97-100은 복구/목록·scroll을 언급하지만 StoryReadState, cursor library, 실제 managed asset 상태와 원본 권한 구분은 task에 충분히 구체화되지 않았다.

**구체 수정 제안:** 제품 범위를 표로 확정한다. Story export MVP는 exact revision의 offline HTML+파생본+manifest index를 제안하고 PDF/복원 JSON은 별도 후속 여부를 명시한다. 사진/결과 export와 이름을 구분하며 기존 export approval/receipt 서비스를 재사용한다. 원본 source permission과 managed copy availability를 분리한다. Story library cursor/search와 기기별 read anchor/selected ID를 정의한다. Android 실패 재시도는 빈 ID 최신 Story 대신 원래 ID로 복귀. 휴지통 복원과 version history는 지원 또는 명시 유예로 결정한다.

**수용:** 1,001개 library에서 오래된 Story 도달·검색, 페이지 중복/누락 0. 오래된 A offline/reconnect/rotation에서 A·읽던 item 복원. 원본 권한 철회와 이미 복사된 preview의 보존 상태를 따로 표시. offline export는 제목/본문/순서·privacy 선택 일치 및 개인 절대 경로/secret 0; 실패·취소는 원문·공유 상태를 바꾸지 않음. Story export 유예 시 전체 제품 완료 범위에서 제외 사실을 명시.

### REVIEW-STORY-08 · MED · 발견 ID→task→gate 추적과 단계별 실기기 검증/변경 소유권을 구체화

**연결:** STORY-01~31 전체, 특히 STORY-18/20/24/25/28.

**현 문서 근거:** 06:39는 coverage 누락 0, 06:119는 플랫폼별 미검증을 남기지 않음, 06:125는 fixture→packaging→설치→기기 순서. G1은 설치 앱, G3은 walkthrough, G5는 등록 기기지만 단계 7을 모든 앞선 변경 이후로 해석하면 intermediate UI 검증이 밀릴 수 있다. 06:57 disjoint write set 병렬 조건은 좋은데 T1.4와 T2.2가 owner renderer/route를 공유하고 단계3/4/5도 같은 파일을 건드릴 수 있다.

**구체 수정 제안:** 리뷰 반영표에 `finding_id, task_id, invariant, test fixture, source/deployed evidence, remaining gate, owner, deferred reason`을 둔다. G3부터 synthetic native/WebView vertical smoke 및 해당 bundle에 대한 소규모 installed acceptance를 반복하고 G7은 최종 end-to-end gate로 남긴다. ‘미검증을 남기지 않음’은 알려진 핵심 gate 미통과면 단계 미완료, 지원하지 않는 표면/보조기술은 명시 제한·후속 작업으로 기록하는 의미로 수정한다. 전체 31개를 실행 명령 수준으로 과도하게 분해하기보다 아래 매핑에 연결한다.

renderer/server/repository/Android MainActivity의 write owner를 단계별로 지정한다. 공통 command skeleton/DTO를 먼저 합의한 뒤 surface adapter를 나누고, 동일 파일은 병렬 직접 수정하지 않는다. 기존 modified people controller는 독립 변경으로 보존·합의 후 통합한다. broad typed-port 분리가 수직 기능의 불필요한 선행 대공사가 되지 않도록 필요한 Story/result/job port부터 적용한다.

**수용:** P0/P1 각각 최소 하나의 실패 반례 fixture와 통과 증거 연결, deferred 항목 명시. 이전 감사 86 pass·부모 194/43 pass는 baseline으로만 표기. 동일 SHA 설치본/기기에서 Story ID·ordered hash·Back/retry·save conflict 확인. 공유 안전을 검증하는 실제 발행/권한/클라우드 작업은 승인된 후속 구현 권한 범위에서 수행하며 이번 조사에서 실행하지 않음.

## 3. 중복 구축·실현가능성 평가

| 항목 | 판정 | 재사용/보강 방향 |
|---|---|---|
| StorySourceSnapshot·manifest·presentation·share package | 재사용 방향 적합 | insert-only membership, append-only content history, tombstone, preparing 상태를 기존 저장소에 점진 추가. 전면 DB 교체 불필요 |
| Story command façade | 필요 | result/manual 서비스를 orchestration으로 연결하고 동일 정책 권위 유지. owner/mobile/MCP에 별도 정책 복제 금지 |
| durable Story operation | 구체화 필요 | 기존 curation claim/idempotency/replay·GPS 대기 패턴 재사용. 별도 큐/서비스 분산 신설 불필요 |
| ResultSnapshot/SelectionSet/StorySourceSnapshot | 역할 명시 필요 | 결과 권위→선택 의도→Story 고정 입력. 동일 ordered list를 세 번 독립 재생성하면 범위 드리프트 재발 |
| StoryRevision | 현재 숫자만으로 부족 | 기존 presentation revision과 본문 이력은 다름. 추천 generation version으로 본문 복원 기능 대체 불가 |
| Android View/PyObjC/WKWebView 유지 | 적합 | 네이티브 lifecycle/Back/AX·상태를 먼저 고침. SwiftUI/Compose 전면 전환 불필요 |
| 5개 테마·legacy share renderer | 적합 | 현 기능을 유지하고 순서/버전/privacy contract 아래 registry 정리. 테마 재구축보다 안전·보존 우선 |
| Story export | 범위 미정 | 기존 사진 export 서비스를 확장하되 문서 revision·본문/순서 패키지 계약은 별도 필요 |

## 4. 발견 coverage 연결표

이 표는 처리 완료가 아니라 계획 보강 제안의 연결이다. 대표 REVIEW 번호를 한 개 지정했으며 관련 항목은 본문에 추가 연결했다.

| 감사 ID | 대표 리뷰 | 기존 계획 흡수 수준 |
|---|---|---|
| STORY-01/02 | REVIEW-STORY-01 | 대상/단계0 예방 negative gate 부족 |
| STORY-03/04/05/06/07 | REVIEW-STORY-02 | snapshot/편집 방향 있음, append-only/tombstone/상태 의미 보강 |
| STORY-08/09 | REVIEW-STORY-03 | 전수 갱신/차단 cardinality fixture 누락 |
| STORY-10/11/12/13/14 | REVIEW-STORY-04 | evidence·privacy 방향 있음, claim/송신/지도 클릭·공통 identity 세부 보강 |
| STORY-15/16/17 | REVIEW-STORY-05 | partial/job 방향 있음, durable result 생성/capability·호출 gate 보강 |
| STORY-18/19 | REVIEW-STORY-07 | 모바일 복구/삭제 영향 있음, exact retry/휴지통·복원 범위 보강 |
| STORY-20 | REVIEW-STORY-05 | exact resource open/task 결합 보강 |
| STORY-21/22/23 | REVIEW-STORY-07 | pagination·오류·result export 있음, library cursor/source 접근/Story export 보강 |
| STORY-24/25 | REVIEW-STORY-08 | baseline 분리 적합, 발견별 반례·task 추적 보강 |
| STORY-26 | REVIEW-STORY-03 | share preparing/activation 원자성 누락 |
| STORY-27 | REVIEW-STORY-04 | AI 독립 attempt·오류 영속화 보강 |
| STORY-28 | REVIEW-STORY-05 | footer/명시 선택 CTA 방향 적합, 실제 workspace acceptance 필요 |
| STORY-29 | REVIEW-STORY-04 | AI 출력 budget/coverage 분리 누락 |
| STORY-30 | REVIEW-STORY-02 | scope union/timezone 제한의 명시적 계약 보강 |
| STORY-31 | REVIEW-STORY-05 | preview/capability 기반 입력 validation 구체화 |

## 5. 남은 미검증과 반영 후 재판정

**저장 후 변경 감지:** 최종 파일 확인 중 부모의 05/06 동시 보강을 감지했다. 새 hash는 05=`c1065c5d6c7f276902433744b81cc8e038ae0b361c6903052f572ef081087062`, 06=`2efb1b5f8f8f064a4ff05a6f824f712a5a95413535fbe644a45c223fdb37403c`이다. 검색으로 단계 0 G0A 예방, StoryRevision/Operation, tombstone 등의 추가를 확인했으나 새 전체 문서의 gate 충족을 다시 판정한 것은 아니다. 위 8개 판정·줄번호는 표의 원래 검토 snapshot 기준으로 유지한다. 따라서 부모는 이미 보강한 항목을 미반영 결함으로 중복 집계하지 말고 각 리뷰의 구체 해소 조건과 대조해야 한다. 본 리뷰 담당이 05/06을 수정한 것은 아니다.

- 부모 예정 단계 0 예방·snapshot/version 우선화가 실제 05/06에 반영되었는지: 이번 리뷰는 반영 전 snapshot이다. 반영표와 새 SHA에서 REVIEW-STORY-01/02/06의 task·gate를 대조해야 한다.
- approval/privacy fail-open의 정확 구조 감사 ID·설정 parsing 경로·호출별 negative 결과: 해당 담당 증거를 연결해야 하며 Story 리뷰가 새로 검증한 사실로 표시하지 않는다.
- migration/CAS/tombstone·501+ share·1,001 Story 전수 갱신, process crash/retry, 반복 materialize의 완전 경로: 미구현 계획이며 실행하지 않았다.
- 실제 AppKit 선택→생성된 ID 열기, Android 오래된 Story retry/Back/rotation/process death, VoiceOver/TalkBack와 큰 글자: 이번 새 UI 검증 없음.
- AI semantic validator·redaction·large-output budget·실제 모델 품질·네트워크 송신 보관 정책: 합성 반례는 기존 감사에서 확인, 실제 AI 미실행.
- exact GPS 공유 preview·지도 사용자 클릭 전 네트워크 차단·공유 만료/폐기 실계정 경로: 이번 실제 공유/지도/클라우드 변경 없음.
- Story export 형식·모바일 범위·버전 복구·휴지통 보존기간·performance budget: 부모가 MVP/유예 여부를 명시해야 한다.

최종적으로 **현재 문서는 좋은 방향의 제안이며, 안전 예방과 데이터 보존 계약을 앞단 gate로 구체화하면 실행 가능한 단계 계획으로 발전시킬 수 있다.** 반영 전 P0 공개 경로와 새 migration/공유 기능의 배포 적합성을 확인 완료로 표시하지 않는다. 본 리뷰의 문서 작성 작업은 완료했다.

## 6. 최종 반영 재판정

2026-10-06 사용자 요청에 따라 보강된 05/06/11의 실제 내용을 다시 읽었다. 이전 8개 판정을 아래와 같이 갱신한다. 앞의 반영 전 리뷰는 검토 이력으로 보존하며, **최종 계획 판정에는 이 절을 우선 적용한다.** 새 테스트·코드 검증·운영 변경은 수행하지 않았다.

재판정 snapshot:

| 문서 | SHA-256 |
|---|---|
| 05-product-and-feature-design.md | `c1065c5d6c7f276902433744b81cc8e038ae0b361c6903052f572ef081087062` |
| 06-execution-and-validation-plan.md | `08305a98facce1e8c76dd85b8d5f6356cf1903c4edea486191916fe8e2235d19` |
| 11-issue-register.md | `a82e8ebd03f5909744398db518e4772265093132b16c03e7308620c78f98056d` |

**결론: Story 관점의 남은 계획 누락 BLOCK 0 / HIGH 0. 검토 문서 작성 목표는 완료됐다.** 기존 위험의 예방 작업·선행 의존성·반례 gate·원장 연결이 명시되어 있으므로 이전 BLOCK/HIGH를 계획 누락으로 계속 유지하지 않는다. 아래 ‘해소’는 문서 수준이다. 운영 G0A/G2/G3/G4/G5/G6/G7은 아직 실행·통과를 확인하지 않았으며 배포·데이터 안전을 PASS로 판정하지 않는다. `11:116`이 발견 항목을 모두 open으로 두는 것은 이 구분과 일치한다.

### 6.1 기존 8개 권고별 판단

| 권고 | 최종 문서 수준 판정 | 확인한 반영 근거 | 남은 task spec 조건 |
|---|---|---|---|
| REVIEW-STORY-01 BLOCK | 해소 | 05:140-142 정확 ID/revision·latest 금지·엄격 policy/승인. 06:43-56 T0A.1~6과 missing/충돌 시 package·AI 0, fail-open/승인/health 예방. 06:17-21,60,94 G0A 선행·배포 gate. 11:69-70 STORY-01/02→T0A.1 | route별 필수 target/revision·policy fingerprint·오류 응답 및 임시 차단 해제 기준을 확정. 실제 G0A 미실행은 운영 gate 미완료이며 계획 누락 BLOCK가 아님 |
| REVIEW-STORY-02 HIGH | 해소 | 05:35-39 불변 source·append-only StoryRevision·expected base·checkpoint·claim. 06:79,86,90 T2.1/T2.8 및 최소 revision/CAS/checkpoint G3 선행. 06:50 tombstone,120 이전 revision 복구. 11:71-77 연결 | source scope union·hash canonicalization·중복 content/order·새 문서 생성 대 동일 command 재시도·CAS transaction·restore 권한을 결정. `draft/reviewed/saved`와 autosave 상태는 spec에서 분리 정의 |
| REVIEW-STORY-03 HIGH | 해소 | 05:144 전수 처리/index/cursor/outbox/consent epoch. 06:50 201/1,001 Story·501+ shares gate,116-117 bounded claim 및 preparing→active,120 public 차단. 05:150 지도 명시 행동 후 src. 11:75-81,94 연결 | 전수 완료 카운트·outbox 실패 재처리·consent epoch 평가 시점·publication activation transaction·철회 차단과 실제 파일 회수의 별도 상태 정의 |
| REVIEW-STORY-04 HIGH | 해소 | 05:38-39 AIAttempt/CaptionClaim,150 전송 종류·서버 coverage·bounded prose·unknown·지도 계약. 06:86 AI 명시 모드,115-116 attempt·민감 원문·철회 이름·1,800-token/10k 전략,120 사실 검토. 11:78-82,95,97 연결 | claim 검증/사용자 확인 규칙·송신 allowlist/redaction·attempt 실패 보존·장면별 budget·공통 identity/privacy 주입 책임을 확정. 실제 모델 품질/네트워크 검증은 후속 gate |
| REVIEW-STORY-05 HIGH | 해소 | 06:84 runtime owner/startup/shutdown/deferred,86 기존 queue/claim 재사용·checkpoint,94 G3 선행,103 결과 read 허용·새 vision/ranking job 0·kill 복구·설치 acceptance. 05:140 exact open,06:133 capability·selected Story retry,99 partial 확인. 11:83-88,96,99 연결 | claim/취소/복구 checkpoint 경계·부분 실패 counts·worker drain/join timeout·capability matrix·exact resource open을 구현 spec으로 확정. 아래 호출 문구 정합 주의 적용 |
| REVIEW-STORY-06 HIGH | 해소 | 06:191-206 legacy unverified·다중 DB/WAL/watermark·파일 hash·키 의존성·restore/failpoint·안전 rollback 금지·G2 실제 rehearsal gate. 05:144 저장소 분리 순서 | DB별 writer/quiesce·operation watermark·암호화 복원 의존성·이중 쓰기 종료·reader compatibility·reconcile matrix를 확정. 운영 DB/credential 복원 권한은 별도이며 이번 수행 없음 |
| REVIEW-STORY-07 MED | 해소(명시 유예 포함) | 05:42-44 ReadState/StoryExport;06:118 T4.9 별도 backlog·offline HTML 후보·G4 필수 범위 분리. 06:133-134 cursor/exact retry/pairing/읽기·GPS 권한 분리,153 T6.7 availability/T6.9 검색. 11:86-91,98 연결 | export는 이번 필수 편집·공유 완료 범위에서 제외하고 후속 MVP gate에 형식/privacy/manifest를 고정. 복원 보존기간·device read anchor·원본 접근과 managed copy 상태·timezone 지원 범위를 명시 |
| REVIEW-STORY-08 MED | 해소 | 06:103 단계별 설치 Mac/synthetic WebView·Android 미연결 G5 잔여,155 필수 gate와 미검증 구분,210 공유 파일 단일 write owner. 11:13-112 99개 기록 task/gate 매핑,116 구현 증거 후 closure | 구현 시 각 task spec에 발견 ID·반례 fixture·환경/SHA·담당·deferred 사유/후속 gate를 붙임. 원장 STORY-06/15의 T4 연결은 T2.8/G3 최소 저장 선행을 지연시키는 것으로 해석하지 않음 |

### 6.2 구현 전 task spec에서 확정할 조건

아래 항목은 남은 설계 세부 결정이다. 이번 상위 계획 문서의 BLOCK/HIGH 누락으로 재분류하지 않으며, 해당 task 착수 전에 확정한다.

1. **호출·상태 용어:** 05 기능 표의 `ranker 호출 0`과 06:100의 `ranker 재호출 없음`은 06:86/103의 명시 gate에 따라 **새 vision/ranking 분석 job 0, 완료 결과 read API 허용**으로 해석한다. spec/계약 테스트는 후자를 단일 기준으로 사용한다. `saved`는 검토 완료와 autosave 완료를 구분한다.
2. **선택·버전·복원:** source kind별 scope와 사진 order/hash 권위, 같은 command 재시도와 별도 Story 생성, CAS/append-only/tombstone의 원자 경계, 삭제 후 명시 복원·과거 revision 복구 및 공유 재발행 조건.
3. **운영 작업·안전 정책:** embedded/standalone worker 단일 owner, checkpoint/claim/cancel/drain의 timeout·복구 규칙; 공개 target/revision·consent epoch와 전수 outbox 완료;Preparing→active transaction; AI 송신 allowlist·claim 검토·budget·실패 attempt 보존.
4. **migration·MVP 경계:** 다중 DB/WAL/files/key 의존성의 backup/restore 방식과 비교 manifest, 안전 guard 유지 rollback, pairing/permission·read state·검색/availability 지원 범위, Story export의 명시 유예 및 후속 수용 gate.

### 6.3 실행 증거의 잔여 경계

G0A 예방 조치, source/revision migration 및 CAS, 전수 동의 반영, crash 복구, 실제 AppKit/Android 접근성·권한·Back, AI 사실성/송신·지도 요청 시점, 공개 만료/폐기와 restore rehearsal은 아직 구현·검증 결과가 아니다. 기존 baseline test 결과도 이 새 gate의 PASS로 재사용하지 않는다. 이 잔여 실행 항목을 ‘검토 문서 미완료’ 또는 ‘운영 안전 확인 완료’로 바꾸지 않는다.

최종 재판정 작성만 수행했으며 05/06/11, 제품 코드, 공유된 기존 수정은 변경하지 않았다.

### 6.4 Freeze 시점 최종 갱신

2026-10-06 추가 UX 보강 후 freeze된 문서의 변경 부분과 원장 연결을 확인했다. 최종 snapshot은 05=`504472fbb4c72d2b4c9e7115d84da6b897f84d86e126a62dcdeb080dbe71c5c3`, 06=`4b814221f5fb759085db21d1def6a7e346add984d74d78bb9aa436f792322134`, 11=`4972dc20904fa266d7c3dc255ca77070a50ecaed8980530f8b88c3fe28a50828`이다.

**Story 관점 최종 문서 판정 유지: 남은 계획 누락 BLOCK 0 / HIGH 0.** 추가 보강은 기존 권고를 앞단에서 구체화하며 기존 안전·저장 계약을 후퇴시키지 않는다.

- 05의 후보 IA·상시 작업 진입 유지, 명시 저장 편집 MVP·후속 확장 분리와 `legacy_saved` 보존은 범위·실현가능성을 개선한다. 새 vision/ranking job 0·완료 결과 read 허용 문구도 기능 표에 반영되어 이전 용어 정합 잔여를 해소했다.
- 06의 T2.9 navigation/Back/권한 복귀 및 T2.10 export receipt/empty·404 분리는 G3의 기존 선택·작업 맥락 보존을 앞당긴다. G4a/G4b는 편집 확장 유예와 안전 guard 유지의 경계를 명확히 한다. 단계4의 포괄 목록은 G4a 전체 선행이 아닌 G4a/G4b의 합산 범위로 task spec에 배정한다.
- G1 대비·keyboard/AX, G0 기존 서비스/field 재사용 매핑, 격리 개발과 통합·배포 의존의 분리, 차단 시 next action은 기존 REVIEW-STORY-05/07/08의 구체성을 높인다. 새 전체 navigation/port 구축을 작은 수정의 선행 대공사로 해석하지 않는다.

구현 전에는 각 task의 source/order/hash 권위·CAS/checkpoint/worker 소유권·정확 공개 대상/consent epoch·claim/송신 정책·복원 transaction과 MVP별 수용 fixture를 확정한다. 이는 구현 task spec의 책임이며 이번 계획 누락 BLOCK/HIGH가 아니다. **운영 G0A 및 후속 gate는 미실행/통과 미확인이다.** 문서 수준 해소를 코드 해결·배포 안전·운영 PASS로 해석하지 않는다. freeze된 05/06/11은 수정하지 않았으며 이 갱신은 09에만 추가했다.
