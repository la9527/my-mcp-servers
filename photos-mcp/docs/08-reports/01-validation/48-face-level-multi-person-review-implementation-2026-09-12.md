# 얼굴 단위 다인물 검토 구현·운영 검증

작성일: 2026-09-12

결론: 사진 전체를 한 사람으로 연결하던 Apple 이름 후보 경로를 얼굴 observation 단위로 정밀화했다. 한 사진에 여러 사람이 있으면 각 얼굴 crop마다 서로 다른 기존 인물 또는 새 이름을 지정할 수 있으며, 확정된 모든 사람이 같은 사진의 Story evidence에 함께 들어간다. 기존 인물 이름·동의·alias·얼굴·audit는 보존했다.

## 구현 범위

| 영역 | 적용 결과 |
|---|---|
| private DB | schema v5 additive migration, geometry·review artifact·photo review revision·alias-face assignment·persistent receipt·Story outbox·versioned 이름 추천 근거 |
| application | Mac·Android 공용 `PeopleWorkspaceService`, 사진별 원자적 다인물 결정 |
| 얼굴 인덱싱 | Apple alias 우선, 정규화 bbox, 검토 crop, 전체 preview, 선택 얼굴 highlight |
| macOS | 전체 사진과 얼굴 crop strip, 정확한 얼굴 선택 뒤 기존·신규 인물 연결 |
| Android | 전용 얼굴 검토 화면, 기존 인물·새 이름·얼굴 아님·나중에, 일치 가능성 및 확정 사진 수, 높은 신뢰도 이름 자동 선택, 중간 저장·다음 사진 |
| 모바일 API | owner session, signed command, nonce, idempotency, stale revision, opaque photo·face·identity·image handle |
| Story | 한 자산의 여러 owner-confirmed face membership을 중복 없이 반영, 갱신 실패는 outbox로 분리 |

## 운영 색인 결과

개인정보 문자열과 원본 파일 경로는 출력하지 않고 집계만 확인했다.

| 지표 | 값 |
|---|---:|
| schema version | 5 |
| 색인 사진 | 40장 |
| 검출 얼굴·embedding | 109개 / 109개 |
| geometry·review crop·highlight | 각 109개 |
| 새 candidate identity | 22개 |
| 새 review item | 40개 |
| 실패 | 0장 |
| pending Apple alias | 10건 / 7장 |
| alias 사진 중 얼굴 검출 | 6장 |
| alias 사진 중 다인물 | 4장 |
| pending review projection | 38장 / 얼굴 109개 |
| 누락 crop·highlight·geometry | 0개 |
| 사용자 선택 가능한 확정 이름 | 3명 |

색인 전 69개였던 기존 얼굴 관측은 삭제하지 않았다. 동일한 안정 face ID는 재사용하고, 새로 포함된 추천 사진의 얼굴만 추가됐다. 사용자 확정 membership은 자동 생성하지 않았고 0건을 유지했다.

## 자동 검증

| 검증 | 결과 |
|---|---|
| 원장·API·인덱싱 집중 pytest | 55 passed |
| Python 전체 pytest | 1025 passed |
| Android release assemble | 성공 |
| Android lintRelease | 오류 0건 |
| APK 서명 | v2·v3 서명 검증 성공 |
| APK metadata | `0.8.2`, versionCode `21`, application `PhotosMcp 앨범` |
| macOS bundle 코드 서명 | deep strict 검증 성공 |
| macOS health | 성공 |
| osxphotos runtime smoke | 성공 |
| photo-source·scene runtime smoke | 성공 |
| person runtime smoke | YuNet·SFace ready |

## 네트워크·배포 검증

| 경계 | 기대 | 결과 |
|---|---|---|
| Tailnet `/mobile-client/download` | 설치 페이지 | HTTP 200, 0.8.2 게시본 |
| Tailnet APK | owner 설치 파일 | HTTP 200, 125,257 bytes, 로컬 게시본과 동일 |
| Tailnet 얼굴 API 무세션 | 인증 필요 | HTTP 401 |
| loopback 모바일 API 무인증 | Tailscale identity 필요 | HTTP 403 |
| 공개 Funnel 8443 얼굴 API | 비공개 | HTTP 404 |

배포 APK SHA-256:

```text
9ee71b59e3fe7feaab64c33de6874fd685e81f68fddac08c99ccb26874ecfd2a
```

다운로드:

<https://byoungyoung-macmini.tail53bcc7.ts.net/mobile-client/download>

## 안전성 확인

- 단체 사진의 기존 사진 단위 alias create·confirm은 repository와 HTTP 양쪽에서 `face_selection_required`로 거절한다.
- 한 transaction에서 같은 얼굴을 두 번 결정할 수 없다.
- 같은 사진의 서로 다른 얼굴을 같은 identity로 중복 연결하면 명시적으로 거절한다.
- Apple alias는 같은 자산의 선택된 얼굴에만 연결할 수 있다.
- command receipt는 SQLite에 저장돼 서버 재시작 후 같은 요청 재전송에도 중복 인물을 만들지 않는다.
- crop·highlight는 `~/.photos-mcp/people/index-private` 아래 owner-only 파생 파일이며 외부 LLM이나 공개 Story package로 전달하지 않는다.
- Story refresh가 실패해도 얼굴과 이름 결정을 rollback하지 않고 outbox 실패로 구분한다.

## 실제 기기 UAT

2026-09-12에 USB로 연결된 Samsung SM-F966N(Android 16, 1080×2520)에 기존 데이터를 보존하는 `adb install -r` 방식으로 0.8.2(versionCode 21)를 설치했다. 최초 설치 시각이 유지되고 앱의 기존 등록 정보도 보존됐다.

| 검증 | 결과 |
|---|---|
| cold launch | 성공, MainActivity 143ms |
| 홈 서버 연결 | `Mac과 연결됨` |
| 앱·서버 버전 | 설치 0.8.2 / 서버 0.8.2 / 최신 |
| GPS Bridge | 연결됨, 실제 최신 동기화 시각 표시 |
| 인물 현황 | 확정 이름 3명, 사진 연결 31건, 이름 후보 10건 |
| 얼굴 검토 진입 | 22장, 얼굴 검토 73건을 서버에서 로드 |
| 다중 얼굴 | 첫 사진에서 서로 다른 얼굴 5개 crop을 독립 선택 가능 |
| 높은 신뢰도 추천 | 지수 94%·확정 사진 6장, 윤지 96%·확정 사진 12장 |
| 자동 선택 | `저장할 후보로 자동 선택됨 · 변경 가능` 표시 확인 |
| 미추천 얼굴 | 기존 인물·새 인물·모르는 사람 무시 선택지 유지 |
| 추천 갤러리 | 실제 썸네일 2장과 GPS 위치 문구 로드 |
| 확대 보기 | 1/2 → 2/2 좌 플릭 성공, 하단 위치 indicator 갱신 |
| 확대 | double tap으로 1.0× → 2.5× 전환, 원래 크기 버튼 상태 갱신 |
| Story WebView | 85장 Story, 장소 태그, 지도, 인물별 필터를 앱 내부에서 로드 |
| 오류·크래시 | 재현 없음, crash buffer와 앱 PID logcat에 fatal/exception 없음 |

실기기에서 Android 16의 edge-to-edge 기본 동작 때문에 `PeopleReviewActivity` 제목이 상태바와 겹치는 문제가 한 건 발견됐다. 시스템 바·display cutout inset을 root에 적용해 수정하고 재빌드·재설치했다. 재검증 결과 콘텐츠는 상태바 아래 `y=110`에서 시작하고 스크롤 영역은 navigation bar 위 `y=2394`에서 끝난다.

테스트는 읽기와 화면 전환까지만 수행했다. 실제 인물 이름 저장은 사용자 선택을 변경하므로 자동으로 누르지 않았다.

## 모르는 인물 무시 후속 검증

- API decision에 `ignore_unknown`을 추가했다.
- 실제 얼굴 observation은 `active`로 유지한다.
- 최신 review state는 `ignored`로 기록한다.
- 연결된 candidate membership은 `rejected`로 전환한다.
- 다른 활성 얼굴이 없는 빈 candidate identity는 `hidden`으로 전환한다.
- Story evidence에는 무시한 얼굴이 포함되지 않는다.
- 같은 모델로 재색인해도 ignored 얼굴을 새 candidate로 다시 묶지 않는다.
- Android 0.8.2와 macOS 후보 사진 화면에 각각 독립 버튼과 접근성 설명을 추가했다.

이 검증은 사용자 이름 입력과 실제 얼굴 판단을 요구하므로 자동화하지 않았다.

## 확정 얼굴 기반 이름 추천 후속 구현

2026-09-12 추가 요구에 따라 이미 사용자가 확정한 얼굴을 다음 얼굴 검토의 이름 후보로 재사용하는 경로를 연결했다.

- 같은 SFace 모델 fingerprint의 owner-confirmed 얼굴만 anchor로 사용한다.
- 새 얼굴과 같은 사진에서 나온 anchor는 지지 표본에서 제외한다.
- 최고 후보뿐 아니라 2순위 후보와의 차이를 함께 평가한다.
- 여러 얼굴 crop 수와 서로 다른 사진 수를 분리해 저장한다.
- 모델 추정 일치 가능성, 확정 사진 수, 추천 이유를 Android와 macOS에 표시한다.
- `ready_to_confirm`은 Android draft와 macOS 인물 선택을 미리 채우지만 DB를 자동 확정하지 않는다.
- 사용자 저장 전에는 Story evidence와 다음 추천의 anchor로 사용되지 않는다.
- 틀린 경우 다른 인물·새 인물·모르는 사람 무시로 바꿀 수 있다.

자동화 단계는 다음처럼 제한했다.

```text
확정 얼굴 축적
  → 새 얼굴과 비교
  → 일치 가능성 + 2순위 차이 + 독립 사진 지지 계산
  → 일반 추천: 사용자가 직접 적용
  → 높은 신뢰도: 선택만 미리 채움
  → 사용자 저장: owner-confirmed + audit + Story refresh
```

`cosine similarity` 자체는 보정 확률이 아니므로 API의 `likelihood_kind`를 `model_estimate`로 고정했다. 무인 자동 확정은 기존 face calibration과 independent holdout이 통계 기준을 통과하기 전에는 활성화하지 않는다.

운영 저장소에 schema v5를 적용한 뒤 기존 얼굴 109개를 삭제 없이 다시 색인했다. 실패 0건으로 끝났고, 확정 얼굴 28개/15장의 anchor에서 현재 후보 30건을 만들었다. 이 중 일반 추천 11건, 저장 전 이름이 미리 선택되는 `ready_to_confirm` 19건이며 20개 pending 사진에서 확인할 수 있다. 얼굴 검출 점수까지 포함한 보수적 재계산 결과이며, 이 집계에는 인물 이름, 사진 경로, embedding 또는 개별 similarity를 기록하지 않았다.
