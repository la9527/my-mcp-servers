# 활성 후보

구현, 외부 계정 검증 또는 장기 표본 수집이 아직 남은 다음 제품 단계만 기록한다. 구현과 회귀 검증이 끝난 계획은 완료 보관소로 이동한다.

## 추천 품질 검토

- [장면별 추천 품질 사람 검토](01-recommendation-quality-review-2026-08-10.md): 현재 자동추천과 사용자의 실제 1·2순위를 비교하고, 개인 검토 큐와 집계 지표를 이용해 다음 shadow score 실험의 기준선을 만든다.
- [인물 구성 기반 장면 분리와 얼굴 품질 추천](02-person-aware-scene-ranking-shadow-2026-08-10.md): 독립 holdout 5쌍의 사람 검토는 같은 사람 5·오병합 0으로 완료했다. 다만 독립 표본의 Wilson 95% 상한이 43.45%이고 strict veto도 87장면에서 +1.1495%p에 그쳐 운영 반영 없이 shadow를 유지한다.
- [Google Photos Picker 실연동](03-google-photos-picker-integration-2026-08-13.md): OAuth·Picker REST·temporary cache·분류 bridge·새 앱 생성 앨범 업로드와 복구 backend까지 자동 구현·계약 검증을 마쳤다. 실계정에서 56장 선택·50장 분류·추천 30장 새 album 업로드와 Picker 취소·재선택·만료 정리를 검증했다. refresh token 철회 뒤 재연결과 실제 네트워크 단절 기반 부분 업로드 복구 E2E가 남아 있다.
- [Google Photos 입력·결과 앨범 AppKit UX](04-google-photos-appkit-ux-2026-08-13.md): source 선택, browser Picker 대기, 결과 앨범 업로드 승인 흐름을 구현하고 controller 테스트를 완료했다. 실제 계정 화면 검증은 Google 외부 설정 후 진행한다.
- [Google Photos OAuth 앱 설정](05-google-photos-oauth-app-settings-2026-08-15.md): OAuth Client ID·Redirect URI·선택적 Client secret을 앱에서 입력해 macOS Keychain에 저장하고 즉시 runtime에 반영한다. callback URL 자동 수신과 실제 계정 E2E는 후속 검증 대상이다.

## 선호 학습

추천·제외 선택을 비식별 집계 feature로만 저장하는 로컬 shadow 저장소와 정규화 모델 기반을 구현했다. 표본 수·class 균형·명시적 동의·독립 holdout을 통과하기 전에는 기존 점수에 영향을 주지 않는다. Google Photos 입력은 개인화 대상에서 제외한다.

다음 장기 검증은 새 500~1,000장 작업에서 두 번째 추천의 `duplicate` 양성 label을 최소 20개 자연 수집하는 것이다. 현재 표본만으로 정책을 승격하거나 인위적으로 양성 사례를 만들지 않는다.

## 동기화 목적지

사용자가 승인한 iCloud Drive 또는 OS 동기화 root 안으로만 원자적 복사하고 hash 검증·충돌 회피·영수증을 남기는 목적지 adapter를 구현했다. 로컬 복사를 cloud 업로드 완료로 오인하지 않도록 상태는 `copied_to_sync_root`, `cloud_sync_verified=false`로 분리한다. 기존 내보내기 UI 연결과 실제 cloud 전파 확인은 다음 제품 단계다.

## 다음 검증 원칙

각 후보는 개인정보를 제거한 결과 요약과 재현 명령을 `reports`에 남긴 뒤 완료 처리한다. 완료 문서 이동 근거는 [2026-09-01 활성 로드맵 정리 검증](../../08-reports/01-validation/27-active-roadmap-cleanup-2026-09-01.md)에 기록했다.
