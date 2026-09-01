# Google Photos 원본 및 메타데이터 보존

## 목적

Google Photos Picker에서 선택한 사진을 분석할 때, 변환본에서 촬영 EXIF가 사라지는 문제를 방지한다. 원본 파일은 변경하지 않고, Picker API가 제공하는 보조 메타데이터는 별도 JSON으로 보존한다.

## 확인 결과

동일한 실제 Picker 사진을 두 방식으로 내려받아 확인했다.

| 다운로드 URL | 관측 결과 |
| --- | --- |
| `=w4096-h4096` | 픽셀 크기는 유지될 수 있으나 EXIF가 DateTimeOriginal·Orientation·Software 등 일부로 축소됐다. |
| `=d` | Make, Model, DateTimeOriginal, ISO, 조리개, 셔터 속도, 초점 거리 등 원본 EXIF가 유지됐다. |

따라서 분류 입력은 기본적으로 `=d`를 사용한다. 임시 캐시는 단일 항목 최대 256 MiB로 제한하며, 초과 항목은 명확한 오류로 처리한다.

## 구현 범위

- Picker 선택 사진은 `=d` 원본 경로로 다운로드한다.
- Picker `mediaFileMetadata`의 촬영 시각, 해상도, 제조사, 모델, 초점 거리, 조리개, ISO, 노출 시간을 사진 옆 `파일명.photos-mcp.json`에 저장한다.
- JSON에는 만료되는 base URL, OAuth access token, refresh token을 절대 저장하지 않는다.
- 작업 제출 전에는 JSON의 촬영 시각을 분석 입력의 보조 capture time으로 사용한다. 원본 EXIF는 파일과 함께 보존되어 검사와 내보내기에 계속 사용된다.
- JSON에 출처가 검증된 위치 정보가 있는 경우 JPEG/WebP 임시 복사본의 GPS EXIF에도 기록한다. Google 원본과 사용자의 로컬 원본은 변경하지 않는다.
- 작업 기록 삭제와 임시 파일 정리는 이미지와 JSON 보조 파일을 함께 지운다.

## 위치 정보 정책

Google Photos Picker API의 `mediaFileMetadata`에는 위치 필드가 없고, Google의 `=d` 다운로드도 위치 메타데이터를 제외한다. 따라서 Picker 기반 JSON에는 다음처럼 **알 수 없음**을 명시한다.

```json
{
  "location": {
    "status": "unavailable_from_google_picker",
    "source": "none"
  }
}
```

위치가 `0, 0`이라는 뜻이 아니며, 앱은 이 값을 GPS로 사용하지 않는다.

향후 Google Takeout 가져오기에서는 선택적으로 다음 두 출처를 별도로 보관한다.

- `geoDataExif`: 카메라 EXIF에서 온 원래 GPS 좌표
- `geoData`: Google Photos가 보관하거나 사용자가 보정한 위치일 수 있는 좌표

Takeout JSON의 위치 필드는 모든 사진에 존재하지 않을 수 있고 Google이 공개한 안정 API 계약도 아니다. 따라서 원본 EXIF를 자동 변경하지 않으며, 화면과 내보내기에서 위치의 출처를 구분해 표시한다.

`takeout_geo_data_exif`, `takeout_geo_data`, `user_confirmed` 출처의 정상 범위 좌표만 임시 JPEG/WebP 복사본에 GPS EXIF로 쓴다. Takeout 원형 JSON의 `geoDataExif`를 먼저 읽고 없을 때만 `geoData`를 사용하며, `0, 0` 자리표시는 위치 없음으로 처리한다. HEIC 등 지원하지 않는 형식은 JSON에 `embedding_status: unsupported_format`을 남겨 위치를 보존한다.

## 검증

```bash
uv run pytest -q tests/test_google_photos_import_service.py \
  tests/test_google_photos_rest_adapters.py \
  tests/test_cloud_source_adapters.py
```

검증 항목은 `=d` 기본 전달, Picker 촬영 메타데이터 추출, JSON 보조 파일 생성, 분석 입력의 촬영 시각 사용, 작업 삭제 시 보조 파일 삭제를 포함한다.

## 완료 판정 — 2026-09-01

- Picker adapter는 사진에 `=d`, 동영상에 `=dv`를 사용하고 `mediaFileMetadata`를 정규화한다.
- import service는 민감한 URL·token을 제외한 `.photos-mcp.json` 보조 파일을 만들며, Picker가 위치를 제공하지 않는 경우 `unavailable_from_google_picker`를 명시한다.
- Takeout `geoDataExif`·`geoData` 출처 구분, `0, 0` 제외, 지원 형식의 임시 복사본 GPS 기록과 관리 cache 삭제가 구현되어 있다.
- 관련 Google Photos·source adapter 테스트를 포함한 최신 전체 회귀 654건을 통과했다. Picker가 제공하지 않는 위치를 추정하거나 원본 파일을 변경하는 기능은 범위에 포함하지 않는다.
