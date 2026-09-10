# 삭제·재분석·추천 앨범 수명주기 Phase 0 검증

- 검증일: 2026-09-10 KST
- 대상: 작업 목록 삭제, 날짜 전체 재분석, 수동 Story, 로컬 추천 보관소, Apple/Google 추천 앨범, 인물·GPS 원장
- 설계 기준: [삭제·재분석·추천 앨범 수명주기 재설계](../../09-roadmap/01-active/14-deletion-reanalysis-and-recommendation-lifecycle-plan-2026-09-10.md)
- 검토 방식: 데이터 저장소, 외부 앨범 게시, UX·복구 정책을 독립 에이전트 3개가 각각 감사한 뒤 운영 DB 실측과 회귀 테스트로 통합

## 1. 결론

삭제 후 재분석에서 과거 추천이 새 결과에 섞이는 직접 원인을 수정했다.

- 작업 목록 비우기는 UI 작업 이력과 임시 산출물만 정리한다.
- 사진별 처리 원장, 인물 이름·동의·감사 기록, Android GPS 원장, 추천 archive, 외부 앨범 영수증은 보존한다.
- `해당 기간 전체 다시 분석`은 처리 원장을 물리 삭제하지 않고 `reanalyze=true`로 새 분석을 만든다.
- 수동 Story는 해당 parent run에서 만들어진 정확한 recommendation collection만 사용한다.
- 추천이 0장인 명시적 빈 collection은 “필터 없음”으로 변환하지 않는다.
- 수동 재분석 결과는 기존 월별 publish group에 자동 편입하지 않는다.
- 로컬 추천 사본 정리 시에도 Apple/Google 외부 게시 영수증은 보존한다.

현재 누적 월별 group과 외부 앨범은 과거 결과를 포함한 archive 성격이다. 이번 Phase 0에서는 이를 자동 제거하지 않았다. generation/current-head 데이터 모델과 provider membership 검증이 없는 상태에서 기존 앨범을 정리하면 사용자가 보관하려던 사진까지 바뀔 수 있기 때문이다.

## 2. 세 가지 독립 감사 결과

### 데이터 저장소·삭제 경계

- 처리 원장에는 Apple 103건, Google Photos 305건, 합계 408건이 남아 있었다.
- 추천 collection 14개와 member 126개, 물리 로컬 추천 자산 96개가 존재했다.
- Story 7개 중 6개는 이미 soft-deleted였고 1개가 ready 상태였다.
- 인물 저장소는 작업 DB와 분리되어 있으며 확정 인물 3명, owner·family 공유 동의, 기존 수동 얼굴 매핑과 감사 기록이 모두 남아 있었다.
- 새 lineage의 관측·membership projection 0건은 작업 목록 삭제로 지워진 결과가 아니었다.

### 추천 앨범·중복·게시 경계

- 현재 월별 group 96개는 “최신 추천 집합”이 아니라 과거 추천의 누적 합집합이었다.
- destination receipt 190개는 local store 96개와 Apple album 94개였다.
- 기존 게시기는 외부 앨범에 새 항목을 추가할 수 있지만 이전 추천의 membership을 제거하지 않는다.
- 로컬 자산 정리 시 외부 영수증까지 지우면 관리형 Google 결과를 다시 입력 사진으로 오인할 수 있어, 외부 영수증을 보존하도록 수정했다.

### UX·복구·개인정보 경계

- `전체 기록 삭제`는 처리 원장·인물 정보까지 지운다는 오해를 만들기 때문에 `작업 목록 비우기`로 명칭을 바꿨다.
- 재분석 요청 조건이 삭제 가능한 작업 row에만 있으면 Story는 남아도 동일 조건 재실행이 불가능하므로, 정규화한 reanalysis spec을 Story scope에도 저장한다.
- 실패한 재분석이 기존 Story나 앨범을 먼저 없애지 않도록 새 결과를 side-by-side로 완성한 뒤 전환하는 정책이 필요하다.

## 3. 확인된 실제 오류와 운영 데이터 교정

가장 최근 수동 실행은 추천 결과가 0장이었다. 그러나 기존 ready Story는 collection 제한 없이 같은 날짜의 모든 과거 로컬 추천을 조회해 96장을 표시하고 있었다.

운영 DB에 exact collection scope backfill을 적용했다.

| 항목 | 교정 전 | 교정 후 |
|---|---:|---:|
| 목록에 보이는 ready Story | 1 | 0 |
| 잘못 표시되던 Story 사진 | 96 | 0 |
| 전체 Story tombstone 포함 | 7 | 7 |
| 사진별 처리 원장 | 408 | 408 |
| 로컬 추천 자산 | 96 | 96 |
| 확정 인물 | 3 | 3 |
| 삭제 Story의 활성 공유 | 0 | 0 |

이번 교정은 잘못 만들어진 Story만 soft-delete했다. 원본, 로컬 추천 파일, 인물·GPS 정보, Apple/Google 앨범과 외부 receipt는 삭제하지 않았다. 다음 전체 재분석이 성공하면 그 실행의 정확한 collection만으로 새 Story가 생성된다.

## 4. 적용한 코드 안전장치

### 수동 Story 범위 고정

- parent manual run의 child `recommendation_storage.collection_id`를 추출한다.
- Story scope에 정확한 `collection_ids`와 정규화된 재분석 조건을 영속화한다.
- 작업 목록을 비운 뒤에도 Story에서 동일 조건 재분석 request를 복구한다.
- 활성 수동 Story의 구형 scope를 보정하고, 정확한 collection이 비어 있으면 잘못된 과거 사진을 보여 주지 않고 Story를 soft-delete한다.

### 수동 결과와 자동 월별 앨범 분리

- `publication_policy=none`인 수동 실행은 로컬 추천 사본과 실행 전용 Story만 만든다.
- 수동 collection을 승인된 월별 publish group에 자동 등록하지 않는다.
- 일일 자동화의 누적 월별 앨범 동작은 유지한다.

### 영수증과 재입력 방지 근거 보존

- 로컬 추천 자산을 제거할 때 `local_store` receipt만 정리한다.
- Apple/Google 외부 receipt는 남겨 원격 사본 provenance와 managed-output 제외 근거로 사용한다.

### Google Picker 부분 결과 차단

- Chrome에서 완료한 선택 수와 Picker API의 전체 pagination 결과 수를 비교한다.
- 반영 지연을 고려한 제한 재조회 뒤에도 다르면 `picker_item_count_mismatch`로 실패시킨다.
- 불완전한 일부 사진을 전체 성공 Story로 확정하지 않는다.

## 5. 재분석 추천의 앨범 목적지 정책

### 기본: 로컬 결과와 exact Story

수동 날짜 재분석은 다음 위치에 결과를 만든다.

1. 촬영일 기준 통합 로컬 추천 보관소에 content hash 중복 방지로 저장
2. 해당 실행의 recommendation collection에 provenance 기록
3. 해당 collection만 표시하는 새 Story 생성
4. Apple/Google의 기존 월별 추천 앨범은 변경하지 않음

따라서 Story를 삭제하고 같은 기간을 재분석해도 과거 Story나 과거 collection을 새 Story에 섞지 않는다.

### 사용자가 외부 앨범 반영을 요청할 때

두 작업을 별도 옵션으로 제공해야 한다.

- `기존 앨범에 새 추천만 추가`: 월별 누적 앨범에 적합하며 기존 사진은 유지한다.
- `새 버전 앨범으로 게시`: 현재 generation의 snapshot을 `2026-09 추천 · v2`처럼 새 album ID에 게시한다. 이전 앨범은 보존하고 앱의 current pointer만 새 앨범으로 전환한다.

정확한 membership 제거와 검증이 아직 없으므로, 재분석 결과를 외부에서 새 집합으로 보여 줄 때의 기본 권고는 `새 버전 앨범으로 게시`다. 기존 앨범이나 provider library의 사진 자체는 자동 삭제하지 않는다.

Google Photos의 membership 제거는 앱이 만든 앨범·미디어에 한정되고 별도 `photoslibrary.edit.appcreateddata` 권한이 필요하다. `albums.batchRemoveMediaItems`는 최대 50개 단위이며 요청 전체가 함께 성공하거나 실패하고, 라이브러리의 미디어 자체가 아니라 앨범 membership만 제거한다. 따라서 권한 확대와 item-level 검증은 current-head 구현 뒤 별도 단계로 둔다.

## 6. 자동 검증 결과

### Python

```text
.venv/bin/pytest -q
954 passed in 13.61s
```

변경된 Python 모듈의 `compileall`과 `git diff --check`도 통과했다.

### Android

```text
JAVA_HOME=/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home \
ANDROID_HOME=/opt/homebrew/share/android-commandlinetools \
ANDROID_SDK_ROOT=/opt/homebrew/share/android-commandlinetools \
./gradlew testDebugUnitTest lintDebug assembleDebug

BUILD SUCCESSFUL
45 actionable tasks: 1 executed, 44 up-to-date
```

Android unit test source는 현재 없어 `NO-SOURCE`이며, Java 컴파일·Lint·debug APK 조립은 통과했다. Gradle 10 이전에 정리해야 할 deprecated feature 경고는 남아 있으나 이번 기능의 빌드 실패는 아니다.

### 운영 앱 반영

- 최신 source로 standalone `PhotosMcp.app`을 다시 패키징했다.
- depth-first ad-hoc 서명, 지정 요구사항, `osxphotos` runtime import, photo-source·photo-ranker vendor smoke를 모두 통과했다.
- `/Volumes/ExtData/02_Services/PhotosMcp/PhotosMcp.app`에 설치하고 `/Applications/PhotosMcp.app` 진입 경로를 유지했다.
- 새 앱 프로세스가 `127.0.0.1:18791`을 수신하며 `/health`의 `status=ok`, `daemon_status=ready`를 확인했다.
- 시작 시 AppleScript·thumbnail probe를 실제 요청 시점까지 미루는 두 항목만 `warning`이며 설치나 분석 엔진 오류가 아니다.
- Tailnet 전용 mobile client를 최신 source로 재기동했다. 인증 없는 loopback capabilities 요청은 정책대로 `403 tailnet_owner_required`를 반환했다.

## 7. 다음 단계

Phase 0은 과거 결과 혼입과 실수로 인한 provenance 손실을 막는 즉시 안전장치다. 최신 추천 집합을 외부 앨범까지 정확히 전환하려면 다음 순서가 남아 있다.

1. recommendation scope·generation·current head 스키마와 결과 집합 hash
2. 기존 96개 group member와 94개 Apple receipt의 read-only dry-run 분류
3. Story revision과 30일 휴지통, 모든 공유 폐기·파생 파일 purge outbox
4. append-only destination attempt와 실제 item state projection
5. 새 버전 앨범 preview·승인·current pointer
6. Apple/Google membership add/remove/verify 정합화
7. 참조 없는 로컬 파일의 quarantine·유예기간·지연 GC

위 단계가 완료되기 전에는 현재 외부 앨범의 기존 사진이나 인물·GPS 원장을 자동 삭제하지 않는다.
