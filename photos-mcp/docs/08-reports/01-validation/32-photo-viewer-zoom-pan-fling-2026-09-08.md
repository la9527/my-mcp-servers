# 사진 뷰어 확대·플리킹 검증

## 결과

PhotosMcp의 한 장 보기 조작을 다시 설계해 Android 네이티브 추천 뷰어와 Story HTML 뷰어에 함께 반영했다. 기존 Story는 `touchstart`와 `touchend` 사이의 가로 이동량만 검사해 확대를 지원하지 않았고, 네이티브 추천 뷰어는 이전·다음 버튼만 있었다.

0.4.0에서는 두 뷰어 모두 다음 조작 모델을 사용한다.

| 입력 | 동작 |
|---|---|
| `+`, `−` | 중앙 기준 단계 확대·축소 |
| 현재 배율 버튼 | 1×와 현재 배율 표시, 선택하면 1× 초기화 |
| 두 번 탭 | 1× ↔ 2.5× 빠른 전환 |
| 두 손가락 pinch | 터치 중심을 유지하는 1×~4× 연속 확대·축소 |
| 확대 상태 한 손가락 drag | 이미지 경계를 넘지 않는 pan |
| 기본 1× 수평 fling | 이전·다음 사진 전환 |
| 좌우 버튼·키보드 화살표 | 제스처를 쓰기 어려운 경우의 동일 기능 |
| Android back·닫기 | 뷰어만 먼저 닫기 |

## 제스처 충돌 방지

페이지 전환과 확대 이미지 이동을 같은 수평 손동작에 연결하면 의도하지 않은 사진 전환이 생긴다. 다음 우선순위를 적용했다.

1. 두 개 이상의 pointer가 있으면 pinch만 처리한다.
2. 배율이 1×보다 크면 한 pointer 이동은 pan으로만 처리한다.
3. 1×일 때만 수평 이동 거리, 속도, 수평/수직 비율을 모두 통과한 fling을 페이지 전환으로 처리한다.
4. 사진이 바뀌면 scale·translation을 1× 중앙으로 초기화한다.
5. 버튼과 키보드 전환은 현재 배율과 관계없이 명시적인 사용자 요청으로 처리한다.

Android에서는 화면 density에 맞춘 48dp 거리와 500dp/s 속도 기준, 수평 이동이 수직 이동의 1.25배 이상이고 수평 속도가 수직 속도의 1.1배 이상일 때만 fling을 허용한다. Story에서는 mobile viewport 폭의 12% 또는 48px 중 큰 값, 850ms 이하, 수평/수직 1.25배 조건을 사용한다.

## Android 구현

`ZoomableImageView`를 별도 컴포넌트로 추가했다.

- Android `Matrix` 기반 fit-center 원본 상태
- `ScaleGestureDetector` 기반 1×~4× pinch
- `GestureDetector` 기반 double-tap, pan과 fling
- 가로·세로 사진에서 각각 실제 표시 영역을 계산하는 translation clamp
- 새 bitmap이나 화면 크기 변경 시 중앙 1× 초기화
- zoom 상태 callback으로 상단 배율 버튼을 실시간 갱신
- 이전·다음·닫기와 확대 controls 모두 최소 48dp
- 큰 preview를 바꿀 때 비동기 이전 image 결과가 현재 사진을 덮지 못하도록 generation guard 유지

Android 단말은 APK 생성 시 ADB에서 분리된 상태라 실제 손가락 계측은 진행하지 못했다. 다음 자동 gate를 통과했다.

```text
compileDebugJavaWithJavac  성공
lintDebug                  성공
assembleRelease            성공
lintRelease                성공
R8 minify                  성공
```

## Story HTML 구현

외부 Swiper CDN을 추가하지 않고 기존 same-origin `story.js`를 Pointer Events 기반 상태기로 교체했다.

- `touch-action:none`과 pointer capture로 WebView·Chrome의 중복 browser gesture 방지
- pointer map으로 pinch와 한 pointer drag 구분
- 확대 중심을 보존하는 scale·translation 계산
- 이미지 실제 표시 크기와 figure viewport를 이용한 pan clamp
- double-tap 320ms·36px 근접 판정
- 기본 배율에서만 거리·방향·시간 gate를 통과한 좌우 전환
- 앞뒤 preview 사전 요청
- mouse wheel, `+`, `−`, `0`, 좌우 키보드 지원
- 48px 확대·축소·초기화·이전·다음·닫기 control과 한국어 제스처 안내
- 배율·사진 전환 상태를 `aria-label`, `aria-live`로 제공

## 실제 운영 Story 검증

새 standalone `PhotosMcp.app`을 빌드·deep codesign·runtime smoke 후 `/Volumes/ExtData/02_Services/PhotosMcp/PhotosMcp.app`에 설치하고 재시작했다. Tailscale owner URL의 실제 28장 Story를 Google Chrome headless의 412×915 mobile viewport에서 검사했다.

| 시나리오 | 결과 |
|---|---|
| 첫 사진 열기 | `1 / 28` |
| 확대 버튼 | `scale(1.5)` |
| 두 번 탭 | `scale(2.5)` |
| 왼쪽 수평 fling | `1 / 28 → 2 / 28` |
| 두 pointer pinch | `scale(2)` |
| 확대 상태 drag | translate 좌표 변경 확인 |
| JavaScript page error | 0건 |

운영 `/story-assets/story.js?v=4`에서 `pointerdown`, `pinchScale`, `setZoom`이 제공되는 것도 확인했다. public 공유와 Android WebView는 같은 renderer와 same-origin script를 사용하므로 동일한 Story 조작 계약을 사용한다.

## Android 0.4.0 APK

새 뷰어를 포함한 R8 release APK를 기존 개인 개발 설치와 같은 인증서로 서명해 Tailnet 다운로드 경로에 다시 게시했다.

```text
package: com.photosmcp.locationbridge
versionCode: 4
versionName: 0.4.0
APK Signature Scheme: v2, v3 검증 성공
SHA-256: 62729c16d9f104c8abdf07e2ee93e845490bb9e26e4f1384e9ff45c18007658a
```

설치 주소:

```text
https://byoungyoung-macmini.tail53bcc7.ts.net/mobile-client/download
https://byoungyoung-macmini.tail53bcc7.ts.net/mobile-client/download/PhotosMcp-Album.apk
```

Tailnet APK 응답은 `200`, Android package MIME, `PhotosMcp-Album-0.4.0.apk` attachment이고 공개 Funnel 8443에는 이 route가 없다.

Android 앱 내부 브라우저가 APK 링크를 다운로드 관리자에 넘기지 못하는 경우를 줄이기 위해 설치 페이지도 보강했다. 링크에 버전 query, `download="PhotosMcp-Album-0.4.0.apk"`, Android package MIME을 명시하고 응답에는 attachment filename, `Content-Description: File Transfer`, byte range를 제공한다. 설치 페이지에는 Tailscale을 유지한 채 Chrome으로 다시 여는 안내를 표시한다. 운영 URL을 Android Chrome user-agent로 요청해 `200`과 96,641 bytes를 확인했고 1,024-byte range 요청은 `206`으로 응답했다. 이는 서버 전달 계약 검증이며, 실제 단말의 다운로드 관리자 완료까지 확인한 것으로 기록하지 않는다.

## 다음 실기기 확인

휴대폰을 다시 연결하거나 Tailnet 링크에서 0.4.0을 업데이트한 뒤 다음만 손으로 확인하면 된다.

1. 추천 사진에서 `+`, `−`, 배율 초기화가 편하게 동작하는지
2. 사진의 아무 지점을 두 번 탭해 2.5×와 1×를 오가는지
3. 두 손가락 확대 후 한 손가락으로 모서리까지 이동 가능한지
4. 확대 중 수평 pan이 다음 사진으로 잘못 넘어가지 않는지
5. 1×에서 빠른 좌우 flick은 한 장씩 자연스럽게 이동하는지
6. 세로·가로 회전과 Android back 뒤 배율이 정상 초기화되는지

실제 기기 확인 전까지 Android gesture는 build·lint·정적 계약 검증 완료 상태로 기록하고, 실기기 검증 완료로 과장하지 않는다.
