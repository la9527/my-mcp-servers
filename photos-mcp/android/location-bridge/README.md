# PhotosMcp 앨범

기존 Location Bridge의 안전한 GPS 동기화를 유지하면서 PhotosMcp의 통합 작업 상태, 추천 결과와 Story를 보는 개인용 Android companion 앱이다. package ID는 `com.photosmcp.locationbridge`를 그대로 사용하므로 기존 Keystore key·등록·checkpoint·암호화 outbox를 보존한다.

## 화면과 연결 경계

- 홈: Mac 상태, 최신 Apple·Google 통합 run, 분석·추천 수, 최신 이야기
- 작업: 최대 1,000장·6시간 정책, 완료·부분 완료·남은 사진
- 추천: Apple·Google을 합친 실제 추천 사진 grid, 위치 근거 상태와 큰 사진 preview
- 이야기: 날짜·위치 chapter, grid와 확대·pan·좌우 fling이 가능한 큰 사진 viewer
- 설정: GPS Bridge 상태, 마지막 전송, 수동 위치 동기화와 Android 권한

상단에는 알림과 설정을 아이콘 action으로 두고, 하단에는 `홈 · 작업 · 추천 · 이야기`를 아이콘과 label이 함께 있는 4개 고정 목적지로 둔다. 디자인 계약은 [Android Companion 디자인 시스템](../../docs/07-design-system/06-android-companion.md)을 따른다.

추천과 이야기의 한 장 보기에서는 `− · 현재 배율 · +` 버튼, 두 번 탭 2.5×, 두 손가락 1×~4× 확대·축소와 확대 상태 pan을 제공한다. 기본 1×에서만 좌우 fling이 이전·다음 사진으로 전환되므로 확대 사진을 움직이는 동작과 페이지 이동이 충돌하지 않는다.

개인 결과 조회는 휴대폰 Tailscale이 켜져 있을 때만 다음 사설 주소를 사용한다.

```text
https://byoungyoung-macmini.tail53bcc7.ts.net/mobile-client
```

GPS upload는 휴대폰을 들고 외부에 있는 경우에도 기존 공개 write-only 주소로 독립 재시도한다. Tailscale이 꺼졌다는 이유로 GPS outbox가 손실되거나 조회 API가 공개로 전환되지 않는다.

## 개인정보·권한 경계

- 읽는 범위: 최근 10일 `DCIM/Camera`, 최초 최대 1,000장, 이후 증분
- 전송: GPS, 촬영 시각, 크기·MIME, 비식별 asset key와 content digest
- 전송하지 않음: 사진 bytes, filename, MediaStore ID
- 권한: 사진 읽기, 원본 EXIF 위치, 인터넷과 네트워크 상태
- 요청하지 않음: 현재 위치, 백그라운드 위치, 전체 파일 접근
- 보관: Android Keystore AES-GCM offline outbox; 서버 ack 뒤 삭제
- 인증: 기기별 non-exportable `K_ingest` P-256 key로 모든 GPS batch 서명
- 소유자 조회: 별도 non-exportable `K_owner`, Tailscale owner identity와 10분 session
- WebView: 45초 일회용 exchange, 30분 HttpOnly cookie, 고정 Story origin, 원본 대신 EXIF 제거 파생본
- bearer: disk나 SharedPreferences에 저장하지 않고 process memory에만 보유

## 빌드

Android SDK 36과 JDK 21을 사용한다.

```bash
export JAVA_HOME=/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home
export ANDROID_HOME=/opt/homebrew/share/android-commandlinetools
export ANDROID_SDK_ROOT="$ANDROID_HOME"
./gradlew clean assembleDebug lintDebug
```

개발 APK(`PhotosMcp 앨범 0.5.0`):

```text
app/build/outputs/apk/debug/app-debug.apk
```

debug source set에는 ADB smoke test용 명시적 receiver가 있지만 release source set에는 포함되지 않는다. 장기 운영 전에는 개인 release keystore로 서명한 non-debuggable APK를 사용한다. keystore와 암호는 저장소에 넣지 않는다.

개인 베타 배포본은 R8 release를 빌드·정렬·서명·검증한 뒤 Tailnet 전용 다운로드 디렉터리에 원자적으로 게시한다. 현재 debug 서명 설치본을 데이터 삭제 없이 갱신할 수 있도록 개인 베타에서는 기존 Android debug 인증서를 이어 쓴다. 장기 운영용 고유 keystore로 바꾸려면 기존 앱과 서명이 달라 in-place update가 불가능하므로 별도 migration 계획이 필요하다.

```bash
.venv/bin/python scripts/build_android_distribution.py --debug-signing
```

생성물과 설치 페이지:

```text
~/.photos-mcp/runtime/mobile-client/downloads/PhotosMcp-Album.apk
https://byoungyoung-macmini.tail53bcc7.ts.net/mobile-client/download
```

설치 페이지와 APK는 승인된 Tailscale owner에게만 제공되며 공개 Funnel 8443에는 route가 없다. APK는 `android:debuggable=false`인 release build이고 checksum은 같은 디렉터리의 `.sha256` 파일과 설치 페이지에 표시된다.

Android에서는 Telegram·Codex 같은 앱 안의 브라우저가 APK 다운로드를 차단하거나 다운로드 관리자에 넘기지 못할 수 있다. 이 경우 Tailscale을 연결한 상태로 설치 페이지 메뉴에서 **Chrome으로 열기**를 선택한다. 설치 페이지의 링크는 `download` 파일명, Android package MIME, 버전 query를 사용하며 서버 응답은 attachment filename과 byte range를 제공한다.

직접 다운로드 주소:

```text
https://byoungyoung-macmini.tail53bcc7.ts.net/mobile-client/download/PhotosMcp-Album.apk?v=0.5.0
```

페이지도 열리지 않으면 Tailscale 연결과 로그인 계정부터 확인한다. 페이지는 열리지만 버튼만 반응하지 않으면 Chrome에서 직접 열고, 다운로드는 됐지만 설치되지 않으면 Android의 **이 출처의 앱 설치 허용**과 기존 설치본의 서명 일치 여부를 확인한다.

## ADB 설치와 자동 등록

개발용 debug APK는 연결된 단말이 한 대일 때 빌드·덮어쓰기 설치·권한 부여·최초 등록을 한 명령으로 처리한다.

```bash
.venv/bin/python scripts/install_connected_android_bridge.py
```

기존 등록이 있으면 key와 checkpoint를 유지하고 앱만 갱신한다. 신규 설치이거나 앱 데이터가 초기화된 경우에는 Mac에서 10분·1회용 token을 메모리로 만들고 ADB intent로 전달해 자동 등록한다. token과 등록 JSON은 콘솔에 출력하지 않는다. 등록된 앱 화면에서는 수동 JSON 입력 영역을 숨긴다.

이미 빌드한 APK만 다시 설치할 때는 다음을 사용한다.

```bash
.venv/bin/python scripts/install_connected_android_bridge.py --no-build
```

## 수동 최초 등록 fallback

Mac에서 10분·1회용 등록 정보를 만든다.

```bash
photos-mcp-mobile-location-admin create-enrollment
```

ADB를 사용할 수 없는 신규 단말에서만 앱을 열어 JSON을 붙여넣고 등록한다. 앱은 private signing key를 Android Keystore 밖으로 내보내지 않으며, 서버는 공개키만 저장한다. 등록 token은 소비 즉시 재사용할 수 없다.

개발 중 USB 연결 단말에는 token을 콘솔에 출력하지 않는 helper를 사용할 수 있다.

```bash
.venv/bin/python scripts/enroll_connected_android_bridge.py
```

USB와 ADB는 최초 개발 검증 수단일 뿐 운영 동기화의 전제 조건이 아니다.

## 운영

등록 뒤 설정의 `최근 사진 위치 지금 동기화`를 누르면 필요한 사진·원본 위치 권한을 그때 요청하고 즉시 증분 scan을 실행한다. 앱 시작만으로 media 권한을 요구하지 않는다. 앱은 24시간 persisted `JobScheduler`도 등록하므로 휴대폰과 Mac이 서로 떨어져 있어도 일반 인터넷으로 다음 주소에 재시도한다.

```text
https://byoungyoung-macmini.tail53bcc7.ts.net:8443/mobile-location
```

Mac 또는 Funnel이 꺼져 있으면 encrypted outbox가 유지된다. 성공 응답을 받기 전에는 항목을 삭제하지 않으며 다음 예약 또는 수동 실행에서 같은 idempotency key로 재전송한다.

서버 쪽 상세 구현·보안과 실기기 결과는 [Android GPS 공개 수신과 실기기 E2E 검증](../../docs/08-reports/01-validation/29-mobile-location-public-ingest-and-android-bridge-2026-09-07.md)을 참고한다.

Companion 조회 계층, 인증 흐름과 최초 실기기 검증은 [Android Companion read-only vertical slice 검증](../../docs/08-reports/01-validation/30-android-companion-readonly-vertical-slice-2026-09-08.md)을, 실제 추천 썸네일과 Tailnet APK 배포 검증은 [Android 추천 사진 grid와 APK 배포 검증](../../docs/08-reports/01-validation/31-android-native-recommendation-gallery-and-apk-delivery-2026-09-08.md)을, 확대·pan·fling 개선은 [사진 뷰어 확대·플리킹 검증](../../docs/08-reports/01-validation/32-photo-viewer-zoom-pan-fling-2026-09-08.md)을 참고한다.
