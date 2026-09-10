# 인물 중심 선별·Story 신원 기반 구현 검증

## 결론

기존 PhotosMcp의 인물 점수 프로필, 레거시 People 원장, 추천 자산, Story와 Android Companion 사이에 끊겨 있던 계약을 하나의 보수적인 흐름으로 연결했다. 사용자는 Android의 날짜별 Story 실행에서 `균형 있게`, `인물 위주`, `풍경 위주`를 선택할 수 있다. Apple·로컬 사진의 `인물 위주`는 기존 person 품질 프로필을 사용하고, Google Photos가 포함된 인물 모드는 Google Photos API 정책을 우회하지 않고 명시적으로 차단한다.

이름은 LLM이 판단하지 않는다. 개인 Story와 가족 공유 Story의 인물 캡션은 소유자가 확인한 identity·membership·이름·대상별 동의가 모두 유효할 때만 서버가 결정적으로 만든다. 가족 공유의 이름 포함은 기본 꺼짐이며, 켜더라도 최신 `family_share` 동의를 다시 조회한다. 공개 패키지와 모바일 DTO에는 내부 identity ID, 얼굴 ID, 임베딩, 유사도, provider asset ID, 원본 경로가 포함되지 않는다.

## 구현 범위

### 인물 선택 계약

- 외부 선택 모드 `balanced`, `people_present`, `landscape`를 각각 내부 `general`, `person`, `landscape` 프로필에 고정 매핑한다.
- `specific_person`은 계약만 예약하고 현재는 명시적인 미지원 오류로 종료한다.
- Android preview와 실제 실행이 같은 모드·프로필을 사용한다.
- 프로필을 바꾸면 승인된 preview를 무효화해 이전 수량으로 잘못 실행하지 않는다.
- combined parent, child, retry, status와 loopback MCP 경로에서 모드가 손실되지 않는다.
- 새벽 3시 자동 작업의 기본값은 계속 `balanced/general`이다.

### private identity 저장소

- `~/.photos-mcp/people/person-identities-private.sqlite3`를 단일 신규 원장으로 추가했다.
- 저장소 디렉터리는 `0700`, DB는 `0600`이다.
- person ID는 불변 opaque ID이며 identity, name, membership, Story 동의를 각각 versioning한다.
- identity revision 기반 optimistic concurrency를 사용한다.
- `owner_confirmed` membership은 얼굴 하나당 현재 identity 하나만 가질 수 있도록 트랜잭션과 DB invariant로 강제한다. 충돌 시 revision과 감사 로그까지 전부 롤백하며, 제외·거절 결정이 레거시 귀속보다 우선한다.
- 얼굴 observation은 로컬 자산 또는 provider 자산 하나에만 연결되고, 모델·crop·bbox fingerprint로 안정 ID를 만든다.
- 이름·경로·임베딩·유사도는 append-only 감사 로그에 평문으로 기록하지 않는다.
- 레거시 JSON은 덮어쓰거나 삭제하지 않는다.

### Android 소유자 동의 관리

- Tailnet owner session 전용 `POST /mobile-client/v1/people/consent`를 추가했다.
- mutation은 별도 `identity:write` scope, 기기 P-256 요청 서명, 2KB strict body, nonce replay 차단, idempotency key 검사를 모두 통과해야 한다.
- 내부 identity ID 대신 기기·identity revision에 묶인 192-bit 난수 action handle을 발급하며 10분 뒤 만료된다.
- stale revision, 변조·만료 handle, 같은 idempotency key의 다른 body는 각각 명시적으로 거부한다.
- user-confirmed 이름은 owner-only 설정에서 동의가 꺼져 있어도 식별할 수 있지만, candidate/provider-asserted 이름은 표시하지 않는다.
- 인물별 `개인 Story에 이름 표시`와 `30일 가족 공유에 이름 표시` 스위치는 독립적이며 초기값은 모두 꺼짐이다.
- 가족 공유 스위치를 켜도 실제 공유 생성 화면의 `이름 포함` 선택은 별도로 필요하다.

### Story와 공유

- Story schema를 `recommendation-story-v3`로 올리고 identity evidence hash를 Story revision 근거에 포함했다.
- LLM에는 실제 이름 대신 opaque person ref만 전달하며 이름이나 관계 추론을 금지한다.
- 모델이 임의로 만든 이름·인물 캡션은 버리고 서버 projection으로 덮어쓴다.
- 개인 Story HTML, 모바일 홈·Story 목록·사진 뷰어에 확인된 인물 요약을 선택적으로 표시한다.
- 이름 필드가 없는 기존 Story는 기존 화면으로 정상 표시한다.
- 가족 공유 이름 포함 체크박스는 기본 꺼짐이다.
- 이름 포함 공유에는 `family_share` 동의된 이름만 들어가며 내부 person ref는 공개하지 않는다.
- `family_share` 동의를 철회하면 해당 인물이 포함된 활성 공유만 안정적인 비공개 revocation tag로 찾아 즉시 무효화하고 파생물을 정리한다.
- 일반 추천 reconciliation과 동의 변경 직후 refresh 모두 같은 identity 저장소를 사용하므로, 정상 동기화가 확인되기 전에 오래된 이름이 남거나 새 이름이 사라진 성공 응답을 반환하지 않는다.
- Story CSS/JS cache key를 `v=6`으로 올려 기존 WebView 캐시가 UI 변경을 가리지 않게 했다.

## 레거시 원장 마이그레이션

운영 레거시 schema v3 원장을 먼저 count-only dry-run한 뒤 신규 저장소에 적용했다. 이름 자체, 얼굴 ID, 사진 경로는 검증 출력과 이 문서에 기록하지 않았다.

| 항목 | 결과 |
|---|---:|
| 레거시 수동 identity | 3명 |
| 보존된 사용자 확인 이름 | 3개 |
| 기존 membership 검토 보류 | 34건 |
| 기존 제외 결정 검토 보류 | 3건 |
| orphan hold | 0건 |
| 신규 원장 visible identity | 3명 |
| 신규 원장 pending lineage hold | 37건 |

기존 face ID만으로 새 분석 결과와 동일 얼굴이라고 가정하지 않았다. 따라서 37건은 안정적인 로컬 자산·모델 lineage가 확인될 때만 membership으로 승격한다. 마이그레이션은 idempotent하며 재실행해도 identity를 중복 생성하지 않는다.

레거시 측정 자료와 현재 추천 보관 DB를 대조한 dry-run에서는 37개 legacy face를 모두 재현했지만, 과거 분석 job과 현재 materialized local recommendation asset 사이의 canonical 연결이 없었다. 결과는 `matched=0`, `ambiguous=0`, `missing=37`이었다. 따라서 적용을 실행하지 않고 기존 hold를 검토 대기로 유지했다. 새 reconciliation CLI도 기본은 read-only dry-run이며, 정확히 하나의 완료 자산으로 증명되는 경우에만 `--apply`가 기존 확인·제외 결정을 옮긴다.

## 검증

| 검증 | 결과 |
|---|---|
| identity·selection·Story·mobile·MCP 핵심 테스트 | 131 passed |
| 전체 Python 테스트 최종 | 930 passed |
| 문서 구조 재검증 | Markdown 90개 통과 |
| Android `testDebugUnitTest assembleDebug lintDebug` | BUILD SUCCESSFUL, lint issue 0 |
| Python compile 및 `git diff --check` | 통과 |
| Android 0.6.0 배포 빌드·R8·release lint·APK v2/v3 서명 검증 | 통과 |

## 운영 반영 확인

- 최신 소스를 `/Users/byoungyoungla/Applications/PhotosMcp.app` 독립 실행 번들로 다시 패키징하고 depth-first 서명, 지정 요구사항, runtime import, vendor runtime smoke를 모두 통과시켰다.
- 메인 앱과 Tailnet 전용 모바일 서비스를 새 프로세스로 재기동했다. 메인 health는 `status=ok`, `daemon_status=ready`, active job과 pending mutation은 모두 0이었다.
- Tailnet HTTPS의 `/photos`, `/mobile-client/download`, `/mobile-client/download/PhotosMcp-Album.apk`가 모두 HTTP 200을 반환했다. 모바일 서비스 loopback 직접 요청은 Tailscale identity가 없으므로 403을 반환해 경계가 유지됐다.
- 배포 APK는 `versionCode=10`, `versionName=0.6.0`, 108,817 bytes이며 SHA-256은 `97474a935bae8bc3e6e159d5b3c3ee6a907027be49c9f4ea5d79b4445bc1a95d`다. 다운로드 응답과 로컬 원본의 digest가 일치하고 APK Signature Scheme v2·v3 검증을 통과했다.
- 운영 identity 원장의 audit hash chain은 유효했다. visible identity 3개와 미해결 lineage hold 37건을 집계로만 확인했고 실제 이름은 검증 출력이나 로그에 남기지 않았다.

## Android 실기기 통합 검증

2026-09-09에 잠금 해제된 Samsung SM-F966N(Android 16)에서 기존 앱 데이터를 보존한 `0.6.0`(`versionCode=10`) 설치본을 콜드 스타트하여 실기기 회귀 검증을 완료했다. 검증 중 새 사진 분석·Story 생성·삭제는 실행하지 않았고, 인물 이름 표시 동의도 변경하지 않았다.

| 실기기 시나리오 | 결과 |
|---|---|
| 앱 콜드 스타트와 owner 연결 | 정상, `Mac과 연결됨` 표시 |
| 홈·작업·추천·이야기 4개 탭 | 정상, 운영 데이터와 기존 결과 표시 |
| 추천 화면 | 실제 추천 썸네일 48장과 GPS 기반 장소 표시 |
| 추천 사진 전용 뷰어 | `1 / 48` → `2 / 48` 좌우 넘김, 더블탭 `1.0×` → `2.5×` → `1.0×` 정상 |
| Story 본문 | 제목·서사·장소 칩·Google 지도·사진 그리드 정상 |
| Story HTML 사진 뷰어 | 타일 열기, `5 / 94` → `6 / 94` 좌우 넘김, 더블탭 `1×` → `2.5×` 정상 |
| 시스템 영역 | 사진 뷰어와 Story 뷰어 모두 상단 상태 바와 하단 시스템 내비게이션 바 유지 |
| 수동 Story 요청 화면 | 기간·소스·최대 장수·`균형 있게`/`인물 위주`/`풍경 위주` 선택 정상 |
| Google 포함 인물 모드 보호 | 미지원 안내 표시 및 실행 버튼 비활성화 정상 |
| Apple-only 인물 모드 미리보기 | Apple 후보 6장, 모바일 GPS 참조 후보 5장 표시; 분석 작업은 시작하지 않음 |
| 알림 화면 | 기존 완료·부분 완료·오류 이벤트 표시 정상 |
| Android 권한 연결 | 앱 상세 설정 화면 진입 후 복귀 정상 |
| 인물 동의 설정 | 미해결 37건과 확인된 인물 설정 표시; 개인·가족 공유 스위치 기본 꺼짐 확인 |
| GPS Bridge 즉시 동기화 | 신규 사진이 없어 0건으로 정상 완료, 마지막 동기화 시각 갱신 |

최종 Android crash buffer는 비어 있었고 최근 logcat에서 `FATAL EXCEPTION`, ANR, TLS·DNS·연결·timeout·HTTP 4xx/5xx 오류가 발견되지 않았다. 서버 health는 `status=ok`, `daemon_status=ready`, active job 0, pending mutation 0이었다. `photos_automation`과 `photos_thumbnail`의 preflight 경고는 시작 시 멈춤을 막기 위해 실제 분석 또는 명시적 점검 시점까지 검사를 미루는 설계된 상태이며, 권한과 Photos 메타데이터 읽기는 정상이다.

Tailnet HTTPS에서는 `/photos`와 APK 직접 다운로드가 HTTP 200, `/photos-actions`가 정상 세션 경로로 HTTP 303을 반환했다. 인증 쿠키 없는 `/mobile-client/story` 요청은 HTTP 401, Tailscale identity 없는 loopback 다운로드 요청은 HTTP 403으로 차단되어 owner 경계도 유지됐다.

## 운영 안전 정책

- 자동 얼굴 군집은 이름을 확정하지 않는다.
- 사용자 확인 전 membership은 Story 인명에 사용하지 않는다.
- 개인 Story 동의와 가족 공유 동의는 독립적이며 둘 다 기본 꺼짐이다.
- Google Photos Picker·Library API의 얼굴 식별 우회 자동화는 하지 않는다.
- 가족 공유 체크박스를 켜도 인물별 `family_share` 동의가 없으면 이름을 제외한다.
- 이름 변경·숨김·동의 철회는 identity evidence hash를 바꾸고 새 Story revision의 근거가 된다.

## 다음 승격 단계

이번 구현은 안전한 기반과 `people_present` 실제 요청 경로를 연다. `특정 인물` 모드와 자동 named match는 아직 운영 기능이 아니다. 다음 단계는 새 분석에서 생성된 안정 face observation과 lineage reconciliation 결과를 소유자가 검토하는 same/different/unsure/face 아님 UX, 그리고 특정 인물 precision holdout을 순서대로 검증하는 것이다. 이름 철회에 따른 기존 가족 공유 cascade revoke 기반은 이번 단계에서 구현·회귀 검증했다.
