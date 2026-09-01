# 활성 로드맵 자동 구현·회귀 검증

## 목적

사람이 직접 수행해야 하는 OAuth 동의, Google Photos 사진 선택과 얼굴 holdout 판정을 제외하고 활성 로드맵의 자동화 가능한 기반을 구현·검증했다. 기존 운영 추천 순위와 Apple·로컬 사진 기능은 승격 조건을 통과하지 않은 shadow 결과로 변경하지 않는다.

## 구현 결과

| 영역 | 구현·검증 결과 | 운영 상태 |
| --- | --- | --- |
| 얼굴 동일인·추천 readiness | 보정, 이중 임계값, grouping audit, strict veto와 holdout을 집계 전용 pipeline으로 통합 | `shadow_only` |
| Google OAuth | PKCE, state/callback 검증, refresh, Keychain, incremental scope와 production 조립 | 실제 계정 연결 대기 |
| Google Picker | REST create/poll/page/delete, pagination, timeout/cancel, bounded cache와 분류 bridge | 자동 계약 검증 완료 |
| Google 결과 업로드 | 작업 귀속 원본 lease, 명시적 승인, album 생성, resumable upload, batchCreate, receipt | 실제 계정 확인 대기 |
| Google AppKit | Apple·로컬·Google 3개 source, 연결·선택·분류 단계, 결과 업로드 확인 | controller 검증 완료 |
| 동기화 목적지 | 승인 root, atomic copy, hash 검증, 충돌 회피와 상태 영수증 | 로컬 복사 완료와 cloud 확인 분리 |
| 선호 학습 | 사진 식별자가 없는 집계 feature 저장과 정규화 shadow 모델 | 운영 점수 미반영 |
| 인물 개인화 | 동의·확인 identity·holdout gate | 자동 승격 차단 |

## 얼굴 추천 실측

작업 `f5d85ba2`의 기존 비식별 검증 산출물을 이용해 다음 명령을 실행했다.

```bash
.venv/bin/python scripts/run_person_shadow_readiness.py \
  --job-id f5d85ba2 \
  --output ~/.photos-mcp/validation/person-aware-scene-shadow/f5d85ba2/readiness-aggregate.json
```

| 지표 | 결과 |
| --- | ---: |
| 동일 주 피사체 장면 | 91 |
| strict veto 비교 가능 장면 | 87 |
| 기존 Top-1 일치율 | 74.71% |
| strict veto Top-1 일치율 | 75.86% |
| 순개선 | 1건, +1.1495%p |
| 독립 holdout 사람 라벨 잔여 | 0쌍, 2026-09-01 완료 |
| 독립 holdout 결과 | 같은 사람 5, 오병합 0, Wilson 95% 상한 43.45% |

최종 판정은 `shadow_only`이며 `operational_ranking_changed=false`다. 독립 holdout 사람 검토는 완료됐지만 5건만으로는 통계 상한이 충분하지 않다. 단일 threshold는 승격 기준을 충족하지 못했고, grouping audit도 오류 0건이지만 표본 17개라 통계 상한이 충분하지 않다. strict veto 역시 100개 표본과 +5%p 개선 조건에 미달했다.

## 보안과 데이터 경계

- Google refresh token은 macOS Keychain repository만 사용하며 로그와 작업 결과에 기록하지 않는다.
- Picker base URL은 영구 저장하지 않고 만료 가능한 작업 lease로만 사용한다.
- Google SQLite 상태 파일은 `0600`, 임시 cache 디렉토리는 `0700` 권한을 적용한다.
- Google 입력에서는 얼굴 군집·인물 식별·개인 선호 학습을 실행하지 않는다.
- 결과 업로드는 사용자가 체크한 사진만 새 앱 생성 album에 사본으로 추가하며 기존 원본과 기존 album을 수정하지 않는다.
- 동기화 adapter는 OS 동기화 root까지 복사된 사실과 실제 cloud 전파 완료를 구분한다.
- 선호 shadow 저장소는 사진 ID·경로·얼굴 crop·embedding을 저장하지 않는다.

## 자동 회귀 검증

```bash
git diff --check
.venv/bin/python -m compileall -q src scripts
.venv/bin/python -m pytest -q
```

결과는 `556 passed in 4.61s`이며 compile과 diff whitespace 검사도 통과했다.

## 사용자 확인이 필요한 잔여 항목

다음은 구현 누락이 아니라 외부 계정 또는 사람 판단이 필요한 검증이다.

1. Google Cloud OAuth client·consent screen·test user 설정과 최초 동의
2. 실제 Picker에서 1장·10장 선택, 취소·만료·재연결 확인
3. 실제 새 Google Photos album 생성, 원본 비변경, 저장공간·위치 metadata 고지 확인
4. iCloud Drive 등 실제 목적지에서 OS가 cloud 전파를 완료했는지 확인

독립 얼굴 holdout 5쌍의 동일인 여부 판정은 2026-09-01 완료했다. 나머지 항목이 완료되기 전에는 Google 실계정 기능을 완전 검증으로 표시하지 않는다. 얼굴 shadow는 사람 검토 완료와 별개로 통계·정확도 gate를 통과하기 전까지 운영 추천에 승격하지 않는다.
