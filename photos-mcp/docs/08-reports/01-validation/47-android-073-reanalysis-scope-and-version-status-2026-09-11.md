# Android 0.7.3 재분석 범위·버전 표시 교정 보고서

- 일자: 2026-09-11 KST
- 배포 버전: `0.7.3` (`versionCode=17`)
- 대상: PhotosMcp Android 앱의 `같은 기간 전체 재분석`, 설정 화면, 모바일 API

## 결론

0.7.2에서 표시된 “최신 앱에서 다시 실행해 주세요”는 GPS 수집 실패가 아니었다. 휴대폰은 대상 사진의 GPS 70건을 Mac으로 정상 전송했지만, 앱과 서버가 서로 다른 날짜 범위를 검증해 서버가 재분석 요청을 거절했다.

- Story 표시 범위: 추천 사진이 실제로 존재한 `2026-08-14 ~ 2026-08-16`
- 원래 분석 요청 범위: 사용자가 지정한 `2026-08-11 ~ 2026-08-20`
- 0.7.2 앱이 영수증에 넣은 범위: Story 표시 범위
- 서버가 요구한 범위: 원래 분석 요청 범위
- 서버 응답: HTTP 428 `location_prefetch_scope_mismatch`

따라서 “최신 앱” 문구만으로는 실제 원인을 알 수 없었다. 0.7.3은 Story 표시 범위와 재분석 범위를 API에서 분리하고, 앱은 재분석 범위로 GPS를 선동기화한다.

## 운영 근거

운영 데이터에서 다음을 확인했다.

- 대상 Story `story-manual-op-201ddb5f346d4545a2481a87`의 표시 범위는 `8/14~8/16`, 사진은 39장이다.
- 이 Story를 만든 원래 수동 작업 `manual-op-201ddb5f346d4545a2481a87`의 요청 범위는 `8/11~8/20`이다.
- Android `android-bridge-2`가 보낸 GPS manifest 70건이 Mac의 위치 ledger에 저장돼 있다.
- 저장된 GPS의 촬영 시각은 `8/14~8/16`에 분포한다. 위치가 있는 사진이 그 기간에만 존재해도 원래 요청 범위 전체를 확인했다는 영수증의 범위는 `8/11~8/20`이어야 한다.

좌표와 사진 식별자는 이 점검 결과에 기록하지 않았다.

## 구현 변경

### 재분석 범위 계약

- 모바일 Story projection에 `analysis_date_from`, `analysis_date_to`를 추가했다.
- 화면에는 기존 `date_from`, `date_to`를 사용해 실제 Story 사진 날짜를 계속 보여준다.
- `같은 기간 전체 재분석`은 `analysis_date_from`, `analysis_date_to`를 사용한다.
- 구형 Story에 분석 범위가 없으면 기존 표시 범위로 안전하게 대체한다.

### 오류 설명

앱이 HTTP 상태만 보던 방식을 바꿔 서버 JSON의 안전한 `error` 코드를 해석한다. 이제 428 오류에서 다음을 구분한다.

- 분석 날짜 범위 불일치
- 휴대폰 전송 완료 후 Mac 수신 대기
- GPS 영수증 만료
- GPS 선동기화 누락
- 조회 수와 GPS 수 불일치
- 영수증 형식 오류

따라서 무조건 “최신 앱”이라고 안내하지 않고 원인별 조치를 제시한다.

### 설정 화면 버전 상태

설정 하단에 다음 정보를 표시한다.

- 설치된 앱 버전
- 서버가 발표한 최신 Android 앱 버전
- `최신 버전입니다` 또는 `업데이트 필요 · 최신 버전 x.y.z`

모바일 capabilities 응답에는 `latest_android_app_version`과 `minimum_android_app_version`이 포함된다.

## 검증

| 항목 | 결과 |
|---|---|
| 모바일 API/Story 회귀 테스트 | 통과 |
| 전체 Python 테스트 | `1003 passed` |
| Android debug 빌드 | 통과 |
| Android debug lint | 통과 |
| Android release 빌드/R8 | 통과 |
| Android release lint | 통과 |
| APK 서명 검증 | v2/v3 통과 |
| APK manifest | `versionName=0.7.3`, `versionCode=17` |
| 운영 Story projection | 표시 `8/14~8/16`, 분석 `8/11~8/20` 분리 확인 |
| Tailnet capabilities | 최신/최소 `0.7.3` 확인 |
| Tailnet 다운로드 페이지 | `0.7.3` 확인 |
| Tailnet APK와 배포 파일 checksum | 일치 |

배포 APK SHA-256:

```text
1cd0967fcd76ff75bfe21a09fb11e08a6d91acecce4c0e74d8a0ad3b60027432
```

## 사용자 확인 순서

1. Tailnet 다운로드 페이지에서 0.7.3 APK를 내려받아 기존 앱 위에 설치한다.
2. 앱 `설정` 하단에서 `설치된 앱 버전 0.7.3`과 `최신 버전입니다`를 확인한다.
3. 기존 Story에서 `같은 기간 전체 재분석`을 실행한다.
4. 앱은 원래 요청 범위 `8/11~8/20`의 휴대폰 카메라 원본을 조회하고 GPS를 먼저 동기화한다.
5. 재분석이 등록되면 진행 화면으로 이동한다. 거절되면 팝업 첫 문장에서 정확한 원인을 확인한다.

현재 Mac에 ADB 기기가 연결돼 있지 않아 원격 설치와 휴대폰 UI 실조작은 수행하지 않았다. APK 배포, 서버 교체, Tailnet 다운로드 검증까지는 완료했다.
