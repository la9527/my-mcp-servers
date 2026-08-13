# Google Photos Picker 실연동 검토

## 결론

Photos MCP에 Google Photos를 연결하는 첫 범위는 **사용자가 Google Photos 화면에서 직접 고른 사진을 읽기 전용으로 분류하는 기능**이다. 전체 보관함·앨범을 앱이 목록으로 탐색하는 기능은 포함하지 않는다.

2025-03-31 이후 Google은 기존 전체 보관함 조회 scope를 제거했고, 기존 보관함에서 사진을 고르는 용도에는 Picker API를 사용하도록 안내한다. Library API는 앱이 만든 사진·앨범 관리와 업로드 목적에 한정한다. [Google API 변경 안내](https://developers.google.com/photos/support/updates), [Picker 시작 안내](https://developers.google.com/photos/picker/guides/get-started-picker)

## 현재 상태

이미 구현된 기반은 다음과 같다.

| 영역 | 현재 구현 | 상태 |
| --- | --- | --- |
| source contract | `google_photos`는 대화형 Picker만 허용 | 완료 |
| OAuth 경계 | Picker 읽기 scope와 macOS Keychain 저장소 | 완료 |
| session lifecycle | create, poll, pagination, consume, timeout, cancel, cleanup fake 계약 | 완료 |
| 콘텐츠 | 64MB 상한의 임시 파일 materialize와 사용 후 삭제 | 완료 |
| 개인정보 정책 | Google 입력에서 얼굴 품질·얼굴 군집 비활성화 | 완료 |
| Library write | 앱 생성 콘텐츠 전용·명시적 승인 gate | 인터페이스만 구현 |
| 실제 OAuth | Google Cloud OAuth client 연동 | 미구현 |
| 실제 Picker REST | `photospicker.googleapis.com` adapter | 미구현 |
| AppKit UX | 로그인, 브라우저 열기, 선택 완료 대기, 결과 연결 | 미구현 |
| 실제 계정 E2E | OAuth, 만료 URL, 취소, 복구 검증 | 미실행 |

현재 fake adapter는 실제 API 연동 전에 lifecycle과 정책 경계를 검증하기 위한 것이며 Google 계정으로 로그인하거나 실제 사진을 가져오지는 않는다.

## 지원 범위와 제외 범위

### 1차 지원

1. 앱의 `사진 분류` 화면에서 `Google Photos에서 선택`을 누른다.
2. 앱 내부 고지와 사용자의 명시적 동의를 먼저 받는다.
3. macOS 기본 브라우저에서 Google OAuth와 `pickerUri`를 연다. `pickerUri`는 iframe에 열 수 없다.
4. Google Photos에서 사용자가 고른 사진만 session polling으로 가져온다.
5. 분석에 필요한 해상도만 임시 캐시에 내려받아 일반 분류 pipeline으로 전달한다.
6. 작업 완료·취소·실패 때 session과 임시 파일을 정리한다.
7. 결과 갤러리와 로컬 내보내기는 현재 구조를 재사용한다.

Picker session은 생성, 사용자 선택, `mediaItemsSet` polling, 목록 조회, 정리 순서를 따른다. polling 간격과 timeout은 Google 응답의 `pollingConfig`를 사용하고, 선택 결과는 페이지 단위로 읽는다. [세션 관리](https://developers.google.com/photos/picker/guides/sessions), [선택 항목 조회](https://developers.google.com/photos/picker/guides/media-items)

### 명시적 제외

- Google Photos 전체 보관함/앨범 목록, 날짜 검색, 자동 전체 백업
- Google 입력 사진의 얼굴 군집, 인물 식별, 인물 구성 검토·학습
- Google Photos를 일반 목적 갤러리 또는 대규모 저장소/CDN으로 사용하는 기능
- 기존 Google Photos 사진을 다른 Google 앨범에 임의로 재구성하는 기능
- Google 계정 간 공유·공유 앨범 제어

Google Photos 정책은 얼굴 군집 생성을 금지하고, 한정된 콘텐츠·한정된 시간의 사용자 선택 사용 사례에 Picker API를 요구한다. 앱은 데이터 이용 목적을 화면에서 명확히 고지하고, 동의 전에 수집을 시작하지 않아야 한다. [Google Photos API 데이터 정책](https://developers.google.com/photos/support/api-policy)

## 권장 UX

`사진 분류` 화면에 Apple/로컬 카드와 동등한 `Google Photos` 카드를 추가한다.

| 단계 | 사용자에게 보이는 내용 | 내부 처리 |
| --- | --- | --- |
| 연결 전 | `Google Photos에서 선택` | OAuth token 여부만 확인 |
| 고지 | 선택 사진을 이번 분류에만 사용하고 임시 저장한다는 설명과 동의 checkbox | 동의 기록 생성, 아직 API 호출 안 함 |
| 로그인 | `Google 로그인 계속` | 기본 browser OAuth, refresh token은 Keychain 저장 |
| 선택 | `Google Photos에서 사진 선택` | `sessions.create`, 기본 browser의 `pickerUri` 열기 |
| 대기 | 선택 완료 대기·취소 | 응답의 poll interval/timeout대로 polling |
| 확인 | 선택 수, 사진/동영상 수, 임시 다운로드 용량 예상 | pagination, MIME와 크기 검증 |
| 분류 | `선택한 사진 분류` | bounded cache materialize, source policy 적용 |
| 종료 | 결과 또는 실패·정리 결과 | content와 session cleanup, 작업 영수증 기록 |

동영상은 1차 화면에서 선택 수는 표시하되, 현재 정적 사진 분석이 지원하지 않는 형식이면 사진만 분류하거나 명확히 제외한다. 자동으로 동영상 frame을 추출하지 않는다.

## 기술 설계

### OAuth와 비밀 정보

- Google Cloud Console에서 **Google Photos Picker API**를 활성화하고 OAuth client ID를 생성한다.
- service account는 지원되지 않으므로 사용자 Google 계정 OAuth를 사용한다.
- 최초 연결에는 `photospicker.mediaitems.readonly`만 요청한다.
- refresh token과 account ID는 `GooglePickerCredentialRepository`를 통해 macOS Keychain에 저장한다.
- client ID는 설정 파일 또는 환경 변수에만 두고 repository·작업 로그에 넣지 않는다.
- OAuth callback은 loopback localhost listener를 사용하며 state와 PKCE를 검증한다.

Google의 공식 설정 안내는 API 활성화와 OAuth client ID를 요구하고, Google Photos API가 service account를 지원하지 않는다고 명시한다. [앱 설정](https://developers.google.com/photos/overview/configure-your-app), [권한 scope](https://developers.google.com/photos/overview/authorization)

### 실제 adapter

새 `GooglePhotosPickerAdapter`는 현재 `PhotoPickerPort`를 구현한다.

1. access token을 확인·갱신한다.
2. `POST https://photospicker.googleapis.com/v1/sessions`로 session을 만든다.
3. 응답의 `pickerUri`, `pollingConfig`, 만료 정보를 `PickerSessionRepository`에 저장한다.
4. `sessions.get`으로 `mediaItemsSet`을 확인한다.
5. 준비 후 `mediaItems.list?sessionId=...`를 pagination한다.
6. 선택 항목의 `id`, MIME, 파일명, `baseUrl` 만을 메모리에서 사용한다. `baseUrl`과 access token은 영구 DB에 저장하지 않는다.
7. materialize 직전에 `baseUrl=w<max>-h<max>` 형태의 해상도 URL을 만들고 Authorization header를 붙인다.
8. `baseUrl` 만료, 401/403, 네트워크 중단은 재선택 안내가 가능한 오류 코드로 바꾼다.
9. bytes를 얻은 뒤 또는 timeout/cancel 뒤 `sessions.delete`를 호출한다.

Picker `baseUrl`은 최대 60분이며 권한 철회 시 더 빨리 만료될 수 있고, 사용 시 Bearer token이 필요하다. 따라서 안정적인 파일 경로나 영구 원본 참조로 저장하면 안 된다. [선택 미디어 콘텐츠 규칙](https://developers.google.com/photos/picker/guides/media-items)

### Pipeline 정책

Google source capability는 이미 `face_quality=False`, `face_clustering=False`다. 실제 workflow 연결 시에도 이 정책을 application service에서 강제한다.

- VLM 기반 장면 설명·일반 분류: 가능하되, 외부 Linux VLM 전송 전 별도 동의 문구를 표시한다.
- 로컬 기술 점수·중복 억제: 원본 또는 thumbnail을 처리하므로 정책·동의 검토 뒤 1차에서는 비활성화하고, 일반 VLM 분석만 허용하는 보수적 시작을 권장한다.
- 인물 분류와 선호 학습: 금지.
- 결과 내보내기: 로컬 디렉터리만 1차 허용한다. Google 재업로드는 별도 승인 범위로 분리한다.

이는 Google 데이터가 외부 Linux workstation으로 전송될 수 있는 현재 Photos MCP 환경을 고려한 보수적 설계다. 정책상 사용자 데이터 전송은 사용자 동의와 화면에 보이는 기능 제공 범위 안에서만 허용된다. 이 판단은 정책을 현재 배포 구조에 적용한 구현상 추론이다. [데이터 정책](https://developers.google.com/photos/support/api-policy)

## 구현 순서

1. Google Cloud 프로젝트·OAuth client·consent screen·test user를 준비하고 local-only 환경 변수 계약을 문서화한다.
2. PKCE loopback OAuth adapter와 Keychain refresh token round-trip 테스트를 구현한다.
3. 실제 Picker REST adapter를 fake adapter와 같은 contract test에 연결한다. HTTP 응답 fixture로 create/poll/page/delete/error를 검증한다.
4. AppKit `Google Photos에서 선택` 카드, 고지·동의 sheet, browser open, polling progress, cancel·timeout 상태를 구현한다.
5. 선택한 항목을 bounded temporary content로 materialize하고 현재 분류 job에 전달하는 source bridge를 추가한다.
6. Google source policy를 job 생성 직전 다시 검증하고, 얼굴·인물·자동 export를 거부하는 E2E 테스트를 추가한다.
7. 실제 개인 Google 계정으로 1장, 10장, 취소, session timeout, 만료 URL, 앱 재시작 recovery를 순서대로 검증한다.
8. 최종으로 로컬 export 1회, 개인 데이터 정리·연결 해제, OAuth revoke 절차를 검증하고 보고서를 작성한다.

## 완료 조건

- user-initiated Picker 선택만 가능하며 전체 보관함 API 호출이 없다.
- OAuth token은 Keychain에만 저장되고 base URL·원본 경로는 영구 DB와 공개 로그에 없다.
- 사용자 고지와 동의가 OAuth·다운로드보다 먼저 표시된다.
- session create/poll/page/consume/delete와 cancel·timeout·앱 재시작 복구가 동작한다.
- MIME, byte 상한, 만료 URL, temporary cache cleanup이 검증된다.
- Google 입력에 얼굴 군집·인물 식별·인물 학습이 실행되지 않는다.
- 외부 VLM 전송은 별도 동의가 없으면 차단된다.
- 실제 계정으로 최소 1·10장 성공, 취소, 만료 오류, 연결 해제를 검증한다.
- 전체 pytest, 문서 검증, standalone bundle과 Google 실환경 보고서가 통과한다.

## 결정이 필요한 항목

1. **1차 분석 runtime**: Google 선택 사진을 Mac mini의 로컬 VLM만 사용해 처리할지, Linux VLM 전송을 별도 동의로 허용할지 결정해야 한다. 권장안은 1차 로컬 전용이다.
2. **Google 업로드**: 결과를 Google Photos에 새 앱 생성 앨범으로 올리는 기능은 Picker 읽기와 별도 OAuth scope·승인·영수증 기능이 필요하다. 1차에서는 제외를 권장한다.
3. **배포 범위**: 개인 단독 사용은 test user로 시작할 수 있지만, 외부 사용자에게 배포하면 OAuth verification과 정책 고지·삭제 지원을 먼저 완료해야 한다.
