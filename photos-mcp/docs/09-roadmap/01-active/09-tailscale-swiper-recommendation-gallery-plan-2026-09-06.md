# Tailscale 추천 사진 생성형 Story Album·Swiper 리포트 계획

## 문서 상태

- 작성일: 2026-09-06
- 상태: 30일 외부 공유·안전한 다운로드, 근거 기반 Story Director와 위치 문맥 추론 구현 완료
- 구현 상태: 공유 Vertical Slice, 날짜·위치 StoryManifest, private 위치 원장, Tailnet 결과 알림과 공유 정보 분리 전달 구현 및 회귀 검증 완료
- 대상 저장소: PhotosMcp, HermesAgent
- 목표: Telegram에서 전달한 Tailscale 링크를 열면 LLM이 추천 사진의 날짜·위치·시각 분석 증거를 바탕으로 편집한 Storyboard를 읽고, 여러 장의 contact grid와 선택형 Swiper viewer에서 사진별 정보를 확인한다. 소유자가 명시적으로 선택하면 같은 Story revision에서 공개 가능한 내용만 묶은 만료형 HTML Share Package를 만들어 Tailscale이 없는 다른 사람도 안전하게 볼 수 있게 한다.

이 문서는 설계 결정과 실제 구현 결과를 함께 추적한다. 2026-09-06에는 추천 사진을 외부 수신자에게 30일 동안 공유하고 안전한 웹용 사본을 내려받게 하는 Vertical Slice를 운영 반영했다. 같은 날 2차 작업으로 Qwen 기반 Story Director, 근거 해시와 날짜 chapter를 구현하고 실데이터에 백필했다. 이어 3차 작업에서 provider/EXIF GPS를 일반 결과 JSON과 분리한 private 원장, 좌표 없는 도시 단위 projection, 공유 전용 위치 projection과 Telegram Tailnet 결과 링크를 구현했다. 4차 작업에서는 offline 도시·시간대 사전을 넓히고, GPS가 없는 사진을 같은 장면 또는 2시간 이내의 일치하는 GPS 근거로만 보수적으로 추정하며, 날짜 안에서 위치별 소장으로 나누는 Story Album을 완성했다.

## 2026-09-06 구현 결과

이번 작업에서 완료한 운영 범위는 다음과 같다.

| 영역 | 반영 결과 |
|---|---|
| 소유자 화면 | Tailnet 전용 `https://byoungyoung-macmini.tail53bcc7.ts.net/photos`에 현재 추천 12장의 반응형 contact grid와 큰 사진 viewer를 제공한다. 활성 공유 목록에서 링크를 다시 열거나 언제든 공유를 종료할 수 있다. |
| 외부 공유 | 소유자가 명시적으로 생성한 immutable `SharedStoryPackage`만 `https://byoungyoung-macmini.tail53bcc7.ts.net:8443/s/{share_id}`에서 공개한다. |
| 기본 만료 | 기간 입력을 생략하면 30일이며, 서버에서도 30일을 상한으로 강제한다. |
| 인증 | 공유별 6자리 passcode는 PBKDF2-SHA256 hash로만 저장하고, 성공 시 12시간짜리 `Secure`, `HttpOnly`, `SameSite=Lax` 서명 session을 발급한다. 15분 동안 5회 실패하면 일시 제한한다. |
| 다운로드 | 기본 허용 상태이며 사진별 `사진 저장`으로 sRGB JPEG 사본을 내려받는다. 긴 변 최대 2048px, 품질 88, EXIF/GPS 제거, 중립 파일명 `photo-NNN.jpg`를 사용한다. 원본 route와 일괄 ZIP은 제공하지 않는다. |
| 폐기 | 소유자가 즉시 폐기하면 기존 session도 410 응답으로 무효화하고 해당 공유의 파생 이미지 cache를 제거한다. |
| 공개 경계 | 기존 443 Open WebUI·`/photos`와 9119 Hermes Dashboard는 Tailnet 전용으로 유지하고, 8443의 별도 loopback 공개 앱만 Funnel로 연결한다. 공개 앱에는 `/health`, MCP, 소유자 관리 route가 없다. 소유자 Tailscale login exact allow-list는 권한 0600의 runtime 파일에도 저장해 로그인 세션 환경변수가 사라진 뒤에도 유지한다. |
| 개인정보 | 잠금 전에는 Story 제목·사진·위치를 노출하지 않는다. 외부 HTML에는 내부 `local_asset_id`를 넣지 않고 공유별 난수 asset ID만 사용한다. HTML과 이미지는 `no-store`, `no-referrer`, `noindex/noimageindex` 정책을 적용한다. |
| UI | 2/3/4열 반응형 grid, modal viewer, 좌우 버튼·키보드·touch swipe, 사진별 저장을 구현했다. CDN 의존성을 없애기 위해 이번 1차 버전은 Swiper와 같은 상호작용을 하는 로컬 경량 controller를 사용한다. 실제 Swiper 패키지 도입은 zoom·pagination을 확장할 때 vendored asset으로만 검토한다. |

### 운영 연결 상태

```text
Tailnet only  https://byoungyoung-macmini.tail53bcc7.ts.net/         -> Open WebUI :3000
Tailnet only  https://byoungyoung-macmini.tail53bcc7.ts.net/photos  -> PhotosMcp owner :18791/photos
Tailnet only  https://byoungyoung-macmini.tail53bcc7.ts.net:9119/   -> Hermes Dashboard :9120
Public Funnel https://byoungyoung-macmini.tail53bcc7.ts.net:8443/   -> Share Gateway :18792
```

### 검증 결과

- 전체 자동 테스트: `764 passed`
- Markdown 문서 검증: 73개 통과
- standalone 앱: build, deep code-sign verification, health, runtime import smoke, vendored runtime smoke 통과
- 실제 운영 URL: Open WebUI 200, Hermes Dashboard 302, 소유자 갤러리 200, 공개 gateway의 허용되지 않은 `/`와 `/health`는 404
- 실제 30일 기본 공유: 생성 201, 잠금 200, 해제 303, 갤러리 200, 추천 사진 12장 표시
- 실제 다운로드: JPEG 1536×2048, EXIF entry 0, `attachment; filename="photo-001.jpg"`, `Cache-Control: no-store, private`
- 실제 폐기: 303 이후 같은 공개 URL과 기존 session이 410이며 공유 파생 cache가 남지 않음
- 실제 소유자 관리: 생성한 공유가 활성 공유 목록에 표시되고 목록에서 다시 열기·종료 가능
- 재로그인 대비 인증: launch session 환경변수를 제거한 상태에서도 권한 0600 runtime allow-list로 Tailnet 소유자 화면 200·추천 12장 확인
- 브라우저 desktop/mobile 확인: grid, viewer, 좌우 이동, touch 대응과 다운로드 통과
- 자동 접근성 검사: WCAG 2 A/AA violation 0건

테스트용 공유는 검증 직후 폐기했으며, 공개 가능한 활성 테스트 링크와 파생 이미지 파일은 남기지 않았다.

## 2026-09-06 Story Director 2차 구현 결과

이번 단계는 위치 추정을 성급히 붙이지 않고, 현재 DB에서 실제로 검증 가능한 `촬영일 + scene description + event type + 추천 사유/점수`만으로 날짜별 스토리를 만드는 수직 범위다.

| 영역 | 반영 결과 |
|---|---|
| Evidence Builder | `local_asset_id`, provider asset ID, `photo_id`, 파일 경로와 인물 label을 모델 입력에서 제거하고, 안정적인 불투명 `photo_ref`와 허용된 분석 필드만 `photo-evidence-v1` envelope로 만든다. |
| 근거 snapshot | 정규화한 evidence 전체의 SHA-256을 저장한다. evidence가 같으면 GET이나 자동 reconcile을 반복해도 Story revision과 내용이 바뀌지 않는다. |
| 기존 분석 재사용 | 앞으로 저장되는 추천 member에는 길이를 제한한 `scene_description`, `event_type`, `meaningful_score`를 함께 남긴다. 기존 추천은 같은 SQLite의 `photo_results`에서 인물 정보가 제외된 열만 읽어 백필한다. |
| Linux 연결 | Hermes capability route에서 `photos-read` profile을 선택하고 동일 decision lease로 `auto-local`을 한 번 호출한다. 응답 target은 반드시 `linux-long-context`여야 하며, 준비 제한 600초와 생성 제한 300초를 분리한다. |
| 구조화 출력 | 모델은 allow-list theme, 제목, 부제, cover `photo_ref`, 날짜별 chapter, closing만 JSON Schema로 반환한다. HTML/CSS/URL과 실제 asset ID는 생성할 수 없다. |
| 후검증 | 존재하는 `photo_ref`만 허용하고 모든 추천 사진이 정확히 한 번 포함되는지, chapter 날짜와 사진 촬영일이 같은지, theme과 text 제한을 검사한다. 하나라도 실패하면 모델 결과 전체를 폐기한다. |
| 실패 격리 | Linux가 꺼져 있거나 준비/응답/검증이 실패해도 추천 파일 저장 상태는 성공으로 유지한다. 날짜와 사진 수만 사용하는 deterministic fallback manifest를 저장하며, 이미 정상 Qwen manifest가 있으면 실패한 강제 재시도로 덮어쓰지 않는다. |
| 저장 시점 | 추천 materialization/reconcile이 `completed` 또는 `partial`로 끝난 직후 한 번 갱신한다. HTTP GET은 모델을 부르지 않으며 저장된 manifest만 읽는다. |
| 화면 | 소유자와 공유 화면 모두 날짜 chapter 제목·본문 아래에 해당 날짜의 여러 사진 grid를 표시하고, 선택 시 기존 전체화면 viewer로 이어진다. 외부 package는 chapter 문장과 공유별 난수 asset ID만 복제한다. |
| 수동 갱신 | Tailnet 소유자 화면의 `Linux Qwen으로 이야기 새로 구성` action으로 현재 evidence를 다시 편집할 수 있다. 이 action도 기존 유효 manifest 보존 규칙을 따른다. |

### 실데이터 백필 검증

- 대상: 현재 로컬 추천 12장
- evidence hash: 운영 기록에는 전체 SHA-256을 저장하고 점검 출력에는 앞 12자만 사용
- 결과: 날짜 chapter 2개, Story revision 9, `source=hermes-router`, `target=linux-long-context`
- 소요 시간: 워크스테이션 준비와 생성 포함 110.120초
- 토큰: prompt 1,225 / completion 505 / total 1,730
- 모델 전달 제외 항목: 이미지 byte, 파일 경로, provider ID, 로컬 asset ID, 얼굴 embedding, 등록 인물 이름, exact GPS
- 현재 추천 사본의 EXIF GPS가 0건이므로 위치 문장은 생성하지 않았다. 위치를 임의 추정하거나 장소명을 단정하지 않는 것이 이번 단계의 정상 동작이다.
- standalone 재빌드: deep code-sign, runtime import, vendor runtime smoke 통과 후 `/Volumes/ExtData/02_Services/PhotosMcp/PhotosMcp.app`에 교체했고 `/Applications/PhotosMcp.app` 링크도 이 운영본으로 복구했다.
- 운영 Tailnet 재검증: Open WebUI 200, `/photos` 200, Hermes Dashboard 200, 공개 gateway `/` 404. 소유자 HTML에는 날짜 chapter 2개, 확대 가능한 사진 12개, `Linux Qwen 편집` 상태가 표시된다.
- 안정판 설치 후 이전 앱·중복 앱·빌드 임시본 약 3.8GB는 별도 롤백본으로 보존하지 않고 제거했다.

### 이번 단계의 운용 흐름

```text
추천 분석 완료
  -> 추천 파일 로컬 저장·hash 확인
  -> share-safe 분석 근거 생성
  -> evidence hash가 기존과 같으면 기존 Story 재사용
  -> 달라졌으면 Hermes Router로 Linux Qwen 1회 호출
       -> schema/evidence 검증 성공: Qwen StoryManifest 저장
       -> 준비·호출·검증 실패: 날짜 기반 fallback 저장
  -> Telegram 저장 결과 알림은 기존 성공/실패 판정을 그대로 사용

소유자/공유자 GET
  -> 저장된 StoryManifest/SharedStoryPackage 읽기
  -> 고정 HTML renderer로 즉시 표시
  -> Linux 워크스테이션 호출 없음
```

## 2026-09-06 위치 provenance·Tailnet 결과 알림 3차 구현 결과

이번 단계는 정확 좌표를 더 널리 노출하는 기능이 아니라, 이미 Apple Photos와 EXIF에서 읽던 위치가 랭킹 체크포인트 이후 유실되는 경로를 고치고 위치의 보안 수명주기를 명확히 한 작업이다.

| 영역 | 반영 결과 |
|---|---|
| 분석 수집 | Apple Photos provider metadata 또는 embedded EXIF에서 얻은 GPS를 stage-1 checkpoint 복원 이후에도 유지한다. 향후 분석부터 `photo_locations_private`에 job/photo별 좌표와 provenance를 저장한다. |
| 일반 결과와 분리 | 정확 좌표는 `photo_results`, recommendation member/asset의 `payload_json`, 날짜 manifest, StoryManifest와 로그에 넣지 않는다. 랭커의 전용 private table과 materialized asset별 `recommendation_asset_locations_private`의 typed column에만 저장한다. |
| 추천 자산 연결 | 추천 파일 hash·local asset ID가 확정된 뒤 분석 private 위치를 연결한다. 이전 분석에 위치 snapshot이 없으면 추천 사본의 embedded EXIF를 로컬에서 한 번 읽어 보완한다. 파일이나 좌표를 외부 서비스로 보내지 않는다. |
| 안전 projection | 정확 좌표는 약 0.01도 격자로 양자화해 private column에만 유지한다. 화면과 LLM에는 좌표 숫자가 아닌 offline gazetteer의 도시 단위 `서울 일대` 같은 label만 전달한다. 알려진 도시 중심에서 90km 밖이면 label을 만들지 않는다. |
| 증거 수준 | 위치가 있으면 `confirmed_gps`, 출처는 `provider_metadata` 또는 `embedded_exif`로 기록한다. GPS가 없거나 검증 범위를 벗어나면 위치를 단정하지 않는다. 이미지 내용만으로 실제 장소를 추측하는 기능은 아직 활성화하지 않았다. |
| 시간대 | 촬영값에 timezone이 있으면 그 값을 snapshot하고, naive EXIF 시간은 기존 정책대로 Asia/Seoul 가정과 confidence를 유지한다. GPS 기반 현지 timezone 추정은 아직 하지 않는다. |
| Story | 날짜 chapter에 해당 사진들의 안전한 위치 label을 배지로 표시하고 viewer의 날짜·위치 정보에도 재사용한다. Qwen evidence에는 `photo_ref`, 날짜, 장면 분석과 도시 단위 label/status만 전달하며 정확 좌표는 전달하지 않는다. |
| 외부 공유 | `SharedStoryPackage` 생성 시 owner label을 그대로 참조하지 않고 별도의 `share_label`만 복제한다. 기존 package는 immutable이므로 자동으로 위치가 추가되지 않으며 새 공유부터 적용한다. HTML·다운로드 JPEG에는 좌표가 없다. |
| Telegram | 추천이 0장으로 정상 종료되어도 결과 알림을 만든다. 완료·부분 실패 알림의 버튼 URL은 localhost/요청 ID 없는 action base가 아니라 `PHOTOS_MCP_OWNER_STORY_URL`의 Tailnet 소유자 Story URL을 사용한다. standalone `Info.plist`에도 현재 Tailnet URL을 명시했다. |

### 개인정보 경계

```text
Apple provider metadata / local EXIF
  -> photo_locations_private                 exact · private
  -> 추천 파일 hash와 local_asset_id 확정
  -> recommendation_asset_locations_private exact/coarse · private typed columns
       ├─ owner projection: 도시 단위 label 또는 blank
       ├─ LLM evidence: 도시 단위 label + evidence status만
       └─ share projection: 허용된 도시 단위 label 또는 blank

금지 경로
  exact GPS -X-> photo_results/result JSON
            -X-> recommendation payload JSON/manifest
            -X-> Hermes Router/LLM prompt
            -X-> owner/public HTML
            -X-> shared JPEG EXIF
            -X-> external reverse-geocoding API
```

### 검증 기준

- 관련 location/storage/story/share 테스트 30개 통과
- 전체 자동 테스트 `764 passed`
- 테스트 JPEG의 서울 GPS가 private 원장에는 정확히 저장되지만 public getter, local asset payload와 LLM evidence에는 숫자 좌표가 없음을 검증
- 0장 정상 완료 알림도 생성되고 버튼이 HTTPS `*.ts.net/photos`를 가리킴을 검증
- 외부 공유는 공유용 위치 label만 포함하며 내부 asset/provider ID와 정확 GPS는 포함하지 않음을 검증

## 2026-09-06 위치 문맥 추론·Story Album 4차 구현 결과

이번 단계는 GPS가 없다는 이유만으로 장소를 창작하지 않으면서도, 촬영 묶음 안에 확실한 GPS 사진이 있으면 그 근거를 안전하게 활용하는 범위다.

| 영역 | 반영 결과 |
|---|---|
| offline 위치·시간대 | 네트워크 reverse geocoder를 호출하지 않는 도시 사전을 국내 주요 도시와 자주 방문할 수 있는 해외 도시로 확장했다. 90km 이내에서만 도시 단위 label과 IANA timezone을 만든다. 좌표는 계속 private typed column에만 존재한다. |
| 기존 DB 호환 | 앱 시작 시 기존 `recommendation_asset_locations_private`에 위치 timezone 열을 안전하게 추가한다. 신규 설치와 기존 운영 DB가 같은 코드 경로를 사용한다. |
| 문맥 추론 | GPS가 없는 추천 사진은 같은 collection 안의 `confirmed_gps` 사진만 근거로 사용한다. 같은 `scene_cluster_id`의 모든 GPS 근거가 한 도시 label에 일치하면 confidence 0.90, 시각이 포함된 촬영값 기준 2시간 이내의 모든 근거가 일치하면 confidence 0.72로 저장한다. 서로 다른 도시가 섞이면 추정하지 않는다. |
| 추론 원장 | 추정치는 exact GPS table과 분리한 `recommendation_asset_location_inferences`에 저장한다. label, 상태, provenance, confidence와 source fingerprint만 저장하며 좌표와 원본 source asset ID는 받지 않는다. 나중에 실제 GPS가 들어오면 추정 행은 자동 삭제되고 확인된 값이 우선한다. |
| 표시 의미 | 확인된 사진은 `GPS 확인`, 전파된 사진은 도시 label 뒤에 `(추정)`과 `문맥 추정`, 근거가 없거나 충돌하면 `위치 미상`·`위치 정보 없음`으로 표시한다. 낮은 confidence의 장소를 사실처럼 쓰지 않는다. |
| 날짜·위치 Story | Story Director가 만든 날짜 chapter는 유지하고, 서버가 각 날짜 안의 사진을 위치별 subchapter로 결정론적으로 다시 묶는다. 모델이 asset ID나 위치 그룹을 임의로 만들 수 없다. 상단에는 외부 지도·CDN 없이 도시별 장수를 보여주는 same-origin 위치 개요가 표시된다. |
| 외부 공유 projection | 공유 package를 만들 때 owner 그룹을 그대로 복사하지 않고 `share_location`과 공유별 난수 asset ID로 위치 그룹·개요를 다시 만든다. 내부 ID, 정확 좌표, 원본 경로는 HTML과 package 공개 view에 없다. |
| 링크·코드 분리 | 공유 생성 직후 `링크 복사`와 `코드 복사`를 별도 버튼으로 제공한다. 잠금 코드는 그 응답 화면에서 한 번만 보이고 DB에는 PBKDF2 hash만 남으므로, Telegram 등 서로 다른 메시지로 나눠 전달할 수 있다. |
| 범위 제한 | OCR 텍스트나 landmark를 외부 검색해 위치를 추측하는 기능은 활성화하지 않았다. 사진 내용만으로 장소를 단정할 위험과 개인 사진·OCR의 외부 전송 위험이 더 크므로, 명시적 opt-in·검증 자료가 생길 때까지 `위치 미상`을 정상 결과로 둔다. |

### 4차 검증 기준

- 위치·Story·공유·추천 저장 관련 테스트 33개 통과
- 전체 자동 테스트 `772 passed`
- 서울·파리의 offline timezone 판정과 알려진 도시 반경 밖의 label 미생성을 검증
- 같은 장면 GPS 전파의 멱등성, 서울/부산 근거 충돌 시 추정 거부, 추론 payload의 좌표 입력 거부를 검증
- scene이 달라도 실제 시각이 2시간 이내이면 일치 근거만 전파되고 날짜만 있는 값은 시간 근거로 사용하지 않음을 검증
- materialization 한 번 안에서 모든 추천 파일을 저장한 뒤 같은 장면 추론이 수행되고 `located_count`와 `inferred_location_count`가 분리됨을 검증
- Story와 공유 package의 위치별 subchapter가 모든 사진을 정확히 한 번 포함하고 공개 ID만 사용함을 검증
- 동일 evidence의 구형 Story schema가 HTTP GET에서 묵묵히 재사용되지 않고 새 schema revision으로 승격됨을 검증
- 생성 화면의 링크/코드 개별 복사와 저장 DB의 평문 passcode 부재를 검증

### 4차 운영 반영 결과

- standalone 앱의 deep code-sign, health, runtime import와 vendor runtime smoke를 모두 통과한 빌드만 `/Volumes/ExtData/02_Services/PhotosMcp/PhotosMcp.app`에 설치했다.
- `/Applications/PhotosMcp.app`은 최종 운영 앱을 가리키며, runtime directory는 `0700`, SQLite DB는 `0600`이다.
- 기존 12장 Story는 구형 manifest를 안전 기본 문장으로 덮지 않도록 먼저 구조 승격 경계를 보완했고, 최종적으로 Qwen Story Director를 정식 재호출해 `recommendation-story-v2`, revision 13, `hermes-router / linux-long-context`로 저장했다.
- 운영 화면은 추천 12장, 날짜 chapter 2개, 위치 subchapter 2개와 위치 개요를 표시한다. 현재 12장에는 확인된 GPS가 없으므로 모두 `위치 미상`으로 유지하며 장소를 만들어내지 않았다.
- 실제 HTTP 확인 결과는 local `/photos` 200, Tailnet `/photos` 200, Open WebUI 200, Hermes Dashboard 302 로그인 이동이다. 공개 8443 gateway의 `/`와 `/health`는 모두 404로 유지했다.
- 443과 9119는 Tailnet only이며 8443만 Funnel인 Tailscale Serve 구성을 다시 확인했다.
- 테스트 공유와 Telegram 테스트 메시지는 만들지 않았고, 운영 알림 원장과 공개 공유 목록에 검증용 흔적을 남기지 않았다.
- 안정판 검증 후 이전 앱 3개와 빌드 임시본 3개 약 11.3GB는 롤백본으로 보존하지 않고 제거했다.

### 이후 선택형 확장

다음 항목은 현재 목표의 필수 조건이 아니며, 실제 사용 중 필요성이 확인될 때 별도 승인 범위로 진행한다.

- OCR/landmark 기반 `시각 추정`: 민감 OCR 외부 전송 금지, 공개 landmark 후보만 명시적 opt-in
- self-hosted PMTiles/MapLibre 정적·상호작용 지도: 현재 위치 개요는 외부 요청 없는 텍스트형 요약
- self-hosted Swiper 정식 패키지: pinch zoom·고급 pagination이 필요할 때만 vendored asset으로 확장
- Telegram 자동 외부 공유 전송: 현재는 소유자가 생성한 링크와 코드를 화면에서 분리 복사하여 의도한 수신자에게 전달

## 결론

Swiper를 사용하는 방향은 적합하다. 첫 구현은 다음 원칙으로 제한한다.

1. 기본 화면에는 **분석 입력 전체가 아니라 최종 추천으로 확정되어 로컬 저장 검증을 통과한 사진만** 반응형 grid로 여러 장 표시한다.
2. grid에서 사진을 선택하면 그 사진을 시작점으로 전체화면 Swiper viewer를 열고, viewer 안에서는 한 장씩 크게 표시한다. 자동 재생과 무한 반복은 사용하지 않는다.
3. 1차 운영본은 외부 의존성이 없는 로컬 경량 viewer controller를 PhotosMcp 앱에 포함한다. zoom·pagination 확장 시에는 Swiper를 CDN 없이 고정 버전의 vendored asset으로 포함한다.
4. 원본 사진을 직접 전송하지 않고 방향을 보정하고 메타데이터를 제거한 웹용 JPEG 미리보기를 제공한다.
5. 소유자용 화면은 Tailscale Serve의 Tailnet 전용 HTTPS와 사용자 identity header를 인증 경계로 사용한다. 외부 수신자용 공유 화면은 기존 Serve 포트를 공개로 전환하지 않고 별도 Funnel 포트와 애플리케이션 자체 인증을 사용한다.
6. Telegram은 항상 개별 event 또는 run URL을 전달하며 localhost URL이나 요청 ID가 빠진 기본 URL을 보내지 않는다.
7. Story 열람 화면은 읽기 전용이다. 사진 삭제, 앨범 변경, 다시 분류 같은 mutation 기능은 넣지 않는다. 다만 외부 공유 package 생성·만료·폐기는 소유자가 명시적으로 실행하는 별도 관리 action으로 취급한다.
8. 화면은 순수 운영 대시보드가 아니라 **에디토리얼 블로그와 사진 앨범을 결합한 `Story Album Report`**로 구성한다. 사진과 날짜별 이야기를 먼저 보여주고 점수·모델·저장 영수증은 상세 계층으로 내린다.
9. GPS는 원본 좌표를 HTML에 노출하지 않는다. 추천 자산 생성 시 별도 민감정보 원장에 좌표·출처·정확도·현지 시간대를 snapshot하고, Tailnet HTML은 기본 `balanced` mode에서 약 1km 수준의 coarse 위치를 사용한다. 본인 identity로 확인된 `personal_detailed` mode는 검증된 장소/POI label을 허용하되 좌표 숫자는 계속 제외한다.
10. 지도는 주 탐색 수단이 아니라 날짜·장소 타임라인을 보조하는 접힌 로컬 개요 지도로 시작한다. 외부 tile, CDN, client-side reverse geocoding은 사용하지 않는다.
11. LLM은 제목·도입부·chapter grouping·사진 순서·대표 사진·허용된 레이아웃 모듈을 선택하지만 임의 HTML, CSS, JavaScript와 URL을 생성하지 않는다. 구조화된 `StoryManifest`를 만들고 고정 renderer가 최종 HTML을 생성한다.
12. GPS가 없는 사진도 시간상 인접한 GPS 사진, OCR된 공개 표지판, 앨범·이벤트 문맥과 시각적 landmark 후보를 종합해 위치를 추정할 수 있다. 다만 `확인`, `문맥 추정`, `시각 추정`, `알 수 없음`을 명확히 구분하고 confidence가 낮으면 장소를 단정하지 않는다.
13. Story 문장마다 사용한 사진·시간·위치·분석 근거 ID를 내부 manifest에 연결한다. LLM이 관찰되지 않은 인물 관계·감정·방문 목적을 만들어내지 못하도록 schema와 post-validation을 적용한다.
14. 다른 사람에게 공유할 때는 내부 Story URL이나 원본 manifest를 전달하지 않는다. 선택한 Story revision에서 공개 가능한 사진·문장·위치만 복제한 immutable `SharedStoryPackage`를 만들고 만료, passcode, 즉시 폐기와 공개 범위를 함께 설정한다.
15. 기존 `443` Serve의 Open WebUI와 Photos 경로, `9119` Hermes Dashboard는 계속 Tailnet 전용으로 유지한다. 공개 공유는 지원 포트 중 별도 `8443` Funnel과 loopback share gateway로만 제공하며 `/s/*`와 정적 공유 asset 외 route는 노출하지 않는다.

## 구현 전 기준선

### HTTP와 Tailscale

작업 전 PhotosMcp는 `127.0.0.1:18791`에만 bind했고, 사용자 조치 HTML route는 다음 하나뿐이었다.

```text
GET /actions/{request_id}
```

Tailscale Serve의 현재 연결은 다음과 같다.

```text
https://byoungyoung-macmini.tail53bcc7.ts.net/               -> 127.0.0.1:3000
https://byoungyoung-macmini.tail53bcc7.ts.net/photos-actions -> 127.0.0.1:18791/actions
https://byoungyoung-macmini.tail53bcc7.ts.net:9119/          -> Hermes Dashboard proxy
```

`/photos-actions/{request_id}`는 정상 HTML을 반환하지만 `/photos-actions`만 요청하면 404다. 일부 완료·실패 알림은 요청 ID가 없는 기본 URL이나 `127.0.0.1` URL을 저장하므로 모바일에서 열 수 없다.

작업 전 HTML은 제목, 상태, 메시지와 다음 단계만 표시했고 이미지 route와 추천 결과 갤러리는 없었다. 현재 운영 연결은 이 문서의 `2026-09-06 구현 결과`를 기준으로 한다.

### 추천 사진 저장 데이터

현재 추천 결과는 다음 자료를 이미 연결할 수 있다.

| 자료 | 현재 저장 위치 | 갤러리 사용 목적 |
|---|---|---|
| 사용자 알림 | `user_action_requests` | event URL, 상태, 제목, run/collection 식별 |
| 일일 자동화 | `photo_automation_runs` | 시작·종료·provider·처리 수·오류 |
| 추천 collection | `recommendation_collections` | 한 분석 작업의 추천 결과 범위 |
| 추천 member | `recommendation_members` | collection과 provider 자산, 추천 순서 연결 |
| 로컬 추천 자산 | `local_recommendation_assets` | 검증된 상대 경로, hash, MIME, 촬영일 |
| 월별 그룹 | `recommendation_groups`와 `recommendation_group_members` | 날짜 그룹과 목적지 앨범 연결 |
| 앨범 영수증 | `recommendation_destination_receipts` | 실제 앨범 반영·중복 억제 상태 |

2026-09-06 점검 시 로컬 추천 자산은 12장이며 현재 모두 `image/jpeg`, `resource_role=primary`다. 자산에는 recommendation root 기준 상대 경로와 content hash가 존재한다. 따라서 DB에 원본 절대 경로를 새로 저장하지 않고도 안전한 ID 기반 조회가 가능하다.

현재 저장 root는 다음과 같다.

```text
/Volumes/ExtData/02_Services/PhotosMcp/recommendations/
```

갤러리는 이 root 아래에서 DB 영수증과 hash 검증을 통과한 파일만 읽는다.

## 사용자 경험

### Telegram 메시지

Telegram에는 긴 결과를 모두 넣지 않고 핵심 수치와 하나의 대표 링크를 제공한다.

```text
📷 Google Photos 추천 사진 정리 완료

추천 2장 · 신규 저장 2장 · 중복 0장 · 실패 0장

사진과 상세 결과 보기
https://byoungyoung-macmini.tail53bcc7.ts.net/photos/events/{request_id}
```

링크 선택 기준은 다음과 같다.

| 알림 종류 | 기본 링크 |
|---|---|
| 추천 사진 보관 완료·부분 실패 | `/photos/events/{request_id}` |
| 일일 작업 전체 요약 | `/photos/runs/{automation_run_id}` |
| 월별 앨범·그룹 요약 | `/photos/groups/{group_id}` |
| 사용자 조치 필요 | `/photos/events/{request_id}` |
| 연결할 식별자가 없는 시스템 오류 | `/photos/` |

기존 `/photos-actions/{request_id}`는 호환 route로 유지하고 새 event URL로 redirect하거나 같은 renderer를 사용한다.

### 화면 정보 구조

기본 화면은 **표지 → 전체 contact sheet → 날짜·장소별 이야기 → 저장·처리 정보** 순서의 스토리 앨범이다. contact sheet를 상단에 두어 여러 장을 먼저 훑을 수 있게 하고, 같은 사진을 아래의 날짜·장소 chapter에서 맥락과 함께 다시 탐색할 수 있게 한다. 여기서 사진 “선택”은 확대 열람을 의미하며 추천 취소, 삭제, 앨범 변경 같은 mutation이 아니다.

```text
┌─────────────────────────────────────┐
│ [대표 사진 — 거의 full bleed]        │
│                                     │
│  2026년 9월의 사진                  │
│  9월 2일–5일 · 추천 28장 · 4일      │
│  Apple Photos + Google Photos       │
│                                     │
│  “늦여름에 촬영된 야외 장면과        │
│   일상의 추천 사진을 모았습니다.”     │
├─────────────────────────────────────┤
│ 모든 추천 사진                       │
│ [사진][사진]                          │
│ [사진][사진]        ← contact grid   │
│ [사진][사진]                          │
├─────────────────────────────────────┤
│ 장소와 시간                          │
│ [여행 경로 개요 펼치기]              │
│ 위치 확인 21장 · 위치 없음 7장       │
├─────────────────────────────────────┤
│ 9월 2일 · 화요일                     │
│ 부산 일대 · 추천 8장                 │
│ “오후에 부산 일대에서 촬영된          │
│ 사진 중 8장을 골랐습니다.”            │
│ [대표 큰 사진]                        │
│ [보조 사진][보조 사진]                │
├─────────────────────────────────────┤
│ 9월 3일 · 수요일                     │
│ 위치 정보 없음 · 추천 7장            │
│ [사진][사진]                          │
├─────────────────────────────────────┤
│ 처리 결과                             │
│ 추천 28 · 신규 12 · 중복 3 · 실패 0 │
│ 로컬 보관 / Apple 앨범 / 분석 정보    │
└─────────────────────────────────────┘
```

사진을 선택하면 어느 section에서 열었는지와 관계없이 선택한 사진의 전역 index부터 전체화면 Swiper를 연다.

```text
모바일
┌─────────────────────────────────────┐
│ 닫기            9월 3일       4 / 28 │
├─────────────────────────────────────┤
│                                     │
│             큰 사진                 │
│          object-fit: contain         │
│                                     │
│        ‹                     ›       │
├─────────────────────────────────────┤
│ 촬영  2026-09-03 16:24              │
│ 장소  경주 일대 · 약 1km 단위        │
│ 이유  같은 장면의 대표 사진          │
│ [사진 분석 자세히 보기]              │
└─────────────────────────────────────┘

데스크톱
┌─────────────────────────────┬──────────────┐
│                             │ 이 사진       │
│                             │ 날짜·장소     │
│          큰 사진            │ 추천 이유     │
│                             │ AI 장면 설명  │
│                             │ 촬영 정보     │
│                             │ 저장·출처     │
└─────────────────────────────┴──────────────┘
        사진 72~78%              320~380px
```

viewer를 닫으면 원래 선택한 grid card의 focus와 scroll 위치를 복원한다. 모바일 분석 영역은 접힌 bottom sheet, 데스크톱은 독립 scroll inspector로 제공한다. 로그, 내부 ID, 모델 상세는 사진보다 먼저 보이지 않는다.

### Story와 Grid 보기

- `Story`는 날짜 → 안전한 장소 cluster → 시간 순서로 보여주는 기본 보기다.
- `Grid`는 전체 추천 사진을 빠르게 훑는 contact sheet다.
- 자동화 run의 기술적 결과 링크는 Grid를 먼저 열 수 있지만, 날짜/월간 group과 여행·이벤트 링크는 Story를 먼저 연다.
- `이야기 순서`, `최신순`, `추천순`을 향후 제공할 수 있으나 첫 구현에서는 페이지 유형별 기본 순서를 서버가 결정한다.
- CSS masonry는 사용하지 않는다. 시각 순서와 DOM 순서가 어긋나고 이미지 로딩 중 layout shift가 생길 수 있으므로 `대표 1장 + 보조 2장 + 일반 grid`의 명시적 editorial module을 사용한다.

### 분석 정보의 표시 계층

사진 분석은 점수표가 아니라 “왜 이 사진이 남았는가”를 설명하는 보조 계층으로 사용한다.

| 계층 | 항상 표시 | 펼쳤을 때 표시 | 표시하지 않음 |
|---|---|---|---|
| grid card | 사진, 촬영일, 추천 1/2순위 | 짧은 추천 이유 | 긴 AI 설명, 모든 점수 |
| viewer caption | 날짜, 안전한 장소, 핵심 추천 이유 | 장면·촬영·저장 정보 | 내부 ID, 원시 코드 |
| 사진 분석 | 품질 등급, 장면 대표 여부 | 상대 점수, cluster 순위, AI 장면 설명 | 존재하지 않는 구도 세부 점수 |
| 처리 정보 | 저장·앨범 결과, 실패 수 | 모델, prompt/policy version, 처리 시간 | token/경로/예외 원문 |

추천 점수는 절대적인 사진 평가처럼 보이지 않게 한다.

```text
기본: 품질 우수
상세: 품질 점수 84 · 이번 이벤트 안에서 상대적으로 높음
안내: 추천 정책 v2 기준이며 절대적인 사진 평가가 아닙니다.
```

`scene_description`에는 `AI 장면 설명`이라는 label을 붙이고 길이를 제한한다. 인물의 이름·관계·감정, GPS로 확인되지 않은 랜드마크나 활동을 사실처럼 쓰지 않는다. 보고서 소개문은 구조화된 촬영일·확인된 장소·사진 수를 이용한 결정론적 문장을 기본으로 하고, AI 문장은 선택적 보조 설명으로 제한한다.

## 세 에이전트 설계 비교와 채택안

동일한 코드·문서·실제 runtime DB를 기준으로 세 관점에서 독립 검토했다.

1. **에디토리얼 앨범 검토**: 사진 중심의 블로그, 날짜 chapter, 대표 사진과 Swiper 상세 화면을 설계했다.
2. **GPS·여행 타임라인 검토**: 위치 provenance, 현지 날짜, 지도 개인정보 보호와 모바일 gesture를 검토했다.
3. **분석 리포트 검토**: 현재 DB에서 실제로 조회 가능한 점수·장면·EXIF·저장 영수증과 추가 스키마가 필요한 정보를 구분했다.

### 후보 비교

| 후보 | 장점 | 약점 | 현재 데이터 적합성 | 채택 판단 |
|---|---|---|---|---|
| A. 순수 블로그형 | 감성적이고 읽기 좋음, 날짜별 이야기 전달 우수 | 사진 전체를 빠르게 비교하기 어렵고 AI 문장 과장이 생길 수 있음 | 장면 설명과 날짜는 충분하나 자동 서사는 제한 필요 | chapter 구성만 채택 |
| B. 순수 앨범형 | 여러 장 탐색과 확대 감상이 빠름 | 날짜·장소·추천 이유의 맥락이 약함 | 즉시 구현 가능 | contact grid와 Swiper를 핵심으로 채택 |
| C. 지도·여행기형 | GPS가 있는 여행 사진의 기억 맥락이 강함 | 현재 추천 원장에 좌표와 현지 timezone이 없고 개인정보 위험이 큼 | 위치 snapshot 보강 후 가능 | timeline과 접힌 로컬 지도만 단계 채택 |
| D. 분석 대시보드형 | 점수·모델·저장 상태를 명확히 진단 | 사진보다 운영 정보가 앞서고 개인 앨범 감성이 약함 | 현재 DB 데이터와 가장 잘 맞음 | viewer inspector와 하단 처리 정보로만 채택 |

### 최종 채택: Story Album Report

네 후보의 장점을 결합하되 화면의 우선순위는 다음처럼 고정한다.

```text
사진 감상
  → 전체 contact sheet
    → 날짜·장소별 이야기
      → 선택 사진의 근거 있는 분석
        → 저장·앨범·모델 실행 정보
```

순수 블로그처럼 서술을 길게 만들지 않고, 순수 대시보드처럼 점수와 운영 상태를 전면에 내세우지 않는다. 지도는 사진과 timeline을 보조하며, 위치가 없는 Google Picker 사진도 동일한 앨범 안에서 자연스럽게 표시한다.

### 시각 디자인 토큰

외부 font와 CDN 없이 사진이 주인공이 되는 차분한 에디토리얼 스타일을 사용한다.

| 용도 | 권장 값 | 의도 |
|---|---|---|
| Story 배경 | warm paper `#F7F4EE` | 블로그·사진집의 종이 질감 |
| 사진 주변 | `#FFFDF8` | 사진색과 충돌하지 않는 밝은 면 |
| 본문 | ink `#181714` | 충분한 대비 |
| 보조 text | stone `#6F6A62` | metadata의 위계 축소 |
| 구분선 | `#D8D2C8` | 카드 대신 섬세한 section 구분 |
| link/focus | muted teal `#335C67` | 상태색보다 접근성 focus 강조 |
| Viewer 배경 | `#0B0B0C` | 사진 집중형 몰입 화면 |
| Viewer text | `#F4F1EA` | 어두운 화면에서 눈부심 완화 |

```css
--font-display: ui-serif, "AppleMyungjo", "Noto Serif KR", Georgia, serif;
--font-body: -apple-system, BlinkMacSystemFont, "Apple SD Gothic Neo",
             "Pretendard", "Noto Sans KR", sans-serif;
```

- 제목과 날짜 chapter에는 serif, 버튼·본문·metadata에는 sans-serif를 쓴다.
- 시스템 font가 우선이며 Google Fonts 요청은 만들지 않는다.
- 과도한 gradient, glass blur, neon, 두꺼운 shadow, 모든 사진 위의 점수 badge는 사용하지 않는다.
- radius는 0~8px로 제한하고 viewer·bottom sheet 외에는 그림자를 최소화한다.
- 본문은 65~75ch, 모바일 본문은 최소 16px, 모든 touch control은 최소 44×44px로 한다.

### GPS·장소·시간 표시 원칙

정확한 GPS는 내부 처리 자료이고 remote HTML의 표시 자료가 아니다. 사진 미리보기는 계속 EXIF를 제거하고, 권한을 통과한 위치 표시는 별도 view model로만 전달한다.

| 위치 자료 | private 위치 원장 | Tailnet HTML 기본값 |
|---|---|---|
| 정확한 위·경도 | provenance와 함께 선택 저장 | 전달하지 않음 |
| 지도 marker | 서버 내부 cluster에 사용 | 소수점 2자리 상당, 약 1km coarse centroid |
| 장소 label | 로컬 reverse geocode 결과 | 도시·구/군 또는 넓은 권역 |
| 집·직장·학교·병원 등 | privacy zone 판정 | marker 숨김 또는 도시 단위 |
| 고도·방향·GPS 시각 | 필요할 때 내부 진단 | 표시하지 않음 |
| GPS 없는 사진 | `unavailable` | `위치 정보 없음` |
| 충돌하는 출처 | `conflicting` | 추정하지 않고 `위치 확인 필요` |

첫 위치 UI는 다음처럼 구성한다.

- 날짜별 text timeline이 authoritative UI다.
- hero 아래에는 `여행 경로 개요 펼치기` 버튼을 두고 지도는 기본적으로 접는다.
- 지도는 외부 tile 없이 서버가 생성한 same-origin 정적 SVG 개요로 시작한다.
- 지도 marker를 누르면 해당 날짜·장소 chapter로 scroll한다.
- Swiper 안에는 interactive map을 넣지 않고 안전한 장소 label과 `지도에서 보기`만 둔다.
- 위치 없는 구간을 임의의 직선으로 잇지 않는다. 긴 시간 공백이나 비현실적인 이동은 route를 끊거나 낮은 신뢰도로 표시한다.
- 상세 지도 필요성이 확인되면 2단계에서 self-hosted PMTiles와 MapLibre GL JS를 검토한다. public Nominatim의 일일 자동 호출과 외부 tile은 사용하지 않는다.

### 현지 날짜 문제

현재 `capture_date_local`은 모든 촬영 시각을 `Asia/Seoul`로 변환하거나 timezone이 없으면 서울로 가정한다. 해외 여행 사진은 자정 전후에 날짜 chapter가 잘못 나뉠 수 있으므로 Story timeline 구현 전에 다음 값을 보존한다.

```text
captured_at_original
original_timezone_offset
timezone_source
capture_local_date_at_location
capture_time_confidence
```

시간대 결정 우선순위는 EXIF offset → GPS 기반 로컬 timezone → provider timezone → 서울 가정값이다. 마지막 경우는 낮은 신뢰도로 표시하고 자정 근처 사진은 자동으로 다른 날짜로 옮기지 않는다.

### 현재 데이터로 가능한 것과 보강이 필요한 것

2026-09-06 runtime DB 점검 결과 추천 member는 15건, 로컬 추천 자산은 12건이며 추천 member는 모두 해당 `photo_results`와 연결할 수 있었다. 다음을 구분해 구현한다.

| 정보 | 현재 신뢰성 | 처리 방안 |
|---|---|---|
| 촬영 시각·날짜 | 사용 가능 | 현재 DB join, 이후 original timezone 보강 |
| 추천 순위·이유 | 사용 가능 | 내부 reason code를 allowlist 한국어 label로 변환 |
| 종합·품질·기술·의미·이벤트·독창성 점수 | 사용 가능 | 상세 inspector에서 상대 지표로 표시 |
| AI 장면 설명·event type | 사용 가능 | escape·길이 제한·AI label 적용 |
| 장면 cluster 크기·순위 | 사용 가능 | `같은 장면의 대표 사진` 근거에 사용 |
| Google/Apple 출처와 로컬·앨범 영수증 | 사용 가능 | 중복 content hash는 한 slide에 출처를 합침 |
| 카메라·렌즈·ISO·조리개 등 EXIF | 로컬 자산에 조건부 존재 | 허용 필드만 materialization 시 snapshot 또는 지연 추출 |
| 실제 사진 종횡비 | 파일에서 확보 가능 | `pixel_width`, `pixel_height`, `aspect_ratio` 보강 |
| GPS 좌표 | source 단계에는 있으나 추천 원장 연결이 불안정 | 별도 private 위치 원장과 materialization snapshot 필요 |
| Google Picker GPS | 일반적으로 제공되지 않음 | GPS는 `unavailable` 유지; 별도의 Story location resolver가 주변 사진·OCR·landmark 근거로 추정 label을 만들 수 있음 |
| 구도 세부 점수 | 현재 없음 | 품질/기술 점수를 구도 점수로 이름 바꾸지 않음 |
| blur·노출·noise 세부 점수 | 최종 DB에는 없음 | 후속 `technical_breakdown` 도입 전 표시하지 않음 |
| 사진별 유사 중복 거리 | 현재 없음 | 동일 content hash와 같은 scene cluster를 혼동하지 않음 |

분석 요청 때마다 원본 EXIF를 다시 읽어 HTML을 만들지 않는다. 원본 이동, iCloud 지연, parser 공격 표면과 비결정성을 피하기 위해 추천 materialization 시 안전한 metadata snapshot을 만든다.

### 보고서 생성용 안전한 snapshot

기존 일반 payload에 정확한 좌표를 섞지 않고 다음 개념을 추가한다.

```text
recommendation_report_metadata
├── local_asset_id
├── captured_at_original
├── original_timezone_offset
├── capture_time_confidence
├── pixel_width / pixel_height / aspect_ratio
├── camera_summary_allowlisted
├── event_label_allowlisted
├── safe_scene_excerpt
├── selection_reason_labels[]
├── analysis_version / confidence
└── safe_location_projection
    ├── visibility: hidden | coarse
    ├── label
    ├── precision: region | city
    ├── source / confidence
    └── redaction_reason

recommendation_asset_locations  # 별도 private DB 또는 권한 제한 table
├── local_asset_id
├── provenance
├── latitude_exact / longitude_exact
├── accuracy_meters
├── location_status
├── timezone_id / timezone_source
├── privacy_class
└── display_allowed
```

정확 좌표 table은 파일 권한 `0600`, 상위 디렉터리 `0700`을 사용하고 일반 HTML view model, 로그, URL, Telegram message에 넣지 않는다. Apple·EXIF·Google Takeout 출처가 충돌하면 먼저 온 값을 쓰지 않고 `conflicting`으로 보존한다.

## 생성형 Storyboard 확장 재검토

### 판단

사용자가 큰 방향만 정하고 LLM이 사진 묶음의 제목, 이야기, chapter, 대표 사진과 화면 리듬을 직접 편집하는 방식은 기존 고정형 Story Album보다 한 단계 높은 목표로 적합하다. 특히 날짜별 자동화 결과를 단순 gallery가 아니라 다시 읽을 가치가 있는 개인 사진 기록으로 바꿀 수 있다.

다만 LLM이 raw HTML 전체를 생성하게 하면 다음 문제가 생긴다.

- 같은 입력인데 실행할 때마다 layout과 DOM이 달라진다.
- 잘못된 URL, inline script, 외부 image/font/map 요청이 들어갈 수 있다.
- 접근성, 모바일 breakpoint, CSP와 Swiper 연결 계약을 보장하기 어렵다.
- 사진 ID를 잘못 연결하거나 존재하지 않는 분석·위치를 만들어낼 수 있다.
- 모델 교체 시 이전 앨범의 표시가 달라지고 회귀 테스트가 사실상 불가능해진다.

따라서 사용자가 원하는 “LLM이 직접 스토리를 짜고 그에 맞게 보여주는 경험”은 다음처럼 구현한다.

```text
LLM의 역할
  제목 · 도입부 · chapter · 사진 순서 · 대표 사진 · layout module 선택

고정 renderer의 역할
  안전한 HTML · CSS · Swiper · media URL · 접근성 · 반응형 · CSP
```

즉 LLM은 **페이지의 편집자와 아트 디렉터**가 되고, 애플리케이션 renderer는 **출판 시스템**이 된다.

### 전체 생성 pipeline

```text
추천 확정 사진
  │
  ├─ 1. Evidence Builder
  │     EXIF · 촬영 시각 · 기존 GPS · OCR · VLM 관찰 · 점수 · scene cluster
  │
  ├─ 2. Location Resolver
  │     GPS 확인 → 시간·인접 사진 문맥 → OCR/landmark 후보 → 제한된 검증
  │
  ├─ 3. Event Grouper
  │     날짜 · 시간 간격 · 위치 거리 · scene embedding으로 후보 chapter 생성
  │
  ├─ 4. Story Director LLM
  │     테마 · 제목 · 도입부 · chapter 순서 · 대표 사진 · module 선택
  │
  ├─ 5. Schema/Evidence Validator
  │     asset allow-list · claim 근거 · 위치 confidence · 민감정보 · 문장 검사
  │
  ├─ 6. StoryManifest 저장
  │     model/prompt/evidence hash/version과 함께 immutable snapshot
  │
  └─ 7. Deterministic HTML Renderer
        표지 · storyboard modules · contact grid · Swiper · 정적 지도
```

사진이 같은데 HTML을 열 때마다 LLM을 다시 호출하지 않는다. 자동화 완료 후 `StoryManifest`를 한 번 생성·검증해 저장하고, HTTP 요청은 저장된 manifest를 빠르게 렌더링한다. 이렇게 해야 Telegram 링크가 즉시 열리고 Linux 워크스테이션이 꺼진 뒤에도 동일한 결과를 볼 수 있다.

### Evidence Builder

Story Director에게 원본 DB와 원본 파일을 통째로 전달하지 않고 사진별 근거를 정규화한 `PhotoEvidence`를 제공한다.

```text
PhotoEvidence
├── photo_ref                   # report 내부 불투명 ID
├── capture
│   ├── captured_at_original
│   ├── local_date_at_location
│   ├── time_of_day
│   └── confidence / source
├── location
│   ├── label_candidate
│   ├── status
│   ├── confidence
│   ├── provenance[]
│   └── sensitive_place_class
├── visual
│   ├── observable_subjects[]
│   ├── setting / weather_visual / lighting
│   ├── activity_candidates[]
│   ├── public_text_ocr[]
│   ├── landmark_candidates[]
│   └── scene_description
├── recommendation
│   ├── slot / reason_labels[]
│   ├── quality_band
│   ├── scene_cluster / rank
│   └── allowed_scores
├── provenance
│   ├── providers[]
│   └── storage / album states
└── evidence_version
```

VLM 관찰은 보이는 사실과 추론을 분리한다.

```text
observable: "파란 표지판에 BUSAN이라는 영문이 보임"
inference:  "부산에서 촬영되었을 가능성"
forbidden:  "가족이 부산 여행을 즐기고 있음"
```

인물 이름·관계·직업·감정은 사용자가 별도 등록한 사실이 아니면 근거에 넣지 않는다. 얼굴 embedding과 내부 파일 경로도 Story Director에게 전달하지 않는다.

### GPS가 없는 사진의 위치 추정 ladder

위치가 없다고 즉시 `알 수 없음`으로 끝내지 않고 다음 순서로 가능한 범위를 좁힌다. 높은 단계가 성공하면 낮은 단계는 불필요하게 실행하지 않는다.

| 단계 | 근거 | 표시 상태 | 권장 confidence |
|---|---|---|---:|
| L0 | 원본 EXIF, Apple Photos catalog, Google Takeout GPS | `GPS 확인` | 0.95~1.00 |
| L1 | 같은 event의 앞뒤 GPS 사진, 짧은 시간·거리 연속성 | `동선 문맥으로 추정` | 0.80~0.94 |
| L2 | 공개 표지판 OCR, 역·공항·랜드마크 이름, 앨범 제목 | `텍스트 단서로 추정` | 0.70~0.89 |
| L3 | 로컬 VLM의 랜드마크·도시 후보와 주변 사진의 공동 단서 | `사진 내용으로 추정` | 0.55~0.79 |
| L4 | 근거 부족 또는 후보 충돌 | `위치 정보 없음` | 0.00~0.54 |

L1 전파는 “가까운 시간에 찍혔다”는 이유만으로 좌표를 그대로 복사하지 않는다. 예를 들어 앞뒤 사진이 모두 같은 고신뢰 장소이고 촬영 간격이 짧으며 이동 가능 범위 안일 때만 같은 coarse location cluster에 넣는다.

권장 초기 규칙은 다음과 같다.

- 앞뒤 두 anchor가 같은 장소 cluster이고 대상 사진이 각각 90분 이내면 L1 후보로 허용한다.
- 한쪽 anchor만 있으면 30분 이내이고 scene/event 단서가 양립할 때만 허용한다.
- 공항·기차·자동차처럼 빠른 이동 가능성이 보이면 시간 기반 전파 범위를 줄이거나 중지한다.
- 두 anchor가 서로 다른 도시라면 중간 사진에 임의의 장소를 붙이지 않는다.
- 동일 camera clock의 timezone이 불확실하면 시간 간격 confidence도 함께 낮춘다.

### 시각 기반 위치 찾기

GPS가 없는 사진은 Qwen3.8 Flash Next vision이 다음 단서만 구조화해 제시하도록 한다.

- 도로·지하철·역·공항 표지판의 공개 문자
- 관광지·산·해변·건축물·스카이라인의 landmark 후보
- 국가나 지역을 좁힐 수 있는 언어·교통 표지·공공 시설물
- 앞뒤 사진과 공통으로 나타나는 장소 단서
- 후보마다 근거와 confidence, 대안 후보

모델이 `부산`, `해운대`, `경복궁`처럼 후보를 제시했다는 사실만으로 확정하지 않는다. 다음 검증을 거친다.

1. OCR text와 landmark candidate가 서로 모순되지 않는지 확인한다.
2. 날짜 group과 앞뒤 GPS anchor의 이동 가능 범위를 검사한다.
3. 로컬 gazetteer에서 후보 장소가 실제로 존재하는지 확인한다.
4. 필요할 때만 공개 landmark 이름 같은 비민감 text candidate를 web 검색으로 검증한다.
5. 검증 점수가 기준을 넘지 못하면 장소명을 낮은 정밀도로 축소하거나 `위치 정보 없음`으로 남긴다.

외부 검색에 사진, 얼굴, raw OCR 전체, 원본 파일명, exact GPS를 전송하지 않는다. 예를 들어 `BUSAN`, `LCT`, `해변 고층 건물`처럼 공개 landmark 후보를 만든 뒤 그 text만 검색할 수 있다. 개인 상점명·주택·학교·병원·숙소·차량 번호는 외부 검증 대상에서 제외한다.

### 위치 표시 수준

개인 Tailnet에서 본다는 사용자 의도를 반영해 기존 coarse-only 정책을 다음 세 mode로 구체화한다.

| mode | GPS 확인 사진 | 위치 추정 사진 | 민감 장소 | 용도 |
|---|---|---|---|---|
| `personal_detailed` | 검증된 장소/POI 이름 가능, 좌표 숫자는 숨김 | `○○ 인근으로 추정` 표시 | 기본 redaction, 개별 opt-in | 본인 Tailnet 기본 후보 |
| `balanced` | 도시·구/군 또는 약 1km 범위 | 도시·권역 수준만 | 항상 redaction | 권장 초기 기본값 |
| `share_safe` | 도시·도 단위 | 원칙적으로 숨김 | 항상 redaction | 향후 외부 공유용 |

첫 구현 기본값은 `balanced`로 유지하고, exact Tailscale login allow-list가 검증된 뒤 사용자가 설정에서 `personal_detailed`를 켤 수 있게 한다. `personal_detailed`에서도 위·경도 숫자와 raw 좌표 JSON은 HTML에 넣지 않고 서버가 생성한 안전한 장소 label과 정적 map projection만 보낸다.

각 장소에는 근거 badge를 붙인다.

```text
경복궁 · GPS 확인
해운대 인근 · 사진 내용으로 추정
부산 일대 · 동선 문맥으로 추정
개인 장소 · 상세 위치 숨김
위치 정보 없음
```

badge는 기본 사진 감상을 방해하지 않게 caption이나 Story chapter subtitle에만 표시한다. 사용자가 `위치 근거 보기`를 펼치면 좌표가 아니라 `Apple Photos 위치`, `앞뒤 사진 6장`, `표지판 OCR`, `랜드마크 후보 검증` 같은 provenance를 보여준다.

### Story Director의 편집 자유도

Story Director는 다음을 결정할 수 있다.

- 앨범 전체 제목과 1~3문장의 도입부
- 날짜·장소·이벤트를 합치거나 나누는 chapter 구성
- chapter 제목, 짧은 본문과 closing 문장
- cover, chapter hero와 보조 사진
- 같은 장면 반복을 줄인 사진 순서
- 허용된 storyboard module 선택
- Story, Grid, Map interlude의 노출 순서
- 촬영 흐름에 맞는 전체 테마

다음은 결정할 수 없다.

- 추천되지 않은 사진을 추가하거나 추천 사진을 삭제
- media URL, route, HTML tag, CSS class, script 생성
- DB의 asset ID를 임의 변경
- 근거가 없는 장소·인물 이름·관계·감정·여행 목적 생성
- location confidence나 provenance를 올려 쓰기
- 민감 장소 redaction 해제
- 원본 사진 또는 exact GPS 공개
- 사진 삭제, 앨범 변경 같은 mutation 실행

### 자동 테마 선택

고정된 한 레이아웃을 매일 반복하지 않도록 Story Director가 evidence에 따라 다음 allow-list 중 하나를 고른다.

| theme | 선택 조건 예시 | 기본 리듬 |
|---|---|---|
| `travel_journal` | 여러 날짜·여러 장소와 이동 흐름 | 지도 개요 → 날짜/장소 chapter → closing route |
| `day_in_life` | 하루 안의 시간대 변화 | 아침 → 낮 → 저녁 timeline |
| `event_highlights` | 하나의 행사·공연·모임 | 대표 장면 → 인물/공간/세부 장면 |
| `urban_walk` | 연속된 거리·건축·도시 장면 | 장소 interlude와 wide image strip |
| `nature_sequence` | 산·바다·공원·날씨 변화 | landscape hero와 느슨한 여백 |
| `seasonal_digest` | 한 달 또는 계절의 여러 소규모 event | 날짜 card와 mosaic summary |
| `mixed_archive` | 통일된 주제가 약한 일상 사진 | 날짜별 contact chapter와 짧은 사실 요약 |

테마 분류가 불확실하면 `mixed_archive`를 사용한다. LLM이 새로운 theme 이름을 만들어 renderer 계약을 깨지 못하도록 enum으로 제한한다.

### 허용된 Storyboard module

LLM이 화면을 직접 디자인하는 느낌은 유지하되, 다음 module을 조합하는 방식으로 제한한다.

```text
cover_hero             대표 사진 + 제목 + 날짜 범위
opening_dek            1~3문장의 도입부
date_divider           날짜와 시간대 구분
location_chapter       안전한 장소 label + 위치 근거
lead_landscape         가로 대표 사진 1장
lead_portrait          세로 대표 사진 1장과 여백
diptych                관련 사진 2장
triptych               관련 사진 3장
cinematic_strip        가로 흐름 3~5장
detail_pair            전체 장면과 디테일 사진 한 쌍
contact_grid           여러 장 훑어보기
map_interlude          로컬 정적 위치 개요
story_text             근거 기반 짧은 본문
closing_mosaic         마지막 여러 장 요약
facts_drawer           분석·저장·모델 정보
```

각 module은 모바일·태블릿·데스크톱용 고정 CSS contract, 이미지 최대 수, alt text, loading 정책을 가진다. Story Director는 module type과 `photo_ref` 순서만 정한다.

### StoryManifest 계약

```json
{
  "schema_version": "story-manifest-v1",
  "theme": "travel_journal",
  "title": "초가을, 바다에서 오래된 도시까지",
  "dek": "9월 2일부터 5일까지 촬영된 추천 사진을 시간과 장소의 흐름으로 묶었습니다.",
  "cover_photo_ref": "p_004",
  "chapters": [
    {
      "chapter_id": "day-2026-09-02-place-01",
      "title": "바다 가까이에서 시작한 오후",
      "date_label": "2026년 9월 2일",
      "location_ref": "loc_01",
      "summary": "오후 시간대의 해변과 도심 장면을 중심으로 8장을 골랐습니다.",
      "claim_refs": ["time_02", "loc_01", "scene_07"],
      "modules": [
        {"type": "lead_landscape", "photo_refs": ["p_004"]},
        {"type": "diptych", "photo_refs": ["p_007", "p_009"]},
        {"type": "contact_grid", "photo_refs": ["p_011", "p_012", "p_014"]}
      ]
    }
  ],
  "closing": "위치가 확인되지 않은 사진 3장은 마지막 일상 기록으로 함께 정리했습니다.",
  "generation": {
    "model_target": "linux-long-context",
    "prompt_version": "story-director-v1",
    "evidence_hash": "opaque",
    "generated_at": "KST timestamp"
  }
}
```

검증기는 다음을 확인한다.

- 모든 `photo_ref`, `location_ref`, `claim_ref`가 evidence allow-list에 존재하는가
- 동일 사진이 의도 없이 여러 chapter의 hero로 반복되지 않는가
- 추천 사진이 manifest에서 누락되면 마지막 contact grid에 포함되는가
- 제목·본문 길이와 chapter 수가 범위 안인가
- 위치 문장에 confidence에 맞는 `확인`, `인근`, `추정` 표현이 있는가
- private 장소와 금지된 인물 표현이 포함되지 않았는가
- module별 허용 사진 수와 종횡비 계약을 만족하는가
- HTML, Markdown image, script, URL 문법이 text에 들어오지 않았는가

검증 실패 시 LLM에 임의 재시도를 반복하지 않는다. 최대 1회 schema repair를 수행하고 다시 실패하면 deterministic fallback StoryManifest를 만든다.

### Story 생성용 모델 경로

현재 Hermes 운영 Router는 `mac-general`과 `linux-long-context` 두 target을 사용하며 별도 `linux-coder` 경로는 폐기됐다. Storyboard는 여러 사진의 시각 문맥, 위치 단서, 긴 구조화 출력과 일관된 편집이 필요하므로 다음이 적합하다.

```text
사진별 기존 VLM 분석 재사용
  + 위치가 불명확한 group의 contact sheet만 Qwen3.8 Flash Next vision 보강
  + 구조화 evidence 전체를 Qwen3.8 Flash Next Story Director에 1회 전달
  = linux-long-context 한 번의 runtime lease 안에서 처리
```

- 신규 추천 사진이 0장이면 Linux를 깨우지 않는다.
- 기존 scene description과 점수를 재사용하고 모든 원본을 다시 vision 처리하지 않는다.
- 위치가 없는 사진은 한 장씩 독립 처리하기보다 날짜·scene group별 4~9장 contact sheet로 문맥을 제공한다.
- 일일 10~30장 수준은 routine context를 64K 이하로 유지한다.
- 사진이 많으면 날짜별 evidence를 먼저 요약한 뒤 report-level Story Director에 전달한다.
- 같은 daily run에서 WOL·model loading은 한 번만 수행하고 story manifest 저장 후 runtime을 release한다.
- Linux 준비 또는 생성이 실패하면 사진 추천·저장 작업은 성공 상태를 유지하고 deterministic Story를 생성한다.

outer Hermes agent가 다시 같은 이미지 분석을 수행하게 해 Linux를 중복 기동하지 않는다. PhotosMcp 내부 Story orchestration이 model lease와 결과 persistence를 소유하고 Hermes는 완료 알림과 결과 URL 전달만 담당한다.

### HTML에서 보이는 생성 결과 예시

```text
┌─────────────────────────────────────────┐
│              [대표 사진]                │
│                                         │
│     초가을, 바다에서 오래된 도시까지    │
│     2026.09.02 — 09.05 · 추천 28장      │
│                                         │
│  네 날 동안 촬영된 사진을 바다, 도심,   │
│  오래된 건축의 흐름으로 묶었습니다.      │
├─────────────────────────────────────────┤
│ 전체 사진 한눈에 보기                    │
│ [ ][ ][ ]                                │
│ [ ][ ][ ]          ← 사진 선택 시 Swiper │
├─────────────────────────────────────────┤
│ 9월 2일                                  │
│ 바다 가까이에서 시작한 오후              │
│ 부산 일대 · GPS 확인                     │
│                                         │
│ [          가로 대표 사진           ]    │
│ [     사진 A     ][      사진 B      ]    │
│                                         │
│ 오후 시간대의 해변과 도심 장면을         │
│ 중심으로 여덟 장을 골랐습니다.            │
├─────────────────────────────────────────┤
│ 9월 3일                                  │
│ 오래된 지붕과 골목의 흐름                │
│ 경주 인근 · 사진 내용으로 추정           │
│                                         │
│ [세로 사진] [세부 사진] [가로 사진]       │
│ [             사진 strip             ]   │
├─────────────────────────────────────────┤
│ 위치를 확인할 수 없는 일상의 장면         │
│ [ ][ ][ ]                                │
├─────────────────────────────────────────┤
│ [장소와 시간 펼치기] [분석 근거 펼치기]   │
│ [저장·앨범·모델 실행 정보 펼치기]         │
└─────────────────────────────────────────┘
```

### 생성 실패와 재생성 정책

Story 생성 실패가 사진 자동화 전체를 실패시키면 안 된다.

| 상황 | 사용자에게 보여줄 결과 | 운영 처리 |
|---|---|---|
| Story 생성 성공 | LLM Storyboard | manifest와 evidence version 저장 |
| 위치 추정만 실패 | Storyboard + `위치 정보 없음` | 사진·앨범 성공 유지 |
| schema validation 실패 | 고정 날짜별 Story | safe error code만 기록 |
| Linux 준비 timeout | 고정 날짜별 Story | 다음 run에서 선택적 재생성 후보 |
| 일부 preview 실패 | 나머지 Story + placeholder | 원본을 대신 노출하지 않음 |
| model/prompt 변경 | 기존 결과 유지 | 명시적 재생성 때 새 version 생성 |

StoryManifest는 생성 당시의 evidence hash, model ID, prompt version과 schema version을 기록한다. 과거 report는 최신 모델로 자동 재작성하지 않는다. 재생성할 경우 기존 manifest를 덮어쓰지 않고 revision을 추가해 비교와 복구가 가능하게 한다.

### 사용자에게 필요한 최소 설정

자동화의 기본값은 다음처럼 단순하게 둔다.

```text
스토리 생성: 켜짐
테마: 자동
위치 표시: 균형 모드
위치 없는 사진 추정: 켜짐
외부 공개 landmark text 검증: 켜짐 또는 별도 opt-in
민감 장소 숨김: 켜짐
언어: 한국어
톤: 사실적이고 따뜻하게
```

사용자가 매번 prompt를 작성할 필요는 없다. 필요하면 Telegram 또는 설정에서 큰 방향만 줄 수 있다.

```text
이번 주말 사진은 여행 일기처럼 정리해줘.
아이 사진은 관계를 추정하지 말고 일상 기록 형태로 보여줘.
위치보다 시간 흐름을 중심으로 구성해줘.
풍경 위주로 차분한 사진집 스타일로 만들어줘.
```

이 입력은 Story Director의 `editorial_brief`로 들어가며 사진 선택 정책, privacy 정책과 asset allow-list를 변경하지 않는다.

### 생성형 확장 채택 결론

다음 요소를 이번 설계에 채택한다.

- LLM 기반 테마·제목·chapter·사진 순서·대표 사진·layout module 선택
- 고정 HTML이 아니라 allow-listed module 조합으로 매 report의 리듬 변화
- 사진별 structured evidence와 claim reference
- GPS 없는 사진의 시간·인접 사진·OCR·landmark 기반 단계적 위치 추정
- 확인 위치와 추정 위치의 명확한 badge
- 필요 시 비민감 공개 landmark text만 외부 검색으로 검증
- Qwen3.8 Flash Next의 vision과 long-context를 한 runtime lease에서 사용
- immutable StoryManifest와 deterministic HTML renderer
- 생성 실패 시 날짜별 고정 Story fallback

다음은 채택하지 않는다.

- LLM이 raw HTML/CSS/JavaScript를 직접 생성하는 방식
- 사진이나 exact GPS를 외부 검색·geocoding 서비스에 전송
- confidence가 낮은 위치를 사실처럼 표기
- 얼굴로 인물 관계·감정·여행 목적을 추측
- report를 열 때마다 LLM을 다시 호출
- 과거 Story를 최신 model 결과로 자동 덮어쓰기
- Story 생성 실패 때문에 추천·로컬 저장·앨범 반영 전체를 실패 처리

## 외부 공유용 단일 Story Package 재검토

### 요구사항 해석

“하나로 묶는다”는 것은 story text, 날짜·장소 chapter, 전체 사진 grid, Swiper viewer와 필요한 공개 metadata를 수신자가 **하나의 URL과 하나의 HTML 문서 흐름**에서 보는 것을 의미한다. 물리적으로 모든 JPEG를 base64로 넣은 거대한 `.html` 한 파일을 기본 방식으로 삼지는 않는다. 브라우저에는 하나의 page로 보이되 이미지 asset은 같은 인증 경계 안에서 필요한 크기로 lazy load한다.

“다른 사람에게 공유한다”는 것은 두 경우로 나눈다.

1. Tailscale을 사용할 수 있는 특정 가족·지인에게 지속적으로 공유
2. Tailscale 설치 없이 휴대폰 browser에서 만료 가능한 링크로 잠시 공유

두 경우의 인증과 공개 범위가 다르므로 하나의 endpoint를 조건문으로 섞지 않는다.

### 공유 방식 비교

| 방식 | 수신자 준비 | 접근 통제 | 회수 | 사용성 | 판단 |
|---|---|---|---|---|---|
| Tailnet Serve + device sharing | Tailscale 설치·로그인·초대 수락 | Tailscale identity와 ACL | 초대 회수 | 안전하지만 진입 절차가 큼 | 가까운 사람의 장기 공유에 선택 제공 |
| Tailscale Funnel + PhotosMcp share auth | 일반 browser | 만료 link + passcode + session | 즉시 폐기 | 가장 간편 | 외부 단기 공유 기본안으로 채택 |
| 완전 공개 Funnel URL | 없음 | URL을 아는 사람 누구나 | Funnel 중지 | 편하지만 전달·검색·preview bot 유출 위험 | 채택하지 않음 |
| base64 단일 HTML 파일 | 파일 다운로드 | 파일 자체 | 사실상 불가능 | offline 가능하지만 크고 모바일 공유 불편 | 선택적 export로만 유예 |
| HTML + assets ZIP | 압축 해제·파일 열기 | 별도 암호 ZIP 가능 | 이미 전달한 사본 회수 불가 | 기술 사용자 외에는 불편 | archive export로만 유예 |

Tailscale 공식 문서에 따르면 Serve는 tailnet 내부 서비스이고 identity header를 제공한다. Device sharing을 수락한 외부 Tailscale 사용자에게도 Serve identity header가 제공된다. Funnel은 Tailscale이 없는 사람에게 공개 internet으로 연결할 수 있지만 identity header가 없으며 같은 port를 Serve와 Funnel이 동시에 사용할 수 없다.

참고:

- <https://tailscale.com/docs/features/tailscale-serve>
- <https://tailscale.com/docs/features/tailscale-funnel>
- <https://tailscale.com/kb/1084/sharing>
- <https://tailscale.com/docs/reference/funnel-vs-sharing>

### 현재 경로와 변경하지 않을 경계

2026-09-06 확인된 실제 상태는 다음과 같다.

```text
Tailscale 1.98.9

443 Serve — tailnet only
├── /                -> Open WebUI 127.0.0.1:3000
└── /photos-actions  -> PhotosMcp 127.0.0.1:18791/actions

9119 Serve — tailnet only
└── /                -> Hermes Dashboard 127.0.0.1:9120

Funnel — 비활성
```

Funnel은 `443`, `8443`, `10000`만 사용할 수 있으며 같은 port의 마지막 설정이 Serve인지 Funnel인지에 따라 port 전체가 private 또는 public이 된다. 따라서 다음은 금지한다.

- 기존 `443`을 Funnel로 전환
- 기존 Open WebUI와 `/photos`를 public path와 같은 listener에 배치
- `9119` Dashboard를 외부 공유에 재사용
- Funnel 요청에서 존재하지 않는 Tailscale identity header를 인증에 사용

외부 공유 전용으로 `8443`을 사용한다.

```text
소유자
  → https://byoungyoung-macmini.tail53bcc7.ts.net/photos/...
  → 443 Serve · Tailscale identity 필수

외부 수신자
  → https://byoungyoung-macmini.tail53bcc7.ts.net:8443/s/{share_id}
  → 8443 Funnel · Share Gateway 자체 인증
  → 127.0.0.1의 별도 loopback share listener
```

`8443` Funnel은 `/share/*` 전용 backend 한 곳만 proxy한다. Share Gateway에는 MCP, health 상세, Open WebUI, Hermes Dashboard, 내부 report listing과 관리 API를 등록하지 않는다.

### 채택 구조: SharedStoryPackage

내부 StoryManifest를 live 참조해 그대로 공개하지 않고 공유 시점의 안전한 사본을 만든다.

```text
Internal StoryManifest revision
  │
  ├─ 사용자가 공유 사진·위치·기간을 확인
  ├─ share_safe privacy projection
  ├─ 공개용 story text 재검증
  ├─ 공개용 thumbnail/preview 사전 생성
  └─ immutable SharedStoryPackage
       ├── shared manifest
       ├── 허용된 image asset 목록
       ├── 만료·폐기 상태
       ├── passcode credential
       └── 최소 view audit
```

공유 package는 다음처럼 저장한다.

```text
SharedStoryPackage
├── share_id                    # 충분히 긴 random public locator
├── source_report_id
├── source_manifest_revision
├── shared_manifest_json
├── privacy_profile: share_safe
├── allowed_asset_ids[]
├── image_policy_version
├── created_at / expires_at / revoked_at
├── credential_hash
├── session_version
├── max_views_optional
├── view_count
└── safe_status
```

`shared_manifest_json`에는 다음을 넣지 않는다.

- exact GPS와 상세 주소
- private location provenance
- 얼굴 이름과 관계
- 내부 추천 점수·model prompt·claim ID
- Apple/Google provider asset ID와 content hash
- 로컬 경로와 저장 root
- 내부 자동화 오류와 debug 정보
- share credential 원문

원본 report가 나중에 바뀌어도 기존 공유 package가 자동으로 새로운 사진이나 위치를 노출하지 않는다. 다시 공유하려면 새 package 또는 새 revision을 명시적으로 만든다.

### 소유자의 공유 생성 화면

내부 Tailnet Story 화면의 상단 메뉴에 `공유`를 두되, 클릭 한 번으로 즉시 공개하지 않는다.

```text
┌─────────────────────────────────────┐
│ 이 Story 공유                       │
├─────────────────────────────────────┤
│ 1. 공유 미리보기                    │
│    공개될 사진 24장                 │
│    제외할 사진 선택                 │
│                                     │
│ 2. 위치 정보                        │
│    ● 도시·권역만 표시               │
│    ○ 확인된 공개 장소 이름까지      │
│    ○ 위치 모두 숨김                 │
│                                     │
│ 3. 유효 기간                        │
│    ● 30일  ○ 24시간  ○ 7일          │
│                                     │
│ 4. 잠금                             │
│    ● 별도 코드 필요                 │
│    ○ 링크만으로 열기                │
│                                     │
│ 5. 포함 정보                        │
│    ☑ Story와 사진                   │
│    ☑ 날짜                           │
│    ☑ 안전한 장소                    │
│    ☐ 사진 분석 점수                 │
│                                     │
│ [취소]                  [공유 만들기] │
└─────────────────────────────────────┘
```

기본값은 다음으로 한다.

```text
privacy_profile: share_safe
위치: 도시·권역만
유효 기간: 30일(기본)
잠금: 별도 코드 필요
공유본 download: 활성(원본·EXIF 제외)
사진 분석 점수·provider·저장 정보: 비포함
검색 engine·social image preview: 비활성
```

공개될 최종 문장과 사진을 보여주는 `공유 미리보기`가 authoritative UI다. 사용자가 제외한 사진은 shared manifest와 asset allow-list 양쪽에서 제거한다.

### 30일 공유와 다운로드 정책

외부 공유의 기본 TTL은 생성 시점부터 정확히 30일이다. 더 짧은 공유가 필요할 때만 24시간 또는 7일을 선택하며, 30일 이후에는 HTML, thumbnail, preview, download가 모두 동일하게 `410 Gone`이 된다. 만료 시각은 UTC로 저장하고 화면에는 Asia/Seoul 기준 날짜와 시간을 함께 표시한다.

수신자에게 제공하는 다운로드 파일은 recommendation root의 원본이 아니다. PhotosMcp가 공유 패키지를 만들 때 다음 고정 정책으로 생성한 공유 전용 파생본이다.

| 항목 | 정책 |
|---|---|
| 형식 | sRGB JPEG |
| 최대 크기 | 긴 변 2048px, 작은 사진은 확대하지 않음 |
| 품질 | JPEG quality 88, optimize 적용 |
| 메타데이터 | EXIF, GPS, XMP, ICC 이외 ancillary metadata 제거 |
| 파일명 | `photo-001.jpg`처럼 공유 순번만 사용 |
| HTTP | session + package asset allow-list 검증 후 `Content-Disposition: attachment` |
| 캐시 | private/no-store, 공유 폐기·만료 cleanup 대상 |

기본 UI는 큰 사진 보기의 `사진 저장` 버튼과 grid item의 접근 가능한 download action을 제공한다. 공유 생성자는 `공유본 다운로드 허용`을 끌 수 있으며 기본값은 켜짐이다. v1에서는 무제한 ZIP 다운로드를 만들지 않고 개별 파일만 제공해 메모리·대역폭 폭주와 전체 패키지 유출 위험을 줄인다.

공유본 생성에 실패하면 원본으로 fallback하지 않는다. 해당 사진의 보기·다운로드를 비활성화하고 owner 화면에 오류를 표시한다. 다운로드 URL은 `share_id`와 불투명한 `asset_id`만 사용하며 원본 파일명, provider ID, 로컬 상대 경로와 content hash를 포함하지 않는다.

### 링크와 passcode 전달

가장 안전하고 이해하기 쉬운 기본 흐름은 링크와 짧은 잠금 코드를 분리하는 것이다.

```text
공유 링크
https://byoungyoung-macmini.tail53bcc7.ts.net:8443/s/{share_id}

잠금 코드
별도 메시지로 전달
```

링크 자체만 credential로 쓰는 `magic link` mode도 선택할 수 있지만 기본값으로 두지 않는다. 메신저의 link preview bot, browser history, 전달된 message와 screenshot을 통해 URL이 복사될 수 있기 때문이다.

권장 unlock 흐름:

```text
GET /s/{share_id}
  → 제목·사진 없는 일반 잠금 화면
  → passcode 입력
  → rate-limit과 credential hash 검증
  → Secure + HttpOnly + SameSite=Lax session cookie 발급
  → Story HTML과 image asset 접근 허용
```

- credential 원문은 저장하지 않고 강한 password KDF hash만 저장한다.
- share ID, IP category와 실패 횟수를 결합해 rate-limit한다.
- 연속 실패는 지연과 일시 잠금을 적용한다.
- image URL에 passcode와 bearer token을 넣지 않는다.
- unlock 뒤 image 요청도 session cookie와 package asset allow-list를 검사한다.
- cookie는 package 만료·폐기·session version 변경 즉시 무효화한다.
- URL query에 secret, email, 좌표를 넣지 않는다.

### 메신저 link preview와 crawler 대응

Telegram, KakaoTalk, Messages 등은 사용자가 열기 전에 bot으로 URL을 fetch할 수 있다. 이 요청이 첫 view를 소비하거나 cover image를 가져가면 안 된다.

- `GET`만으로 one-time token을 소비하지 않는다.
- unlock 전 HTML에는 사진 URL, Story 제목, 날짜·장소를 넣지 않는다.
- `og:image`, 외부 thumbnail과 공개 JSON-LD를 제공하지 않는다.
- `X-Robots-Tag: noindex, nofollow, noarchive, noimageindex`를 설정한다.
- `robots.txt` 차단은 보조 수단으로만 사용하고 인증을 대체하지 않는다.
- `Referrer-Policy: no-referrer`를 유지한다.
- unlock page와 Story는 `Cache-Control: no-store`를 기본으로 한다.

따라서 메신저에서는 “개인 사진 Story가 공유되었습니다” 정도의 일반 preview만 보이고 실제 사진은 passcode를 입력한 사람에게만 나타난다.

### 수신자 화면

수신자는 관리·분석 화면이 아니라 하나의 정돈된 Story만 본다.

```text
잠금 전
┌─────────────────────────────────────┐
│ 사진 Story가 공유되었습니다          │
│ 공유자가 전달한 코드를 입력하세요.   │
│ [        잠금 코드        ]          │
│ [열기]                              │
└─────────────────────────────────────┘

잠금 후
┌─────────────────────────────────────┐
│ [대표 사진]                         │
│ 초가을, 바다에서 오래된 도시까지    │
│ 2026.09.02—09.05                    │
├─────────────────────────────────────┤
│ Story chapter                       │
│ 사진 module · 날짜 · 안전한 장소     │
├─────────────────────────────────────┤
│ 전체 사진 보기                      │
│ [ ][ ][ ]                           │
│ 선택 → Swiper 한 장 보기            │
├─────────────────────────────────────┤
│ 이 공유는 2026.09.13 만료됩니다.     │
└─────────────────────────────────────┘
```

수신자 화면에서 제외한다.

- 공유 관리·재생성·삭제 버튼
- 원본 download. 단, 공유 전용 2048px JPEG 개별 다운로드는 허용한다.
- 내부 추천 점수와 기술 분석
- provider, 로컬 저장 경로와 앨범 영수증
- 위치 추론 상세 근거와 exact 좌표
- 모델명·prompt·token·오류
- 다른 report 탐색과 listing

Swiper, semantic HTML, keyboard, reduced motion, mobile safe area와 JavaScript fallback은 내부 Story와 동일하게 유지한다.

### 공유 위치·문장 projection

내부 `personal_detailed` Story를 그대로 공유하지 않는다. 공유 package는 항상 별도의 `share_safe` projection을 만든다.

| 내부 표시 | 공유 기본 표시 |
|---|---|
| `경복궁 · GPS 확인` | `서울 종로구` 또는 사용자가 허용하면 `경복궁` |
| `해운대 인근 · 사진 내용으로 추정` | `부산 일대 · 위치 추정` |
| `개인 장소 · 상세 위치 숨김` | 위치 section 자체를 제거 |
| 촬영 분 단위 시각 | 날짜 또는 넓은 시간대 |
| 위치 추정 confidence 78% | `위치 추정` badge만 |
| 내부 evidence provenance | 공개하지 않음 |

Story 문장에 상세 위치나 개인 단서가 들어 있으면 단순 문자열 마스킹만 하지 않는다. share-safe evidence를 사용해 해당 chapter의 제목·요약을 다시 생성하거나, 실패하면 결정론적 중립 문장으로 교체한다. 공유 생성 화면에서 최종 공개 문장을 반드시 미리 본다.

### 공유 이미지 derivative

내부 preview cache를 public route가 직접 가리키지 않게 공유 전용 derivative를 만든다.

```text
share thumbnail: grid용 480px 내외
share preview: viewer용 긴 변 1600~2048px
format: 안전한 JPEG 또는 검증된 WebP
metadata: EXIF/GPS/XMP 제거
원본 route: 없음
filename: random opaque ID
```

각 asset 요청은 `share_id + session + allowed_asset_id`를 모두 검사한다. URL을 추측해도 같은 package 밖의 사진은 읽을 수 없다. 만료·폐기 후 공유 derivative와 session을 정리하고 내부 추천 사본은 유지한다.

워터마크는 선택 기능으로 둘 수 있지만 보안 수단으로 간주하지 않는다. browser에서 볼 수 있는 사진은 screenshot이나 화면 촬영으로 복제될 수 있다는 안내를 공유 생성 화면에 표시한다.

### 공유 수명주기

```text
draft
  → previewed
  → active
  → expired | revoked
  → derivative cleanup completed
```

소유자 관리 화면에는 다음만 제공한다.

- Story 이름과 cover
- 생성 시각·만료 시각
- active/expired/revoked 상태
- 공개 사진 수와 위치 공개 수준
- 집계 view 수와 마지막 접근 시각
- 링크 복사, 코드 재발급, 즉시 공유 중지

view log는 원시 IP, user-agent 전체, 사진별 열람 기록을 장기 보존하지 않는다. 보안 rate-limit용 단기 hash/category와 전체 view count 정도만 기록한다.

활성 공유가 하나도 없으면 Funnel을 자동으로 중지하는 정책을 검토한다. 최소한 Share Gateway는 active package가 없을 때 모든 `/s/*`에 generic 404를 반환해야 한다.

### 물리적 단일 HTML export

인터넷 연결 없이 파일 하나로 전달해야 하는 경우에만 별도의 `Export as portable HTML`을 후속 기능으로 제공한다.

```text
하나의 .html
├── sanitized StoryManifest
├── inline CSS/JavaScript
└── base64 encoded share previews
```

장점:

- 서버나 Mac mini가 꺼져도 열림
- 하나의 파일만 전달하면 됨
- 외부 CDN과 API가 필요 없음

약점:

- 20~30장만 들어가도 파일이 수십 MB가 될 수 있음
- 이미 전달한 파일을 만료·폐기할 수 없음
- 모바일 messenger와 browser에서 큰 파일 처리가 불편함
- Story, 사진과 위치 text가 파일 내부에 영구적으로 남음
- 열람 count, passcode rate-limit과 session 통제를 제공하기 어려움

따라서 기본 공유는 web Share Package로 하고 portable HTML은 사용자가 영구 사본 전달을 명시적으로 선택할 때만 만든다. portable export는 위치를 모두 숨기는 `share_safe` profile과 낮은 해상도 preview를 기본으로 한다.

### 운영 가용성 한계

Funnel 공유 링크는 Mac mini, Tailscale과 Share Gateway가 동작할 때만 열린다. Tailscale 공식 문서상 Funnel traffic에는 비구성 bandwidth limit도 있다. 대규모 공개 앨범이나 다수 수신자용 CDN으로 사용하지 않는다.

권장 운영 범위:

- 가족·지인 소수에게 단기 공유
- report당 약 20~100장
- 기본 30일. 24시간 또는 7일로 줄일 수 있고, 30일을 넘기는 공유는 이번 범위에서 허용하지 않는다.
- 동시 active package 수 제한
- 원본과 video 미제공. 공유 패키지에 포함된 사진은 EXIF/GPS/XMP를 제거한 2048px 이내 JPEG 공유본만 개별 다운로드할 수 있다.
- thumbnail lazy load와 viewer 인접 preview만 preload
- 대량·장기 공유가 필요해지면 별도 object storage와 인증 gateway를 재검토

### 외부 공유 채택 결론

기본안으로 다음을 채택한다.

- 하나의 responsive Story HTML URL
- 내부 report와 분리된 immutable SharedStoryPackage
- 소유자의 공유 미리보기와 사진 제외
- 항상 `share_safe` privacy projection
- 기본 30일 만료와 별도 passcode
- 8443 Funnel에 외부 공유 전용 loopback gateway만 연결
- unlock 전 사진·제목·위치와 OpenGraph image 비노출
- session cookie와 package별 asset allow-list
- 즉시 폐기와 derivative cleanup
- 내부 Story의 contact grid와 Swiper 감상 경험 재사용

다음은 채택하지 않는다.

- 기존 443 Serve 또는 9119 Dashboard의 public 전환
- Funnel URL만 알면 바로 사진이 보이는 완전 공개 방식
- 내부 StoryManifest와 내부 preview route의 직접 공유
- LLM 분석 점수, 내부 경로, provider metadata의 외부 공개
- exact GPS, 민감 장소와 detailed location evidence 공개
- 원본 download와 삭제·앨범 mutation. 공유 전용 파생본 download는 별도 허용 자산 검사 후 제공한다.
- base64 단일 HTML을 기본 공유 방식으로 사용

## Grid + Swiper 설계

### 버전과 배포 방식

2026-09-06 npm registry의 `latest`는 Swiper `14.2.0`이며 MIT license다. 구현 시 정확한 버전과 배포 파일의 hash를 고정한다.

권장 방식:

```text
src/photos_mcp/interfaces/http/static/vendor/swiper/14.2.0/
├── swiper-bundle.min.css
├── swiper-bundle.min.js
└── LICENSE
```

현재 PhotosMcp에는 별도의 Node frontend build가 없다. 따라서 Node를 운영 의존성으로 추가하지 않고, 검증된 browser bundle을 앱 resource에 포함하는 방식이 가장 단순하다. 버전 갱신은 별도 dependency update 작업으로 수행한다.

CDN은 사용하지 않는다. CDN을 사용하면 다음 문제가 생긴다.

- 외부 네트워크가 끊기면 Tailnet 내부 결과 화면도 깨진다.
- 개인 사진 화면의 접속 metadata가 외부 CDN으로 전달될 수 있다.
- 현재 `default-src 'none'` 기반 CSP를 약화해야 한다.
- CDN의 최신 파일 변경과 앱 배포 버전이 분리된다.

공식 Swiper 문서는 npm 설치, CDN, 로컬 asset 방식을 모두 지원한다. 이 설계에서는 privacy와 offline 동작을 위해 로컬 asset 방식을 선택한다.

참고:

- <https://swiperjs.com/get-started>
- <https://swiperjs.com/swiper-api>
- <https://github.com/nolimits4web/swiper>

### 기본 grid

기본 목록은 Swiper의 Grid module이 아니라 semantic HTML과 CSS Grid로 구현한다. 목록 자체는 세로 scroll을 사용하므로 모바일의 자연스러운 탐색과 browser 접근성을 유지할 수 있다.

권장 column 수:

| viewport | column | card 간격 |
|---|---:|---:|
| 359px 이하 | 2 | 8px |
| 360~767px | 2 | 10px |
| 768~1099px | 3 | 12px |
| 1100px 이상 | 4 | 14px |

상단 contact sheet의 첫 구현은 정사각형 card와 `object-fit: cover`를 사용하고, 선택 후 큰 viewer에서는 `object-fit: contain`으로 사진 전체를 보여준다. Story chapter는 종횡비 snapshot이 준비되면 `대표 1장 + 보조 2장 + 일반 grid` 템플릿으로 실제 가로·세로 리듬을 반영한다. card에는 촬영일과 provider를 screen reader용 text로 제공하고 시각적으로는 불필요한 긴 파일명을 표시하지 않는다.

각 card는 실제 `<button type="button">`으로 만들어 touch, keyboard, screen reader에서 같은 방식으로 열리게 한다. 다중 선택 checkbox는 사용하지 않는다.

### Swiper viewer 모듈과 동작

첫 버전에서 사용하는 기능은 다음으로 제한한다.

| 기능 | 설정 | 이유 |
|---|---|---|
| 선택 index 시작 | `initialSlide` | grid에서 누른 사진부터 열기 |
| 한 장 크게 보기 | `slidesPerView: 1` | viewer 안에서 한 장씩 보기 |
| 터치 이동 | 기본 활성화 | Android/iPhone 좌우 스와이프 |
| 좌우 버튼 | Navigation | 데스크톱과 접근성 보조 |
| 위치 표시 | fraction Pagination | `현재 / 전체`를 명확하게 표시 |
| 키보드 | Keyboard | 좌우 방향키로 이동 |
| 접근성 | A11y | 한국어 이전·다음 안내와 slide label |
| 확대 | Zoom | pinch와 double tap으로 미리보기 확대 |
| 인접 preload | `lazyPreloadPrevNext: 1` | 다음 사진 전환 지연 감소 |
| native lazy load | `loading="lazy"` | 전체 사진을 한꺼번에 전송하지 않음 |

다음 기능은 사용하지 않는다.

- autoplay: 사용자가 사진을 읽는 속도를 방해하고 접근성을 낮춘다.
- loop: 처음과 끝을 오인하게 만들고 사진 수 확인을 어렵게 한다.
- coverflow·cube 같은 장식 효과: 사진 비교와 안정성에 도움이 되지 않는다.
- mousewheel slide: 모바일 중심 화면에서 페이지 스크롤과 충돌할 수 있다.
- viewer 내부 thumbnail strip: 기본 grid가 이미 전체 탐색 역할을 하므로 중복 UI를 만들지 않는다.

권장 초기 설정은 다음 의미를 갖는다. 실제 코드는 승인 이후 작성한다.

```javascript
{
  initialSlide: selectedIndex,
  slidesPerView: 1,
  loop: false,
  rewind: false,
  autoHeight: false,
  speed: 260,
  lazyPreloadPrevNext: 1,
  navigation: true,
  pagination: { type: 'fraction' },
  keyboard: { enabled: true, onlyInViewport: true },
  a11y: {
    prevSlideMessage: '이전 추천 사진',
    nextSlideMessage: '다음 추천 사진',
    slideLabelMessage: '{{index}} / {{slidesLength}}'
  },
  zoom: { maxRatio: 3 }
}
```

viewer를 열 때 선택한 slide 이미지를 우선 요청하고, 앞뒤 한 장만 preload한다. 나머지 큰 preview는 `loading="lazy"`를 유지한다. Swiper 9 이후에는 별도 lazy API보다 browser native lazy loading을 사용하는 것이 공식 방식이다.

viewer는 `<dialog>` 또는 동등한 접근성 modal로 구현한다. modal은 `100dvh` 고정 높이로 두고 사진 canvas와 inspector/bottom sheet를 분리해 caption 길이가 달라져도 화면이 흔들리지 않게 한다. 열릴 때 background scroll을 잠그고, 닫기 버튼·`Escape`·browser 뒤로가기로 닫을 수 있게 한다. URL fragment에 현재 slide index를 선택적으로 반영하면 화면 회전이나 browser history에서도 상태를 복원할 수 있지만 asset ID나 credential은 fragment에 넣지 않는다.

### JavaScript 실패 시 fallback

서버는 grid card, slide와 caption을 HTML로 먼저 렌더링한다. JavaScript가 차단되면 grid는 그대로 여러 장을 보여준다. 각 card의 fallback link는 별도 단일 사진 HTML로 이동한다. Swiper 초기화만 실패하면 modal 내부에서 CSS scroll-snap으로 한 장씩 넘길 수 있게 한다.

따라서 여러 장 훑어보기와 단일 사진 확인 모두 Swiper 초기화 성공에만 의존하지 않는다.

## 표시 대상과 순서

### 표시 대상

갤러리에 포함할 수 있는 사진은 다음 조건을 모두 만족해야 한다.

1. `recommendation_members.materialization_status`가 완료 상태다.
2. 연결된 `local_recommendation_assets`가 존재한다.
3. recommendation root를 기준으로 상대 경로가 안전하게 해석된다.
4. 실제 파일이 regular file로 존재한다.
5. content hash 또는 저장 영수증 검증이 완료됐다.
6. MIME이 허용된 이미지 형식이다.
7. `resource_role=primary`다.

분석 대상이지만 추천되지 않은 사진, 다운로드만 된 Google Picker 임시 사진, 실패·취소된 사진, video는 표시하지 않는다.

### event별 범위

- 추천 보관 event: event의 `collection_id`에 포함된 추천 사진만 표시한다.
- 0건 완료 event: 사진 영역 대신 “이번 실행의 신규 추천 사진이 없습니다” empty state를 표시한다.
- 오류 event: 오류 이전에 검증 완료된 추천 사진이 있으면 그 사진만 표시하고 partial badge를 붙인다.
- Picker 사용자 조치 event: 아직 추천 결과가 없으므로 갤러리를 표시하지 않고 사용자 조치 안내를 표시한다.

### run별 범위

run 화면은 같은 `automation_run_id`에서 만들어진 collection의 추천 사진만 표시한다. 여러 provider 결과가 연결된 경우 content hash가 같은 사진은 slide 하나로 합치고 Apple Photos와 Google Photos provenance badge를 함께 표시한다.

### group별 범위

월별 group 화면은 `recommendation_group_members`를 기준으로 한다. 기본 정렬은 촬영 시각 최신순이며 날짜가 같으면 저장된 상대 경로와 local asset ID로 안정 정렬한다.

collection 화면은 `recommendation_slot`과 `scene_cluster_id`를 우선 사용해 모델이 선택한 순서를 유지한다.

첫 버전의 한 화면 최대 slide 수는 200으로 제한한다. 200장을 초과하는 월별 group은 날짜 구간을 나눠 다음 page로 이동한다. 현재 일일 Picker 최대 100장과 운영 추천량에는 충분한 범위다.

## 이미지 제공 방식

### 원본 직접 제공 금지

recommendation root의 원본 파일을 Tailscale route에 직접 매핑하지 않는다. Tailscale Serve의 directory serving도 사용하지 않는다. 반드시 PhotosMcp HTTP handler가 DB의 local asset ID를 검증한 후 web preview만 응답한다.

권장 내부 route:

```text
GET /ui/media/{local_asset_id}/thumbnail
GET /ui/media/{local_asset_id}/preview
```

권장 외부 route:

```text
GET /photos/media/{local_asset_id}/thumbnail
GET /photos/media/{local_asset_id}/preview
```

handler의 필수 절차:

1. local asset ID 형식을 allow-list 정규식으로 검증한다.
2. repository에서 ID에 해당하는 자산을 조회한다.
3. 저장 root와 `relative_path`를 결합해 `resolve()`한다.
4. resolved path가 반드시 recommendation root 하위인지 확인한다.
5. symlink와 directory를 거부한다.
6. regular file과 허용 MIME을 확인한다.
7. 요청 variant에 따라 grid thumbnail 또는 큰 preview cache를 생성하거나 검증된 기존 결과를 읽는다.
8. `Content-Type`, `Content-Length`, `X-Content-Type-Options`를 명시한다.
9. 절대 경로와 EXIF를 응답에 넣지 않는다.

### thumbnail과 web preview 정책

grid용 thumbnail과 큰 viewer용 preview를 분리한다. 여러 장 grid가 원본 또는 2048px 이미지를 한꺼번에 가져오지 않게 하는 것이 핵심이다.

| 항목 | Grid thumbnail | Viewer preview |
|---|---|---|
| 포맷 | JPEG | JPEG |
| pixel 크기 | 480×480 이내, crop variant | 긴 변 최대 2048px, 원본 비율 |
| 품질 | 78 전후 | 84 전후 |
| 화면 표시 | `object-fit: cover` | `object-fit: contain` |
| 방향 | EXIF orientation을 실제 pixel에 반영 | EXIF orientation을 실제 pixel에 반영 |
| metadata | EXIF, GPS, camera serial, face region 등 제거 | EXIF, GPS, camera serial, face region 등 제거 |
| color | sRGB로 정규화 | sRGB로 정규화 |
| 확대 | 확대하지 않음 | browser에서 최대 3배, 원본 해상도는 제공하지 않음 |
| cache key | content hash + thumbnail policy version | content hash + preview policy version |

현재 저장 사진은 모두 JPEG지만, 이후 HEIC·RAW가 들어와도 같은 preview contract로 변환한다. 기존 `viewer_asset_service`의 안전한 source/preview 선택과 RAW render/cache 패턴을 재사용하되 웹 미리보기는 별도의 정책 버전과 cache root를 사용한다.

권장 cache 위치:

```text
~/.photos-mcp/cache/web-previews/v1/thumb/{content_hash}.jpg
~/.photos-mcp/cache/web-previews/v1/large/{content_hash}.jpg
```

cache directory는 `0700`, 파일은 `0600`을 유지한다. 원본 hash가 바뀌거나 preview policy version이 바뀌면 새로 생성한다.

### 응답 cache 정책

grid와 Swiper 전환 성능, 개인정보 보호 사이의 균형을 위해 image 응답은 다음을 권장한다.

```text
Cache-Control: private, max-age=300
Referrer-Policy: no-referrer
X-Content-Type-Options: nosniff
```

5분 browser cache는 Tailnet 내부 grid와 viewer 사이를 이동하거나 앞뒤 slide를 볼 때 재전송을 줄인다. 더 강한 privacy가 필요하면 사용자 설정으로 `no-store`를 선택할 수 있게 한다. HTML과 JSON 상태 응답은 기존처럼 `no-store`를 유지한다.

공개 Share Gateway의 HTML, session response와 share image derivative는 첫 구현에서 모두 `Cache-Control: no-store`를 사용한다. 성능 측정 뒤 session-scoped private image cache를 검토할 수 있지만, 폐기된 공유의 사진이 browser disk cache에 오래 남지 않는 것을 우선한다.

## HTML route 설계

### 내부 route

```text
GET /ui/
GET /ui/events/{request_id}
GET /ui/runs/{automation_run_id}
GET /ui/groups/{group_id}
GET /ui/photos/{local_asset_id}
GET /ui/media/{local_asset_id}/thumbnail
GET /ui/media/{local_asset_id}/preview
GET /ui/assets/results.css
GET /ui/assets/results.js
GET /ui/assets/vendor/swiper/14.2.0/swiper-bundle.min.css
GET /ui/assets/vendor/swiper/14.2.0/swiper-bundle.min.js
```

### Tailnet route

Tailscale Serve에는 기존 root와 Dashboard 설정을 건드리지 않고 `/photos` mount만 추가한다.

```text
https://byoungyoung-macmini.tail53bcc7.ts.net/photos/... -> http://127.0.0.1:18791/ui/...
```

기존 경로와의 관계:

```text
/                    Open WebUI 유지
/photos/...          추천 결과 HTML과 preview
/photos-actions/...  기존 사용자 조치 링크 호환
:9119/               Hermes Dashboard 유지
```

Tailscale Serve는 Tailnet 내부에서만 HTTPS reverse proxy를 제공하고 identity header를 backend에 전달한다. backend는 계속 localhost에서만 수신해야 한다. 이는 identity header spoofing 범위를 로컬 프로세스로 제한하기 위한 Tailscale 공식 권장사항과 일치한다.

참고:

- <https://tailscale.com/docs/features/tailscale-serve>
- <https://tailscale.com/docs/concepts/tailscale-identity>

### 공개 공유 route

별도 loopback Share Gateway는 공개에 필요한 최소 route만 제공한다.

```text
GET  /s/{share_id}                              잠금 화면 또는 Story
POST /s/{share_id}/unlock                       passcode 검증과 session 발급
GET  /s/{share_id}/media/{public_asset_id}/thumbnail
GET  /s/{share_id}/media/{public_asset_id}/preview
GET  /assets/shared-story.css
GET  /assets/shared-story.js
GET  /assets/vendor/swiper/14.2.0/...
```

share 생성·목록·폐기 API는 public gateway에 두지 않고 443 Tailnet owner route에만 둔다.

```text
POST /ui/reports/{report_id}/shares
GET  /ui/shares
POST /ui/shares/{share_id}/revoke
POST /ui/shares/{share_id}/rotate-code
```

개념적 외부 연결은 다음과 같다.

```text
https://byoungyoung-macmini.tail53bcc7.ts.net:8443/s/...
  -> Tailscale Funnel 8443
  -> 별도 127.0.0.1 Share Gateway
```

## 인증과 개인정보 보호

사진 preview는 일반 상태 정보보다 민감하므로 request ID의 난수성만 인증 수단으로 사용하지 않는다.

### 인증 경계

Tailnet `/photos` 요청은 다음을 모두 만족해야 한다.

1. Tailscale Serve를 통해 들어온 HTTPS 요청이다.
2. Tailscale identity header가 존재한다.
3. header의 login이 환경 설정의 정확한 allow-list와 일치한다.
4. 해당 443 port는 Serve로만 구성되어 있고 Funnel이 아니다.

권장 설정 이름:

```text
PHOTOS_MCP_TAILSCALE_ALLOWED_LOGINS
PHOTOS_MCP_PUBLIC_RESULTS_BASE_URL
PHOTOS_MCP_WEB_PREVIEW_CACHE_POLICY
```

허용 login 값은 문서와 로그에 기록하지 않는다. 앱 설정 또는 권한이 제한된 runtime 환경으로 주입한다.

localhost에서 직접 여는 개발·진단 요청은 loopback일 때만 허용한다. 외부에서 임의 identity header를 보낼 수 없도록 앱은 계속 `127.0.0.1`에만 bind한다.

### 공개 Share Gateway 인증 경계

Funnel traffic에는 Tailscale identity header가 없으므로 다음을 모두 만족해야 한다.

1. 요청한 `share_id`가 active이고 만료·폐기되지 않았다.
2. unlock 전에는 일반 잠금 HTML 외에는 아무 자료도 반환하지 않는다.
3. passcode 또는 명시적으로 선택한 magic-link credential이 검증됐다.
4. 검증된 browser session에는 Secure, HttpOnly, SameSite cookie가 있다.
5. image와 Story 요청 모두 share session과 package asset allow-list를 다시 검사한다.
6. credential 실패 rate-limit과 session version 폐기가 동작한다.
7. public listener에는 owner 관리, report listing, MCP, health 상세 route가 없다.

내부 Tailscale identity와 외부 share session은 서로 대체할 수 없다. Funnel에서 `Tailscale-User-Login` 같은 header를 받더라도 무시하며, Share Gateway는 외부가 보낸 identity header를 인증 자료로 사용하지 않는다.

### HTML 보안 header

현재 action page의 보안 header를 유지하면서 외부 asset을 허용하지 않는 CSP로 확장한다.

```text
default-src 'none'
img-src 'self'
script-src 'self'
style-src 'self'
connect-src 'self'
base-uri 'none'
form-action 'none'
frame-ancestors 'none'
```

inline script는 사용하지 않는다. 가능하면 inline style도 제거해 `style-src 'self'`만 유지한다. Swiper, 결과 UI JavaScript, CSS와 이미지는 모두 동일 origin에서 제공한다.

추가 header:

```text
Referrer-Policy: no-referrer
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
Cross-Origin-Resource-Policy: same-origin
Permissions-Policy: camera=(), microphone=(), geolocation=()
```

공개 Share Gateway에는 다음을 추가한다.

```text
Cache-Control: no-store
X-Robots-Tag: noindex, nofollow, noarchive, noimageindex
Cross-Origin-Opener-Policy: same-origin
```

unlock 전 page에는 `og:image`, Story title, 날짜·장소와 image preload를 넣지 않는다. 공개 Story에서도 analytics, external font, map tile, social SDK를 사용하지 않는다.

### 화면에서 제외할 정보

- recommendation root의 절대 경로
- 파일의 원래 전체 이름이 불필요하게 개인 정보를 포함하는 경우 그 이름
- 정확한 GPS 좌표, 전체 주소와 좌표가 포함된 외부 지도 URL
- OAuth token, refresh token, API key
- Chrome profile 경로
- Mac 사용자명
- 원본 provider download URL
- 상세 exception과 command line
- 얼굴 embedding과 내부 feature vector

사진 caption은 촬영 날짜, provider, 추천 순위, 핵심 추천 이유, 로컬 저장·앨범 반영 상태와 privacy policy를 통과한 위치 label로 제한한다. 기본 `balanced` mode는 coarse 위치만 사용하고 `personal_detailed`도 좌표 숫자가 아닌 검증된 장소 label만 허용한다. 집·직장·학교·병원 등 민감 장소는 기본적으로 `개인 장소 · 상세 위치 숨김`으로 치환하고 raw 좌표는 HTML, DOM, JSON, URL, log 어느 곳에도 포함하지 않는다.

## server-side view model

HTML renderer가 DB payload 전체를 template에 넘기지 않도록 명시적 view model을 둔다.

```text
StoryAlbumReportView
├── report_id / report_type
├── page_title
├── status / status_label
├── provider_labels[]
├── local_run_date_kst
├── view_mode: story | grid
├── story
│   ├── manifest_revision / schema_version
│   ├── theme / editorial_brief
│   ├── title / date_range_label / safe_dek
│   ├── cover_slide_id
│   ├── located_count / unlocated_count
│   └── chapters[]
│       ├── chapter_id / date_label
│       ├── safe_location_label
│       ├── safe_summary
│       ├── claim_refs[]
│       └── modules[]
│           ├── type
│           └── slide_ids[]
├── summary_counts
│   ├── discovered / analyzed / recommended
│   ├── materialized / duplicate / failed
│   └── scene_count
├── slides[]
│   ├── local_asset_id
│   ├── thumbnail_url / preview_url / detail_url
│   ├── capture_at_display / capture_time_confidence
│   ├── pixel_width / pixel_height / aspect_ratio
│   ├── recommendation_slot / selection_reason_labels[]
│   ├── event_label / safe_scene_excerpt
│   ├── quality_band
│   ├── scores
│   │   ├── total / quality / technical
│   │   └── meaningful / family / event / uniqueness
│   ├── scene_cluster_size / scene_cluster_rank
│   ├── safe_location
│   │   ├── visibility / label / precision
│   │   ├── status: verified | context_inferred | visual_inferred | unavailable
│   │   └── confidence / provenance_labels[] / redaction_reason
│   ├── provider_labels[]
│   ├── local_store_state / destination_states[]
│   └── analysis_version / integrity_verified
├── map_summary
│   ├── available
│   ├── static_map_url
│   └── safe_place_clusters[]
├── destination_summary
├── runtime_summary
├── story_generation
│   ├── state / fallback_used
│   ├── model_target / model_id
│   ├── prompt_version / evidence_hash
│   └── generated_at_kst
└── safe_error_summary
```

각 문자열은 server-side escape하고 길이를 제한한다. `preview_url`은 local asset ID로만 생성하고 파일 경로를 포함하지 않는다. exact 좌표, raw EXIF, 사람 이름, provider asset ID, content hash, 내부 오류는 이 view model에 들어가지 않는다.

## 데이터 연결 규칙

### event 화면

1. `request_id`로 `user_action_requests`를 조회한다.
2. payload의 `collection_id` 또는 `automation_run_id`를 읽는다.
3. collection이 있으면 `recommendation_members`를 조회한다.
4. member의 `local_asset_id`로 검증된 로컬 자산을 조회한다.
5. `recommendation_collections.analysis_run_id = photo_results.job_id`와 `recommendation_members.photo_id = photo_results.photo_id`로 분석 결과를 bulk join한다.
6. group membership과 destination receipt를 합쳐 각 slide의 저장 상태를 만든다.
7. recommendation report metadata와 안전한 위치 projection이 있으면 Story chapter를 만든다.
8. 연결 자료가 없으면 message와 empty/error state만 표시한다.

현재 추천 보관 알림에는 `collection_id`와 `automation_run_id`가 있으므로 이 흐름을 바로 적용할 수 있다. 0건 알림에는 run ID가 있어 run summary를 표시할 수 있다.

### structured payload 보강

향후 HTML이 사람이 읽는 `message` 문자열을 다시 parsing하지 않도록 추천 보관 알림 payload에 다음 수치를 명시적으로 추가한다.

```text
recommended_count
materialized_count
new_file_count
duplicate_count
failed_count
group_id
destination_provider
destination_album_name
destination_album_id
```

기존 이벤트는 DB 관계를 조회해 표시하고, 새 이벤트는 structured payload를 우선 사용한다. 두 경로의 결과가 다르면 DB와 완료 영수증을 authoritative source로 사용한다.

### 필요한 읽기 query

N+1 조회와 원시 payload 누출을 피하기 위해 repository에 다음 목적별 query를 둔다.

```text
get_local_recommendation_asset_by_id(local_asset_id)
list_gallery_items_for_collection(collection_id, offset, limit)
list_gallery_items_for_run(automation_run_id, offset, limit)
list_gallery_items_for_group(group_id, offset, limit)
list_destination_receipts_for_assets(local_asset_ids)
load_photo_results_for_ids(job_id, photo_ids)
list_safe_report_locations(report_id)
```

`list_gallery_items_*`는 recommendation member, local asset, photo result, destination receipt와 provider provenance를 한 번에 연결한다. media route는 공개 입력으로 `local_asset_id`만 받고 DB에서 recommendation root 아래의 안전한 상대 경로를 찾아야 한다.

## 상태별 화면

| 상태 | 사진 영역 | 안내 |
|---|---|---|
| 완료, 추천 있음 | 여러 장 grid, 선택 시 Swiper viewer | 저장·앨범 결과 표시 |
| 완료, 추천 0 | empty state | 정상 완료와 중복/신규 0건 설명 |
| 부분 완료 | 검증 완료 사진만 표시 | 실패 수와 재시도 상태 표시 |
| 실패, 추천 없음 | 오류 카드 | 안전한 오류 코드와 다음 조치 |
| 사용자 조치 필요 | 갤러리 미표시 | Picker/login 조치 설명 |
| 만료 | 갤러리 정책에 따라 과거 결과만 표시 | mutation action은 비활성 |
| 존재하지 않는 ID | 404 | 내부 정보 없이 찾을 수 없음 표시 |

알림 status인 `notified`는 작업 실패를 의미하지 않는다. UI에서는 `전달 완료` 같은 별도 delivery badge로 표시하고 실제 작업 status와 분리한다.

## 성능 예산

모바일 Tailnet 접속을 기준으로 다음 예산을 둔다.

| 항목 | 목표 |
|---|---|
| HTML + CSS + JS 압축 전송 | 300KB 이하 목표 |
| grid thumbnail 한 장 | 100KB 이하 권장 |
| viewer preview 한 장 | 500KB 이하 권장, 사진 특성에 따라 예외 기록 |
| grid thumbnail 동시 요청 | browser 기본과 server 최대 6개 제한 |
| viewer에서 동시에 요청하는 큰 preview | 선택 사진과 인접 사진, 최대 3장 |
| 첫 content 표시 | direct Tailnet 기준 2초 이내 목표 |
| slide 전환 | 인접 preload 성공 시 즉시 체감 |
| 첫 grid page | 모바일 24장, 태블릿 36장, 데스크톱 48장; 더 보기는 다음 batch |
| 한 event viewer slide 수 | 최대 200 |
| preview 생성 동시성 | 2~3 worker로 제한 |

preview 생성은 request thread를 장시간 점유하지 않도록 bounded executor를 사용한다. 이미 생성된 content-hash cache를 우선 사용한다. 실패 시 원본을 대신 보내지 않고 placeholder와 재시도 안내를 표시한다.

## 접근성·모바일 기준

- grid card는 최소 88px 이상으로 표시하고 touch target 전체를 button으로 사용한다.
- grid card에 keyboard focus indicator를 제공하고 `Enter` 또는 `Space`로 viewer를 연다.
- viewer는 좌우 swipe 외에 44px 이상의 이전·다음·닫기 버튼을 제공한다.
- 현재 사진 번호와 전체 사진 수를 text로 표시한다.
- screen reader용 한국어 A11y message를 설정한다.
- keyboard 좌우 방향키를 지원한다.
- modal focus trap, `Escape` 닫기, 닫은 뒤 선택했던 grid card로 focus 복원을 지원한다.
- `prefers-reduced-motion: reduce`이면 transition 시간을 최소화한다.
- portrait/landscape 전환 때 `object-fit: contain`과 viewport height를 다시 계산한다.
- iOS safe area를 CSS `env(safe-area-inset-*)`로 반영한다.
- image alt는 “2026-09-05 추천 사진 1/2”처럼 최소 정보만 담고 인물이나 장소를 추측하지 않는다.
- 색상만으로 상태를 구분하지 않고 icon과 text label을 함께 쓴다.

## 구현 단위

### 1단계: Tailscale 인증 경계와 링크 정상화

- PhotosMcp는 `127.0.0.1` bind를 유지한다.
- `/photos`의 HTML, JSON, thumbnail, preview, 정적 map에 동일한 exact identity allow-list를 적용한다.
- 443의 Funnel 비활성, Tailnet ACL과 direct local request 정책을 검증한다.

Hermes Telegram 전송 직전에 모든 event URL을 다음 규칙으로 재생성한다.

```text
{PHOTOS_MCP_PUBLIC_RESULTS_BASE_URL}/events/{request_id}
```

- 저장된 `action_url`이 localhost여도 public URL로 교정한다.
- host는 정확한 allow-list를 사용한다.
- 요청 ID 없는 `/photos` 또는 `/photos-actions` 링크를 완료·오류 event에 보내지 않는다.
- 과거 pending event도 전송 시점에 교정한다.

### 2단계: 읽기 전용 bulk query와 기본 view model

- event/run/group별 추천 asset query를 repository에 추가한다.
- recommendation member와 `photo_results`, local asset, receipt, provider provenance를 bulk join한다.
- 중복 content hash는 slide 하나로 합친다.
- structured count, 추천 이유, AI 장면 설명, 상대 점수와 destination receipt를 안전한 view model로 변환한다.
- 파일 경로·token·내부 오류가 view model에 들어가지 않는 테스트를 먼저 작성한다.

### 3단계: report metadata와 private 위치 snapshot

- materialization 시 원본 촬영 시각·timezone 출처·종횡비·허용된 EXIF·분석 버전을 snapshot한다.
- exact GPS는 일반 recommendation payload와 분리된 권한 제한 원장에 provenance와 함께 저장한다.
- privacy zone, location confidence, conflicting 상태와 remote coarse projection을 계산한다.
- Google Picker GPS 부재를 정상 상태로 유지하고, 추정 위치를 verified GPS 필드에 섞지 않은 별도 provenance로 저장한다.
- 해외 사진의 현지 날짜와 낮은 신뢰도 날짜를 테스트한다.

### 4단계: Evidence Builder와 위치 추정

- 현재 분석 결과와 metadata snapshot을 `PhotoEvidence` schema로 정규화한다.
- GPS anchor, 시간·동선, 공개 OCR, landmark와 주변 사진 단서를 단계별로 평가한다.
- Qwen3.8 Flash Next에는 위치가 불명확한 날짜·scene group의 contact sheet만 추가로 전달한다.
- 외부 검증이 켜져 있으면 비민감 공개 landmark candidate text만 검색하고 사진·exact GPS·raw OCR은 전송하지 않는다.
- verified와 inferred 위치를 별도 필드·provenance·confidence로 저장한다.

### 5단계: Story Director와 StoryManifest

- 사용자 `editorial_brief`와 evidence를 Qwen3.8 Flash Next에 구조화 입력으로 전달한다.
- theme, 제목, chapter, 대표 사진, 순서와 allow-listed storyboard module을 JSON schema로 생성한다.
- asset/claim/location reference, 민감정보, 문장과 module 계약을 post-validation한다.
- 최대 1회 schema repair 뒤 실패하면 deterministic 날짜별 manifest로 fallback한다.
- evidence hash, model/prompt/schema version과 immutable manifest revision을 저장한다.

### 6단계: 안전한 thumbnail·preview service

- local asset ID 조회
- path traversal와 symlink 차단
- grid thumbnail과 viewer preview를 각각 생성하고 orientation 적용, sRGB 변환, EXIF 제거
- content hash 기반 cache
- MIME와 크기 제한
- preview response header 적용

### 7단계: Storyboard SSR, grid와 Swiper modal

- HTML renderer와 정적 CSS/JS를 추가한다.
- Swiper 14.2.0 asset과 LICENSE를 앱에 포함한다.
- StoryManifest의 module을 warm-paper 표지, 전체 contact sheet, 날짜·장소 chapter와 하단 처리 정보로 결정론적으로 렌더링한다.
- 여러 장 grid, 전역 선택 index 연결, 고정 높이 modal 한 장 보기, navigation, fraction pagination, keyboard, A11y, zoom을 설정한다.
- 데스크톱 inspector와 모바일 caption bottom sheet를 구현한다.
- JavaScript 실패 fallback과 empty/error state를 구현한다.

### 8단계: 날짜 타임라인과 로컬 정적 지도

- 날짜 → coarse 장소 cluster → 사진의 결정론적 timeline을 만든다.
- 외부 tile·geocoder 없이 same-origin 정적 SVG 개요를 생성한다.
- marker와 Story chapter 이동, 위치 없음/숨김/충돌 상태, text accessibility fallback을 구현한다.
- interactive map은 별도 필요성이 확인될 때 self-hosted PMTiles/MapLibre 단계로 유예한다.

### 9단계: SharedStoryPackage와 owner 관리

- 선택한 manifest revision에서 share-safe story, 위치와 asset projection을 만든다.
- 사진 제외, 위치 수준, 만료, passcode와 포함 정보를 확인하는 공유 미리보기를 구현한다.
- credential hash, session version, expiry, revoke와 derivative cleanup 원장을 추가한다.
- 내부 owner route에서 링크 생성, 코드 재발급, 즉시 폐기와 상태 확인을 제공한다.

### 10단계: Share Gateway와 Funnel 8443

- 공개 route만 가진 별도 loopback Share Gateway를 구성한다.
- 잠금 화면, rate-limit, Secure session cookie와 package별 image allow-list를 구현한다.
- unlock 전 metadata·사진·OpenGraph preview 차단과 crawler/cache header를 검증한다.
- 기존 443 Serve와 9119 Dashboard를 유지한 채 8443에만 Funnel을 연결한다.
- active share가 없을 때 Funnel 중지 또는 generic 404 정책을 검증한다.

### 11단계: Telegram 통합

- 완료, 0건, 부분 완료, 실패, 사용자 조치 알림 각각에 올바른 URL을 넣는다.
- KST 표기와 summary count를 확인한다.
- Telegram test message는 명시적으로 한 번 승인받은 뒤 보내거나 dry-run preview로 먼저 검증한다.

### 12단계: 설치 앱 반영

- py2app resource에 HTML/CSS/JS/Swiper/license가 포함되는지 확인한다.
- 설치 앱 재빌드·서명 후 source와 bundle asset hash를 비교한다.
- 재시작 후 기존 MCP, health, 자동화 route 회귀 테스트를 수행한다.

## 테스트 계획

### 단위 테스트

- request ID, run ID, group ID 형식 검증
- localhost·base-only URL을 Tailscale event URL로 교정
- 정확하지 않은 `.ts.net` host 거부
- event에서 올바른 collection asset만 선택
- 추천되지 않은 분석 입력 제외
- 동일 content hash의 Apple/Google member를 slide 하나로 통합
- grid card 순서와 선택한 index의 Swiper `initialSlide` 일치
- 0건과 오류 view model
- path traversal, symlink, root 이탈 차단
- EXIF와 GPS 제거
- orientation과 aspect ratio 유지
- preview cache hit/miss와 policy version 변경
- thumbnail과 preview variant cache 분리
- Apple/EXIF/Takeout GPS provenance와 `0,0` placeholder 거부
- Google Picker의 GPS 없음 정상 처리, 추정 위치의 별도 provenance와 confidence 검증
- 앞뒤 GPS anchor의 같은 cluster/시간 범위 추정과 서로 다른 도시일 때 전파 거부
- OCR·landmark candidate의 confidence 하향 및 민감 text 외부 검증 차단
- `StoryManifest` JSON schema, enum theme/module과 모든 asset/claim reference allow-list
- LLM text에 HTML·script·URL·인물 관계·근거 없는 확정 위치가 있을 때 validation 실패
- schema repair 1회 제한과 deterministic fallback manifest
- 같은 evidence hash의 idempotent manifest 재사용과 model/prompt 변경 시 revision 분리
- StoryManifest의 모든 추천 사진 포함 또는 closing contact grid 보완
- exact GPS를 coarse projection으로 변환하고 소수점 2자리보다 상세한 값이 HTML에 없는지 검사
- privacy zone의 `개인 장소` 치환과 conflicting location 격리
- EXIF offset, GPS timezone, provider timezone, 서울 가정의 현지 날짜 우선순위
- 날짜·장소 chapter의 slide ID와 전역 Swiper index 일치
- 정적 지도 data에 local/provider asset ID, 정확 좌표와 경로가 없는지 검사
- SharedStoryPackage가 고정 source revision과 share-safe manifest만 포함하는지 검사
- 내부 asset ID, exact location, model/debug/provider 자료의 share projection 제외
- share ID와 credential entropy, credential 원문 미저장과 hash 검증
- 만료·폐기·session version 변경 때 기존 cookie와 asset 요청 거부
- package별 asset allow-list와 다른 report/share asset 접근 거부
- passcode 실패 rate-limit, 일시 잠금과 성공 후 counter 정리
- 공유 사진 제외 시 manifest module과 asset allow-list를 함께 갱신
- unlock GET 또는 crawler user-agent가 view limit과 one-time credential을 소비하지 않음
- HTML escape와 CSP header
- absolute path, OAuth token, API key가 HTML에 없는지 검사

### HTTP 통합 테스트

| 요청 | 기대 결과 |
|---|---|
| `/ui/events/{valid}` | 200, HTML, 연결된 추천 slide만 포함 |
| `/ui/events/{zero}` | 200, 정상 0건 empty state |
| `/ui/events/{unknown}` | 404, 내부 정보 없음 |
| `/ui/media/{valid}/thumbnail` | 200, 작은 JPEG, EXIF 없음 |
| `/ui/media/{valid}/preview` | 200, JPEG, EXIF 없음 |
| `/ui/media/{unknown}/preview` | 404 |
| `/ui/media/{traversal}/preview` | 400 또는 404 |
| `/ui/reports/{valid}/map.svg` | 200, same-origin SVG, coarse 위치만 포함 |
| `/ui/reports/{unlocated}/map.svg` | 404 또는 위치 없는 안전한 empty state |
| Story generation pending | 200, 결정론적 기본 Story 또는 생성 중 상태; 빈 화면 없음 |
| Story generation failed | 200, fallback Story와 safe 상태; 사진 결과는 유지 |
| Tailnet identity header 없음 | 내부 route 401/403 |
| 허용하지 않은 login | 403 |
| `/s/{active}` unlock 전 | 200, 일반 잠금 화면; 제목·사진·위치 URL 없음 |
| `/s/{active}/unlock` 올바른 code | session 발급 후 share-safe Story 접근 |
| `/s/{active}/unlock` 잘못된 code 반복 | 401/429, 지연·일시 잠금 |
| `/s/{expired-or-revoked}` | generic 404/410, metadata 없음 |
| 다른 package의 image ID | 404, image byte 없음 |
| unlock 없는 image 요청 | 401/404, image byte 없음 |
| static Swiper asset | 200, 고정 hash와 MIME |

### browser 검증

다음 viewport를 최소 기준으로 한다.

- Android Chrome: 360×800, 412×915
- iPhone Safari: 390×844, 430×932
- iPad Safari: 820×1180
- macOS Chrome/Safari: 1440×900

확인 항목:

- 여러 장 grid의 column 변화와 세로 scroll
- grid 사진 선택 시 같은 사진부터 viewer 열기
- viewer 닫기와 grid scroll/focus 위치 복원
- 좌우 swipe와 버튼 이동
- `1 / N` pagination
- pinch/double-tap zoom과 slide swipe 충돌
- 세로 페이지 scroll과 가로 swipe 충돌
- 화면 회전 후 aspect ratio
- 첫 사진 우선 표시와 다음 사진 preload
- 뒤로가기 후 현재 화면 복귀
- reduced motion
- keyboard와 screen reader label
- JavaScript 차단 fallback
- Story/Grid 전환과 날짜 chapter의 의미 있는 DOM 순서
- 접힌 지도와 동일 내용을 제공하는 text timeline
- 지도 marker → 해당 chapter scroll, 닫힌 viewer의 focus 복원
- 위치 없음·숨김·확인 필요가 색상 외 text로 구분되는지 확인
- 자동 선택 theme의 module 순서, 대표 사진과 모든 사진의 Swiper 전역 index 일치
- `GPS 확인`, `동선 문맥으로 추정`, `사진 내용으로 추정` badge와 근거 펼치기
- LLM 생성 문장이 길어져도 mobile overflow와 cumulative layout shift가 없는지 확인

### Tailnet E2E

1. Mac mini에서 local `/ui`를 연다.
2. Tailscale Serve 상태에 `/photos`가 Tailnet only로 표시되는지 확인한다.
3. Tailscale이 켜진 Android/iPhone에서 Telegram 링크를 연다.
4. 추천 사진 여러 장이 grid로 보이고, 한 장을 선택하면 그 사진부터 큰 Swiper viewer가 열리는지 확인한다.
5. viewer에서 다음 사진이 swipe되고 닫은 뒤 원래 grid 위치로 돌아오는지 확인한다.
6. 위치가 있는 사진은 coarse 장소와 정적 개요로만 보이고 raw 좌표가 HTML·network 응답에 없는지 확인한다.
7. Tailscale을 끄고 같은 URL이 접근되지 않는지 확인한다.
8. 허용하지 않은 Tailnet identity가 차단되는지 확인한다.
9. 443과 9119가 계속 Serve/tailnet only이고 public Funnel로 바뀌지 않았는지 확인한다.
10. Open WebUI root와 Hermes Dashboard가 여전히 정상인지 확인한다.

### 공개 공유 E2E

1. owner Tailnet 화면에서 공유 미리보기를 열고 공개 사진, 날짜·위치 수준과 최종 문장을 확인한다.
2. 기본 30일 만료와 별도 passcode로 package를 생성한다. 필요하면 24시간 또는 7일로 더 짧게 만든다.
3. `tailscale funnel status`에서 `8443`만 public Share Gateway로 연결됐는지 확인한다.
4. Tailscale이 설치되지 않은 Android/iPhone mobile network에서 링크를 연다.
5. unlock 전 Story title, cover, image URL과 위치가 HTML·network에 없는지 확인한다.
6. 잘못된 code의 오류·rate-limit과 올바른 code의 Secure session을 확인한다.
7. Story, contact grid, 선택형 Swiper와 share-safe 위치가 정상인지 확인한다.
8. 원본 download, 내부 report listing, `/health`, MCP와 owner 관리 route가 public gateway에서 모두 차단되는지 확인한다. 허용된 공유 파생본만 session과 allowlist를 통과해 다운로드되는지 함께 확인한다.
9. Telegram/KakaoTalk/Messages link preview bot이 사진을 가져가거나 view를 소비하지 않는지 확인한다.
10. 공유를 즉시 폐기하고 열린 browser session과 image URL이 더 이상 동작하지 않는지 확인한다.
11. 만료 cleanup 뒤 share derivative가 제거되고 내부 Story와 추천 사진은 유지되는지 확인한다.

### 기존 기능 회귀

- PhotosMcp 전체 pytest
- Hermes Telegram bridge 테스트
- MCP handshake와 공개 도구 목록
- `/health`와 `/health/capabilities`
- 일일 Apple/Google 자동화
- recommendation reconcile
- mutation approval과 destination receipt
- py2app build, strict codesign, installed health

## 관찰 지표와 로그 최소화

운영 log에는 다음 비식별 수치만 남긴다.

- page type과 HTTP status
- slide count
- preview cache hit/miss
- preview 생성 시간과 byte size
- safe error code
- Tailscale identity allow/deny 결과의 hash 또는 category
- share 생성·만료·폐기 상태와 전체 view count
- passcode 검증 성공/실패 category와 rate-limit 상태

다음은 기록하지 않는다.

- preview byte
- 사진 파일명과 절대 경로
- request URL 전체
- Tailscale login 원문
- 사진 caption 원문
- OAuth/API credential
- 정확한 GPS, raw reverse-geocoder 응답, home/work label
- share credential, session cookie, 전체 share URL과 원시 IP/user-agent

별도의 analytics SDK나 외부 error tracking은 사용하지 않는다.

## 위험과 대응

| 위험 | 대응 |
|---|---|
| Tailnet의 다른 사용자가 개인 사진을 봄 | identity header exact allow-list와 Tailnet ACL |
| URL만 알면 asset을 순회 | asset ID 검증, identity 필수, listing API 비공개 |
| path traversal로 다른 파일 노출 | DB lookup 후 root containment와 symlink 차단 |
| GPS·camera 정보 노출 | web preview 재인코딩, 별도 private 위치 원장, coarse projection과 privacy zone |
| 해외 사진이 한국 날짜로 잘못 묶임 | 원본 offset과 GPS/provider timezone 보존, confidence 표시 |
| Google Picker 추정 위치가 GPS 사실처럼 보임 | GPS `unavailable`은 유지하고 `사진 내용/동선으로 추정` badge와 confidence를 별도 표시 |
| 외부 지도에 좌표가 전송됨 | 1차 same-origin 정적 SVG, 외부 tile·geocoder·CDN 금지 |
| LLM이 장소·인물·감정을 창작 | `PhotoEvidence`와 claim reference, confidence 문구, 금지 표현 post-validation |
| LLM 생성 HTML이 CSP·접근성을 파괴 | raw HTML 금지, enum Story module과 deterministic renderer |
| Story가 매번 달라짐 | immutable StoryManifest, evidence hash와 model/prompt/schema revision 저장 |
| Linux 준비·Story 생성 실패가 사진 작업을 실패시킴 | 추천·저장 결과와 Story 상태 분리, deterministic 날짜별 fallback |
| 외부 위치 검증이 개인정보를 전송 | 비민감 공개 landmark candidate text만 opt-in 검색, 사진·좌표·raw OCR 전송 금지 |
| Funnel이 기존 Open WebUI·Dashboard를 공개 | 기존 443/9119 유지, 별도 8443 loopback Share Gateway만 Funnel 연결 |
| Funnel에는 Tailscale identity가 없음 | passcode와 Secure session, package별 asset allow-list를 애플리케이션에서 검증 |
| 공유 URL이 메신저 preview bot에 노출 | unlock 전 generic page, GET으로 credential/view 소비 금지, OG image 없음 |
| 링크 전달로 제3자가 접근 | 기본 별도 passcode, 짧은 만료, rate-limit과 즉시 폐기 |
| 폐기 뒤 browser cache에 사진이 남음 | share HTML/image `no-store`, session version 폐기와 derivative cleanup |
| 공유 내용이 내부 Story 변경으로 확대 | immutable SharedStoryPackage와 고정 source revision |
| 수신자가 screenshot으로 복제 | 기술적으로 완전 차단 불가를 명시하고 preview 해상도·선택 watermark 제공 |
| CDN이 접속 정보를 수집 | Swiper/CSS/JS 전부 self-host |
| grid가 많은 원본을 동시에 내려받음 | 별도 480px thumbnail, 모바일 24/태블릿 36/데스크톱 48장 batch, 큰 preview는 선택 후 요청 |
| 많은 사진으로 모바일 메모리 증가 | thumbnail lazy load, viewer 인접 1장 preload, 200장 cap |
| RAW/HEIC browser 비호환 | JPEG preview contract |
| 확대 시 원본이 노출 | 2048px preview만 제공, 원본 route 없음 |
| Swiper JS 실패 | server-rendered HTML과 CSS scroll-snap fallback |
| 기존 Telegram 링크가 계속 실패 | delivery 시점 canonical URL 재생성 |
| 설치 앱에서 정적 asset 누락 | bundle resource와 hash smoke test |
| Tailscale key 만료 | 운영 health에 node key expiry 경고 추가 검토 |

## 완료 조건

다음 조건이 모두 충족돼야 구현을 완료로 판단한다.

- Telegram의 완료·0건·오류·사용자 조치 링크가 모두 Tailscale HTTPS 개별 URL이다.
- 링크를 Android/iPhone에서 열었을 때 해당 결과의 추천 사진 여러 장이 grid로 표시된다.
- 표지, 전체 contact sheet, 날짜·장소별 Story chapter, 하단 처리 정보의 위계로 표시된다.
- LLM이 근거 묶음에서 theme, 제목, chapter, 대표 사진, 순서와 allow-listed module을 선택한 유효한 `StoryManifest`가 저장된다.
- 모든 Story 문장과 위치 표시는 내부 claim/evidence reference로 추적 가능하며 validation을 통과한다.
- LLM은 HTML·CSS·JavaScript·media URL을 생성하지 않고 고정 renderer가 manifest를 HTML로 변환한다.
- grid에서 선택한 사진이 Swiper viewer의 첫 slide로 크게 열리고, 이후 한 장씩 넘길 수 있다.
- 좌우 swipe, 버튼, keyboard, pagination, zoom, 접근성 label이 동작한다.
- grid는 작은 thumbnail만 받고 큰 preview는 사진을 선택한 뒤 lazy load되며, 원본 파일은 HTTP로 직접 제공되지 않는다.
- preview에서 EXIF와 GPS가 제거된다.
- 사진별 추천 이유, AI 장면 설명, 촬영·저장 정보는 현재 저장된 근거 안에서만 표시되고 내부 원시 코드와 존재하지 않는 구도 세부 점수를 만들지 않는다.
- 위치가 있는 추천 자산은 provenance와 현지 시간대가 private snapshot으로 보존된다. 기본 `balanced` HTML은 coarse 장소를, 승인된 `personal_detailed` mode는 좌표 숫자 없이 검증된 장소 label을 표시한다.
- 위치 확인·문맥 추정·시각 추정·숨김·충돌·알 수 없음 상태가 명확하며 Google Picker 추정 위치를 GPS 사실로 저장하거나 표시하지 않는다.
- GPS가 없는 사진은 시간상 인접 GPS anchor, 공개 OCR, landmark와 주변 사진 단서로 단계적 추정을 수행하되 낮은 confidence에서는 `위치 정보 없음`으로 남는다.
- 정적 지도는 same-origin으로 제공되고 외부 tile·geocoder 요청과 exact GPS network payload가 없다.
- Apple/Google 중복 사진은 한 slide로 합쳐진다.
- 0건·부분 완료·오류 상태가 사진이 있는 성공 화면과 명확히 구분된다.
- 소유자용 `/photos`는 Tailnet에 연결하지 않은 기기에서 접근할 수 없고 허용된 Tailscale identity만 볼 수 있다.
- 외부 `/s/{share_id}`는 active SharedStoryPackage와 올바른 passcode/session 없이는 Story, title, 위치와 image를 반환하지 않는다.
- 외부 공유는 share-safe 사진·문장·위치 projection만 포함하고 내부 report·원본·분석·provider·다른 asset에 접근할 수 없다.
- 기본 30일 만료, 즉시 폐기, code 재발급과 열린 session 무효화가 동작한다.
- 443 Serve와 9119 Dashboard는 tailnet only로 유지되고 8443 Share Gateway만 Funnel에 연결된다.
- 메신저 preview bot과 검색 crawler가 사진을 가져가거나 one-time/view 상태를 소비하지 않는다.
- Open WebUI root, Hermes Dashboard, 기존 Photos action URL이 회귀하지 않는다.
- 전체 자동 테스트, 설치 앱 build, code signing과 실기기 E2E가 통과한다.
- 테스트용 Telegram 알림과 임시 preview가 운영 원장에 남지 않는다.

## 원래 승인 범위

전체 설계에서 승인된 범위는 다음과 같다. 위 구현 결과에 명시한 공유 Vertical Slice는 완료했고, 생성형 Story 고도화 항목은 후속 단계로 남긴다.

1. 추천 결과 전용 읽기 Story Album HTML, 여러 장 contact grid와 선택형 Swiper viewer
2. 검증된 추천 자산의 metadata 제거 grid thumbnail과 큰 web preview
3. 추천 분석 snapshot, 촬영 시간대, 종횡비와 privacy-projected 위치 metadata
4. `PhotoEvidence` 생성과 GPS anchor·동선·OCR·landmark 기반 위치 추정
5. Qwen3.8 Flash Next Story Director, schema validation, immutable StoryManifest와 fallback
6. 날짜·장소별 text timeline과 same-origin 정적 SVG 위치 개요
7. event/run/group별 읽기 route
8. Tailscale identity allow-list와 `/photos` Serve 연결
9. share-safe projection, SharedStoryPackage, 공개 사진 미리보기와 만료·폐기 관리
10. passcode/session/rate-limit을 가진 별도 loopback Share Gateway와 8443 Funnel
11. Telegram canonical 내부 결과 링크와 명시적으로 만든 외부 공유 링크
12. 단위·HTTP·Tailnet·public mobile 통합 테스트
13. 설치 앱 반영과 문서 업데이트

다음 기능은 이번 승인 범위에서 제외한다.

- 웹에서 사진 삭제
- 추천 취소 또는 재분류
- Apple/Google 앨범 쓰기 버튼
- 원본 다운로드
- 정확한 GPS·전체 주소 표시와 외부 지도 embed
- self-hosted PMTiles/MapLibre interactive map
- LLM이 생성한 raw HTML·CSS·JavaScript 실행
- 사진·exact GPS·민감 OCR의 외부 위치 검색 전송
- 얼굴 이름 표시
- 기존 443 Open WebUI·Photos와 9119 Dashboard의 Funnel 공개
- passcode·만료 없는 완전 공개 Story link
- base64 단일 HTML과 ZIP의 기본 자동 생성
- 자동 slideshow

## 전체 설계 권장 구현 순서

```text
identity allow-list와 링크 정상화
  -> bulk query와 기본 view model
  -> report metadata와 private 위치 snapshot
  -> PhotoEvidence와 위치 추정 ladder
  -> Qwen3.8 Story Director와 StoryManifest validator
  -> preview 보안 service
  -> deterministic Storyboard SSR와 Swiper self-host 적용
  -> 날짜 타임라인과 로컬 정적 지도
  -> Tailscale /photos 연결 검증
  -> share-safe projection과 SharedStoryPackage
  -> Share Gateway 인증과 Funnel 8443
  -> Tailscale 없는 Android/iPhone 공개 공유 E2E
  -> Telegram dry-run
  -> 승인된 실메시지 1건
  -> Android/iPhone E2E
  -> 전체 회귀·배포
```

이 순서를 따르면 링크 문제를 먼저 해결하고, 사진 제공 경계를 검증한 뒤 Swiper UI와 외부 Tailnet 경로를 연결할 수 있다.
