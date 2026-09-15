# 2026-09-13 Story Swiper 통합·설정 검증

## 결과

Story 큰 사진 뷰어의 직접 구현한 pointer 제스처를 Swiper 14.2.0으로 교체했다. 로컬 Mac 브라우저, Tailscale HTTPS, Android WebView용 owner story, 30일 공유 Story가 같은 뷰어와 고정 버전 자산을 사용한다.

사진은 `1×`에서 항상 현재 stage 안에 원본 종횡비로 맞춰 보이고, 기본 배율에서는 Swiper의 저항감과 transition을 사용하는 좌우 플릭으로 이동한다. 확대 중에는 Swiper Zoom이 pinch·double tap·pan을 처리한다. 별도 `+`·`-`·이전·다음 버튼은 추가하지 않았으며, 하단의 `현재 / 전체` 숫자와 진행 막대를 유지했다.

## 의존성과 배포 방식

- 버전: Swiper 14.2.0
- 공급 경로: npm의 `swiper@14.2.0` 배포 패키지
- 라이선스: MIT, `resources/web/swiper-14.2.0/LICENSE` 포함
- 브라우저 제공: CDN이 아니라 PhotosMcp 자체 origin의 `story-assets` 경로
- 앱 번들: `Contents/Resources/story-assets`에 CSS, JS, LICENSE를 함께 포함
- 캐시: 버전 query와 `Cache-Control: public, max-age=31536000, immutable`
- CSP: 기존 `script-src 'self'`, `style-src 'self'` 유지. 외부 script/style 허용을 추가하지 않음

번들 소스와 설치 앱의 SHA-256은 다음과 같이 일치했다.

| 파일 | SHA-256 |
|---|---|
| `swiper-bundle.min.js` | `d4e1d888723c80623ed161f220af4734a5735a5dd30abe0b9860fa5b53e6be77` |
| `swiper-bundle.min.css` | `d3bd7ac4a5cb1eb7c19587255bbba195c3ea726d4aa1e76d3e573f595249fcfd` |
| `LICENSE` | `99f08e3e3ee0799fe1488fcbcb41dc933b14873e9556693209daf9e1007601d6` |

필수 자산이나 라이선스가 없거나 비어 있으면 패키징 단계에서 `FileNotFoundError`로 실패한다. 실행 중 CDN 장애나 임의 버전 상승에 영향을 받지 않는다.

공식 근거:

- [Swiper 시작 및 로컬 bundle 구성](https://swiperjs.com/get-started)
- [Swiper API: Zoom, Keyboard, A11y](https://swiperjs.com/swiper-api)
- [Swiper 변경 이력](https://swiperjs.com/changelog)

## 동작 구조

1. 썸네일을 선택한 다음 dialog를 먼저 화면에 올린다.
2. 현재 필터 결과로 `.swiper > .swiper-wrapper > .swiper-slide` 구조를 만든다.
3. 최대 1,000장의 slide shell은 만들 수 있지만 preview URL은 현재 사진과 앞뒤 한 장에만 넣는다.
4. 사진 load가 끝나면 원본 너비·높이와 stage 너비·높이의 작은 비율을 계산해 `1×` 기준 크기를 명시한다.
5. Swiper가 좌우 transition, touch resistance, pinch, double tap, pan, keyboard, 접근성 안내를 처리한다.
6. 창 크기나 기기 방향이 바뀌면 열린 사진을 다시 stage에 맞추고 확대 배율을 초기화한다.
7. dialog를 닫으면 Swiper 인스턴스를 파기하고 모든 image `src`와 slide DOM을 제거한다.

`prefers-reduced-motion: reduce`에서는 slide transition 시간을 0으로 낮춘다. 마우스 환경에서는 휠 확대를 유지하지만 휠로 사진을 넘기지는 않는다.

## 제공 경로 점검

| 소비자 | HTML/자산 경로 | 결과 |
|---|---|---|
| Mac owner / 로컬 브라우저 | `/photos/stories/{story_id}`, `/story-assets/*` | 정상 |
| Tailscale owner | `https://byoungyoung-macmini.tail53bcc7.ts.net/photos/stories/{story_id}` | 정상 |
| 30일 공유 Story | `/s/{share_id}`, `/story-assets/*` | 인증·허용 목록 유지, 정상 |
| Android WebView | `/mobile-client/story`, `/mobile-client/story/*` | owner web session 요구, 계약 테스트 정상 |

브라우저 자동 요청에서 누락됐던 favicon도 self-hosted SVG로 추가했다. Tailscale 경로에서 발생하던 `/favicon.ico` 502 콘솔 잡음을 제거했고, favicon은 사진이나 사용자 데이터를 포함하지 않는다.

## 실제 Chrome 153 검증

실행 중인 설치 앱을 다시 빌드·서명·설치·재기동하고, 실제 41장 Story로 확인했다.

| 검증 항목 | 결과 |
|---|---|
| Swiper 전역 및 자산 로드 | `typeof Swiper === "function"`, CSS/JS 200 |
| 설치 앱 자산 해시 | 소스 3개 파일과 전부 일치 |
| 첫 사진 | `1 / 41`, 세로 1,536×2,048 |
| 화면 맞춤 | stage 1,920×655, 사진 491×655, 잘림 없음 |
| 지연 로딩 | 최초 3장만 URL 부여 |
| 마우스 플릭 | `1 / 41 → 2 / 41` |
| touch event 플릭 | Tailscale HTTPS에서 `1 / 41 → 2 / 41` |
| 휠 확대 | `1× → 1.2×` |
| 키보드 이동 | `2 / 41 → 3 / 41` |
| dialog 닫기 | slide 0, 남은 image `src` 0 |
| Tailscale 콘솔 | warning/error/exception 0 |
| 코드서명 | `codesign --verify --deep --strict` 통과 |
| 앱 상태 | `/health`: `status=ok`, `daemon_status=ready` |

## 자동 검증

```text
uv run pytest -q
1077 passed in 15.62s

uv run pytest -q tests/test_story_sharing.py tests/test_mobile_client.py tests/test_packaging.py
57 passed in 1.67s
```

정적 분석용 `ruff` 실행 파일은 현재 개발 virtualenv에 설치되어 있지 않아 실행하지 못했다. 대신 Python compileall, 전체 pytest, py2app runtime/import/vendor/face smoke, 코드서명, 실제 Chrome 콘솔 검증을 모두 통과했다. `ruff` 부재는 설치 앱 동작에는 영향을 주지 않지만 개발 도구 설정의 후속 정리 항목이다.
