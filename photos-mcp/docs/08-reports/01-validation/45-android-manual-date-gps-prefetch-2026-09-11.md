# Android 수동 날짜 GPS 선동기화 구현·검증

검증일: 2026-09-11

## 1. 결론

Android 앱의 `날짜로 Story 만들기`는 이제 분석 작업을 먼저 등록하지 않는다. 사용자가 선택한 촬영 날짜 범위의 휴대폰 카메라 원본을 다시 조회하고, EXIF GPS와 매칭용 원본 SHA-256을 Mac mini의 암호화 위치 원장에 모두 전달한 뒤에만 Apple Photos·Google Photos 통합 분석을 등록한다.

이 변경으로 최근 10일 증분 동기화 체크포인트보다 오래된 8월 6~9일 같은 기간도 수동 Story 실행 시 다시 읽힌다. 전송하지 못한 암호화 배치가 하나라도 남으면 분석을 시작하지 않으므로, 추천·Story가 먼저 생성되어 모두 `위치 미상`이 되는 순서 역전을 차단한다.

## 2. 기존 문제의 원인

수동 실행 화면의 사진 수 미리보기와 GPS 동기화는 서로 다른 경로였다.

1. `MediaDayCounter`는 사용자가 선택한 정확한 촬영일 범위를 `DATE_TAKEN`으로 세었다.
2. 분석 시작 버튼은 곧바로 Mac mini의 수동 작업 API를 호출했다.
3. Android GPS Bridge는 별도 일일 작업에서 `DATE_ADDED` 체크포인트 이후의 최근 사진만 읽었다.
4. 따라서 화면에는 과거 날짜 사진 수가 보여도 해당 원본 GPS manifest는 위치 원장에 없을 수 있었다.
5. Google Picker 추천이 생성된 뒤 휴대폰 원본과 매칭할 GPS가 없으면 Story는 `위치 미상`으로 남았다.

서버는 이미 추천 파일 생성 후 `원본 해시 우선 → 촬영시각·크기 보조`로 Android GPS를 Google 추천에 투영하고, 그 다음 Story를 재생성하는 순서를 갖고 있었다. 누락된 연결은 수동 실행 전에 선택 기간 manifest를 보장하는 Android 측 선행 단계였다.

## 3. 반영한 실행 계약

```text
날짜·출처·장수 선택
  → 사진 수 미리보기
  → 분석 시작 확인
  → Android 전체 사진 + 원본 위치 권한 확인
  → KST 촬영일 범위의 DCIM/Camera 원본 재조회
  → GPS가 있는 원본의 좌표·촬영시각·크기·SHA-256 생성
  → 100건 단위 암호화 outbox 전송
  → outbox 잔여 0건 확인
  → Mac mini 수동 분석 작업 등록
  → Google 우선 취득·추천 생성
  → Android 원본 GPS 보수적 매칭
  → 위치가 반영된 scoped Story 생성
```

세부 정책은 다음과 같다.

- 날짜 기준: `Asia/Seoul`, 시작일·종료일 모두 포함
- 조회 범위: 최대 31일
- 조회 상한: 최대 1,000장
- 대상: Android 10 이상에서 `DCIM/Camera/` 원본만 조회해 화면 캡처를 제외
- 권한: 전체 사진 읽기와 `ACCESS_MEDIA_LOCATION`이 모두 있어야 분석 시작 가능
- 전송: 기존 기기 키 서명, nonce, sequence, idempotency key, AES-GCM outbox 유지
- 실패: 권한 거부·수신 실패·전송 잔여 발생 시 분석을 등록하지 않고 앱에 원인을 표시
- 체크포인트: 과거 범위 조회는 일일 증분 `media_checkpoint`를 변경하지 않음
- 재실행: 같은 날짜를 다시 실행할 수 있으며 서버 원장은 중복 manifest를 안전하게 수용
- GPS 0건: 해당 범위 원본에서 실제 GPS가 발견되지 않은 정상 결과로 처리하고 분석은 계속 가능

Android GPS는 기존 설계대로 Google Photos Picker 파일의 위치 복원에 사용한다. Apple Photos는 Photos 보관함의 원본 메타데이터를 우선 사용한다. 두 출처의 추천은 하나의 scoped Story로 합쳐진다.

## 4. 앱 사용자 경험

- 수동 Story 화면에서 선택 기간 GPS를 먼저 동기화한다는 사실을 명시한다.
- 사진 수 미리보기는 분석 대상 수와 혼동되지 않도록 `이 휴대폰 카메라 원본`으로 표시한다.
- 시작 직후 `선택한 날짜의 휴대폰 원본 GPS를 먼저 동기화하고 있어요…` 상태를 보여준다.
- 성공하면 GPS manifest 건수와 분석 등록 단계를 이어서 보여준다.
- 권한이나 네트워크 문제면 `분석을 시작하지 않았습니다`를 명시하고 재시도 버튼을 다시 활성화한다.

기존에 생성된 `위치 미상` Story는 APK 업데이트만으로 자동 변경되지 않는다. 0.7.1 앱에서 같은 날짜를 다시 분석하면 선동기화 계약이 적용된다. 휴대폰 원본 자체에 GPS가 없거나 Google 추천과 안전하게 일치하지 않는 사진은 근거 없는 위치를 만들지 않고 계속 위치 미상으로 둔다.

## 5. 검증 결과

| 검증 | 결과 |
|---|---|
| Android debug compile·assemble·lint | 성공 |
| Android release R8·assemble·lint | 성공 |
| APK 서명 | APK Signature Scheme v2·v3 검증 통과 |
| GPS 매칭·모바일 API·Story 집중 회귀 | 65개 통과 |
| Python 전체 회귀 | 1,000개 통과, 14.29초 |
| `git diff --check` | 통과 |
| Tailnet 다운로드 페이지 | 0.7.1 노출, HTTP 200 |
| Tailnet 다운로드 파일 일치 | 112,913 bytes, SHA-256 일치 |

배포 산출물:

- Android 버전: `0.7.1` (`versionCode=15`)
- APK: `~/.photos-mcp/runtime/mobile-client/downloads/PhotosMcp-Album.apk`
- Tailnet: `https://byoungyoung-macmini.tail53bcc7.ts.net/mobile-client/download`
- SHA-256: `989d8454a8b3fe30eb1ccef2546cc9e236f829d33bf452a020a8adda35b6d035`

실사진 GPS 수와 최종 장소 매칭 수는 개인 사진·단말 권한에 의존하므로 저장소 문서에 개별 좌표를 남기지 않는다. APK 설치 후 동일 기간을 실행하면 앱 상태, Mac 위치 원장의 aggregate 수, 해당 Story의 위치 표시 수로 최종 운영 확인할 수 있다.
