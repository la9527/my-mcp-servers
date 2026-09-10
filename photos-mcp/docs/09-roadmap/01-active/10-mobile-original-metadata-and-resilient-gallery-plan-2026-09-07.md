# 휴대폰 원본 메타데이터 수집과 안정적인 사진 뷰어 개선 계획

> **2026-09-09 위치 표시 정책 변경:** GPS 수집·서명·암호화·재전송 방어는 그대로 유지하지만, 본인·가족 Story에서는 상세 위치와 지도 표시를 허용한다. 화면 projection과 캡처 이미지 제외의 최신 기준은 [상세 장소·Google 지도·캡처 제외 전환 계획](12-detailed-place-map-and-screenshot-exclusion-plan-2026-09-09.md)을 우선 적용한다.

- 작성일: 2026-09-07 KST
- 상태: Android GPS 수집 vertical slice 구현·실기기 검증 완료, Google Picker 자동 연결 고도화 진행 중
- 범위: PhotosMcp의 iPhone·Android 원본 사진 수집, EXIF/GPS provenance, Story Album 뷰어 안정화
- 전제: 사용자가 소유한 휴대폰과 Mac을 사용한다. 관리 UI는 Tailnet 내부로 제한하고 GPS sidecar write-only 수신구만 서명 인증을 전제로 공개 예외로 둔다.
- 2026-09-07 구현에서 Android Bridge, Mac 전용 수신기와 기존 `8443` Funnel의 `/mobile-location` 경로를 운영 반영했다. 새 `10000` 포트는 열지 않았다.

## 결론

현재 문제는 `Swiper`, 위치 표시, 사진 수집을 한 가지 문제로 다루면 해결하기 어렵다. 실제로는 다음 세 문제가 겹쳐 있다.

1. 운영 HTML은 정식 Swiper 패키지를 사용하지 않는다. `<dialog>`와 작은 자체 JavaScript가 사진 한 장의 `src`를 바꾸는 간이 viewer이므로, Telegram 인앱 브라우저·모바일 WebView·큰 preview 생성 지연에 취약하다.
2. Google Photos Picker의 공식 응답에는 GPS가 없다. Chrome 자동 선택을 개선하거나 Picker 권한을 반복 승인해도 위치 필드가 새로 생기지 않는다.
3. 최근 Apple Photos 입력은 iPhone 촬영 원본보다 PhotosMcp가 Apple 추천 앨범에 다시 넣은 관리 출력물을 재수집한 비중이 높다. 위치·카메라 정보가 제거된 파생본이 다음 실행의 입력으로 돌아오는 feedback loop부터 차단해야 한다.

따라서 채택할 방향은 다음과 같다.

```text
P0  PhotosMcp 관리 출력의 Apple 입력 재수집 차단
    + SSR 사진 링크와 CSS scroll-snap viewer 안전망

P1  iPhone은 iCloud Photos → Mac PhotoKit을 기준 수집 경로로 강화
    + Apple Photos 위치와 원본 EXIF 위치를 provenance별 보존

P2  Android는 GPS sidecar companion → encrypted offline outbox
    → public write-only HTTPS 예외 또는 Tailnet → private metadata ledger
    + Google Picker 자산과 다중 증거 fingerprint로 연결

P3  위치가 없는 사진은 날짜·시간·장면을 중심으로 Story 구성
    + cross-provider anchor, OCR/VLM 후보, 소유자 확인을 단계적으로 추가

P4  Android 원본 자체의 별도 백업도 필요해질 때만
    PhotoSync → Mac mobile_inbox를 추가
```

휴대폰 전체 파일시스템을 원격 탐색하는 방식은 권하지 않는다. iPhone은 기존 Apple 경로를 유지한다. Android는 아래의 사용자 목적 수정에 따라 사진 전체 백업 앱보다 GPS sidecar만 전달하는 작은 companion을 우선 검토한다.

## 사용자 목적 수정: Android GPS sidecar만 보강

사용자의 실제 목적은 Android 사진 원본을 Mac에 다시 백업하는 것이 아니다.

```text
Android Camera 원본
  ├─ Google Photos가 기존 방식으로 cloud에 동기화
  └─ PhotosMcp Android Bridge가 원본의 GPS와 식별 증거만 보존

Google Picker가 같은 사진을 PhotosMcp로 전달
  → Android sidecar와 안전하게 매칭
  → 원본 GPS를 해당 Google 추천 사진의 private 위치로 연결
```

이 목적에서는 PhotoSync가 기본안보다 fallback에 가깝다. PhotoSync는 원본 파일 전체를 다시 전송하므로 데이터 이동·중복 저장·inbox 관리가 추가된다. 반면 Android 전용 metadata bridge는 사진 바이트를 서버에 저장하지 않고 다음 정보만 보낸다.

- Android MediaStore 자산의 로컬 식별자에서 파생한 불투명 device asset key
- 원본 촬영 시각과 offset/timezone
- 원본 폭·높이·MIME·orientation
- camera make/model 및 필요한 비민감 EXIF
- exact GPS, altitude와 GPS timestamp가 있을 때 해당 값
- original byte hash
- metadata를 제거해도 비교 가능한 image-content fingerprint
- manifest schema·extractor version·관찰 시각

정확 GPS는 Tailnet의 import-only endpoint를 통해 private location ledger에만 보낸다. 원본 사진 바이트는 매칭 실패를 해결하기 위해 사용자가 명시적으로 요청한 경우가 아니라면 보내지 않는다.

### 이 방식의 결정적인 제약

Android의 MediaStore 자산과 Google Photos Picker 자산 사이에는 앱이 사용할 수 있는 공식 공통 ID가 없다. Android 로컬 `_ID`를 Google Picker의 persistent media item ID로 변환할 수도 없다. 따라서 다음 값 하나만으로 자동 연결하면 안 된다.

- filename 단독
- 촬영 시각 단독
- original SHA-256 단독
- pHash 단독

Google Picker 다운로드는 위치 EXIF가 제거되어 컨테이너 bytes가 달라질 수 있으므로 Android 원본 SHA-256과 Google 다운로드 SHA-256이 항상 같다고 가정할 수 없다. filename은 편집·복사·burst에서 중복될 수 있고, 촬영 시각도 초 단위 충돌이 생길 수 있다.

### 권장 다중 증거 매칭

```text
1. Android manifest 수신
   ├─ original SHA-256
   ├─ metadata-independent content fingerprint
   ├─ perceptual hash
   ├─ capture time + timezone
   ├─ width × height
   ├─ filename
   └─ camera metadata

2. Google Picker 원본형 다운로드 수신
   └─ 같은 fingerprint 집합 계산

3. 후보 검색
   ├─ 촬영 시각 window
   ├─ dimensions/orientation
   └─ filename/camera 보조 증거

4. 자동 연결 gate
   ├─ 유일한 강한 후보만 auto-linked
   ├─ burst·편집본·복수 후보는 needs-review
   └─ 불일치는 unmatched로 유지
```

JPEG는 EXIF APP segment를 제외한 압축 image scan data의 digest를 계산하면 GPS만 제거된 파일을 강하게 비교할 수 있다. HEIC·WebP·편집본은 컨테이너가 다시 작성될 수 있으므로 orientation을 반영한 시각 fingerprint와 pHash를 함께 사용한다. Android와 Mac decoder의 출력 차이를 고려해 pHash는 단독 증거가 아니라 후보 축소와 확인용으로만 사용한다.

권장 gate는 다음과 같다. 실제 threshold는 20~50쌍 feasibility spike에서 결정한다.

| 등급 | 예시 증거 | 처리 |
|---|---|---|
| A | metadata-independent strong digest 일치 + 시각·크기 일치 + 유일 후보 | 자동 GPS 연결 |
| B | 매우 가까운 pHash + 시각·크기·카메라·파일명 다중 일치 + 유일 후보 | shadow 자동 후보, 표본 검증 후 승격 |
| C | pHash는 유사하지만 burst/편집본 등 복수 후보 | owner 확인 |
| D | 사진 내용 또는 메타데이터 충돌 | 연결하지 않음 |

잘못된 GPS 연결은 위치 없음보다 위험하므로 precision을 우선한다. 자동 매칭이 불확실하면 위치를 비워 두고 `위치 연결 확인 필요`로 표시한다.

### 권장 Android Bridge 구조

앱 이름 예시는 `PhotosMcp Location Bridge`로 둔다. 개인용 signed APK를 직접 설치할 수 있으며 Play Store 배포는 필요하지 않다.

앱이 요청할 기능:

- Android 13 이상 전체 자동 처리를 위한 사진 읽기 권한
- 위치 EXIF redaction을 해제하기 위한 `ACCESS_MEDIA_LOCATION`
- `MediaStore.setRequireOriginal(uri)`로 원본 metadata 접근
- 인터넷 권한은 서명된 GPS write-only endpoint 전송에만 사용
- 현재 위치를 추적하는 `ACCESS_FINE_LOCATION` 또는 background location은 요청하지 않음
- 전체 파일 접근 `MANAGE_EXTERNAL_STORAGE`도 요청하지 않음

처음 한 번 전체 사진 접근과 원본 위치 접근을 사용자가 승인하면 이후 신규 MediaStore image만 증분 처리한다. Android 14 이상에서 선택한 사진만 허용하면 그 범위만 처리하고, 모든 향후 Camera 사진을 자동 처리하려면 전체 사진 접근을 명시적으로 선택해야 한다.

증분 cursor는 `DATE_MODIFIED`만 사용하지 않는다. Android 11 이상에서는 MediaStore version과 generation을 기록하고, version이 유지되는 동안 generation-added/modified 기준으로 신규·변경 자산을 찾는다. MediaStore version이 바뀌면 bounded rescan을 수행한다.

구현은 외부 scheduler 의존성을 늘리지 않는 Android `JobScheduler`의 persisted periodic job으로 구성했다. WorkManager로 바꿔도 outbox·서명·수신 계약은 동일하다.

- 24시간 주기와 충분한 flex window
- unmetered network 우선
- battery not low
- 충전 조건은 선택 사항
- 실패 exponential backoff
- 정확한 새벽 시각을 보장하지 않음
- 앱을 열면 즉시 `지금 동기화` 가능

ADB·USB 연결은 개발 중 권한과 원본 metadata를 확인하는 진단 수단일 뿐 운영 입력 경로로 사용하지 않는다. 휴대폰은 Mac과 분리되어 있는 시간이 기본 상태이므로 앱은 다음 offline-first outbox를 가진다.

```text
MediaStore 증분 발견
  → 단말 안에서 GPS·식별 지문 추출
  → Keystore-backed key로 민감 payload 암호화
  → outbox queued
  → public Funnel GPS endpoint 접근 가능 시 signed batch 전송
  → 서버 ack 뒤 delivered/acknowledged
  → 실패·휴대폰 오프라인·Mac 오프라인이면 JobScheduler retry 또는 다음 주기에서 재시도
```

outbox에는 원본 사진 bytes를 넣지 않는다. exact GPS, 촬영 시각, 식별 지문과 최소 provenance만 보존하며 filename과 MediaStore ID는 device-local key로 치환한다. 전송 완료 전 앱이 종료되거나 재부팅되어도 queue는 유지되어야 한다.

Google Photos 동기화와 Android Bridge 전송의 순서는 고정할 필요가 없다. 두 원장은 독립적으로 저장하고 어느 쪽이 먼저 도착하더라도 나중에 matcher가 연결한다. 특히 새벽 PhotosMcp 작업은 휴대폰이나 sidecar 응답을 기다리지 않는다.

- sidecar가 이미 있으면 같은 실행에서 위치를 보강한다.
- sidecar가 없으면 `awaiting_mobile_metadata` 상태로 추천·스토리·HTML을 정상 완료한다.
- sidecar가 나중에 도착하면 reconciliation job이 private 위치를 연결하고 Story/HTML location section만 재생성한다.
- 늦은 보강은 원래 새벽 작업을 실패로 바꾸지 않고, 동일 collection revision을 올린다.
- Telegram에는 매 sidecar마다 알리지 않고 보강된 collection이 실제 다시 게시될 때 한 번만 요약한다.

### GPS write-only endpoint

권장 API는 원격 파일 브라우저가 아니라 append-only metadata ingest다.

```text
POST /mobile-location/v1/enroll
POST /mobile-location/v1/batches
```

운영 규칙:

- Tailscale Funnel의 `/mobile-location` path 뒤에 분리한 loopback 서비스
- device별 pairing secret 또는 key pair
- batch size와 payload size 제한
- replay 방지 nonce/sequence
- manifest idempotency key
- list/download/delete API 없음
- 좌표·filename·local MediaStore ID를 access log와 Telegram에 기록하지 않음
- exact GPS는 기존 private typed table 또는 전용 Android private ledger에만 저장

사진 원본 on-demand 요청은 기본적으로 제공하지 않는다. 추후 품질 분석이나 매칭 확인 때문에 필요해지면 owner 승인 receipt가 있는 단일 자산에만 짧게 허용한다.

### 모바일 VPN 비사용 시 외부 전송 예외

휴대폰에서 Tailscale VPN을 항상 켜 두지 않는 사용 패턴을 고려하면 Android Bridge 전송을 Tailnet 연결에만 의존하면 안 된다. 단, Mac의 기존 WebUI·Dashboard·사진 결과 전체를 공인망에 노출하는 것도 허용하지 않는다. `GPS sidecar 쓰기 전용 수신구` 하나만 공개 HTTPS 예외로 둔다.

2026-09-07 현재 Tailscale 구성은 다음과 같다.

| 포트 | 현재 상태 | 유지 정책 |
| --- | --- | --- |
| `443` | Serve, Open WebUI와 `/photos*`를 Tailnet에 제공 | 사설 유지 |
| `9119` | Serve, Hermes Dashboard를 Tailnet에 제공 | 사설 유지 |
| `8443` | 기존 story 공유용 Funnel | `/` story와 `/mobile-location` GPS 수신기를 path 단위로 분리 |
| `10000` | 미사용 | 열지 않음 |

Tailscale Funnel은 허용 포트가 `443`, `8443`, `10000`으로 제한되고, 같은 포트는 마지막 설정에 따라 전체가 Serve 또는 Funnel이 된다. 따라서 임의의 높은 외부 포트를 고를 수 없으며 `443`을 Funnel로 바꾸면 기존 Open WebUI·Photos 경로까지 공용이 될 수 있어 금지한다. 사용자 결정에 따라 새 `10000`을 열지 않고 이미 공개 중인 `8443`의 path routing을 재사용한다. 프로세스와 loopback 포트는 분리하므로 story gateway가 GPS ledger 권한을 얻지는 않는다.

```text
Android Location Bridge (일반 LTE/5G/Wi-Fi, Tailscale 불필요)
  → HTTPS
  → Tailscale Funnel :8443/mobile-location
  → 127.0.0.1:18793 전용 receiver
  → signature/replay/schema/rate 검증
  → encrypted private manifest ledger

Tailnet only, 변경 없음
  ├─ :443  Open WebUI + owner Photos
  └─ :9119 Hermes Dashboard

기존 public share port, path 분리
  ├─ :8443/                expiring story share → 127.0.0.1:18792
  └─ :8443/mobile-location GPS write-only       → 127.0.0.1:18793
```

외부 공개 receiver는 일반 REST 서비스가 아니라 capability가 극도로 작은 dropbox로 제한한다.

```text
POST /mobile-location/v1/enroll   # Mac에서 10분 enrollment window를 연 때만
POST /mobile-location/v1/batches  # 정상 운영에서 유일한 data route
```

- list/get/search/download/delete endpoint를 만들지 않는다.
- health endpoint가 필요하면 버전·DB·장치 정보를 주지 않고 `204`만 반환한다.
- CORS를 허용하지 않는다. Android native client만 사용한다.
- `Content-Type: application/json`과 `Content-Length`를 강제한다.
- 한 요청은 최대 100 manifest 또는 1 MiB 중 먼저 도달하는 한도로 둔다.
- 사진 bytes, thumbnail, base64 image 필드는 schema 수준에서 거부한다.
- receiver는 `127.0.0.1`에만 bind하고 Funnel만 외부 ingress를 맡는다.

Funnel은 공개 인터넷의 누구나 URL에 연결할 수 있으므로 URL이나 난수 path를 인증 수단으로 취급하지 않는다. Funnel/TLS는 전송 암호화와 origin IP 은닉을 담당하고, device 인증은 PhotosMcp가 별도로 수행한다.

권장 enrollment와 요청 인증:

1. Mac owner UI에서 10분·1회용 QR enrollment token을 생성한다.
2. Android 앱은 Android Keystore에 non-exportable P-256 signing key를 만든다.
3. 앱이 QR의 server URL/token과 device public key를 enrollment endpoint에 전송한다.
4. 서버는 token을 즉시 소모하고 opaque `device_id`와 `key_id`만 발급한다.
5. 이후 모든 batch는 method, path, body SHA-256, sent-at, nonce, idempotency key를 canonicalize해 device private key로 서명한다.
6. 서버는 등록된 public key로 서명 검증 후에만 JSON을 parse·저장한다.
7. device key는 owner UI에서 즉시 revoke할 수 있고 재등록 전에는 동일 device 요청을 모두 거부한다.

APK 안에 공통 bearer API key를 넣지 않는다. 앱을 역분석하면 모든 설치본에서 재사용될 수 있기 때문이다. device private key는 Android Keystore 밖으로 내보내지 않고, 개인 설치라도 가능한 경우 hardware-backed key를 사용한다. hardware attestation은 1인 개인 환경의 MVP 필수 조건은 아니지만 key가 `TrustedEnvironment`/`StrongBox`인지 상태에 기록할 수 있다.

replay와 중복 방지:

- request `sent_at`은 서버 시각 ±10분 범위만 허용한다. 사진의 오래된 `captured_at`에는 이 제한을 적용하지 않는다.
- 128-bit 이상 nonce를 device별 짧은 TTL cache에 기록한다.
- device별 monotonic sequence와 batch idempotency key를 DB unique key로 둔다.
- 동일 batch 재전송은 같은 ack를 반환하고 다시 저장하지 않는다.
- sequence가 지나치게 앞서거나 rollback되면 저장하지 않고 device 상태를 `review_required`로 둔다.

public abuse 방어:

- 인증 전 IP/global token bucket과 작은 connection timeout을 적용한다.
- 인증 후 device별 batch/minute·manifest/day 상한을 별도로 적용한다.
- shared carrier NAT를 고려해 IP만으로 영구 차단하지 않는다.
- body 전체, filename, coordinates, signature, token을 access/error log에 남기지 않는다.
- 인증 실패 응답은 device 존재 여부를 구분하지 않는 동일 `401`로 둔다.
- JSON depth, string length, digest length, 좌표 범위와 schema version을 검증한다.
- receiver process는 별도 비권한 사용자 또는 최소 권한 sandbox로 실행하고 photo library·recommendation 원본 디렉터리 읽기 권한을 주지 않는다.

민감 데이터 보호:

- Android outbox는 Keystore-backed key로 전체 payload를 암호화한다.
- HTTPS는 Funnel에서 Mac node까지 종단되고 local receiver로만 전달한다.
- Mac의 exact GPS columns는 Keychain의 별도 data key로 application-level encryption을 적용하고 DB에는 ciphertext·nonce·key version만 둔다.
- public endpoint의 응답에는 좌표·매칭 결과·사진 존재 여부를 넣지 않고 batch ack만 반환한다.
- Telegram에는 aggregate count와 상태만 보내고 정확 좌표를 보내지 않는다.

Mac이 꺼져 있거나 Tailscale/Funnel이 중단되면 앱은 outbox를 보존하고 exponential backoff로 재시도한다. Funnel은 Mac이 켜지고 Tailscale에 연결된 동안만 전달되므로 이 상태는 오류가 아니라 정상적인 지연이다.

대안 비교:

| 안 | 장점 | 단점 | 판정 |
| --- | --- | --- | --- |
| 기존 Funnel `:8443/mobile-location`을 별도 loopback receiver로 path routing | 새 public port 없음, 공인 IP/포트포워딩 불필요, Mac IP 은닉 | 공개 URL이므로 자체 인증 필수, Tailscale 설정 경계는 story와 공유 | 채택 |
| Tailscale Funnel `:10000` 전용 receiver | 프로세스뿐 아니라 외부 포트도 분리 | 새 공개 포트가 늘고 일부 이동통신망에서 비표준 포트 차단 가능 | 미채택 |
| Cloudflare Tunnel 전용 subdomain | outbound-only tunnel, WAF/rate limit, 표준 443 | 도메인·Cloudflare 운영과 추가 trust 필요 | 운영 강화 2순위 |
| 공유기 port forwarding/DDNS | 중간 서비스 없음 | Mac IP/포트 직접 노출, 방화벽·인증·DDoS 책임 증가 | 금지 |
| 외부 cloud queue에 GPS 저장 | Mac 오프라인 수신 가능 | 제3자 저장·키·삭제 정책 증가 | end-to-end ciphertext relay일 때만 별도 ADR |

운영 URL은 `https://byoungyoung-macmini.tail53bcc7.ts.net:8443/mobile-location`이다. 이동통신망에서 `8443`이 차단된다면 `443`을 공개로 바꾸지 않고 Cloudflare Tunnel의 전용 subdomain/표준 443을 별도 ADR로 검토한다.

공개 예외 rollout gate:

1. receiver를 loopback에서만 실행하고 fake encrypted coordinate로 contract test한다.
2. enrollment closed 상태, 만료 token, 재사용 token, 잘못된 signature를 모두 거부한다.
3. replay, duplicate, oversized body, image field, schema bomb, rate limit을 검증한다.
4. Funnel 없이 loopback E2E를 통과한다.
5. 기존 `:8443`에 `/mobile-location` path만 추가하고 signed fake payload 1건을 Funnel HTTPS로 전송한다.
6. 동시에 `:443`·`:9119`가 공인망에서 계속 차단되고 Tailnet에서는 정상인지 확인한다.
7. 정확 좌표 대신 비식별 test coordinate를 사용한 보안 회귀가 모두 통과한 뒤에만 실제 sidecar를 허용한다.

2026-09-07 구현에서는 receiver 인증·암호화·rate limit 검증 뒤 `/mobile-location` path만 활성화했다. `tailscale funnel status --json`의 `AllowFunnel`은 `8443` 하나뿐이며 `443`과 `9119`는 계속 Tailnet 전용이다.

#### 2026-09-07 구현 결과

- Mac receiver: [mobile_location.py](../../../src/photos_mcp/interfaces/http/mobile_location.py), [ledger.py](../../../src/photos_mcp/infrastructure/mobile_location/ledger.py)
- local bind: `127.0.0.1:18793`; LaunchAgent `com.photosmcp.mobile-location`이 로그인 시 시작하고 비정상 종료 시 재시작한다.
- public ingress: `https://byoungyoung-macmini.tail53bcc7.ts.net:8443/mobile-location`; `10000`은 열지 않았다.
- normal data route는 signed `POST /v1/batches` 하나이며 list/get/search/download/delete route는 없다. health는 내용 없는 `204`만 반환한다.
- 등록은 10분·1회용 token과 Android Keystore P-256 public key로 수행한다. 정상 batch는 body hash, 전송 시각, nonce, sequence, idempotency key를 ECDSA로 검증한다.
- 정확 좌표 묶음은 macOS Keychain의 별도 256-bit key로 AES-GCM 암호화한 뒤 전용 SQLite ledger에 저장한다. device label은 SHA-256으로만 남기고 filename·MediaStore ID·사진 bytes는 받지 않는다.
- Android 앱 소스는 [android/location-bridge](../../../android/location-bridge)에 있다. 사진 원본 GPS 접근용 `READ_MEDIA_IMAGES`·`ACCESS_MEDIA_LOCATION`, 예약 작업의 네트워크 조건 확인용 `ACCESS_NETWORK_STATE`와 통신용 `INTERNET`만 사용한다. 현재 위치·백그라운드 위치·전체 파일 접근은 요청하지 않는다.
- ADB 개발 설치는 `scripts/install_connected_android_bridge.py` 한 명령으로 빌드·덮어쓰기 설치·권한·pairing을 처리한다. 기존 pairing은 보존하고 미등록 단말만 token을 노출하지 않고 자동 등록하며, 동일 P-256 key의 서버 enrollment도 idempotent하다.
- Android outbox는 Android Keystore AES-GCM으로 암호화한다. 단말 P-256 private key와 asset HMAC key도 Keystore 밖으로 내보내지 않는다.
- 첫 실행 범위는 최근 10일의 `DCIM/Camera`이고 한 실행 최대 1,000장, batch당 최대 100건이다. 이후 `(DATE_ADDED, _ID)` 복합 checkpoint 뒤의 새 사진만 처리한다.
- 공개 Funnel 가상 좌표 E2E는 등록→signed batch→ack→단말 revoke→테스트 state purge까지 통과했다.
- Android 16 실기기에서 공개 Funnel 등록, 최근 10일 `DCIM/Camera` scan, 268개 GPS manifest의 3개 signed batch 전송, Mac 암호화 저장과 빈 outbox까지 확인했다. 이어진 증분 실행은 신규 0건으로 성공했고 persisted 24시간 job은 `waiting` 상태로 등록됐다.
- 검증 중 확인한 Android 16 `JobScheduler`의 `ACCESS_NETWORK_STATE` 요구사항과 Android Keystore AES-GCM의 provider-generated IV 요구사항을 반영했다. 운영은 USB/ADB에 의존하지 않는다.

### Google Picker 결합 ledger

다음 두 테이블 또는 동등한 구조가 필요하다.

```text
android_asset_manifests
├── device_asset_key
├── device_id
├── media_generation
├── capture_time
├── dimensions/mime
├── strong_content_digest
├── perceptual_hash
├── encrypted/private location fields
├── extractor_version
└── received_at

google_android_asset_links
├── google_picker_asset_id
├── device_asset_key
├── match_state
├── confidence
├── evidence_codes[]
├── matcher_version
├── owner_decision
└── linked_at
```

Google Picker 자산이 recommendation collection에 들어올 때 matcher를 실행하고, A등급 연결만 해당 `photo_locations_private`와 recommendation location snapshot으로 projection한다. source는 `android_original_sidecar`로 명시한다.

2026-09-07 실제 50쌍에서는 Google 분석 cache와 Android 원본 사이의 strong A가 23쌍, 정확하지만 변환 영향을 받은 shadow B가 25쌍이었다. A를 늘리기 위해 Google Picker 입력은 가능하면 `=d` 원본 크기 bytes를 한 번만 받아 strong digest를 계산하고, VLM용 축소본은 로컬에서 만든다. 원본 bytes는 fingerprint와 분석 파생본 생성 뒤 즉시 폐기한다. 휴대폰 연결은 필요하지 않으며 Google 다운로드를 원본 보관으로 바꾸는 것도 아니다.

### 앱의 확장 용도

GPS-only MVP 이후에도 원본 사진을 자동 업로드하지 않고 다음 기능을 선택적으로 제공할 수 있다.

- 촬영 timezone·orientation·camera/lens 정보 보강
- Android burst·motion photo 관계 보존
- Google cloud에 올라갔지만 device sidecar가 없는 사진 목록 표시
- device에는 있지만 아직 Google Picker 분석과 연결되지 않은 manifest 수 표시
- 사용자가 위치를 제거하거나 수정한 자산의 metadata generation 변경 감지
- 매칭이 애매한 burst/편집 사진 2~3장을 휴대폰에서 확인
- `공간 확보` 전에 sidecar가 Mac에 안전하게 도착했는지 확인
- owner가 요청한 한 장만 원본 또는 작은 확인 thumbnail 전송

Google Photos 앱의 backup 완료 상태를 읽는 공식 API는 없으므로 앱이 `Google 백업 완료`라고 단정해서는 안 된다. 대신 `device sidecar 전송 완료`, `Google Picker 자산 연결 완료`를 별도 상태로 표시한다.

## 5개 독립 검토 결과

이번 설계는 다음 다섯 관점을 별도로 검토한 뒤 통합했다.

| 관점 | 핵심 발견 | 채택 판단 |
|---|---|---|
| iPhone 원본 수집 | 최근 Apple 입력에 PhotosMcp 관리 추천본이 다시 섞이는 feedback loop가 확인됐다. Apple catalog 위치 수집 자체는 과거 자산에서 동작한다. | 관리 출력 제외를 가장 먼저 적용하고 PhotoKit을 기준 경계로 강화한다. |
| Android 원본 수집 | Google Picker만으로는 GPS를 받을 수 없으며, 광범위 검토에서는 `DCIM/Camera` 원본 복제가 가장 단순한 복구 경로였다. | 사용자 목적을 GPS-only로 재한정한 뒤 metadata bridge를 1순위, PhotoSync를 원본 fallback으로 조정했다. |
| Gallery UI 안정성 | 현재는 정식 Swiper가 아니라 `<dialog>` 기반 자체 controller이며 실제 no-JS·dialog 미지원 fallback이 없다. | SSR `<a>` 링크 + CSS scroll-snap을 기본으로 하고 JS는 progressive enhancement로만 사용한다. |
| 공식 API·보안 | PhotoKit과 Android MediaStore는 한 번 승인된 전체 사진 접근을 지원하지만 앱별 권한은 계속 필요하다. Google Picker는 위치를 제공하지 않는다. | 보안 제거가 아니라 권한 경계를 단순화하고, 정확 위치 수집과 외부 공유를 분리한다. |
| 교차 플랫폼 운영 | iPhone은 iCloud/PhotoKit, Android 원본 백업은 push/sync가 안정적이다. Immich와 범용 전용 앱은 운영 범위가 크다. | Android GPS-only 범위에는 작은 sidecar bridge만 먼저 구현하고 범용 기능은 유예한다. |

## 현재 구현과 운영 데이터에서 확인한 사실

### Gallery 구현

운영 UI는 [story_web.py](../../../src/photos_mcp/interfaces/http/story_web.py)의 `STORY_JS`와 `<dialog>` 하나로 동작한다.

```text
grid의 <button> 선택
  → show(index)
  → 하나의 <img>에 preview URL 지정
  → dialog.showModal()
  → touchstart/touchend의 X 차이를 직접 계산
  → 이전 또는 다음 사진으로 src 교체
```

현재 source·앱 bundle에는 `Swiper(...)`, `swiper-bundle.min.js`, `swiper-bundle.min.css`가 없다. 활성 설계 문서도 1차 운영본이 실제 Swiper 대신 로컬 경량 controller임을 명시한다.

현재 controller에는 다음 취약점이 있다.

- `dialog.showModal()` 지원 여부와 예외를 확인하지 않는다.
- 사진 카드가 `<button>`이므로 JavaScript가 실패하면 큰 사진으로 이동할 실제 URL이 없다.
- CSS `touch-action` 없이 수평 swipe를 직접 계산해 Telegram 인앱 브라우저·iOS 뒤로가기 gesture와 충돌할 수 있다.
- preview 로딩 중·404·decode 실패·재시도 상태가 없다.
- viewer selector 하나가 누락돼도 초기화 전체가 중단될 수 있다.
- 처음 보는 큰 preview는 HTTP 요청 중 동기 생성될 수 있어 검은 화면처럼 느껴질 수 있다.
- HTML·JS·CSS 호환 테스트가 문자열과 HTTP 응답에 집중되어 있고 실제 모바일 interaction E2E가 없다.
- 계획 문서에 적힌 단일 사진 SSR fallback과 CSS scroll-snap fallback이 아직 실제 구현에 없다.

### 위치 데이터

2026-09-07 KST 읽기 전용 집계 결과는 다음과 같다. 파일명·자산 ID·좌표는 출력하지 않았다.

| 관찰 | 결과 | 해석 |
|---|---:|---|
| 완료 recommendation member | Apple 5, Google 28 | 현재 추천 스토리 기반 |
| recommendation member 중 private location 연결 | 0 | HTML이 숨긴 것이 아니라 위치 snapshot이 없다. |
| 최근 30일 촬영일 기준 Apple 이미지 | 57 | 이 표본에는 GPS가 없었다. |
| 위 57장 중 Apple catalog GPS | 0 | 최근 유입 자산을 별도로 진단해야 한다. |
| Apple 전체 image-only catalog | 21,843 | catalog 읽기 자체는 가능하다. |
| 위 catalog 중 GPS 있음 | 17,024 | 위치 parser/DB 접근이 전면 고장 난 것은 아니다. |
| 최근 10일 `date_added` 표본 | 28 | 모두 PhotosMcp 관리 추천 앨범에 속했다. |
| 위 28장 중 위치·iPhone camera model | 각각 0 | 관리 파생본의 재수집이 최근 입력을 오염시켰다. |

전체 Photos catalog 집계는 `images=True` 조건이고, `date_added` 및 recommendation 집계와 범위가 다르므로 숫자를 직접 합산하지 않는다. 중요한 결론은 과거 Apple 자산에는 위치가 충분히 존재하지만 최근 자동화 입력은 관리 출력물로 편향됐다는 점이다.

### 이미 구현된 위치 경계

현재 코드에는 필요한 기반이 상당 부분 있다.

- Apple source는 `p.latitude`, `p.longitude`를 분석 입력에 전달한다.
- 랭커는 provider metadata와 embedded EXIF 위치를 구분해 private 위치 테이블에 저장할 수 있다.
- 추천 materialization은 분석 위치가 없을 때 로컬 원본 파일의 embedded GPS를 다시 읽는다.
- exact 좌표는 private typed column에 두고 Story에는 coarse label만 제공한다.
- 같은 collection의 동일 scene 또는 2시간 이내 anchor가 모두 같은 label일 때만 `(추정)` 위치를 만들 수 있다.
- 공유 derivative JPEG는 EXIF/GPS를 제거한다.

다만 다음 제약이 남아 있다.

- Apple/Google collection이 분리되어 provider를 넘는 동일 사진·인접 장면 위치 anchor를 활용하지 못한다.
- `location_privacy.py`의 offline 도시 목록이 제한적이고 90km 밖의 GPS는 label을 만들지 못한다.
- 좌표가 있는데 label만 해결되지 않은 상태와 좌표 자체가 없는 상태가 충분히 분리되지 않는다.
- OCR, 랜드마크 후보, 사용자 위치 확인 UI는 계획만 있고 운영 구현은 없다.
- local source는 분석용 1024px JPEG를 만들기 전에 원본 EXIF를 최상위 source metadata로 충분히 보존하지 않는다.
- combined daily parent는 아직 `apple|google`만 지원하며 `mobile_inbox`가 없다.

## 왜 내 사진인데도 앱 권한이 필요한가

사진 소유권과 앱 프로세스 권한은 다른 개념이다. OS는 사람이 소유자라는 사실보다 “어느 앱이 어떤 데이터에 접근하는가”를 판단한다.

| 경계 | 보호 대상 | 필요한 것 |
|---|---|---|
| iOS 앱 sandbox | 다른 앱과 Photos library의 실제 자산 | PhotoKit 사진 접근 승인 |
| Android scoped storage | 다른 앱과 공유 저장소의 사진 | 사진 미디어 접근 승인 |
| 사진 속 위치 | 얼굴·집·동선이 포함될 수 있는 GPS | Android에서는 별도 `ACCESS_MEDIA_LOCATION`; iOS에서는 PhotoKit 자산 접근 |
| Tailnet | 휴대폰과 Mac 사이 네트워크 | 승인된 Tailscale 장치와 grant |
| Mac inbox | 업로드된 원본 | 전용 endpoint credential과 폴더 ACL |
| 외부 공유 | 제3자가 보는 사진과 위치 | 별도 share package·만료·표시 등급 |

사용자가 PhotoSync나 PhotosMcp companion 앱에 한 번 전체 사진 접근을 허용하면 매 사진마다 Picker를 다시 열 필요는 없다. 다만 사용자는 OS 설정에서 언제든 권한을 제한하거나 철회할 수 있어야 한다.

Tailscale은 전송 경로를 보호하지만 iOS PhotoKit 또는 Android MediaStore 권한을 대신 부여하지 않는다. 반대로 사진 접근 권한을 승인해도 Mac의 모든 파일이나 Tailnet 서비스에 접근할 권한이 생기는 것은 아니다.

따라서 사용자 경험 목표는 권한을 없애는 것이 아니라 다음처럼 만드는 것이다.

1. 최초 한 번만 이해 가능한 설명과 함께 사진 접근을 승인한다.
2. 이후에는 신규 자산만 증분 전송한다.
3. 휴대폰 앱에는 삭제·원격 탐색·다른 폴더 접근을 주지 않는다.
4. 정확 GPS는 private 처리에 사용하고 외부 공유는 별도 승인 수준으로 투영한다.

## 권장 최종 아키텍처

```text
                         ┌────────────────────────────┐
iPhone Camera            │ Apple 주 경로              │
  → iCloud Photos        │ Mac System Photo Library   │
                         │ → PhotoKit metadata/helper │
                         └────────────┬───────────────┘
                                      │
Android Camera                         │
  ├→ Google Photos → Picker content ───┤
  └→ Location Bridge                   │
     → GPS sidecar + fingerprint ──────┤
                                      ▼
                         AssetEnvelope / provenance
                         ├─ origin provider/device
                         ├─ content SHA-256
                         ├─ capture time/timezone
                         ├─ library asset location
                         ├─ embedded EXIF location
                         ├─ conflict/status
                         └─ managed output flag
                                      │
                                      ▼
                         combined curation parent
                         ├─ Google↔Android evidence match
                         ├─ content hash dedupe
                         ├─ scene/time grouping
                         ├─ local/private location
                         ├─ recommendation storage
                         └─ Telegram result 1건
                                      │
                      ┌───────────────┴────────────────┐
                      ▼                                ▼
            Owner/Tailnet Story              30일 외부 공유
            상세 장소·근거                   재인코딩 이미지
            exact 좌표는 접힘                도시/권역 label만
```

### AssetEnvelope 권장 필드

```text
canonical_asset_id
origin_provider
origin_asset_id
origin_device_id
content_sha256
metadata_independent_digest
perceptual_hash
resource_role
capture_time_original
capture_timezone
library_asset_location
embedded_exif_location
sidecar_location
canonical_location_source
location_conflict
metadata_provenance[]
google_android_match_state
matcher_version
is_managed_output
source_ingested_at
```

핵심은 좌표 한 쌍만 저장하지 않고 `Apple Photos가 현재 표시하는 위치`, `원본 EXIF 위치`, `Android 원본 위치`, `Takeout sidecar 위치`, `사용자 확인 위치`를 provenance별로 구분하는 것이다.

## 도구와 앱 비교

| 방식 | 플랫폼 | 원본·GPS | 자동화 | 추가 운영 | PhotosMcp 적합성 | 판단 |
|---|---|---:|---:|---:|---:|---|
| iCloud Photos → Mac PhotoKit | iPhone | 매우 높음 | 높음 | 낮음 | 매우 높음 | **iPhone 기본안** |
| Android GPS sidecar companion | Android | 사진 미전송·GPS만 보강 | 높음 | 초기 개발 중간 | 매우 높음 | **현재 목적의 Android 기본안** |
| PhotoSync → Mac inbox | iOS·Android | 높음 | 중상 | 낮음~중간 | 높음 | 원본 백업도 필요할 때 fallback |
| FolderSync | Android | 원본 복사형, 실측 필요 | 높음 | 낮음 | 높음 | PhotoSync 2순위 |
| Syncthing-Fork | Android 중심 | 높음 | 높음 | 중간 | 높음 | 공식 Android 앱 종료로 신규 표준 비권장 |
| Immich | iOS·Android | 높음 | 중상 | 높음 | 중간 | 별도 사진 플랫폼까지 원할 때만 |
| 범용 PhotosMcp companion | iOS·Android 별도 | 매우 높음 | 중간 | 개발·유지 높음 | 매우 높음 | GPS-only MVP 이후 필요할 때 확장 |
| USB Image Capture | iPhone | 매우 높음 | 낮음 | 낮음 | 높음 | 검증·복구 경로 |
| iMazing | iPhone | 높음 | 낮음~중간 | 중간 | 낮음~중간 | 진단용 |
| iOS 단축어 | iPhone | 보존 계약 불명확 | 중간 | 낮음 | 낮음 | 예외 수동 전송용 |

### PhotoSync를 fallback으로 유지하는 이유

- Android Bridge의 매칭 feasibility가 부족하면 원본 전체를 확실히 가져오는 복구 경로가 필요하다.
- 제작사 문서는 원본을 변경하지 않고 EXIF와 GPS를 보존한다고 명시한다.
- iOS와 Android를 모두 지원한다.
- SMB, SFTP, WebDAV 등 Mac/NAS 경로로 직접 보낼 수 있다.
- 새 사진, 특정 앨범, 충전, Wi-Fi/VPN, 일정 조건을 지원한다.
- PhotosMcp의 local file 분석과 추천 저장 기반을 재사용할 수 있다.

단, 현재 GPS-only 목적에는 원본 파일 전체를 다시 저장하는 비용이 있으므로 1차 선택은 아니다. 상용 앱의 문서만 신뢰하지 않고 JPEG·HEIC·Live Photo·편집본 표본으로 전송 전후 메타데이터를 비교해야 한다. iOS의 백그라운드 작업은 정확한 시각을 보장하지 않으므로 “03:00까지 안 왔으면 실패”가 아니라 다음 실행에서 carry-over하는 정책이 필요하다.

### Immich를 지금 기본안으로 쓰지 않는 이유

Immich는 모바일 백업·지도·앨범·검색 UI가 뛰어나지만 현재 요구에는 다음 비용이 추가된다.

- Docker Compose·PostgreSQL·원본과 DB의 별도 백업
- 서버와 모바일 앱의 버전 관리
- 썸네일·변환 영상 스토리지
- 항상 켜진 서버
- PhotosMcp용 read-only API adapter

현재 Linux workstation은 필요할 때만 켜는 정책이므로 일일 휴대폰 백업 서버와 맞지 않는다. macOS Docker를 핵심 운영 서버로 두는 것도 Immich 공식 권고와 맞지 않는다. 장래에 PhotosMcp Story와 별개로 Google Photos 대체형 모바일 라이브러리 전체를 원할 때 다시 검토한다.

### 범용 companion으로 확장할 때의 올바른 형태

전용 앱은 휴대폰 파일시스템 원격 브라우저가 아니라 증분 사진 uploader여야 한다.

iOS:

- PhotoKit `.readWrite` 전체 또는 사용자가 선택한 제한 접근
- `PHAsset.localIdentifier`, `creationDate`, `location`
- `PHAssetResourceManager` 원본 리소스
- 처리한 자산 ledger와 재전송 receipt
- BackgroundTasks의 지연 실행 허용

Android:

- `READ_MEDIA_IMAGES`
- 위치가 필요하면 `ACCESS_MEDIA_LOCATION`
- `MediaStore.setRequireOriginal(uri)`
- WorkManager 또는 JobScheduler의 충전·Wi-Fi 제약
- content SHA-256과 resumable upload

공통:

- Tailnet 내부 HTTPS import-only endpoint
- 등록된 장치 credential
- upload·status만 제공하고 list·download·delete API는 제공하지 않음
- 원본과 metadata manifest를 하나의 receipt로 commit
- 성공 receipt 뒤에만 device ledger 갱신

Android GPS sidecar MVP는 앞 절의 최소 manifest 기능만 구현한다. 원본 업로드까지 포함하는 범용 companion은 metadata-only 운영에서 부족한 요구가 구체적으로 확인된 뒤에만 확장한다.

## iPhone 설정 및 파일럿

### 0단계: 촬영 원본에 위치가 실제로 있는지 확인

1. iPhone 사진 앱에서 최근 iPhone Camera 사진 3~5장을 연다.
2. 위로 쓸어 올리거나 정보 버튼을 눌러 지도·위치가 있는지 확인한다.
3. 지도가 없다면 `설정 → 개인정보 보호 및 보안 → 위치 서비스 → 카메라`에서 `앱을 사용하는 동안`과 `정확한 위치`를 켠다.
4. 설정 변경은 앞으로 촬영하는 사진에만 적용되며 과거 사진의 GPS를 복원하지 않는다.

판단:

- iPhone에도 위치 없음: 수집 도구가 복구할 GPS가 없다.
- iPhone에는 위치 있고 Mac Photos에도 위치 있음: PhotosMcp의 자산 연결/필터 문제다.
- iPhone에는 위치 있고 Mac Photos에는 없음: iCloud 동기화 또는 Mac PhotoKit bridge를 진단한다.
- Google Photos에만 장소가 있고 iPhone 원본에는 없음: Google이 추정한 위치일 수 있으며 Picker로는 가져올 수 없다.

### 1단계: 기존 Apple 경로 정상화

1. iPhone과 Mac의 iCloud Photos 동기화를 확인한다.
2. Mac Photos library가 System Photo Library인지 확인한다.
3. macOS `개인정보 보호 및 보안 → 사진`에서 PhotosMcp 전체 접근을 확인한다.
4. 저장 공간이 충분하면 Photos 설정에서 `이 Mac으로 원본 다운로드`를 사용한다.
5. 저장 공간을 아끼려면 최적화를 유지하되 선택된 자산만 PhotoKit `isNetworkAccessAllowed`로 가져온다.
6. Apple daily discovery에서 PhotosMcp 관리 폴더·추천 앨범과 managed output ledger 자산을 제외한다.
7. 위치가 확실한 iPhone 원본 20장으로 `PHAsset.location`, osxphotos, embedded EXIF를 비교한다.

관리 출력 제외를 앨범 이름 한 가지에만 의존하면 안 된다. 다음 증거를 조합한다.

- album/destination receipt가 반환한 Apple asset ID
- PhotosMcp가 관리하는 destination album/folder ID
- `is_managed_output=true` ledger
- content hash와 local recommendation asset 연결

UUID형 파일명만으로 제외하면 정상 자산을 오탐할 수 있으므로 사용하지 않는다.

### 2단계: 필요한 경우 PhotoSync 보조 파일럿

Mac PhotoKit만으로 최신 원본 유입이 불안정할 때만 iPhone에도 PhotoSync를 붙인다.

권장값:

- 사진 접근: 전체 접근
- 품질: Original/Full size
- HEIC 변환: 끔
- 위치 제거: 끔
- 편집 위치를 쓸지 원본 위치를 쓸지 명시적으로 선택
- 원본 앨범: Camera 또는 별도 `PhotosMcp Input`
- New only: 켬
- Delete after transfer: 끔
- 실행: 충전 중 하루 한 번
- Background App Refresh: 켬
- 전송 실패: 다음 기회에 재시도

iOS는 정확한 cron을 보장하지 않으며 앱을 강제 종료하면 background transfer가 지연될 수 있다.

## Android 설정 및 파일럿

### 권장 파일럿 A: GPS sidecar matching feasibility

전체 Android 앱을 만들기 전에 20~50쌍의 Android 원본과 동일한 Google Picker 다운로드로 매칭 가능성을 계측한다.

2026-09-07 기반 구현·실측 상태:

- `mobile_location_matching.py`에 원본 SHA-256, metadata-independent JPEG digest, 방향 보정 normalized pixel digest, 256-bit pHash, 촬영 시각·크기·파일명·camera 증거 수집을 구현했다.
- `analyze_android_picker_matching.py` CLI는 Android/Picker 디렉터리와 명시적 정답 JSON을 받아 A/B/C/D 판정과 비식별 집계만 저장한다. 공통 모듈은 bytes API도 제공해 ADB 진단이나 향후 app ingest가 원본을 디스크에 복사하지 않고 지문을 만들 수 있다.
- A는 유일한 strong identity만 허용하고, 재인코딩 기반 B는 `shadow_only`, 중복·복수 후보 C는 `needs_review`, 무증거 D는 `unmatched`로 고정했다.
- 출력 JSON에는 사진 경로·파일명·좌표·원본 해시값이 포함되지 않는다. 정답 20쌍 전체 coverage, 자동 연결률 95% 이상, false positive 0건을 모두 만족해야만 `passed`가 된다.
- synthetic EXIF 제거·재인코딩·중복 후보·오정답 회귀와 Google Picker/위치 저장 관련 표적 회귀 53개, 전체 회귀 804개가 통과했다.
- 20쌍 임시 synthetic CLI E2E에서 A 20·오연결 0·`passed`·종료 코드 0을 확인했고, 1,000쌍 strong-identity in-memory 매칭도 전체 비교표 없이 완료했다. 이는 도구 검증이며 실제 사진 품질 gate를 대신하지 않는다.
- Google Picker 운영 캐시의 최근 30장을 읽기 전용으로 검사해 30장 모두 sidecar·촬영 시각·camera model·JPEG digest·pixel digest·pHash 생성에 성공했고 embedded GPS는 예상대로 0장이었다.
- Android Platform Tools 37.0.1로 연결된 Android 16 단말을 일회성 진단했다. 최근 10일 `DCIM/Camera` JPEG는 269장이며, 최근 원본 30장은 GPS 30·촬영 시각/offset 30·camera metadata 30·decode 오류 0이었다. Content URI 표본 5장은 filesystem 원본과 bytes가 동일했고 GPS도 유지됐다.
- 원본을 저장하지 않고 ADB memory stream과 기존 Picker cache에서 unique filename·촬영 시각으로 실제 정답 후보 50쌍을 구성했다. 판정은 filename과 무관한 strong/visual evidence로 수행했고 A 23·B 25·C 2·D 0, 제시된 오답 후보 0, A+B correct candidate coverage 96%였다.
- 엄격한 자동 gate는 A만 세므로 46%로 아직 실패다. B를 바로 자동 승격하지 않고 Google Picker `=d` original-size transient fingerprint 검증으로 A 비율을 높인 뒤 다시 판정한다.
- ADB는 이 실측 이후 운영 기본 경로가 아니다. signed Android 앱의 offline outbox와 Tailnet retry가 운영 경로다.

상세 검증 결과와 실행 예시는 [Android 원본↔Google Picker 매칭 기반 검증 보고서](../../08-reports/01-validation/28-android-google-picker-matching-foundation-2026-09-07.md)에 둔다.

1. 완료: 연결된 단말에서 50쌍을 ADB로 memory stream해 원본 bytes를 저장하지 않고 1차 실측했다.
2. 다음: 같은 사진의 새 Google Picker session에서 `=d` bytes로 transient strong digest를 계산한다.
3. Google 원본 bytes는 digest와 VLM용 파생본 생성 직후 폐기한다.
4. A 비율과 false match를 재측정하고 B·C는 계속 자동 연결하지 않는다.
5. 운영에서는 ADB를 제거하고 동일 extractor를 Android 앱에서 실행해 offline outbox로 전송한다.

합격 기준:

- 일반 camera 사진의 95% 이상이 유일 후보로 연결된다.
- 자동 연결 false positive가 0건이다.
- burst·편집본의 애매한 후보는 자동 연결하지 않는다.
- Google 다운로드에서 GPS가 없어도 Android sidecar 위치가 정확히 연결된다.
- 사진 원본 bytes를 Mac에 영구 저장하지 않고 검증 후 제거할 수 있다.

### 권장 파일럿 B: Android Location Bridge MVP

1. 개인용 signed APK를 설치한다.
2. 전체 사진 접근과 원본 media location 접근을 한 번 허용한다.
3. 기본 scope를 최근 10일·`DCIM/Camera`로 제한한다.
4. `지금 동기화`로 manifest batch를 public Funnel의 GPS write-only endpoint에 보낸다.
5. exact GPS가 있는 수, 없는 수, 전송 수, 연결 수만 앱과 owner status에 표시한다.
6. Google Picker 표본을 실행해 A/B/C/D match state를 확인한다.
7. 네트워크 없이 `queued`가 유지되고 앱·단말 재시작 뒤에도 사라지지 않는지 검증한다.
8. Tailnet이 다시 연결되면 batch 전송·ack·idempotent retry를 검증한다.
9. 성공 후에만 JobScheduler 하루 한 번 실행을 켠다.

앱은 현재 위치를 추적하지 않고 기존 사진 파일의 GPS metadata만 읽는다. 따라서 Camera/Photos 권한 외에 background location permission을 요구해서는 안 된다.

### 원본 백업도 필요할 때: PhotoSync

1. Android에 Tailscale과 PhotoSync를 설치한다.
2. Tailnet에서 장치를 승인한다.
3. PhotoSync에 사진·동영상 접근을 허용한다.
4. 원본 위치를 읽는 데 필요한 Android 권한 안내가 나오면 명시적으로 승인한다.
5. source album은 우선 `Camera/DCIM`으로 제한한다.
6. 품질은 `Original`, 위치 제거는 끔, 모바일 데이터는 끈다.
7. `Delete after transfer`, `Move`, 양방향 삭제 동기화는 모두 끈다.
8. 실행은 02:15 KST 전후의 일정 또는 충전 중 + Wi-Fi/VPN 조건으로 둔다.
9. Android 배터리 최적화에서 PhotoSync를 제외한다.
10. Mac의 기기별 전용 inbox로 보낸다.

권장 디렉터리:

```text
/Volumes/ExtData/02_Services/PhotosMcp/inbox/
└── android-phone/
    ├── incoming/
    ├── ready/
    └── rejected/
```

Mac 수신은 다음 두 단계로 접근한다.

1. 파일럿: 전용 SMB3 또는 SFTP 계정과 해당 inbox만 사용한다.
2. 운영: PhotosMcp의 Tailnet 전용 upload-only HTTPS/WebDAV endpoint로 축소한다.

SMB/SFTP를 사용한다면 Tailnet grant만 믿지 않는다. LAN에서 같은 포트가 열릴 수 있으므로 macOS firewall, 전용 계정, 공유 폴더 ACL도 적용한다. 인터넷에는 노출하지 않는다.

### FolderSync 대안

PhotoSync를 쓰지 않으면 Android FolderSync를 one-way upload로 시험할 수 있다.

- phone → Mac 단방향
- Sync deletions: OFF
- Move files: OFF
- 충돌: skip
- 원본 폴더 누락: 오류로 중단
- 충전 + Wi-Fi/VPN
- 실패 재시도: ON

FolderSync는 더 넓은 파일 권한을 요구할 수 있고 GPS 보존 계약이 PhotoSync만큼 명확하지 않으므로 표본 검증 없이 운영 전환하지 않는다.

## 수신 inbox 처리 계약

휴대폰이 파일을 복사했다고 즉시 분석하지 않는다.

```text
incoming
  → size/mtime가 두 번 연속 안정적인지 확인
  → 허용 MIME과 실제 magic bytes 비교
  → SHA-256 계산
  → 중복 ledger 확인
  → 원본 EXIF/촬영 시각/GPS 먼저 추출
  → sidecar와 병합하되 provenance 보존
  → ready로 원자 이동
  → 분석용 축소 이미지 생성
  → combined parent에 mobile_inbox child로 제출
```

안전 규칙:

- 임시 파일과 부분 업로드는 무시한다.
- symlink와 inbox root escape를 거부한다.
- 원본은 수정하지 않는다.
- 추천 derivative를 만들기 전에 metadata를 추출한다.
- 같은 content hash는 provider가 달라도 한 번만 분석한다.
- 휴대폰 전송 성공만으로 분석 완료로 기록하지 않는다.
- 분석 실패·시간 초과 파일은 carry-over한다.
- Apple·Google·mobile 결과는 하나의 parent와 Telegram 최종 메시지 한 건으로 합친다.

## 위치 모델과 표시 개선

### 위치 source 우선순위

| 순위 | source | 상태 |
|---:|---|---|
| 1 | 사용자가 Photos에서 확인·수정한 asset location | `user_confirmed` 또는 `confirmed_photokit` |
| 2 | 카메라 원본 EXIF/Android original | `confirmed_camera_gps` |
| 3 | Google Takeout `geoDataExif` | `confirmed_takeout_exif` |
| 4 | Google Takeout `geoData` | `provider_location` |
| 5 | 동일 content hash의 다른 provider anchor | `cross_provider_match` |
| 6 | 동일 scene·인접 시간의 일치 anchor | `same_scene_inferred` / `nearby_time_inferred` |
| 7 | OCR·앨범명·VLM landmark 후보 | `visual_candidate` |
| 8 | 소유자 장소 label 확인 | `user_confirmed_label` |
| 9 | 근거 없음 | `unknown` |

사용자가 장소명만 확인했을 때 임의의 정확 좌표를 만들지 않는다. `해운대 일대` label 확인과 위·경도 확인은 별도 상태다.

### 위치 충돌

Apple Photos에서 사용자가 수정한 위치와 카메라 EXIF 위치가 충분히 다르면 둘 중 하나를 덮어쓰지 않는다.

```text
library_asset_location
embedded_exif_location
location_conflict = true
canonical_location_source = pending_owner_review
```

소유자 화면에서 어느 위치를 쓸지 선택하도록 하고, 외부 공유는 해결 전까지 위치를 숨긴다.

### offline reverse geocoding

현재 소수 도시 중심점 상수는 시·군·구·해외 소도시 표현에 부족하다. 다음 단계에서는 GeoNames 데이터를 로컬 SQLite/R-tree로 인덱싱한다.

- 네트워크에 좌표를 보내지 않는다.
- 국가·admin1·admin2·도시를 지역별 privacy policy에 맞게 축약한다.
- 데이터 version과 attribution을 기록한다.
- 집·직장·학교·병원 등 민감 장소는 `개인 장소 · 상세 위치 숨김`으로 치환한다.
- 공개 Nominatim endpoint를 일일 batch reverse geocoder로 사용하지 않는다.

### 대상별 표시 수준

| 대상 | 기본 표시 |
|---|---|
| private DB | exact 좌표와 provenance |
| Mac 로컬 관리 UI | exact 좌표·원본/수정 위치·충돌 근거 |
| Tailnet owner Story | 시·군·구 또는 도시, exact 좌표는 접힘/선택 표시 |
| 30일 외부 공유 | 도시·광역권, 민감 장소 숨김 |
| thumbnail/preview/download derivative | EXIF/GPS 제거 |
| Telegram | 좌표와 상세 주소 없음 |
| LLM Story prompt | 이미 coarse 처리된 label과 confidence만 |

`Permissions-Policy: geolocation=()`은 웹페이지가 열람자의 현재 휴대폰 위치를 읽지 못하게 하는 설정이다. 저장된 사진 EXIF 수집을 막는 설정이 아니므로 유지한다.

## 위치가 없는 사진의 Story 구성

위치가 없다고 앨범 정보까지 빈 화면처럼 보여서는 안 된다. 현재 파이프라인에는 촬영일, 장면 설명, 이벤트 유형, 장면 cluster, 추천 순위, 품질, 추천 이유, provider가 있다.

기본 구성 축을 다음처럼 바꾼다.

```text
날짜
  → 시간대
    → 장면/event cluster
      → 대표 사진
      → 같은 장면의 보조 사진
      → 추천 이유와 출처
```

위치는 확인된 경우에만 보조 label로 추가한다.

모든 사진에 위치가 없을 때는 `위치 미상` chip과 소제목을 28번 반복하지 않는다. 위치 section을 숨기고 한 번만 다음과 같이 알린다.

> 이 사진 묶음에는 확인된 위치정보가 없어 촬영일과 장면을 기준으로 구성했습니다.

사진별 viewer에는 위치 대신 다음 정보를 우선 표시한다.

- 촬영 날짜와 시간대
- AI 장면 설명이라는 명시적 label
- 같은 장면에서 선택된 이유
- 추천 순위 또는 quality band
- Apple/Google/mobile provenance
- 위치 상태: 확인됨·추정·없음·검토 필요

VLM이 본 랜드마크는 장소 사실이 아니라 후보로만 저장한다.

## Gallery 안정화 설계

### 정식 Swiper를 바로 설치하지 않는 이유

정식 Swiper는 touch·pagination·zoom 구현 품질을 높일 수 있지만 JavaScript가 로드되지 않거나 preview가 실패하는 문제까지 해결하지 않는다. 먼저 사용자에게 항상 작동하는 HTML 경로를 제공해야 한다.

권장 3층 구조:

```text
1층  SSR grid
     각 사진이 실제 <a href="/photos/photo/{asset-id}"> 링크

2층  CSS scroll-snap viewer
     브라우저 native 수평 이동, JS 없이도 동작

3층  JavaScript enhancement
     선택 index, keyboard, history, caption, preload
     필요성이 확인되면 self-hosted Swiper를 이 층에만 도입
```

### P0 UI 변경

1. grid의 `<button>`을 사진별 SSR `<a>`로 바꾼다.
2. owner와 share 양쪽에 안전한 단일 사진 HTML route를 추가한다.
3. `<dialog>`을 필수 조건에서 제거하고 fixed viewer 또는 route viewer를 기본으로 둔다.
4. CSS scroll-snap을 touch 이동의 기준으로 사용한다.
5. preview에 loading, decode, error, retry, thumbnail fallback 상태를 추가한다.
6. browser Back으로 viewer를 닫고 원래 grid 위치와 focus를 복원한다.
7. `100vh` fallback과 `100dvh`, safe-area inset을 함께 적용한다.
8. JS/CSS URL의 `v=3` 고정값을 build/content hash로 교체한다.

### 이미지 요청 정책

- grid thumbnail은 Story 생성 시 background prewarm한다.
- 큰 preview도 작은 앨범은 prewarm하고, 큰 앨범은 현재 사진과 앞뒤 한 장만 우선 생성한다.
- 사진을 빠르게 넘길 때 이전 요청을 취소하거나 늦게 온 응답이 현재 사진을 덮지 않게 generation token을 사용한다.
- preview 실패 시 해당 thumbnail을 확대해 표시하고 재시도 버튼을 제공한다.
- wrap-around loop는 기본적으로 끈다.
- browser native lazy loading을 유지한다.

정식 Swiper가 필요해지면 고정 버전 JS/CSS와 license를 앱에 vendoring하고 CSP는 `self`만 유지한다. CDN은 사용하지 않는다.

### 필수 browser E2E

- Chromium mobile viewport
- WebKit/iPhone viewport
- JavaScript disabled
- `HTMLDialogElement` 또는 `showModal` 없음
- preview 첫 생성 지연
- preview 404와 decode 오류
- 빠른 연속 next/previous
- touch swipe와 edge/back gesture
- 화면 회전
- browser Back과 focus 복원
- Telegram 인앱 브라우저 실기기 smoke test
- owner/Tailnet과 외부 share/passcode 양쪽

## 단계별 구현 계획

### Phase 0 — 입력 순환 차단과 진단

- Apple daily discovery에서 managed output을 제외한다.
- destination receipt에 Apple asset ID와 origin lineage를 저장한다.
- `origin_provider`, `origin_asset_id`, `is_managed_output`, `metadata_provenance`를 도입한다.
- 위치 없음 사유를 `source_has_no_location`, `google_picker_unavailable`, `managed_derivative`, `extract_failed`, `label_unresolved`로 구분한다.
- 최근 10일 재실행에서 관리 출력 제출 수가 0인지 확인한다.

합격 기준:

- PhotosMcp 출력이 다음 Apple 신규 실행에 다시 제출되지 않는다.
- 정상 iPhone 원본을 album 이름만으로 오탐 제외하지 않는다.
- 하나의 content hash가 provider를 바꿔도 중복 분석되지 않는다.

### Phase 1 — Gallery progressive enhancement

- SSR 사진 route와 anchor를 구현한다.
- CSS scroll-snap viewer, loading/error/retry를 구현한다.
- 고정 cache version을 content hash로 교체한다.
- no-JS·mobile WebKit interaction E2E를 추가한다.

합격 기준:

- JavaScript가 꺼져도 모든 사진을 열 수 있다.
- dialog API가 없어도 큰 사진을 볼 수 있다.
- preview 실패 시 빈 검은 화면이 아니라 오류와 재시도 또는 thumbnail을 표시한다.
- Telegram 인앱 브라우저에서 grid → 큰 사진 → 다음/이전 → back이 동작한다.

### Phase 2 — Apple PhotoKit metadata 기준 경로

- signed PhotosMcp app 안에 read-only PhotoKit helper를 둔다.
- `PHAsset.location`, `creationDate`, resource list를 읽는다.
- iCloud-only 원본은 targeted network fetch를 사용한다.
- osxphotos/PhotoKit/original EXIF 집계를 비교한다.
- managed output은 helper 단계에서도 제외한다.

합격 기준:

- 위치가 확인된 iPhone 원본 표본 20장 모두에서 PhotoKit 위치가 수집된다.
- 원본 EXIF 위치와 PhotoKit 위치가 다르면 conflict로 남는다.
- 전체 원본을 미리 내려받지 않아도 선택된 iCloud-only 자산을 준비할 수 있다.

### Phase 3 — Android GPS sidecar bridge와 Google matcher

- [진행 중] ADB memory stream 실제 50쌍에서 A 23·B 25·C 2·오답 후보 0을 확인했다. A-only 95% gate를 위해 Google Picker `=d` transient fingerprint 재검증이 남아 있다. ADB는 운영 경로가 아니다.
- [완료·실기기 검증] Android Location Bridge의 수동 `지금 동기화` MVP를 구현했다.
- [완료] 단말 암호화 outbox, offline 지속성, 네트워크 복구 retry를 구현했다.
- [완료] append-only manifest endpoint와 device enrollment를 구현했다.
- Android manifest와 Google Picker 자산의 A/B/C/D matcher를 구현한다.
- A등급만 exact GPS private projection에 자동 연결한다.
- [완료·실기기 검증] JobScheduler 하루 한 번 persisted 증분 sync와 retry를 구현했다.

합격 기준:

- GPS가 실제 있는 camera 표본에서 sidecar 위치가 유지된다.
- 일반 camera 사진의 95% 이상이 유일 후보로 연결되고 false positive는 0건이다.
- burst·편집본 복수 후보를 자동 연결하지 않는다.
- 원본 사진 bytes가 기본 endpoint와 DB에 저장되지 않는다.
- retry 뒤에도 같은 manifest가 한 번만 반영된다.
- sidecar가 Google 사진보다 먼저 또는 나중에 와도 최종 연결된다.
- 휴대폰이 오프라인이어도 새벽 추천·스토리·HTML 생성은 정상 완료된다.
- 늦게 도착한 sidecar가 동일 collection revision에 위치를 후반영한다.

### Phase 3B — 선택적 PhotoSync mobile inbox

Android 원본의 별도 백업 또는 matcher 복구 경로가 필요할 때만 수행한다.

- `incoming → ready → rejected` 안정화 worker를 구현한다.
- local source가 축소 전에 EXIF/GPS를 추출하도록 보강한다.
- combined daily parent에 `mobile_inbox` child를 추가한다.
- Apple·Google·mobile 통합 dedupe와 단일 Telegram 결과를 검증한다.

### Phase 4 — 위치 표현 고도화

- combined run 단위 cross-provider anchor를 도입한다.
- coordinate 상태와 label resolution 상태를 분리한다.
- GeoNames offline resolver와 attribution을 추가한다.
- owner 위치 검토 inbox를 추가한다.
- OCR·VLM landmark 후보는 owner 확인 전까지 사실로 표시하지 않는다.

합격 기준:

- exact 좌표가 HTML source·로그·Telegram·LLM prompt에 노출되지 않는다.
- owner와 외부 share가 서로 다른 location projection을 사용한다.
- label 없는 exact GPS가 단순 `위치 미상`으로 잘못 처리되지 않는다.
- 모든 inference에 provenance와 confidence가 있다.

### Phase 5 — 도구 선택 재평가

PhotoSync 운영 결과를 2~4주 관찰한 뒤 다음 조건에서만 companion 앱을 검토한다.

- 상용 앱이 필요한 sidecar/provenance를 제공하지 않는다.
- 자동 재개·대용량 동영상·다중 기기 처리가 불안정하다.
- 제3자 앱의 전체 사진 접근을 허용하고 싶지 않다.
- PhotosMcp와의 status/receipt 통합을 휴대폰에서 직접 보여줘야 한다.

Immich는 다음 조건에서만 별도 ADR로 검토한다.

- PhotosMcp Story 외에 전체 사진 라이브러리용 모바일 timeline/map/search가 필요하다.
- 항상 켜진 Linux 서버와 정식 백업 운영이 가능하다.
- Immich를 원본 시스템으로 둘 것인지 역할을 명확히 정할 수 있다.

## 파일럿 테스트 세트

다음 표본은 사용자 사진의 실제 내용을 출력하지 않고 결과 개수·보존 여부만 기록한다.

| 표본 | 수량 | 확인 항목 |
|---|---:|---|
| GPS가 있는 iPhone camera JPEG/HEIC | 5 | PhotoKit·EXIF 위치, 촬영일, timezone |
| 위치를 Photos 앱에서 수정한 사진 | 3 | library location과 original EXIF 충돌 |
| iCloud-only 원본 | 3 | targeted download와 timeout/carry-over |
| GPS가 있는 Android camera | 5 | original EXIF·해시·해상도 |
| Android 편집본 | 5 | 원본/편집 provenance |
| screenshot·메신저 저장본 | 5 | 위치 없음이 정상으로 처리되는지 |
| Google Picker 사진 | 5 | `unavailable_from_google_picker` 유지 |
| Google Takeout sidecar | 3 | `geoDataExif`와 `geoData` 구분 |
| Live Photo 또는 motion pair | 3 | paired resource 보존 |

메타데이터 합격 검사는 `exiftool -n`으로 `DateTimeOriginal`, `OffsetTimeOriginal`, GPS, Make, Model을 확인하고, 전송 재시도 전후 파일 크기와 SHA-256을 비교한다. 테스트 보고서에는 좌표·파일명·원본 경로를 쓰지 않는다.

## 사용자가 먼저 확인할 사항

구현에 들어가기 전 사용자가 할 일은 세 가지뿐이다.

1. 최근 실제 iPhone Camera 사진 3~5장에서 정보 화면의 지도가 보이는지 확인한다.
2. iPhone `위치 서비스 → 카메라 → 앱을 사용하는 동안 → 정확한 위치`가 켜져 있는지 확인한다.
3. Android Bridge의 사진 전체 접근과 원본 위치 접근 권한을 유지한다. 최초 enrollment와 공개 경로 실기기 전송 검증은 완료했다.

PhotoSync 연결과 원본 inbox는 GPS sidecar matching feasibility가 실패하거나 Android 원본의 별도 백업도 원할 때만 설정한다. Android Bridge는 전체 원본 파일을 Mac에 보관하지 않는다.

## 이번 단계에서 하지 않는 것

- 휴대폰 전체 파일시스템을 Mac이 원격 탐색
- Google Photos 웹 DOM에서 위치를 scraping
- Google Picker를 GPS source로 취급
- 정확 GPS를 외부 geocoder 또는 LLM에 전달
- 공유 derivative에 EXIF/GPS 유지
- PhotoSync 전송 직후 원본 삭제
- SMB/SFTP를 공용 인터넷에 노출
- 이름만 보고 PhotosMcp 관리 자산이라고 판정
- 근거가 없는 VLM 장소를 확정 위치로 저장
- Swiper 도입만으로 viewer 문제를 해결했다고 판단

## 공식 참고 자료

### Apple

- PhotoKit: <https://developer.apple.com/documentation/PhotoKit>
- PHPhotoLibrary 권한: <https://developer.apple.com/documentation/photos/phphotolibrary>
- PHAsset location: <https://developer.apple.com/documentation/photos/phasset/location>
- PhotoKit privacy: <https://developer.apple.com/documentation/PhotoKit/delivering-an-enhanced-privacy-experience-in-your-photos-app>
- iCloud asset network access: <https://developer.apple.com/documentation/photos/phassetresourcerequestoptions/isnetworkaccessallowed>
- iCloud Photos 원본 다운로드: <https://support.apple.com/guide/photos/download-photos-to-your-mac-from-icloud-phtfa50fd1ec/mac>
- iPhone 사진 정보·위치 확인: <https://support.apple.com/guide/iphone/see-photo-and-video-information-iph0edb9c18f/26/ios/26>
- 사진 위치 메타데이터 관리: <https://support.apple.com/guide/personal-safety/manage-location-metadata-in-photos-ips0d7a5df82/web>
- BackgroundTasks: <https://developer.apple.com/documentation/BackgroundTasks>

### Google·Android

- Google Picker media item schema: <https://developers.google.com/photos/picker/reference/rest/v1/mediaItems>
- Google Picker media retrieval: <https://developers.google.com/photos/picker/guides/media-items>
- Google Photos API release notes: <https://developers.google.com/photos/support/release-notes>
- Google Photos 데이터 다운로드 metadata: <https://support.google.com/photos/answer/3024190>
- Android shared media·unredacted location: <https://developer.android.com/training/data-storage/shared/media>
- Android MediaStore: <https://developer.android.com/reference/android/provider/MediaStore>
- Android 14 selected photos access: <https://developer.android.com/about/versions/14/changes/partial-photo-video-access>
- Android WorkManager periodic work·constraints: <https://developer.android.com/develop/background-work/background-tasks/persistent/getting-started/define-work>
- Android JobScheduler: <https://developer.android.com/reference/android/app/job/JobScheduler>
- Android hardware-backed key attestation: <https://developer.android.com/privacy-and-security/security-key-attestation>

### 전송·자가 호스팅

- PhotoSync metadata 보존: <https://www.photosync-app.com/support/basics/answers/does-photosync-preserve-the-metadata-in-my-photos-and-videos>
- PhotoSync Android 자동 전송: <https://www.photosync-app.com/support/android/answers/android-autotransfer-howto>
- PhotoSync SMB: <https://www.photosync-app.com/support/nas/answers/how-to-transfer-photos-using-smb>
- FolderSync folder pair: <https://foldersync.io/docs/help/v2/folderpairs/>
- Syncthing folder types: <https://docs.syncthing.net/users/foldertypes.html>
- Immich mobile backup: <https://docs.immich.app/features/mobile-backup/>
- Immich remote access: <https://docs.immich.app/guides/remote-access/>
- Immich backup·original assets: <https://docs.immich.app/administration/backup-and-restore/>
- Tailscale grants: <https://tailscale.com/docs/features/access-control/grants>
- Tailscale Funnel 개요·제약: <https://tailscale.com/docs/features/tailscale-funnel>
- Tailscale Funnel CLI와 path routing: <https://tailscale.com/docs/reference/tailscale-cli/funnel>
- Cloudflare Tunnel outbound-only ingress: <https://developers.cloudflare.com/tunnel/>
- GeoNames download: <https://download.geonames.org/export/dump/>
- Nominatim public usage policy: <https://operations.osmfoundation.org/policies/nominatim/>

## 최종 채택안

다음 구현 순서를 기본 결정으로 삼는다.

```text
1. Apple managed-output feedback loop 차단
2. SSR 링크 + CSS scroll-snap viewer 안정화
3. iPhone PhotoKit metadata bridge와 20장 검증
4. 위치가 없는 Story의 날짜·장면 중심 표현 개선
5. Android↔Google Picker 50쌍 실측 결과를 바탕으로 `=d` strong fingerprint 재검증
6. loopback GPS write-only receiver·device signature·replay/rate/schema 보안 구현
7. [구현 완료·실기기 검증 완료] Android GPS Bridge encrypted offline outbox와 JobScheduler retry
8. [구현 완료] 보안 gate 통과 뒤 기존 Funnel `:8443/mobile-location` path와 signed fake payload 외부망 검증
9. late sidecar reconciliation·Google matcher·ambiguous owner review
10. offline GeoNames + owner location review
11. 원본 백업도 필요할 때만 PhotoSync/mobile_inbox
12. 필요성이 입증될 때만 범용 companion 또는 정식 Swiper 검토
```

이 순서는 사용자의 원본과 정확한 위치를 실제로 더 많이 확보하면서도, 이미 구현된 Apple Photos·Google Picker·추천 저장·Telegram·Tailscale 공유 구조를 최대한 재사용한다.
