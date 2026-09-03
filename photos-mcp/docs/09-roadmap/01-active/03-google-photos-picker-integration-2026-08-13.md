# Google Photos Picker 실연동과 원격 위임 계획

## 결론

Photos MCP에 Google Photos를 연결하는 범위는 **사용자가 Google Photos 화면에서 직접 고른 사진을 분류한 뒤, 사용자가 확정한 결과만 Photos MCP가 만든 새 앨범으로 업로드하는 기능**이다. 기존 Google Photos 항목은 읽기 전용이며 이동·수정·삭제하지 않는다. 최초 OAuth는 Mac에서 한 번 직접 승인하고, 이후 원격 LLM/nanobot 요청은 서명된 위임 profile 안에서 Picker link를 전달해 사용자가 편한 browser에서 사진을 선택한다. 전체 보관함·기존 앨범을 앱이 목록으로 탐색하는 기능은 포함하지 않는다.

2025-03-31 이후 Google은 기존 전체 보관함 조회 scope를 제거했고, 기존 보관함에서 사진을 고르는 용도에는 Picker API를 사용하도록 안내한다. Library API는 앱이 만든 사진·앨범 관리와 업로드 목적에 한정한다. [Google API 변경 안내](https://developers.google.com/photos/support/updates), [Picker 시작 안내](https://developers.google.com/photos/picker/guides/get-started-picker)

### 확정 운영 방향

| 경계 | 확정안 |
| --- | --- |
| 최초 Google OAuth | Mac Photos MCP 앱에서 사용자가 1회 직접 승인 |
| token 소유·갱신 | Mac Keychain만 보관·자동 갱신, LLM/Linux/원격 로그에는 노출하지 않음 |
| 원격 요청 검증 | 위임 profile에 묶인 nanobot/runtime identity의 signed 또는 mTLS 요청만 허용 |
| 일상 사진 선택 | Mac이 만든 `pickerUri`를 원격 LLM이 사용자에게 전달, 사용자는 **같은 Google 계정으로 로그인한** 원하는 browser에서 선택 |
| 다운로드와 분류 | Mac이 polling 후 자동 처리, browser 파일 다운로드 없음 |
| 결과 업로드 | 사용자가 결과 화면에서 명시적으로 승인한 사진만 새 `Photos MCP - ...` 앱 생성 앨범으로 업로드 |
| 기존 Google Photos 원본 | 수정·이동·삭제·기존 앨범 재구성 금지 |
| Linux VLM 전송 | 개인 단독 사용·신뢰된 동일 LAN 환경에서는 별도 작업별 동의 없이 허용. Google OAuth와 Picker의 사용자 선택은 계속 필수 |
| Chrome MCP | 개발 E2E 화면 확인만 사용, OAuth 허용·Picker 선택 자동 클릭에는 사용하지 않음 |

#### 2026-08-20 개인 설치 결정

현재 Photos MCP는 한 명의 사용자가 소유·관리하는 Mac과 동일 LAN의 Linux workstation만 사용하는 개인 설치다. 따라서 Google OAuth 승인과 Picker의 능동적인 사진 선택을 이번 작업의 동의 경계로 유지하고, 선택된 사진을 Linux Qwen3.8로 분석할 때 별도의 작업별 전송 동의 화면은 추가하지 않는다. 이 문서의 Touch ID/passkey 위임 profile과 원격 runtime별 고지 내용은 다중 사용자·외부 runtime 배포를 위한 후속 확장안이며, 현재 앱 범위에는 적용하지 않는다.

## 현재 상태

2026-08-14 기준 자동 구현과 계약 검증 상태는 다음과 같다.

| 영역 | 현재 구현 | 상태 |
| --- | --- | --- |
| source contract | `google_photos`는 대화형 Picker만 허용 | 완료 |
| OAuth 경계 | Picker 읽기 scope와 macOS Keychain 저장소 | 완료 |
| session lifecycle | create, poll, pagination, consume, timeout, cancel, cleanup fake·REST 계약 | 완료 |
| 콘텐츠 | 64MB 상한의 임시 파일 materialize와 사용 후 삭제 | 완료 |
| 개인정보 정책 | Google 입력에서 얼굴 품질·얼굴 군집 비활성화 | 완료 |
| Library write | 명시적 승인, album 생성, resumable upload, batchCreate, 영수증·부분 실패 | 자동 구현·계약 검증 완료 |
| 실제 OAuth adapter | PKCE, state/callback 검증, refresh, Keychain 저장, incremental scope | 자동 구현·계약 검증 완료 |
| 실제 Picker REST | `photospicker.googleapis.com` create/poll/page/delete와 오류 변환 | 자동 구현·fixture 검증 완료 |
| AppKit UX | 3단계 연결·선택·분류 창, 링크 열기·복사·취소·polling·분류 연결 | 자동 구현·단위 검증 완료 |
| 결과 업로드 UX | Google 입력 작업만 노출, 선택 수·앨범명·용량 고지와 승인 | 자동 구현·단위 검증 완료 |
| 원격 위임 | 요청 서명, Touch ID/passkey 위임 profile, picker link relay | 후속 별도 범위 |
| 실제 계정 E2E | 기존 Keychain OAuth 연결, Picker 선택, 자동 다운로드·분류, 실제 새 album 생성과 Google UI 확인 | 2026-08-20 기본 성공. 취소·만료·재연결·부분 실패는 추가 검증 필요 |

fake adapter는 빠른 회귀 검증용으로 유지하며, production runtime은 앱 설정 Keychain을 우선하고 기존 환경 변수는 fallback으로만 사용해 실제 REST adapter를 조립한다. 저장 DB와 cache는 각각 `0600`, `0700` 권한으로 제한한다. 실제 Google 계정 E2E는 사용자가 OAuth 동의와 Picker 선택을 직접 완료해야 하므로 자동 완료 범위에 포함하지 않는다. 설정 저장 구조와 callback 경계는 [OAuth 앱 설정 문서](05-google-photos-oauth-app-settings-2026-08-15.md)에 정리한다.

### 자동 구현 판정

- OAuth·Picker·다운로드·분류 bridge·결과 앨범 업로드 backend와 AppKit 진입 흐름은 구현됐다.
- token과 refresh token은 응답·로그에 노출하지 않고 Keychain repository를 통한다.
- Picker 원본은 작업에 귀속된 임시 lease로만 결과 업로드에 재사용하며, 만료·다른 작업 경로는 거부한다.
- 기존 Google Photos 원본과 기존 album을 수정하는 API 경로는 없다.
- 실계정의 기존 OAuth 연결, Picker 사진 선택, 자동 다운로드·분류, 새 album 표시 확인은 [2026-08-20 실계정 E2E 보고서](../../08-reports/01-validation/21-google-photos-real-account-e2e-2026-08-20.md)로 검증했다. 새 Keychain에서의 최초 동의와 취소·만료·재연결은 별도 예외 검증으로 남는다.

## 지원 범위와 제외 범위

### 1차 지원

1. 앱의 `사진 분류` 화면에서 `Google Photos에서 선택`을 누른다.
2. 앱 내부 고지와 사용자의 명시적 동의를 먼저 받는다.
3. macOS 기본 브라우저에서 Google OAuth와 `pickerUri`를 연다. `pickerUri`는 iframe에 열 수 없다.
4. Google Photos에서 사용자가 고른 사진만 session polling으로 가져온다.
5. 분석에 필요한 해상도만 임시 캐시에 내려받아 일반 분류 pipeline으로 전달한다.
6. 작업 완료·취소·실패 때 session과 임시 파일을 정리한다.
7. 사용자가 결과 갤러리에서 고른 사진만 `Google Photos 새 앨범으로 업로드`로 명시 승인할 수 있다.
8. 앱은 새 album을 만들고 원본 bytes를 upload한 뒤, 해당 앱 생성 album에 결과를 넣는다. 기존 Google Photos 원본은 변경하지 않는다.

Picker session은 생성, 사용자 선택, `mediaItemsSet` polling, 목록 조회, 정리 순서를 따른다. polling 간격과 timeout은 Google 응답의 `pollingConfig`를 사용하고, 선택 결과는 페이지 단위로 읽는다. [세션 관리](https://developers.google.com/photos/picker/guides/sessions), [선택 항목 조회](https://developers.google.com/photos/picker/guides/media-items)

### 명시적 제외

- Google Photos 전체 보관함/앨범 목록, 날짜 검색, 자동 전체 백업
- Google 입력 사진의 얼굴 군집, 인물 식별, 인물 구성 검토·학습
- Google Photos를 일반 목적 갤러리 또는 대규모 저장소/CDN으로 사용하는 기능
- 기존 Google Photos 사진을 다른 Google 앨범에 임의로 재구성하는 기능
- 기존 Google Photos 사진의 설명·태그·EXIF·인물·앨범을 자동 수정하는 기능
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
| 결과 확인 | 추천·검토 필요 결과와 `Google Photos 새 앨범으로 업로드` | 업로드 전 사진 수·예상 전송량·앨범 이름 확인 |
| 업로드 승인 | `선택한 N장 업로드` | append-only OAuth scope 추가 동의, album 생성, 재개 가능한 upload job 시작 |
| 종료 | 결과 또는 실패·정리 결과 | content와 session cleanup, 업로드 영수증 기록 |

동영상은 1차 화면에서 선택 수는 표시하되, 현재 정적 사진 분석이 지원하지 않는 형식이면 사진만 분류하거나 명확히 제외한다. 자동으로 동영상 frame을 추출하지 않는다.

### 결과 앨범 업로드의 정확한 의미

Google Photos Picker는 기존 사진을 선택해서 읽는 API이고, Library API의 쓰기 권한은 **Photos MCP가 새로 만든 미디어와 앨범**에만 적용된다. 따라서 선택한 기존 사진을 원래 Google Photos 항목 그대로 새 앨범에 "추가"하는 것은 지원하지 않는다. 결과 업로드는 다음과 같이 처리한다.

```text
Google Photos 기존 원본
  -> Picker에서 사용자 선택
  -> Photos MCP가 원본 품질 bytes를 임시 materialize
  -> 사용자가 결과 사진과 새 앨범 이름을 명시 승인
  -> Library API upload + batchCreate
  -> Photos MCP가 만든 새 album에 새 media item으로 저장
```

새 album의 기본 이름은 `Photos MCP - 2026-08-13 추천`처럼 날짜·사용자가 고른 목적을 사용한다. 자동 점수, 내부 파일명, 머신 생성 tag를 Google Photos의 description으로 올리지 않는다. Google은 description에 사용자가 만든 의미 있는 설명만 허용하며 자동 생성 tag·파일명·metadata를 넣지 않도록 안내한다. 분류 점수·사유·분석 JSON은 Photos MCP의 로컬 작업 기록에만 유지한다. [Google Photos 업로드 지침](https://developers.google.com/photos/library/guides/upload-media)

Picker의 `baseUrl=d` 다운로드는 위치 metadata를 제외한 EXIF를 유지한다. 따라서 새로 업로드한 사본의 GPS 위치가 유지되는지는 기대하지 않으며, 업로드 확인 sheet에 `새 사본에는 원본 위치 정보가 포함되지 않을 수 있음`을 명시한다. 같은 bytes를 다시 올릴 때 Google이 같은 media item으로 반환할 수는 있지만, 기존 원본과 새 결과가 어떤 방식으로 표시되는지에 의존하지 않는다. 동일 사진 재업로드 방지는 Photos MCP의 content fingerprint와 upload receipt로 처리하고, 실계정 E2E에서 album 포함 여부와 Google Photos UI의 중복 표시를 확인한다. [Picker 미디어 다운로드](https://developers.google.com/photos/picker/guides/media-items), [업로드 응답과 중복 bytes](https://developers.google.com/photos/library/guides/upload-media)

업로드는 저장 용량을 추가로 사용할 수 있다. Google은 API로 올린 원본 품질 파일이 사용자 Google 저장공간에 반영될 수 있음을 안내하므로, 실행 전 사진 수·합계 bytes·대략적인 용량 영향을 표시하고 사용자의 명시적 승인을 받는다. [Google Photos 업로드 저장공간](https://developers.google.com/photos/library/guides/upload-media)

## OAuth와 다운로드 자동화 범위

### 결론

**브라우저에서 사용자가 파일을 내려받는 단계는 만들지 않는다.** 브라우저는 최초 OAuth 동의와 Google Photos 안에서의 사진 선택에만 쓰고, 선택 완료 뒤의 실제 파일 전송은 Photos MCP가 OAuth Bearer token으로 백그라운드에서 수행한다.

다만 Google 계정 동의와 기존 보관함의 사진 선택을 무인으로 우회하거나 반복 실행하는 것은 기술적으로도 정책적으로도 지원 범위가 아니다. Picker API는 사용자가 현재 session에서 고른 항목만 앱에 전달하도록 설계되어 있고, 기존 전체 보관함을 계속 동기화할 권한은 제공하지 않는다. [권한 scope](https://developers.google.com/photos/overview/authorization), [Google API 변경 안내](https://developers.google.com/photos/support/updates)

| 단계 | 자동화 여부 | 권장 동작 |
| --- | --- | --- |
| 최초 계정 연결과 scope 동의 | 사용자 필수 | macOS `ASWebAuthenticationSession`으로 Google 로그인과 동의를 한 번만 진행한다. |
| 이후 access token 갱신 | 자동 | Keychain의 refresh token으로 access token을 조용히 갱신한다. |
| Picker session 생성·browser 열기 | 자동 시작 | 사용자가 `Google Photos에서 선택`을 누르면 앱이 session을 만들고 `pickerUri`를 연다. |
| 기존 Google Photos에서 항목 선택 | 세션별 필요 | 기본 공식 흐름에서는 사용자가 선택한다. 이 Mac의 전용 visible Chrome 보조 흐름은 날짜·개수 검증을 통과한 최근 사진을 선택하고 완료까지 자동화한다. 로그인·MFA·CAPTCHA가 나타나면 사용자에게 넘긴다. |
| 선택 완료 감지 | 자동 | Google이 제공한 polling interval과 timeout으로 session을 감시한다. |
| 이미지 다운로드 | 자동 | 앱이 `Authorization: Bearer` header로 `baseUrl`을 직접 요청하여 임시 캐시에 저장한다. 브라우저 다운로드 UI는 없다. |
| 분류·결과 표시·임시 파일 및 session 정리 | 자동 | bounded concurrency, 진행률, 오류 복구를 적용하고 완료·취소·실패 뒤 삭제한다. |

#### 2026-09-03 로컬 browser assistant 재검토

개인 Mac에서 사용자가 명시적으로 Remote Debugging을 허용한 현재 Chrome과 Chrome DevTools MCP를 사용하면 Picker 화면을 열고 최신순 개별 사진을 제한 수량만큼 미리 선택할 수 있다. 이는 OAuth 연결을 영구 권한으로 바꾸는 기능이 아니라, 매 Picker session 안에서 사용자 최종 확인 전 선택을 로컬 agent가 보조하는 별도 계층이다.

기본 자동화는 Chrome DevTools MCP가 Picker dialog의 접근성 역할과 label을 검증해 실행일 포함 최근 10일의 개별 사진을 선택한다. 기본 상한은 Picker session 한도와 같은 100장이며 날짜 그룹 checkbox는 제외한다. 검증을 통과하면 고유한 활성 완료 버튼까지 자동 클릭한다. 선택 완료 뒤 파일은 browser download가 아니라 기존 Picker REST adapter의 `mediaItems.list`와 `baseUrl`로 받는다.

완료 자동 클릭은 Google이 안정적인 DOM 계약으로 보증한 API가 아니므로 날짜 범위, 개별 사진 구조, 선택 수, 고유한 활성 완료 버튼을 모두 다시 확인한 경우에만 수행한다. Picker UI 변경, 계정 재인증, MFA, CAPTCHA 또는 모호한 버튼 상태에서는 클릭하지 않고 `AWAITING_USER_ACTION`으로 전환한다. 상세 경계와 acceptance gate는 [일일 사진 큐레이션 자동화 계획](06-daily-photo-curation-automation-2026-09-03.md#94-로컬-chromechrome-devtools-mcp-보조안)에 따른다.

운영 경로는 개인용 기본 Chrome에 `--auto-connect`하지 않는다. PhotosMcp가 `~/.photos-mcp/chrome/google-picker-profile`의 영구 전용 프로필을 일반 Chrome 프로세스로 먼저 실행하고, MCP는 `--browser-url=http://127.0.0.1:9333`으로 나중에 연결한다. 프로필 디렉터리는 `0700`으로 제한한다. 최초 Google 로그인·2단계 인증·OAuth 동의는 MCP를 연결하지 않은 전용 창에서 사용자가 직접 수행하고, account chooser·CAPTCHA·재인증에서는 자동화를 중지한다. 이 방식은 Chrome의 live-session 연결 승인 창과 WebDriver 상태의 Google 로그인 차단을 피하면서 개인용 Chrome 쿠키와 자동화 세션을 분리한다. [Chrome DevTools MCP 전용 프로필과 기존 세션 연결](https://github.com/ChromeDevTools/chrome-devtools-mcp/blob/main/docs/advanced-usage.md#connecting-to-a-running-chrome-instance)

자동화를 중지한 상태는 `AWAITING_USER_ACTION`으로 저장하고 Telegram private DM으로 알린다. raw `pickerUri`나 Google 계정·사진 정보를 메시지에 넣지 않고, opaque action request ID와 Tailscale-only action center의 짧은 1회용 링크만 제공한다. 계정 선택·MFA·OAuth 동의·CAPTCHA·Picker 최종 확인·UI 변경·session 만료를 구분하며, 알림 실패가 run 상태를 없애지 않게 한다. 상세 메시지·재개·quiet-hours 계약은 [일일 사진 큐레이션 자동화 계획](06-daily-photo-curation-automation-2026-09-03.md#95-사용자-액션-요청과-메시지-알림)에 따른다.

macOS에서는 `ASWebAuthenticationSession`이 기본 browser 또는 Safari에서 OAuth 인증을 수행한 뒤 callback을 호출하므로, 앱 내 임베디드 로그인 화면을 별도로 만들 필요가 없다. Picker URI는 보안상 iframe에 넣을 수 없으므로 기본 browser에서 연다. [Apple ASWebAuthenticationSession](https://developer.apple.com/documentation/authenticationservices/aswebauthenticationsession), [Picker session 참조](https://developers.google.com/photos/picker/reference/rest/v1/sessions)

### Chrome 로그인 세션과 AI 보조의 역할

Chrome에 이미 Google 로그인이 되어 있으면 OAuth browser가 같은 로그인 상태를 재사용해 계정·비밀번호 입력을 줄일 수 있다. 그러나 macOS 인증 session이 실제로 어느 browser profile을 쓰는지는 시스템 browser 설정과 browser 지원 여부에 따라 달라지므로, Chrome cookie를 읽거나 Chrome MCP의 로그인 상태를 앱의 인증 저장소로 취급하면 안 된다.

AI 또는 Chrome MCP는 개발·지원 중에 다음까지만 보조한다.

- 사용자가 앱에서 `Google Photos 연결`을 시작한 뒤 OAuth/Pickers 창을 여는 안내
- 이미 승인한 같은 Google Cloud 프로젝트·같은 scope의 재연결 상태 확인
- 선택 완료 후 앱의 자동 polling·다운로드·오류 진단

다음 행동은 AI가 대신 수행하지 않는다.

- Google 로그인 비밀번호·2단계 인증 입력
- 앱 데이터 고지 checkbox와 Google OAuth의 `허용` 버튼 클릭
- Picker에서 사진을 대신 고르거나 `완료`를 누르는 행동
- browser cookie, session token, refresh token을 추출·복사·공유하는 행동

Google Photos 정책은 수집 전에 화면 안의 고지와 사용자의 명시적 동의를 요구하며, 동의는 checkbox나 accept처럼 사용자의 능동 행동이어야 한다고 규정한다. OAuth 정책도 개발자가 제어하는 embedded user-agent와 session cookie 접근을 금지한다. 따라서 **Chrome MCP가 동의 화면을 자동 조작하는 방식은 채택하지 않는다.** [Google Photos 데이터 정책](https://developers.google.com/photos/support/api-policy), [Google OAuth 정책](https://developers.google.com/identity/protocols/oauth2/policies)

가장 짧은 정상 흐름은 다음과 같다: 앱의 연결 버튼 클릭 -> 이미 로그인된 browser에서 Google account 확인 -> 사용자가 최초 1회 `허용` -> 앱이 Keychain에 refresh token 저장 -> 이후 token 갱신·다운로드는 browser 없이 자동 처리. 이전에 같은 client ID와 같은 scope를 승인했다면 Google이 동의 화면을 다시 띄우지 않을 수 있지만, 이는 Google의 기존 승인 기록에 따른 결과이며 앱이나 AI가 동의를 우회하는 방식이 아니다.

### 원격 LLM 요청과 Picker link 전달

원격 LLM/nanobot은 Google credential을 소유하지 않는다. **Mac의 Photos MCP 앱만** OAuth를 완료하고 refresh token을 Keychain에 보관한다. 반면 Google Picker는 session이 만든 `pickerUri`를 클릭 가능한 link 또는 QR로 사용자에게 제시하도록 공식 지원하므로, 최초 연결이 끝난 뒤의 사진 선택은 사용자가 로그인한 휴대폰·외부 PC·Mac browser 어디에서든 할 수 있다. [Picker 시작 안내](https://developers.google.com/photos/picker/guides/get-started-picker)

```text
최초 한 번: 사용자 Mac
  Photos MCP 앱 -> 데이터 이용 고지 -> ASWebAuthenticationSession
  -> Google OAuth 직접 허용 -> PKCE callback -> refresh token을 Mac Keychain에 저장

일상 원격 요청: nanobot / 원격 LLM
  -> 상호 인증된 Photos MCP control API에 분류 요청
  -> Mac이 refresh token으로 access token 갱신, Picker session 생성
  -> 원격 LLM이 사용자에게 일회성 pickerUri 전달
  -> 사용자가 편한 browser의 Google Photos에서 사진 선택·완료
  -> Mac이 polling, 자동 다운로드, 분류, session/cache 정리
  -> 원격 LLM은 작업 결과와 상태만 수신
```

이 구조에서 원격에는 `pickerUri`, 작업 ID, 선택 완료 상태만 전달한다. `state`, PKCE verifier, authorization code, access token, refresh token, `baseUrl`은 Mac 밖으로 보내거나 원격 로그에 남기지 않는다. `pickerUri`도 민감한 임시 link로 취급해 Google session의 `pollingConfig.timeoutIn`과 작업·사용자 바인딩을 적용하고, 전달 뒤 로그를 마스킹한다.

Google의 Device Authorization Flow는 다른 장치에서 URL·코드를 입력하는 형태라 원격 동의에 적합해 보이지만, 현재 허용 scope가 OpenID, Drive, YouTube에 한정되어 **Photos Picker scope에는 사용할 수 없다.** Photos MCP에는 도입하지 않는다. [Google Device Authorization Flow](https://developers.google.com/identity/protocols/oauth2/limited-input-device)

#### 위임 profile과 강화 본인 인증

초기 Google 동의가 끝나면 Mac 앱은 Touch ID/passkey로 `이 Keychain credential의 Picker 선택·다운로드를 이 원격 Photos MCP 요청자에게 위임`하는 profile을 만든다. profile에는 Keychain credential 식별자 hash, OAuth client ID hash, 정확한 Picker scope, 허용된 nanobot/runtime 공개키 또는 mTLS identity, 허용 작업 유형, 생성·만료 시각을 저장한다. 계정 ID를 확인하려고 `email`·`profile` 같은 불필요한 scope를 추가 요청하지 않는다.

| 요청 종류 | 위임 profile 동작 | 추가 사용자 확인 |
| --- | --- | --- |
| Mac local VLM으로 사진 분류 | profile이 유효하면 자동 | 없음 |
| 원격 LLM이 picker link 전달 | profile이 유효하면 자동 | 사용자는 link에서 사진을 선택할 때만 행동 |
| Linux VLM으로 사진 bytes 전송 | 개인 단독 사용·신뢰된 동일 LAN의 현재 설치에서는 자동 | 다중 사용자·원격 runtime 전환 시 작업별 Touch ID/passkey와 전송 고지 |
| scope·계정 변경, 연결 해제, token 삭제 | 기본 차단 | Mac의 Touch ID/passkey와 명시적 확인 |

이 방식에서 본인 인증은 원격 요청의 의도를 강하게 증명하는 역할을 한다. 그러나 Chrome MCP/LLM이 Google OAuth의 `허용` 버튼을 대신 클릭하는 방식은 채택하지 않는다. OAuth 화면은 Google 계정·앱 identity·scope를 사용자가 확인하는 별도 보안 경계이고, agent-driven click은 Google이 Photos Picker 승인 방식으로 보증하지 않는다. [Google OAuth 정책](https://developers.google.com/identity/protocols/oauth2/policies), [Google Photos 데이터 정책](https://developers.google.com/photos/support/api-policy)

#### 사용자 경험

1. **최초 설정 한 번**: Mac 앱에서 `Google Photos 연결`을 누르고, 짧은 고지와 Google의 `허용`을 직접 완료한다. 이어 Touch ID/passkey로 원격 위임 profile을 만든다.
2. **일상 사용**: 원격 LLM에 `Google Photos에서 사진을 골라 분류해줘`라고 요청한다. 별도 Mac 버튼 없이 LLM이 받은 `사진 선택` link를 전달한다.
3. **사진 선택**: 사용자는 최초 연결한 **같은 Google 계정으로 로그인된** Google Photos browser에서 필요한 사진을 고르고 `완료`한다. 다른 계정이면 Google Picker가 session을 열지 못할 수 있으므로, 원격 UI는 `연결한 계정으로 선택하세요`를 명시한다. 파일을 browser로 저장하지 않는다.
4. **자동 처리**: Mac이 선택 완료를 감지하고 제한된 동시성으로 임시 cache에 내려받아 분류한다. 원격 LLM은 진행률과 결과만 표시한다.
5. **예외**: token 철회·6개월 미사용·계정/범위 변경은 위임 profile을 무효화한다. 이때만 Mac에서 최초 설정과 같은 짧은 재연결을 다시 진행한다.

이 흐름은 첫 설정 뒤에는 원격에서도 별도의 Mac 조작 없이 쓸 수 있으며, 사용자의 실제 행동은 Google Photos에서 어떤 사진을 공유할지 선택하는 일로만 제한된다.

#### 필요한 구성 요소

| 구성 요소 | 책임 |
| --- | --- |
| `GoogleOAuthSessionManager` | PKCE/state 생성, `ASWebAuthenticationSession`, callback code 교환, Keychain 저장·갱신 |
| `RemoteDelegationProfileService` | Touch ID/passkey로 profile 생성, 요청자 identity·scope·runtime binding, 무효화 |
| `RemotePickerRequestService` | 상호 인증된 원격 요청 검증, session 생성, one-time picker link 전달 상태 관리 |
| `GooglePhotosPickerAdapter` | session create/poll/page/delete, base URL materialize |
| `GooglePickerDownloadCoordinator` | 2~3개 동시 다운로드, byte/cache 상한, 진행률, 취소와 정리 |
| 원격 control API | `pending`, `selecting`, `downloading`, `completed`, `denied`, `expired` 상태와 비밀 없는 결과만 노출 |

### 자동화하지 않는 항목

- OAuth 동의 화면의 자동 승인, 저장된 비밀번호 입력, headless browser 로그인
- 사용자 선택 없이 기존 Google Photos 전체 보관함·앨범을 주기적으로 스캔하거나 내려받는 작업
- service account로 개인 Google Photos를 읽는 방식
- 만료된 `baseUrl`을 장기 파일 URL처럼 보관하거나 재사용하는 방식

Google Photos API는 service account를 지원하지 않고 모든 요청을 인증된 사용자 OAuth로 승인해야 한다. Picker의 `baseUrl`은 최대 60분만 유효하며 Bearer token을 요구한다. [권한 scope](https://developers.google.com/photos/overview/authorization), [선택 미디어 콘텐츠 규칙](https://developers.google.com/photos/picker/guides/media-items)

`60분`은 **OAuth 동의 또는 refresh token의 유효시간이 아니다.** 최초 승인이 유지되는 한 access token은 앱이 refresh token으로 자동 갱신한다. 60분 제한은 선택 결과에 포함된 일회성 `baseUrl`에만 적용되므로, 선택 완료를 감지한 즉시 앱이 임시 cache로 materialize하면 사용자가 1시간마다 재승인하거나 재다운로드할 이유가 없다. 다만 사용자가 새 사진 묶음을 추가로 가져오려는 경우에는 해당 묶음을 Picker에서 다시 선택해야 한다.

#### Refresh token 수명과 재연결

| OAuth 상태 | refresh token 기대 수명 | Photos MCP 동작 |
| --- | --- | --- |
| External + `Testing` | **동의 시점부터 7일** | 개발 검증 전용이다. 7일 뒤 `다시 연결`이 필요하다. |
| External + production publish | 고정 만료일 없음 | 정상적으로 사용 중이면 장기 유지할 수 있으나, 영구 보장은 없다. |
| 사용자가 권한 철회·보안 정책 변경·관리자 session control | 즉시 또는 정책 시점에 무효화 | 다음 token refresh의 `invalid_grant`를 감지해 안전하게 `다시 연결`만 표시한다. |
| 6개월 동안 refresh token 미사용 | 무효화 가능 | 실제 사용자 요청으로 다시 사용할 때 재연결한다. |

Google은 testing 상태의 external OAuth project에서 비기본 scope를 요청하면 refresh token을 7일로 제한한다. production publish 상태에서 발급한 refresh token은 일정한 만료일이 정해져 있지 않지만, 사용자가 연결을 해제하거나 6개월 미사용, token 발급 수 초과, 조직 정책 같은 조건에서 무효화될 수 있다. 따라서 갱신이 수명을 "영구 연장"한다기보다, **유효한 동안 새 access token을 자동 발급받는 방식**으로 이해해야 한다. [Google OAuth token 만료 조건](https://developers.google.com/identity/protocols/oauth2), [Google Cloud 앱 audience](https://support.google.com/cloud/answer/15549945)

권장 운영안은 개인용 OAuth client를 별도로 구성하고, `photospicker.mediaitems.readonly` 한 scope만 요청하는 것이다. 개발 E2E 동안에는 test user와 7일 refresh token 제한을 명확히 표시한다. 장기 운영 전에 Google Cloud의 audience·publishing 상태와 Google Photos/OAuth 정책 요건을 재확인해, testing 상태 제한을 벗어나는 구성을 확정한다. Photos MCP는 실제 사진 선택을 시작할 때만 token refresh를 시도하며, token을 살리기 위한 무의미한 주기 갱신은 하지 않는다. refresh 실패 시에만 현재 작업을 멈추고 `Google Photos 다시 연결` 버튼을 표시한다.

### 권장 구현안: 최초 직접 연결, 원격 Picker 선택, 자동 다운로드

1. 최초 한 번 사용자가 Mac 앱의 `Google Photos 연결`에서 짧은 고지와 OAuth Code + PKCE 동의를 직접 완료한다. refresh token은 Keychain에만 저장한다.
2. 이어 Touch ID/passkey로 원격 위임 profile을 만든다.
3. 일상적으로는 원격 LLM이 signed request를 보내고, Mac이 session을 만들며 LLM은 `pickerUri`를 사용자에게 전달한다.
4. 사용자는 원하는 browser의 Google Photos에서 필요한 사진을 선택한다.
5. Mac 앱은 `pollingConfig`에 맞춰 완료를 감지하고 전체 페이지를 읽는다.
6. 앱은 최대 2~3개 동시 요청으로 분석용 해상도부터 temporary cache에 스트리밍한다. 원본이 필요한 내보내기는 사용자가 명시적으로 선택한 경우에만 별도 요청한다.
7. 다운로드가 끝난 항목부터 기존 분류 job으로 전달하고 진행 수·실패 수·남은 임시 용량을 원격 LLM과 Mac 앱에 표시한다.
8. 모든 bytes 처리가 끝나면 session을 삭제하고 temporary cache를 정리한다. URL 만료·권한 철회는 다시 선택 또는 재연결을 안내한다.

이 방식은 사용자가 브라우저에서 파일을 하나씩 저장하는 과정을 제거하면서, Google이 요구하는 사용자 선택과 최소 권한 원칙은 유지한다. Picker session은 선택 완료 후 항목 bytes를 가져온 뒤 삭제하는 것이 권장된다. [세션 관리](https://developers.google.com/photos/picker/guides/sessions)

### 완전 자동 수집이 꼭 필요한 경우의 대안

Google Photos의 기존 개인 보관함을 무인 동기화하는 요구에는 Picker를 확장하면 안 된다. 요구가 "새 사진을 정기적으로 자동 분석"이라면 다음 중 하나를 별도 source로 선택한다.

| 대안 | 자동화 수준 | 적합한 용도 | Google Photos 기존 보관함 읽기 |
| --- | --- | --- | --- |
| Google Photos Picker | 선택 뒤 완전 자동 | 필요할 때 고른 사진의 분류 | 가능, 매 session 사용자 선택 필요 |
| 로컬/iCloud 사진 또는 로컬 폴더 | 완전 자동 가능 | 개인 사진의 주기적 분류 | 해당 없음 |
| Google Drive 또는 Cloud Storage 전용 업로드 폴더 | 완전 자동 가능 | 사용자가 별도 업로드한 분석 대기 폴더 | 불가, 별도 저장소 source |
| Google Photos Library API의 앱 생성 콘텐츠 | 제한 자동 | 앱이 직접 올린 사진·앨범 관리 | 불가, 앱 생성 항목만 가능 |

현재 Photos MCP의 목적에는 **Picker를 사용자 주도 가져오기**, 로컬 폴더·Apple 사진을 **반복 분석 source**로 유지하는 조합이 가장 안전하다. 현재 개인 단독 사용·동일 LAN 설치에서는 Google Photos 입력을 Linux Qwen3.8로 분석할 수 있으며, 별도 전송 고지는 다중 사용자 또는 원격 runtime 전환 시에만 다시 요구한다.

### 날짜 기반 자동 선택의 API 한계

기존 Google Photos 보관함에서 `오늘 촬영한 사진만`을 OAuth API로 직접 검색·다운로드하는 기능은 현재 Picker integration에 사용할 수 없다. 현재 Library API의 날짜 filter는 `photoslibrary.readonly.appcreateddata` scope로 읽을 수 있는 **앱 생성 콘텐츠**에만 적용된다. 과거 전체 보관함 읽기 scope를 전제로 한 date search 예제는 Photos MCP의 사용자 기존 사진에는 적용하지 않는다. [앱 생성 콘텐츠 목록](https://developers.google.com/photos/library/guides/list), [날짜 filter](https://developers.google.com/photos/library/guides/apply-filters)

Ambient API는 Google Photos의 선택된 media source를 ambient device(스마트 TV·photo frame)에 표시하기 위한 별도 API다. 사용자가 앨범·photo collection을 source로 고른 뒤 그 항목을 나열할 수 있지만, 날짜 search API가 아니며 사진 분류용 전체 보관함 source로 전용하지 않는다. [Ambient API 소개](https://developers.google.com/photos/ambient/guides/about), [Ambient device media source](https://developers.google.com/photos/ambient/reference/rest/v1/devices)

따라서 `오늘 사진 분류`는 다음 preset으로만 제공한다.

1. Mac이 Google Picker session을 만들고 원격 LLM이 picker link를 전달한다.
2. 사용자는 Google Photos에서 오늘 사진을 선택한다.
3. Mac은 선택 뒤 반환된 media item의 creation time을 오늘의 사용자 timezone 범위와 대조한다.
4. 범위를 벗어난 항목은 `제외됨`으로 표시하고, 오늘 사진만 다운로드·분류한다.

이 검증은 실수로 다른 날짜 사진을 선택했을 때의 안전장치이지, API가 사용자 보관함에서 오늘 사진을 자동으로 탐색하는 기능은 아니다.

## 기술 설계

### OAuth와 비밀 정보

- Google Cloud Console에서 **Google Photos Picker API**를 활성화하고 OAuth client ID를 생성한다.
- service account는 지원되지 않으므로 사용자 Google 계정 OAuth를 사용한다.
- 최초 연결에는 `photospicker.mediaitems.readonly`만 요청한다.
- refresh token과 account ID는 `GooglePickerCredentialRepository`를 통해 macOS Keychain에 저장한다.
- client ID는 설정 파일 또는 환경 변수에만 두고 repository·작업 로그에 넣지 않는다.
- OAuth는 Code + PKCE와 state를 검증한다. macOS 앱은 인증마다 `127.0.0.1` 임시 포트에 loopback callback을 열어 browser 승인 결과를 자동 수신한다. 커스텀 URI scheme과 수동 URL 복사·붙여넣기 방식은 사용하지 않는다.

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

- VLM 기반 장면 설명·일반 분류: 개인 단독 사용자가 신뢰된 동일 LAN의 Linux workstation을 운영하는 현재 배포에서는 별도 작업별 동의 없이 허용한다. Google OAuth와 Picker에서 사용자가 직접 사진을 선택하는 동의 경계는 유지한다.
- 로컬 기술 점수·중복 억제: 원본 또는 thumbnail을 처리하므로 정책·동의 검토 뒤 1차에서는 비활성화하고, 일반 VLM 분석만 허용하는 보수적 시작을 권장한다.
- 인물 분류와 선호 학습: 금지.
- 결과 내보내기: 로컬 디렉터리만 1차 허용한다. Google 재업로드는 별도 승인 범위로 분리한다.

현재 설치는 한 명의 사용자가 Mac과 동일 네트워크의 Linux workstation을 직접 관리하는 개인 전용 환경이다. 따라서 OAuth 승인과 Picker의 능동 선택으로 이번 분류 사용 목적을 확인하고, 별도의 Linux 전송 확인 sheet는 표시하지 않는다. 이 예외는 개인 설치에만 적용한다. 다중 사용자 배포, 원격 네트워크 전송 또는 관리 주체가 다른 runtime을 지원할 때는 전송 대상·처리 목적·보관 기간을 보이는 작업별 고지를 다시 도입한다. [데이터 정책](https://developers.google.com/photos/support/api-policy)

## 구현 순서

1. Google Cloud 프로젝트·OAuth client·consent screen·test user 준비: **사용자 외부 설정 대기**.
2. PKCE OAuth adapter와 Keychain refresh token round-trip: **완료**.
3. 원격 signed/mTLS 위임 profile: **후속 별도 범위**.
4. 실제 Picker REST adapter와 fixture contract test: **완료**.
5. AppKit 연결·링크·진행률·취소·timeout 상태: **완료**. 원격 relay는 후속 범위다.
6. bounded temporary content와 분류 source bridge: **완료**.
7. 결과 album upload 확인, 용량 고지와 receipt: **완료**.
8. incremental append-only OAuth, resumable upload, batchCreate와 부분 실패 backend: **완료**.
9. Google source 정책 재검증과 얼굴·인물 기능 차단: **완료**.
10. 실제 계정 Picker·자동 다운로드·분류·새 album 업로드: **기본 성공**. 1장/10장, 취소·만료·재연결은 추가 검증 대기.
11. 실제 새 album 생성·원본 비변경 확인: **기본 성공**. 중복·부분 실패·복구는 추가 검증 대기.

## 완료 조건

- user-initiated Picker 선택만 가능하며 전체 보관함 API 호출이 없다.
- OAuth token은 Keychain에만 저장되고 base URL·원본 경로는 영구 DB와 공개 로그에 없다.
- 사용자 고지와 동의가 OAuth·다운로드보다 먼저 표시된다.
- session create/poll/page/consume/delete와 cancel·timeout·앱 재시작 복구가 동작한다.
- MIME, byte 상한, 만료 URL, temporary cache cleanup이 검증된다.
- Google 입력에 얼굴 군집·인물 식별·인물 학습이 실행되지 않는다.
- 사용자가 고른 결과만 명시 승인 뒤 새 app-created album에 업로드되며, 기존 Google Photos 원본과 기존 album은 변경되지 않는다.
- 업로드 scope는 `photoslibrary.appendonly`을 필요 시점에만 추가 요청하고, 업로드 영수증·재시도·부분 실패·취소가 복구된다.
- 결과 album에는 자동 생성 점수·tag·내부 metadata를 description으로 쓰지 않고, 위치 metadata가 사본에서 빠질 수 있음을 사전 고지한다.
- 개인 단독 사용·신뢰된 동일 LAN의 Linux VLM 전송은 OAuth와 Picker 선택 뒤 허용되며, 다중 사용자 또는 원격 runtime 배포에서는 작업별 고지·동의를 다시 요구한다.
- 원격 요청은 위임 profile의 요청자 identity·scope·runtime 검증을 통과해야 하며, OAuth credential과 base URL은 원격에 노출되지 않는다.
- 실제 계정으로 최소 1·10장 성공, 취소, 만료 오류, 연결 해제를 검증한다.
- 최초 연결 뒤 원격 browser에서 picker link로 사진을 선택해도 Mac이 자동 다운로드·분류하는 것을 검증한다.
- 실제 계정에서 최초 동의 뒤 refresh token으로 재연결하고, browser 파일 다운로드 없이 선택 항목이 temporary cache까지 자동 materialize되는 것을 검증한다.
- 전체 pytest, 문서 검증, standalone bundle과 Google 실환경 보고서가 통과한다.

## 결정이 필요한 항목

1. **1차 분석 runtime**: 개인 단독 사용·동일 LAN 설치에서는 Linux Qwen3.8을 기본 분석 runtime으로 사용한다. 다중 사용자 또는 원격 runtime 배포로 전환할 때에만 별도 전송 동의 UX를 재검토한다.
2. **결과 album 이름**: 기본 이름은 날짜 기반 `Photos MCP - 추천`으로 두고, 업로드 sheet에서 사용자가 매번 변경할 수 있게 한다.
3. **업로드 대상**: 기본은 추천·사용자 선택 사진만으로 하며, 검토 필요 결과를 올릴 때는 별도 checkbox로 명시 선택하게 한다.
4. **배포 범위**: 개인 단독 사용은 test user로 시작할 수 있지만, 외부 사용자에게 배포하면 OAuth verification과 정책 고지·삭제 지원을 먼저 완료해야 한다.
