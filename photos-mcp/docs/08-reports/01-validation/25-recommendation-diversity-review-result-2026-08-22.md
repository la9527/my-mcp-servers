# 2026-08-22 추천 다양성 사람 검토 결과

## 범위

보존된 완료 작업의 복수 사진 장면 23개를 대상으로, 자동 두 번째 추천이 첫 번째 추천과 사실상 중복되는지 개인 검토로 확인했다. 개인 검토 입력에는 사진 식별자와 경로가 포함되므로 이 문서와 Git에는 집계 결과만 기록한다.

## 검토 결과

| 항목 | 결과 |
| --- | ---: |
| 완료 장면 | 23개 |
| 분석한 사진 | 66장 |
| 자동 두 번째 추천 | 7개 |
| `duplicate` 판단 | 1개 |
| 사람 2순위 선택 | 0개 |
| 미리보기 누락 | 0개 |
| 특징 추출 | Apple Vision FeaturePrint |

## 재현성 보정

초기 shadow 집계는 `우수 사진 선별` 작업의 전역 추천 점수 경계를 반영하지 않아, 추천 대상이 아닌 장면까지 1순위로 재현했다. 실행 당시 저장된 `scene_recommendation_min_score`를 읽어 장면별 점수 차이 및 시각적 거리와 함께 적용하도록 분석 스크립트를 보정했다.

| 항목 | 보정 전 | 보정 후 |
| --- | ---: | ---: |
| 실행 당시 추천 점수 경계 | 미반영 | 62.3점 |
| 기준선 재현 불일치 | 16개 장면 | 0개 장면 |

이 보정은 운영 추천 정책을 변경하지 않고, 검증 도구가 실제 저장된 추천 결과를 정확히 replay하도록 한 것이다.

## Shadow 비교

`Vision 0.027` 후보는 중복으로 판단된 두 번째 추천 1개를 제외했고, 1순위 회수율 변화는 없었다. 다만 승격에 필요한 명시적 중복 label 20개에 미달하므로 운영 임계값은 변경하지 않는다.

| 항목 | 현재 정책 | Vision 0.027 |
| --- | ---: | ---: |
| 두 번째 추천 | 7개 | 6개 |
| 중복 두 번째 추천 | 1개 | 0개 |
| 1순위 회수율 | 0.3913 | 0.3913 |
| 변경 장면 | 0개 | 1개 |
| 승격 결과 | - | 표본 부족 |

## 결론과 다음 수집

- 현재 두 번째 추천 정책을 유지한다.
- 새 500~1,000장 작업에서 복수 사진 장면과 `duplicate` 판단을 추가로 수집한다.
- 양성 label 20개 이상, 재현 불일치 0, 1순위 회수율 저하 1% 이내를 모두 만족할 때만 후보 정책을 운영에 반영한다.

## 검증 명령

```bash
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/test_recommendation_diversity_analysis.py -q

PYTHONPATH=src .venv/bin/python scripts/analyze_recommendation_diversity.py \
  --review ~/.photos-mcp/validation/recommendation-quality/<job-id>/review-private.json \
  --database ~/.photos-mcp/runtime/photo-ranker/jobs.db \
  --output /tmp/photos-mcp-recommendation-diversity-summary.json
```
