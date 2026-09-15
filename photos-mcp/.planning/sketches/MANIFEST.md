# PhotosMcp UI Sketch Manifest

## Design Direction

PhotosMcp Story를 한 가지 고정 템플릿이 아니라, 같은 분석 데이터로 서로 다른 감상 경험을 선택하는 개인·가족 사진 출판 도구로 확장한다. 현재 차분한 편집형 스타일은 보존하면서 시네마, 필름 인덱스, 지도 여행기, 가족 메모리북을 추가한다. 사진·인물·장소·문장은 동일하게 유지하고 프레젠테이션만 바뀌며, 모든 테마는 Mac, Android WebView, Tailscale 공유 페이지에서 동일한 의미 구조와 공통 전체화면 Swiper 뷰어를 사용한다.

Design read: 개인·가족용 사진 Story이며, 차분한 편집 디자인을 기반으로 테마마다 레이아웃과 탐색법이 명확히 달라지는 방향이다.

- `DESIGN_VARIANCE`: 8/10 - 다섯 테마가 색상 변형이 아닌 서로 다른 출판 형식이어야 한다.
- `MOTION_INTENSITY`: 6/10 - 시네마와 필름 인덱스에만 의미 있는 Swiper 동작을 사용한다.
- `VISUAL_DENSITY`: 4/10 - 읽기와 사진 감상을 우선하되 1,000장용 고밀도 테마를 별도로 제공한다.

## Reference Points

- Swiper 공식 Demos/API: slide, fade, thumbs, free mode, zoom, virtual
- Exposure: 사진, 글, 지도 결합형 visual story
- Mapbox Storytelling: 위치별 chapter와 지도 카메라를 연결하는 scrollytelling
- Google Arts & Culture: 번호와 해설이 있는 편집형 사진 에세이
- Apple Photos Memories / Journal: 사람·장소·날짜를 moment로 묶는 개인 기록
- Pixieset: 대표 이미지와 collection/set 기반 client gallery
- W3C WAI carousel pattern: 사용자 제어, 이전·다음, 현재 위치, 자동 재생 제한

## Sketches

| # | Name | Design Question | Winner | Tags |
|---|---|---|---|---|
| 001 | Story Theme System | 같은 Story를 5개의 실제로 다른 감상 경험으로 보여줄 때 어떤 조합이 가장 유용한가? | null | story, themes, swiper, editorial, map, family |
| 002 | Story Theme Lab | 공통 녹색과 일반적인 포토북 문법을 버리고도 가족 사진을 편안하게 감상할 수 있는가? | null | cobalt, darkroom, pop-up, transit, silver, swiper |
