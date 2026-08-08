# 로컬 사진 브라우저 탐색·한 장 보기 재설계

## 목표

로컬 사진 브라우저의 control을 기능 소유 pane 안으로 이동하고, pane 경계를 명확히 하며, 기존 격자 선택을 유지한 채 원본 비율의 한 장 크게 보기를 추가한다.

## 확정 방향

- B안인 Finder형 혼합 배치를 사용한다.
- 56pt 전역 toolbar를 제거한다.
- `위치 추가`는 왼쪽 폴더 pane header의 `+` icon button으로 이동한다.
- 이전·다음, 현재 폴더명, 검색은 중앙 사진 pane 첫 번째 header 행에 둔다.
- 중앙 pane 두 번째 header 행에는 `격자 | 한 장`, 하위 폴더 포함, 정렬, 밀도 control을 둔다.
- 오른쪽 Inspector 구조와 작업 설정은 유지한다.
- 세 pane 사이에는 전체 높이에 걸친 1px 연속 구분선을 사용한다. 점이나 부분적인 separator는 사용하지 않는다.

## 한 장 보기

중앙 header의 `격자 | 한 장` segmented control로 모드를 전환한다.

- 격자 모드는 현재 thumbnail grid, focus, 독립 checkbox 동작을 유지한다.
- thumbnail 본문을 double-click하면 해당 사진을 focus한 상태로 한 장 모드에 진입한다.
- checkbox click은 분류 대상만 변경하며 보기 모드는 바꾸지 않는다.
- 한 장 모드는 중앙 content 영역 전체를 aspect-fit 이미지 stage로 사용한다.
- 이미지 내부 왼쪽·오른쪽에 이전·다음 overlay button을 둔다.
- 우상단에는 현재 사진의 `분류 대상` checkbox를 둔다.
- 하단에는 파일명, 현재 순번/전체 결과 수, 전체 선택 수를 표시한다.
- 좌우 button과 키보드 `←`/`→`는 검색·정렬된 현재 결과 순서로 focus만 이동한다.
- `Esc` 또는 `격자` 선택 시 이전 grid scroll 위치와 focus를 보존해 돌아간다.
- 첫 사진에서는 이전, 마지막 사진에서는 다음 control을 disabled 처리한다.
- 검색 결과가 비면 한 장 모드를 비활성화하고 격자 empty state를 표시한다.

## 선택 모델

`focus`와 `분류 대상 선택`은 계속 별도 상태다.

- 사진 본문 선택 또는 한 장 보기 이동은 `_focused_path`만 변경한다.
- thumbnail checkbox와 한 장 보기 checkbox는 `_selected_paths`만 변경한다.
- 두 모드의 checkbox는 같은 상태를 즉시 반영한다.
- Inspector는 두 모드 모두 현재 `_focused_path`의 metadata를 표시한다.

## 이미지 비율

thumbnail, 한 장 보기, Inspector preview 모두 원본 종횡비를 유지한다.

- ImageIO thumbnail 생성 결과의 실제 pixel width와 height로 `NSImage` 논리 크기를 만든다.
- 정사각형 논리 크기를 강제로 지정하지 않는다.
- 모든 `NSImageView`는 `NSImageScaleProportionallyUpOrDown`을 사용한다.
- 격자 thumbnail은 cell 안에 aspect-fit으로 표시하며 남는 영역은 pane 배경으로 둔다.
- 한 장 보기와 Inspector도 crop이나 stretch 없이 aspect-fit한다.
- EXIF orientation은 기존 `kCGImageSourceCreateThumbnailWithTransform` 경로를 유지한다.

## 반응형 규칙

- window 최소 크기 `1180×700`은 유지한다.
- sidebar `240–340pt`, center 최소 `500pt`, Inspector `320–440pt` 규칙을 유지한다.
- 전역 toolbar 제거로 확보한 수직 공간은 세 pane에 돌려준다.
- center 폭이 `640pt` 미만이면 header를 두 행으로 유지하고 control 간 최소 8pt 간격을 확보한다.
- 한 장 stage의 overlay button과 checkbox는 이미지가 아니라 stage 경계를 기준으로 배치해 극단적인 가로·세로 이미지에서도 접근 가능하게 한다.
- 긴 파일명과 폴더명은 말줄임 처리하며 다른 control을 밀어내지 않는다.

## 접근성

- icon button에 `이전 폴더`, `다음 폴더`, `위치 추가`, `이전 사진`, `다음 사진` label과 tooltip을 제공한다.
- 보기 전환에는 `사진 보기 방식` label을 제공한다.
- 한 장 보기 checkbox에는 파일명을 포함한 `분류 대상으로 선택` label을 제공한다.
- disabled 탐색 control은 현재 상태를 정확히 반영한다.

## 검증 기준

자동화 검증:

- toolbar가 제거되고 control이 올바른 pane의 descendant인지 확인한다.
- split divider가 1px 연속선으로 렌더링되도록 style과 geometry를 확인한다.
- 격자/한 장 전환, focus 이동, 경계 disabled 상태, checkbox 동기화를 확인한다.
- 비정사각형 fixture를 decode했을 때 `NSImage.size()` 비율이 원본과 일치하는지 확인한다.
- 최소 center 폭과 최소 window 높이에서 header, stage, footer, Inspector가 겹치지 않는지 확인한다.

live 검증:

- exact window capture로 `1180×700`, `1280×760`, `1440×860`, `1680×900`을 확인한다.
- 각 크기에서 격자와 한 장 모드를 모두 확인한다.
- 가로·세로 이미지를 각각 열어 thumbnail, stage, Inspector가 stretch/crop되지 않는지 확인한다.
- pane 구분선, header control, overlay button, checkbox, footer가 잘리거나 겹치지 않는지 확인한다.
