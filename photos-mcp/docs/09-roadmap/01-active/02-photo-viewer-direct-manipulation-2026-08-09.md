# 전체 화면 사진 뷰어 직접 조작 UX

## 상태

- 단계: 구현 및 자동·설치 앱 핵심 검증 완료
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
- Apple [IKImageView](https://developer.apple.com/documentation/Quartz/IKImageView)는 `setImageZoomFactor(_:center:)`, 이미지·뷰 좌표 변환, 지정 위치 scroll API를 제공한다. 현재 뷰어가 사용하는 네이티브 ImageKit 안에서 확대 기준점과 이동을 처리할 수 있다.
- Apple [Scroll views](https://developer.apple.com/design/human-interface-guidelines/scroll-views/)는 기본 scroll 제스처와 키보드 동작을 유지하고, 확대를 지원하면 최소·최대 배율을 명확히 제한하도록 권장한다.

외부 사진 뷰어 라이브러리는 도입하지 않는다. 현재 `IKImageView`의 메타데이터 처리, RAW 캐시 표시, 접근성 구조를 보존하면서 필요한 API를 그대로 사용할 수 있다.

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
  -> PhotosMcpZoomImageView
  -> image view 좌표를 image 좌표로 변환
  -> PhotosMcpPhotoViewerController
  -> IKImageView.setImageZoomFactor(center:)
  -> IKImageView.scroll(to:)로 기준점을 화면 중앙에 배치
```

구현 항목:

1. `PhotosMcpZoomImageView`에 event 위치를 image 좌표로 바꾸는 helper와 drag 시작·이동·종료 처리를 추가한다.
2. controller에 `zoom_at_image_point`, `fit_zoom_factor`, `pan_by_view_delta`를 둔다. ImageKit 좌표 변환과 동일 zoom 중심 갱신을 이용해 읽기 전용 상태에서도 drag pan을 처리하고, 배율 계산과 clamp는 한 곳에서 처리한다.
3. 더블클릭과 `smartMagnifyWithEvent_`는 같은 `toggle_zoom_at_point`를 사용한다.
4. pinch와 `Command` + scroll은 발생 위치를 anchor로 하는 연속/단계 확대를 사용한다.
5. RAW 캐시 준비·URL 비동기 재그리기 generation이 완료된 뒤에도 수동 조작 generation을 확인하여, 이미 사용자가 확대·이동했다면 `zoomImageToFit_`을 다시 호출하지 않는다.
6. ImageKit의 기본 edit panel은 계속 열지 않도록 `doubleClickOpensImageEditPanel`을 명시적으로 끈다. 이 앱은 읽기 전용 결과 뷰어다.

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
| `src/photos_mcp/photo_viewer_appkit.py` | anchor zoom, read-only drag pan, cursor, 비동기 재배치 보호 |
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
- controller는 `setImageZoomFactor:centerPoint:`에 이미지 좌표를 전달해 클릭한 지점을 기준으로 확대하고, 두 번째 double-click에서 화면 맞춤으로 복귀한다.
- 확대 상태의 mouse drag는 시작 위치와 현재 위치의 delta를 image 좌표로 환산해 읽기 전용 이미지 중심을 이동한다. ImageKit이 같은 배율의 중심 변경을 무시하는 경우에는 즉시 복원되는 미세 배율 pulse로 새 중심을 적용한다.
- 설치 앱에서 500장 RAW 결과를 열어 우하단 지점 double-click 확대, 화면 맞춤 복귀, toolbar 접근성 라벨과 비활성 상태 전환을 확인했다.
- Computer Use의 Cocoa drag 재현은 포인터 좌표가 이동하지 않는 제한이 있어 화면 캡처만으로 drag 결과를 판정하지 않았다. 대신 실제 AppKit `mouseDown`·`mouseDragged`·`mouseUp` 이벤트가 pan controller로 전달되는 자동 회귀 테스트와 delta 중심 이동 테스트를 추가했다.
