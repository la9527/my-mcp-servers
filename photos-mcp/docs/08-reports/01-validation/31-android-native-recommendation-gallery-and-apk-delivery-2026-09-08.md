# Android 추천 사진 grid와 APK 배포 검증

## 결과

`PhotosMcp 앨범` 0.3.0에 Apple Photos와 Google Photos의 통합 추천 결과를 실제 사진 썸네일로 보는 네이티브 grid와 큰 사진 preview를 구현했다. 앱에 원본 접근 URL이나 장기 secret을 추가하지 않고, 현재 추천 원장에 속한 EXIF 제거 파생본만 Tailnet owner identity와 짧은 기기 session을 모두 통과한 단말에 제공한다.

R8로 축소한 non-debuggable release APK를 기존 개발 설치와 같은 인증서로 서명해 in-place update가 가능한 개인 베타 파일로 만들었다. 설치 페이지, APK와 checksum은 Tailscale 443의 owner 전용 `/mobile-client/download`에만 제공하며 공개 Funnel 8443에서는 404임을 확인했다.

## 사용자 화면

추천 화면은 다음 규칙을 따른다.

- 세로 화면은 2열, 넓은 화면은 최대 4열의 정사각형 실제 사진 grid
- 각 tile에 추천 thumb, 읽기 쉬운 하단 gradient, 추천 제목, 날짜·도시 요약
- 48dp 이상 선택 영역, ripple, TalkBack용 추천 요약과 `크게 보기` 설명
- 사진별 독립 loading/error 상태와 최대 4개 동시 image 작업
- tile 선택 시 검은 배경의 EXIF 제거 preview, 현재 번호, 전체 설명, 이전·다음·닫기
- 큰 사진 dialog를 먼저 닫는 Android back 처리

서버가 반환하는 현재 결과 window는 48장이다. 분석 정책의 최대 1,000장과 추천 화면에서 동시에 만드는 view 수는 다른 개념이다. 추천 결과가 48장을 넘을 때의 연속 pagination과 RecyclerView/Compose lazy grid 기반 가상화는 후속 성능 gate로 유지한다.

## 이미지·캐시 경계

### 서버

추가 route:

```text
GET /mobile-client/v1/results/assets/{asset_id}/thumb
GET /mobile-client/v1/results/assets/{asset_id}/preview
```

- Tailscale owner identity와 `derivative:read` bearer를 모두 요구한다.
- 현재 `current_mobile_story()`의 추천 photo 목록에 포함된 공개 asset ID만 허용한다.
- `thumb`, `preview` 외 kind는 404이고 `original` route는 없다.
- `ShareImageService`가 만든 orientation 보정·sRGB·EXIF/GPS 제거 JPEG만 반환한다.
- `Cache-Control: no-store, private`, `nosniff`, frame/search 차단 header를 적용한다.

### Android

- access bearer는 process memory에만 있고 image cache key나 파일명에 넣지 않는다.
- cache 파일은 app-private `cacheDir/recommendation-images-v1` 아래에만 둔다.
- SHA-256 cache key를 써서 server asset ID를 파일명으로 노출하지 않는다.
- 24MiB memory LRU, 128MiB disk 상한과 오래된 파일 우선 삭제를 적용한다.
- 각 응답은 JPEG MIME과 최대 10MiB를 검사하며 HTTP cache는 사용하지 않는다.
- 화면 크기에 맞춰 sampled `RGB_565` bitmap으로 decode한다.

## APK 전달

### 빌드

```bash
.venv/bin/python scripts/build_android_distribution.py --debug-signing
```

스크립트는 다음을 한 번에 수행한다.

1. `clean assembleRelease lintRelease`
2. release metadata가 versionName `0.3.0`인지 확인
3. `zipalign -f 4`
4. 기존 `~/.android/debug.keystore`로 개인 베타 서명
5. APK Signature Scheme v2/v3 검증
6. private runtime 디렉터리로 원자적 게시하고 SHA-256 생성

현재 debug 인증서를 사용하는 이유는 단 하나다. 이미 설치된 개인 개발 앱과 인증서를 맞춰 앱 데이터, 기기 등록, GPS checkpoint와 암호화 outbox를 지우지 않고 업데이트하기 위해서다. APK 자체는 release variant이고 `android:debuggable` attribute가 없으며 release source에는 ADB 자동 등록 receiver가 포함되지 않는다. 장기 고유 release keystore로 전환하려면 Android가 다른 앱 서명으로 판단하므로 데이터 migration 또는 새 package 전략을 먼저 설계해야 한다.

생성 파일:

```text
/Users/byoungyoungla/.photos-mcp/runtime/mobile-client/downloads/PhotosMcp-Album.apk
/Users/byoungyoungla/.photos-mcp/runtime/mobile-client/downloads/PhotosMcp-Album.apk.sha256
```

설치 주소:

```text
https://byoungyoung-macmini.tail53bcc7.ts.net/mobile-client/download
https://byoungyoung-macmini.tail53bcc7.ts.net/mobile-client/download/PhotosMcp-Album.apk
```

두 주소는 휴대폰에서 Tailscale을 켜고 승인된 owner 계정으로 연결했을 때만 열린다. Android가 브라우저 설치 권한을 묻는 경우 현재 다운로드에 사용한 브라우저의 `이 출처의 앱 설치`를 한 번 허용하고 `업데이트`를 선택한다. 앱을 삭제한 뒤 새로 설치하지 않아야 기존 등록 정보가 보존된다.

## 자동·운영 검증

### Python 전체 회귀

```text
.venv/bin/pytest -q
817 passed in 9.90s
```

추가 계약 검증은 다음을 포함한다.

- 유효한 owner session의 현재 추천 thumb는 `200 image/jpeg`
- bearer가 없으면 401
- 현재 추천에 없는 asset은 404
- `original` kind는 404
- Tailnet identity가 없는 APK 설치 페이지는 403
- 설치 페이지, APK, checksum은 owner test client에서 200
- 공개 mobile-location 애플리케이션의 `/mobile-client/download`는 404

### Android build·lint

```text
assembleDebug lintDebug       BUILD SUCCESSFUL
assembleRelease lintRelease   BUILD SUCCESSFUL
```

- package: `com.photosmcp.locationbridge`
- versionCode: `3`
- versionName: `0.3.0`
- label: `PhotosMcp 앨범`
- minSdk: 26
- targetSdk: 36
- cleartext traffic: false
- release manifest: debuggable 없음
- zipalign 4-byte: 성공
- APK v2 signature: 성공
- APK v3 signature: 성공
- signer SHA-256: 기존 debug APK와 동일
- APK 크기: 약 90KiB
- APK SHA-256: `2837936053bc4e063b96ccf06f391e2897cec52718a0db531b753fb815d24eba`

### Tailnet·공개 경계

| Probe | 결과 |
|---|---:|
| direct loopback 설치 페이지, identity 없음 | 403 |
| direct loopback 설치 페이지, owner identity | 200 |
| direct loopback APK, owner identity | 200 |
| Tailnet 443 설치 페이지 | 200 |
| Tailnet 443 APK | 200 |
| Tailnet에서 받은 APK SHA-256 | 게시 파일과 일치 |
| public Funnel 8443 `/mobile-client/download` | 404 |

`tailscale serve status --json`에서 `AllowFunnel`은 계속 8443 하나뿐이다. 443의 `/mobile-client`는 Tailnet only이며 Open WebUI `/`, `/photos`, `/photos-actions`, `/story-assets` 기존 route를 보존한다. mobile client LaunchAgent는 `127.0.0.1:18794`에서 실행 중이다.

## 이번에 확인하지 못한 항목

0.3.0 APK 생성 시점에는 Android 단말이 ADB 목록에서 분리돼 새 추천 grid를 실제 기기에 다시 설치하거나 스크린샷으로 시각 회귀하지 못했다. 연결돼 있던 0.2.0에서 package update 보존, 여섯 화면, Story 실제 이미지, light/dark, 130% 글자, landscape와 GPS 동기화는 이미 검증됐지만 이를 0.3.0 grid 검증으로 대신 주장하지 않는다.

사용자는 Tailnet 설치 링크로 0.3.0을 직접 업데이트할 수 있다. 다음 단말 연결 때는 앱 데이터를 삭제하지 않은 상태에서 아래 항목을 추가 확인한다.

1. 추천 화면에서 실제 thumb가 2열로 표시되는지
2. 선택한 사진의 preview와 이전·다음·닫기가 동작하는지
3. 느린 네트워크와 일부 image 실패가 전체 grid를 막지 않는지
4. update 뒤 pairing, 권한, checkpoint, encrypted outbox와 JobScheduler가 유지되는지
5. 앱 process log에 fatal, TLS, DNS 오류가 없는지

## 남은 제품 gate

- 48장 초과 결과의 cursor pagination과 RecyclerView/Compose lazy grid
- 0·1·100·1,000장 fixture 및 저메모리 실기기 계측
- cache TTL, owner device revoke/logout 시 cache purge
- 0.3.0 Android 실기기 시각·접근성 회귀
- 장기 고유 release keystore와 기존 개인 베타 데이터 migration 결정
- generic push, 제한된 실행·공유 제어, encrypted offline Story는 기존 로드맵 Phase 4~6에서 계속 관리

현재 APK는 사용자가 바로 설치할 수 있는 Tailnet 전용 개인 베타로 완료했다. 위 후속 gate를 통과하기 전 Google Play 또는 불특정 사용자에게 배포할 운영 정식판으로 부르지는 않는다.
