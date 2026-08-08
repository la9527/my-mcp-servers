# 기초 토큰

## 글꼴

모든 AppKit 텍스트는 `app_font(size, weight)`를 사용한다. 시스템 글꼴을 사용하므로 한글 fallback과 macOS 렌더링을 유지하며, 큰 작업 화면에서 너무 작아 보이지 않도록 원본 크기에 계층별 배율을 적용한다.

| 원본 크기 | 적용 배율 |
| --- | --- |
| 24pt 이상 | 1.16 |
| 16pt 이상 | 1.20 |
| 16pt 미만 | 1.27 |

제목, 본문, 보조 정보의 상대적 차이를 유지해야 한다. 화면 전체를 같은 비율로 무조건 확대하지 않는다.

## 간격

공통 spacing scale은 다음과 같다.

| 토큰 | 값 |
| --- | --- |
| `xs` | 4pt |
| `sm` | 8pt |
| `md` | 12pt |
| `lg` | 16pt |
| `xl` | 24pt |
| `xxl` | 32pt |

관련 요소는 `sm`~`md`, 카드와 카드 사이는 `lg`~`xl`, 화면 구획은 `xl`~`xxl`을 기본으로 한다. 빈 공간이 콘텐츠보다 더 강하게 보이면 세로 간격을 먼저 줄인다.

## 아이콘

| 크기 | 값 | 사용 예 |
| --- | --- | --- |
| small | 16pt | 버튼 내부 보조 아이콘 |
| medium | 20pt | 탐색 항목, 상태 |
| large | 28pt | 주요 작업 카드 |

폴더 브라우저의 disclosure, 폴더, 검색 아이콘은 본문 글꼴과 시각적으로 균형을 맞추기 위해 각각 17pt, 20pt, 17pt를 사용한다. 아이콘만 있는 버튼은 최소 클릭 영역을 별도로 확보한다.

## 색상

- 배경: `windowBackgroundColor`
- 패널: `controlBackgroundColor` 기반 반투명 색
- 본문: `labelColor`
- 보조 텍스트: `secondaryLabelColor`
- 경계: `separatorColor` 기반 저대비 색
- 강조: `controlAccentColor`
- 상태: `systemGreen`, `systemYellow`, `systemRed`, `systemBlue`

고정 RGB보다 semantic color를 사용해 macOS appearance 변화에 대응한다.

## 창 기준

| 화면 | 기본 크기 | 최소 크기 |
| --- | --- | --- |
| 메인 앱 | 1180 × 780 | 화면 구현의 제한에 따름 |
| 결과 갤러리 | 1320 × 820 | 1100 × 680 |
| 사진 뷰어 | 1180 × 820 | 화면 구현의 제한에 따름 |

최소 크기 아래에서 요소를 억지로 압축하지 않는다. 필요한 경우 scroll view를 제공한다.
