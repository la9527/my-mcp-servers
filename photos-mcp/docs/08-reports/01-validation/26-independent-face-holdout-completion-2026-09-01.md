# 독립 얼굴 holdout 완료와 readiness 재집계

## 결론

독립 복수 지지 병합 holdout 5쌍의 사람 검토가 모두 완료됐다. 결과는 `같은 사람 5`, `다른 사람 0`, `판단 어려움 0`, `잘못 검출 0`이며 사람 라벨 잔여는 0건이다.

사람 검토 완료는 운영 승격을 뜻하지 않는다. 독립 표본 5건에서 관측 오병합은 0건이지만 Wilson 95% 상한은 43.45%로 운영 허용 상한 5%보다 높다. readiness는 계속 `shadow_only`, `operational_ranking_changed=false`를 유지한다.

## 재집계 결과

| 항목 | 결과 |
| --- | ---: |
| 후보 병합 | 9 |
| 판독 불가 제외 | 4 |
| 검토 가능 독립 쌍 | 5 |
| 같은 사람 | 5 |
| 다른 사람 | 0 |
| 미검토 | 0 |
| 관측 오병합률 | 0.00% |
| Wilson 95% 상한 | 43.45% |
| 운영 허용 상한 | 5.00% |
| 사람 증거 완료 | 예 |
| 운영 승격 가능 | 아니요 |

독립 holdout 자체의 Wilson 상한을 5% 이하로 낮추려면 오병합이 추가되지 않는다는 가정에서도 총 73건이 필요하다. 현재 5건 기준 추가 68건이 필요하다. 기존 primary audit 17건과 단순 합산하면 22건 무오류이지만, readiness는 보정·선택에 사용된 primary audit과 독립 holdout을 별도 gate로 유지한다.

## 집계 상태 교정

기존 readiness는 holdout의 `promotion_ready=false`를 곧바로 `사람 라벨 대기`로 해석했다. 이 조건은 다음 두 상태를 혼동했다.

1. 사람이 아직 검토하지 않은 상태
2. 사람 검토는 끝났지만 통계 표본이 부족한 상태

집계 로직을 다음과 같이 분리했다.

- 미검토 또는 unresolved 라벨이 있으면 `independent_holdout_human_evidence_pending`
- 사람 검토가 끝났지만 Wilson 신뢰가 부족하면 `independent_holdout_statistical_confidence_insufficient`
- `human_evidence.complete`, `required`, `remaining_holdout_pairs`는 실제 사람 검토 상태만 반영
- 운영 판정과 추천 순위 변경 여부는 기존 안전 정책을 유지

전체 원본 `results.json`은 이전 산출물 정리 이후 남아 있지 않다. 기존 aggregate의 다른 비식별 증거를 보존하면서 독립 holdout 요약만 교체하도록 명시적 `--existing-aggregate` 경로를 추가했다. 이 옵션을 지정하지 않으면 기존 전체 재생 경로를 그대로 사용하므로 stale 결과를 자동 추정하지 않는다.

```bash
.venv/bin/python scripts/run_person_shadow_readiness.py \
  --job-id f5d85ba2 \
  --existing-aggregate ~/.photos-mcp/validation/person-aware-scene-shadow/f5d85ba2/readiness-aggregate.json \
  --output ~/.photos-mcp/validation/person-aware-scene-shadow/f5d85ba2/readiness-aggregate.json
```

## 현재 차단 사유

- `single_threshold_calibration_failed`
- `primary_grouping_audit_not_promotion_ready`
- `independent_holdout_statistical_confidence_insufficient`
- `strict_veto_sample_below_100`
- `strict_veto_top1_gain_below_5pp`

사람 라벨 대기 blocker는 제거됐다. 다만 단일 threshold 보정 실패, primary grouping 통계 부족, 독립 holdout 통계 부족, strict veto 표본 87개, Top-1 개선 +1.1495%p는 그대로이므로 운영 추천 정책은 변경하지 않는다.

## 검증

```text
person shadow readiness 및 grouping review 표적 테스트: 9 passed
전체 Python 회귀 테스트: 654 passed
Python compileall: 통과
git diff --check: 통과
로컬 readiness aggregate 갱신: 완료
사람 라벨 잔여: 0
운영 추천 순위 변경: 없음
```

## 개인정보 경계

- 비공개 review 원본은 Git에 추가하지 않았다.
- 공개 보고서와 aggregate에는 사진 ID, 파일 경로, 얼굴 crop, embedding이 없다.
- 재집계는 사진 원본과 Photos 라이브러리를 읽거나 변경하지 않았다.
- 비공개 holdout 파일의 기존 owner-only 권한은 유지했다.

## 다음 판단

인물 기반 추천을 계속 개발할 경우에만 다른 인물 전환과 경계 장면 중심의 독립 merge audit을 추가한다. 단순히 표본 수를 채우기 위한 편향된 쌍은 승격 근거로 사용하지 않는다. 이 기능을 우선하지 않으면 현행 `shadow_only` 정책과 aggregate를 보존하고 추가 개인 사진 검토는 진행하지 않는다.
