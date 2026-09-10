# 2026-09-07 Android GPS 공개 수신과 실기기 E2E 검증

## 결론

Android 원본 사진의 GPS sidecar만 자동 수집하는 vertical slice를 구현하고 실제 Android 16 단말에서 끝까지 검증했다. 새 Tailscale Funnel `10000` 포트는 열지 않았다. Funnel이 지원하는 세 포트 중 이미 외부 공유에 쓰던 `8443`을 재사용하되 `/mobile-location` 경로를 별도 loopback 수신기 `127.0.0.1:18793`으로 분리했다.

실제 단말은 최근 10일의 `DCIM/Camera` 사진을 원본 복사 없이 검사해 GPS manifest 268건을 만들었고, 이를 Android Keystore로 암호화한 offline outbox에서 3개 P-256 서명 배치로 전송했다. Mac은 268건 모두를 별도 SQLite ledger에 저장했으며 정확 좌표는 macOS Keychain의 별도 AES-256-GCM key로 application-level 암호화했다. 전송 뒤 단말 outbox는 0건이었고 즉시 재실행은 신규 0건으로 정상 종료했다.

사진 bytes, filename, Android MediaStore ID는 공개 API와 Mac ledger에 전달하거나 저장하지 않는다. 이 단계는 GPS 수집·안전 보관까지 완료한 것이며, Google Picker 사진에 자동 투영하는 A-only matcher와 late reconciliation은 별도 품질 gate가 남아 있다.

## 포트 결정

Tailscale Funnel은 HTTPS 공개 포트를 `443`, `8443`, `10000`으로 제한한다. 임의의 희소 포트를 고르는 것은 불가능하다. 현재 `443`은 Open WebUI와 소유자 Photos 경로, `9119`는 Hermes Dashboard의 Tailnet 전용 진입점이므로 공개 상태로 바꾸지 않았다.

| 진입점 | 공개 범위 | upstream | 결과 |
| --- | --- | --- | --- |
| `:443` | Tailnet only | Open WebUI 및 소유자 Photos | 변경 없음 |
| `:9119` | Tailnet only | Hermes Dashboard | 변경 없음 |
| `:8443/` | Funnel | 기존 만료형 Story 공유 gateway | 변경 없음 |
| `:8443/mobile-location` | Funnel | GPS 전용 receiver `127.0.0.1:18793` | 추가·검증 완료 |
| `:10000` | 미사용 | 없음 | 열지 않음 |

운영 수신 기준 주소는 다음과 같다.

```text
https://byoungyoung-macmini.tail53bcc7.ts.net:8443/mobile-location
```

`tailscale funnel status`에서 Funnel은 `8443` 하나에만 활성화됐고, 공개 health probe는 내용 없는 `204`를 반환했다.

## 구현 산출물

### Mac 수신기

- `src/photos_mcp/interfaces/http/mobile_location.py`
  - 10분·1회용 enrollment
  - P-256 ECDSA 요청 검증
  - timestamp, nonce, sequence와 idempotency 검증
  - 최대 1 MiB, 배치당 100 manifest, schema `extra=forbid`
  - 사진·base64 필드 거부
  - CORS 미허용, no-store와 보안 응답 헤더
  - body·좌표·token·signature access log 비활성화
- `src/photos_mcp/infrastructure/mobile_location/ledger.py`
  - device revoke와 테스트 device purge
  - nonce replay cache와 단조 증가 sequence
  - idempotent batch receipt
  - exact GPS의 AES-256-GCM 암호화 저장
  - Keychain service `photos-mcp.mobile-location`
- `src/photos_mcp/mobile_location_server.py`
  - loopback bind만 허용
  - 기본 `127.0.0.1:18793`
- `resources/launchd/com.photosmcp.mobile-location.plist`
  - 로그인 시 시작, 비정상 종료 시 재시작
- `scripts/probe_mobile_location_receiver.py`
  - 가상 좌표 signed E2E 뒤 임시 device·batch·manifest purge
- `scripts/install_connected_android_bridge.py`
  - ADB 단말 한 대를 확인하고 최신 debug APK 빌드·덮어쓰기 설치·권한 부여
  - 기존 pairing이 있으면 그대로 보존하고 예약 작업 재등록
  - 미등록 단말만 10분·1회용 token을 메모리에서 전달해 자동 등록
  - token과 등록 JSON은 콘솔에 출력하지 않음

### Android Bridge

- `android/location-bridge`
  - Android 13+ 사진 읽기와 원본 위치 접근
  - 최근 10일 `DCIM/Camera`, 첫 실행 최대 1,000장
  - 이후 `(DATE_ADDED, _ID)` 복합 checkpoint 증분 처리
  - 한 장의 손상·미지원 포맷이 뒤 사진 처리를 막지 않음
  - 한 배치 최대 100 manifest
  - non-exportable P-256 signing key와 HMAC asset pseudonym
  - Android Keystore AES-GCM 암호화 offline outbox
  - 서버 ack 뒤에만 outbox 삭제
  - 24시간 persisted `JobScheduler`와 네트워크 복구 retry
  - 수동 `지금 동기화`와 aggregate 상태 표시
  - 현재 위치, 백그라운드 위치, 전체 파일 접근 권한 없음
  - ADB 개발 설치 시 기존 pairing을 보존하거나, 미등록 단말만 1회용 token을 콘솔 노출 없이 자동 등록
  - 등록 완료 상태에서는 수동 JSON 입력 UI를 숨김

개발용 APK에만 명시적 ADB smoke-test receiver가 포함된다. release source set에는 이 receiver가 없으며 운영은 USB나 ADB에 의존하지 않는다.

## 검증 과정과 결과

### 보안·계약 테스트

다음을 자동 검증했다.

- P-256 이외 enrollment 거부와 token 1회성
- 만료 token, encoded body와 oversized body 거부
- 잘못된 signature, 오래된 timestamp와 사진 필드 거부
- nonce replay, sequence rollback/jump와 idempotency 충돌 방어
- device revoke 뒤 요청 거부와 명시적 purge
- route prefix가 Funnel path stripping 전후 모두 같은 계약 유지
- 저장 전 exact GPS 암호화와 복호화 round-trip
- synthetic device의 공개 Funnel enrollment→signed batch→ack→revoke→purge

공개 synthetic probe 결과는 `200`, 수락 1건, 임시 상태 purge 성공이었다. probe 뒤 revoked 임시 device는 0건이고 실기기 device만 active 상태로 남았다.

### Android 16 실기기

| 항목 | 결과 |
| --- | ---: |
| enrollment | 성공 |
| 검사 범위 | 최근 10일 `DCIM/Camera` |
| GPS manifest | 268건 |
| signed batch | 3개 |
| Mac 저장 manifest | 268건 |
| 전송 후 단말 outbox | 0건 |
| 즉시 증분 재실행 | 신규 0건·성공 |
| 24시간 persisted job | 등록 성공·`waiting` |
| 원본 사진 복사 | 0장 |

검증 중 두 가지 Android 16 호환 문제를 발견해 수정했다.

1. 네트워크 조건이 있는 `JobScheduler` 등록에는 `ACCESS_NETWORK_STATE`가 필요했다. 이 읽기 전용 상태 권한을 선언한 뒤 job 등록이 성공했다.
2. Android Keystore AES-GCM은 보안상 암호화 IV를 provider가 생성해야 했다. 앱 생성 IV 전달을 제거하고 provider가 만든 고유 IV를 ciphertext 옆에 저장하도록 수정한 뒤 실제 268건 전송이 성공했다.

ADB 통합 설치 검증 중 기존 pairing 판별의 shell quoting 문제도 발견했다. 판별을 안전한 presence check로 바꾸고, 같은 P-256 공개키가 다시 enrollment되면 기존 device receipt를 반환하도록 서버 등록을 idempotent하게 만들었다. 검증 과정에서 만들어진 빈 중복 device 1건과 사용되지 않은 token은 제거했으며 기존 268개 manifest가 연결된 원래 pairing을 보존했다. 최종 재설치에서는 `existing pairing was preserved`, active device 1개, 미사용 token 0개를 확인했다.

정확 좌표, 사진 이름, device/key ID와 원본 digest 값은 테스트 출력이나 이 보고서에 기록하지 않았다.

### 빌드와 회귀

Android:

```text
./gradlew clean assembleDebug lintDebug
./gradlew assembleRelease
BUILD SUCCESSFUL
```

R8/minify가 적용된 unsigned release APK도 빌드했고 manifest에 개발용 `DebugControlReceiver`가 없음을 확인했다. 실제 단말은 기존 enrollment를 보존하기 위해 검증용 debug APK 상태로 유지했다. 개인 release key로 서명해 교체할 때는 Android 서명 변경으로 앱 데이터가 초기화되므로 한 차례 재등록이 필요하다.

Python 전체 회귀:

```text
812 passed in 9.93s
```

Mac LaunchAgent를 최종 코드로 재시작한 뒤 public health `204`, synthetic signed Funnel E2E와 실제 ledger 집계를 다시 확인했다. receiver access log 파일은 0 byte로 유지돼 private payload가 남지 않았다.

## 현재 운영 흐름

```text
Android Camera 원본
  → 앱이 기기 안에서 GPS·촬영 시각·식별 지문만 추출
  → Keystore AES-GCM offline outbox
  → 인터넷 연결 시 :8443/mobile-location으로 P-256 signed batch
  → loopback 전용 Mac receiver
  → signature/replay/schema/rate 검증
  → Keychain AES-256-GCM private ledger

Google Photos Picker
  → 기존 사진 처리·추천·스토리 작업은 sidecar를 기다리지 않고 완료
  → 후속 A-only matcher gate를 통과한 항목만 private GPS 연결
```

Mac 또는 Funnel이 꺼져 있으면 outbox는 삭제되지 않는다. 다음 예약 또는 수동 동기화가 같은 idempotency key로 재전송하며, 서버 ack를 받은 뒤에만 삭제한다.

## 보안 경계와 후속 gate

완료된 경계:

- owner UI, Open WebUI, Hermes Dashboard는 Tailnet 전용 유지
- 공개 API는 enroll, append-only batch와 무정보 health뿐
- list/get/search/download/delete route 없음
- 공통 bearer secret을 APK에 내장하지 않음
- 정확 좌표를 Telegram, LLM prompt, public story, access log에 노출하지 않음
- 앱은 Android 현재 위치를 추적하지 않고 사진에 이미 기록된 EXIF GPS만 읽음

운영 자동 연결 전에 남은 gate:

- Google Picker `=d` transient bytes와 Android strong digest의 A-only coverage 95% 이상
- A등급 false positive 0건
- late sidecar reconciliation과 collection revision 재생성
- ambiguous 후보의 owner review
- 생성 검증된 release APK를 개인 release key로 서명·교체하고 한 차례 재등록

수집기 자체는 실기기 E2E까지 완료됐지만, 위 품질 gate 전에는 268건의 exact GPS를 Google 사진에 자동 투영하지 않는다. 위치 없음보다 잘못된 위치가 더 위험하므로 보수적으로 격리한다.

## 공식 참고

- Tailscale Funnel 제약: <https://tailscale.com/docs/features/tailscale-funnel>
- Tailscale Funnel path routing: <https://tailscale.com/docs/reference/tailscale-cli/funnel>
- Android shared media와 원본 위치 접근: <https://developer.android.com/training/data-storage/shared/media>
- Android JobScheduler: <https://developer.android.com/reference/android/app/job/JobScheduler>
- Android Keystore key attestation: <https://developer.android.com/privacy-and-security/security-key-attestation>
