# 2026-09-14 5종 Story 테마 운영 구현·배포 검증

## 결과

Story의 분석 데이터는 다시 만들지 않고 표현 방식만 전환하는 5종 presentation 계층을 운영 환경에 반영했다. Mac PhotosMcp, Android Story WebView, 소유자 브라우저는 같은 renderer와 self-hosted Swiper 14.2.0을 사용한다.

| theme ID | 표시 이름 | 본문 탐색과 시각 방향 |
|---|---|---|
| `journal` | 코발트 포스터 | 코발트·버밀리언·노랑의 비대칭 편집형 grid |
| `cinema` | 암실 시네마 | 암실색·앰버의 큰 가로 사진과 free-mode Swiper |
| `memory_book` | 팝업 플레이북 | 하늘·산호·라일락의 인물 중심 collage |
| `map_journey` | 트랜짓 아틀라스 | 네이비·오렌지의 sticky map과 장소별 Swiper |
| `film_index` | 실버 인덱스 | 은색·흑색·신호 빨강의 고밀도 contact sheet |

외부 CDN, 외부 font, 자동 재생은 추가하지 않았다. 확대·축소, 좌우 플릭, 현재 사진 indicator, 인물 필터와 Google 지도는 기존 공통 계약을 유지한다.

## 저장과 호환성

- `story_presentations`에 Story별 theme, design preset, revision, 선택 방식을 별도로 저장한다.
- 사용자 선택은 Story 재분석과 GPS·인물 projection 갱신 뒤에도 유지된다.
- 잘못된 theme ID는 allowlist에서 거절하고 안전한 기본값으로 정규화한다.
- revision 충돌은 이전 화면의 저장이 최신 선택을 덮지 않게 차단한다.
- Story 삭제 시 해당 presentation도 함께 정리한다.
- 30일 공유는 생성 시점의 presentation을 package에 복사한다. 소유자가 나중에 테마를 바꿔도 기존 공유 화면은 바뀌지 않는다.
- 공개 Story에는 테마 선택 UI가 노출되지 않는다.

## 자동 선택

사용자가 아직 선택하지 않은 Story만 사진 특성으로 초기 테마를 정한다.

1. 180장 이상: 실버 인덱스
2. 확인된 인물 정보가 있는 경우: 팝업 플레이북
3. 확인된 장소가 둘 이상인 경우: 트랜짓 아틀라스
4. 1~18장: 암실 시네마
5. 그 외와 빈 Story: 코발트 포스터

수동 선택 이후에는 자동 정책이 다시 덮지 않는다.

## 실제 화면 검증

개인 사진의 파일명·인물 이름·상세 위치를 보고서에 기록하지 않고 집계만 사용했다.

| 항목 | 결과 |
|---|---|
| 실제 운영 Story | 추천 사진 315장 |
| 자동 초기 테마 | `film_index` |
| desktop viewport | 1440×1000, 5종 구분과 정보 계층 정상 |
| mobile/WebView viewport | 390×844, 5종 반응형 정상 |
| 긴 한국어 날짜 제목 | 시작·종료 날짜를 두 줄로 분리해 가로 잘림 제거 |
| 장소·인물 요약 | 항목이 많을 때 접힘 영역으로 축약 |
| 수동 Story form | 작은 화면에서 실행 버튼이 카드 밖으로 밀리지 않음 |
| 본문 Swiper | 암실 시네마·트랜짓 아틀라스에서만 활성화 |
| 큰 사진 viewer | 모든 테마에서 동일한 Swiper zoom·fling 유지 |
| reduced motion | 전환 시간과 장식 motion 억제 |

로컬 시각 검증용 다른 포트에서는 Google Maps API의 HTTP referrer 제한에 따라 지도가 거절될 수 있다. 이는 임시 검증 origin의 제한이며 운영 `127.0.0.1:18791`과 Tailnet 경로의 키 제한을 넓히지 않았다.

## 자동 검증

```text
uvx ruff check --select E,F --ignore E501 <변경 Python 파일>
All checks passed!

python -c 'from photos_mcp.interfaces.http.story_web import STORY_JS; print(STORY_JS)' | node --check
통과

python -m pytest -q
1083 passed in 14.92s

python -m pytest -q tests/test_story_presentation.py tests/test_story_sharing.py tests/test_mobile_client.py tests/test_main.py
73 passed in 2.15s
```

## 앱 패키징과 재기동

`scripts/build_framework_standalone.sh`로 설치 번들을 재생성했다. 다음 build smoke가 모두 통과했다.

- 앱 health
- Python/runtime import
- Apple Photos source와 Vision runtime
- OpenCV YuNet/SFace 얼굴 runtime
- `codesign --verify --deep --strict`

설치 위치는 `/Users/byoungyoungla/Applications/PhotosMcp.app`이며 새 프로세스로 재기동했다. 최종 상태는 `status=ok`, `daemon_status=ready`다. Android의 별도 mobile-client LaunchAgent도 재기동해 새 Story renderer와 asset revision `v=9`를 사용하게 했다.
