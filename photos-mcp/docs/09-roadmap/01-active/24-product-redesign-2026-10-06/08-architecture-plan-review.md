# Architecture 관점 통합 설계·실행 계획 peer review

- 작성일: 2026-10-06. architecture 독립 감사 담당의 후속 검토.
- 대상: [05 제품·기능 설계](05-product-and-feature-design.md), [06 실행·검증 계획](06-execution-and-validation-plan.md).
- 대조: [01 architecture 감사](01-architecture-audit.md)의 ARCHITECTURE-01~23, [03 Story 감사](03-story-audit.md)의 관련 snapshot/share 발견. 현행 코드는 필요한 공유 대상 분기와 분석 상한만 추가 확인했다.
- 소유권: 이 `08-architecture-plan-review.md`만 작성했다. 05/06, 다른 담당 보고서, 공유 중인 `people/controller.py`는 수정하거나 되돌리지 않았다.
- 검증 경계: 문서·소스 읽기와 산출물 검사만 수행. 신규 AI·실사진·HTTP·클라우드·Keychain·설치/배포 작업 및 테스트 재실행 없음. 01/03/06의 실행 결과는 각 문서 작성자의 증거로 구분한다.

## 1. 판정과 기준 문서 버전

**최종 판정: 보강 후 문서는 조건부 수용이다. 아래 1.1의 최신 반영 평가를 우선한다.** 최초 읽기 버전의 방향은 수용 가능하지만 실행 착수 사양으로는 조건부 수용이었다. 기존 PyObjC AppKit/Java Views/Python 서비스·SQLite 유지, 공통 command/query, ordered immutable snapshot, raw error 제거, 설치본 검증 분리는 적절하다. 전면 교체·서비스 분산을 요구하지 않는 점은 실현 가능성을 높인다.

반면 아래 R1의 예방 gate가 없으면 새 공유/원격 분석/쓰기 경로의 활성화·운영 배포를 허용할 수 없다. 읽기 감사·fixture 작성·격리 개발은 계속할 수 있다. R2~R5는 승인 실행·worker·버전·migration 선행조건을 명시하지 않으면 뒤 단계에서 안전 보장을 뒤늦게 다시 구현할 위험이 있다. 문서 제목의 `baseline·안전·계약` 또는 T2.5 한 행으로 상세 불변식을 충족했다고 판단하지 않는다.

부모는 P0 wrong-story-share와 approval/privacy 설정 fail-open을 단계 0 예방 조치로, snapshot/version을 P1 우선 작업으로 보강할 예정이라고 알렸다. **최초 검토는 그 보강이 반영되지 않은 아래 버전을 평가했다. 저장 직후 동시 수정이 확인되어 실제 반영된 문장을 다시 읽고 1.1에 판정을 갱신했다.** 보강되면 해당 문구와 gate를 재검토해야 하며, 예정 사실만으로 BLOCK/HIGH를 닫지 않는다.

| 읽은 파일 | 줄 수 | 읽기 기준 SHA-256 |
| --- | --- | --- |
| 05-product-and-feature-design.md | 162 | `6a7e7e536d2b134c6f0f3c509016cb1a5338a433e44e2fbf1f9cc678157fa6fb` |
| 06-execution-and-validation-plan.md | 165 | `212920412b232dce6441f0962ee98ae12b0d7d579fd4c1962021da6e7a8e4841` |

파일 hash는 소스 문서 식별용이며 개인 데이터·credential 값이 아니다. 줄번호는 이 버전을 기준으로 한다.

분류:

| 분류 | 의미 |
| --- | --- |
| BLOCK | 해당 위험을 갖는 기능 활성화·운영 release 전에 반드시 충족할 선행조건. 감사/격리 준비 작업을 막는 뜻은 아님 |
| HIGH | phase 의존성과 안전·데이터 불변식을 변경해야 하는 누락. 구현 task 분해 전 반영 필요 |
| MED | 중복/범위/계약/측정 가능성을 개선해야 하는 항목. 명시적 축소/보류도 가능하나 사유 필요 |
| LOW | 방향이 이미 적절하고 문구·측정 기록만 보완하면 되는 사항 |

핵심 수정 제안은 **8개**로 제한했다. 아래 소항목은 해당 제안의 구체 수용 기준이며 별도 무한 탐색 backlog가 아니다.

### 1.1 저장 직후 보강 문서 재확인 — 최종 적용 판정

부모의 보강은 예정으로 가정하지 않고 hash 변경을 확인한 뒤 실제 문장을 읽었다. 아래 최신 판정이 이후 2절의 **최초 버전 대상 지적**보다 우선한다. 과거 근거와 정확한 줄번호는 검토 추적을 위해 유지한다.

| 재확인 파일 | 줄 수 | SHA-256 |
| --- | --- | --- |
| 05 | 175 | `c1065c5d6c7f276902433744b81cc8e038ae0b361c6903052f572ef081087062` |
| 06 | 185 | `2efb1b5f8f8f064a4ff05a6f824f712a5a95413535fbe644a45c223fdb37403c` |

| 리뷰 제안 | 최종 상태/분류 | 실제 반영 및 남은 수정 |
| --- | --- | --- |
| APR-01 | **계획 누락 BLOCK 해소. 운영 활성화는 G0A 통과 조건 유지. MED 의존 표기 보완** | 05:140–144, 06:41–54의 T0A.1~6에서 share/strict input/privacy/health/proxy/approval/import 예방 조치를 실제 반영. 다이어그램 06:14–25에는 G0A가 없고 단계2 :72 선행은 G0만이므로 `G0→G0A→위험 경로 활성화`를 명시한다. 격리 UI 작업은 병렬 가능. 테스트·코드 해결 완료로 판단하지 않음 |
| APR-02 | **HIGH→MED 상세 검증 보완** | 05:142와 06:51에 중앙 executor/claim+started receipt 동일 transaction/목적지 reconcile/직접·복구 승인 우회 금지 반영. 잔여: failpoint 4개, 두 concurrent 소비, exact snapshot/revision/policy 변경, 목적지별 supported retry 표를 G0A task spec에 넣기. 별도 새 승인 원장 구축 불필요 |
| APR-03 | **HIGH 유지, 일부 설계 반영** | 05:38에 StoryOperation/AIAttempt, 06:105에 durable operation 추가. 하지만 배정은 단계4로 G3 이후이며 embedded/standalone dispatcher lifespan·thread join timeout·Google deferred 종료 task는 여전히 없음. 최소 worker 소유권/checkpoint/state taxonomy는 G3 선행으로 이동 |
| APR-04 | **HIGH 유지, tombstone/revoke 부분 해소** | 05:37에 append-only StoryRevision, 06:48에 tombstone/consent epoch/전수 revoke 예방 gate 반영. 최소 revision/CAS와 preparing package는 여전히 단계4(:100,107). snapshot/CAS·source 변경의 새 version 관계와 G3 생성 durable 저장을 단계2에서 확정. 편집 UI는 단계4 유지 |
| APR-05 | **HIGH 유지** | 현행 06:175–179의 versioned migration/legacy unverified/flag 방향 유지. 다중 DB/WAL/file/key consistent backup spec 및 unsafe 경로로 rollback 금지 task는 아직 구체화 안 됨 |
| APR-06 | **MED 유지, 일부 중복 방지 반영** | 05:144가 repository 분리보다 transaction/lifecycle 고정을 우선함. 기존 객체/원장 매핑, provider/account/grant/lease/expiry 및 상태 alias/schema version을 기능 spec에 추가 |
| APR-07 | **HIGH 유지** | 06:64의 상태/문구/confirmed fixture는 유지. durable ResolvePersonConflict의 expected revision·membership 보존·lineage/outbox·semantic retry를 명시해야 함. 전체 해결 기능은 T1에 몰지 않음 |
| APR-08 | **MED 유지, import task 누락 해소** | 06:52가 import alias 예방 task를 배정. deferred polling/ID별 coverage/evidence matrix와 G6 :139의 미검증 0 문구는 여전히 보완 필요. baseline 테스트 합산·미검증 완료 표현은 피할 것 |

따라서 최신 계획에는 **누락된 예방 단계 때문에 발생한 계획 BLOCK은 없다.** 구현되지 않은 G0A가 통과됐다는 의미는 아니다. 잔여 HIGH는 worker·G3 이전 최소 버전/CAS·실제 migration/rollback·인물 durable 복구 사양이다. R1/R2의 이미 반영된 요구를 다시 새로 구축하지 말고 기존 task의 테스트·의존관계를 보강한다.

## 2. 핵심 개선 제안 8개 — 최초 읽기 버전의 근거와 수정안

### APR-01 · BLOCK — 단계 0을 baseline 기록에서 예방 안전 gate로 보강

**대상 근거:** 06:29–39는 baseline/fixture/schema 전략만 요구한다. 잘못된 Story 공유의 차단은 06:88–90의 단계 4에, 승인 정책은 06:65의 단계 2에 배치되어 있다. 05:38,127은 올바른 share 불변식을 제시하지만 현재 legacy endpoint 차단의 선행조건은 없다. 05:131–137에도 invalid options/privacy enum 실패 시 동작과 health 정보 최소화가 없다.

**연결:** ARCHITECTURE-02/03/05/12/13/14, STORY-01. 현재 코드 `src/photos_mcp/interfaces/mcp/server.py:741–775`, 특히 :757–758은 여전히 최신 Story를 공유 대상으로 읽는다. 이는 새 모델 구현 전에 예방 조치가 필요한 코드 확정 증거다. 승인 토큰만으로 pending 요청이 실행되는 fail-open은 01에서 확인되지 않았다; 오히려 pending token은 차단된다. 따라서 부모가 말한 “approval fail-open”을 일반화하지 말고 **어느 입력/설정/entrypoint가 fail-open인지** 식별해야 한다.

**제안 수정:** G0에 안전 예방 하위 gate G0-S를 추가한다. 새 Story 편집 기능 전에 legacy `/photos/share`는 명시 story_id+expected revision이 없으면 차단하고 latest fallback을 제거한다. 이것이 초기에는 임시 기능 비활성화여도 된다. invalid options는 default Apple scope로 실행하지 않으며 invalid privacy policy는 config error/차단으로 처리한다. token 발급·owner approval decision·실행의 세 단계를 문서화한다. public health는 token/계획/개인 경로를 반환하지 않는다. loopback-only 판단은 listener 설정과 proxy 인증을 구별하여 trusted caller gate를 명시한다.

**수용:** A 상세를 연 동안 B를 갱신해도 A의 명시 revision만 공유; 대상 없음/삭제/충돌은 package 0개. invalid options/policy는 writer·AI 준비·네트워크 호출 0. pending/rejected/expired token의 write 0. health sentinel secret/path/photo ID 0. proxy를 통해 들어온 비인가 automation 요청 실행 0. endpoint 노출 여부는 별도 read-only 배포 audit로 확인한다.

**조건부 판단:** 부모가 예방 조치를 단계 0에 넣더라도 목표 문구만으로 닫히지 않는다. legacy route 차단/정확 대상 binding/negative test와 G0-S 완료 전 release 금지가 확인되면 BLOCK 해소 가능. 전체 Story 버전/편집 구현까지 단계 0에 넣을 필요는 없다.

### APR-02 · HIGH — 승인 실행기를 transaction·목적지 재조정까지 구체화

**대상 근거:** 05:39,133–135와 06:T2.5(:65), G2(:67)는 승인/idempotency/negative test 방향만 적는다. consume+started receipt 원자성, source exact set, 목적지별 retry/reconciliation은 명시되지 않는다. 06:151의 “미승인 쓰기 자동 실행 금지”는 필요한 조건이지만 충분하지 않다.

**연결:** ARCHITECTURE-03/04/08/09/18. 특히 01의 `mutation_approval.py:205–218`과 `run_repository.py:2579–2587`의 소비 commit 뒤 `server.py:341–345` receipt 저장은 분리된다.

**제안 수정:** T2.5를 작은 명시 task로 나눈다: (a) planner/owner decision/executor 역할 분리, (b) repository transaction의 claim+started receipt, (c) 목적지 adapter별 reconciliation capability, (d) 실제 write 또는 write resume의 중앙 executor 강제. `ApprovedMutationCommand`는 repository에서 읽은 exact snapshot/destination/revision/policy와 결합하고, 내부 handler가 현재 selected/head를 다시 조회해 확장하지 않는다. `resume` 승인은 actual destination mutation 승인을 무조건 대신하지 않는다.

**수용:** 두 concurrent consume에 외부 효과 최대 1회. consume 직전/직후/receipt 직후/writer 성공 후 각각 failpoint에서 durable receipt와 recovery action 존재. pending/rejected/expired/대상 변경/동의 변경은 write 0. partial/timeout/import/category/cleanup/local export/publish의 action별 next action 명시. unknown은 completed로 추정하지 않는다.

**중복·이행:** 기존 mutation plan/receipt, desktop export, selected bundle, recommendation destination receipt를 재사용한다. 새로운 별도 approval DB/쓰기 프레임워크를 만들지 않는다. consumed-no-receipt 과거 레코드는 자동 실행 없이 recovery 후보로 남긴다. 내부 caller 목록과 호환 wrapper 이행을 G2 완료 조건에 넣는다.

### APR-03 · HIGH — worker 소유권·embedded lifespan·복구 작업을 G3 선행조건으로 배정

**대상 근거:** 05:70–74는 projection과 revision polling, 06:T2.4(:64)는 state parity를 제안한다. 그러나 daemon 실제 종료, mobile embedded/standalone worker startup·shutdown, coordinator task cancel/await에 담당 task가 없다. 06:98과 :151, G7(:127)에 재시작 결과만 제시하면 복구 구현을 마지막 검증 시점에 발견하게 된다.

**연결:** ARCHITECTURE-01/06/07/19/22, STORY-15. queue terminal/deferred 처리와 parent operation→child→action request 연결은 단순 화면 polling으로 해결되지 않는다.

**제안 수정:** 단계 2에 `T2.6 OperationLifecycle`을 추가하고 완료 결과→Story G3의 선행조건으로 지정한다. 하나의 composition root가 dispatcher/worker/task/DB owner를 소유한다. embedded와 standalone은 같은 lifecycle service를 사용하되 실제 실행 소유자 하나를 배포 topology로 정한다. drain/cancel/await/checkpoint/receipt flush/thread 종료 확인 후 stopped로 전환한다. join timeout이면 degraded/stopping이며 old thread 참조를 잃지 않는다. 필수 user action 생성은 operation 저장과 transaction/outbox로 연결한다. deferred polling 즉시 종료와 carry-over를 공통 terminal taxonomy에 포함한다.

**수용:** fake worker의 정상 종료·timeout·CancelledError·재시작 2회에서 active owner 최대 1. startup stale requeue 및 shutdown dispatcher 정리 호출 확인. 실패·재시작 후 durable operation/action/checkpoint 존재. 모든 persisted status가 running/resource-wait/user-action/history 중 하나로 매핑되어 승인 대기 누락 0. Google deferred 응답 뒤 장시간 polling/불필요 cancel 0.

**실현 가능성:** 서비스 분산/새 event bus 없이 기존 asyncio/thread와 SQLite를 정리하는 범위로 제한한다. ASGI lifecycle fake 테스트는 G2, 설치본 두 listener/thread 확인은 중간 package smoke와 G7에서 수행한다. G7에만 모든 lifecycle 증거를 미루지 않는다.

### APR-04 · HIGH — snapshot과 최소 revision/CAS/tombstone 계약을 편집 단계보다 먼저 고정

**대상 근거:** 05:33–38과 06:T2.1(:61)은 membership/order/hash를 올바르게 요구한다. 하지만 content/presentation revision·편집 conflict·삭제/공유는 대부분 단계 4(:84–90)다. G3(:78)는 새 snapshot에서 동일 3장만 검사하며 legacy snapshot/read path와 concurrent content/share 문제를 닫지는 않는다. StoryShare 제안 필드(05:38)에는 content revision만 명시되고 presentation/renderer/policy/consent epoch 연결은 다른 객체·본문에 분산되어 있다.

**연결:** ARCHITECTURE-15/18/19, STORY-03/04/06/07/09/26.

**제안 수정:** 단계 2에는 ordered source/result/selection snapshot의 insert-only 계약, command idempotency, content revision의 최소 저장·CAS, tombstone 존중, share에 고정할 content/presentation/renderer/disclosure policy 버전을 먼저 정의한다. 풍부한 편집 UI·장면 조작은 단계 4 유지. immutable source membership과 사용자가 편집한 새 Story version membership의 관계를 명시한다. `사진 추가/제외`는 source snapshot을 in-place 바꾸는지 새 snapshot/revision을 만드는지 반드시 결정한다.

**수용:** 같은 job {A,B}→{C}가 서로 다른 snapshot이고 이전 객체가 변하지 않음. 동일 command만 동일 객체. A/B/A와 역순 선택의 order 일치. deleted Story에 evidence가 추가되어도 부활하지 않음. concurrent update는 CAS conflict. reviewed revision의 preview hash와 preparing→active package hash 동일. 모든 영향받는 share를 cursor로 전수 revoke하며 임의 limit 뒤 항목 누락 0. partial source와 narrative readiness를 별도 필드로 유지.

**조건부 판단:** 부모의 P1 snapshot/version 보강이 최소 저장 계약·선행 gate까지 포함하면 HIGH 해소 가능. 단순 우선순위 표 변경은 충분하지 않다. 완전한 버전 편집 제품을 G2에서 만들 필요는 없다.

### APR-05 · HIGH — 다중 DB·WAL·파일·키 의존성을 포함한 migration/rollback 실행 계약 필요

**대상 근거:** 06:37,155–159는 migration version/legacy unverified/dual-write/feature flag를 적절히 언급한다. 그러나 backup 대상과 consistent point, DB별 version compatibility, outbox/원본 파일과 commit 경계, rollback이 보호할 정보가 구체적이지 않다. 05:159의 기능별 repository port 분리도 물리 DB 분리와 구별할 필요가 있다.

**연결:** ARCHITECTURE-10/11/15/19. jobs.db 외 people private DB, mobile auth DB, encrypted GPS ledger, Google lease/session/upload receipt와 retained originals가 존재한다. JSON legacy import도 canonical storage와 다르다.

**제안 수정:** 단계 0에 versioned storage inventory와 backup/restore manifest spec, 단계 2/3에는 migration rehearsal task를 명시한다. DB별 schema version/reader-write compatibility와 SQLite backup API 또는 quiescent snapshot 정책을 정의한다. retained originals의 relative path/hash/reference와 key 의존성은 manifest에 적고 secret 자체는 적지 않는다. 다른 DB에 걸친 작업은 transaction 하나로 보장한다고 쓰지 않고 outbox+reconcile/compensation 계약을 쓴다.

**수용:** old-schema→upgrade→동일 migration 반복→중간 중단→restore fixture가 통과하고 integrity/FK/reference orphan 검사 결과 기록. DB state와 asset set의 시점 정합 확인. key 없는 restore는 명확한 blocked 상태. IDs/consent/receipt/share link 유지. unknown legacy scope는 새 immutable 사실로 자동 승격하지 않음.

**rollback 경계:** read/UI feature flag는 사용 가능하지만 R1 unsafe latest-share/default-scope/privacy fallback이나 R2 무승인 내부 writer로 돌아가는 flag는 허용하지 않는다. rollback은 안전하게 제한된 이전 reader 또는 disable 경로다. 실제 private DB·키 변경은 별도 승인이며 이 리뷰에서 실행하지 않았다.

### APR-06 · MED — 새 객체·port를 기존 서비스의 확장으로 매핑하고 schema 누락을 닫기

**대상 근거:** 05:27,66과 06:T2.3(:63)는 재사용·점진 분리 원칙이 적절하다. 다만 05:31–40의 새 객체와 05:133의 commands가 기존 구현 어느 원장을 재사용하는지 명시되지 않는다. AnalysisRequest에 provider/account/grant/lease/origin policy가 없고, ExportPlan에 approved decision/consent policy revision, 공통 응답에 schema_version/idempotency/request_hash가 명시되지 않는다. 상태 문자열도 worker `cancelled`와 제안 `canceled`, `deferred`와 `retry_wait` 등의 mapping table이 없다.

**연결:** ARCHITECTURE-15/16/17/18/20.

**제안 수정:** 기능 spec에 “제안 객체→기존 서비스/테이블→새 필드→migration→최종 owner” 매핑을 추가한다. `ResultStoryService`, `StorySourceSnapshot`, recommendation versions, `PeopleWorkspaceService`, manual/combined operation, mutation receipt, mobile auth/session/nonce를 먼저 활용한다. DTO에는 source provenance/expiry/policy version, schema version과 state classification을 포함한다. SourceRegistry와 VendorPhotoSourcePort를 동시에 새로 확장하지 말고 한 adapter를 통한 translation으로 수렴한다.

**수용:** 같은 명령에 두 independent 원장/ID/승인 상태를 만들지 않음. 기존 네 MCP 도구와 aliases/wrappers 읽기 호환. 모든 provider origin이 resume/export/people 경계에서 유지되며 Google face 금지 회귀 0. relative/dynamic import 검사와 typed gateway negative response 테스트. 신규 port는 대형 파일을 나눈 수가 아니라 concrete 호출/정책 중복을 줄인 근거로 평가.

**LOW 보완:** 10,000/2,000/100 상한은 이미 `src/photos_mcp/application/analysis_limits.py:12–24`에 존재한다. T6의 논리 operation/provider session 검사에서 새 limit 상수를 만들지 말고 이를 재사용한다. 수치는 이번에 재설계한 기능이나 새 실측 성과로 표현하지 않는다.

### APR-07 · HIGH — 인물 conflict를 오류 문구 교체가 아닌 durable 복구 명령으로 구체화

**대상 근거:** 05:42,98,133–135와 06:T1.3(:49), :148은 unknown/충돌·확정 연결 유지 방향을 반영한다. 하지만 T1.3의 “다시 시도와 입력 보존”만으로 conflict 회복을 검증할 수 없다. source failure와 semantic membership conflict는 다른 재시도다.

**연결:** ARCHITECTURE-23, ARCHITECTURE-18/19, LIVE-04. 01에 기록한 parent live는 과거 보존 오류 관찰이며 현재 DB 원인과 동일 이름 카드의 실제 duplicate는 미확인이다.

**제안 수정:** T1.3에는 `PeopleWorkspaceStatus`의 catalog/queue freshness, last-good overview, index run failed/partial, safe error/diagnostic reference를 명시한다. `ResolvePersonConflict`는 단계 2/3의 기존 identity command receipt/revision/lineage/outbox를 사용한다. owner-confirmed 연결 유지/명시 이동/별도 인물 유지/보류를 구분한다. 이름으로 자동 병합하거나 conflict 때 자동 재색인을 실행하지 않는다. 동일 이름 카드는 identity ID와 provenance를 비식별 구분하는 UI spec으로 연결한다.

**수용:** dashboard read failure→unknown; index failure+queue 0→완료 문구 금지; last-good 표시에는 stale 명시. semantic conflict의 “다시 시도”는 기존 owner membership을 바꾸지 않음. expected revision 경합은 durable action, 성공한 resolve는 idempotent receipt+lineage+Story refresh outbox. 다른 client/partial index/restart 뒤 동일 conflict 상태를 조회. raw exception name/message와 개인 경로 노출 0.

**순서:** T1에서는 state projection/문구·안전 조치만 먼저 수정할 수 있다. 전체 이동·병합/undo 기능을 T1에 몰아넣지 않고 G2 이후 기능으로 분리한다. 현재 라이브 실패의 원인 확정과 데이터 수정은 별도 승인된 비식별 진단 후 진행한다.

### APR-08 · MED — 발견→task→gate→증거 추적 표와 측정 가능한 gate를 완성

**대상 근거:** 06:35,39,129–151,163–165는 coverage/evidence 분리 원칙이 좋지만 ARCHITECTURE ID별 task와 보류 사유가 없다. 06:G6(:119)의 “플랫폼별 미검증을 남기지 않고”는 미검증이 있을 수 있다는 문서 철학·실기기 권한 제약과 충돌한다. 성능 budget은 :112에서 나중에 확정하므로 :119의 G6 종료 전에 누가 언제 baseline/threshold를 결정할지 필요하다.

**연결:** ARCHITECTURE-05/20/21/22 및 전체 01~23.

**제안 수정:** 부모 07 통합표 또는 06 부록에 각 발견 ID/task/owner/dependency/acceptance fixture/evidence tier/status/defer reason을 기록한다. import_then_curate 실제 의미 불일치는 이름/alias 폐기 또는 진짜 선별 구현 중 선택을 task로 배정한다. deferred immediate exit는 R3에 넣는다. action doc 테스트는 이름 집합뿐 아니라 required/type/default/approval/response/error contract를 확인한다. G6 문구는 “필수 gate 검증 완료, 나머지 미검증과 보류 사유 및 release 제약 기록”으로 수정한다.

**수용:** 발견 23개 모두 해결/부분/보류가 추적되고 포괄 문구만으로 closed 처리 0. fake integration/isolated ASGI/fault injection/package/native/live를 별도 증거 열에 기록. 부모 194+43, architecture 27의 숫자는 overlapping 가능성이 있으므로 합산 total로 독립 coverage를 주장하지 않는다. 성능 baseline 기기/fixture/측정 항목/예산 승인 시점 명시; 아직 미측정 숫자를 SLA로 선언하지 않음.

## 3. ARCHITECTURE-01~23 coverage 평가

아래 표는 최초 읽기 버전의 coverage다. 최신 보강으로 02/03/04/05/08/09/12/13/14의 예방 task와 일부 tombstone가 반영되었으며 1.1이 우선한다. “부분”은 방향 반영을 뜻하며 안전 gate 완료나 구현 완료를 의미하지 않는다.

| 감사 ID | 현 05/06 반영 | 잔여/본 리뷰 연결 |
| --- | --- | --- |
| 01 승인 대기 목록 | 05:32,70–74; T2.4 부분 | 모든 상태의 분류/목록 coverage 및 latest 고정, R3 |
| 02 invalid options | 명시 task 없음 | G0-S 엄격 입력/범위 기본 실행 차단, R1 |
| 03 승인 3단계 | T2.5 포괄 문구 | 신뢰 owner decision/토큰·문서 parity, R1/R2 |
| 04 내부 writer 경계 | 05:66; T2.5 부분 | 중앙 executor/entrypoint 목록/resume, R2 |
| 05 import alias 의미 | 누락 | 지원 의미 결정·alias migration/정확 plan, R1/R8 |
| 06 종료 보장 | G7 재시작 결과만 | worker drain/join/receipt checkpoint 선행 task, R3 |
| 07 embedded lifespan | 누락 | standalone/embedded owner topology/lifespan, R3 |
| 08 consume crash window | T2.5 포괄 문구 | claim+started receipt transaction/failpoints, R2 |
| 09 reconciliation | Export receipt/복구 포괄 문구 | 목적지별 capability·partial retry, R2 |
| 10 원본 보관 | 05:39; T4.4/T6 storage 부분 | retained original/materialized/cache 수명 문서, R5/R8 |
| 11 multi-DB backup | 단계0/rollback 부분 | WAL/file/key/version consistent manifest, R5 |
| 12 health 상세 token | 누락 | public minimal projection/owner diagnostics, R1 |
| 13 listener 신뢰 | 표면 권한표/G5 부분 | config.host 대신 caller/proxy 경계, R1 |
| 14 privacy typo/별도 송신 | 공유 disclosure만 부분 | strict privacy config + 송신 종류별 정책, R1/R6 |
| 15 origin provenance | 05:40,42; G2 부분 | grant/lease/account/expiry/origin policy fields, R6 |
| 16 import test 한계 | T2.3 부분 | relative/dynamic/stdlib I/O+실제 port rules, R6 |
| 17 typed/vendor 병존 | T2.3 부분 | 신규 registry 이중 구축 방지/translation, R6 |
| 18 envelope 상태 | 05:32,135; T2.4 부분 | schema version/ID map/safe error/완전 state map, R2/R3/R6 |
| 19 parent/child/action | G5 조치함 부분 | 원장 relationship/transaction/outbox G3 전 확보, R3 |
| 20 현행 docs 누락 | T6 문서 부분 | action schema parity+storage/auth/current path, R8 |
| 21 evidence tiers | 06:129–151 적절한 방향 | coverage matrix/미검증 종료 문구·성능 gate, R8 |
| 22 deferred polling | 누락 | shared terminal rule 즉시 종료/carry-over, R3/R8 |
| 23 인물 conflict | T1.3·negative scenarios 부분 | typed error/freshness/durable resolve/lineage, R7 |

## 4. 권장 의존 순서와 범위 관리

현재 G0→G1/G2→G3→G4/G5의 큰 구조는 유지할 수 있다. 변경은 다음 선행조건을 붙이는 정도로 제한한다.

1. **G0-B baseline + G0-S 예방 안전**: 기존 ownership/배포/fixture 기록과 위험 entrypoint 차단을 분리. G0-S 전에는 운영 공유/원격 분석/쓰기 활성화 금지.
2. **G1 상태/UI 보정**과 **G2-A snapshot 최소 버전·승인 실행기·OperationLifecycle**를 진행. G1/G2가 `state_store`, people projection, result service 등을 함께 수정한다면 disjoint 조건은 충족하지 않는다. 파일 소유자와 shared DTO owner를 먼저 정한다.
3. **G2-M migration rehearsal**: DB schema를 실제 변경하기 전에 실행. 기존 versions/receipt/outbox 확장만 우선하고 물리 DB 통합·분산은 제외.
4. G1+G2-A+G2-M 후 **G3 결과→초안 수직 기능**. 순서/선택/partial/idempotency뿐 아니라 mid-phase restart/failure를 포함.
5. **G4 편집 UI/버전 관리**와 **G5 모바일 경험**. 최소 CAS/tombstone/share binding은 이미 선행 단계에서 확보했으므로 이 단계는 제품 완성에 집중.
6. **G6/G7 성능·접근성·설치본·허용된 실환경 수용**. 중간 package smoke도 수행하여 수명주기 문제를 최종 단계까지 지연하지 않음.

실현 가능성을 위해 10,000장 지원을 새로 구축하지 않는다. existing limit·download batch·checkpoint의 회귀와 전체 수명주기 검증을 한다. port 분리는 안전 불변식이 필요한 서비스부터 하고 전체 repository 분해를 G3의 필수 큰 refactor로 만들지 않는다. Story version/CAS와 mutation executor의 transaction 단위는 먼저 결정해야 한다.

## 5. 남은 미검증과 조건부 수용

- 부모 보강은 저장 중 실제 반영되어 1.1에서 재확인했다. 이후 추가 변경은 이 리뷰의 최신 hash와 다를 수 있으며 해당 revision의 task/gate 의존관계를 다시 확인해야 한다.
- 설치 Mac bundle/Android APK와 현재 checkout의 동일 revision 여부, embedded/standalone control service의 실제 실행 topology는 미확인이다.
- Tailscale Serve/Funnel 노출 경로와 trusted header 처리, MCP SDK의 input/schema/transport 보호 범위는 라이브로 검증하지 않았다. listener host만으로 공개 취약점 발생을 확정하지 않는다.
- 실제 multi-DB schema/retained files/lease expiry/Keychain 복원 상태를 읽지 않았다. migration 시간·공간·복구 시간 예산은 미산정이다.
- 부모 인물 오류의 정확한 최초 발생 stack, actionable queue 실제 count, 동일 이름 카드의 duplicate 여부는 미확인이다. 이름/실사진/실 DB 정보는 리뷰에 기록하지 않았다.
- claim crash window·thread join timeout·cancel propagation·partial publish·consent revoke 전수 coverage는 이번 peer review에서 실행하지 않았다. 01/03의 코드 추론과 합성 실험을 실환경 확정으로 바꾸지 않는다.
- 모델 품질/성능, 10,000장 memory/render budget, native AX/TalkBack/VoiceOver, actual destination roundtrip은 미검증이다. fixture와 reference 기기를 지정한 후 담당 gate에서 검증한다.
- 문서에 나온 새 편집/버전 command는 구현 제안이며 deployed capability로 판단하지 않는다.

**재검토 종료 조건:** 부모 반영표에 APR-01~08의 반영/부분/보류 이유를 남기고 23개 감사 ID를 task/gate에 연결하면 문서 검토를 닫을 수 있다. 코드 해결은 각각의 구현·검증 증거가 생긴 후 별도로 닫는다. 이 리뷰 작성 완료는 제품 구현 완료 또는 안전 gate 통과를 의미하지 않는다.

### 마감 시점 추가 변경 경계

최종 링크/ID 검사 중 06이 다시 196줄, SHA-256 `60534e6b40dacc2aaa0790034b7aa79c09903765e851e72450a89d5b1c415669`로 변경되었다. 동시 편집의 이후 전체 차이는 이번 리뷰에 재반영하지 않았다. 따라서 1.1의 185줄 버전까지가 근거를 읽고 판정한 범위이며, 부모는 이후 수정이 APR-03~08을 충족하는지 반영표에서 연결해야 한다. 계속 바뀌는 문서를 추적하며 리뷰 완료를 무기한 지연하지 않는다. 05/06 직접 수정은 하지 않았다.

## 최종 반영 재판정

**재검토일: 2026-10-06. 최종 결론: architecture 관점의 남은 계획 누락 BLOCK/HIGH 없음. 검토 문서 완료로 수용한다.** 아래 판정은 앞선 최초/중간 버전의 잔여 HIGH 및 마감 변경 경계를 대체한다. 구현 완료나 코드 검증 PASS를 뜻하지 않는다.

이번에는 보강된 05/06/11의 실제 내용을 읽었다. 05는 175줄, 06은 216줄이며, 11은 모든 발견을 open으로 유지하면서 99개 감사 기록을 task/gate에 연결한다. 99개가 서로 독립적인 결함이 아니라 겹치는 원인·증상을 포함한 감사 기록이라는 설명도 유지된다. architecture 23개 각각의 매핑을 확인했다. 새로운 테스트·코드·운영 변경은 수행하지 않았다. 이번 변경은 이 08 문서 끝의 재판정 추가뿐이다.

| 재검토 문서 | SHA-256 |
| --- | --- |
| 05-product-and-feature-design.md | `c1065c5d6c7f276902433744b81cc8e038ae0b361c6903052f572ef081087062` |
| 06-execution-and-validation-plan.md | `08305a98facce1e8c76dd85b8d5f6356cf1903c4edea486191916fe8e2235d19` |
| 11-issue-register.md | `a82e8ebd03f5909744398db518e4772265093132b16c03e7308620c78f98056d` |

### 8개 권고의 문서 수준 반영 결과

| 권고 | 최종 판단 | 실제 반영 근거 | 구현 전 task spec에 남길 조건 |
| --- | --- | --- | --- |
| APR-01 예방 안전 gate | **문서 수준 해소. 계획 BLOCK 없음** | 06:17–21의 G0A→G3 DAG, :49–54의 T0A.1~6, :60의 배포 전 G0A, :94의 G3 선행 G0A/G1/G2. 05:140–150에서 정확 대상·승인·strict input/privacy·최소 health·송신 구분 | 실제 legacy entrypoint 적용 목록·trusted proxy/caller 조건·임시 차단의 해제 증거를 정한다. G0A 미실행을 완료로 표시하지 않음 |
| APR-02 승인 transaction/재조정 | **문서 수준 해소. 기존 HIGH/MED 요구 배정 완료** | 06:53의 중앙 executor와 동일 DB claim/receipt transaction, :204의 네 failpoint/동시 소비/목적지 capability 표. 05:142의 plan→owner decision→execute | SQL transaction 범위·exact snapshot/policy/revision binding·목적지별 retry 가능한 잔여 집합·resume의 재승인 종류를 정한다. 기존 approval/receipt를 재사용 |
| APR-03 worker/lifespan/복구 | **문서 수준 해소. HIGH 없음** | T2.6(06:84)에 runtime owner 1개·embedded/standalone startup/shutdown·drain/cancel/await/checkpoint/join·deferred·두 번 restart 명시. T2.8(:86), :94 및 G3(:103)에 내구 저장/worker 선행·checkpoint 종료 복구 | 배포 topology의 실제 worker owner, 취소·join timeout budget, deferred→terminal/action-required 상태표 및 parent/child/action 저장 단위를 확정 |
| APR-04 최소 snapshot/revision/CAS | **문서 수준 해소. HIGH 없음** | T2.1(:79), T2.8(:86), G2 설명(:90)에 insert-only source·content revision/CAS/tombstone/checkpoint를 G3 전에 고정. G3(:103)는 새 vision/ranking job과 완료 결과 read API 구분. 전체 편집 UI는 단계4 유지 | source membership 수정은 새 snapshot/revision으로 표현할지 정확히 정하고, content/presentation/renderer/disclosure/consent version의 package binding·CAS 충돌 응답을 명세 |
| APR-05 multi-DB backup/migration/rollback | **문서 수준 해소. HIGH 없음** | 06:199–206에 DB별 writer·SQLite backup/WAL·operation watermark·managed files hash/ref·key 복원 의존성·old→new/failpoint/restore·안전 rollback 금지·G2 전 rehearsal 명시 | snapshot consistency를 보장할 quiesce 또는 watermark 프로토콜, DB별 schema/reader compatibility, 키 미복원 시 차단·compensation·보존 manifest를 결정. 운영 migration은 별도 허용 범위 |
| APR-06 기존 서비스 재사용/typed 계약 | **문서 수준 해소. task spec 세부화만 남음** | 05:27,66,144의 재사용/분산 금지/경계 우선, 06:86,90의 기존 queue/claim/원장 재사용·field mapping/state alias/schema version·provider/account/grant/lease/expiry/provenance. :210의 공유 파일 단일 write owner | concrete 서비스/테이블→새 DTO 필드 매핑과 migration owner, relative/dynamic import rule·vendor 응답 validation을 확정. 기존 10,000장 상한/port/원장 중복 구축 금지 |
| APR-07 인물 durable conflict 복구 | **문서 수준 해소. HIGH 없음** | T1.3(06:66)의 unknown/stale/error, T2.7(:85)의 ID 구분·owner-confirmed 보존·revision/CAS/lineage/undo/outbox·concurrent conflict. 11에서 ARCHITECTURE-23 및 UX-23/24를 연결 | catalog/queue freshness와 last-good 표시, 보류/유지/이동의 command 의미, expected revision·idempotent receipt·outbox·compensating revision을 정한다. 이름만으로 병합/자동 재색인하지 않음 |
| APR-08 coverage/evidence/gate | **문서 수준 해소. 추가 계획 BLOCK/HIGH 없음** | 11의 99개 ID/task 매핑·전 항목 open·완료 증거 규칙. 06:155의 미검증 보류/담당·후속 gate, :169–172의 baseline 한계, :206/210의 rehearsal·write owner. T4.9(:118)는 Story export 명시 유예, T5.7(:134)는 pairing/권한, T6.9(:153)는 search/cursor | task별 담당자·fixture·증거 등급·reference 기기/성능 budget·명시 유예 release 범위를 확정. overlapping pass 합계로 coverage를 주장하지 않음 |

T1.6의 대비/고정 badge, T5.7의 연결·권한 분리, T6.9의 source 권한 범위 내 검색도 누락 없이 배정되어 있다. 이들은 architecture에서 새 구축 요구로 확장하지 않고 해당 UX/mobile task의 공통 상태·인가 계약과 연결한다.

### 계획 누락 BLOCK과 운영 gate의 구별

- **계획 누락 BLOCK/HIGH: 없음.** 앞선 권고의 선행조건·소유권·검증·migration·보류가 실행 계획과 원장에 배정되었다. 더 큰 상위 계획 문서를 다시 쓰도록 요구하지 않는다.
- **운영 G0A: 아직 미실행.** 정확 대상/승인/privacy/public guard가 구현·검증되기 전 위험 경로 활성화·배포를 허용한다는 의미가 아니다. 이 상태는 미완료 구현 gate이며 peer review 문서의 누락 BLOCK과 다르다.
- **발견 기록: 계속 open.** 11의 규칙대로 실제 변경과 자동/설치본/기기 증거가 생긴 뒤 닫는다. 이번 문서 재검토로 결함 해결·코드 PASS·실환경 수용을 주장하지 않는다.

### 구현 전 task spec에서 확정할 최소 조건

1. **실행·인가 계약:** entrypoint/caller/worker owner, exact snapshot·revision·policy binding, 원자 claim/receipt SQL, 목적지 reconciliation 표와 취소/join budget.
2. **데이터 이행 계약:** 객체·기존 원장 매핑, provider/grant/lease provenance, 새 source/content version의 생성 의미, multi-DB consistent snapshot/restore·reader compatibility와 안전 rollback.
3. **인물·상태 계약:** unknown/stale/partial/deferred/approval 대기의 완전한 mapping, parent/child/action의 durable 연결, conflict decision/CAS/lineage/undo/outbox. 현재 보존 인물 오류의 원인은 여전히 별도 비식별 진단 대상.
4. **증거·수용 계약:** task별 실제 write owner·fixture·baseline·reference 기기/성능 budget·증거 등급과 live 권한 범위. 05:122의 “ranker 호출 0” 및 06:100의 “재호출하지 않음”은 구체 G3(:103)에 따라 **새 분석 job 시작 0, 완료 결과 read 허용**으로 task spec에 통일한다. 이는 LOW 문구 명확화이며 새 기능/계획 차단 사유가 아니다.

실제 topology/proxy 노출, 운영 DB와 키 복원, 프로세스 종료/동시 writer, live 사진·AI·목적지 roundtrip·기기 성능은 이번에도 실행하지 않았다. 이 미검증은 위 task/gate의 구현 증거로 남기며, 검토 문서 완료를 지연시키는 새 조사 요구로 추가하지 않는다.


### 동결본 갱신 판정 — 추가 UX 리뷰 반영 후

**2026-10-06 최종 동결본 기준: 기존 APR-01~08의 문서 수준 해소 판정을 유지한다. 남은 계획 누락 BLOCK/HIGH는 없다.** 이번 추가는 앞단 계약과 수용 gate를 강화하며 예방 안전·내구성 의존을 후퇴시키지 않는다. 위 재판정의 과거 줄번호와 hash는 당시 판본의 이력이며, 현재 판본 식별에는 아래 hash를 사용한다.

| 동결 문서 | SHA-256 |
| --- | --- |
| 05-product-and-feature-design.md | `504472fbb4c72d2b4c9e7115d84da6b897f84d86e126a62dcdeb080dbe71c5c3` |
| 06-execution-and-validation-plan.md | `4b814221f5fb759085db21d1def6a7e346add984d74d78bb9aa436f792322134` |
| 11-issue-register.md | `4972dc20904fa266d7c3dc255ca77070a50ecaed8980530f8b88c3fe28a50828` |

- **순서·작업 보존:** T2.9/T2.10(06:91–92)이 최소 navigation/Back/권한 복귀와 export receipt/빈 상태 분리를 G2에 포함하며 G3의 G2 선행(06:102)을 유지한다. 후보 IA·상시 작업 진입 유지(05:112), 기존 서비스 field 매핑(06:98), 개발 병행과 위험 경로 통합/배포의 구별·차단 후 next action(06:58)은 APR-03/06/08 판단을 보강한다.
- **편집·호환성:** 명시 저장 MVP와 legacy_saved 보존(05:169, 06:117), G4a/G4b 분리는 최소 CAS/내구 저장을 G3 앞에 두는 APR-04 판정과 일치한다. 구현 task spec에서는 G4a/G4b 각각의 종료 기준을 전체 G4 gate(06:130)에서 분해하고, 미저장 상태의 Back/창 닫기와 legacy 공유 경로에도 예방 public guard를 적용한다. 이는 명시된 단계 분할의 구체화 조건이며 새 계획 HIGH가 아니다.
- **검증·문구:** G1의 대비·keyboard/AX gate(06:75)는 변경된 주 행동을 앞단에서 검증한다. 05:124와 06:111의 새 vision/ranking job 0·완료 결과 read 허용으로 이전 LOW 문구 권고도 해소됐다. 06:108의 재호출 금지는 이 구체 수용 계약에 따라 해석한다.

기존 네 가지 구현 전 task spec 조건(실행·승인, 데이터·복원, 상태·conflict, 증거·소유권)은 유지한다. **운영 G0A는 미실행이고 11의 발견은 계속 open이다.** 이번에는 문서·hash만 확인했으며 새 코드 검사·테스트·운영 변경을 수행하지 않았다. 따라서 문서 검토 완료를 코드 해결, 운영 gate 통과 또는 배포 허가로 해석하지 않는다. 05/06/11 동결본과 공유 변경은 수정하지 않았다.
