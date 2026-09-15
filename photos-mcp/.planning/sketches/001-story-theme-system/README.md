---
sketch: 001
name: story-theme-system
question: "같은 PhotosMcp Story를 5개의 실제로 다른 감상 경험으로 보여줄 때 어떤 조합이 가장 유용한가?"
winner: null
tags: [story, themes, swiper, editorial, map, family]
---

# Sketch 001: Story Theme System

## Design Question

현재 Story의 장점은 유지하면서, 사진 수·인물·GPS·대표 장면에 맞춰 선택할 수 있는 5개 테마를 어떻게 구분할 것인가?

## How to View

저장소 루트에서 다음 명령을 실행한 뒤 브라우저로 엽니다.

```bash
python3 -m http.server 8765
```

`http://127.0.0.1:8765/.planning/sketches/001-story-theme-system/index.html`

시안은 저장소의 `.runtime` 미리보기 파생본을 로컬에서만 읽습니다. 원본 업로드나 외부 전송은 없습니다.

## Variants

- **A: 기록지** - 현재 스타일을 발전시킨 세로형 편집 사진 에세이
- **B: 시네마** - 장별 대표 사진과 짧은 문장을 한 화면씩 넘기는 Swiper 경험
- **C: 필름 인덱스** - 많은 사진을 빠르게 훑고 선택한 사진을 크게 보는 contact sheet
- **D: 지도 여행기** - 고정 지도와 장소별 사진 chapter를 함께 읽는 여행 Story
- **E: 가족 메모리북** - 사람·날짜·짧은 기억 문장을 책의 spread처럼 조합한 가족 앨범

## What to Look For

- 테마 이름만 보고도 감상 방식이 예측되는가
- 5개가 단순 색상 변형이 아니라 서로 다른 목적을 담당하는가
- 사진이 적을 때와 1,000장일 때 모두 적합한 선택지가 있는가
- 위치나 인물 데이터가 부족해도 화면이 어색하게 비지 않는가
- PC와 Android에서 같은 Story 순서로 자연스럽게 축소되는가
- Swiper 동작이 사진 감상을 돕고 세로 스크롤·확대 제스처와 충돌하지 않는가

## Recommended Baseline

기존 Story는 **기록지**로 유지하고, 시스템은 데이터 적합도에 따라 추천 배지만 표시한다. 사용자가 선택한 테마는 자동 추천보다 우선하며, 테마 변경은 사진·LLM·GPS·인물 재분석을 실행하지 않는다.
