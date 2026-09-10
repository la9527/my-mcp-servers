# Android 뷰어 시스템 바 보존

## 결과

추천 사진 크게 보기의 `Theme_DeviceDefault_NoActionBar_Fullscreen` Dialog를 `Theme_DeviceDefault_NoActionBar`로 변경했다. viewer가 열릴 때 Android 상단 상태바와 하단 내비게이션 바를 명시적으로 표시하며 immersive, fullscreen, hide-navigation 상태를 제거한다.

Android 15 이후의 edge-to-edge 동작과 카메라 cutout이 있는 단말도 고려해 viewer root에는 다음 inset을 padding으로 적용한다.

- status bars
- navigation bars
- display cutout

따라서 검은 viewer 배경은 시스템 바 뒤까지 자연스럽게 이어질 수 있지만 사진, 배율·닫기 control, 하단 위치 indicator, 설명은 시스템 영역과 겹치지 않는다. 상태바와 내비게이션 바 배경은 viewer의 짙은 배경색에 맞추고 아이콘은 밝은 모양으로 유지한다.

## 호환성

- Android 11 이상은 `WindowInsetsController.show(statusBars | navigationBars)`를 사용한다.
- Android 8~10은 fullscreen, hide-navigation, immersive와 light-system-bar flag를 해제한다.
- Android 9부터만 제공되는 navigation bar divider 색상 API는 SDK 조건으로 보호한다.
- 최소 지원 버전 API 26은 그대로 유지한다.

## 배포·검증

```text
versionName: 0.4.2
versionCode: 6
APK size: 96,529 bytes
SHA-256: 857729d6df7e48a6e5063eb414e85f6e7ca814a80e5c9e78401679b3dc0e91c1
```

- mobile client 대상 Python 테스트 5개 통과
- Android debug compile·lint 통과
- R8 release assemble·lint 통과
- APK Signature Scheme v2·v3 검증 통과

현재 ADB에 연결된 단말이 없어 실제 Samsung One UI에서 상태바 시계·알림 아이콘과 하단 내비게이션 영역을 눈으로 확인한 것으로 기록하지 않는다. 설치 후 viewer를 열어 두 영역이 유지되고 사진과 하단 indicator가 겹치지 않는지 확인하는 것이 마지막 실기기 gate다.
