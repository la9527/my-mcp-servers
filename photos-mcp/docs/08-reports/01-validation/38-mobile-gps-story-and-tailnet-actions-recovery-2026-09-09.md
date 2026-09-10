# Android GPS Story 연결·Tailnet 결과 링크 복구 검증

## 결론

Android 앱이 전송한 원본 GPS manifest는 정상 보관되고 있었지만, Google Picker가 재인코딩한 추천 사본과 GPS sidecar를 연결하는 운영 단계가 없었다. 그 결과 수동 Story 97장이 모두 `위치 미상`으로 표시됐다. 또한 5분 주기의 추천 보관 조정은 이미 완료된 실행마다 앨범 발행과 Story 생성을 반복해 60초를 넘겼고, Hermes가 이를 실제 외장 볼륨 장애로 잘못 안내했다. Tailnet `/photos-actions`는 request id 없는 기본 route가 없어 404를 반환했다.

이번 변경으로 세 경로를 함께 복구했다.

- Android 원본 GPS와 Google 추천 사본을 강한 digest 또는 촬영시각 ±0.5초·회전 호환 크기의 양방향 유일 일치로만 연결한다.
- 확정할 수 없는 사진은 추측하지 않으며 같은 장면·인접 시간의 단일 장소 합의만 좌표 없는 `contextual_estimate`로 저장한다.
- 모바일 수동 분석은 추천 보관 완료 뒤 GPS projection을 먼저 수행하고 scoped Story를 생성한다.
- loopback 추천 조정도 GPS projection을 수행하되 새 위치가 생겼을 때만 Story를 갱신한다.
- 이미 완료된 로컬 보관 run은 재발행·재생성하지 않는 빠른 no-op으로 바꿨다.
- `/actions`는 `/photos`로 303 이동하며, Tailnet `/photos-actions` 기본 링크도 owner gallery로 연결된다.
- 조정 timeout 알림은 외장 볼륨 고장으로 단정하지 않고 다음 주기 자동 재시도를 안내한다.

## 운영 데이터 복구 결과

개인 사진 파일명, 기기 asset key, 좌표는 검증 출력과 이 문서에 기록하지 않았다.

| 항목 | 결과 |
|---|---:|
| 로컬 추천 사진 | 97장 |
| Android GPS manifest | 271건 |
| 확정 GPS 연결 | 75장 |
| 좌표 없는 문맥 추정 | 15장 |
| 근거 부족으로 위치 미상 유지 | 7장 |
| 모호한 자동 연결 | 0장 |
| 읽기 실패 | 0장 |

기존 global Story와 2026-09-03~2026-09-09 수동 Story는 원본·추천 사본을 삭제하지 않고 새 manifest revision으로 갱신했다. 수동 Story 97장의 최종 위치 상태는 `confirmed_gps=75`, `contextual_estimate=15`, `unknown=7`이다.

## 장애 원인과 방지 정책

Google Picker 사본의 SHA-256은 Android 원본과 일치하지 않았다. Picker export 과정의 재인코딩·metadata 제거가 원인이므로 파일명, 단일 perceptual hash 또는 가장 가까운 시간만으로 위치를 복사하지 않는다. 시간·크기 연결은 양쪽에서 후보가 정확히 하나인 경우에만 허용한다. 이후 Android manifest가 추가되면 기존 문맥 추정도 확정 GPS로 승격할 수 있다.

추천 보관 조정은 durable collection과 automation run 양쪽이 terminal `completed/partial`이고 collection id가 같으면 즉시 건너뛴다. 따라서 5분 poll은 vendor, Apple Photos 발행, remote Story LLM을 다시 호출하지 않는다.

## 검증

| 검증 | 결과 |
|---|---|
| PhotosMcp 전체 Python 테스트 | 861 passed |
| Hermes 알림 브리지 테스트 | 29 passed |
| standalone deep codesign | 통과 |
| bundle health/runtime/vendor smoke | 모두 통과 |
| 운영 health | ready, HTTP 200 |
| loopback reconcile | HTTP 200, 0.084초 |
| Tailnet `/photos-actions` | HTTP 303 → `/photos` |
| Tailnet `/photos` | HTTP 200 |
| 기존 오탐 조정 알림 pending | 0건 |

운영 bundle은 `/Volumes/ExtData/02_Services/PhotosMcp/PhotosMcp.app`에 설치했고 `/Applications/PhotosMcp.app` 링크, main server, Tailnet 전용 mobile client를 재시작했다. 이번 변경은 Android UI나 API 계약을 바꾸지 않으므로 APK 교체 및 ADB 작업은 필요하지 않다.
