# 인물 연계 Story v4·Apple alias 수직 기능 구현 검증

검증일: 2026-09-11

관련 계획: [인물 이름 연계 Story·인물별 사진 탐색 개선 계획](../../09-roadmap/01-active/16-person-linked-story-and-people-gallery-plan-2026-09-11.md)

## 1. 결론

보존된 인물 이름과 추천 사진 사이가 끊겨 Story에서 이름이 보이지 않던 경로를 실제 제품 흐름으로 연결했다. Apple Photos 또는 로컬 분석의 이름 정보는 개인 identity DB의 검수 후보로만 들어가며, 사용자가 Android 소유자 화면에서 기존 확정 인물과 연결한 정확한 사진만 Story 인물 증거가 된다.

Story는 `recommendation-story-v4`로 승격했다. 확인된 인물마다 Story 범위의 불투명 facet handle을 생성하고, 인물 버튼을 누르면 해당 인물이 확인된 사진만 grid와 확대 viewer에 남는다. 필터 후 viewer의 현재 위치와 전체 장수도 필터된 집합을 기준으로 계산한다. 실제 이름은 Hermes/Qwen 프롬프트에 전달하지 않고 서버가 확인된 이름만 사용해 자연스러운 chapter 문구를 만든다.

## 2. 구현 내용

### 2.1 개인 identity DB schema v2

- `provider_person_alias_versions`: Apple/local 이름 후보의 append-only 버전
- `asset_person_association_versions`: 정확한 `local_asset_id`와 확정 identity의 버전 연결
- `current_owner_confirmed_asset_people`: Story 조회용 현재 projection
- schema 생성과 version 갱신은 `BEGIN IMMEDIATE` transaction 안에서 수행
- 기존 identity, 이름, 동의, 얼굴 관측, membership, legacy hold, audit chain은 보존
- DB 파일 `0600`, 상위 디렉터리 `0700` 유지

이름만 있는 provider 힌트의 키에는 자산 식별자를 포함한다. 서로 다른 사진의 같은 문자열은 별도 후보가 되며 자동으로 동일 인물에 병합되지 않는다.

### 2.2 추천 보관과 기존 데이터 백필

- 새 Apple/local 추천을 보관한 직후 `known_persons` 또는 `persons`를 개인 alias 후보로 등록
- Google 추천에는 이 경로를 적용하지 않아 Apple 이름으로 Google 사진이 오인 연결되지 않음
- 이름 문자열은 일반 `recommendation_members.payload_json`, manifest, 로그에 복사하지 않음
- `scripts/backfill_provider_person_aliases.py`는 기본 dry-run이며 `--apply` 때만 기존 완료 추천에서 개인 후보를 복구
- 운영 백필 결과: source 후보 5건, 신규 private alias 5건, 자동 확정 0건
- 동일 명령 재실행 결과: 신규 0건으로 idempotency 확인

### 2.3 owner API와 Android

추가한 owner API는 모두 Tailnet owner와 device session 경계를 유지한다.

- `GET /mobile-client/v1/people/readiness`
- `GET /mobile-client/v1/people/aliases`
- `POST /mobile-client/v1/people/alias/confirm`

mutation은 기존 owner command의 기기 서명, nonce, idempotency key, 10분 action handle, optimistic identity revision을 재사용한다. API는 내부 person/alias ID를 보내지 않고 기기·세션에 묶인 `pah_`와 `aal_` handle만 보낸다. 연결이 성공하면 모든 활성 Story의 identity projection을 갱신한다.

Android `인물 확인 현황`에는 다음이 표시된다.

- 확정 이름 수
- 사진 연결 수
- 확인할 이름 후보 수
- legacy lineage 검수 수
- Apple 이름 후보와 연결 가능한 기존 확정 인물 버튼
- 개인 Story·30일 가족 공유 이름 동의

모든 동작 버튼은 기존 최소 48dp 계열 컴포넌트를 사용하고 접근성 설명을 제공한다.

### 2.4 Story v4와 HTML viewer

- capabilities: `people_summary`, `people_filter`, `structured_people_narrative`
- 사람별 Story-scoped `facet_handle`, `asset_ids`, 대표 asset
- 사진별 `person_facets`, chapter별 `person_facets`
- 확인된 이름을 사용한 server-owned `people_title`, `people_intro`
- 인물 chip을 접근 가능한 `aria-pressed` filter button으로 전환
- 전체/인물 필터의 사진 수를 live region으로 안내
- 필터된 tile 집합으로 viewer index, progress, 좌우 이동, 인접 이미지 preload를 재계산
- v3 mobile projection 호환 유지
- family-share는 기존 별도 audience projection을 유지하며, 이름 미포함 공유에는 owner 이름과 새 private handle이 노출되지 않음

## 3. 운영 적용 결과

| 항목 | 결과 |
|---|---:|
| 확정 identity | 3 |
| active face observation | 0 |
| 확정 face membership | 0 |
| 확정 asset association | 0 |
| Apple private alias 후보 | 5 |
| pending legacy lineage hold | 37 |
| audit chain | 정상 |
| 활성 Story v4 | 1/1 |
| 현재 Story 인물 facet | 0 — 소유자 연결 전이므로 정상 |

이 단계에서 alias 5건을 자동으로 세 identity에 붙이지 않았다. Android의 `설정 → 인물 확인 현황`에서 사진에 맞는 이름을 선택하면 해당 자산만 확정 연결되고 Story가 갱신된다.

## 4. 검증 결과

| 검증 | 결과 |
|---|---|
| Python 전체 회귀 | 1,000개 테스트 통과 |
| identity·Story·mobile 집중 회귀 | 112개 테스트 통과 |
| Android debug unit/assemble/lint | 성공, lint issue 0 |
| Android release R8/assemble/lint | 성공 |
| APK 서명 | APK Signature Scheme v2·v3 검증 통과 |
| 문서 링크·형식 검사 | 통과 |
| `git diff --check` | 통과 |

주요 자동 검증에는 schema v1→v2 보존, alias 동명이인 비병합, 승인 전 이름 미노출, 정확한 자산만 연결, Google 경로 제외, Story facet 자산 집합, HTML 필터와 viewer 동일 집합, signed API의 handle 범위와 stale revision을 포함한다.

## 5. 배포 산출물

- Android 버전: `0.7.0` (`versionCode=14`)
- APK: `~/.photos-mcp/runtime/mobile-client/downloads/PhotosMcp-Album.apk`
- 크기: 112,913 bytes
- SHA-256: `b7ff97e2467052ae7c508e33787e312f0492194205930f670a90510e57a80582`
- mobile client launch agent 재시작 후 신규 route 확인: GET route는 미인증 요청에 401, POST route는 잘못된 method에 405로 응답하여 배포된 route가 활성화됨

## 6. 의도적으로 자동 승격하지 않은 항목

현재 환경은 identity-capable embedding backend의 운영 preflight를 통과하지 않았다. MediaPipe detection-only 결과를 동일인 근거로 사용하지 않으며, active face observation이 0건인 상태에서 legacy hold 37건을 이름에 자동 연결하지 않는다. 이는 미완성 fallback이 아니라 잘못된 가족 이름 표시를 막기 위한 정책 gate다.

다음 운영 검수는 Android에서 5개 Apple 후보를 실제 사진과 대조해 소수부터 연결하는 것이다. 그 뒤 Story의 인물별 장수·필터 grid·viewer indicator가 모두 일치하는지 확인하면 Phase 1 운영 승격이 끝난다. routine face backend는 별도 표본의 얼굴 검출률, embedding 생성률, 동명이인·오병합 검수를 통과한 뒤 Phase 2로 승격한다.
