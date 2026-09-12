# 활성 후보

구현, 외부 계정 검증 또는 장기 표본 수집이 아직 남은 다음 제품 단계만 기록한다. 구현과 회귀 검증이 끝난 계획은 완료 보관소로 이동한다.

## 추천 품질 검토

- [장면별 추천 품질 사람 검토](01-recommendation-quality-review-2026-08-10.md): 현재 자동추천과 사용자의 실제 1·2순위를 비교하고, 개인 검토 큐와 집계 지표를 이용해 다음 shadow score 실험의 기준선을 만든다.
- [인물 구성 기반 장면 분리와 얼굴 품질 추천](02-person-aware-scene-ranking-shadow-2026-08-10.md): 독립 holdout 5쌍의 사람 검토는 같은 사람 5·오병합 0으로 완료했다. 다만 독립 표본의 Wilson 95% 상한이 43.45%이고 strict veto도 87장면에서 +1.1495%p에 그쳐 운영 반영 없이 shadow를 유지한다.
- [Google Photos Picker 실연동](03-google-photos-picker-integration-2026-08-13.md): OAuth·Picker REST·temporary cache·분류 bridge·새 앱 생성 앨범 업로드와 복구 backend까지 자동 구현·계약 검증을 마쳤다. 실계정에서 56장 선택·50장 분류·추천 30장 새 album 업로드와 Picker 취소·재선택·만료 정리를 검증했다. refresh token 철회 뒤 재연결과 실제 네트워크 단절 기반 부분 업로드 복구 E2E가 남아 있다.
- [Google Photos 입력·결과 앨범 AppKit UX](04-google-photos-appkit-ux-2026-08-13.md): source 선택, browser Picker 대기, 결과 앨범 업로드 승인 흐름을 구현하고 controller 테스트를 완료했다. 실제 계정 화면 검증은 Google 외부 설정 후 진행한다.
- [Google Photos OAuth 앱 설정](05-google-photos-oauth-app-settings-2026-08-15.md): OAuth Client ID·Redirect URI·선택적 Client secret을 앱에서 입력해 macOS Keychain에 저장하고 즉시 runtime에 반영한다. callback URL 자동 수신과 실제 계정 E2E는 후속 검증 대상이다.
- [일일 사진 큐레이션 자동화](06-daily-photo-curation-automation-2026-09-03.md): Apple Photos의 신규 자산을 증분 발견해 장면별 우수 후보를 매일 선별하고 Hermes cron·Telegram으로 검토하는 계획이다. Google Photos는 공식 Picker 경계를 유지한 사용자 선택형 inbox로 분리하고, 14일 read-only shadow 뒤 add-only 앨범 정책을 단계적으로 도입한다.
- [일일 사진 큐레이션 단계별 구현 계획](07-daily-photo-curation-implementation-plan-2026-09-03.md): `date_added` 증분 탐색부터 처리 원장, `daily_curate`, Google Picker 사용자 조치, Hermes 텔레그램 브리지까지의 구현 순서와 검증 gate를 추적한다.
- [추천 사진 통합 보관과 그룹 앨범 이중 저장 계획](08-recommended-photo-storage-and-album-plan-2026-09-04.md): Apple·Google 추천 사진의 실제 바이트를 촬영 날짜별 로컬 기준 사본으로 모은 뒤, 그룹마다 지정한 Apple 또는 Google 앨범 한 곳에 add/import/upload하는 이중 저장·중복 방지·승인 계획이다.
- [Tailscale 추천 사진 생성형 Story Album·Swiper·외부 공유 계획](09-tailscale-swiper-recommendation-gallery-plan-2026-09-06.md): 외부 공유 Vertical Slice를 운영 반영했다. Tailnet 전용 소유자 grid와 별도 8443 Funnel gateway, immutable SharedStoryPackage, passcode/session, 기본 30일 만료, 2048px·EXIF 제거 JPEG 다운로드와 즉시 폐기를 실제 URL에서 검증했다. Qwen3.8 Story Director, GPS 근거와 날짜·위치 chapter는 후속 고도화 범위다.
- [휴대폰 원본 메타데이터 수집과 안정적인 사진 뷰어 개선 계획](10-mobile-original-metadata-and-resilient-gallery-plan-2026-09-07.md): Android GPS Bridge의 Keystore 서명·암호화 outbox, Mac의 P-256 검증·replay/idempotency 방어·Keychain AES-GCM ledger를 구현했다. 새 `10000`은 열지 않고 기존 public `8443/mobile-location`만 `127.0.0.1:18793` 전용 receiver로 분리했으며 `443`/`9119`는 사설로 유지했다. Google Picker 자동 연결은 A-only 품질 gate와 late reconciliation이 남아 있다.
- [PhotosMcp Android Companion 앱 아키텍처·제품 로드맵](11-photosmcp-android-companion-app-architecture-roadmap-2026-09-08.md): GPS outbox를 보존한 `PhotosMcp 앨범` 0.5.0에 실제 추천 grid·확대/플리킹·Story WebView와 촬영일 기반 수동 Story 작업을 구현했다. Apple/Google·최대 1,000장, 6시간 queue, device-signed control, Google 명시 날짜 Qwen/Chrome mission, 실행별 Story 격리를 검증했다. generic push, 1,000장 UI 가상화, 고유 release 서명 migration이 다음 gate다.
- [상세 장소·Google 지도·캡처 제외 전환 계획](12-detailed-place-map-and-screenshot-exclusion-plan-2026-09-09.md): 본인·가족 사용에 맞춰 exact GPS와 명확한 POI를 Story·30일 공유에 표시하고, 애매하면 시·군·구로 내리는 정책으로 전환한다. Google Maps Embed, Place ID 중심 저장, API 키 분리와 Apple/Google 캡처의 분석 전 제외·기존 추천 정리까지 단계별로 검증한다.
- [인물 중심 추천·동일인 확인·Story 인명 반영 계획](13-person-centric-curation-and-story-identity-plan-2026-09-09.md): 기존 person 점수·Apple 사람 필터·동일인 shadow·People UI를 하나의 private identity 흐름으로 연결한다. 자동 동일인은 검토 후보로만 사용하고, 소유자가 확인한 이름만 개인 Story에 결정적으로 표시하며 가족 공유는 인물별 별도 동의를 적용한다.
- [삭제·재분석·추천 앨범 수명주기 재설계](14-deletion-reanalysis-and-recommendation-lifecycle-plan-2026-09-10.md): 작업 목록·Story·처리 원장·추천 archive·현재 추천·외부 앨범·인물·GPS의 삭제 경계를 분리한다. 수동 재분석은 exact collection Story와 로컬 결과를 먼저 만들고, 기존 앨범 추가와 새 버전 앨범 게시를 구분한 뒤 generation/current-head와 provider membership 정합화로 단계적으로 전환한다.
- [Google Photos 검색·분할 수집 개선안](15-picker-search-and-batched-acquisition-plan-2026-09-11.md): Picker 날짜·앨범 검색과 다중 화면 매크로를 결합하고, 최대 1,000장을 100장 단위로 내구 다운로드·분석한 뒤 Google·Apple 추천과 Story를 하나로 통합한다. 여러 Picker 세션은 날짜 범위의 완전 소진 또는 안정적인 이어선택이 검증된 경우에만 사용한다.
- [인물 이름 연계 Story·인물별 사진 탐색 개선 계획](16-person-linked-story-and-people-gallery-plan-2026-09-11.md): 보존된 인물 이름과 최신 추천 자산 사이의 association 단절을 복구하고, Apple alias 확인·안정 얼굴 lineage·인물별 사진 필터·오인식 수정·검증 가능한 Story v4 인물 서사를 단계적으로 연결한다.
- [운영 인물 인덱싱·재검증·통합 관리 UX 계획](17-operational-people-indexing-and-review-ux-plan-2026-09-12.md): 첫 수직 기능과 20장 운영 파일럿을 완료했다. Apple 후보 사진 검수, 설치 앱의 YuNet·SFace 인물 인덱싱, macOS·Android의 stable overview를 연결했으며 사용자 인물 확인과 최대 1,000장용 helper·checkpoint, 고급 병합·분리 UX를 다음 단계로 남긴다.
- [얼굴 단위 다인물 사진 검토·Android 이름 입력 구현 계획](18-face-level-multi-person-review-plan-2026-09-12.md): 첫 운영 수직 기능을 완료했다. schema v5, Apple alias 우선 얼굴 색인, 109개 bbox·검토 crop·highlight, 사진별 원자적 다인물 결정, 확정 얼굴 기반 이름 후보와 일치 가능성, macOS 정확 얼굴 선택, Android 0.8.2 얼굴별 기존 인물·새 이름·모르는 사람 무시·얼굴 아님·나중에를 배포했다. 높은 신뢰도 후보는 저장 전 자동 선택되며 무인 확정은 calibration gate 통과 전까지 비활성이다.
- [예외 중심 인물 자동 인식·Mac/Android 통합 계획](19-exception-only-people-recognition-mac-android-plan-2026-09-12.md): 모든 얼굴을 수동 분류하지 않고 품질 미달과 1~2회 등장 얼굴을 숨긴다. 독립적으로 3회 이상 등장한 새 인물과 애매한 기존 인물만 검토하며, 사용자 확정 anchor가 충분한 인물의 초고신뢰 얼굴은 별도 자동 상태로 연결한다. 자동 결과는 Story에 사용할 수 있지만 다음 인식 anchor에는 사용하지 않고 Mac·Android에서 같은 예외 큐·최근 자동 정리·교정·undo를 제공한다.

## 선호 학습

추천·제외 선택을 비식별 집계 feature로만 저장하는 로컬 shadow 저장소와 정규화 모델 기반을 구현했다. 표본 수·class 균형·명시적 동의·독립 holdout을 통과하기 전에는 기존 점수에 영향을 주지 않는다. Google Photos 입력은 개인화 대상에서 제외한다.

다음 장기 검증은 새 500~1,000장 작업에서 두 번째 추천의 `duplicate` 양성 label을 최소 20개 자연 수집하는 것이다. 현재 표본만으로 정책을 승격하거나 인위적으로 양성 사례를 만들지 않는다.

## 동기화 목적지

사용자가 승인한 iCloud Drive 또는 OS 동기화 root 안으로만 원자적 복사하고 hash 검증·충돌 회피·영수증을 남기는 목적지 adapter를 구현했다. 로컬 복사를 cloud 업로드 완료로 오인하지 않도록 상태는 `copied_to_sync_root`, `cloud_sync_verified=false`로 분리한다. 기존 내보내기 UI 연결과 실제 cloud 전파 확인은 다음 제품 단계다.

## 다음 검증 원칙

각 후보는 개인정보를 제거한 결과 요약과 재현 명령을 `reports`에 남긴 뒤 완료 처리한다. 완료 문서 이동 근거는 [2026-09-01 활성 로드맵 정리 검증](../../08-reports/01-validation/27-active-roadmap-cleanup-2026-09-01.md)에 기록했다.
