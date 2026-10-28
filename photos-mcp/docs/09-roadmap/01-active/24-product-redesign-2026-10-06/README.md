# PhotosMcp 전반 감사와 제품 재설계

> 작성일: 2026-10-06
> 범위: 기능·구조·UX·기기간 사용성·Story·화면 구현·개선 방향·작업 계획 검토
> 상태: 조사·설계 산출물. 제품 코드 수정·schema migration·운영 배포는 포함하지 않는다.
> 코드 baseline: `328bc9f063675fe1e0f532310816b97708c92b10` + 조사 시작 시 작업 트리

## 먼저 읽을 문서

| 순서 | 문서 | 내용 |
| --- | --- | --- |
| 1 | [발견 항목 통합 원장](11-issue-register.md) | 독립 감사 86개와 부모 관찰/문서 13개, 총 99개 기록. 중복 원인을 연결해 작업 ID로 추적 |
| 2 | [제품 구조·기능 설계](05-product-and-feature-design.md) | 기기별 역할, 공통 객체·상태, IA·화면·Story·명령·안전 계약 |
| 3 | [개선 작업·검증 계획](06-execution-and-validation-plan.md) | 예방 수정부터 수직 기능·mobile·성능·설치본 검증까지 의존성과 완료 gate |
| 4 | [계획 검토·반영 결과](07-plan-review.md) | 같은 3개 에이전트의 peer review와 통합 조정, 잔여 결정 |

## 세 에이전트의 독립 감사

| 에이전트 | 담당 | 감사 결과 | 별도 계획 재검토 |
| --- | --- | --- | --- |
| Laplace | 구조·API/MCP·승인·데이터·작업 생명주기 | [01 구조 감사](01-architecture-audit.md), 23개 | [08 구조 계획 검토](08-architecture-plan-review.md) |
| Sartre | AppKit·Android·웹 UX·화면·기기간 사용성 | [02 UX 감사](02-ux-cross-device-audit.md), 32개 | [10 UX 계획 검토](10-ux-plan-review.md) |
| Faraday | Story 생성·편집·순서·버전·공유·삭제·AI | [03 Story 감사](03-story-audit.md), 31개 | [09 Story 계획 검토](09-story-plan-review.md) |

부모는 [04 설치 앱·문서 대조](04-live-observations-and-documentation.md)를 별도로 작성하고, 통합 설계·작업 순서·원장을 담당했다. 에이전트별 코드 근거·우선순위·증거 강도·수용 기준은 감사 원문에 있다.

## 현재의 주요 판단

1. **공유·재구성 대상부터 정확히 고정한다.** Story 상세의 공유 폼은 Story ID/revision을 전달하지 않고 서버가 최신 Story를 선택한다. A를 보면서 B를 공유할 수 있는 코드 경로를 확인했다. 실제 공개 공유를 생성하거나 피해 발생을 확인한 것은 아니다.
2. **선택과 결과를 immutable scope로 만든다.** 현재 snapshot은 선택된 사진 목록·순서까지 고정하지 않는다. 같은 작업의 서로 다른 선택이 같은 Story로 수렴할 수 있다.
3. **작업 상태·오류·화면 layout을 먼저 정합화한다.** 설치 앱의 완료 작업/진행 중 표시 불일치, 결과 header·inspector overlap, 인물 실패/정리 완료 동시 표시를 직접 확인했다.
4. **Story를 편집·버전·삭제 보호를 갖춘 객체로 보강한다.** 제목·순서·사진·본문의 사용자 수정, tombstone, 전체 철회/폐기, AI 주장 근거가 필요하다. 테마 확대만으로 해결되지 않는다.
5. **공통 서비스 위에서 기기 역할을 살린다.** 기존 PyObjC·Java Views·Python·공통 renderer를 유지하며 분석→결과→선택→Story 초안→저장/공유를 정합화한다.

## 확인된 범위와 남은 범위

부모는 설치 Mac 앱의 홈·분류·Story·작업·저장 공간·인물·완료 결과를 읽기 전용으로 직접 확인했다. UX 에이전트는 개인 사진 없는 AppKit 실제 기하 및 웹 fixture를 확인했다. Story/구조 담당은 메모리·임시 DB와 합성 입력으로 일부 결함을 직접 재현했다. 각 문서는 코드 확인·직접 재현·추론·미검증을 구별한다.

Android는 ADB 연결 기기 0대여서 실기기 화면·TalkBack·rotation/process death 검증이 남아 있다. 설치 Mac bundle과 현재 체크아웃의 정확한 버전 대응, 실제 계정·권한·사진 쓰기·외부 공유·실제 AI·전체 성능/보조기술 검증도 완료로 표시하지 않는다. 개인 사진·키·실명·정확 장소를 문서에 복제하지 않았다.

부모의 기존 계약 baseline은 **194개 + 43개 테스트 통과**다. 에이전트들의 27개/16개/86개 테스트는 일부 중복되므로 모두 합산해 고유 테스트 수로 발표하지 않는다. 테스트 pass와 실제 결함 해소를 구별한다. 최종 문서·action 계약·diff 검증은 [계획 검토](07-plan-review.md)에 기록한다.

## 스킬과 기존 계획의 관계

`ux-heuristics`, `refactoring-ui`, `swift-macos`, `mobile-android-design`을 설계 판단에 적용했고, UX 담당이 `adb` 스킬로 실제 모바일 검증 가능성을 확인했다. 사용한 원칙과 공식 근거는 [설계안 7절](05-product-and-feature-design.md#7-스킬-적용과-공식-기준)에 기록했다.

기존 [PC·Android 정합화 계획](../23-pc-mobile-feature-parity-and-result-to-story-plan-2026-09-24.md)과 [5종 Story 계획](../22-five-approved-story-themes-and-ratio-gallery-plan-2026-09-15.md)은 기반·이력으로 유지한다. 새 감사는 이미 구현된 연결을 다시 미구현으로 취급하지 않고 남아 있는 계약·화면·검증 결함을 보강한다. 이 조사에 따라 과거 계획을 자동 완료 처리하거나 문서를 일괄 이동하지 않는다.
