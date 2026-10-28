# 발견 항목 통합 원장

> 조사일: 2026-10-06. 모든 항목은 미수정 상태이며 구현 gate로 연결한다.
> 3개 독립 감사의 86개 항목과 부모의 13개 관찰/문서 항목, 총 99개 감사 기록이다. 서로 겹치는 원인·증상이 있으므로 99개의 독립 결함이라고 계산하지 않는다.

## 우선순위 정규화

원 감사의 P0/P1 정의가 다르다. UX 담당의 P0는 먼저 고칠 상태·배치·대비 문제이며, 통합 우선순위에서는 주요 작업 문제 P1로 정규화한다. STORY-01은 잘못된 객체 공개 가능성 때문에 P0로 유지한다. 원래 등급과 근거는 원 감사에 보존한다. 노출/사고 발생은 확인하지 않았다.

각 항목의 상세 코드 줄, 재현 조건, 해결 대안, 수용 기준은 원문 감사에 있고 아래 작업 ID는 [작업 계획](06-execution-and-validation-plan.md)의 구체 task 또는 gate다. T3.1~4/T4.1~9/T5.1~7은 해당 단계의 번호 목록 항목이다. T6.1~9는 아래 계획의 영역 번호를 따른다.

| ID | 발견 요약 | 원 등급 | 통합 등급 | 작업·gate | 상세 근거 |
| --- | --- | --- | --- | --- | --- |
| ARCHITECTURE-01 | 승인 대기 작업이 공통 작업 목록에서 사라짐 | P1 | P1 | T2.4 / T5.4 | [ARCHITECTURE 감사](01-architecture-audit.md) |
| ARCHITECTURE-02 | 잘못된 options가 빈 객체로 바뀌며 기본 Apple 범위로 해석됨 | P1 | P1 | T0A.3 | [ARCHITECTURE 감사](01-architecture-audit.md) |
| ARCHITECTURE-03 | 문서의 2단계 승인 설명과 실제 3단계 승인 상태가 다름 | P1 | P1 | T0A.5 | [ARCHITECTURE 감사](01-architecture-audit.md) |
| ARCHITECTURE-04 | 승인 경계가 transport/desktop wrapper에 있고 일반 쓰기 use case는 직접 실행 가능 | P1 | P1 | T0A.5 | [ARCHITECTURE 감사](01-architecture-audit.md) |
| ARCHITECTURE-05 | import_then_curate_to_album은 실제로 import_to_album으로만 변환됨 | P1 | P1 | T0A.6 | [ARCHITECTURE 감사](01-architecture-audit.md) |
| ARCHITECTURE-06 | daemon 종료가 thread 실제 종료와 durable worker 정리를 보증하지 않음 | P1 | P1 | T2.6 / G7 | [ARCHITECTURE 감사](01-architecture-audit.md) |
| ARCHITECTURE-07 | MCP 내 모바일 라우트 등록은 독립 서버의 worker lifespan을 연결하지 않음 | P1 | P1 | T2.6 / G7 | [ARCHITECTURE 감사](01-architecture-audit.md) |
| ARCHITECTURE-08 | 승인 소비와 시작 영수증 저장 사이 crash window | P1 | P1 | T0A.5 | [ARCHITECTURE 감사](01-architecture-audit.md) |
| ARCHITECTURE-09 | 자동 재조정 지원이 일부 앨범 action에 한정됨 | P1 | P1 | T0A.5 | [ARCHITECTURE 감사](01-architecture-audit.md) |
| ARCHITECTURE-10 | 현행 저장소 문서의 “원본 복제 없음”과 실제 managed 원본 복사가 충돌 | P1 | P1 | G0 / T6.7 | [ARCHITECTURE 감사](01-architecture-audit.md) |
| ARCHITECTURE-11 | 다중 DB·WAL·원본 사본의 일관된 백업/마이그레이션 계약 부족 | P1 | P1 | G0 / migration | [ARCHITECTURE 감사](01-architecture-audit.md) |
| ARCHITECTURE-12 | 승인 token과 상세 작업이 health 응답에 포함됨 | P1 | P1 | T0A.4 | [ARCHITECTURE 감사](01-architecture-audit.md) |
| ARCHITECTURE-13 | “loopback-only” 자동화 판단이 listener host에 의존 | P1 | P1 | T0A.4 | [ARCHITECTURE 감사](01-architecture-audit.md) |
| ARCHITECTURE-14 | local_only 오타는 원격 허용으로 해석되고 위치 외부 전송은 별도 정책 | P1 | P1 | T0A.3 | [ARCHITECTURE 감사](01-architecture-audit.md) |
| ARCHITECTURE-15 | Google-origin local materialization에서 정책 provenance가 중요함 | P2 | P2 | T2.2 / T2.5 | [ARCHITECTURE 감사](01-architecture-audit.md) |
| ARCHITECTURE-16 | 계층 테스트가 문서의 전체 의존 경계를 강제하지 못함 | P2 | P2 | T2.3 | [ARCHITECTURE 감사](01-architecture-audit.md) |
| ARCHITECTURE-17 | SourceRegistry·port 모델과 실제 vendor gateway 계약이 병존 | P2 | P2 | T2.3 | [ARCHITECTURE 감사](01-architecture-audit.md) |
| ARCHITECTURE-18 | 공통 응답·상태·ID 계약이 분산되고 오류가 단순 문자열로 투영됨 | P2 | P2 | T2.4 | [ARCHITECTURE 감사](01-architecture-audit.md) |
| ARCHITECTURE-19 | 자동화 부모·자식·사용자 조치의 연결을 하나의 작업 계약으로 설명하지 못함 | P2 | P2 | T2.4 / T5.4 | [ARCHITECTURE 감사](01-architecture-audit.md) |
| ARCHITECTURE-20 | 현행 아키텍처와 문서 검증이 확장된 계약을 따라가지 못함 | P2 | P2 | T6.8 | [ARCHITECTURE 감사](01-architecture-audit.md) |
| ARCHITECTURE-21 | 테스트의 단위 계약과 생명주기/E2E 범위를 분리해 관리할 필요 | P2 | P2 | G0 / G7 | [ARCHITECTURE 감사](01-architecture-audit.md) |
| ARCHITECTURE-22 | Google classification polling이 deferred terminal 상태를 완료 조건으로 보지 않음 | P2 | P2 | T2.4 / T2.6 | [ARCHITECTURE 감사](01-architecture-audit.md) |
| ARCHITECTURE-23 | 인물 오류·unknown을 큐 0/정리 완료로 오해하고 conflict 회복 계약이 부족 | P1 | P1 | T1.3 / T2.7 | [ARCHITECTURE 감사](01-architecture-audit.md) |
| UX-01 | 완료 작업과 분류 화면의 stale 상태 불일치 | P0 | P1 | T1.1 | [UX 감사](02-ux-cross-device-audit.md) |
| UX-02 | 실행 버튼 title/AX/Help가 서로 다른 의미 | P0 | P1 | T1.5 | [UX 감사](02-ux-cross-device-audit.md) |
| UX-03 | 홈이 작업 목표보다 MCP 준비 중심, 연결 headline은 고정 | P1 | P1 | T3.0 / T5.0 | [UX 감사](02-ux-cross-device-audit.md) |
| UX-04 | 작업 기록의 서버 정상 badge는 고정 | P0 | P1 | T1.6 | [UX 감사](02-ux-cross-device-audit.md) |
| UX-05 | '결과'의 객체와 출처가 기기마다 다름 | P1 | P1 | T2.4 / T5.2 | [UX 감사](02-ux-cross-device-audit.md) |
| UX-06 | Story 탭 shell 안내와 현재 웹 경로 맥락 불일치 | P1 | P1 | T1.4 | [UX 감사](02-ux-cross-device-audit.md) |
| UX-07 | 생성·작업·알림이 감상 설정 disclosure 안에 숨음 | P1 | P1 | T1.4 / T3.0 | [UX 감사](02-ux-cross-device-audit.md) |
| UX-08 | 웹 알림이 행동 없는 설명 카드 | P1 | P1 | T5.4 | [UX 감사](02-ux-cross-device-audit.md) |
| UX-09 | Android 최초 연결은 설정의 등록 JSON/ADB 중심 | P1 | P1 | T2.9 / T5.7 | [UX 감사](02-ux-cross-device-audit.md) |
| UX-10 | 사진 읽기와 원본 GPS 권한을 단일 성공 조건으로 묶음 | P1 | P1 | T2.9 / T5.7 | [UX 감사](02-ux-cross-device-audit.md) |
| UX-11 | 권한 승인 이후 원래 작업으로 복귀하지 않음 | P1 | P1 | T2.9 / T5.3 / T5.7 | [UX 감사](02-ux-cross-device-audit.md) |
| UX-12 | Google 계정 연결 설정은 기술 계약이 주 사용자 경로 | P1 | P1 | T5.7 / T3.0 | [UX 감사](02-ux-cross-device-audit.md) |
| UX-13 | 연결 오류가 일반 체크리스트와 raw message로 묶임 | P1 | P1 | T2.4 / T5.7 | [UX 감사](02-ux-cross-device-audit.md) |
| UX-14 | 제품 전체 검색과 현재 폴더 파일명 검색의 범위 차이 | P2 | P2 | T6.9 | [UX 감사](02-ux-cross-device-audit.md) |
| UX-15 | 대형 Mac 창 최소 크기와 전체 rebuild 전략 | P2 | P2 | T1.2 / T6.2 | [UX 감사](02-ux-cross-device-audit.md) |
| UX-16 | 결과 모드 전환 버튼과 inspector 이미지가 겹침 | P0 | P1 | T1.2 | [UX 감사](02-ux-cross-device-audit.md) |
| UX-17 | 선택한 사진으로 이야기 만드는 CTA가 선택 모드에서 숨음 | P1 | P1 | T3.1 | [UX 감사](02-ux-cross-device-audit.md) |
| UX-18 | 내보내기 계획/재시도는 있으나 작업 이어하기가 modal 중심 | P1 | P1 | T2.10 / T4.4 / T5.3 | [UX 감사](02-ux-cross-device-audit.md) |
| UX-19 | 저장 공간은 7개 Story까지만 표시, 전체 탐색 불가 | P1 | P1 | T6.7 | [UX 감사](02-ux-cross-device-audit.md) |
| UX-20 | Android 추천은 page 단위 탐색이며 화면 컨테이너 폭을 사용하지 않음 | P2 | P2 | T5.2 / T6.1 | [UX 감사](02-ux-cross-device-audit.md) |
| UX-21 | 추천 없음·Story 삭제·404가 비슷한 빈 상태 | P1 | P1 | T2.10 / T3.2 | [UX 감사](02-ux-cross-device-audit.md) |
| UX-22 | 인물 오류/미조회가 '모두 정리됨'으로 오해됨 | P0 | P1 | T1.3 | [UX 감사](02-ux-cross-device-audit.md) |
| UX-23 | 인물 확정 충돌이 raw exception으로 끝남 | P1 | P1 | T1.3 / T2.7 | [UX 감사](02-ux-cross-device-audit.md) |
| UX-24 | 동일 이름 인물 카드의 구별 단서 부족 | P1 | P1 | T2.7 | [UX 감사](02-ux-cross-device-audit.md) |
| UX-25 | Android 인물 진입 접근성 문구가 '읽기 전용'인데 실제 변경 기능 존재 | P1 | P1 | T1.5 / T5.1 | [UX 감사](02-ux-cross-device-audit.md) |
| UX-26 | Android main 화면 복원이 날짜 세 필드로 제한 | P1 | P1 | T2.9 / T5.3 | [UX 감사](02-ux-cross-device-audit.md) |
| UX-27 | Android Back이 상위 맥락 대신 홈으로 이동 | P1 | P1 | T2.9 / T5.3 / T5.5 | [UX 감사](02-ux-cross-device-audit.md) |
| UX-28 | 웹 활성 primary 버튼의 다크 테마 대비 실패 | P0 | P1 | T1.6 / T6.2 | [UX 감사](02-ux-cross-device-audit.md) |
| UX-29 | native 단축키·focus 계약은 화면별로 분산 | P2 | P2 | T6.3 | [UX 감사](02-ux-cross-device-audit.md) |
| UX-30 | 시각 토큰이 존재하지만 적용·의미가 표면별로 다름 | P2 | P2 | T6.2 | [UX 감사](02-ux-cross-device-audit.md) |
| UX-31 | 검토 저장과 이동의 의미를 명시하는 화면 계약 부족 | P1 | P1 | T3.1 / T4.1 | [UX 감사](02-ux-cross-device-audit.md) |
| UX-32 | 통과 테스트가 제품 상태/겹침/기기간 역할을 보장하지 않음 | P1 | P1 | G1 / G6 / G7 | [UX 감사](02-ux-cross-device-audit.md) |
| STORY-01 | 다른 Story를 공유할 수 있는 대상 결합 오류 | P0 | P0 | T0A.1 | [STORY 감사](03-story-audit.md) |
| STORY-02 | 상세의 이야기 재구성도 전역 객체를 변경 | P1 | P1 | T0A.1 | [STORY 감사](03-story-audit.md) |
| STORY-03 | 선택 변경이 다른 초안이 아니라 같은 collection/Story로 수렴 | P1 | P1 | T2.1 | [STORY 감사](03-story-audit.md) |
| STORY-04 | 사용자/촬영 순서 대신 해시·장소 묶음 순서 | P1 | P1 | T2.1 / T4.1 | [STORY 감사](03-story-audit.md) |
| STORY-05 | ‘Story 만들기’ 이후 편집·초안·선택 수정 작업이 빠짐 | P1 | P1 | T4.1 | [STORY 감사](03-story-audit.md) |
| STORY-06 | revision 숫자는 있지만 편집 버전·복구 저장소가 아님 | P1 | P1 | T2.8 / T4.2 | [STORY 감사](03-story-audit.md) |
| STORY-07 | 삭제 tombstone을 생성 경로가 보호하지 않음 | P1 | P1 | T0A.2 | [STORY 감사](03-story-audit.md) |
| STORY-08 | 전체 갱신 500개 요청이 200개로 잘림 | P1 | P1 | T0A.2 | [STORY 감사](03-story-audit.md) |
| STORY-09 | 삭제/동의 철회/만료의 공유 열거 상한 | P1 | P1 | T0A.2 | [STORY 감사](03-story-audit.md) |
| STORY-10 | refs/date 검증과 사실 검증을 혼동 | P1 | P1 | T4.7 | [STORY 감사](03-story-audit.md) |
| STORY-11 | opaque ID만으로 AI 입력의 개인정보 위험이 제거되지는 않음 | P1 | P1 | T0A.3 / T4.7 | [STORY 감사](03-story-audit.md) |
| STORY-12 | 상세 GPS 공유 정책은 고정되어 있고 선택 가능한 공개 수준이 없음 | P1 | P1 | T4.5 | [STORY 감사](03-story-audit.md) |
| STORY-13 | 지도 설명과 실제 로딩 시점 계약 불명확 | P2 | P2 | T4.5 / T6.3 | [STORY 감사](03-story-audit.md) |
| STORY-14 | 완료 결과 생성 호출에서 인물 저장소가 빠짐 | P2 | P2 | T2.2 | [STORY 감사](03-story-audit.md) |
| STORY-15 | 완료 결과 생성은 durable operation이 아닌 daemon thread | P1 | P1 | T2.6 / T2.8 / T4.6 | [STORY 감사](03-story-audit.md) |
| STORY-16 | 일부 사진 저장 실패도 최종 완료 안내 | P1 | P1 | T2.4 / T3.2 | [STORY 감사](03-story-audit.md) |
| STORY-17 | 완료 결과 Story를 Android에서 재분석 가능 manual로 오인 | P1 | P1 | T5.6 | [STORY 감사](03-story-audit.md) |
| STORY-18 | 모바일 오류 재시도와 뒤로가기에서 선택 Story/목록 상태 손실 | P2 | P2 | T2.9 / T5.3 / T5.5 | [STORY 감사](03-story-audit.md) |
| STORY-19 | Mac 삭제·재분석의 범위 확인/복구 UX 부족 | P1 | P1 | T4.3 / T4.4 | [STORY 감사](03-story-audit.md) |
| STORY-20 | 생성 성공 후 생성된 ID로 바로 열지 않음 | P2 | P2 | T0A.1 / T3.4 | [STORY 감사](03-story-audit.md) |
| STORY-21 | 저장된 Story가 플랫폼별 고정 목록 제한 뒤로 사라짐 | P2 | P2 | T5.6 / T6.9 | [STORY 감사](03-story-audit.md) |
| STORY-22 | 사진 availability가 실제 파일/권한 상태와 분리 | P2 | P2 | T3.2 / T6.7 | [STORY 감사](03-story-audit.md) |
| STORY-23 | 사진/결과 export와 Story export를 혼동할 수 있음 | P2 | P2 | T4.9 | [STORY 감사](03-story-audit.md) |
| STORY-24 | 문서 완료 판정과 현재 소스·앞부분 설명의 충돌 | P2 | P2 | T6.8 | [STORY 감사](03-story-audit.md) |
| STORY-25 | 테스트 통과가 Story 작업 완결성과 개인정보 대상 정합을 보증하지 않음 | P1 | P1 | G2 / G4 / G7 | [STORY 감사](03-story-audit.md) |
| STORY-26 | 공유 패키지는 준비 완료 전에 active로 저장 | P2 | P2 | T4.8 | [STORY 감사](03-story-audit.md) |
| STORY-27 | fallback 실패 상태와 재시도 결과가 숨거나 저장되지 않을 수 있음 | P2 | P2 | T4.6 / T4.7 | [STORY 감사](03-story-audit.md) |
| STORY-28 | 선택 모드에서 선택 사진 Story CTA를 숨김 | P2 | P2 | T3.1 | [STORY 감사](03-story-audit.md) |
| STORY-29 | AI 1회 출력 한도가 대용량 사진/날짜 계약과 맞지 않음 | P1 | P1 | T4.7 | [STORY 감사](03-story-audit.md) |
| STORY-30 | 날짜/시간대 계약이 Story 출처·표시 범위와 혼재 | P2 | P2 | T2.2 / T3.2 | [STORY 감사](03-story-audit.md) |
| STORY-31 | Mac 폼은 서버가 금지하는 구성 조합을 선택 가능하게 함 | P2 | P2 | T2.2 / T3.2 | [STORY 감사](03-story-audit.md) |
| LIVE-01 | 분류 terminal 상태 불일치 | P1 | P1 | T1.1 | [직접 관찰](04-live-observations-and-documentation.md) |
| LIVE-02 | title/AX/Help 불일치 | P2 | P2 | T1.5 | [직접 관찰](04-live-observations-and-documentation.md) |
| LIVE-03 | 결과 header·inspector 겹침 | P1 | P1 | T1.2 | [직접 관찰](04-live-observations-and-documentation.md) |
| LIVE-04 | 인물 완료·실패 동시 표시 | P1 | P1 | T1.3 | [직접 관찰](04-live-observations-and-documentation.md) |
| LIVE-05 | Story route와 shell 맥락 불일치 | P2 | P2 | T1.4 | [직접 관찰](04-live-observations-and-documentation.md) |
| LIVE-06 | MCP 중심 홈 | P2 | P2 | T3.0 | [직접 관찰](04-live-observations-and-documentation.md) |
| LIVE-07 | 선택→Story 다음 행동 발견성 | P2 | P2 | T3.1 | [직접 관찰](04-live-observations-and-documentation.md) |
| LIVE-08 | 저장 공간 전체 Story 접근 부재 | P2 | P2 | T6.7 | [직접 관찰](04-live-observations-and-documentation.md) |
| DOC-01 | 제품 표면 누락 | P1 | P1 | T6.8 | [문서 대조](04-live-observations-and-documentation.md) |
| DOC-02 | 현재 화면 목록 누락 | P1 | P1 | T6.8 | [문서 대조](04-live-observations-and-documentation.md) |
| DOC-03 | 완료·대기/테마 범위 혼재 | P1 | P1 | T6.8 | [문서 대조](04-live-observations-and-documentation.md) |
| DOC-04 | action registry 근거 경로 오류 | P2 | P2 | T6.8 | [문서 대조](04-live-observations-and-documentation.md) |
| DOC-05 | 확장된 endpoint/보안/검증 상태 누락 | P1 | P1 | T6.8 | [문서 대조](04-live-observations-and-documentation.md) |

## 완료 추적 규칙

구현 시 각 ID에 PR/commit, baseline 재현, 변경 후 자동/설치본/기기 증거, residual risk와 gate 결과를 붙인다. 코드 수정만으로 closed 처리하지 않는다. 동일 원인을 가진 항목은 하나의 task로 해결하되 연결 ID를 제거하지 않는다. 현재는 모든 발견을 open으로 둔다.
