# Android Companion 디자인 시스템

## 방향

PhotosMcp Android 앱은 **Memory Paper**의 따뜻한 개인 사진 아카이브 분위기와 Android Material navigation의 익숙한 상호작용을 결합한다. 사진과 이야기가 주인공이고, 앱 chrome은 단정한 도구 역할만 한다.

세 독립 검토는 다음 관점을 비교했다.

| 검토 관점 | 핵심 결론 |
|---|---|
| Android navigation·icon | text-only 버튼과 선택 상태 부재가 가장 큰 문제다. 4개 하단 목적지는 유지하고 vector icon, active pill, ripple을 적용한다. |
| 사진 앱 branding | warm paper + forest palette가 가족 사진과 기록에 가장 잘 맞는다. GPS pin은 앱 정체성이 아니므로 launcher에서 제거한다. |
| 사용성·접근성 | 기존 화면은 약 5/10이다. 명칭 일치, 48dp target, TalkBack selected state, dark mode, WebView 오류 복구가 필요하다. |

검토한 대안 중 Gallery Mono는 정서는 부족했고, Material Tonal Utility 단독은 일반 업무 dashboard처럼 보였다. 최종안은 Memory Paper의 시각 언어에 Material interaction을 결합한 것이다.

## 명칭과 정보 구조

| 위치 | 명칭 | 아이콘 | 의미 |
|---|---|---|---|
| 앱 | `PhotosMcp 앨범` | 겹친 사진 프레임 + 작은 선택 표시 | 추천·이야기·GPS 보강을 포괄하는 개인 사진 앱 |
| 하단 1 | `홈` | house | 전체 상태와 최근 이야기 |
| 하단 2 | `작업` | history | 실행 명령이 아니라 현재 작업과 이력 |
| 하단 3 | `추천` | photo + sparkle | Apple·Google 통합 추천 사진 |
| 하단 4 | `이야기` | open book | 날짜·장소별 Story Album |
| 상단 | `알림` | bell | 완료·오류·확인 필요 event |
| 상단 | `설정` | gear | GPS Bridge, 권한, 기기 연결 |

내부 route ID는 `home`, `runs`, `results`, `story`, `inbox`, `settings`를 유지한다. 사용자에게 보이는 명칭만 목적에 맞게 바꿔 서버 계약을 건드리지 않는다.

## 토큰

### Light

| 역할 | 값 |
|---|---|
| background | `#F7F5EF` |
| surface | `#FFFDF8` |
| raised surface | `#FFFFFF` |
| primary text | `#1B211F` |
| secondary text | `#5F6964` |
| primary | `#1D6552` |
| primary container | `#D9EFE7` |
| outline | `#D8DCD6` |
| success / warning / error / info | `#2F6B4F` / `#745519` / `#A33D3D` / `#425F77` |

### Dark

| 역할 | 값 |
|---|---|
| background | `#101411` |
| surface / raised | `#181D1A` / `#202622` |
| primary / secondary text | `#EFF3EE` / `#AFB8B1` |
| primary / container | `#82CFB4` / `#214D40` |
| outline | `#3C4741` |

네이티브 resource와 Story CSS는 같은 semantic 역할과 값을 사용한다. 외부 font·icon CDN을 쓰지 않고 Android system font, generic system serif와 로컬 VectorDrawable만 사용한다.

## 컴포넌트 계약

- 상단 앱 바는 64dp 이상이며 32dp mark, 한 번만 표시하는 `PhotosMcp`, 48dp 알림·설정 action으로 구성한다.
- 하단 NavigationBar는 80dp이며 icon 24dp와 12sp label을 함께 사용한다.
- 선택 항목은 색에만 의존하지 않고 64×32dp pill, primary tint, 굵은 label, accessibility selected state를 함께 사용한다.
- 모든 주요 touch target은 최소 48×48dp다.
- 화면 gutter 20dp, section 24dp, card gap 12dp, card padding/radius 16dp의 4/8dp rhythm을 사용한다.
- 기본 버튼은 48dp 이상, 14dp radius와 native ripple을 사용한다.
- 화면 heading은 28sp, body는 15~16sp, navigation label은 12sp를 기본으로 한다.
- 완료·일부 완료·오류·확인 필요는 색상과 문구를 함께 사용한다.
- WebView viewer는 48px control, 네 방향 safe-area, SVG chevron, `aria-live` counter/caption을 사용한다.

## Launcher icon

기존 위치 pin은 GPS 보조 앱으로만 보이므로 제거한다. forest background 위에 겹친 두 장의 사진 프레임, 작은 golden sparkle, white selection mark를 배치한다. adaptive icon foreground/background를 분리하고 Android 13 monochrome resource도 제공한다.

## 검증 기준

- Android 16 실제 단말에서 여섯 화면과 선택 상태를 확인한다.
- UI Automator로 nav action의 label, selected state와 최소 48dp bounds를 확인한다.
- light/dark, 기본/큰 글자, portrait/landscape에서 겹침과 잘림을 확인한다.
- Story grid, 확대, swipe, Android back, HTTP/네트워크 오류 재시도를 확인한다.
- 기존 pairing, Keystore, GPS checkpoint, encrypted outbox와 Tailnet/Funnel 경계를 회귀 검증한다.

## 2026-09-08 실기기 검증 결과

Android 16 단말의 기존 설치 위에 새 APK를 덮어써 pairing과 GPS 상태를 보존한 채 검증했다.

- dark·light 모두 상단 mark, 알림·설정과 `홈 · 작업 · 추천 · 이야기`가 의도한 token으로 표시됐다.
- UI Automator 기준 상단 action은 48×48dp, 하단 각 목적지는 약 99×72dp이며 label·선택 상태가 accessibility tree에 노출됐다.
- 하단 선택은 pill, icon tint, 굵은 label, `선택됨` state를 함께 사용해 색상에만 의존하지 않는다.
- Story는 앱의 dark/light 상태를 문서 `data-theme`로 명시해 WebView가 시스템 선호를 전달하지 않는 단말에서도 native shell과 일치한다.
- Story grid, 큰 사진 viewer, 48px 이전·다음·닫기, 좌우 swipe와 Android back dismiss를 실제 28장 Story로 확인했다.
- light + font scale 130%, landscape에서도 header·card·navigation이 겹치거나 잘리지 않았고 검사 후 단말 설정을 원복했다.
- 주요 foreground/background 대비는 5.22:1~16.57:1로 일반 텍스트 WCAG AA 기준을 충족했다.

Refactoring UI 간이 진단은 기존 5.0/10에서 8.4/10으로 올랐다. hierarchy, spacing, dark/light, icon consistency, touch/accessibility는 해소됐다.

## 2026-09-08 추천 사진 grid 보완

버전 0.3.0부터 네이티브 `추천` 화면에도 실제 추천 파생 이미지가 표시된다.

- 세로 화면은 2열, 가용 폭이 넓은 화면은 최대 4열의 정사각형 grid를 사용한다.
- 각 tile은 실제 thumb, 하단 gradient, 추천 제목과 날짜·도시 요약을 함께 보여 준다.
- 로딩 중에는 사진 placeholder와 progress를 표시하고, 한 장의 실패가 다른 tile을 막지 않는다.
- tile을 선택하면 검은 배경의 큰 preview와 배율 초기화·닫기, 하단 현재 위치, 추천 설명이 열린다.
- 썸네일은 최대 4개 동시 요청으로 제한하고 24MiB memory LRU와 128MiB app-private disk cache를 사용한다.
- 서버는 현재 추천 결과에 포함된 asset의 `thumb`과 `preview`만 짧은 기기 bearer로 제공하며 원본 route는 만들지 않는다.
- 파생 JPEG는 orientation을 보정하고 EXIF/GPS를 제거한다. 응답과 설치 페이지에는 `no-store`와 검색 차단 정책을 유지한다.

0.3.0은 자동 빌드·lint·API 계약 검증을 통과했지만 새 grid의 Android 실기기 시각 회귀는 단말이 ADB에서 분리돼 다음 연결 때 최종 확인한다. 0.2.0에서 완료한 navigation, Story, light/dark, 130% 글자, landscape 실측 결과를 0.3.0 검증으로 잘못 확대하지 않는다.

## 2026-09-08 한 장 보기 제스처 계약

버전 0.4.1부터 네이티브 추천 뷰어와 Story WebView가 같은 조작 원칙을 사용한다. 0.4.2에서는 네이티브 추천 뷰어가 Android 시스템 바를 숨기지 않도록 보완했다.

- 핀치·두 번 탭으로 확대하고, 확대 상태에서는 drag로 이동한다.
- 1×에서는 좌우 flick으로 사진을 한 장씩 넘긴다.
- 화면을 가리는 이전·다음·확대·축소 버튼은 두 뷰어 모두 표시하지 않는다.
- 상단에는 현재 배율을 표시하고 원래 크기로 돌아가는 `1×` 계열 버튼과 닫기만 둔다.
- 하단에는 `현재 / 전체` 숫자와 얇은 진행 막대를 함께 표시한다. 사진이 많아도 점 indicator를 과도하게 나열하지 않는다.
- 키보드 환경의 방향키, `+`, `−`, `0` fallback은 화면 버튼과 별개로 Story에 유지한다.
- 네이티브 전체 화면 viewer에서도 상단 상태바와 하단 내비게이션 바를 항상 표시한다. 사진·control·설명은 status/navigation/display-cutout inset을 제외한 안전 영역 안에 배치한다.

- 화면 상단에는 48dp/px 이상의 `현재 배율/초기화 · 닫기` control을 제공한다.
- 두 번 탭은 기본 1×와 빠른 확대 2.5×를 오간다.
- 두 손가락 pinch는 터치 중심을 유지하면서 1×~4× 범위에서 확대·축소한다.
- 1×보다 클 때 한 손가락 drag는 사진 내부 pan으로 사용하며 빈 배경이 과도하게 드러나지 않도록 이동 범위를 제한한다.
- 기본 1×에서만 수평 이동량·속도·수직 편차를 함께 통과한 fling을 이전/다음 사진 전환으로 처리한다. 확대된 사진을 살펴보는 동작이 페이지 전환으로 오인되지 않는다.
- Story의 키보드 좌우 화살표는 제스처를 사용하기 어려운 데스크톱 환경의 fallback으로 유지한다.
- 사진 전환 때 배율과 pan을 1× 중앙으로 초기화하고 앞뒤 preview를 미리 요청한다.
- 뷰어 아래에는 `두 번 탭하거나 두 손가락으로 확대 · 기본 크기에서 좌우로 넘기기` 안내를 표시한다.

0.4.0에서 실제 Tailnet Story 28장을 412×915 mobile viewport로 검증해 더블탭 2.5×, pinch 2×, 확대 상태 pan, `1 / 28 → 2 / 28` 수평 fling과 JavaScript 오류 0건을 확인했다. 0.4.1은 이 조작을 유지하면서 버튼을 정리하고 하단 위치 indicator를 추가했으며 Android compile·lint, Story 계약 테스트와 release build를 통과했다. 실제 단말 재확인은 다음 ADB 연결 때 수행한다.
