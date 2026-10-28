# 개선 계획 검토와 반영 결과

> 작성일: 2026-10-06
> 대상: [05 설계](05-product-and-feature-design.md), [06 작업 계획](06-execution-and-validation-plan.md), [11 원장](11-issue-register.md)
> 검토 방식: 독립 감사한 동일 3개 에이전트가 통합 초안을 재검토하고 부모가 수정·반영. 새 에이전트 추가 없음.
> 주의: 문서 수준 검토와 실제 구현·설치본·기기·운영 gate 통과는 별개다.

## 1. 검토의 결과

초기 설계 방향은 기존 PyObjC/Java Views/Python/SQLite·renderer를 유지하며 command/query·scope·상태를 정합화하는 것이었다. peer review에서 **안전 예방·최소 버전/CAS·worker 내구성·migration 계약의 순서를 앞당겨야 한다**는 지적과 **편집 MVP·탐색 비교·Back/복원·기기 부재를 더 명확히 해야 한다**는 지적을 받았다.

그 결과 예방 수정은 G0A, 최소 snapshot/revision·worker/route 복원은 G2, 결과→Story는 G3, 작은 편집 MVP는 G4a, 후속 편집은 G4b로 구분했다. 공유·승인·privacy·삭제 guard는 UI rollback으로 제거하지 않는다. 감사 기록 99개를 모두 작업/gate에 연결했고 기록은 전부 open으로 유지한다.

**최종 동결본 재판정:** 구조·Story·UX 세 담당 모두 05/06/11을 다시 읽고 **계획 문서 수준의 잔여 BLOCK 0 / HIGH 0**으로 수용했다. 8개씩 총24개 권고의 반영·후속 task spec 조건이 각 리뷰 말미에 기록됐다. 이는 계획의 누락을 보강했다는 뜻이며 실제 결함 해결, G0A 또는 후속 운영 gate 통과, Android 배포 허용을 뜻하지 않는다.

개별 리뷰의 최초 지적과 중간 판정은 검토 당시 hash를 기준으로 보존된다. **각 리뷰 말미의 ‘최종 반영 재판정’이 이전 snapshot에 대한 누락 판단보다 우선한다.** 아래 표는 부모의 실제 반영 위치를 기록한 것이며 구현 pass 결과가 아니다.

## 2. 24개 검토 권고와 반영

### 구조 담당 — [08 원문](08-architecture-plan-review.md)

| 권고 | 반영 | 연결 작업·수용 기준 |
| --- | --- | --- |
| APR-01 예방 gate | 반영 | T0A.1~6와 G0A→G3. 정확 대상·strict 입력·health/proxy·승인·workflow 의미부터 개선. 위험 경로 배포는 G0A 이후 |
| APR-02 승인 원자성/재조정 | 반영 | claim+started receipt 한 transaction, 네 failpoint·동시 소비·목적지 reconcile capability 표. 기존 원장 재사용 |
| APR-03 worker 소유권/lifespan | 반영 | T2.6 embedded/standalone owner·cancel/await/checkpoint/join·deferred 상태, G3 선행 |
| APR-04 최소 revision/CAS | 반영 | T2.8 insert-only source·append-only content·CAS/tombstone/생성 checkpoint G3 앞단, 편집 UI는 G4 |
| APR-05 다중 DB/migration | 반영 | G0 자료 소유권, G2 SQLite backup/WAL/watermark/files/hash/key restore rehearsal, 안전 rollback 금지 |
| APR-06 기존 계약·provenance | 반영 | G0 개념→현행 서비스/field 매핑, provider/account/grant/lease/expiry·상태 alias/schema 유지. 필요한 port만 분리 |
| APR-07 인물 durable conflict | 반영 | T1.3 사용자 상태/T2.7 confirmed membership 보존·ID 구분·revision/lineage/undo/outbox·재시도 |
| APR-08 coverage/증거 | 반영 | 23개 ARCHITECTURE ID 포함 99개 원장, 테스트 중복 합산 금지·task owner/기기/미검증 gate |

### Story 담당 — [09 원문](09-story-plan-review.md)

| 권고 | 반영 | 연결 작업·수용 기준 |
| --- | --- | --- |
| REVIEW-STORY-01 잘못된 대상 공개 | 반영 | T0A.1 명시 story_id/revision·missing reject·latest 금지. A/B 동시 갱신 반례, 반환 ID 직접 열기 |
| REVIEW-STORY-02 선택·본문·삭제 | 반영 | T2.1/T2.8 ordered membership/hash·CAS·append-only version/tombstone. legacy 불확실 scope는 확인 전 유지 |
| REVIEW-STORY-03 전수 철회·발행 | 반영 | T0A.2 cursor/SQL/outbox·public parent/consent guard, 201/1,001 Story·501+ share. T4.8 preparing→active |
| REVIEW-STORY-04 AI 사실·송신·budget | 반영 | CaptionClaim/evidence, 전송 종류별 policy·redaction, 서버 coverage·장면별 prose, AIAttempt, 지도 사용자 행동 전 src 금지 계약 |
| REVIEW-STORY-05 durable 완료 결과 | 반영 | T2.6/8와 G3 checkpoint. partial counts, origin capability·exact ID, 새 분석 시작 0/read 허용/명시 AI mode |
| REVIEW-STORY-06 migration/rollback | 반영 | read/presentation rollback과 항상 유지할 guard 구분, fixture migration·before/after row/hash/order·복원 검증 |
| REVIEW-STORY-07 export/목록/읽기 | 반영·범위 분리 | StoryReadState·cursor/search·managed/source availability, T4.9 offline HTML export는 후속 MVP, PDF/복원 JSON은 확정 기능 아님 |
| REVIEW-STORY-08 trace/단계 검증 | 반영 | 31개 STORY ID 원장, 단계별 native/web smoke·G3 설치 Mac acceptance·G5 actual Android, 공유 파일 write owner |

### UX 담당 — [10 원문](10-ux-plan-review.md)

| 권고 | 반영 | 연결 작업·수용 기준 |
| --- | --- | --- |
| UXR-01 개발/배포 의존·차단 복구 | 반영 | G0A DAG, 격리 UI 개발 허용·위험 경로 release 선행 구별. missing/deleted/revision/invalid 입력의 다음 행동과 입력 보존 |
| UXR-02 대비/AX 선행 | 반영 | T1.6·G1 light/dark 대비, 서버 실제 badge, 변경 화면 keyboard·AX smoke. 전체 보조기술 검증은 G6 |
| UXR-03 탭 이동 과확장 | 반영 | 후보 IA와 기존 작업 탭 유지안 비교. 배너 없이 과거 결과/실패/GPS/export 찾기, 상시 작업 링크·redirect |
| UXR-04 중복 엔진 방지 | 반영 | G0 field/service mapping, 기존 result_story/selection/export/receipt 재사용, 하나의 authoritative selection |
| UXR-05 편집 MVP | 반영 | G4a 제목·짧은 글·scope 내 제외/순서·명시 저장·CAS/복구, G4b source expansion/장면/자동저장, legacy_saved 보존 |
| UXR-06 Back/recreation 앞당김 | 반영 | T2.9 route stack·selected ID/revision·query/cursor/scroll·selection/draft·operation, viewer 우선 Back·session 재조회 |
| UXR-07 연결/권한/export coverage | 반영 | T2.9 최소 pairing/부분 권한 복귀, T5.7 확장, T2.10 receipt 상시 재개·0장/404/deleted/unknown 구별, UX32개 원장 |
| UXR-08 실기기 부재·evidence | 반영 | Android 연결0→not_run, synthetic/emulator/실기기/배포 증거 구별. 실제 기기 gate는 G5/G7 미완료로 유지 |

## 3. 구현 전에 기능 task spec에서 확정할 조건

큰 계획을 다시 작성할 필요 없이 각 단계의 implementation spec에 다음을 확정한다.

- **명령·저장:** exact ordered snapshot hash 구성, command_id/idempotency key, CAS와 revision conflict 응답, origin/capability, transaction·outbox·cancel/timeout·worker owner.
- **승인·공개:** trusted owner decision 채널, 직접/복구 caller 목록, 목적지별 reconcile 표, disclosure/consent epoch, legacy route의 호환·차단·해제 조건.
- **이행:** 기존 테이블/필드→새 객체 mapping, DB/WAL/files/key의 일관성 경계, before/after 대조, restore 및 unsafe rollback 금지.
- **작은 화면 계약:** G4a 저장·닫기·Back·미저장·충돌, 첫 pairing/권한 복귀, 결과/Story 빈 상태·오류·scope 확인, AX label·초점.
- **성능·검증:** reference Mac/Android model/OS/build, fixture 크기·메모리·response budget, actual native/보조기술·실계정 gate 및 명시 유예 범위.

모바일 편집 확장, 자동저장/오프라인 초안, 장면 구성, Story export/PDF는 한 번에 필수 기능으로 묶지 않는다. 기존 원본·Story·공유·키·인물 confirmed membership을 보호하는 계약은 유예 대상이 아니다.

## 4. 검증 기록과 해석

| 구분 | 실제 확인 | 한계 |
| --- | --- | --- |
| 부모 자동 baseline | 194개 Story/mobile/result/action/menu + 43개 architecture/approval/lifecycle/storage/source 테스트 통과 | 현재 계약 범위, 변경 구현·결함 해결 증거 아님 |
| 구조 담당 | 27개 기존 테스트, 합성 승인 대기/options/privacy 반례 | live topology·실제 쓰기·키 복원 미검증 |
| Story 담당 | 71+15개 기존 테스트, 임시 DB 제목 변경/삭제 부활/200개 상한/AI 문장 반례 | 실제 materialize 반복 선택·AI·대량 철회·공개 발행 미실행 |
| UX 담당 | 16개 fixture 테스트, 실제 AppKit 기하 overlap, 웹0장 fixture/390px·다크 대비1.21:1 | enhanced JS 모든 테마·VoiceOver/TalkBack 전체 미검증 |
| 부모 설치 Mac | 홈/분류/Story/작업/저장/인물/결과 읽기 전용 직접 관찰 | bundle commit과 소스 일치 미확인·전체 조합 검증 아님 |
| Android | ADB 연결 기기0, source package/version 확인 | native render·권한·rotation/process death·TalkBack은 not_run |

서로 겹치는 테스트를 합산해 고유 통과 수로 발표하지 않는다. agent 실행은 각 담당의 보고된 증거이며 부모의 직접 실행과 구분한다. 개인정보가 없는 fixture로 가능한 반례부터 검증했고 운영 사진·앨범·키·클라우드·실제 공유는 변경하지 않았다.

### 최종 문서 확인

최종 정리에서 `scripts/validate_docs.py`는 **현행 Markdown 134개 검증 통과**, `tests/test_documentation_structure.py`와 `tests/test_action_contract_docs.py`는 **3 passed in 0.12s**, `git diff --check`는 통과했다. 원장 생성 검사는 감사 ID **99개/작업 매핑 누락0**을 확인했다. 현재 발견 기록의 closed 개수는 **0**이며 다음 구현에서 근거를 갖춰 닫는다.

최종 설계·작업·원장 hash:

| 문서 | SHA-256 |
| --- | --- |
| 05 | `504472fbb4c72d2b4c9e7115d84da6b897f84d86e126a62dcdeb080dbe71c5c3` |
| 06 | `4b814221f5fb759085db21d1def6a7e346add984d74d78bb9aa436f792322134` |
| 11 | `4972dc20904fa266d7c3dc255ca77070a50ecaed8980530f8b88c3fe28a50828` |

## 5. 판정과 다음 작업 경계

이번 요청의 결과는 **문서·구현·일부 실제 화면을 대조한 전반 감사, 개선 방향, 기능 재설계, 단계별 작업 계획과 3개 담당의 peer review**다. 기능 결함 자체를 수정·배포한 것이 아니다.

다음 구현의 첫 범위는 G0 baseline과 G0A 대상·권한 예방 수정, 병렬 가능한 G1 상태·bounds·오류·대비 복구다. 이후 G2의 최소 scope/version/operation/navigation 계약을 거쳐 G3 수직 기능으로 진행한다. 기존 `people/controller.py` 미커밋 변경을 보존하고 해당 기능 구현에 들어갈 때 먼저 의미를 확인한다.
