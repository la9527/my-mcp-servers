# Story Theme Lab 2

기존 녹색 편집형 시안에서 벗어나기 위한 두 번째 인터랙티브 디자인 검토안이다. 운영 Story renderer에는 연결하지 않은 로컬 시안이다.

## 테마

- `poster`: 코발트 포스터. 코발트, 버밀리언, 노랑과 비대칭 대형 타이포그래피를 사용한다.
- `darkroom`: 암실 시네마. 자주색이 도는 암실 배경과 앰버 조명, 한 장씩 넘기는 Swiper를 사용한다.
- `playbook`: 팝업 플레이북. 하늘색, 산호색, 라일락, 노랑의 종이 오브젝트와 가족 콜라주를 사용한다.
- `transit`: 트랜짓 아틀라스. 네이비, 코발트, 오렌지 노선도와 장소별 Swiper를 사용한다.
- `silver`: 실버 인덱스. 차가운 은색 작업대와 빨간 선택 표시로 최대 1,000장을 탐색한다.

## 실행

저장소 루트에서 `python3 -m http.server 8765`를 실행하고 다음 주소를 연다.

```text
http://127.0.0.1:8765/.planning/sketches/002-story-theme-lab/index.html
```

`?theme=poster`, `darkroom`, `playbook`, `transit`, `silver` query parameter로 각 테마를 바로 열 수 있다.

## 검토 포인트

- 다섯 테마가 동일한 공통 녹색 accent를 사용하지 않는다.
- 색만 다른 skin이 아니라 hero, 사진 밀도, 스크롤 리듬, Swiper의 역할이 다르다.
- 외부 font, CDN, analytics를 사용하지 않는다.
- 사진은 `.runtime`의 local preview 파생 이미지만 사용한다.
- 확대 viewer는 디자인 비교용이다. 운영 적용 시 기존 공통 pinch-zoom Swiper viewer를 사용한다.
