# 추천 버전·현재 결과와 읽기 전용 진단

- 작성일: 2026-09-10 KST
- 선행 커밋: `9471b7e` (Android Companion 및 삭제·재분석 Phase 0, origin/main 푸시 완료)
- 범위: Phase 1 DB·로컬 파일 진단과 Phase 2 수동 날짜 재분석 버전 관리
- 설계: [삭제·재분석·추천 앨범 수명주기](../../09-roadmap/01-active/14-deletion-reanalysis-and-recommendation-lifecycle-plan-2026-09-10.md)

## 구현 결과

같은 촬영일 범위·소스·선별 모드·한도·캡처 제외 정책을 안정적인 scope로 묶는다. `reanalyze`, 실행 제한 시간, 기기, 실행 순서에 따른 source 배열 순서는 scope를 바꾸지 않는다. 장수와 provider별 예산이 바뀌면 대상 집합이 달라질 수 있어 새 scope가 된다.

수동 실행을 시작할 때 당시 current revision을 기록하고 새 generation을 예약한다. Apple·Google 통합 결과는 provider collection 여러 개를 하나의 generation snapshot으로 묶는다. 기존 설계의 단일 `current_collection_id`는 통합 실행을 표현하지 못하므로 `generation_id`가 복수 collection과 자산 snapshot을 가리키도록 구체화했다.

완료 시 다음 조건을 모두 만족한 전체 재분석만 현재 결과로 전환한다.

- 실행 시작 시 버전이 예약되어 있음
- 사용자가 `reanalyze=true`를 요청함
- 모든 요청 소스가 완료되고 미처리 사진·소스 오류가 없음
- collection의 추천 수, 저장 수, 실제 member 수가 일치함
- 모든 member가 저장 완료된 local asset에 연결됨
- 각 사진의 촬영일이 요청 범위 안에 있음
- Story 사진 집합이 해당 generation의 자산 집합과 일치함
- 시작 시점의 current revision이 완료 시점에도 동일함

이 조건은 요청 한도 안의 결과에 대한 검증이다. 예를 들어 250장 한도로 실행했다면 해당 기간의 전체 라이브러리가 250장 이하라는 뜻은 아니다.

`recommendation_scope_heads`는 현재 generation과 revision을 저장하고, `recommendation_generations`는 각 실행의 범위·버전·시작 revision·상태·복수 collection·자산 snapshot을 저장한다. 처리 완료 이력 삭제는 이 두 테이블을 정리하지 않는다.

## 전환 사례

| 실행 | 결과 | 현재 결과 |
|---|---|---|
| 첫 전체 재분석 | A, B 추천 | A, B |
| 같은 조건 전체 재분석 | B, C 추천 | B, C; 이전 A, B는 archive |
| 부분 실패 또는 timeout | D만 일부 완료 | 기존 B, C 유지 |
| 신규만 찾는 증분 실행 | E 추천 또는 0장 | 기존 B, C 유지; 증분 결과는 실행별 Story |
| 전체 재분석 성공, 추천 0장 | 빈 snapshot | 현재 결과 비움; 과거 archive로 fallback하지 않음 |
| 같은 결과 재분석 | B, C 추천 | 새 분석 provenance로 전환, 이전과 동등한 사진 집합임을 기록 |
| 두 실행이 같은 current revision에서 시작 | 서로 다른 결과 | 먼저 확정된 하나만 전환, 다른 것은 conflict 이력 |

사진 집합이 같아도 최신 분석의 설명·인물·위치 연결을 사용할 수 있도록 최신 generation을 current로 전환한다. `equivalent_to_generation_id`를 별도로 기록해 후속 외부 앨범 동기화에서 중복 게시를 피할 근거로 사용한다. 이번 단계에서는 외부 provider write 자체를 실행하지 않는다.

모바일 홈의 현재 Story는 확정된 current snapshot을 우선 사용한다. 부분 결과가 나중에 생성되어도 기존 확정 Story를 대체하지 않으며, current snapshot이 0장이면 과거 Story를 다시 표시하지 않는다. Story 목록은 기존처럼 실행 이력을 볼 수 있다.

## 새 앨범 계획의 기반

`album_snapshot_preview(repository, scope_id)`는 현재 generation의 사진 수와 결과 hash, 권장 앨범 이름을 반환한다. 예: `2026-09-01 ~ 2026-09-07 추천 · v2`.

이 함수는 read-only 계획이며 외부 앨범을 만들거나 사진을 추가하지 않는다. API 작업 상태에는 `recommendation_version`으로 버전·상태·현재 여부·검증 오류가 제공된다. 앨범 미리보기 화면과 게시 버튼은 다음 구현 범위다.

## 운영 데이터 진단

다음 명령은 SQLite를 `mode=ro`와 `query_only`로 열어 동일 read transaction 안에서 검사한다. `--verify-files`는 관리 보관소의 파일을 읽어 SHA-256도 검증한다.

```sh
.venv/bin/python scripts/audit_recommendation_lifecycle.py --verify-files
```

출력에는 사진 경로, 원격 photo ID, 인물 이름, 정확한 GPS가 포함되지 않는다. 검사한 실측 결과:

| 항목 | 결과 |
|---|---:|
| 사진별 처리 원장 | 408 |
| 기존 recommendation collection | 14 |
| member | 126 |
| 로컬 자산 | 96 |
| 외부/로컬 receipt | 190 |
| 월별 group member | 96 |
| Story | 삭제 상태 7, 표시 중 0 |
| 파일 SHA-256 일치 | 96 |
| 파일 누락·hash 불일치·읽기 실패·경로 이탈 | 각각 0 |
| member/collection/local asset 연결 단절 | 0 |
| 삭제 Story의 활성 공유 | 0 |
| 완료 collection 집계 불일치 | 1 |

집계 불일치 한 건은 저장된 추천·저장 수가 각각 67인데 실제 남은 member는 64인 상태다. 과거 정리 작업의 정확한 원인이 이 집계만으로 확인되지 않으므로 67을 64로 임의 덮어쓰지 않았다. 실제 파일 96개는 모두 정상이며 해당 collection은 자동 current backfill 대상에서 제외한다. 다음 감사에서는 과거 정리 receipt와 비교해 별도 제외·삭제 집계로 설명해야 한다.

추가 확인: 이 collection은 9월 9일 생성된 Google 결과다. 기존 상세 장소·캡처 제외 문서에 같은 날 캡처 3장을 제외한 기록이 있고, 해당 격리 폴더에도 파일 3개가 남아 있다. 수량과 날짜가 일치하므로 그 정리가 원인일 가능성이 높다. 단, 삭제된 member와 격리 파일을 연결하는 개별 receipt까지 복원한 것은 아니므로 진단에서는 집계 차이를 그대로 유지했다.

기존 collection 14개는 scope/current를 추정해 운영 앨범에서 제거하거나 최신 결과로 승격하지 않는다. 새로운 명시적 전체 재분석부터 버전 구조를 사용한다.

이 진단의 제한: Apple/Google 원격 앨범 membership, 별도 인물 DB·GPS 원장, 공유 파생 파일, 날짜 manifest와 물리 파일의 전체 참조 비교는 이 명령의 검사 대상이 아니다. 이전 Phase 0의 인물·GPS 보존 확인과 별도로 후속 감사에서 보강한다.

## 검증

- 전체 Python 테스트: **973 passed in 13.79s**.
- 새 회귀 19개: stable scope, A→B 전환, archive 보존, 부분·실패·증분 제외, 0장 전환, 동등 결과, 두 SQLite connection 경쟁, 재시작·재시도, 누락·범위 오류, 실제 수동 dispatch→reconcile→홈 Story, 작업 이력 삭제 후 버전 보존, 파일 진단·경로 이탈, 트랜잭션 rollback.
- 신규 모듈은 기존 자동 일일 앨범 게시 동작을 바꾸지 않는다.
- Android 0.6.3 (`versionCode=13`) release R8 빌드·Lint·APK v2/v3 서명 검증 통과. 기존 설치 인증서로 서명해 앱 내 등록 정보를 유지한 업데이트를 제공한다.
- 배포 APK: 112,913 bytes (약 110 KiB), SHA-256 `42cadc732ed7b04d975e25aa031b10076ea96622f844355d0757e7d6355eeaa5`.
- Android version 변경 후 모바일 API 테스트 17개 추가 실행, 모두 통과.
- macOS standalone 번들 재생성·코드 서명·osxphotos/photo-source/photo-ranker import smoke 통과. `/Volumes/ExtData/02_Services/PhotosMcp/PhotosMcp.app`에 설치하고 앱과 mobile client를 재기동했다. 운영 health는 `status=ok`, `daemon_status=ready`, `active_job_count=0`.
- 실제 Tailnet APK GET에서 HTTP 200, `PhotosMcp-Album-0.6.3.apk` 다운로드 파일명, 112,913 bytes와 위 SHA-256 일치를 확인했다.
- 실제 사진 재분석/외부 앨범 mutation은 이번 버전 테스트에서 실행하지 않았다. 테스트 DB·가짜 provider 기반 통합 검증과 운영 원장·96개 실파일의 read-only 검증을 구분한다. 운영 generation/current scope는 아직 0개이며, 다음 전체 재분석부터 기록된다.

## 아직 완료하지 않은 범위

- 일일 자동화의 generation 전환과 전체 추천 Story를 current heads로 전환
- 과거 scope의 보수적 backfill, 위 67/64 집계 차이의 원인 기반 복구
- 별도 current 날짜 manifest와 참조 기반 파일 정리
- Story 30일 휴지통과 공유 파생 파일 삭제 outbox
- 새 버전 앨범 preview/승인 UI 및 실제 게시
- 외부 앨범 receipt attempt history, membership add/remove/verify

다음 작업은 기존 데이터의 진단 결과를 유지하면서 새 버전 앨범 게시 계획을 사용자 화면에 연결하고, publisher의 현재 상태와 시도 이력을 분리하는 단계다.
