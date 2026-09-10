# 2026-09-07 Android 원본↔Google Picker 매칭 기반 검증

## 결론

Android 원본에서 GPS sidecar만 수집한 뒤 Google Photos Picker 사본에 연결하기 위한 privacy-safe 진단 기반을 구현하고, 연결된 Android 16 단말을 ADB로 일회성 실측했다. 최근 원본 30장은 모두 GPS를 보존했고, 실제 Android↔Picker 50쌍에서 A 23·B 25·C 2·D 0, 제시된 오답 후보 0을 확인했다. A+B 후보 coverage는 96%지만 자동 허용 등급인 A는 46%이므로 엄격한 자동 gate는 아직 실패다.

ADB는 이 실측을 위한 개발 도구일 뿐 운영 기본 경로가 아니다. 운영은 Android 앱의 암호화 offline outbox와 Tailnet 재전송을 사용하며, 휴대폰이 연결되지 않아도 새벽 PhotosMcp 작업은 중단하지 않는다.

따라서 현재 상태는 다음처럼 구분한다.

| 항목 | 상태 |
| --- | --- |
| 다중 지문 추출 | 완료 |
| A/B/C/D 보수적 판정 | 완료 |
| 비식별 결과와 정답 기반 품질 gate | 완료 |
| synthetic EXIF 제거·재인코딩·중복 검증 | 통과 |
| synthetic 20쌍 CLI E2E | A 20·오연결 0·통과 |
| synthetic 1,000쌍 matcher | 전체 비교표 없이 A 1,000 완료 |
| 실제 Google Picker 사본 읽기 | 통과 |
| Android 원본 30장 EXIF/GPS 직접 검사 | GPS 30·촬영 시각/offset 30·오류 0 |
| Android↔Picker 실제 50쌍 후보 정확도 | A 23·B 25·C 2·D 0·오답 후보 0 |
| 엄격한 A-only 자동 gate | 실패, 46% |
| A+B correct candidate coverage | 96%, B는 계속 shadow |
| 다음 강한 지문 검증 | Picker `=d` transient 원본 bytes 필요 |
| 운영 Android 경로 | 앱 offline outbox, ADB 사용 안 함 |

## 구현 산출물

- `src/photos_mcp/application/mobile_location_matching.py`
  - 원본 파일 SHA-256
  - JPEG APP/COM metadata segment를 제외한 codestream digest
  - EXIF 방향을 적용한 최대 2048px normalized RGB digest
  - 256-bit perceptual hash
  - 촬영 시각과 offset, 크기, camera make/model, 원본 GPS 존재 여부
  - Google Picker `.photos-mcp.json` sidecar의 원래 파일명과 provider metadata 결합
  - 디스크 저장 없이 ADB 진단·향후 ingest에 사용할 in-memory bytes fingerprint
  - A/B/C/D 매칭과 aggregate-only 결과 생성
- `scripts/analyze_android_picker_matching.py`
  - Android 원본 디렉터리와 Picker 사본 디렉터리를 읽기 전용으로 검사
  - 명시적 ground-truth JSON을 사용한 false-positive 측정
  - 사진 경로·파일명·좌표·해시 원문을 제외한 JSON 보고서 저장
- `tests/test_mobile_location_matching.py`
  - EXIF GPS만 제거한 JPEG의 strong identity 유지
  - 동일 콘텐츠 중복본의 자동 연결 금지
  - 재인코딩 후보의 `shadow_only` 고정
  - 20쌍·95%·오연결 0건·전체 정답 coverage gate
  - 정답 미제공 상태의 승격 금지
  - 단 한 건의 오정답도 gate 실패 처리
  - EXIF 회전 전후의 swapped dimensions를 호환 후보로 처리하되 B등급 유지

## 등급 정책

| 등급 | 의미 | 자동 GPS 연결 |
| --- | --- | --- |
| A | 파일·JPEG codestream·normalized pixel 중 strong identity가 유일하게 일치 | 후보 자격 있음 |
| B | pHash와 촬영 시각·크기·파일명·camera의 복수 증거가 유일하게 일치 | 금지, shadow only |
| C | strong duplicate, 연사, 편집본 또는 복수 후보 | 금지, owner review |
| D | 읽기 실패, 충돌 또는 안전한 후보 없음 | 금지 |

A등급이라도 최종 실표본 gate를 통과하기 전에는 운영 위치 테이블로 투영하지 않는다. pHash, 파일명, 촬영 시각 중 하나만으로는 어떤 등급에서도 자동 연결하지 않는다. 세로 사진의 `width×height`가 Google 변환 뒤 뒤집히는 경우는 호환 증거로 인정하지만 strong identity로 승격하지 않는다.

## 개인정보 경계

진단기 내부에서는 매칭을 위해 경로, 파일명, 정확한 GPS 존재 여부와 해시를 사용하지만 결과 JSON에는 다음 값을 쓰지 않는다.

- 절대·상대 사진 경로
- 실제 파일명
- 위도·경도·고도
- 원본 파일 SHA-256 문자열
- JPEG·pixel digest 문자열
- camera serial 또는 단말 MediaStore ID

결과에는 경로에서 만든 비가역 safe ID, 증거별 일치 여부, normalized pHash 거리, 시각 차이, 등급과 집계만 남긴다. 로그와 문서에도 개인 사진 식별자를 기록하지 않았다.

## 실제 Picker 파일 읽기 검증

운영 Google Picker 캐시에서 최근 사본 30장을 읽기 전용으로 검사했다. 파일명·자산 ID·촬영 내용은 출력하지 않았다.

| 항목 | 결과 |
| --- | ---: |
| 표본 | 30장 |
| 정상 decode | 30장 |
| Picker sidecar 발견 | 30장 |
| 촬영 시각 확보 | 30장 |
| camera model 확보 | 30장 |
| JPEG codestream digest 생성 | 30장 |
| normalized pixel digest 생성 | 30장 |
| pHash 생성 | 30장 |
| embedded GPS | 0장 |
| decode 오류 | 0건 |
| 동일 파일 hash 중복 group | 0개 |
| 동일 codestream 중복 group | 0개 |

Picker 사본의 embedded GPS가 0장인 것은 기존 구현의 `unavailable_from_google_picker` 계약과 일치한다. 이 결과만으로 Android 원본과의 실제 연결률을 추정하지는 않는다.

## Android 단말 일회성 ADB 직접 검사

Android Platform Tools 37.0.1을 설치하고 사용자가 단말에서 이 Mac의 RSA USB debugging key를 승인했다. 연결 단말은 Android 16/API 36이며, ADB는 이 검증에서만 사용한다.

MediaStore와 `DCIM/Camera`를 변경하지 않는 query·content stream만 수행했다.

| 항목 | 결과 |
| --- | ---: |
| 전체 `DCIM/Camera` 파일 | 3,282개 |
| 최근 10일 Camera JPEG | 269장 |
| 직접 검사한 최근 원본 | 30장 |
| 정상 decode | 30장 |
| GPS 위도·경도 포함 | 30장 |
| GPS 고도 포함 | 26장 |
| 촬영 시각 포함 | 30장 |
| 촬영 timezone offset 포함 | 30장 |
| camera make/model 포함 | 30장 |
| 오류 | 0건 |
| Mac에 저장한 Android 원본 | 0장 |

추가로 최근 5장을 MediaStore Content URI와 filesystem 원본 stream으로 각각 읽어 byte hash를 비교했다. 5장 모두 byte-identical했고 양쪽 stream에서 GPS가 유지됐다. 정확 좌표·파일명·MediaStore ID는 출력하거나 문서에 기록하지 않았다.

이 결과로 현재 단말 원본에는 필요한 GPS가 실제 존재하고, Android 앱이 `ACCESS_MEDIA_LOCATION`과 원본 media 접근을 올바르게 사용하면 sidecar를 만들 수 있다는 기반을 확인했다. USB 연결이 운영 전제라는 의미는 아니다.

## 실제 Android↔Picker 50쌍 매칭

최근 10일 Android MediaStore 이름과 현재 보존된 Picker sidecar의 원래 이름을 메모리에서만 대조했다. 겹치는 이름은 78개, 양쪽에서 이름이 유일한 후보는 72개였다. 이 이름은 50개 정답 후보를 구성하는 데만 사용했고, 매칭 판정 자체는 strong/visual digest와 촬영 metadata로 수행했다.

50쌍 모두 촬영 시각이 5초 이내였고 Android 원본 GPS가 있었다. 파일을 저장하지 않고 ADB Content URI bytes를 메모리로 읽어 다음 결과를 얻었다.

| 결과 | 1차 | 회전 해상도 호환 후 |
| --- | ---: | ---: |
| A strong unique | 23 | 23 |
| B shadow unique | 1 | 25 |
| C ambiguous | 0 | 2 |
| D unmatched | 26 | 0 |
| 제시된 오답 후보 | 0 | 0 |
| A-only 자동률 | 46% | 46% |
| A+B 올바른 후보 coverage | 48% | 96% |

1차 D 26건은 실제로 다른 사진이어서가 아니라 portrait orientation이 Android와 Google에서 `width×height`/`height×width`로 다르게 표현되는 경우가 후보 index에서 제외된 영향이 컸다. swapped dimensions를 compatible evidence로 인정하되 strong identity로 올리지 않자 25건이 정확한 B, 2건이 안전한 C로 이동했다.

이는 B 정책의 유효성을 보여주지만 자동 승격 근거로는 아직 부족하다. 현재 Google 분석 cache는 Picker 원본 식별용 보존물이 아니라 VLM 분석용 변환 사본일 수 있다. 다음 검증에서는 Picker `=d` 원본 크기 bytes를 일시적으로 받아 JPEG strong digest를 계산하고 즉시 폐기한다. 이 방법은 휴대폰 연결 없이 Google 입력 처리 시점에 수행할 수 있다.

## 자동 검증

실행 명령:

```bash
.venv/bin/python -m pytest -q \
  tests/test_mobile_location_matching.py \
  tests/test_google_photos_import_service.py \
  tests/test_google_photos_rest_adapters.py \
  tests/test_location_privacy.py \
  tests/test_recommendation_storage.py
```

결과:

```text
53 passed
```

20쌍 임시 synthetic 디렉터리와 명시적 정답 JSON으로 CLI 전체 경로도 실행했다.

```text
exit_code=0
quality_gate=passed
grade A=20, B=0, C=0, D=0
false_positive_count=0
```

1,000쌍의 서로 다른 strong identity를 메모리 내 matcher에 전달해 A 1,000건을 확인했다. matcher는 1,000×1,000 evidence matrix를 만들지 않고 strong digest index와 동일 해상도 shadow 후보군을 사용한다. 이 synthetic 결과는 확장성·계약 검증이며 실제 사진 품질 판정을 대신하지 않는다.

전체 프로젝트 회귀 결과:

```text
804 passed in 10.28s
```

추가로 두 신규 Python 파일의 compile, 문서 검사와 `git diff --check`를 통과했다.

## 운영 연결 정책 수정

ADB로 직접 읽는 방식은 표본 검사에는 유용하지만 휴대폰을 늘 USB에 연결할 수 없으므로 운영 기본안으로 사용하지 않는다. 복사용 임시 디렉터리도 생성 직후 비어 있음을 확인하고 제거했다.

운영 흐름은 다음과 같다.

```text
Android Location Bridge
  → MediaStore 신규/변경 자산 발견
  → 단말에서 GPS + strong/visual fingerprint 추출
  → Keystore-backed encrypted outbox에 저장
  → 휴대폰 또는 Mac이 오프라인이면 그대로 보존
  → Tailnet endpoint가 열리면 batch upload
  → 서버 ack 뒤 acknowledged

Nightly PhotosMcp
  → 휴대폰 연결을 기다리지 않음
  → 현재 도착한 Google/Apple 사진으로 추천·스토리·HTML 완료
  → sidecar 미도착 자산은 awaiting_mobile_metadata
  → 나중에 sidecar가 오면 private GPS 연결
  → 동일 collection revision의 위치 section만 재생성
```

따라서 “Android sidecar 미도착”은 새벽 작업 오류가 아니다. Google Photos의 동기화와 앱 outbox 전송은 독립적으로 진행되고, 어느 쪽이 먼저 와도 reconciliation이 최종 연결한다.

## 승격 기준과 다음 단계

다음 조건을 모두 만족해야 exact GPS 자동 projection을 운영 활성화한다.

- 정답쌍 20개 이상과 Android 정상 decode 전체를 ground truth가 포괄한다.
- A등급 자동 연결률이 95% 이상이다.
- A등급 false positive가 0건이다.
- 중복·연사·편집 복수 후보가 C로 남는다.
- GPS가 없는 Google 사본에 올바른 Android 원본의 GPS 존재 상태를 연결할 수 있다.
- 결과 파일에 경로·파일명·좌표·원본 해시가 없다.

현재 A-only gate는 46%여서 실패다. 다음으로 같은 사진을 새 Picker session에서 `=d` transient bytes로 내려받아 strong digest가 95% 이상으로 올라가는지 검증한다. 원본은 fingerprint 계산과 VLM 파생본 생성 직후 폐기한다.

Android Location Bridge와 append-only endpoint는 offline outbox·idempotent ack 경계를 전제로 설계한다. 앱의 첫 MVP는 `지금 동기화`, queue 상태, 네트워크 단절 후 재전송을 포함한다. 새벽 작업은 이 앱의 연결 여부에 종속시키지 않는다. B등급 자동 승격과 선택적 원본 요청은 계속 유예한다.
