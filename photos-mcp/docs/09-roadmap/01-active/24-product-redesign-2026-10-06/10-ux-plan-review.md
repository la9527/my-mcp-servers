# UX·기기 간 관점 제품/실행 계획 Peer Review

- 작성일: 2026-10-06
- 검토 담당: 최초 `02-ux-cross-device-audit.md`를 작성한 동일 UX 담당
- 검토 대상: `05-product-and-feature-design.md`, `06-execution-and-validation-plan.md`의 최신 작업 트리. 단계 0A 및 동시 보완된 T1.6/T2.6~7/T3.0/T5.0·7/T6.9와 StoryReadState 추가본까지 재대조했다. UXR-02~08의 06 줄번호는 T1.6 등 추가본(SHA-256 `60534e6b40dacc2aaa0790034b7aa79c09903765e851e72450a89d5b1c415669`) 기준이다. 종료 직전 추가된 G0A 도표·G3 선행은 UXR-01에 최신 줄번호로 따로 반영했다. 그 외 새 삽입으로 줄번호가 이동한 경우 task/절 이름을 함께 사용한다.
- 산출 범위: 이 파일만 작성. 05/06·소스·다른 담당 문서 수정 없음. 추가 운영 UI 조작/사진 쓰기/권한 변경/모델 실행 없음.
- 근거: 대상 두 문서의 직접 읽기와 02 감사의 기존 증거. 이번 peer review에서 기존 live/fixture 관찰을 다시 수행하지 않았으며 계획 수용기준의 구현 완료를 주장하지 않는다.

## 1. 판정

**조건부 진행: 아래 HIGH 4개를 실행 task/spec에 반영한 뒤 해당 의존 작업에 착수하는 것이 적절하다.** 문서 전체 재작성이나 플랫폼 교체는 필요하지 않다. 최다 8개 권고로 제한했으며 분류는 **BLOCK 0 / HIGH 4 / MED 4 / LOW 0**이다.

- BLOCK: 계획대로 진행해도 허용할 수 없는 명백한 손실/권한 우회가 남는 경우. 이번 두 문서는 원본·승인·공개 경계 보존과 0A를 명시하므로 UX 관점 신규 BLOCK은 제시하지 않는다. 구조/Story 담당의 독립 BLOCK 판정을 대체하지 않는다.
- HIGH: 해당 기능의 착수 순서나 gate를 확정하기 전에 보완해야 할 결함. 수정 계획 자체가 아니라 실계정·공개 변경과 새 기능 배포에는 기존 G0A가 계속 선행한다.
- MED: 설계/task 분해 시 보완해야 할 누락·불필요한 확장 위험.
- LOW: 선택적 정돈. 이번에는 구체적인 실행·검증 영향이 있는 권고에 집중한다.

좋은 방향은 유지한다. `05:11,23,27`의 기존 PyObjC/Java Views와 저장 모델 재사용, `05:32-43,75-85`의 접수/완료·선택/포커스·초안/공유 분리, `05:106`의 header/body/footer 경계, `05:146`의 unknown/stale 오류, `06:47-54`의 정확한 Story 대상·폐기·승인 예방 수정, `06:69,167`의 자동 테스트와 설치본 검증 구분은 UX 감사와 일치한다. 아래 `05:`/`06:` 줄번호는 각각 검토 대상 파일을 가리킨다.

## 2. 구체 수정 권고 — 최대 8개

### UXR-01 · MED — 0A 의존성 보완을 인정하고 예방 차단 시 사용자 복구 흐름을 명시할 것

**연결 UX:** UX-06, UX-08, UX-13, UX-18, UX-21, UX-32.

**근거:** 초기 도표의 0A 누락은 종료 직전 부모 보완으로 해소됐다. 최신 `06:17-19`에 G0A와 G3 의존성이 있고 `:94`는 G0A/G1/G2를 선행으로 명시한다. 이를 반영하여 HIGH에서 MED로 낮췄다. `06:45-56`의 예방 차단 방향은 적절하며 남은 권고는 개발 가능/배포 가능 경계의 설명과 missing ID/revision/unknown option 차단 시 사용자 복구 행동이다.

**영향:** 0A가 도표에 연결된 위험은 해소됐다. 다만 missing ID/unknown option 차단으로 기존 사용자가 복구 행동 없이 막히거나 독립 bounds/문구 fixture 개발까지 불필요하게 기다리는 해석은 피해야 한다.

**수정 권고:** 현재 추가된 G0A 도표를 유지하고 각 단계의 `개발 가능 선행 / 통합·배포 선행`을 별도 열로 고정한다. 예를 들어 G0 이후 독립 UI 예방 수정의 개발·fixture 검증은 가능하되 외부 대상·공유·쓰기 command 변경의 통합/배포는 G0A 통과 후로 명시한다. 차단 오류에는 현재 대상 재선택, revision 새로 읽기, 호환 입력 안내, 기존 결과 읽기라는 대안을 함께 정의한다. latest fallback을 UX 편의로 되살리지 않는다.

**수용:** G0A 미통과 상태에서 허용된 fixture/UI 개발과 금지된 외부 변경을 task별로 판단할 수 있다. missing storyId/deleted/revision conflict/invalid option fixture에서 잘못된 대상 호출 0이며, 화면은 입력·선택을 유지하고 구체적인 다음 행동을 제공한다. 단계 0A 선행은 이미 추가됐으므로 이 권고를 '예방 수정 자체가 누락됨'으로 해석하지 않는다.

### UXR-02 · HIGH — 추가된 T1.6을 G1에 연결하고 변경 화면의 접근성 검증을 선행할 것

**연결 UX:** UX-02, UX-13, UX-22, UX-25, UX-28, UX-29, UX-30, UX-32.

**근거:** 최신 `06:67`의 T1.6에 대비·고정 정상 상태가 추가되어 UX-28과 UX-04 예방 수정은 단계 1에 배정됐다. 이 부분은 반영 완료로 인정한다. 그러나 `06:69` G1은 live 상태/overlap/빈 큐만 명시하고, `:133-150`에 keyboard/VoiceOver/TalkBack 확장 검증이 남아 있다. `05:161`의 접근성 목표를 변경 화면의 G1 gate에 연결할 필요가 있다. 02 감사 `:207-210`의 1.21:1 반례는 기존 증거다.

**영향:** 수정 task가 존재해도 G1 합격 조건에 빠져 있으면 대비 반례를 남긴 채 다음 단계로 넘어갈 수 있다. AX 문구만 맞추고 focus/keyboard 동작을 후반에 확인하면 초안 편집의 실제 사용 가능성을 늦게 발견한다.

**수정 권고:** 이미 추가된 T1.6의 대비·정상 상태 검증을 G1 합격 조건에 명시하고, `T1.7 변경 화면 keyboard·focus·오류 안내 smoke`를 추가한다. UX-25의 Android '읽기 전용' 오표기는 좁은 label 수정으로 독립 반영한다. 단계 6은 전 표면·테마·대량·보조기술 확장 감사로 유지한다. `05:161`의 px/pt/dp 구분을 유지하고 screenshot 판독과 computed contrast, AX 이름/state를 별도 검증한다.

**수용:** primary enabled/disabled/hover/focus 및 light/dark에서 목표 대비를 측정하고 실패 상태로 배포하지 않는다. 변경한 화면의 keyboard-only 핵심 행동, focus 가림, title/name/help/state 일치, 오류와 성공 동시 노출 금지를 G1에 포함한다. VoiceOver/TalkBack 미수행을 fixture label 검사로 대신 합격 처리하지 않는다.

### UXR-03 · MED — Android 작업 탭 제거는 기본안으로 확정하지 말고 기존 IA를 유지한 비교안으로 둘 것

**연결 UX:** UX-03, UX-05, UX-08, UX-09, UX-26, UX-27.

**근거:** `05:91`은 Mac 탐색의 점진 전환, `:110`은 Android 홈/사진/Story/인물로 변경하고 작업을 홈/배너에서 열도록 제안한다. 최신 `06:91` T3.0과 `:121` T5.0에 fixture 시안·walkthrough 후 IA 확정이 추가됐다. 따라서 전면 교체 강제가 아니라 시안 검증 방향은 반영됐다. 다만 작업 탭을 유지한 비교안과 완료/실패 이력을 배너 없이 찾는 성공 조건은 없다. `05:169`의 사용자 확인은 모바일 편집 범위 중심이다.

**영향:** 배너가 사라진 완료·실패 작업, 과거 실행·내보내기·GPS 대기를 다시 찾기 어렵다. 인물 관리가 모든 사용자에게 작업보다 빈번하다는 근거가 아직 없다. Mac과 Android가 서로 다른 주요 업무 진입점을 갖게 된다.

**수정 권고:** 1차는 기존 4개 탭의 이름/객체 의미를 바로잡고 작업 탭을 유지한다. 인물은 사진의 하위 탭과 확인 필요 deep link로 노출하거나 기존 설정 진입을 병행한다. 새 IA는 feature flag/prototype 비교안으로 두고, 작업 없이도 완료/실패 이력에 접근하는 상시 링크와 기존 route redirect를 요구한다. Mac 저장 공간 이동도 기존 진입 경로·링크를 보존한다.

**수용:** 홈 배너 없는 상태에서 이전 완료 결과, 실패 원인, GPS 대기, 인물 검토를 발견하는 walkthrough를 비교한다. task completion/잘못된 진입/복귀 경로를 기록하고 탭 제거를 그 결과로 결정한다. 탐색 개선을 위해 전체 IA 이동을 단계 1 상태 수정의 선행 조건으로 만들지 않는다.

### UXR-04 · MED — 새 객체/command 명칭을 기존 구현에 매핑해 중복 DB·선택·Story 엔진 구축을 막을 것

**연결 UX:** UX-05, UX-17, UX-18, UX-31, UX-32.

**근거:** `05:27` 재사용 원칙, `:31-43` 제안 객체, `:144` command 목록을 확인했다. `06:77-85` snapshot/ports/approval과 `:87-98` 결과→초안 작업은 현행 함수/field를 개념별로 매핑한 표가 없다. 최신 `06:113` Story export가 사진 export 승인 경로를 재사용한다고 명시한 점은 적절하다. 02 `:149-157`는 현재 Story chooser와 receipt 재시도 구현 존재를 확인했다.

**영향:** 이미 있는 result_story_service/selection persistence/export approval을 병렬로 새로 구축하고 서로 다른 selection ID와 scope를 만드는 위험이 있다. 사용자는 같은 동작이 다른 화면·서버 상태로 늘어나는 경험을 하게 된다.

**수정 권고:** G0 산출물에 `제안 개념 → 현행 서비스/schema/field → 유지/alias/필드 보강/신설 → 호환 caller → 검증` 매핑 표를 추가한다. T2/T3는 기존 함수/route를 통해 snapshot 순서·revision·UI 발견성을 보강하는 것으로 정의하고 신설은 실제 공백에 제한한다. typed repository 전체 분리 완료를 작은 결과→Story UX 수정의 불필요한 선행 조건으로 만들지 않는다.

**수용:** 현행 job 결과 열기·selection 저장·완료 결과→Story·export resume가 기존 caller에서 계속 동작한다. 같은 selection은 단일 authoritative 저장소/정책을 사용하며 새 adapter가 독립된 head를 만들지 않는다. alias와 deprecated 경로의 제거 조건을 기록한다.

### UXR-05 · HIGH — 단계 4 편집을 작은 MVP와 후속 확장으로 나누고 기존 Story의 상태를 명시할 것

**연결 UX:** UX-05, UX-17, UX-21, UX-26, UX-29, UX-31.

**근거:** `05:36,83,100,125-140`은 초안/저장/공유 및 편집 계약을 제안한다. `05:169`에서 모바일 편집 범위는 사용자 확인 대상이지만 `06:104-115` 단계 4는 사진·장면·자동저장·revision·생성·삭제·공유를 하나의 gate로 묶는다. 최신 `06:113` Story export의 별도 후속 MVP 분리는 적절하나 핵심 편집 MVP 분리는 아직 없다. 기존 Story를 draft/reviewed/saved로 표시할 사용자 의미도 확정되지 않았다.

**영향:** 작은 제목/사진 선택 수정의 가치를 확인하기 전에 전체 편집·생성·공유 엔진이 필요해진다. 자동저장인데 버튼은 '초안 저장'인 식의 불일치, reviewed의 의미 불명확, 기존 공유를 다시 검토하라고 강제하는 회귀가 생길 수 있다.

**수정 권고:** G4a 편집 MVP는 기존 Story 복제/버전 정책을 명시한 뒤 `제목·짧은 글 수정 + 기존 scope 안의 제외/순서 + 명시 저장 + revision conflict/이전 버전 복구`로 제한한다. 저장 중/실패/미저장 이탈을 먼저 설계한다. 사진 추가와 source expansion, 장면 분할/병합, 자동저장·오프라인 draft, AI 재구성은 G4b 이후로 분리한다. MVP 범위 밖이더라도 공유의 보안·tombstone·scope 불변 조건은 0A에서 후퇴시키지 않는다. 현행 저장 Story의 legacy 상태는 자동 AI 재생성·재분석 없이 읽고 기존 링크를 보호한다.

**수용:** 저장/닫기/Back/실패/conflict에서 편집 손실 0; 제목 수정이 membership/presentation/share를 암묵 변경하지 않음; 같은 draft를 두 기기에서 수정한 충돌에 비교/보류 가능. legacy_story가 처음 열릴 때 원래 감상·공유가 유지되고 '초안 검토 필요'가 필요한 경우 그 이유를 설명한다. 모바일 편집 capability는 지원 범위에 맞게 표시한다.

### UXR-06 · HIGH — Back·recreation·WebView 계약의 최소 구현을 G3/G4 이전으로 당기고 route 복원 키를 고정할 것

**연결 UX:** UX-06, UX-11, UX-17, UX-21, UX-26, UX-27, UX-29, UX-31.

**근거:** 최신 `05:42` StoryReadState에 selected Story/revision·anchor·cursor·scroll·facet이 추가됐다. 이 감상 상태 계약은 반영 완료다. 그러나 선택/draft/operation을 포함한 native 최소 복원·Back contract의 실행은 `06:117-131` G3 이후다. `06:96,98`은 이미 G3에서 Android/웹 동일 초안 진입을 요구하고 `:104-105`는 편집/conflict를 시작한다. `05:112` wrapper 원칙을 앞 단계 gate로 연결해야 한다. 02 `:197-205`는 현행 main 복원 부족과 Back→홈 문제의 근거다.

**수정 권고:** G2b/G3 선행 최소 contract를 두고 destination stack, selected_story_id/result_snapshot_id, page/query/scroll, selection revision, operationId, draft revision을 명시한다. WebView에는 origin/path allowlist·cookie scope, 목록/상세/history, viewer-close 우선, native back, load fail/reconnect, focus 인계를 포함한다. handle TTL/session 만료 후 복원은 재인증·최신 revision 재조회이고 pending mutation 자동 실행이 아니다. 전체 MainActivity 분리는 단계 5에 남기되 이 최소 계약 때문에 그 전체 리팩터링을 기다리지 않는다.

**수용:** 결과→초안→감상→Back은 같은 결과·선택 위치로 돌아온다. 회전/process recreation/권한 화면·외부 브라우저 왕복 후 stable id 기반으로 복원하며 무단 분석/중복 제출 0. WebView와 native back가 같은 상위 맥락으로 이동하고 stale page response가 삭제/새 revision을 되돌리지 않는다. 실제 native 실기기 gate는 UXR-08대로 별도 유지한다.

### UXR-07 · HIGH — 추가된 연결·권한·검색 task를 인정하고 남은 coverage·export 재개·선행 순서를 고정할 것

**연결 UX:** UX-03/04/07~15/18~21/23~25/30/32 중심. 전체 UX-01~32.

**근거:** 최신 06에는 T1.6 정상 badge/대비(`:67`), T2.7 인물 충돌/동명이인(`:83`), T3.0 첫 실행/IA(`:91`), T5.7 pairing/사진·GPS 권한/OAuth handoff(`:129`), T6.9 검색(`:148`)이 추가됐다. 따라서 이 기능들의 task 존재를 더 이상 누락으로 판정하지 않는다. 다만 UX-01~32를 task/gate에 연결한 06 내부 표는 없으며, durable 사진 export receipt를 작업 목록에서 다시 찾는 UX-18과 404/empty 분리 UX-21의 명확한 task/acceptance는 남아 있다. 권한·첫 연결이 G3 cross-device 동작 이후인 단계 5에 배정된 순서도 점검해야 한다.

**영향:** 단계 7까지 계획을 완료했는데도 알려진 오류와 연결 진입점이 남을 수 있다. 장수/선택 의미를 통일해도 사용자가 최초 작업이나 실패한 저장을 끝낼 수 없으면 목표가 달성되지 않는다.

**수정 권고:** 각 UX ID에 `task·owner·phase·acceptance·evidence tier·remaining gate`를 붙인다. 기존 T1.6/T2.7/T3.0/T5.7/T6.9에 각 UX ID를 연결하고 실제 미배정인 사진 export receipt의 작업 목록 재개와 404/empty 구분을 추가한다. T5.7의 pairing·부분 권한·승인 후 명시 계속은 G3의 기기간 작업에 필요한 최소 준비 경로와 후속 UX 확장으로 나눠 앞당긴다. 검색은 T6.9의 범위/유예를 명시하면 된다. owner 조치 발견성과 출처별 오류 recovery에도 개별 acceptance를 연결한다. 모든 항목을 새 기능으로 신설하지 말고 현행 보강/명시적 유예와 이유를 기록한다.

**수용:** 32개 ID가 모두 resolved-by-task/accepted-defer/검증 대기로 연결되며 단순 '단계 6 UX 개선' 묶음은 불합격. UX-04 정상 badge, UX-18 receipt 재개 등 작은 기존 결함이 large Story editor 완성을 기다리지 않아야 한다. 유예한 기능은 제품 완료 주장과 release scope에서 제외한다.

### UXR-08 · MED — 실기기 부재를 gate 상태로 명시하고 증거 수준별 완료/배포 기준을 나눌 것

**연결 UX:** UX-10, UX-11, UX-15, UX-20, UX-26~29, UX-32.

**근거:** 이전 감사의 ADB 연결 기기는 0대였다(02 실행 검증/잔여 gate 절). 이번 peer review에서 ADB를 다시 조회하지 않았으므로 현재 연결 여부는 새로 확정하지 않는다. `06:34` 설치 metadata, `:131` 등록 기기 G5, `:150` 플랫폼별 미검증 없는 G6, `:156-158` 설치본/실계정 G7이 있다. 기기 확보 실패 시 fixture 개발 완료와 native 검증 보류를 구분하는 gate status는 명시되지 않았다.

**수정 권고:** gate 결과를 `passed / failed / not_run(reason, owner, required_environment)`로 기록하고 fixture 개발 완료·플랫폼 검증 완료·배포 허용을 분리한다. G0에서는 미연결과 저장소 version을 설치 여부와 분리해 기록하며 Android 기기 부재만으로 read-only baseline/독립 Mac 예방 수정이 멈추지 않게 한다. G3 cross-device는 mock/API consistency와 실제 기기 열람 두 gate로 구분하되 전자를 Android native 합격으로 표현하지 않는다. G5/G6/G7의 native release gate는 실제 Android 검증 전 열지 않는다.

**수용:** 연결 기기 없으면 Android native 항목은 명확히 not_run으로 남는다. emulator/instrumentation이 있으면 별도 증거로 기록하며 실제 OS 권한·GPS·백그라운드·TalkBack 실기기 경험을 대체한 것처럼 쓰지 않는다. 기기 확보 이후 모델/권한/설치 version·reference 폭을 기록하고 합성 데이터로 검증한다. 실계정 변경/사진 쓰기 검증은 별도 권한 범위로 남긴다.

## 3. 권장 착수 순서 — 기존 단계를 유지한 보완안

1. **G0:** 현재 수정/설치본/fixture·기기 부재·기능 재사용 표와 UX coverage를 기록. 현재 기기 없음은 환경 미검증이며 전체 조사·개발의 임의 BLOCK은 아니다.
2. **G0A:** 외부 대상/공개 폐기/승인·privacy guard를 우선 통합. 독립 상태·bounds·AX·contrast fixture 개발은 허용 범위를 명시해 병행. 새 외부 영향 경로 배포는 G0A 후.
3. **G1:** stale 분류/인물 unknown/error/정상 badge/결과 overlap/owner CTA contrast 및 변경 화면 keyboard·AX smoke. 큰 IA 이동 없이 현재 화면에서 바로 검증한다.
4. **G2:** 현행 snapshot/selection/receipt 서비스 보강과 최소 route·Back·복원 계약. 필요한 repository port만 먼저 분리한다.
5. **G3:** 기존 완료 결과→Story를 보강하는 수직 기능. 선택 범위·정확한 생성 Story 진입·원래 결과 복귀·실패 후 선택 보존. mock consistency와 native E2E를 구분한다.
6. **G4a:** 작고 명시적으로 저장하는 편집 MVP. **G4b/G5:** source expansion/장면 구성/자동저장/모바일 모듈 분리/조치함 확장 등 후속. 연결·권한과 export 재개 task는 사용자 목표의 선행 필요에 따라 G1~G3에 병행 배정한다.
7. **G6/G7:** 전체 테마·대량·접근성·실기기·운영 사용자 경로 확장. 이전 단계의 확인된 결함을 여기에 몰아넣지 않는다.

## 4. 미검증 목록과 재검토 완료 조건

검토 중 05/06의 해시가 변경되어 동시 보완 내용을 재독하고 위 권고를 수정했다. 이 담당은 05/06에 쓰지 않았으며 부모 변경을 되돌리지 않았다. T1.6/T2.7/T3.0/T5.7/T6.9와 StoryReadState는 추가 사실을 인정하고 잔여 요건만 권고했다. 종료 직전 G0A 도표·G3 선행 보완도 인정하여 UXR-01을 MED로 낮췄다. 지속적으로 변경되는 문서에 대해 무한 재감사를 하지 않고 이 시점의 잔여 권고를 전달한다.

| 항목 | 현재 증거/잔여 | 완료 조건 |
|---|---|---|
| 05/06의 실행 가능성 | 문서 peer review만 수행; 구현은 제안 | task/spec에 권고 반영, 위험한 선행/후행 모순 제거 |
| Android 설치/실기기 | 이전 ADB 연결 0대, 이번 추가 기기 조회 없음 | 등록 기기 package/version과 source 대조; 합성 데이터 native E2E |
| Back·WebView | 코드와 계획 수준; 현행 일부 부모 live | 목록/상세/viewer/history/인증/연결 failure의 실제 native 동작 및 focus |
| rotation/process recreation | 현행 코드 복원 부족; 새로운 contract 미구현 | scope/선택/operation/draft 복원과 handle/session TTL negative test |
| 권한·GPS | 실제 OS 권한 dialog/부분 권한/GPS 거부 미검증 | capability별 상태·원래 화면 계속, GPS/outbox/서명 회귀 실기기 검증 |
| 접근성 | 기존 fixture AX/웹 대비 일부; VoiceOver/TalkBack 전체 U | 수정 화면부터 keyboard·보조기술 핵심 task, 모든 표면/테마 확대 |
| 새 초안 편집 MVP | 신규 version/edit 모델은 설계 | 명시 저장·이탈·conflict·복구·legacy state 보존의 end-to-end |
| snapshot/선택 parity | 기존 fixture 일부, 새 모델 미구현 | order/hash/scope/selection 의미와 deleted/revision conflict/재시도 비교 |
| export·공유·AI | 실제 쓰기/공개 변경/AI 실행 안 함 | 승인 범위 내 안전한 테스트 환경의 receipt 재개·공유 폐기·모델 호출 계수 |
| IA 이동의 효과 | 탐색 비교 사용자 관찰 없음 | 기존/후보 IA에서 이전 작업·조치·인물·결과 찾기 walkthrough |
| 대량·큰 글꼴 | 이전 Mac 10k fixture 등 일부; 기기 baseline 없음 | 기준 기기·메모리/응답 예산과 8+ Story/대량 목록/확대 실측 |

05/06을 읽은 것과 위 기능의 구현·설치 검증은 다르다. 이번 리뷰 완료는 **8개 구체 수정 권고와 미검증 gate를 전달했다는 뜻**이다. 부모 반영 후에는 UXR-01~08 각각의 반영/유예 사유와 UX coverage를 대조하면 되며, 동일한 감사나 운영 사진 분석을 다시 실행할 필요는 없다.


## 5. 최종 반영 재판정

**재검토일: 2026-10-06. 판정: UX 관점 계획 문서 수준 수용 — 잔여 BLOCK 0 / HIGH 0.** UXR-01~08은 모두 계획 수준에서 해소됐다. 앞 절의 HIGH 4 / MED 4는 반영 전 검토 이력이며, 최신 05/06/11의 진행 판정은 이 절을 따른다. 이 판정은 실행 task/spec 및 플랫폼별 기기 gate를 조건으로 한 계획 수용이다. 제품 결함 해결, 기능 구현 완료, Android 배포 허용을 뜻하지 않는다. 다른 담당의 구조·Story 판정은 별도다.

이번에는 최종 작업 트리의 `05-product-and-feature-design.md` 전체, `06-execution-and-validation-plan.md` 전체, `11-issue-register.md` 전체를 실제로 다시 읽었다. 아래 `05:`, `06:`, `11:`은 이번 재독 시점의 해당 파일 줄번호다. 원문을 수정하거나 추가 코드/UI/ADB/테스트를 실행하지 않았다. 11은 모든 발견을 open으로 유지하고 있어, 계획 권고 해소를 실제 결함 closed로 오해하지 않게 한다(`11:3,114-116`).

### 5.1 권고별 반영과 잔여 실행 조건

| 권고 / 연결 UX | 문서 수준 재판정·직접 근거 | 실행 task/spec·gate에 남길 조건 |
| --- | --- | --- |
| **UXR-01 / UX-06·08·13·18·21·32** | **해소.** G0A→G3 DAG(`06:17-21`), G0A/G1/G2 선행(`06:102`), 격리 개발과 외부 경로 통합/배포의 구분, missing ID/revision/deleted/invalid options별 다음 행동을 명시했다(`06:58,62`). 최신 대상 자동 대체 없이 기존 입력·선택·읽기를 유지한다. | task별 개발/통합/배포 선행을 옮겨 적고 차단 이유와 next action을 렌더·AX에 함께 전달한다. G0A 통과는 구현 증거가 필요하다. 예방 수정이 실제 사고 발생을 증명하는 것은 아니다. |
| **UXR-02 / UX-02·13·22·25·28·29·30·32** | **해소.** T1.5·T1.6과 G1에 실제 상태, 대비, keyboard-only·AX label/value/focus smoke가 연결됐다(`06:70-75`). 변경한 주 행동이 접근 불가하면 G1을 닫지 않는다는 차단 조건도 있다. UX-25는 T1.5/T5.1로 연결됐다(`11:61`). | G1의 변경 화면별 주 행동·오류 안내·focus 복귀 목록과 측정 증거를 task에 명시한다. 보조기술 미수행을 label fixture 통과로 대체하지 않는다. 대비 기준과 플랫폼 단위를 유지하고 native 항목 미실행은 별도 기록한다. |
| **UXR-03 / UX-03·05·08·09·26·27** | **해소.** Android IA를 후보로 명시하고 현재 작업 진입, 배너 없는 과거 작업의 상시 링크, 기존 route/저장 공간 진입을 유지한다(`05:110-114`). 기존 작업 탭 유지안과 task completion·잘못된 진입·복귀 walkthrough 비교 후 이동을 결정한다(`06:104,136-138`). 전체 IA 교체는 선행 조건이 아니다. | 비교 시 배너 없는 완료·실패·GPS 대기·export receipt를 반드시 포함한다. 실제 발견성 근거가 없으면 기존 작업 진입을 유지한다. 후보 시안의 존재를 IA 변경 승인이나 사용성 향상 실적으로 기록하지 않는다. |
| **UXR-04 / UX-05·17·18·31·32** | **해소.** 현행 객체·worker·DB 재사용(`05:27,71,75`), G0의 개념→현행 서비스/schema/field→보강/신설→호환 caller→검증 매핑, 단일 선택 권위와 전체 port 대공사 배제 조건이 명시됐다(`06:96-98`). | 실제 field/caller 매핑 표는 G0 산출물로 작성해야 한다. 표의 작성 요구가 반영된 것이며 매핑 자체가 완성됐다는 뜻은 아니다. 신규 저장 모델 작업 전 기존 selection/export resume/receipt 회귀와 alias 종료 조건을 task spec으로 고정한다. |
| **UXR-05 / UX-05·17·21·26·29·31** | **해소.** 명시 저장·기존 scope의 제목/짧은 글/제외/순서·CAS 충돌·이전 revision 복구가 첫 MVP다. source expansion/장면 구성/자동저장/오프라인/AI는 후속으로 분리했고 legacy_saved 감상·공유를 보존한다(`05:169`, `06:117`). 공개·폐기 guard는 유예되지 않는다. | 단계4 번호 목록과 G4 전체 lifecycle는 확장까지 포함한 전체 범위다(`06:119-130`). 실행 task에는 G4a/G4b 라벨과 각각의 종료 gate를 붙여 사진 추가·자동저장 등 후속 기능이 G4a 완료 조건으로 섞이지 않게 한다. `05:173`의 모바일 사진 추가 후보도 G4b 범위로 해석한다. 명시 저장 실패/Back/닫기/미저장 입력 보존을 G4a spec에서 구체화한다. 이는 범위가 이미 명시된 계획의 실행 분해 조건이며 잔여 HIGH가 아니다. |
| **UXR-06 / UX-06·11·17·21·26·27·29·31** | **해소.** StoryReadState와 wrapper 계약(`05:42,114`)에 T2.9의 최소 route stack·stable ID·query/cursor/scroll·selection/draft revision·operation ID가 연결됐다. TTL/인증 실패 재조회·재인증, pending mutation 자동 실행 0, G3 이전 최소 복원 및 Android 실기기 별도 gate가 명시됐다(`06:91,102,111`). | T2.9 spec은 결과→초안→Back, 목록→상세→viewer-close 순서, native/WebView history 우선순위와 focus 인계를 명시한다. URL/cookie/외부 링크·download 전체 계약은 T5.5(`06:144`)에서 확장하되 G3에서 실제 사용하는 경로의 allowlist·실패 복귀·초점은 최소 구현에서 확인한다. 전체 Activity 분리를 기다릴 이유는 없다. |
| **UXR-07 / 전체 UX-01~32** | **해소.** 원장 `11:37-68`에 32개 UX ID가 빠짐없이 task/gate로 연결됐다. UX-18/21은 T2.10의 상시 export receipt·정확 remaining set 재승인·0장/삭제/404/권한/네트워크/unknown 구분으로 구체화됐고(`06:92`, `11:54,57`), 연결/부분 권한/승인 후 계속은 T2.9로 앞당겨졌다(`06:91,138`). 검색은 T6.9에 범위가 있다(`06:165`). | 각 실행 ID에 담당·PR/commit·baseline·변경 후 자동/설치본/기기 증거·잔여 gate를 채운다(`11:116`). 원장은 task coverage 확보이지 32개 해결 실적이 아니다. 유예/배포 제외 항목은 release scope에 명시하고 export 재개에서 자동 재승인·자동 쓰기를 만들지 않는다. |
| **UXR-08 / UX-10·11·15·20·26~29·32** | **해소.** G0은 implemented/reachable/deployed/verified/remaining_gate를 나누고(`06:36-41`), T2.9/G3은 Android 실제 기기 미검증을 별도 유지한다(`06:91,111`). G5는 등록 기기, G6는 지원 범위 필수 gate와 미연결 담당/증거, G7은 설치본·기기·재시작 E2E를 요구한다(`06:148,167,173-175`). 따라서 fixture 통과만으로 native 합격을 주장하는 해석은 배제된다. | 실행 결과 형식은 passed / failed / not_run(reason, owner, required_environment)로 고정한다. 문서는 그 구분의 의미와 미검증 유지 조건을 충족한다. Android 실기기 not_run이면 해당 native release gate를 열지 않는다. 독립 Mac 개발/예방 수정과 API fixture는 그 상태와 별도로 진행할 수 있다. |

### 5.2 미검증과 완료 주장 경계

아래는 **계획 문서의 잔여 BLOCK/HIGH가 아니라 실제 구현·검증이 남은 항목**이다. 이번 재판정은 문서 읽기만 수행했으므로 새 pass 증거를 생성하지 않았다.

| 항목 | 이번 판정과 잔여 상태 | 실행 완료 증거 / gate |
| --- | --- | --- |
| 기존 실제 Mac 결함 | stale 상태·overlap·인물 unknown/conflict·Story shell·AX 불일치 등은 원장에서 여전히 open. 계획 반영과 결함 수정은 별개 | G1 변경 bundle의 설치 UI, 상태/AX/keyboard/대비와 tab·재시작 복귀 |
| Android 연결·설치 | 이전 감사의 ADB 연결 0대만 기존 증거. 이번 연결/설치 여부 재조회 없음. native 검증은 **not_run**으로 유지 | G0 package/version/source 대조와 등록 기기 확보, G5/G7 합성 데이터 native E2E |
| route·Back·recreation·권한 복귀 | T2.9 계약 추가, 구현/연결/회전·process death·실제 OS 권한 복귀는 미검증 | G2 최소 route/복원 경계, G3 synthetic WebView, G5 실제 기기. pending mutation 자동 실행 0 |
| 접근성·반응형 | G1 선행 smoke와 G6 확장 계획 수용. 실제 VoiceOver/TalkBack, 큰 글꼴·기기 폭·모든 테마 검증은 미완료 | 변경 주 행동의 G1 설치 검증 및 G6 플랫폼/테마/폭별 증거. 보조기술 미수행은 not_run |
| 편집 G4a·legacy | 명시 저장·CAS·복구·legacy_saved는 설계, 아직 새 동작의 구현/마이그레이션 완료 아님 | save/fail/Back/닫기/conflict, 이전 revision·공유 보존; G4a task별 종료 gate |
| snapshot·parity·IA | 개념 매핑·순서/hash·후보 IA 비교는 계획. 새로운 계약의 전 표면 연결과 사용성 효과는 미검증 | G0 실제 재사용 매핑, G2/G3 동일 scope/order/hash, T3.0/T5.0 비교 walkthrough |
| export·공유·AI·실계정 | 실제 쓰기/공개 변경/AI 실행 없음. 영수증 재개·공유 폐기·정책 차단 결과는 이번에 확인하지 않음 | G0A와 관련 기능 negative/recovery 증거, 승인된 격리 환경/권한 범위. unknown write는 reconcile 상태 유지 |
| 대량·성능·운영 복구 | 기존 baseline을 새 설계의 품질 증거로 승격하지 않음. 새 기기 예산/설치본 E2E는 미검증 | G6 측정 예산·reference 환경, G7 앱/daemon/기기 단절 후 새 작업 E2E |

**최종 결론:** UX 관점에서 05/06/11은 실행 task spec 작성과 해당 기기 gate를 조건으로 수용한다. 문서 수준 잔여 BLOCK/HIGH를 이유로 추가 계획 수정이나 같은 UI 감사를 반복할 필요는 없다. G4a/b 종료 기준, T2.9 경로별 최소 계약, G0 현행 매핑, evidence별 gate 결과는 해당 task 착수 시 구체화한다. 실제 미구현 기능과 Android not_run을 숨기거나 플랫폼 배포 gate를 통과한 것으로 처리하면 이 수용 조건을 충족하지 않는다.
