# 2026-08-21 추천 다양성 검토 큐 재준비

## 목적

두 번째 추천이 첫 번째 추천과 시각적으로 중복되는지를 사람 판단으로 다시 검증하기 위해, 현재 보존된 완료 작업에서 개인 검토 큐를 새로 만들었다.

## 기존 자료 점검

기존 개인 검토 큐는 한 개가 140장면 중 116장면 완료와 `duplicate` 4건을 보유했지만, 해당 작업 결과는 작업 기록 정리 과정에서 삭제되어 현재 score·preview와 다시 연결할 수 없었다. 다른 기존 큐도 완료 장면이 없었다. 따라서 과거 label을 새 정책의 근거로 재사용하지 않는다.

## 새 큐

| 항목 | 결과 |
| --- | --- |
| 기반 완료 작업 | 121장 분석 결과 |
| 전체 장면 | 78개 |
| 복수 사진 검토 장면 | 23개 |
| 초기 완료 | 0개 |
| 초기 `duplicate` label | 0개 |
| 저장 위치 | `~/.photos-mcp/validation/recommendation-quality/<job-id>/review-private.json` |
| 저장 권한 | `0600` |

`추천 품질 검토` 창을 열어 첫 장면부터 검토를 시작할 수 있게 준비했다. 각 장면에서 사람 1·2순위를 고르고, 자동 두 번째 추천이 첫 번째 추천과 사실상 같은 사진일 때만 `유사 사진 중복`을 기록한다. 이 개인 큐에는 사진 ID와 로컬 경로가 포함되므로 Git에 추가하지 않는다.

## 판정 기준

- 새 큐의 23장면을 우선 완료한 뒤 비식별 집계를 다시 생성한다.
- 현재 보존 결과만으로 `duplicate` 양성 label 20건을 확보하지 못하면, 과거 삭제 결과를 복구하지 않고 새 500~1,000장 분석 작업에서 추가 장면을 수집한다.
- 최소 20개 양성 label, Top-2 품질 손실 1% 이내, 전체 회귀 통과 전에는 두 번째 추천의 다양성 정책을 운영 점수에 반영하지 않는다.

## 다음 행동

사용자가 이 개인 큐의 23장면을 검토 완료하면 다음 명령으로 비식별 요약을 다시 생성한다.

```bash
PYTHONPATH=src .venv/bin/python scripts/analyze_recommendation_diversity.py \
  --review ~/.photos-mcp/validation/recommendation-quality/<job-id>/review-private.json \
  --database ~/.photos-mcp/runtime/photo-ranker/jobs.db \
  --output /tmp/photos-mcp-recommendation-diversity-summary.json
```
