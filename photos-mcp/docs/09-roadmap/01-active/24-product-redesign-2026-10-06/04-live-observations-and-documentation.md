# 설치 앱 관찰과 문서 정합성 감사

> 조사일: 2026-10-06 (Asia/Seoul)
> 코드 기준: `328bc9f063675fe1e0f532310816b97708c92b10` 및 조사 시작 시 작업 트리
> 범위: 현행 문서, 소스, 기존 계약 테스트, 설치 macOS 앱의 읽기 전용 탐색
> 상태: 개선 계획의 근거. 이 문서 작성은 제품 수정이나 재배포 완료를 뜻하지 않는다.

## 검증 경계

부모 에이전트가 Computer Use로 설치된 `PhotosMcp`의 홈, 사진 분류, Story, 작업 기록, 저장 공간, 인물 관리와 완료 결과 창의 사진 보기·선택 및 저장 모드를 직접 확인했다. 메인 창은 약 1398×812, 결과 창은 약 1920×978의 관찰 화면이다. 기존 결과를 열고 화면을 이동했으며 새 분석, 인물 재색인, 저장, 삭제, 공유 생성, 설정 변경은 실행하지 않았다.

설치 앱의 화면과 현재 체크아웃 코드는 독립적인 근거다. 설치 bundle의 commit 대응은 확인하지 않았으므로 코드에서 설명 가능한 경로와 실제 관찰을 구별한다. 화면 속 개인 사진·실명·장소·자산 경로는 이 문서에 복제하지 않는다. 개인 스크린샷을 저장소에 저장하지 않는다.

Android는 이번 부모 검증에서 실제 기기를 조작하지 않았다. 에이전트의 코드 검토와 자동 테스트는 기기에서의 렌더·TalkBack·백그라운드 복구 검증을 대신하지 않는다. 모든 폭·모든 데이터 조합·모든 사용자 흐름을 실제 실행했다고 주장하지 않는다.

## 직접 관찰한 문제

| ID | 우선순위 | 직접 관찰 | 현재 코드의 대응 근거 | 개선 및 완료 조건 |
| --- | --- | --- | --- | --- |
| LIVE-01 | P1 | 분류 화면은 `분석 진행 중`으로 비활성화되어 있으나 작업 기록은 진행 중 0건이고 해당 이전 작업도 완료·100%였다. 준비 문구와 시작 완료 문구도 함께 남았다. | `interfaces/appkit/classification/controller.py:890-916`은 요청 수락 후 버튼을 비활성화한다. `interfaces/appkit/main/controller.py:155-161`은 classification의 snapshot 변화에 따른 rebuild를 제외한다. | 입력 초안과 JobViewState 분리. 수락 시 job_id를 바인딩하고 terminal 상태를 구독하여 결과 보기/새 분석으로 전환. 완료 후 재진입·앱 재시작·실패·취소에서 같은 상태를 표시한다. |
| LIVE-02 | P2 | 분류 버튼의 화면 상태, AX Description의 선택 수/작업 종류, AX Help의 기본 50장 분류가 일치하지 않았다. | `classification/controller.py:499`, `:890-916`, `:1079` 주변의 기본 도움말·상태 갱신 경로. | title·enabled·AX label·help를 하나의 view model에서 계산한다. 50/362/10,000장, 분류/선별, 진행/완료 상태마다 일치 확인. |
| LIVE-03 | P1 | 결과 창의 우상단 작업 모드 컨트롤과 inspector 이미지 위쪽이 같은 영역을 점유했다. 사진 보기와 선택 및 저장 두 모드에서 재현됐다. | `results/controller.py:928-930`은 inspector 높이를 전체 창 높이에서 계산한다. 상단 header/workspace 예약 높이가 별도로 있다. | header/body/footer 공통 layout rect 계약으로 교체. 최소/기본/최대 창에서 컨트롤과 inspector bounds의 교집합 0, 초점 표시·클릭 영역 가림 0. |
| LIVE-04 | P1 | 인물 화면에 `지금은 모두 정리됐어요`와 `인물 찾기 실패 · ConfirmedMembershipConflictError`가 동시에 보였다. | `people/controller.py:158`은 review_count 0만으로 성공 문구를 만든다. `:626`은 예외 class name을 사용자 문구에 넣는다. 해당 파일에는 조사 전 미커밋 변경이 있다. | ready-empty와 failed/unknown을 분리한다. 충돌 시 이전 인물 정보를 유지하며 해결할 얼굴·다시 시도·상세 진단을 제공. 오류 상태에서 정리 완료를 선언하지 않는다. |
| LIVE-05 | P2 | Story 탭의 상위 안내는 날짜 선택·생성을 설명하지만 WebView는 이전에 연 Story 상세 감상 화면이었다. 앱 shell에는 Story 목록/뒤로가기/새 초안의 명확한 구분이 없다. | `main/controller.py:670-687`의 portal URL 및 WebView 유지, `:687` 이후 고정 Story header. | Story 라이브러리/만들기/편집/감상 route와 제목을 분리한다. 상세에서 목록으로 복귀하고 뒤로가기는 viewer→상세→목록 순서로 작동. 상위 안내는 실제 route와 일치. |
| LIVE-06 | P2 | 홈은 보관함 연결·MCP 요청 준비 중심이다. 사용자가 이어 할 검토·Story 초안·휴대폰 조치가 홈의 주요 행동으로 묶이지 않는다. | `main/controller.py:302` 이후 홈 구성, 사용자 가이드의 홈 정의. | 홈은 할 일/이어하기/최근 결과 중심. 서버 운영 정보는 작은 상태 영역. 권한·연결 문제가 작업을 막을 때만 주 행동으로 승격. |
| LIVE-07 | P2 | 결과→Story 버튼이 main body 하단에 고립되어 있다. 선택 및 저장의 현재 선택과 Story에 사용할 사진의 관계를 버튼만으로 알기 어렵다. | `results/controller.py:471` 및 Story 실행 경로, `application/result_story_service.py:77` 이후 `explicit_photo_ids` 지원. | 버튼 전에 추천/내 선택/전체 결과 범위를 보여주고 장수·제외 사유를 확인한다. 감상 초안을 만든 후 저장·공유는 별도 단계. 선택 정책이 기기간 유지되어야 한다. |
| LIVE-08 | P2 | 저장 공간의 Story 용량 목록은 관찰 화면에 7개 행만 노출되며 전체 목록 접근이 없다. 참조 용량과 실제 삭제 가능한 용량의 차이가 다음 행동으로 연결되지 않는다. | `main/controller.py:483-500`은 최대 7개 visible story를 잘라 보여준다. | 전체 목록 또는 페이지 처리. 고유 바이트·중복 참조·파생 캐시·회수 가능량을 분리한다. 삭제 전 영향을 받는 Story/공유/보관 사본을 설명. |

위 항목은 해당 설치 앱 상태에서 확인한 증상이다. 상태가 언제부터 남았는지, 설치본 버전과 체크아웃이 정확히 같은지는 후속 구현 baseline에서 확인해야 한다.

## 문서가 제품 범위를 따라가지 못하는 문제

| ID | 우선순위 | 근거 | 문제 | 작업 방향 |
| --- | --- | --- | --- | --- |
| DOC-01 | P1 | `docs/README.md:82-88`, `docs/01-getting-started/README.md:7-19` | 두 표면(macOS·MCP) 중심 설명에 소유자 웹, 공개 공유, Android 제어·GPS 역할이 빠져 있다. | 사용자 제품 표면 5개와 내부 receiver/worker 경계를 명시한다. public share와 owner control 권한을 구분한다. |
| DOC-02 | P1 | `docs/02-user-guide/README.md:7-16`; `main/controller.py:250-260` | 가이드는 4개 탭, 실제 앱은 Story·인물·저장 공간을 포함한 7개 탭이다. | 현재 화면 목록·각 작업 목적·기기간 이어하기·Story 사용법을 구현 이후 함께 갱신한다. |
| DOC-03 | P1 | `docs/09-roadmap/01-active/README.md:3`, `:9`, `:11`, `:39`; `application/story_presentation.py:15-18` | 동일 인덱스에서 parity 계획은 확인 대기/구현 완료로, Story는 2종/5종으로 다르게 설명된다. | 문서별 검증일·코드 기준·구현 상태·남은 gate를 표로 단일 관리. 과거 승인안과 현재 제공되는 선택지를 구별한다. |
| DOC-04 | P2 | `docs/README.md:107`; `tests/test_action_contract_docs.py:6`; `application/action_options.py` | 안내의 action 파일 경로가 현재 registry 경로와 다르다. compatibility wrapper와 기능 소유 모듈도 혼재한다. | 새 감사 링크 추가와 함께 registry 안내 경로를 교정. 후속 문서 정합화 때 소유 모듈 기준으로 근거 경로 갱신. |
| DOC-05 | P1 | `docs/04-architecture/README.md:3`, `:52`, `:73-78`; 현행 HTTP·Android 모듈 | 외부 endpoint/인증/Story/모바일 모델이 시스템 개요에 충분히 나타나지 않는다. Google 실계정 검증도 과거 상태로 서술된다. | 아키텍처·보안·운영 문서를 배포 artifact 기준으로 함께 갱신. 소스에서 지원됨/배포됨/실계정 검증됨을 별도로 표기. |

## 이번 자동 검증

다음은 현재 체크아웃에서 부모가 직접 실행했다.

```bash
.venv/bin/python -m pytest -q \
  tests/test_action_contract_docs.py \
  tests/test_result_story_service.py \
  tests/test_story_generation.py \
  tests/test_story_presentation.py \
  tests/test_story_sharing.py \
  tests/test_mobile_client.py \
  tests/test_mobile_client_server.py \
  tests/test_menu_appkit_layout.py
```

결과: **194 passed in 6.95s**. 기존 계약의 확인이며 LIVE-01~08이 해결되었다는 뜻은 아니다.

既存のStory・承認・スナップショット・モバイル契約を残したまま改善する。描画文字列の存在だけを確認するテストに、状態遷移、実際のbounds、失敗復帰、同じ選択集合の比較を追加する必要がある。
