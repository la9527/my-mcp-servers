# macOS Story 화면·교차 클라이언트 작업 내역 동기화 검증

## 결론

PhotosMcp macOS 앱에 `Story` 사이드바를 추가하고, 소유자용 Story 포털을 앱 내부 `WKWebView`로 연결했다. 이 화면에서 저장된 Story를 열어 보고, 촬영 시작일·종료일, Apple/Google 소스, 사진 구성, 최대 장수를 선택해 Android와 동일한 수동 분석 큐에 새 Story를 요청할 수 있다. 요청은 `mac_app` 출처로 저장되지만 분석·추천·GPS 보강·Story 생성은 Android 요청과 같은 파이프라인을 사용한다.

Android에 남아 있던 데이터는 기기 안에 별도 복제된 결과가 아니었다. macOS의 기존 `전체 작업 기록 삭제`는 데스크톱 `workflow_runs`와 photo-ranker job만 지웠지만 Android의 작업·알림 화면은 `photo_automation_runs`, `curation_operations`, `browser_mission_runs`, `user_action_requests`를 조회했다. 같은 SQLite DB 안의 서로 다른 projection을 삭제 범위에 포함하지 않은 것이 원인이었다.

## 수정한 동기화 계약

| 사용자 동작 | 삭제 | 보존 |
|---|---|---|
| macOS `전체 작업 기록 삭제` | 종료된 데스크톱 작업, 종료된 통합/수동/브라우저 작업, 그 작업의 알림, 독립적인 과거 실패 알림 | 실행 중 작업, 추천 사진 보관 사본, 처리·중복 방지 원장, Story와 공유의 독립 lifecycle |
| Story 삭제 | 해당 Story를 `deleted`로 전환하고 활성 공유 폐기 | 추천 사진 보관 사본과 분석 근거 |
| Android/맥 Story 조회 | `ready` Story manifest만 표시 | 삭제된 Story를 추천 보관 사본으로 자동 재생성하지 않음 |

따라서 작업 내역 삭제와 Story 삭제는 서로 다른 동작이다. 작업 내역을 지워도 사용자가 보존한 Story는 남고, Story를 지우면 추천 사진 사본이 남아 있어도 Android와 macOS 화면에서 다시 나타나지 않는다. 새 Story 생성이나 명시적인 재분석을 실행할 때만 새 manifest가 만들어진다.

## macOS Story 기능

- 앱 사이드바에 `Story` 항목과 책 모양 시스템 아이콘을 추가했다.
- `http://127.0.0.1:18791/photos` 소유자 포털을 앱 안에 표시한다.
- standalone에 없는 선택 Python WebKit wrapper를 요구하지 않고 macOS 시스템 WebKit을 Objective-C runtime으로 로드한다.
- 네트워크 또는 렌더링 문제를 위한 `새로고침`, 외부 브라우저에서 확인하는 `브라우저에서 열기`를 제공한다.
- 최근 7일을 기본 기간으로 하며 최대 30일, 최대 1,000장을 허용한다.
- Apple Photos와 Google Photos를 각각 선택할 수 있다.
- `균형 있게`, `인물 위주`, `풍경 위주` 구성 계약을 사용한다. Google이 포함된 인물 모드는 현재 지원 경계에 따라 서버가 거부한다.
- 스크린샷은 제외하고 최대 실행 시간은 6시간이며, 이 요청만으로 앨범을 변경하지 않는다.
- 저장된 Story 목록과 진행 중인 Story 작업을 같은 화면에서 확인한다.

## 운영 데이터 정리 결과

사용자가 이미 요청했던 전체 작업 기록 삭제의 의도에 맞춰 운영 DB의 누락된 교차 클라이언트 기록을 정리했다. 사진 제목, 사람 이름, 파일 경로는 출력하거나 문서에 기록하지 않았다.

| 정리 대상 | 삭제 건수 |
|---|---:|
| 자동·통합 실행 기록 | 71 |
| 수동 Story 작업 기록 | 8 |
| Google Picker 브라우저 mission 기록 | 30 |
| 실행에 연결된 완료·오류·취소 알림 | 52 |
| 실행 ID가 없던 과거 실패 알림 | 15 |
| 합계 | 176 |

정리 후 자동·수동·브라우저 작업과 사용자 알림은 모두 0건이다. 표시 가능한 Story도 0건이다. 삭제 상태 Story 4건은 tombstone으로 남아 재등장과 공유 재활성화를 막는다. 로컬 추천 사본 94개, 추천 collection 11개와 member 104개는 Story 재분석 및 중복 방지를 위한 별도 보관 데이터이므로 삭제하지 않았다.

## 회귀 방지

- Android 홈·작업·결과·Story 화면은 앱으로 돌아올 때 서버 projection을 다시 읽는다.
- Story가 없을 때 홈의 `null · 0장` 대신 빈 상태를 표시한다.
- 추천 결과 API의 정상적인 404는 연결 장애로 오인하지 않고 `아직 표시할 추천 사진이 없습니다`로 표시한다.
- Story 조회 함수에서 Story 생성 함수를 제거했다. 조회는 읽기 전용이다.
- macOS 전체 삭제 버튼은 데스크톱 job이 0건이어도 Android용 종료 기록이 있으면 활성화된다.
- 삭제 확인 문구에 Mac·Android 작업 기록과 알림의 범위, Story·추천 보관 사본의 보존 범위를 명시했다.

## 검증

| 검증 | 결과 |
|---|---|
| 전체 Python 회귀 | 933 passed |
| 변경 경계 집중 회귀 | 169 passed |
| Python compile·Ruff | 통과 |
| Android debug build·lint | BUILD SUCCESSFUL |
| Android release R8·lint·서명 | BUILD SUCCESSFUL, APK Signature Scheme v2·v3 통과 |
| Android 실기기 설치 | Samsung SM-F966N, `0.6.1` (`versionCode=11`) 갱신 설치 성공 |

배포 APK는 108,817 bytes이고 SHA-256은 `ff8b7e85924825bcb9f5ed7f0f16511b6fd9eee2aee5e0848e68732f6c7c16ef`이다. Tailnet에서 다시 다운로드한 APK와 게시 원본의 digest가 일치한다.

## 설치본 확인

- `/Users/byoungyoungla/Applications/PhotosMcp.app`과 `/Volumes/ExtData/02_Services/PhotosMcp/PhotosMcp.app`을 같은 최종 source로 패키징하고 depth-first 서명·runtime smoke를 통과시켰다.
- 실행 앱의 Story 탭에서 제목, 기간·소스·구성·최대 장수 입력, `분석하고 Story 만들기` 버튼과 빈 Story 상태가 실제로 렌더링되는 것을 확인했다.
- 재기동 후 health는 `status=ok`, `daemon_status=ready`, active job·recent job·pending mutation이 모두 0이다.
- loopback `/photos`와 Tailnet `/photos`, `/mobile-client/download`는 HTTP 200이고 `/photos-actions`는 `/photos`로 HTTP 303 연결된다. Tailscale identity가 없는 모바일 loopback 직접 접근은 HTTP 403으로 유지된다.
- Samsung 실기기에는 기존 앱 데이터와 owner key를 보존한 채 0.6.1을 설치했다. 검증 시 기기가 보안 잠금 화면으로 다시 전환되어 화면 캡처 기반의 최종 빈 상태 확인은 수행하지 않았지만, 다음 잠금 해제 시 `onResume`이 홈·작업·추천·Story를 서버에서 다시 읽도록 구현·계약 검증했다.
