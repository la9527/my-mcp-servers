# Android 수동 작업·Google Picker 복구 안정화 검증

## 결론

2026-09-08 23:51 KST에 Android 앱에서 실행한 Apple Photos 250장 + Google Photos 250장 통합 작업은 약 8분 뒤 실패했다. 전체 작업 제한은 6시간이었지만 Google Picker의 Qwen 선택 단계에 별도 300초 기본값이 적용됐고, 173장을 선택한 뒤 32장만 다운로드한 상태에서 작업이 중단됐다. 같은 시각 Android 앱은 종료된 Activity의 예약 폴링이 이미 종료된 executor를 다시 호출해 `RejectedExecutionException`으로 종료됐다.

이번 변경은 두 장애를 다음과 같이 수정한다.

- Android 수동 작업 폴링은 Activity 및 화면 lifecycle에 묶는다. 화면을 다시 그리거나 Activity가 종료되면 예약된 콜백을 제거하고 generation을 폐기한다.
- UTC로 저장된 실행·알림 시각은 앱에서 `Asia/Seoul`로 변환해 표시한다.
- 모바일 서버는 전체 6시간 상한 안에서 요청 장수에 비례한 Qwen 선택 예산을 명시적으로 전달한다.
- Google 다운로드 중 일부만 성공하면 성공한 파일과 sidecar를 보존하고 즉시 분석한다. 완료하지 못한 장수는 `unfinished_count`로 기록해 통합 결과와 Telegram에 남긴다.
- 프로세스가 강제 중단된 경우에는 동일 날짜·장수·재분석 정책에 맞는 unbound 다운로드 checkpoint만 다음 실행에서 복구한다.
- Picker worker의 개인정보 비포함 JSON 진행 로그를 권한 `0600`으로 별도 보존하고 browser mission 원장에 선택·다운로드·잔여 개수를 기록한다.

## 장애 재현 근거

| 항목 | 관측 결과 |
|---|---:|
| 통합 실행 | `combined-461caa17ff614124a427` |
| 요청 범위 | 2026-09-01 ~ 2026-09-08 |
| 요청 상한 | Apple 250 + Google 250, 전체 500 |
| 전체 제한시간 | 21,600초 |
| Apple 발견 | 34장, 모두 기존 처리로 no-op |
| Google Picker 선택 | 173장 |
| 중단 전 다운로드 | 32장 |
| 분석 제출 | 0장 |
| 기존 Qwen 미션 제한 | 300초 |
| Android 오류 | 종료된 executor에 대한 `RejectedExecutionException` |

사진 내용, 위치, Picker URL, OAuth 토큰은 검증 문서와 worker 로그에 기록하지 않는다.

## 제한시간 정책

모바일 수동 작업의 외부 계약은 기존과 동일하게 최대 21,600초다. Qwen 선택 단계는 `min(전체 제한, max(600, min(7200, 요청 장수 × 4초)))`를 사용한다.

| Google 요청 장수 | Qwen 선택 예산 |
|---:|---:|
| 1~150장 | 600초 |
| 250장 | 1,000초 |
| 500장 | 2,000초 |
| 1,000장 | 4,000초 |

선택 예산을 넘기면 기존 deterministic fallback을 사용할 수 있으며, 선택·다운로드·분석·Story 전체는 여전히 6시간 상한을 넘지 않는다. CLI의 `--model-mission-timeout-seconds` 검증 상한도 전체 작업 계약과 동일한 21,600초로 확장했다.

## 부분 완료와 다음 실행 정책

```text
Picker 선택 완료
  └─ Google 사진 병렬 다운로드
       ├─ 모두 성공 → 전체를 분석
       ├─ 일부 성공 → 성공 파일을 분석 + 나머지 unfinished_count 기록
       └─ 프로세스 중단 → 완료 파일 lease 보존
                            └─ 다음 동일 범위 실행에서 checkpoint 우선 제출
```

부분 다운로드에서 분석된 Google asset은 처리 원장에 `submitted/completed`로 기록된다. 다음 실행에서 같은 날짜를 다시 Picker로 선택해도 `reanalyze=false`이면 이미 처리된 asset을 제외하므로 남은 사진부터 진행된다. `reanalyze=true`는 기존 분석 제외를 명시적으로 우회한다.

복구는 다음 조건을 모두 만족하는 session에만 허용한다.

- 이전 browser mission이 running, failed 또는 cancelled 상태
- 명시 날짜 시작·종료가 동일하거나 recent-days 범위가 동일
- Google 선택 상한이 동일
- 재분석 정책이 동일
- 다운로드 lease가 실제 파일을 가리키며 아직 분석 job에 바인딩되지 않음

다른 날짜나 다른 작업에 속한 파일을 자동으로 섞지 않는다. 분석 job에 이미 바인딩된 파일도 복구 후보에서 제외한다.

## Android lifecycle 수정

수동 작업 상세는 5초마다 상태를 조회한다. 이제 다음 조건을 모두 만족할 때만 executor를 사용한다.

- polling generation이 현재 화면과 동일
- 현재 화면이 작업 화면
- Activity가 finishing/destroyed 상태가 아님
- executor가 종료되지 않음

화면 전환, 같은 작업 탭의 재렌더링, Activity 종료 시 예약 callback을 즉시 제거한다. executor 종료와 callback 실행이 경합하더라도 `RejectedExecutionException`은 Activity teardown의 정상 경계로 처리해 앱을 종료시키지 않는다.

## 검증 결과

- Python 전체 회귀: `854 passed`
- 추가 회귀 범위:
  - 250장 요청의 Qwen 선택 예산이 1,000초로 전달되는지
  - 다운로드 한 건이 실패해도 성공 파일·sidecar·lease가 남는지
  - 부분 파일이 분석되고 잔여 장수가 child/combined 결과에 보존되는지
  - 동일 scope checkpoint만 Picker를 다시 열지 않고 복구하는지
  - 기존 정상 전체 다운로드 응답 계약이 변하지 않는지
- Android Java/R8 release compile 및 lint: 성공
- APK Signature Scheme v2/v3 검증: 성공
- 앱 버전: `0.5.2` (`versionCode 9`)
- 배포 APK: `~/.photos-mcp/runtime/mobile-client/downloads/PhotosMcp-Album.apk`
- APK 크기: `104,721 bytes`
- SHA-256: `889a1261322b2f687003722775da0ffe9b943b2fc8992d535e85692202b498ae`

Tailnet 배포 검증:

| 경로 | 결과 |
|---|---:|
| `443 /mobile-client/download` | 200, 설치 페이지에 0.5.2 표시 |
| `443 /mobile-client/download/PhotosMcp-Album.apk` | 200, 로컬 게시본과 SHA-256 일치 |
| public Funnel `8443 /mobile-client/download/...` | 404 |
| direct loopback 무인증 APK 요청 | 403 |

실기기 설치와 화면 종료·재진입 crash-buffer 검증은 ADB에 `SM-F966N`이 다시 나타나는 즉시 인플레이스 설치로 수행한다. 앱 데이터와 Android Keystore 등록은 유지한다.

## 운영 상태

`com.photosmcp.mobile-client` LaunchAgent를 재시작해 새 worker 실행 정책을 반영했다. loopback에 직접 접근한 무인증 `/mobile-client/v1/capabilities` 요청은 계속 403으로 거절돼 Tailnet owner 경계를 유지한다. Google Picker worker가 현재 실행 중이지 않으므로 실패한 기존 작업이 자동으로 뒤늦게 재개되지는 않는다.
