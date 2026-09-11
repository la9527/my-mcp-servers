# 실행 검증 보고서

- [2026-09-10 추천 버전·현재 결과와 읽기 전용 진단](43-recommendation-generation-and-readonly-audit-2026-09-10.md): 수동 전체 재분석의 복수 provider snapshot과 원자적 current 전환, 실패·증분·동시 실행 보호를 구현했다. 운영 로컬 파일 96개의 hash를 검증하고 과거 집계 차이 한 건을 자동 변경 없이 식별했다.
- [2026-09-11 Google Picker 날짜 검색·분할 수집 실검증](44-google-picker-date-search-batch-validation-2026-09-11.md): 5일 범위 후보 probe 후 희소 창은 연속 선택하고 과밀 창은 하루로 좁히는 적응형 검색과 제한형 DOM 선택·완료 매크로를 구현했다. 실제 Google 계정에서 단일 날짜 10회와 5일 이동 창 10회를 각각 선택·API 조회·임시 다운로드까지 연속 검증했고, 100장 다운로드 묶음과 다운로드 완료 전 세션 보존 정책도 확인했다.
- [2026-09-11 Android 수동 날짜 GPS 선동기화 구현·검증](45-android-manual-date-gps-prefetch-2026-09-11.md): 선택한 KST 촬영 날짜의 휴대폰 카메라 원본 GPS를 과거 체크포인트와 무관하게 먼저 암호화 전송하고, outbox 잔여가 0일 때만 수동 분석을 등록하도록 Android 0.7.1을 배포했다.

실제 앱 또는 MCP 검증 결과를 날짜가 포함된 Markdown 파일로 추가한다.

권장 파일명:

```text
YYYY-MM-DD-<검증-대상>.md
```

자동 테스트 결과는 전체 통과 수, 실행 시간, 명령을 함께 기록한다. Apple 사진 검증은 사진 원본이나 인물 이름을 문서에 포함하지 않고 개수와 상태만 남긴다.

## 보고서

- [2026-08-09 문서 재구성 검증](02-documentation-rebuild-2026-08-09.md)
- [2026-08-09 standalone 앱 빌드 및 화면 검증](03-standalone-app-build-and-ui-validation-2026-08-09.md)
- [2026-08-09 로컬 사진 500장 E2E 검증](02-local-500-photo-e2e-2026-08-09.md)
- [2026-08-09 결과 갤러리·상대 추천 기준 검증](04-result-gallery-relative-recommendation-2026-08-09.md)
- [2026-08-09 RAW 전체 화면 뷰어 캐시 검증](05-raw-viewer-preview-cache-2026-08-09.md)
- [2026-08-09 코드베이스 리팩터링 검증](06-codebase-refactoring-2026-08-09.md)
- [2026-08-10 리팩터링 실환경 회귀 검증](07-refactor-real-environment-regression-2026-08-10.md)
- [2026-08-10 추천 품질 사람 검토 UI 검증](08-recommendation-quality-review-ui-2026-08-10.md)
- [2026-08-10 추천 품질 사람 기준선 및 shadow 점수 검증](09-recommendation-shadow-score-2026-08-10.md)
- [2026-08-10 두 번째 추천 시각적 다양성 shadow 검증](10-recommendation-second-diversity-shadow-2026-08-10.md)
- [2026-08-10 인물 구성 장면 분리와 얼굴 품질 shadow 검증](11-person-aware-scene-shadow-2026-08-10.md)
- [2026-08-11 동일 인물 사진 pairwise VLM shadow 검증](12-person-pairwise-shadow-2026-08-11.md)
- [2026-08-11 인물 구성 라벨링·SFace 보정 기반 검증](13-person-composition-calibration-foundation-2026-08-11.md)
- [2026-08-14 얼굴 crop pair 직접 보정 검증](14-face-identity-pair-calibration-2026-08-14.md)
- [2026-08-14 얼굴 동일인 이중 임계값 shadow 검증](15-face-identity-dual-threshold-shadow-2026-08-14.md)
- [2026-08-14 얼굴 동일인 constrained grouping shadow 검증](16-face-identity-constrained-grouping-shadow-2026-08-14.md)
- [2026-08-14 복수 지지 병합 private audit UI 검증](17-face-identity-multi-support-audit-ui-2026-08-14.md)
- [2026-08-14 복수 지지 병합 audit 결과와 독립 holdout](18-face-identity-multi-support-audit-result-2026-08-14.md)
- [2026-08-14 동일 주 피사체 얼굴·표정 순위 shadow 검증](19-person-face-expression-pairwise-shadow-2026-08-14.md)
- [2026-08-14 활성 로드맵 자동 구현·회귀 검증](20-automated-roadmap-implementation-2026-08-14.md)
- [2026-08-20 Google Photos 실계정 E2E 검증](21-google-photos-real-account-e2e-2026-08-20.md)
- [2026-08-20 Linux Qwen3.8 VLM 기본값·실사진 검증](22-linux-qwen38-vision-runtime-2026-08-20.md)
- [2026-08-20 Google Photos 취소·만료 예외 흐름 검증](23-google-photos-exception-flow-2026-08-20.md)
- [2026-08-21 추천 다양성 검토 큐 재준비](24-recommendation-diversity-review-refresh-2026-08-21.md): 삭제된 과거 작업과 분리해 현재 보존된 결과의 개인 검토 큐를 다시 만들고, 두 번째 추천 중복 label 재수집 기준을 기록했다.
- [2026-08-22 추천 다양성 사람 검토 결과](25-recommendation-diversity-review-result-2026-08-22.md): 23개 복수 사진 장면의 개인 검토 집계와 shadow 재현성 보정, 현행 정책 유지 결론을 기록했다.
- [2026-09-01 독립 얼굴 holdout 완료와 readiness 재집계](26-independent-face-holdout-completion-2026-09-01.md): 5쌍 사람 검토 완료와 통계 부족 분리, aggregate-only 재집계 결과를 기록했다.
- [2026-09-01 활성 로드맵 정리 검증](27-active-roadmap-cleanup-2026-09-01.md): 구현·회귀 검증이 끝난 계획 7건과 관련 시안을 완료 보관소로 이동하고 실제 미완료 후보만 활성 목록에 남긴 근거를 기록했다.
- [2026-09-07 Android 원본↔Google Picker 매칭 기반 검증](28-android-google-picker-matching-foundation-2026-09-07.md): GPS sidecar용 다중 지문 A/B/C/D 판정기와 비식별 품질 gate를 구현했다. Android 원본 30장의 GPS 보존과 실제 50쌍 A 23·B 25·C 2·오답 후보 0을 복사 없이 ADB로 일회성 실측했다. 운영은 ADB가 아닌 Android 앱 offline outbox이며, A-only 95% gate를 위한 Picker `=d` 검증이 남아 있다.
- [2026-09-07 Android GPS 공개 수신과 실기기 E2E 검증](29-mobile-location-public-ingest-and-android-bridge-2026-09-07.md): 새 `10000`을 열지 않고 기존 Funnel `8443/mobile-location`에 write-only 수신기를 분리했다. Android 16 실기기에서 최근 10일 GPS manifest 268건을 3개 서명 배치로 전송해 Mac 암호화 저장·빈 outbox·24시간 예약 작업까지 확인했다.
- [2026-09-08 Android Companion read-only vertical slice 검증](30-android-companion-readonly-vertical-slice-2026-09-08.md): 기존 GPS Bridge의 package·키·outbox·JobScheduler를 유지한 `PhotosMcp 앨범` 0.2.0과 Tailnet 전용 모바일 BFF를 구현했다. 별도 owner key·challenge/session, redacted Home·Runs·Results, 일회용 Story WebView cookie, EXIF 제거 파생 이미지를 계약 테스트했으며 Python 817개, Android debug/release build·lint, 실제 Tailscale 443/8443 경계와 light/dark·130% 글자·가로 화면을 확인했다.
- [2026-09-08 Android 추천 사진 grid와 APK 배포 검증](31-android-native-recommendation-gallery-and-apk-delivery-2026-09-08.md): `PhotosMcp 앨범` 0.3.0의 적응형 실제 추천 썸네일 grid, 큰 preview, app-private 제한 cache와 bearer 파생 이미지 route를 구현했다. non-debuggable R8 APK를 기존 설치 인증서로 서명해 Tailnet owner 전용 다운로드로 게시하고 checksum·공개 비노출·전체 817개 회귀를 검증했다.
- [2026-09-08 사진 뷰어 확대·플리킹 검증](32-photo-viewer-zoom-pan-fling-2026-09-08.md): Android 추천 뷰어와 Story HTML을 1×~4× pinch, 두 번 탭 2.5×, 제한 pan과 기본 배율 좌우 fling으로 통일했다. 실제 운영 Story 28장에서 확대·pan·`1 / 28 → 2 / 28` 전환과 오류 0건을 확인하고 0.4.0 APK를 재배포했다.
- [2026-09-08 사진 뷰어 제스처 중심 컨트롤 정리](33-photo-viewer-gesture-first-controls-2026-09-08.md): 네이티브 추천 뷰어와 Story WebView에서 중복되는 좌우·`+`·`−` 버튼을 제거하고 상단에는 배율 초기화와 닫기, 하단에는 숫자와 진행 막대를 결합한 위치 indicator를 적용했다. 0.4.1 release APK를 빌드·서명해 재배포했다.
- [2026-09-08 Android 뷰어 시스템 바 보존](34-android-viewer-system-bars-2026-09-08.md): 추천 사진 viewer의 fullscreen Dialog를 일반 no-action-bar Dialog로 바꾸고 상태바·내비게이션 바를 명시적으로 표시했다. system bar와 display cutout inset을 viewer shell에 적용한 0.4.2 APK를 재배포했다.
- [2026-09-08 Android 날짜 선택 Story 수동 실행 검증](35-android-manual-date-story-2026-09-08.md): 촬영일·Apple/Google·최대 1,000장을 선택하는 앱 수동 작업, 서명·nonce·idempotency queue, Google 명시 날짜 Qwen/Chrome mission, 실행별 Story·추천 grid를 구현했다. 9월 4일·7일 실사진과 Android 16 실기기 버튼 실행으로 Qwen 직접 호출·자동 선택·중복 재사용·Story와 전용 추천 grid를 확인했다. 대량 실행에서 발견한 Qwen 127장 이후 fallback 전역 275/250 초과도 Picker dialog 전역 카운터와 잔여 예산 guard로 보강했으며 Python 839개, Android R8/lint, APK Tailnet 다운로드 일치, 443 control·8443 비노출을 검증한 0.5.0 기록이다.
- [2026-09-08 Story 재분석 및 안전 삭제 검증](36-story-reanalysis-and-safe-delete-2026-09-08.md): 수동 Story의 원래 범위를 복원하는 `다시 분석`, Apple·Google 기존 처리 제외 우회, Story soft delete와 활성 공유 즉시 폐기를 device-signed API와 Android 0.5.1 화면에 연결했다. 원본·추천 사본·분석 이력·앨범은 보존하며 전체 845개 회귀와 Android debug/release 빌드를 통과했다.
- [2026-09-09 Android 수동 작업·Google Picker 복구 안정화 검증](37-manual-picker-resilience-and-android-polling-2026-09-09.md): Android lifecycle 종료 뒤 남은 폴링 callback crash, 6시간 작업과 별개였던 Qwen 300초 제한, Google 부분 다운로드 전량 정리를 수정했다. 장수 기반 Qwen 예산, 동일 scope checkpoint 복구, 부분 분석과 남은 사진 이월, 앱 KST 표시를 Android 0.5.2에 반영했다.
- [2026-09-09 Android GPS Story 연결·Tailnet 결과 링크 복구 검증](38-mobile-gps-story-and-tailnet-actions-recovery-2026-09-09.md): 암호화 보관된 Android 원본 GPS를 Google 추천 사본에 보수적으로 연결해 기존 Story 97장 중 90장의 장소 표시를 복구했다. 완료 run 반복 재처리로 생긴 Hermes timeout 오탐과 Tailnet `/photos-actions` 404도 함께 수정하고 전체 861개 회귀·운영 URL을 검증했다.
- [2026-09-09 인물 중심 선별·Story 신원 기반 구현 검증](39-person-centric-selection-and-story-identity-foundation-2026-09-09.md): Android 수동 Story의 균형·인물·풍경 모드, 안정 private identity 저장소와 레거시 원장 보존 마이그레이션, 이름 없는 LLM 계약, 개인 Story의 결정형 인물 캡션과 가족 공유 기본 비공개 경계를 연결했다.
- [2026-09-09 macOS Story 화면·교차 클라이언트 작업 내역 동기화 검증](40-mac-story-and-cross-client-history-sync-2026-09-09.md): macOS 앱 안에서 Story 생성·열람이 가능한 소유자 포털을 연결하고, 데스크톱 작업 삭제 뒤 Android에 자동화·수동 작업·알림이 남던 다중 저장소 삭제 범위를 통합했다. Story 조회의 암묵적 재생성을 제거하고 Android 빈 상태·복귀 시 새로고침을 보강했다.
- [2026-09-10 날짜 재분석·작업 기록 의미·Google Picker 수량 무결성 보강](41-reanalysis-and-picker-count-integrity-2026-09-10.md): 작업 목록 삭제와 사진별 처리 원장의 차이, 인물 DB 보존 상태를 확인하고 Android의 명시적 기간 재분석 옵션과 Chrome 선택 수↔Picker API 반환 수 fail-closed 검증을 추가했다.
- [2026-09-10 삭제·재분석·추천 앨범 수명주기 Phase 0 검증](42-deletion-reanalysis-lifecycle-phase0-2026-09-10.md): 세 에이전트의 저장소·외부 앨범·복구 정책 감사를 통합했다. 수동 Story를 exact collection으로 제한하고, 추천 0장 실행이 과거 96장을 보여 주던 운영 Story를 원본·인물·GPS·앨범 삭제 없이 교정했으며 수동 결과의 월별 앨범 자동 편입과 외부 receipt 유실을 차단했다.
- [2026-09-10 추천 generation과 비파괴 초기화 검증](43-recommendation-generation-and-readonly-audit-2026-09-10.md): 재분석 세대와 현재 추천 head를 분리하고 초기화·재생성 경계를 검증했으며 Android 0.6.3 배포 상태를 기록했다.
- [2026-09-11 인물 연계 Story v4·Apple alias 수직 기능 검증](44-person-linked-story-v4-and-apple-alias-vertical-slice-2026-09-11.md): private identity DB v2, Apple 이름 후보의 소유자 확정 연결, Story-scoped 인물 필터·viewer 동기화, Android 검수 화면과 0.7.0 APK를 구현하고 1,000개 회귀를 통과했다.
- [2026-09-11 Android 수동 날짜 GPS 선동기화 구현·검증](45-android-manual-date-gps-prefetch-2026-09-11.md): 수동 Story가 분석보다 먼저 정확한 날짜 범위의 Android 원본 GPS를 전송하도록 실행 순서를 교정하고, 권한·전송 실패 시 분석 등록을 차단하는 fail-closed 경계를 검증했다.
