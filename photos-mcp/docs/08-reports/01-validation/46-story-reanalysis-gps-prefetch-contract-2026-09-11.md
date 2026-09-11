# Story 재분석 GPS 선동기화 계약 교정·검증

검증일: 2026-09-11

관련 선행 검증: [Android 수동 날짜 GPS 선동기화 구현·검증](45-android-manual-date-gps-prefetch-2026-09-11.md)

## 1. 운영 증상과 결론

2026년 8월 11~20일 범위의 완료 Story는 추천 39장 전체가 `unknown`이었다. Story scope의 Apple Photos 6장과 Google Photos 33장 모두 추천 위치 레코드가 없었고, 추천 사본 자체에도 EXIF GPS가 없었다. Mac mini의 Android 위치 원장에는 과거 증분 동기화로 받은 273건만 있었으며 촬영 범위는 8월 31일 이후였다. 새 범위 스캐너의 `android-bridge-2` manifest와 8월 11~20일 manifest는 각각 0건이었다.

원인은 Story 목록의 `같은 기간 전체 재분석` 경로였다. 새 `날짜로 Story 만들기`에는 GPS 선동기화를 연결했지만 기존 Story 재분석 버튼은 선택 범위를 Android MediaStore에서 다시 읽지 않고 곧바로 서버 재분석 명령을 보냈다. 15:57 KST에 다시 시작한 동일 범위 작업도 이 우회 경로를 사용했으며, Google Picker worker가 실행 중이지만 선행 GPS 수신은 없었다.

## 2. 수정 내용

Android 0.7.2부터 두 수동 경로가 동일한 계약을 사용한다.

```text
새 날짜 Story / 기존 Story 전체 재분석
  → Story의 원래 date_from·date_to 복원
  → 전체 사진·원본 위치 권한 확인
  → 선택 기간의 Android 카메라 원본 최대 1,000장 재조회
  → GPS 원본 manifest 암호화 전송
  → outbox 잔여 0 확인
  → 범위·조회 수·GPS 수·완료 시각을 담은 서명 영수증 생성
  → 서버 검증
  → 검증 성공 시에만 분석 queue 등록
```

앱 변경:

- Story 목록의 재분석 버튼에 저장된 시작일·종료일을 명시적으로 전달한다.
- 재분석 확인창에 GPS 선동기화 단계를 안내한다.
- 권한이 부족하면 분석 명령 전에 차단한다.
- `BridgeSync.runRange` 결과에 GPS 보유 수뿐 아니라 실제 조회 수를 포함한다.
- 서명 본문에 `location_prefetch` 영수증을 포함한다.
- 앱 버전을 0.7.2(`versionCode=16`)로 올렸다.

서버 변경:

- Google Photos가 포함된 새 수동 Story와 기존 Story 재분석은 GPS 영수증을 필수로 요구한다.
- 영수증의 날짜 범위가 분석 범위와 다르면 HTTP 428로 거부한다.
- GPS 수가 조회 수보다 크거나, 완료 시각이 오래됐거나, 전송 대기 배치가 남은 영수증은 거부한다.
- 앱이 GPS manifest를 보냈다고 보고하면 동일 기기·동일 날짜·동일 extractor의 Mac 원장 집계가 실제 수신 수 이상인지 확인한다.
- GPS 0건은 `조회 완료 0건` 영수증이 있을 때만 허용해 `조회하지 않음`과 구분한다.
- Apple Photos만 선택한 작업은 Android GPS에 의존하지 않으므로 기존 호환성을 유지한다.
- 재전송된 동일 idempotency 요청은 영수증 유효 시간이 지나도 기존 operation을 반환한다.

서버 집계 검증은 좌표를 복호화하거나 API에 노출하지 않고 기기·촬영 범위·extractor별 manifest 수만 센다.

## 3. 현재 실행 중 작업의 의미

교정 전에 이미 시작한 8월 11~20일 재분석은 GPS 선동기화를 거치지 않았다. 실행 중 프로세스를 임의 종료하지 않았으며, 이 작업이 완료돼도 Android 위치가 새로 생기지는 않는다. 0.7.2 설치 후 같은 Story에서 한 번 더 `같은 기간 전체 재분석`을 실행해야 새 계약이 적용된다.

재실행 후 Mac 위치 원장에 `android-bridge-2`가 생겨야 한다. GPS가 있는 원본이 존재한다면 해당 날짜 범위 manifest 수가 1건 이상이어야 하며, 추천 파일 생성 후 `android_original_digest` 또는 `android_original_time_dimensions` 매칭을 거쳐 Story가 다시 만들어진다. 원본에 실제 GPS가 없거나 안전한 일대일 매칭이 되지 않는 사진만 `위치 미상`으로 남는다.

## 4. 검증 결과

| 검증 | 결과 |
|---|---|
| 원인 운영 집계 | 완료 Story 39/39 unknown, 신규 범위 manifest 0 확인 |
| GPS·모바일·Story 집중 회귀 | 39개 통과 |
| Python 전체 회귀 | 1,002개 통과, 14.54초 |
| Android debug compile·assemble·lint | 성공 |
| Android release R8·assemble·lint | 성공 |
| APK 서명 | APK Signature Scheme v2·v3 통과 |
| Tailnet 다운로드 | 0.7.2, HTTP 200 |

배포 산출물:

- APK: `~/.photos-mcp/runtime/mobile-client/downloads/PhotosMcp-Album.apk`
- Tailnet: `https://byoungyoung-macmini.tail53bcc7.ts.net/mobile-client/download`
- 크기: `117,009 bytes`
- SHA-256: `85501ba0260563fd4f5898b3fa3f242581b0611e3c726b1d9f56b330e67e28b9`

ADB 연결 기기는 검증 시점에 없었으므로 8월 11~20일의 실기기 조회 수와 GPS 수는 APK 설치 후 재실행 시 확인한다. 개인 좌표·사진명은 이 보고서에 기록하지 않는다.
