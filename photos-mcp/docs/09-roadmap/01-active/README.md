# 활성 후보

구현이나 실환경 검증이 아직 남은 다음 제품 단계를 기록한다. 완료된 로컬 선택, 사진 뷰어와 코드베이스 리팩터링 계획은 보관소로 이동했다.

## 추천 품질 검토

- [장면별 추천 품질 사람 검토](01-recommendation-quality-review-2026-08-10.md): 현재 자동추천과 사용자의 실제 1·2순위를 비교하고, 개인 검토 큐와 집계 지표를 이용해 다음 shadow score 실험의 기준선을 만든다.
- [인물 구성 기반 장면 분리와 얼굴 품질 추천](02-person-aware-scene-ranking-shadow-2026-08-10.md): 자동 승격 판정 pipeline까지 구현했다. strict veto는 87장면에서 +1.1495%p에 그쳐 운영 반영 없이 shadow를 유지하며, 독립 holdout 5쌍의 사람 라벨이 남아 있다.
- [Google Photos Picker 실연동](03-google-photos-picker-integration-2026-08-13.md): OAuth·Picker REST·temporary cache·분류 bridge·새 앱 생성 앨범 업로드와 복구 backend까지 자동 구현·계약 검증을 마쳤다. 실제 계정 OAuth 동의·Picker 선택·앨범 확인은 사용자 E2E가 남아 있다.
- [Google Photos 입력·결과 앨범 AppKit UX](04-google-photos-appkit-ux-2026-08-13.md): source 선택, browser Picker 대기, 결과 앨범 업로드 승인 흐름을 구현하고 controller 테스트를 완료했다. 실제 계정 화면 검증은 Google 외부 설정 후 진행한다.
- [Google Photos OAuth 앱 설정](05-google-photos-oauth-app-settings-2026-08-15.md): OAuth Client ID·Redirect URI·선택적 Client secret을 앱에서 입력해 macOS Keychain에 저장하고 즉시 runtime에 반영한다. callback URL 자동 수신과 실제 계정 E2E는 후속 검증 대상이다.
- [Google Photos 재선택과 작업 기록 정리](06-google-photos-reselection-and-job-history-cleanup-2026-08-15.md): 취소·제출 뒤 새 선택과 화면 초기화를 제공하고, 작업 기록 개별·전체 삭제 시 앱 생성 결과·미리보기·Google 임시 다운로드를 함께 안전하게 정리한다.
- [소스 인식형 사진 분류와 Google Photos 자동 준비](07-source-aware-classification-workflow-2026-08-15.md): Apple·로컬·Google의 2단계를 소스별로 분리하고, Google Picker 콜백 뒤 사진만 자동 다운로드한 다음 공통 분석 방법과 실행 전 확인으로 합류시킨다.
- [장면 대표 한 장 결과 갤러리](08-scene-best-only-result-gallery-2026-08-15.md): 같은 장면에서는 최고 점수 대표 사진만 먼저 보여주고, 나머지는 전용 비교 창에서 확인하도록 결과 탐색을 정리한다.
- [작업 기록 신뢰성 있는 삭제와 진행 상태](09-job-history-reliable-deletion-2026-08-16.md): 실패·재개 확인 기록을 포함한 통합 삭제, 생성 캐시 정리, 대량 삭제 진행률과 안전 경계를 정의한다.
- [Google Photos 원본 및 메타데이터 보존](10-google-photos-original-metadata-2026-08-16.md): Picker 원본 다운로드와 보조 JSON의 보존 범위, 위치 정보 제한과 Takeout 연계 원칙을 정의한다.

## 인물 확인과 개인화

vendor 계층에는 얼굴 crop, embedding, 수동 label 저장 기능이 있다. 메인 앱에서 사용자가 얼굴을 확인하고 이름을 지정하며 잘못된 분류를 수정하는 완결된 흐름은 별도 설계와 개인정보 검증이 필요하다.

완료 조건:

- 미확인 얼굴 묶음 검토 UI
- 사용자 확인 전 이름 자동 확정 금지
- 로컬 저장·삭제·재학습 범위 제어
- 같은 인물 병합과 잘못된 인물 분리
- ground truth 기반 정확도 측정

## 선호 학습

추천·제외 선택을 비식별 집계 feature로만 저장하는 로컬 shadow 저장소와 정규화 모델 기반을 구현했다. 표본 수·class 균형·명시적 동의·독립 holdout을 통과하기 전에는 기존 점수에 영향을 주지 않는다. Google Photos 입력은 개인화 대상에서 제외한다.

## 동기화 목적지

사용자가 승인한 iCloud Drive 또는 OS 동기화 root 안으로만 원자적 복사하고 hash 검증·충돌 회피·영수증을 남기는 목적지 adapter를 구현했다. 로컬 복사를 cloud 업로드 완료로 오인하지 않도록 상태는 `copied_to_sync_root`, `cloud_sync_verified=false`로 분리한다. 기존 내보내기 UI 연결과 실제 cloud 전파 확인은 다음 제품 단계다.

## 다음 검증 원칙

각 후보는 개인정보를 제거한 결과 요약과 재현 명령을 `reports`에 남긴 뒤 완료 처리한다. 리팩터링 실환경 회귀 결과는 [2026-08-10 검증 보고서](../../08-reports/01-validation/07-refactor-real-environment-regression-2026-08-10.md)에 기록했다.
