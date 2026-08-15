# Google Photos OAuth 앱 설정

## 목적

Google Photos 연결에 필요한 OAuth client 값을 shell 환경 변수에 의존하지 않고 PhotosMcp 앱에서 직접 입력·관리한다. Finder, Dock, Spotlight로 실행한 macOS 앱은 사용자의 터미널 환경 변수를 상속하지 않으므로, 기존 방식은 일반 사용자가 연결을 시작하기 어려웠다.

## 사용자 흐름

1. `사진 분류`에서 `Google Photos`를 선택한다.
2. 연결 창의 `OAuth 설정 열기`를 누른다.
3. `Google Cloud 열기`로 Console의 OAuth client 화면을 열거나, 이미 발급받은 값을 입력한다.
4. `Desktop app` 유형의 `OAuth Client ID`와 선택적 `Client secret`을 입력하고 `저장 후 연결`을 누른다.
5. 앱은 `127.0.0.1`의 임시 포트에 loopback callback 수신기를 열고, 사용자는 기본 브라우저에서 Google OAuth 동의를 직접 완료한다.
6. 승인 뒤 browser는 앱의 임시 loopback 주소로 자동 복귀한다. 앱은 PKCE `state`를 검증하고 refresh token을 Keychain에 저장한다.
7. Google Photos Picker에서 사진을 선택하면 앱이 polling·임시 다운로드·분류를 자동으로 진행한다.

## 저장과 보안

| 값 | 저장 위치 | 화면 동작 |
| --- | --- | --- |
| OAuth Client ID | macOS Keychain | 설정 창에서 다시 표시 가능 |
| Redirect URI | 앱 실행 중 메모리 | `http://127.0.0.1:<임시 포트>/oauth/google`으로 인증마다 새로 생성 |
| Client secret | macOS Keychain | 입력 뒤 다시 표시하지 않음. 빈 값으로 저장하면 기존 값을 유지 |
| refresh token과 scope | 별도 macOS Keychain 항목 | 설정 창·로그·원격 요청에 노출하지 않음 |

설정은 `photos-mcp.google-photos-oauth-settings` Keychain service에, refresh token은 기존 `photos-mcp.google-photos-picker` service에 분리한다. repository, 작업 DB, 진단 로그, 결과 JSON에는 client secret·authorization code·access token·refresh token을 기록하지 않는다.

## 우선순위와 변경 규칙

1. 앱에서 Keychain에 저장한 유효한 설정을 우선 사용한다.
2. 앱 설정이 없거나 Keychain을 읽을 수 없을 때만 기존 `PHOTOS_MCP_GOOGLE_CLIENT_ID`, `PHOTOS_MCP_GOOGLE_CLIENT_SECRET` 환경 변수를 fallback으로 사용한다. 과거 `PHOTOS_MCP_GOOGLE_REDIRECT_URI` 값은 loopback 흐름에서는 사용하지 않는다.
3. Client ID 또는 Client secret이 바뀌면 기존 refresh token을 삭제하고 최초 연결을 다시 요구한다. OAuth token은 발급 client에 묶여 있으므로 다른 client 설정으로 재사용하지 않는다.
4. 저장 뒤 앱 자체를 종료하거나 재시작할 필요는 없다. Google Photos runtime과 연결 창이 즉시 새 설정을 사용한다.

## Google Cloud 준비

Google Cloud Console에서 다음을 준비한다.

1. 프로젝트를 만들고 Google Photos Picker API를 활성화한다.
2. 결과를 새 Google Photos 앨범으로 업로드할 예정이면 Google Photos Library API도 활성화한다.
3. OAuth 동의 화면과 test user를 설정한다.
4. OAuth client를 `Desktop app` 유형으로 만든다. 앱은 인증마다 `http://127.0.0.1:<임시 포트>/oauth/google` callback을 자동으로 사용하므로 Redirect URI를 수동 입력하거나 Web application client를 만들지 않는다.

최초 사진 선택에는 `photospicker.mediaitems.readonly`만 요청한다. 결과를 새 album으로 업로드할 때에만 `photoslibrary.appendonly`을 incremental authorization으로 추가 요청한다. 기존 Google Photos 원본이나 기존 album을 조회·수정·삭제하지 않는다.

## Loopback callback

Google OAuth 정책상 커스텀 scheme (`photos-mcp://...`)과 수동 callback URL 복사·붙여넣기(OOB)는 사용하지 않는다. 인증 시작 시 앱이 `127.0.0.1`에 임시 포트를 할당하고 `/oauth/google` 경로만 수신한다. callback에는 `code` 또는 `error`가 있어야 하며, 수신기는 외부 인터페이스에 열리지 않고 authorization code를 로그에 기록하지 않는다.

앱은 callback URL의 scheme·host·port·path, OAuth `state`, PKCE verifier를 검증한 뒤에만 token 교환을 허용한다. 승인 대기 시간은 최대 5분이며 창을 닫거나 설정을 바꾸면 listener를 즉시 닫는다.

## 검증

- Keychain 저장소가 client 설정과 refresh token을 분리하는지 자동 테스트한다.
- 앱 설정이 환경 변수보다 우선하는지 자동 테스트한다.
- loopback receiver가 임시 `127.0.0.1` 포트에서 callback을 받고, 잘못된 경로는 거부하며 authorization code를 로그에 남기지 않는지 자동 테스트한다.
- 설정 창에서 secret을 다시 표시하지 않고, Redirect URI 수동 입력 없이 `저장`, `저장 후 연결`, Google Cloud Console 진입 버튼을 제공하는지 AppKit 테스트한다.
- 저장 후 기존 Google Photos 창이 새 runtime을 반영하는지 controller 테스트한다.
- 실제 계정 검증은 Picker API 연결 1장 선택부터 시작하고, 기존 원본 비변경과 새 album 업로드를 별도로 확인한다.

## 참고

- [Google Photos 앱 설정](https://developers.google.com/photos/overview/configure-your-app)
- [Google Photos 권한 scope](https://developers.google.com/photos/overview/authorization)
- [Google Photos Picker 세션](https://developers.google.com/photos/picker/guides/sessions)
- [Google 데스크톱 OAuth loopback callback](https://developers.google.com/identity/protocols/oauth2/native-app)
