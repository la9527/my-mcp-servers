# 인물 구성 라벨링·SFace 보정 기반 검증

## 결론

기존 추천 품질 검토 큐를 버전 2로 확장해, 사진 선택 검토와 별도로 인물 구성 라벨을 수집할 수 있게 했다. 현재 개인 검토 큐에는 인물 구성 라벨이 아직 없으므로 SFace cosine 기준과 주요 얼굴 면적 비율은 확정하지 않는다. 운영 장면 분리와 추천 순위는 그대로 유지한다.

## 구현

결과 화면에 `인물 구성 검토` 버튼을 추가했다. 전용 창은 기존 사진 격자와 전체 화면 뷰어를 그대로 사용하며, 사람의 이름이나 관계를 입력받지 않는다.

| 라벨 | 의미 | 보정 사용처 |
| --- | --- | --- |
| 주요 인물이 모두 같음 | 같은 주요 피사체의 연속 사진 | 과분리율 측정 |
| 주요 인물 구성이 다름 | 서로 다른 인물 또는 구성 | 미분리율 측정 |
| 배경 행인만 다름 | 주 피사체는 같고 작은 배경 얼굴만 변함 | 주요 얼굴 면적 비율 보정 |
| 얼굴 검출이 어려움 | 얼굴 기반 판정에 사용하지 않음 | 실패율 분리 |
| 판단 보류 | 기준선에서 제외 | 불확실성 보존 |

라벨은 기존 `review-private.json`에만 저장한다. 기존 1·2순위, 장면 경계와 실패 이유는 유지되며, v1 큐를 열면 v2로 원자적으로 갱신한다. 개인 큐 파일은 계속 `0600`이고 공개 집계에는 사진 ID, 경로, 얼굴 box와 embedding을 포함하지 않는다.

## 보정 규칙

`scripts/analyze_person_composition_calibration.py`는 아래 후보를 모두 재생한다.

| 항목 | 후보 |
| --- | --- |
| SFace cosine | 0.363, 0.40, 0.45, 0.50, 0.55 |
| 주요 얼굴 상대 면적 | 0.30, 0.50, 0.70 |

각 후보는 같은 주요 인물 장면을 하나로 유지하는 정확도와 다른 주요 인물 장면을 나누는 재현율의 균형 정확도로 비교한다. 라벨이 각각 30개 이상인 상태는 `sample_ready`일 뿐이며, 운영 후보 추천은 다음 조건을 모두 충족해야 한다.

| 승격 보정 기준 | 최소값 | 목적 |
| --- | ---: | --- |
| 같은 주요 인물 정확도 | 90% | 같은 연속 사진을 과도하게 나누지 않음 |
| 다른 주요 인물 분리 재현율 | 95% | 다른 사람을 같은 그룹으로 잘못 합치는 오류를 5% 이하로 제한 |

두 품질 기준을 통과하지 못하면 `recommended_candidate`를 비워 두고 `promotion_ready: false`를 반환한다. 즉, 라벨 수만 채운 임의 데이터나 편향된 데이터가 운영 기준값으로 승격될 수 없다.

## 실제 실행 확인

2026-08-11에 실제 1,000장 작업의 개인 검토 큐와 314장 얼굴 계측 캐시를 사용해 CLI를 실행했다.

| 항목 | 결과 |
| --- | ---: |
| 기존 완료 장면 | 116 |
| 기존 건너뜀 장면 | 24 |
| 인물 구성 라벨 | 0 |
| 보정 평가 장면 | 0 |
| 추천 임계값 | 없음 |
| 운영 승격 가능 | 아니오 |

인물 구성 라벨이 없을 때도 출력은 비식별 aggregate JSON이며, `promotion_ready: false`를 반환했다. 빈 표본을 근거로 임계값을 고정하지 않는 것을 확인했다.

### 임의 라벨 게이트 시험

실제 개인 검토 파일은 변경하지 않고 임시 복사본에 같은 주요 인물 35장면, 다른 주요 인물 35장면, 배경 행인만 다름 10장면, 얼굴 검출 어려움 60장면을 임의로 부여해 전체 흐름을 다시 실행했다. 이 결과는 UI와 안전 게이트 시험용이며, 운영 설정과 실제 사용자 라벨에는 반영하지 않는다.

| 항목 | 결과 |
| --- | ---: |
| 보정 평가 장면 | 80 |
| 표본 수 충족 | 예 |
| 최고 관측 균형 정확도 | 46.98% |
| 같은 주요 인물 정확도 | 71.11% |
| 다른 주요 인물 분리 재현율 | 22.86% |
| 운영 후보 추천 | 없음 |
| 승격 가능 | 아니오 |

최고 관측 후보는 cosine `0.363`, 주요 얼굴 상대 면적 `0.70`이었으나, 사람을 다르게 분리하는 재현율이 95% 기준에 크게 못 미쳤다. 따라서 표본 수만으로 승격하지 않고 `shadow_only`를 유지하는 것을 확인했다.

같은 입력의 사람 구성 shadow 재생은 계측 314장을 모두 개인 캐시에서 재사용했고 0.507초에 완료됐다. 얼굴 중심 순위는 cosine `0.363`, `0.450`, `0.550` 모두에서 기존 사람 Top-1보다 낮았다. 승격 게이트는 `shadow_only`를 반환했으며, 차단 사유는 인물 구성 라벨 부족과 전체 Top-2 손실 회귀 미측정이다.

## 자동 회귀

```text
.venv/bin/pytest -q
503 passed in 4.40s
```

추천 품질 검토·인물 구성 검토·결과 갤러리 AppKit 테스트, 내보내기 계약과 문서 구조 검사를 포함한다. 결과 창이 최소 폭일 때 새 검토 버튼과 내보내기 버튼이 두 줄로 재배치되는 시나리오도 자동 검증했다.

## Standalone 번들

기존 사용 중인 설치 앱을 바꾸지 않고 임시 경로에서 framework standalone 번들을 다시 만들었다.

| 검사 | 결과 |
| --- | --- |
| bundle 생성 | `/tmp/photos-mcp-standalone-20260811-dist/PhotosMcp.app` |
| bundle 크기 | 540MB |
| 코드 서명 | `codesign --verify --deep --strict` 통과 |
| AppKit 인물 구성 모듈 | 번들 내부 포함 확인 |
| `--runtime-import-smoke` | `osxphotos` 통과 |
| `--vendor-runtime-smoke` | `photo-source`, `photo-ranker-vision` 통과 |

임시 번들만 검증했으며 기존 설치본은 변경하지 않았다.

### 보정 게이트 재빌드

보정 품질 게이트를 추가한 뒤에도 설치 앱을 변경하지 않고 별도 임시 경로에서 standalone을 재빌드했다. 번들 내부에 `person_composition_calibration.py`, `person_promotion.py`, 인물 구성 검토 AppKit controller가 모두 포함된 것을 확인했다.

| 검사 | 결과 |
| --- | --- |
| bundle 생성 | `/tmp/photos-mcp-standalone-20260811-gate-dist/PhotosMcp.app` |
| bundle 크기 | 547MB |
| 코드 서명 | `codesign --verify --deep --strict` 통과 |
| `--health` | 통과 |
| `--runtime-import-smoke` | `osxphotos` 통과 |
| `--vendor-runtime-smoke` | `photo-source`, `photo-ranker-vision` 통과 |

`--health` 출력의 기본 설치 경로는 실행 설정값을 표시할 뿐이다. 실제 검증 대상은 위 임시 bundle이며 `~/Applications/PhotosMcp.app`은 복사하거나 교체하지 않았다.

## 재현

```bash
PYTHONPATH=src .venv/bin/python scripts/analyze_person_composition_calibration.py \
  --review ~/.photos-mcp/validation/recommendation-quality/f5d85ba2/review-private.json \
  --measurements ~/.photos-mcp/validation/person-aware-scene-shadow/f5d85ba2/measurements-private.json \
  --output /tmp/photos-mcp-person-composition-calibration.json
```

## 다음 조건

1. `인물 구성 검토`에서 같은 주요 인물 30장면과 다른 주요 인물 30장면을 실제 사진 기준으로 라벨링한다.
2. 균형 정확도와 개별 과분리·미분리 사례, 같은 주요 인물 정확도 90%와 다른 주요 인물 분리 재현율 95%를 확인한다.
3. 최고 후보가 승격 조건을 만족할 때만 장면 분리 shadow를 다시 실행한다.
4. 추천 순위·내보내기·1,000장 회귀를 다시 검증한 뒤에만 운영 변경을 검토한다.
