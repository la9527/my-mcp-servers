# 사진 뷰어 제스처 중심 컨트롤 정리

## 결과

Android 네이티브 추천 뷰어와 Story HTML 뷰어에서 화면을 가리던 좌우 이동, `+`, `−` 버튼을 제거했다. 핀치·두 번 탭·pan·기본 배율 좌우 flick은 그대로 유지한다. 상단에는 현재 배율을 알리고 선택하면 1×로 초기화하는 버튼과 닫기만 남겼다.

현재 사진의 위치는 화면 하단에서 다음 두 표현을 함께 사용한다.

- `5 / 28` 형식의 tabular 숫자
- 전체 중 현재 위치를 보여주는 3px 진행 막대

사진 수가 많을 때 점을 수십 개 나열하지 않고도 정확한 번호와 대략적인 진행 위치를 동시에 알 수 있다. 숫자는 접근성 live region으로 사진 전환을 알리고 진행 막대는 중복 읽기를 막는다. Story HTML에서는 progressbar role과 `aria-valuenow`, `aria-valuemax`, `aria-valuetext`를 갱신한다.

## 조작 계약

| 입력 | 동작 |
|---|---|
| 두 손가락 pinch | 1×~4× 연속 확대·축소 |
| 두 번 탭 | 1×와 2.5× 전환 |
| 확대 상태 drag | 이미지 경계 안에서 pan |
| 1× 수평 flick | 이전·다음 사진 전환 |
| 상단 배율 | 현재 배율 표시, 선택하면 1× 초기화 |
| 닫기·Android back | 한 장 보기 종료 |
| Story 키보드 방향키·`+`·`−`·`0` | 데스크톱과 보조 입력 fallback |

좌우 버튼과 확대·축소 버튼을 없애도 기능 자체는 사라지지 않는다. 사진 이동과 확대는 제스처가 기본이며 Story의 키보드 fallback은 유지한다.

## 반영 범위

- Android `MainActivity` 추천 사진 전체 화면
- owner 전용 `/mobile-client/story` WebView
- 30일 공유 Story의 동일 HTML renderer
- Story CSS/JS cache version 5
- Android 앱 `0.4.1`, versionCode 5

## 검증

- Story share·mobile client 관련 Python 테스트 12개 통과
- Android `compileDebugJavaWithJavac` 통과
- Android `lintDebug` 통과
- R8 `assembleRelease`, `lintRelease` 통과
- APK Signature Scheme v2·v3 검증 통과
- 문서 검증과 `git diff --check` 통과

배포 APK:

```text
/Users/byoungyoungla/.photos-mcp/runtime/mobile-client/downloads/PhotosMcp-Album.apk
versionName: 0.4.1
versionCode: 5
size: 96,529 bytes
SHA-256: eaf4d6c8d7f1e5040f8c0bcc3e76e8e65a9b48d74db4c192c3d3a3a47ed0f01a
```

실제 Android 단말이 ADB에 연결되지 않은 상태에서는 손가락 입력과 위치 indicator의 실기기 화면을 확인한 것으로 기록하지 않는다. 설치 뒤 핀치, 두 번 탭, 확대 상태 pan, 1× 좌우 flick과 하단 위치 변경을 한 번 확인하는 것이 마지막 실기기 gate다.
