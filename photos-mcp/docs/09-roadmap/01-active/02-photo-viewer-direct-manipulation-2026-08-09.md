# 전체 화면 사진 뷰어 직접 조작 UX

## 상태

- 단계: 표준 스크롤 뷰 재구현 및 자동·설치 앱 검증 완료
- 작성일: 2026-08-09
- 대상 화면: `사진 크게 보기`
- 목표: 사진 앱과 Preview에서 기대하는 방식으로 확대 지점을 보존하고, 확대된 사진을 직접 끌어 이동

## 문제

현재 전체 화면 뷰어는 trackpad 확대, `Command` + scroll, toolbar와 키보드 확대·축소를 지원한다. 그러나 더블클릭은 `화면 맞춤`과 `실제 크기`를 단순 전환할 뿐 클릭한 지점을 중심으로 확대하지 않는다. 확대된 사진을 마우스로 끌어 보는 동작도 명시적 계약과 검증이 없다.

사진의 특정 인물이나 세부 장면을 보려는 사용자는 확대 후 다시 원하는 위치로 이동해야 한다. 이는 직접 조작이라는 기대와 맞지 않고, 특히 큰 RAW 사진에서 반복 동작을 만든다.

휴리스틱 평가는 `7/10`, 심각도 `2(경미하지만 반복적인 지연)`이다. 확대 기준점, 이동 방식, 복귀 동작과 대체 입력 수단을 일관되게 제공하면 `10/10`으로 본다.

## 조사 근거

- Apple [Gestures](https://developer.apple.com/design/human-interface-guidelines/gestures/)는 macOS에서 double-click을 확대·축소의 표준 제스처로, drag를 직접 조작으로 정의한다. 익숙한 제스처는 문맥마다 같은 의미로 동작해야 한다.
- Apple [Pointing devices](https://developer.apple.com/design/human-interface-guidelines/pointing-devices/)는 Mac의 smart zoom을 콘텐츠 확대·축소 동작으로 제시한다. 마우스와 trackpad 설정이 다를 수 있으므로 특정 입력만 강제하면 안 된다.
- Apple [Scroll views](https://developer.apple.com/design/human-interface-guidelines/scroll-views/)와 AppKit `NSScrollView`는 확대 콘텐츠의 clip origin, scroll bar, trackpad scroll을 표준 방식으로 제공하며 최소·최대 배율을 명확히 제한하도록 권장한다. ImageKit 내부 scroll 상태는 앱이 안정적으로 읽고 갱신할 수 없으므로 결과 뷰어는 `NSScrollView`와 읽기 전용 사진 canvas를 사용한다.

외부 사진 뷰어 라이브러리는 도입하지 않는다. AppKit `NSScrollView`, `NSClipView`, `NSImage`만 사용하고 기존 RAW 고해상도 캐시와 접근성 구조를 보존한다.

## 확정 제안

### 1. 확대 기준점

| 입력 | 동작 | 기준점 |
| --- | --- | --- |
| 사진 더블클릭 | 화면 맞춤 상태에서는 `2x` 단계 또는 실제 크기 중 더 작은 유효 배율로 확대 | 더블클릭한 사진 내부 위치가 뷰어 중앙에 오도록 이동 |
| 확대 후 다시 더블클릭 | `화면 맞춤`으로 복귀 | 전체 사진 |
| trackpad smart zoom | 더블클릭과 같은 토글 | smart zoom 발생 위치 |
| pinch 확대·축소 | 연속 확대·축소 | pinch 중심 |
| `Command` + scroll | 단계 확대·축소 | 포인터 위치 |
| toolbar `+`, `−`, `Command` + `+`, `Command` + `−` | 단계 확대·축소 | 현재 보이는 영역의 중앙 |
| `화면 맞춤`, `100%` 버튼 | 각각 화면 맞춤, 실제 크기 | 전체 사진 또는 현재 보이는 영역의 중앙 |

더블클릭은 일반 클릭의 선택·이동 의미를 빼앗지 않는다. 한 번 클릭은 사진 상태를 바꾸지 않으며, 더블클릭만 확대 토글로 소비한다.

### 2. 이동과 scroll

- 화면 맞춤보다 확대된 상태에서 primary button을 누른 채 움직이면 사진을 그 이동량만큼 pan한다.
- drag 시작 시 open-hand, 이동 중 closed-hand cursor를 사용하고, 버튼을 놓으면 기본 cursor로 복귀한다.
- 확대되지 않은 상태의 drag는 사진 위치를 움직이지 않는다. 사용자가 실수로 사진을 잃는 것을 막는다.
- 일반 scroll은 현재처럼 사진을 세로·가로로 이동한다. `Command` + scroll만 확대·축소한다.
- scroll bar는 자동 숨김을 유지하되 확대된 사진에서 이동 가능함을 보여 준다.

### 3. 배율 상태와 복귀 규칙

- 최소 배율은 `화면 맞춤` 배율이다. 사진보다 작은 축소로 검은 빈 공간만 늘어나는 동작은 허용하지 않는다.
- 최대 배율은 현재와 같은 `8x`이며, 원본 또는 RAW 캐시 해상도보다 의미 없는 확대가 되지 않도록 제한한다.
- 수동 확대·이동 후에는 이미지 비동기 로드 재그리기와 창 resize가 사용자가 고른 배율·위치를 덮어쓰지 않는다.
- 사진을 이전·다음으로 이동하거나 새 사진을 열면 `화면 맞춤`으로 시작한다. 사진별 확대 상태는 저장하지 않는다.
- 상태 문자열은 현재 사진 순번과 RAW 준비 상태만 표시한다. 배율은 VoiceOver label과 toolbar tooltip으로 제공하며 정보를 과도하게 늘리지 않는다.

### 4. 접근성과 키보드

- 모든 동작은 toolbar 버튼으로도 가능하게 유지한다. 제스처만으로 기능을 숨기지 않는다.
- `Command` + `+`, `Command` + `−`는 확대·축소를 유지한다.
- `Escape` 닫기, 좌우 화살표 이전·다음 사진은 그대로 유지한다.
- toolbar 버튼은 `확대`, `축소`, `화면에 맞춤`, `실제 크기`의 접근성 라벨과 tooltip을 제공한다. 확대가 상한·하한에 닿으면 해당 버튼을 비활성화하거나 이유를 VoiceOver에 전달한다.

## 구현 설계

`PhotosMcpZoomImageView`와 `PhotosMcpPhotoViewerController`의 책임을 아래처럼 분리한다.

```text
NSEvent (double click / smart magnify / pinch / command-scroll / drag)
  -> PhotosMcpZoomImageView (read-only canvas)
  -> document 좌표를 원본 image 좌표로 변환
  -> PhotosMcpPhotoViewerController
  -> 확대된 document frame과 image rect 계산
  -> NSClipView.scrollToPoint로 기준점을 중앙 배치하거나 drag delta 반영
```

구현 항목:

1. `PhotosMcpZoomImageView`는 사진을 읽기 전용 canvas에 그리고 event 위치와 drag 시작·이동·종료를 controller로 전달한다.
2. controller는 원본 image 좌표, 확대된 표시 rect, `NSClipView` origin을 명시적으로 관리한다. drag delta와 scroll 범위 clamp는 한 곳에서 처리한다.
3. 더블클릭과 `smartMagnifyWithEvent_`는 같은 `toggle_zoom_at_point`를 사용한다.
4. pinch와 `Command` + scroll은 발생 위치를 anchor로 하는 연속/단계 확대를 사용한다.
5. RAW 캐시 준비·URL 비동기 재그리기 generation이 완료된 뒤에도 수동 조작 generation을 확인하여, 이미 사용자가 확대·이동했다면 `zoomImageToFit_`을 다시 호출하지 않는다.
6. canvas에는 파일 drop, 선택, 편집 또는 저장 경로를 두지 않는다. 확대·이동은 화면 상태만 바꾸며 원본은 계속 읽기 전용이다.

## 검증 계획

### 자동 테스트

- 서로 다른 클릭 좌표에서 target zoom과 ImageKit center 좌표가 맞는지 확인한다.
- 화면 맞춤 → 더블클릭 확대 → 다시 더블클릭 화면 맞춤 전환을 확인한다.
- pinch, `Command` + scroll, toolbar 단축키가 동일한 최소·최대 배율 정책을 지키는지 확인한다.
- 확대된 상태에서 drag delta가 pan으로 변환되고 화면 맞춤 상태에서는 이동하지 않는지 확인한다.
- 이미지 전환과 지연된 URL/RAW 로드 콜백이 이전 사진 또는 사용자의 수동 확대를 덮어쓰지 않는지 확인한다.

### 설치 앱 검증

1. JPEG, HEIC, SONY ARW를 각각 연다.
2. 사진의 좌상단, 중앙, 우하단을 더블클릭해 해당 위치가 중앙으로 이동하는지 확인한다.
3. 확대 상태에서 마우스와 trackpad로 끌어 이동하고, 일반 scroll과 `Command` + scroll의 역할이 분리되는지 확인한다.
4. 확대된 상태에서 창 크기 변경, 정보 panel 토글, 이전·다음 사진 이동을 수행해 겹침·깜빡임·의도치 않은 화면 맞춤이 없는지 확인한다.
5. 최소 창, 기본 창, 전체 화면에서 toolbar 접근성·버튼 상태·keyboard-only 조작을 확인한다.

## 영향 범위

| 경로 | 변경 |
| --- | --- |
| `src/photos_mcp/photo_viewer_appkit.py` | 표준 scroll view, anchor zoom, read-only drag pan, cursor, 비동기 재배치 보호 |
| `tests/test_photo_viewer_appkit.py` | 좌표·배율·drag·지연 로드 회귀 |
| `docs/02-user-guide/04-results-and-export.md` | 실제 뷰어 조작법 |
| `docs/02-user-guide/05-keyboard-shortcuts.md` | 단축키 확인 및 갱신 |
| `docs/07-design-system/04-screen-patterns.md` | 전체 화면 사진 뷰어 상호작용 계약 |
| `docs/07-design-system/05-accessibility.md` | 비제스처 대체 입력·VoiceOver 규칙 |

## 완료 조건

- 사용자가 더블클릭한 사진 위치가 확대 후 중앙에 보인다.
- 확대된 사진은 mouse/trackpad drag로 자연스럽게 이동한다.
- pinch, smart zoom, `Command` + scroll, toolbar, keyboard가 충돌하지 않고 같은 배율 제한을 사용한다.
- RAW와 일반 이미지 모두 창 resize 없이 표시되고, 수동 확대·이동 중 비동기 재그리기가 상태를 덮어쓰지 않는다.
- 자동 테스트, 문서 검사, 기본·최소·전체 화면의 설치 앱 검증을 모두 통과한다.

## 구현 결과

- `PhotosMcpZoomImageView`는 double-click, smart zoom, pinch, `Command` + scroll의 발생 위치를 controller로 전달한다.
- controller는 클릭 위치의 원본 image 좌표를 계산한 뒤 확대된 document에서 그 좌표가 뷰 중앙에 오도록 `NSClipView` origin을 갱신한다. 가장자리도 중앙에 배치할 수 있도록 수동 확대 상태에서만 반 화면 크기의 검은 여백을 허용한다.
- 확대 상태의 mouse drag는 시작 clip origin과 포인터 delta로 계산한다. 가로축은 delta를 빼고, 위쪽으로 증가하는 window 좌표와 아래쪽으로 증가하는 flipped document 좌표가 반대인 세로축은 delta를 더해 잡은 이미지 지점이 포인터를 그대로 따라가게 한다. 범위를 벗어난 값은 document 크기에 맞게 clamp한다.
- 500장 RAW 결과 설치 앱에서 2단계 확대 후 세로·가로 drag를 각각 재검증했다. 아래로 150px 이동할 때 세로 scroll bar가 `0.500 → 0.338`, 오른쪽으로 200px 이동할 때 가로 scroll bar가 `0.500 → 0.356`으로 감소하며 잡은 사진 지점이 포인터와 같은 방향으로 이동했다. 아래쪽·위쪽·가로쪽 포인터 이동은 `NSClipView` 실객체 회귀 테스트로 고정했다.
