# Phase 1.5 사전 검토 실험

이 작업 공간은 사진 추천 Phase 1.5 구현 전에 macOS Vision의 실제 런타임 가용성, 거리 방식의 차이, 처리 비용과 결과 계측 가능성을 검토하기 위한 별도 공간이다.

원본 사진, 미리보기, 사진 ID, 경로, 임베딩, 개별 점수는 저장하거나 Git에 넣지 않는다. `run_preflight.py`가 생성하는 JSON은 집계 수치만 포함한다.

## 실행

```bash
./.venv/bin/python experiments/phase1_5_preflight_2026-08-03/run_preflight.py \
  --artifact-dir "$HOME/.photos-mcp/runtime/photo-ranker/artifacts/<local-job-id>" \
  --output experiments/phase1_5_preflight_2026-08-03/results/local-preview-summary.json \
  --source-label local-preview-set
```

## 검토 범위

1. `VNGenerateImageFeaturePrintRequest`의 공식 거리와 현재 raw FeaturePrint 코사인 거리의 분포·상관성
2. `VNDetectFaceCaptureQualityRequest`와 `VNCalculateImageAestheticsScoresRequest`의 실제 실행 가능 여부와 요청 시간
3. 현재 결과 산출물이 상세 분석 후보 4장과 `Recall@4` 평가에 필요한 사진별 계측값을 남기는지
4. Phase 1.5 구현에 앞서 고정해야 할 캐시 버전, 폴백, 정답 세트와 롤백 조건

거리 임계값의 실험 결과는 현재 장면 ID를 기준으로 한 **분포 매핑용 대리 지표**다. 현재 장면 ID 자체가 시간·인물·코사인 거리 신호를 이미 사용하므로, 이 수치만으로 공식 거리 방식의 품질 우위를 주장하지 않는다. 최종 선택에는 200~300개 장면의 사람 검토 정답 세트가 필요하다.
