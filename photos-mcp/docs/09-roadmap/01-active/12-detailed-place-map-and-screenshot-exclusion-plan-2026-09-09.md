# 상세 장소·Google 지도·캡처 제외 전환 계획

작성일: 2026-09-09
상태: 구현·운영 데이터 전환 완료, Android 실기기 지도 확인 대기
적용 범위: PhotosMcp 추천 저장소, Story 생성·공유, Android Companion, Apple Photos, Google Photos Picker

## 1. 이번 결정

PhotosMcp는 본인과 가족이 사용하는 서비스라는 전제에 맞춰 위치 표시 정책을 `상세 위치 허용`으로 전환한다. Tailnet 소유자 화면과 30일 가족 공유 Story 모두 실제 GPS 좌표를 지도 위치로 사용할 수 있고, 확인된 장소명도 숨기지 않는다. 기존의 `서울 일대`, `부산 일대`, `○○ (추정)` 같은 완곡한 표기는 더 이상 사용자 화면과 Story 문장에 사용하지 않는다.

위치 표시는 다음 순서로 결정한다.

1. 사진의 GPS가 Google Places의 명확한 장소와 일치하면 장소의 고유 이름을 쓴다. 예: `불국사`, `국립중앙박물관`, `해운대해수욕장`, `현대백화점 판교점`, `회사명`.
2. 명확한 장소를 고르기 어렵지만 역지오코딩이 성공하면 행정구역을 쓴다. 국내는 가능한 경우 `시·군·구` 조합으로 만들고, 불필요한 도로명·번지까지 제목에 붙이지 않는다.
3. 지도에는 사진의 실제 GPS 지점을 표시한다. 명확한 장소가 채택되면 Google Place ID를 사용하고, 그렇지 않으면 원 GPS 좌표를 사용한다.
4. GPS가 없고 주변 사진의 위치 근거도 서로 일치하지 않으면 장소를 만들어내지 않고 `위치 정보 없음`으로 둔다.
5. 내부 데이터에는 `confirmed_gps`, `contextual_estimate`, confidence, provenance를 계속 보존한다. 다만 사용자에게는 `일대`, `추정`이라는 단어를 노출하지 않는다. 문맥 위치는 높은 신뢰도 기준을 통과한 경우에만 자연스러운 장소명 또는 행정구역으로 표시하고, 기준 미달이면 표시하지 않는다.

캡처 이미지는 수집 직후, 이미지 분석 전에 제외한다. Apple·Google 어느 출처든 분석 비용, 추천 순위, 로컬 추천 보관소, Apple/Google 추천 앨범, Story와 공유 패키지에 들어가지 않게 한다. 원본 사진 보관함의 파일은 삭제하지 않는다.

## 2. 계획 수립 당시 구현에서 확인한 차이

### 위치

- `application/location_privacy.py`는 GPS를 받아도 오프라인 도시 중심점과 비교해 `○○ 일대`만 만든다.
- `RunRepository.get_recommendation_asset_location()`은 의도적으로 좌표를 제거한 projection만 반환한다.
- Story Evidence Builder는 `coarse_location`만 LLM에 전달하고, 공유 패키지도 `share_location`만 복사한다.
- Story 웹의 Content Security Policy는 `frame-src`가 없고 기본값이 `none`이어서 Google 지도를 현재 상태로는 넣을 수 없다.
- 현재 설정 파일과 launchd 자원에서 Google Maps/Places/Geocoding 전용 API 키 변수는 발견되지 않았다.

따라서 라벨 한 줄만 바꾸면 끝나지 않는다. 위치 resolver, 저장 모델, Story projection, 공유 projection, 지도 iframe 정책을 함께 바꿔야 한다.

### 캡처 이미지

- 수동 실행, 일일 실행, 통합 실행은 이미 `exclude_screenshots=true`를 기본값으로 보낸다.
- 실제 제외는 VLM 분석을 모두 마친 뒤 `photo_id`, 임시 경로, 장면 설명, note에 `screenshot`, `screen capture` 같은 영어 키워드가 있는지 확인하는 방식이다.
- 이 방식은 분석 비용을 먼저 사용하고, Samsung/Android의 `Screenshot_...`, `스크린샷 ...`, Photos 앱에서 다시 저장된 캡처, VLM이 단순히 문서·UI라고 설명한 캡처를 놓칠 수 있다.
- 설치된 `osxphotos`의 `PhotoInfo`에는 `screenshot`과 `screen_recording` 속성이 이미 있어 Apple Photos는 소스 단계에서 확정 판별할 수 있다.
- Google Picker 응답에는 원본 `filename`, 크기, 카메라 제조사·모델이 sidecar로 보존된다. 전용 screenshot 플래그는 없으므로 filename·provider metadata·이미지 신호를 조합해야 한다.

## 3. 위치 해석 설계

### 3.1 서버 측 처리

정확한 좌표는 Mac mini 서버에서만 Google 웹 서비스로 보낸다. 사진 바이트, 얼굴, 파일명, 장면 설명은 Google Maps Platform에 보내지 않는다.

한 Story의 GPS를 날짜·장면 기준 30m 안팎으로 먼저 묶고 대표 좌표만 조회한다. 최대 1,000장 실행에서도 사진마다 API를 한 번씩 호출하지 않게 하며, 같은 지점의 사진은 결과를 공유한다.

처리 순서는 다음과 같다.

```text
사진 GPS
  -> 같은 날짜·장면의 근접 좌표 군집화
  -> Geocoding API 역지오코딩
  -> Places API (New) Nearby Search
  -> 후보 Place Details 최소 필드 확인
  -> 명확한 POI 또는 시·군·구 선택
  -> Place ID + 원본 GPS를 Story 위치 참조로 저장
```

역지오코딩 결과에서는 `administrative_area_level_*`, `locality`, `sublocality`를 국가별 규칙으로 조합한다. 배열 순서나 첫 번째 문자열을 그대로 믿지 않고 component type을 기준으로 추출한다. Google은 역지오코딩 결과가 상세 주소부터 도시·주 같은 정치적 구역까지 여러 결과를 반환한다고 명시한다.

Places API는 필드 마스크를 최소화해 다음 값만 요청한다.

- `id`
- `displayName`
- `formattedAddress`
- `primaryType`, `types`
- `location`, `viewport`
- `businessStatus`
- `googleMapsUri`
- 지원되는 경우 `containingPlaces`

Google의 Nearby Search (New)는 좌표와 반경으로 장소 유형을 검색하며 응답 필드를 Field Mask로 명시해야 한다. 현재 공식 place type에는 `buddhist_temple`, `museum`, `art_museum`, `history_museum`, `beach`, `department_store`, `shopping_mall`, `corporate_office`, `historical_landmark`, `cultural_landmark` 등이 있다.

참고:

- [Google Geocoding API 역지오코딩](https://developers.google.com/maps/documentation/geocoding/reverse-geocoding)
- [Places API Nearby Search (New)](https://developers.google.com/maps/documentation/places/web-service/nearby-search)
- [Places API 최신 장소 유형](https://developers.google.com/maps/documentation/places/web-service/place-types)
- [Places API 응답 필드 선택](https://developers.google.com/maps/documentation/places/web-service/choose-fields)

### 3.2 POI 채택 규칙

단순히 가장 가까운 상호를 붙이지 않는다. 사진을 회사·백화점·사찰로 잘못 단정하는 것이 행정구역 표시보다 더 나쁜 결과이기 때문이다.

| 우선순위 | 조건 | 화면 표시 |
|---|---|---|
| 1 | 좌표가 장소의 containing place 또는 신뢰 가능한 viewport 안에 있고 허용 유형과 일치 | 장소의 `displayName` |
| 2 | 점형 장소는 매우 가까운 거리이고 장소 유형·영업 상태가 합리적 | 장소의 `displayName` |
| 3 | 해수욕장·공원·박물관 단지처럼 넓은 장소는 유형별 완화 반경 안에 있고 경쟁 후보가 없음 | 장소의 `displayName` |
| 4 | 장소 후보가 여러 개이거나 회사 건물 판정이 불명확 | 시·군·구 |
| 5 | 역지오코딩 실패 | 지도에는 GPS, 텍스트는 `위치 정보 없음` |

초기 임계값은 점형 건물 40m, 대형 시설 120m, 해수욕장·공원 250m로 시작하되 고정된 진리가 아니라 실제 가족 사진 표본으로 조정한다. `corporate_office`는 같은 건물에 여러 회사가 있을 수 있으므로 containing place 또는 건물 수준 일치가 없으면 회사명을 채택하지 않는다.

POI 후보가 채택되지 않아도 Google이 돌려준 가까운 상호를 Story 문장에 섞지 않는다. LLM에는 resolver가 최종 승인한 `display_label`만 전달하고, 원시 후보 목록은 전달하지 않는다.

### 3.3 저장 모델과 Google 정책

사용자 소유 데이터인 원본 GPS는 7자리 정밀도로 계속 저장할 수 있다. Google 결과에서는 장기 식별자로 `place_id`만 영구 저장한다. Google 공식 정책상 Place ID는 캐시 제한의 예외이며 재사용할 수 있고, 12개월 이상 된 ID는 갱신이 권장된다. 반면 Places·Geocoding 응답의 일반적인 사전 수집·장기 저장은 제한되므로 원시 응답 전체를 DB나 로그에 남기지 않는다.

권장 스키마는 다음과 같다.

```text
recommendation_asset_locations_private
  latitude_exact, longitude_exact              # 사용자 사진의 원본 GPS
  display_policy = family_detailed
  resolution_status = poi_verified | administrative | coordinate_only
  google_place_id                               # 장기 저장 가능
  poi_type
  provider_checked_at
  owner_label_source / share_label_source
```

`displayName`, `formattedAddress`, `googleMapsUri`는 영구 원시 응답으로 저장하지 않고 화면 요청 시 Place ID로 다시 가져오는 projection을 기본으로 한다. 30일 공유 Story에 장소 문구를 고정 저장할 필요가 생기면 구현 전에 현재 Google Maps Platform 계약의 허용 범위를 다시 확인하는 gate를 둔다. 지도 밖에 Google 장소 데이터를 보여줄 때 필요한 Google attribution도 함께 표시한다.

참고:

- [Places API 정책과 attribution](https://developers.google.com/maps/documentation/places/web-service/policies)
- [Geocoding API 정책과 attribution](https://developers.google.com/maps/documentation/geocoding/policies)
- [Place ID 저장과 갱신](https://developers.google.com/maps/documentation/places/web-service/place-id)

## 4. Story 내부 Google 지도

첫 구현은 Maps Embed API의 `place` 모드를 사용한다. Story 날짜 chapter마다 위치 칩을 보여주고, 칩을 선택하면 같은 화면 안의 지도가 해당 촬영 위치로 바뀐다.

```text
┌ 2026년 9월 3일 ───────────────────────┐
│ 불국사  24장   경주시  11장            │
│                                        │
│  [사진 grid / Story 문장]               │
│                                        │
│  ┌ Google 지도 ─────────────────────┐  │
│  │ 선택한 장소와 사진 위치 marker     │  │
│  └──────────────────────────────────┘  │
│  [Google 지도에서 열기]                 │
└────────────────────────────────────────┘
```

- 명확한 장소는 `q=place_id:...`로 표시한다.
- 행정구역만 있는 사진은 원 GPS 좌표를 `q`로 넘겨 marker를 표시한다.
- 지도는 `loading=lazy`로 현재 chapter 또는 사용자가 연 위치만 불러온다.
- 사진 1장마다 iframe을 만들지 않는다. 위치 칩이 하나의 지도 viewport를 바꾸게 해 비용과 메모리 사용을 제한한다.
- Android Companion의 Story WebView도 같은 Story URL을 사용하므로 별도 지도 화면을 중복 구현하지 않는다.
- 지도를 못 불러와도 Story·사진 뷰어는 그대로 동작하며 `Google 지도에서 열기` 링크를 fallback으로 제공한다.

Maps Embed API는 place name, 주소, 좌표 또는 Place ID를 iframe의 `q`로 받을 수 있다. 현재 Story CSP에는 `frame-src`가 없어 아래처럼 필요한 Google 지도 origin만 허용해야 한다.

```text
frame-src https://www.google.com https://maps.google.com;
```

기존 `default-src 'none'`, same-origin 이미지·스크립트, `frame-ancestors 'none'`은 유지한다. Google 지도 iframe에는 `referrerpolicy="strict-origin-when-cross-origin"`를 지정한다.

참고: [Google Maps Embed API](https://developers.google.com/maps/documentation/embed/embedding-map)

## 5. API 키와 접근 정책

가족용 서비스라고 해도 API 키는 사진 위치와 별개의 비용·권한 자산이므로 공개하지 않는다. 두 키를 분리한다.

| 키 | 사용 위치 | 제한 |
|---|---|---|
| `PHOTOS_MCP_GOOGLE_MAPS_SERVER_API_KEY` | Mac mini의 Geocoding·Places 호출 | server IP 제한이 가능하면 적용, Geocoding API와 Places API (New)만 허용 |
| `PHOTOS_MCP_GOOGLE_MAPS_EMBED_API_KEY` | Story의 Google Maps iframe | Maps Embed API만 허용, `byoungyoung-macmini.tail53bcc7.ts.net/*`와 실제 30일 공유 host를 Website referrer로 제한 |

키 값은 repository, Story manifest, 로그, Telegram에 쓰지 않는다. launchd에는 환경 파일 경로 또는 Keychain 조회만 전달한다. Google도 앱별 키 분리, application restriction과 API restriction의 동시 적용을 권장하며, Maps Embed에는 별도 키를 만들어 Embed API에만 제한하는 방식을 권장한다.

상세 위치 허용은 PhotosMcp의 가족 공유 정책이며 Google Maps Platform의 사용 조건을 없애지는 않는다. Story에서 지도를 열면 Place ID 또는 GPS와 접속 origin이 Google에 전달될 수 있음을 가족용 개인정보 안내에 적고, 공유 host에 짧은 `/privacy`와 `/terms` 페이지를 제공한다. Story footer에는 Google 지도 attribution을 유지한다.

참고: [Google Maps Platform API 보안 권장사항](https://developers.google.com/maps/api-security-best-practices)

2026-09-09에 서버용 키와 Embed용 키를 분리해 등록했다. 서버 키는 Geocoding API와 Places API (New)에만, Embed 키는 Maps Embed API와 운영 Tailnet/Funnel referrer에만 제한했다. 두 값은 macOS Keychain에만 저장하며 저장소·DB·Telegram에는 남기지 않는다. Mac mini의 인터넷 egress가 유동 IP라 서버 키에는 현재 API 제한만 적용했고, 향후 고정 egress가 마련되면 IP 제한을 추가한다.

## 6. 캡처 이미지 제외 설계

### 6.1 공통 판별 결과

각 후보는 분석 전에 아래 값을 갖는다.

```text
content_kind = camera_photo | screenshot | screen_recording_frame | document_image | unknown
content_kind_source = apple_photokit | provider_filename | metadata | visual_classifier
content_kind_confidence = 0.0 .. 1.0
exclusion_reason = screenshot
```

`screenshot` 또는 `screen_recording_frame`이 확정되면 VLM queue에 넣지 않는다. 제외 이력은 개수와 reason code만 작업 결과에 남기고, 파일명이나 화면 OCR 텍스트는 Telegram과 일반 로그에 남기지 않는다.

### 6.2 출처별 판별

Apple Photos는 `PhotoInfo.screenshot == true`를 최우선으로 사용한다. 이 값이 있으면 filename이나 VLM 결과와 관계없이 제외한다. `screen_recording`은 비디오 정책과 함께 제외한다.

Google Photos Picker와 local 파일은 다음 증거를 순서대로 사용한다.

1. sidecar의 원본 filename: `Screenshot_`, `Screenshot-`, `Screen Shot`, `스크린샷`, `스크린 캡처`, `SmartSelect`, `Screenshot_YYYYMMDD` 등 Android·Samsung·Pixel 패턴
2. provider metadata: 카메라 make/model 부재, PNG/WebP 형식, 휴대폰 화면 해상도 계열
3. 이미지 구조: status/navigation bar, 앱 chrome, 긴 문서 캡처, 고밀도 OCR 텍스트, UI 아이콘 반복
4. VLM의 구조화된 `content_kind=screenshot` 판정

파일 확장자, GPS 부재, 세로 비율 하나만으로는 제외하지 않는다. 메신저에서 받은 이미지, 카메라로 찍은 문서, 편집한 사진이 오탐될 수 있기 때문이다. filename/native flag는 단일 강한 증거로 허용하고, 나머지는 두 개 이상의 신호 또는 높은 구조화 분류 confidence를 요구한다.

분석 뒤에는 현재 텍스트 keyword 검사를 보조 안전망으로 유지하되 한글 키워드와 구조화 필드를 추가한다.

### 6.3 기존 추천과 Story 정리

새 규칙은 신규 실행뿐 아니라 이미 추천된 캡처에도 적용한다.

1. 추천 보관소와 현재 Story의 managed copy를 검사해 `dry-run` 보고서를 만든다.
2. 확정된 캡처만 recommendation member와 날짜 그룹에서 제외한다.
3. 해당 자산이 다른 정상 사진과 같은 content hash를 공유하지 않을 때만 PhotosMcp가 만든 로컬 추천 사본과 파생 공유 JPEG를 제거한다.
4. Apple/Google 원본은 삭제하지 않는다.
5. 영향받은 Story와 공유 package를 새 revision으로 재생성하고, 추천 앨범은 add-only 원칙을 깨지 않도록 별도의 `remove_screenshot_members` 명시 작업으로 동기화한다.
6. 제거 전후 개수와 Story revision만 검증 보고서에 남긴다.

## 7. 구현 순서

### Phase A — 캡처 선제 제외

- 공통 `ScreenCaptureDetector`와 판별 결과 모델 추가
- Apple loader에서 `PhotoInfo.screenshot`을 읽어 분석 전 제외
- Google/local loader에서 sidecar filename과 metadata를 분석 전 검사
- VLM 응답에 구조화된 `content_kind`를 추가하고 post-analysis backstop 유지
- daily/manual/Telegram/Android 모든 진입점에서 `exclude_screenshots=true` 강제 기본값 유지
- 제외 개수와 사유를 통합 완료 결과에 포함

이 단계는 Google Maps 키 없이 바로 구현·검증할 수 있고, 원치 않는 캡처 추천을 가장 먼저 막는다.

### Phase B — Google 위치 resolver

- `GoogleLocationResolver` port와 HTTP adapter 추가
- 30m 위치 군집, rate limit, timeout, retry/backoff, circuit breaker 구현
- Geocoding 행정구역 parser와 POI allowlist/scorer 구현
- Place ID 중심 저장 migration 추가
- Google API 실패 시 기존 GPS와 행정구역 fallback으로 정상 완료

### Phase C — Story·공유 projection과 지도

- owner/share 위치 projection에 exact GPS, resolution status, Place ID를 허용
- LLM Evidence Builder에는 최종 승인 label만 전달
- Story와 공유 package의 위치 chip·지도 view model 추가
- Maps Embed iframe과 `Google 지도에서 열기` fallback 추가
- CSP를 Google 지도 frame origin에 한해 확장
- Android WebView에서 지도 로드·복귀·화면 회전·Story 사진 viewer와의 충돌 검증

### Phase D — 기존 데이터 재처리

- 기존 exact GPS 75건을 시작으로 Google location enrichment 실행
- 기존 `○○ 일대`, `(추정)` 라벨을 새 display policy로 재생성
- 기존 추천 캡처 dry-run 후 managed recommendation과 Story에서 제거
- 활성 30일 공유 Story를 새 revision으로 재발행
- 원본과 기존 분석 결과는 유지하고, 필요한 Story projection만 재생성

### Phase E — 운영 전환

- Google API quota와 일별 비용 상한 설정
- 공유 host의 `/privacy`, `/terms`와 Story footer의 Google attribution 확인
- Telegram 완료 메시지는 통합 결과 1건만 유지하며 `지도 반영 수`, `행정구역 표시 수`, `캡처 제외 수`를 추가
- 키·좌표·원시 Google 응답이 로그에 없는지 검사
- launchd 새벽 3시 실행과 Android 수동 날짜 실행을 각각 실데이터로 검증

## 8. 테스트 계획과 완료 기준

### 자동 테스트

- Apple `screenshot=true`는 VLM 호출 전에 제외된다.
- Google filename의 Samsung·Pixel·한글 캡처 패턴이 제외된다.
- GPS 없음, PNG, 세로 화면비 하나만으로 일반 사진을 오탐하지 않는다.
- 캡처는 recommendation storage, destination album, Story, shared package 어디에도 나타나지 않는다.
- 동일 좌표 군집은 Google lookup 한 번만 수행한다.
- 불국사·박물관·백화점·해수욕장·회사 확정 fixture는 고유 장소명을 선택한다.
- 경쟁 POI fixture와 회사 건물 불확실 fixture는 시·군·구로 fallback한다.
- 사용자 노출 HTML·JSON·Android 문자열에 `일대`, `추정`이 없다.
- Google 장애·quota 초과·timeout에도 사진 작업 전체는 실패하지 않는다.
- Story CSP는 Google map iframe만 허용하고 임의 iframe은 차단한다.
- server/embed 키가 API 응답, HTML 소스의 server key, DB, 로그, Telegram에 없다. Embed key는 referrer+API 제한된 전용 키만 사용한다.

### 통합·실데이터 테스트

1. 알려진 사찰·박물관·백화점·해수욕장 사진과 일반 주거 지역 사진 각 2장 이상으로 label을 사람 검토한다.
2. 실제 Story에서 사진 위치 marker와 장소 칩이 맞는지 확인한다.
3. Tailnet owner URL과 30일 family share URL에서 지도가 모두 열리는지 확인한다.
4. Android WebView에서 위치 칩 선택, 지도 조작, Google 지도 앱/웹 열기, 뒤로 가기를 확인한다.
5. 실제 휴대폰 캡처 10장과 일반 사진 20장으로 recall/오탐을 기록한다. 목표는 확정 캡처 recall 100%, 일반 사진 오탐 0건이다.
6. 현재 Story를 재생성해 GPS가 확보된 사진에는 위치가 표시되고, 캡처가 추천 grid에서 사라지는지 확인한다.

완료 조건은 다음과 같다.

- 확정 GPS 사진은 장소명 또는 시·군·구와 Google 지도 marker를 가진다.
- 유명하고 명확한 장소만 이름으로 표시하며 애매한 장소는 행정구역으로 내려간다.
- 화면과 Story 문장 어디에도 `일대`, `추정`을 쓰지 않는다.
- 캡처 이미지는 분석·추천·보관·앨범·Story 전 구간에서 제외된다.
- Apple/Google 원본은 변경하거나 삭제하지 않는다.
- 자동 새벽 실행과 Android 수동 실행이 같은 정책을 사용한다.

## 9. 승인 후 바로 진행할 작업

1. Phase A 캡처 선제 제외와 테스트를 먼저 완료한다.
2. API 키가 준비되는 동안 Google resolver의 fake adapter·schema·테스트를 구현한다.
3. 사용자가 제한된 Google server/embed 키를 발급하면 live resolver와 Maps Embed를 연결한다.
4. 현재 추천/Story에 대해 캡처 cleanup dry-run과 위치 enrichment preview를 먼저 보여준다.
5. preview 확인 후 managed copy 정리, Story 재발행, launchd/Android 통합 테스트까지 마무리한다.

## 10. Google Maps Platform API 키 발급 안내

### 10.1 프로젝트와 결제

1. [Google Cloud Console](https://console.cloud.google.com/)에 로그인한다.
2. 상단 프로젝트 선택기에서 기존 Google Photos Picker에 사용한 PhotosMcp 프로젝트를 선택한다. Maps 비용·권한을 완전히 분리하고 싶을 때만 별도 `photosmcp-maps` 프로젝트를 만든다.
3. [결제 연결 화면](https://console.cloud.google.com/billing/linkedaccount)에서 선택한 프로젝트에 결제 계정을 연결한다. Google Maps Platform API는 무료 범위 안에서 사용하더라도 결제 연결이 필요하다.
4. 결제의 Budgets & alerts에서 개인용 초기 예산 경보를 설정한다. 예산 경보는 알림이며 API 사용을 자동 중지하는 hard cap은 아니므로 API별 quota도 별도로 설정한다.

### 10.2 API 세 개 활성화

[API Library](https://console.cloud.google.com/apis/library)에서 아래 이름을 차례로 검색하고 `사용` 또는 `Enable`을 누른다.

- `Places API (New)` (`places.googleapis.com`)
- `Geocoding API`
- `Maps Embed API` (`maps-embed-backend.googleapis.com`)

이미 활성화된 API는 `사용` 대신 `관리`가 보인다. `Places API` legacy가 아니라 반드시 `Places API (New)`를 활성화한다.

### 10.3 서버 위치 조회 키

1. [Google Maps Platform Credentials](https://console.cloud.google.com/google/maps-apis/credentials)로 이동한다.
2. `Create credentials` → `API key`를 선택한다.
3. 생성된 키의 `Edit API key`를 열고 이름을 `PhotosMcp Location Server`로 바꾼다.
4. `Application restrictions`는 Mac mini의 공인 egress IP가 고정이면 `IP addresses`를 선택하고 그 공인 IPv4/IPv6를 넣는다. `127.0.0.1`이나 Tailscale `100.x` 주소를 넣으면 안 된다. Google이 보는 주소는 인터넷으로 나가는 공인 IP다.
5. 공인 IP가 유동이면 최초 연동 동안 application restriction을 비워 두고, 아래 API restriction과 낮은 quota를 먼저 적용한다. 운영 전에는 고정 egress IP를 마련하거나 현재 공인 IP 제한을 적용하고 변경 감시를 붙인다.
6. `API restrictions`에서 `Restrict key`를 선택하고 `Places API (New)`와 `Geocoding API` 두 개만 허용한다.
7. 저장한다. 제한 전파에는 몇 분이 걸릴 수 있다.

이 키가 `PHOTOS_MCP_GOOGLE_MAPS_SERVER_API_KEY`가 된다. Story HTML과 APK에는 넣지 않는다.

### 10.4 Story 지도 표시 키

1. 같은 Credentials 화면에서 `Create credentials` → `API key`를 다시 선택한다.
2. 이름을 `PhotosMcp Story Maps Embed`로 바꾼다.
3. `Application restrictions`에서 `Websites` 또는 `HTTP referrers`를 선택한다.
4. 현재 운영 경로를 다음처럼 등록한다.

```text
https://byoungyoung-macmini.tail53bcc7.ts.net
https://byoungyoung-macmini.tail53bcc7.ts.net/*
https://byoungyoung-macmini.tail53bcc7.ts.net:8443
https://byoungyoung-macmini.tail53bcc7.ts.net:8443/*
```

`443` 기본 포트는 URL에 포트를 쓰지 않은 첫 두 항목이 담당하고, 30일 공유 Story Funnel은 `:8443` 두 항목이 담당한다. Hermes Dashboard `:9119`에도 지도를 넣게 될 때만 다음 두 항목을 추가한다.

```text
https://byoungyoung-macmini.tail53bcc7.ts.net:9119
https://byoungyoung-macmini.tail53bcc7.ts.net:9119/*
```

5. `API restrictions`에서 `Restrict key`를 선택하고 `Maps Embed API` 하나만 허용한다.
6. 저장한다.

이 키가 `PHOTOS_MCP_GOOGLE_MAPS_EMBED_API_KEY`가 된다. iframe URL에는 이 키가 보일 수 있지만 referrer와 API 종류가 제한되어 다른 사이트나 Google API에는 사용할 수 없게 한다.

Android Companion은 Tailnet HTTPS Story를 WebView로 여는 구조이므로 이 키에 Android package/SHA-1 제한을 적용하지 않는다. 브라우저 제한 키에는 한 종류의 application restriction만 적용할 수 있고, 이 호출의 실제 client는 Android SDK가 아니라 Story 웹 origin이다.

macOS 앱의 내장 Story도 `PHOTOS_MCP_OWNER_STORY_URL`의 Tailnet HTTPS 주소를 우선 사용한다. 따라서 지도 요청의 referrer는 운영 허용 목록과 같아지고, 임의 포트의 `127.0.0.1` 때문에 Embed API가 거부되는 문제를 피한다. 해당 환경값이 명시적으로 빈 경우에만 daemon의 loopback Story를 fallback으로 사용한다. loopback 주소를 직접 지도 표시용으로 쓸 운영 요구가 생기면 `http://127.0.0.1:18791/*`를 Embed 키 허용 referrer에 별도로 추가해야 한다.

### 10.5 Mac Keychain에 임시 보관

키를 채팅, Telegram, 문서, shell history에 붙여 넣지 않는다. 발급 직후 Mac 터미널에서 아래 명령을 실행하면 `security`가 값을 숨겨서 입력받는다.

```bash
security add-generic-password -U -a "$USER" -s "photos-mcp-google-maps-server-api-key" -w
security add-generic-password -U -a "$USER" -s "photos-mcp-google-maps-embed-api-key" -w
```

첫 번째 prompt에는 서버 키를, 두 번째 prompt에는 Embed 키를 붙여 넣고 Enter를 누른다. 명령의 마지막 `-w` 뒤에 키를 직접 적지 않는다. 키 조회 여부는 실제 값을 출력하지 않고 다음처럼 확인한다.

```bash
security find-generic-password -a "$USER" -s "photos-mcp-google-maps-server-api-key" >/dev/null && echo "server key: registered"
security find-generic-password -a "$USER" -s "photos-mcp-google-maps-embed-api-key" >/dev/null && echo "embed key: registered"
```

PhotosMcp runtime은 `infrastructure/google_location.py`에서 두 Keychain 항목을 직접 읽는다. launchd에는 키 값이 아니라 위치 해석 활성화 플래그만 전달한다.

### 10.6 초기 quota 권장값

개인용 첫 운영은 위치 군집화가 정상 동작하는지 확인할 때까지 다음과 같이 낮게 시작한다.

- Geocoding: 일 1,000 요청 이하
- Places Nearby Search: 일 1,000 요청 이하
- Place Details: 일 1,000 요청 이하
- 서버 내부 동시 호출: 최대 2개
- HTTP 429 또는 quota 오류: 사진 작업을 실패시키지 않고 행정구역/offline fallback으로 완료

Google Cloud의 `APIs & Services` → 각 API → `Quotas & System Limits`에서 조정한다. Maps Embed API는 현재 공식 안내상 요청 요금이 없지만 유효한 키와 결제 계정은 필요하다. Places·Geocoding은 요청 필드와 월간 사용량에 따라 과금될 수 있으므로 최신 [Google Maps Platform 가격표](https://developers.google.com/maps/billing-and-pricing/pricing)를 기준으로 확인한다.

## 11. 2026-09-09 구현 및 데이터 전환 결과

### 완료된 구현

- Apple `PhotoInfo.screenshot`/`screen_recording`과 Google Picker 원본 filename sidecar를 이용하는 분석 전 screen-capture detector를 추가했다.
- Samsung·Pixel 계열 영문 패턴과 `스크린샷`, `화면 캡처`, `화면 기록` 한글 패턴을 지원한다.
- 기존 결과 텍스트 필터는 2차 안전망으로 유지하되 한글 키워드를 보강했다.
- Google Geocoding + Places API (New) resolver, 30m process cache, 2회 요청, timeout, 3회 연속 실패 circuit breaker를 구현했다.
- POI는 허용 유형과 거리·경쟁 후보 기준을 통과할 때만 채택하며, 나머지는 행정구역으로 표시한다.
- 위치 테이블에 `resolution_status`, `google_place_id`, `poi_type`, `provider_checked_at` migration을 적용했다.
- 가족용 owner/share projection에 원본 GPS와 Place ID를 전달하고 Story chapter별 Google 지도와 외부 지도 열기 링크를 추가했다.
- CSP는 Google 지도 frame origin만 추가 허용하고 기존 same-origin·frame ancestor 제한은 유지했다.
- `/privacy`, `/terms`, `/photos/privacy`, `/photos/terms` 안내 페이지와 Google attribution footer를 추가했다.
- 운영 앱과 mobile-client launchd는 Keychain adapter를 사용하도록 전환했다.

### 운영 데이터 전환

- exact GPS 75건을 Google resolver로 재처리했다: `poi_verified` 33건, `administrative` 42건, 실패 0건.
- 기존 문맥 위치 15건은 새 anchor label로 다시 만들었다.
- 활성 Story 2개를 새 revision으로 갱신했으며 사용자 노출 데이터에서 `일대`, `추정` 표기는 0건이다.
- 기존 관리 추천 97장 중 Google Picker 원본 filename으로 확정된 캡처 3장을 발견했다.
- 확정 캡처의 PhotosMcp 관리 사본만 `~/.photos-mcp/runtime/quarantine/screenshots/20260909T081658Z`로 이동했고 Google Photos 원본은 삭제하지 않았다.
- 추천 보관소와 활성 Story는 94장으로 재구성됐다. 정리 후 재검사에서 확정 캡처는 0건이다.

### 검증 상태

- 공개 좌표를 이용한 live API 검사에서 행정구역과 POI 조회가 모두 성공했고 경복궁 좌표는 `경복궁`/`cultural_landmark`로 확정됐다.
- 자동 테스트에서 30m 조회 재사용, 애매한 POI 행정구역 fallback, Apple/Google 선제 제외, 공유 GPS projection, Maps Embed HTML과 referrer policy를 검증한다.
- 남은 수동 검증은 Android WebView에서 실제 지도 로드·위치 칩 전환·외부 Google 지도 열기·뒤로 가기 확인이다. 현재 사용자가 원격 접속 중이고 ADB를 사용할 수 없으므로 실기기 확인은 다음 연결 시 수행한다.

### Google Cloud 키 등록과 제한

- 프로젝트 `ai-assistant-local-490105`에서 Places API (New), Geocoding API, Maps Embed API 활성화를 확인했다.
- `PhotosMcp Location Server` 키는 Places API (New)와 Geocoding API에만 제한했다. Mac mini의 공인 egress IP가 유동이므로 application restriction은 적용하지 않고 API 제한과 서버 측 요청 절감·circuit breaker를 사용한다.
- `PhotosMcp Story Maps Embed` 키는 Maps Embed API 하나에만 제한하고 Tailnet owner host와 `:8443` 가족 공유 host만 HTTP referrer로 허용했다.
- 두 키는 macOS Keychain의 `photos-mcp-google-maps-server-api-key`, `photos-mcp-google-maps-embed-api-key` 서비스에 저장했다. 문서·DB·launchd·로그에는 키 값을 쓰지 않는다.
- 등록 과정에서 화면에 노출된 최초 Embed 키는 사용 전에 즉시 삭제했고, 새 키로 교체해 제한과 Keychain 등록을 다시 완료했다.

### 배포와 live endpoint 확인

- 새 standalone `PhotosMcp.app`을 빌드·서명해 `/Volumes/ExtData/02_Services/PhotosMcp/PhotosMcp.app`에 설치하고 `/Applications/PhotosMcp.app` 링크를 유지했다.
- PhotosMcp daemon은 loopback `127.0.0.1:18791`, Android mobile-client BFF는 `127.0.0.1:18794`에서 실행한다. mobile-client launchd에는 키가 아니라 `PHOTOS_MCP_GOOGLE_LOCATION_ENABLED=1`만 설정했다.
- Tailnet owner의 `/photos`, `/photos/privacy`, `/photos/terms`, `/mobile-client/v1/capabilities`는 모두 HTTP 200으로 확인했다.
- owner Story HTML에서 Google 지도 iframe 5개, Google frame origin만 허용한 CSP, `strict-origin-when-cross-origin` referrer policy를 확인했다. 사용자 노출 HTML의 `일대`, `추정` 표기는 0건이다.
- `:8443/privacy`, `:8443/terms` 공개 안내 페이지도 HTTP 200이다. 현재 활성 30일 공유 링크는 없어 공유 Story 본문의 지도는 다음 공유 생성 시 같은 renderer로 최종 확인한다.
- 다음 통합 실행부터 최종 Telegram 결과 1건에 `지도 반영`, `장소명`, `행정구역`, `캡처 제외` 장수를 함께 표시한다. Apple/Google 자식 작업은 기존대로 중간 결과 메시지를 따로 만들지 않는다.
- 전체 자동 테스트는 `867 passed`이며 키 값이나 Google 원시 응답을 출력하지 않는 방식으로 Keychain·endpoint·최근 서비스 로그를 점검했다.
- Keychain의 현재 두 키와 정확히 같은 문자열은 소스 저장소·PhotosMcp DB·runtime·서비스 로그에서 0건이다. Google Picker 전용 Chrome 프로필에는 Google 자체 웹앱이 사용하는 다른 `AIza...` 문자열이 존재하지만 PhotosMcp의 두 키와는 일치하지 않는다.
- 테스트용 중복 앱 실행이 거부된 경우 Google 위치 활성화 환경변수가 호출 프로세스에 남지 않도록, 실제 daemon이 단일 인스턴스 lock을 확보한 뒤에만 활성화하도록 수명 주기를 수정했다.
