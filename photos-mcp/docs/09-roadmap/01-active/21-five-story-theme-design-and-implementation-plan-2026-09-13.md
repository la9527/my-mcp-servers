# PhotosMcp 5종 Story 테마 설계 및 구현 계획

작성일: 2026-09-13
상태: 운영 구현, 설치 앱 배포 및 실화면 검증 완료
대상: Mac PhotosMcp, Android PhotosMcp, 소유자 브라우저, 30일 가족 공유 Story

## 1. 결론

현재 Story의 기능 계약은 유지하되 시각 언어는 `코발트 포스터`, `암실 시네마`, `팝업 플레이북`, `트랜짓 아틀라스`, `실버 인덱스`의 5종으로 확장한다. 다섯 테마는 색상만 바꾸는 skin이 아니라 사진 밀도, 텍스트 위치, 페이지 리듬, 지도와 Swiper의 역할이 서로 다른 presentation renderer다.

핵심 원칙은 다음과 같다.

1. 사진 추천, Vision 분석, GPS, 인물, LLM Story는 한 번만 만든다.
2. 테마 변경은 분석을 다시 하지 않고 즉시 렌더링한다.
3. presentation이 없는 기존 Story에는 사진 특성에 맞는 초기 테마를 자동 선택하고, 알 수 없는 값은 `journal`로 안전하게 표시한다.
4. 시스템 자동 선택은 첫 표시에서만 적용하며 수동 선택을 다시 덮지 않는다.
5. 사용자가 고른 테마는 자동 추천보다 항상 우선한다.
6. Mac, Android WebView, 브라우저가 같은 서버 렌더러를 사용한다.
7. 가족 공유는 링크 생성 시점의 테마를 스냅샷으로 고정한다.
8. 확대·축소와 전체화면 이동은 모든 테마가 현재의 공통 Swiper 뷰어를 사용한다.
9. 외부 CDN과 외부 font를 추가하지 않는다. 기존 CSP와 self-hosted Swiper 14.2.0을 유지한다.

## 2. 조사 방식과 참고 사례

서로 겹치지 않는 세 관점의 에이전트가 편집형 Story, Swiper 중심 감상, 현 코드 아키텍처를 각각 조사했다. 메인 검토에서는 인터넷의 실제 제품·공식 기술 문서와 현재 `story_web.py`, Story 저장소, 공유 스냅샷, Android WebView 흐름을 대조했다.

| 참고 | 채택할 원리 | PhotosMcp 적용 |
|---|---|---|
| [Swiper 공식 Demos](https://swiperjs.com/demos) | slide, fade, thumbs, free mode, zoom, virtual을 목적에 따라 분리 | 한 가지 강한 3D 효과가 아니라 테마별 탐색 역할에 맞는 preset 사용 |
| [Swiper Element](https://swiperjs.com/element) | lazy image, virtual slide, thumbs, 모듈별 CSS 제공 | 1,000장 뷰어의 DOM windowing 및 self-hosted asset 유지 |
| [W3C Carousel Pattern](https://www.w3.org/WAI/ARIA/apg/patterns/carousel/) | 이전·다음, 현재 slide 선택, 자동 회전 제어, 키보드 focus | 자동 재생 기본 해제, 위치 안내와 버튼, keyboard/a11y 공통화 |
| [Exposure](https://exposure.co/) | 사진, 긴 글, 지도, 공유를 하나의 visual story에 결합 | 기록지와 지도 여행기에서 chapter 문장과 미디어를 교차 배치 |
| [Mapbox Storytelling](https://github.com/mapbox/storytelling) | story chapter마다 지도 위치를 연결하는 scrollytelling | 고정 지도와 위치별 chapter를 연결하되 기존 Google Maps 경계를 유지 |
| [Pixieset Gallery](https://pixieset.com/example/) | 실제 대표 사진과 collection/set 단위 탐색 | 테마 선택 카드에 현재 Story의 대표 파생 이미지를 사용 |
| [Apple의 포용적 Photos Memories 설계](https://developer.apple.com/videos/play/wwdc2021/10304/) | 사진은 개인의 삶과 정체성을 반영하며 다양한 사람·장소·경험을 존중 | 가족 메모리북 추천은 가능하지만 사용자가 원하지 않는 사람을 강제 노출하지 않음 |
| [Apple PhotoKit 앨범 탐색 예제](https://developer.apple.com/documentation/PhotoKit/browsing-and-modifying-photo-albums) | 대량 사진은 collection과 thumbnail grid로 먼저 탐색 | 필름 인덱스에서 빠른 전체 탐색과 큰 사진 감상을 분리 |
| [Google Arts & Culture 사진 에세이](https://artsandculture.google.com/story/twWRzBM63VVaJw?hl=en) | 사진 순서와 충분한 해설로 장면의 의미를 만든다 | 기록지에서 대표 장면과 문장 중심의 리듬 사용 |
| [Artifact Uprising Photo Book Layout Ideas](https://www.artifactuprising.com/diy/photo-book-layout-ideas) | full bleed, 2-up, 4-up, portrait sequence 조합 | 가족 메모리북은 무작위 collage가 아니라 제한된 spread 조합 사용 |

## 3. 다섯 테마

### 3.1 코발트 포스터 `journal`

현재 Story의 장점을 보존한 기본 편집형 사진 에세이다.

- 탐색: 세로 스크롤, 사진 선택 시 공통 전체화면 Swiper
- 구성: cover, 날짜·장소·인물 요약, chapter 제목과 문장, full-bleed/2-up/3-up 사진 리듬
- 적합: 8-80장, 일반적인 나들이·여행·일상 Story
- 데이터가 부족할 때: 현재처럼 사진과 날짜만으로도 자연스럽게 표시
- 난이도: 2/5
- 기본값: 기존 Story와 알 수 없는 테마 ID의 fallback

### 3.2 암실 시네마 `cinema`

대표 장면 한 장과 짧은 문장을 한 화면씩 넘기는 몰입형 테마다.

- 탐색: 메인 Swiper `slide`, 장 사이에만 선택적 짧은 `fade`
- 구성: 사진은 배경 색조 위에 원본 비율을 보존하고, 제목과 caption은 별도 HTML로 표시
- 적합: 대표 점수가 높은 사진 3-30장
- 정책: 자동 재생하지 않음, 1,000장을 메인 slide로 만들지 않음
- 난이도: 3/5
- 접근성: 이전·다음, 현재 위치, keyboard, reduced motion에서 전환 0ms

### 3.3 실버 인덱스 `film_index`

많은 사진을 contact sheet처럼 빠르게 훑고 선택한 사진을 크게 감상하는 테마다.

- 탐색: 날짜·chapter별 grid 또는 `freeMode` strip, 선택 상세, 공통 전체화면 Swiper
- 구성: 장면 연속성을 보이는 thumbnail, 추천 여부, 날짜·장소·인물·용량 metadata
- 적합: 50-1,000장, 추천과 대안 사진을 함께 볼 때
- 정책: 하나의 1,000장 thumbnail Swiper 대신 chapter당 50-100장으로 분할
- 난이도: 3/5
- 장점: 기존 분석 완료 화면과 다른 목적을 가지며 Story 안에서 전체 흐름을 빠르게 탐색 가능

### 3.4 트랜짓 아틀라스 `map_journey`

고정 지도와 위치별 사진 chapter를 함께 읽는 여행형 Story다.

- 탐색: desktop은 sticky map + 세로 chapter, mobile은 지도 요약 + 장소별 소형 Swiper
- 구성: GPS/POI 장소, 지도 pin, 장소별 대표 사진, 이동 순서, 장소 문장
- 적합: GPS 확인 사진과 서로 다른 장소가 충분한 여행
- 데이터가 부족할 때: 선택을 막지 않고 지도 없는 chapter는 일반 장소 카드로 축소
- 난이도: 4/5
- 보안: 기존 Google Maps embed allowlist와 key 제한을 그대로 사용

### 3.5 팝업 플레이북 `memory_book`

사람, 날짜, 짧은 기억 문장을 정돈된 사진책 spread로 보여준다.

- 탐색: 책처럼 이어지는 세로 spread, 사진 묶음 또는 전체화면 Swiper
- 구성: cover, 함께한 사람, 1 large + 2 small, 연속 표정 3장, 4-square, 짧은 note
- 적합: 확인된 인물이 반복해서 등장하는 15-120장
- 데이터가 부족할 때: 모르는 인물을 억지로 표시하지 않고 날짜·장면 중심 spread로 전환
- 난이도: 3/5
- 모바일: 회전과 겹침을 제거하고 읽기 순서가 명확한 단일 column으로 축소

## 4. 테마 선택 화면

테마 선택은 설정 깊숙한 곳이 아니라 각 Story의 `테마 바꾸기`에서 진입한다. Mac에서는 Story 포털 안의 sheet 또는 modal, Android에서는 native bottom sheet가 적합하다.

```text
Story 보기
  └─ 테마 바꾸기
       ├─ 현재 Story 대표 사진으로 만든 5개 preview card
       ├─ 현재 사용 중 표시
       ├─ 이 Story에 추천 표시
       ├─ 데이터 적합도 설명
       │    예: 사진 97장에 적합
       │    예: GPS 사진이 적어 지도 정보가 간단하게 표시됨
       ├─ 미리보기
       └─ 적용
            ├─ Story presentation만 저장
            └─ 현재 Story reload, 재분석 없음
```

카드에는 추상적인 색상 견본이 아니라 현재 Story의 대표 사진을 사용한다. 한 카드 안에는 다음 네 정보만 둔다.

- 테마 이름
- 실제 사진 미리보기
- 탐색 방식
- 추천 또는 데이터 적합도 한 문장

`미리보기`와 `적용`의 역할을 분리한다. 미리보기는 저장하지 않고, 적용은 optimistic revision을 검증해 저장한다.

## 5. 추천 정책

추천은 사용자의 결정을 돕는 배지이며 자동 적용 규칙이 아니다.

```text
1-12장                          -> 시네마 추천
13-80장 + 확인 인물 비율 높음    -> 가족 메모리북 추천
13-80장 + GPS/장소 비율 높음      -> 지도 여행기 추천
13-80장 + 그 외                  -> 기록지 추천
81장 이상                        -> 필름 인덱스 추천
```

보조 신호:

- 가로형 대표 사진이 많고 평균 품질이 높으면 시네마 가점
- chapter 수와 문장 완성도가 높으면 기록지 가점
- 동일 인물이 3회 이상 확인된 사진 비율이 높으면 가족 메모리북 가점
- confirmed GPS와 명백한 POI가 두 곳 이상이면 지도 여행기 가점
- 사진 수가 많고 같은 장면 대안이 많으면 필름 인덱스 가점

추천 사유와 적합도는 소유자에게만 표시하고 공개 공유 패키지에는 넣지 않는다.

## 6. 코드 구조 판단

현재 `src/photos_mcp/interfaces/http/story_web.py`의 `render_story()`가 chapter, 위치, 사람 filter, grid, 지도, viewer를 한 함수에서 렌더링한다. 여기에 5개 조건문을 계속 추가하면 기능 수정 때 모든 테마를 동시에 깨뜨릴 수 있다. 아래처럼 view model, theme renderer, 공통 viewer를 분리한다.

```text
Story manifest + StoryPresentation
                |
                v
build_story_view_model()
  - owner/public asset URL 정규화
  - 공개 정보 제거
  - chapter/location/people/photo 공통 모델
                |
                v
ThemeRenderer registry
  ├─ JournalRenderer
  ├─ CinemaRenderer
  ├─ FilmIndexRenderer
  ├─ MapJourneyRenderer
  └─ MemoryBookRenderer
                |
                v
공통 asset / map / people filter / fullscreen Swiper / legal footer
```

권장 파일:

- `src/photos_mcp/application/story_presentation.py`
- `src/photos_mcp/interfaces/http/story_view_model.py`
- `src/photos_mcp/interfaces/http/story_themes/__init__.py`
- `src/photos_mcp/interfaces/http/story_themes/journal.py`
- `src/photos_mcp/interfaces/http/story_themes/cinema.py`
- `src/photos_mcp/interfaces/http/story_themes/film_index.py`
- `src/photos_mcp/interfaces/http/story_themes/map_journey.py`
- `src/photos_mcp/interfaces/http/story_themes/memory_book.py`
- `resources/web/story-themes-v1/base.css`
- `resources/web/story-themes-v1/*.css`

### 6.1 기존 `theme` 필드는 유지

Story manifest에는 이미 `theme`가 존재하며 `day_in_life`, `weekend_journal`, `seasonal_digest`, `mixed_archive` 같은 LLM 이야기 유형을 의미한다. 이 필드를 시각 테마에 재사용하면 안 된다.

시각 선택은 별도의 presentation으로 정의한다.

```json
{
  "story_id": "daily-recommendations",
  "theme": "weekend_journal",
  "presentation": {
    "schema_version": 1,
    "theme_id": "cinema",
    "presentation_revision": 3,
    "selection_mode": "manual"
  }
}
```

### 6.2 별도 저장소

Story manifest 안에 presentation만 저장하면 재분석이나 GPS/인물 재투영에서 덮일 위험이 있다. 다음 별도 table이 안전하다.

```sql
CREATE TABLE story_presentations (
    story_id TEXT PRIMARY KEY,
    theme_id TEXT NOT NULL,
    presentation_revision INTEGER NOT NULL DEFAULT 1,
    selection_mode TEXT NOT NULL DEFAULT 'manual',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

이 저장소는 Story revision과 독립적이어서 재분석 후에도 사용자 선택을 유지한다. Story 삭제 시에만 함께 정리한다.

### 6.3 color mode와 visual theme 분리

Android WebView는 현재 `document.documentElement.dataset.theme = 'dark' | 'light'`를 사용한다. 새 시각 테마는 반드시 다른 속성을 사용한다.

```html
<html data-theme="dark" data-story-theme="memory_book">
```

- `data-theme`: light/dark color scheme
- `data-story-theme`: Story layout/presentation

## 7. API와 공유

### Mac/브라우저

```http
POST /photos/stories/{story_id}/presentation
Content-Type: application/x-www-form-urlencoded

theme_id=cinema
presentation_revision=2
```

기존 Tailscale owner 검사, same-origin 검사, 허용 theme ID allowlist를 적용한다.

### Android

```http
POST /mobile-client/story/presentation
Cookie: photos_mobile_story=<일회용 WebView session>
Content-Type: application/x-www-form-urlencoded

theme_id=memory_book&presentation_revision=2
```

Android는 bearer token을 JavaScript에 노출하지 않고 기존 Tailnet WebView session cookie를 사용한다. 저장 성공 후 APK에 HTML/CSS를 내려받지 않고 Story WebView만 redirect·reload한다.

### 30일 가족 공유

- 공유 생성 시 `theme_id`와 `presentation_revision`을 package JSON에 복사한다.
- 소유자가 이후 테마를 변경해도 기존 공유는 갑자기 바뀌지 않는다.
- 새 테마를 공유하려면 새 공유 링크를 생성한다.
- 기존 공유 package에 presentation이 없으면 `journal`로 표시한다.
- 공개 package에는 추천 점수와 내부 자동 선택 근거를 넣지 않는다.

## 8. Swiper 사용 규칙

| 테마 | 본문에서 Swiper | 공통 전체화면 viewer |
|---|---|---|
| 기록지 | 사용하지 않음 | 사용 |
| 시네마 | 대표 장면 3-30장 | 사용 |
| 필름 인덱스 | chapter별 freeMode strip 또는 thumb 연결 | 사용 |
| 지도 여행기 | mobile의 장소별 사진 묶음 | 사용 |
| 가족 메모리북 | 연속 표정 또는 작은 사진 묶음에만 선택 사용 | 사용 |

다음 효과는 기본 테마에서 제외한다.

- 강한 cube/flip: 사진을 3D 면으로 왜곡함
- 강한 coverflow: 주변 사진과 인물 비율을 왜곡함
- 과한 parallax/Ken Burns: 구도와 얼굴 위치가 계속 움직임
- 자동 재생: 사진 문장을 읽는 속도를 강제함

확대 배율이 1배보다 클 때는 손가락 이동을 사진 pan에만 사용하고 좌우 slide 이동은 잠근다. 다음 사진으로 넘어가면 확대를 1배로 초기화한다.

## 9. 1,000장 성능 정책

현재 viewer는 이미지 `src`를 현재 사진 주변에만 부여하지만 사진 수만큼 slide DOM을 만든다. 1,000장에서는 DOM 비용이 남는다.

- 전체화면 viewer는 Swiper Virtual 또는 현재 위치 기준 windowed DOM으로 전환
- 메인 시네마는 chapter 대표 장면 최대 30장만 사용
- 필름 인덱스는 날짜/chapter별 50-100장으로 분리
- hero 한 장만 eager load, 나머지는 lazy load
- thumbnail과 전체화면 preview 파생 크기를 분리
- viewer 종료 시 Swiper instance, slide DOM, image URL을 완전히 해제
- 1,000장 fixture에서 최초 요청이 모든 preview를 불러오지 않는지 검증

## 10. 구현 순서

### 단계 1. 표현 도메인과 호환 기반

1. `StoryPresentation` model과 5개 theme registry 정의
2. `story_presentations` table과 repository 추가
3. presentation이 없는 Story와 알 수 없는 값은 `journal` fallback
4. Story 재분석·GPS 갱신·인물 갱신 후에도 presentation 유지

### 단계 2. 공통 렌더러 분리

1. `build_story_view_model()` 도입
2. 사진 URL, 위치 그룹, 인물 filter, 공개 projection을 한 번만 정규화
3. 공통 fullscreen Swiper와 map, footer를 component 함수로 분리
4. light/dark와 story theme data attribute 분리

### 단계 3. 첫 수직 구현

1. `journal`로 현재 UI를 동일하게 재현
2. `cinema` 구현
3. `film_index` 구현
4. 1,000장 virtual/windowed viewer 적용

### 단계 4. 데이터 중심 테마

1. `map_journey`와 GPS 부족 fallback 구현
2. `memory_book`과 인물 부족 fallback 구현
3. image `width`, `height`, `aspect_class`를 view model에 추가해 CLS 방지

### 단계 5. 선택 UX

1. Mac Story 포털에 실제 Story 사진 기반 5개 preview card 추가
2. 저장하지 않는 전체 미리보기 제공
3. Android Story 목록에 native theme bottom sheet 추가
4. 적용 성공 후 WebView reload와 현재 테마 표시

### 단계 6. 공유·패키징·검증

1. 공유 package에 presentation snapshot 추가
2. theme CSS/JS allowlist와 app packaging 반영
3. CSP에 외부 CDN 의존성이 없는지 검증
4. Mac WKWebView, Android WebView, localhost, Tailscale, 30일 공유 통합 테스트

## 11. 테스트 기준

### 자동 테스트

- 5개 허용 theme 저장, 알 수 없는 theme 거절
- optimistic revision 충돌 409
- Story 재분석, GPS/인물 갱신 뒤 theme 유지
- Story 삭제 시 presentation 정리
- 5개 theme가 동일한 사진·chapter를 누락 없이 렌더링
- public Story에 로컬 path, local asset ID, private person ID가 노출되지 않음
- 공유 생성 뒤 원본 Story theme를 바꿔도 공유 theme 유지
- Android signed command의 replay, nonce, idempotency 검증
- static theme asset path traversal 거절

### 화면·동작 테스트

- viewport: 375×812, 768×1024, 1440×900
- fixture: 1장, 5장, 30장, 100장, 1,000장
- 조건: GPS 없음, 인물 없음, 긴 한국어 제목, 가로/세로 혼합, offline map
- 동작: swipe, pinch zoom, double tap, keyboard, Esc, Android back
- 확대 상태에서 의도하지 않은 다음 사진 이동이 없음
- reduced motion에서 3D/parallax와 전환 motion 제거
- viewer를 30회 열고 닫아 DOM과 memory가 누적되지 않음

## 12. 디자인 검토 시안

클릭 가능한 5종 시안을 다음 위치에 작성했다.

- `.planning/sketches/001-story-theme-system/index.html`
- `.planning/sketches/001-story-theme-system/README.md`
- `.planning/sketches/MANIFEST.md`

시안은 실제 저장소의 local preview 사진을 사용하며 원본을 외부로 보내지 않는다. 상단의 다섯 테마를 선택해 레이아웃을 비교하고, 우측 하단에서 PC·태블릿·모바일 폭을 바꿀 수 있다. 시네마는 self-hosted Swiper 14.2.0으로 실제 이동한다.

## 13. 승인 후 확정할 항목

구현 전 사용자 확인은 한 가지면 충분하다.

> 다섯 테마 조합을 `기록지`, `시네마`, `필름 인덱스`, `지도 여행기`, `가족 메모리북`으로 확정할 것인가?

권장 답은 그대로 확정이다. `기록지`를 기존 호환 기본값으로 두고, 첫 구현은 `기록지 + 시네마 + 필름 인덱스`, 두 번째 구현은 `지도 여행기 + 가족 메모리북` 순서로 진행한다.

## 14. 디자인 재검토 2안: 공통 녹색 제거

2026-09-14 사용자 검토에서 첫 시안이 일반적이고 녹색 중심이라는 의견이 있었다. 기능 분류는 유효하지만, 시각적으로는 테마들이 같은 브랜드 skin의 변형처럼 느껴질 수 있다. 따라서 renderer의 기능 역할은 유지하고 art direction을 다음처럼 전면 교체한다.

| 새 테마 | 대체하는 역할 | 색 체계 | 핵심 문법 |
|---|---|---|---|
| 코발트 포스터 `poster` | 기록지 | 코발트 `#153CFF`, 버밀리언 `#FF3E2F`, 노랑 `#FFD325` | 비대칭 대형 타이포그래피와 사진 절단면 |
| 암실 시네마 `darkroom` | 시네마 | 암실색 `#0B0710`, 앰버 `#FFAD0A`, 자주색 `#2A1832` | 사진 투사 공간과 한 장씩 넘기는 Swiper |
| 팝업 플레이북 `playbook` | 가족 메모리북 | 하늘 `#B9D8FF`, 산호 `#FF6B55`, 라일락 `#CDB8FF`, 노랑 `#FFE34D` | 종이 오브젝트, 표정 중심 가족 collage |
| 트랜짓 아틀라스 `transit` | 지도 여행기 | 네이비 `#0D1638`, 코발트 `#2747FF`, 오렌지 `#FF6A2A` | 이동 경로를 교통 노선처럼 보여주는 sticky map |
| 실버 인덱스 `silver` | 필름 인덱스 | 은색 `#D9DDE3`, 흑색 `#121317`, 신호 빨강 `#F03B3B` | 사진가 contact sheet와 선택 상세 작업대 |

### 14.1 외부 디자인 스킬 검토

`skills.sh`와 GitHub에서 다음 후보를 확인했다.

- `bytedance/deer-flow@frontend-design`: 3.4K 설치, ByteDance 저장소 기반이다. 강한 시각 방향 설정은 참고할 가치가 있지만 생성물에 Deerflow 외부 브랜드 표기를 강제하므로 PhotosMcp에는 설치·적용하지 않는다.
- `mengto/skills@cinematic-scroll-storytelling`: 1.1K 설치. scroll staging, sticky scene, scrub과 reduced motion 원칙을 암실 시네마와 트랜짓 아틀라스 설계에 반영한다. 실제 운영에서는 현재 self-hosted Swiper를 우선하고 GSAP/Lenis 의존성은 추가하지 않는다.
- `bergside/awesome-design-skills@editorial`: 1.4K 설치. 접근성·token 규칙은 유효하지만 결과가 첫 시안의 일반 잡지형과 겹쳐 시각 방향으로는 채택하지 않는다.
- `dylantarre/animation-principles@creative-director`: staging, timing, spatial coherence를 테마 전환과 Swiper motion 검토 기준으로 사용한다. 설치 수와 저장소 규모가 작으므로 의존성으로 추가하지 않는다.

외부 skill은 공급망 의존성으로 설치하지 않고 공개된 설계 원칙만 검증 자료로 사용한다. 운영 구현은 현재 저장소의 local CSS, JavaScript, self-hosted Swiper만으로 가능해야 한다.

### 14.2 인터넷 사례에서 채택한 부분

- Memento의 물리적 전시 같은 full-bleed, 비대칭 구성, 충분한 여백은 코발트 포스터에 반영한다.
- Shorthand의 reader-controlled scrollytelling과 progressive reveal은 트랜짓 아틀라스의 장소 진행에 반영한다.
- Vev 사례의 horizontal storytelling과 user-triggered interaction은 암실 시네마와 장소별 Swiper에 제한적으로 반영한다.
- immersive photo essay의 핵심인 방해 요소 제거와 독자 주도 속도는 모든 테마의 자동 재생 금지 원칙으로 유지한다.

### 14.3 구현 ID와 표시 이름 분리

첫 계획의 기능적 renderer ID를 그대로 데이터베이스에 저장하면 시각 방향 변경 때 migration이 필요해진다. 영속 ID는 다음처럼 중립적인 역할 이름으로 두고, 표시 이름과 token preset을 revision으로 관리한다.

```json
{
  "theme_id": "cinema",
  "design_preset": "darkroom-v1",
  "display_name": "암실 시네마"
}
```

- `journal` + `poster-v1` = 코발트 포스터
- `cinema` + `darkroom-v1` = 암실 시네마
- `memory_book` + `playbook-v1` = 팝업 플레이북
- `map_journey` + `transit-v1` = 트랜짓 아틀라스
- `film_index` + `silver-v1` = 실버 인덱스

이 구조는 Story의 기능 적합도와 사용자의 시각적 취향을 분리한다. 향후 같은 `cinema` renderer에 다른 color/token preset을 추가해도 분석 데이터와 공유 구조를 바꾸지 않는다.

### 14.4 새 검토 시안

- `.planning/sketches/002-story-theme-lab/index.html`
- `.planning/sketches/002-story-theme-lab/README.md`

두 번째 시안을 우선 검토 대상으로 사용한다. 첫 시안은 기능 비교와 회귀 기준으로 보존한다.

## 15. 2026-09-14 운영 구현 결과

사용자 승인 뒤 두 번째 시안의 다섯 시각 언어를 운영 Story 렌더러에 연결했다.

### 15.1 반영 범위

- Story 의미 분류인 기존 `theme`와 시각 표현인 `presentation`을 분리했다.
- SQLite `story_presentations`에 `theme_id`, `design_preset`, revision, 선택 방식을 저장한다.
- 저장 값은 allowlist로 정규화하고 알 수 없는 ID는 안전한 기본 테마로 내린다.
- 사용자 선택은 Story 재분석과 위치·인물 projection 갱신에 영향을 받지 않는다.
- Mac Story 화면과 Android Story WebView에서 동일한 다섯 테마 선택기를 제공한다.
- `cinema`와 `map_journey` 본문 사진 묶음은 self-hosted Swiper를 사용한다. 자동 재생은 사용하지 않는다.
- 모든 테마가 기존 확대·축소·좌우 넘김 dialog와 인물 필터, 지도, 다운로드 정책을 공유한다.
- 공유 링크 생성 시 당시 presentation을 immutable package에 snapshot한다. 이후 소유자 테마 변경은 기존 공유에 영향을 주지 않는다.
- Story 삭제 시 분리 저장된 presentation도 정리한다.
- CSS와 JavaScript asset revision을 `v=9`로 올려 설치 앱과 WebView의 오래된 cache를 피한다.

### 15.2 자동 기본 선택

수동 선택이 없을 때만 다음 순서로 첫 표현을 고른다.

1. 180장 이상은 `film_index`
2. 확인된 인물 정보가 있으면 `memory_book`
3. 확인된 장소가 둘 이상이면 `map_journey`
4. 1-18장은 `cinema`
5. 그 외와 빈 Story는 `journal`

한 번 수동 선택한 뒤에는 이 규칙을 다시 적용하지 않는다.

### 15.3 검증 기준과 결과

- 5종 registry와 preset 고유성
- 잘못된 입력의 allowlist fallback
- optimistic revision 충돌 방지
- Mac POST 저장과 재렌더링
- Android WebView session 기반 POST 저장과 재렌더링
- 공유 package snapshot 불변성
- public Story에서 선택 UI 미노출
- 기존 viewer, 인물 privacy, 30일 공유·다운로드 회귀

검증 명령은 다음과 같다.

```bash
.venv/bin/python -m pytest -q \
  tests/test_story_presentation.py \
  tests/test_story_sharing.py \
  tests/test_mobile_client.py \
  tests/test_main.py
```

### 15.4 최종 운영 검증

- 전체 회귀: `1083 passed in 14.92s`
- 테마·공유·Android WebView·Mac HTTP 집중 회귀: `73 passed in 2.15s`
- Ruff `E/F` 검사와 Story JavaScript `node --check`: 통과
- 실제 운영 Story 315장으로 5종 desktop·mobile 렌더링 확인
- 390px 폭에서 긴 날짜 제목을 시작·종료 구간 두 줄로 나눠 잘림 제거
- 390px 폭의 수동 Story 실행 영역을 전체 폭 한 줄 동작으로 정리
- standalone build의 health, runtime import, photo source, Vision, 얼굴 runtime smoke: 통과
- 설치 앱 코드 서명: `codesign --verify --deep --strict` 통과
- 설치 앱 재기동: `/health`의 `status=ok`, `daemon_status=ready`
- Android WebView 서버 재기동: Tailnet 세션 경계와 동일 서버 렌더러 유지

최종 검증 기록은 `docs/08-reports/01-validation/55-five-story-theme-runtime-validation-2026-09-14.md`에 남긴다.
