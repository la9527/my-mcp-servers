# Phase 1.5 사전 검토 실험

이 작업 공간은 사진 추천 Phase 1.5 구현 전에 macOS Vision의 실제 런타임 가용성, 거리 방식의 차이, 처리 비용과 결과 계측 가능성을 검토하기 위한 별도 공간이다.

원본 사진, 미리보기, 사진 ID, 경로, 임베딩, 개별 점수는 저장하거나 Git에 넣지 않는다. `run_preflight.py`가 생성하는 JSON은 집계 수치만 포함한다.

2026-08-03 재검증에서는 Apple 사진 보관함에서 새 사진 100장을 별도 개인 경로에 준비했다. 기존 50장 결과의 사진 ID는 제외했고, 미리보기와 상세 결과는 `$HOME/.photos-mcp/validation/` 아래에만 둔다. 저장소에는 `apple-photos-revalidation-summary.json` 같은 비식별 집계만 남긴다.

## 실행

```bash
./.venv/bin/python experiments/phase1_5_preflight_2026-08-03/run_preflight.py \
  --artifact-dir "$HOME/.photos-mcp/runtime/photo-ranker/artifacts/<local-job-id>" \
  --output experiments/phase1_5_preflight_2026-08-03/results/local-preview-summary.json \
  --source-label local-preview-set
```

### 새 Apple 사진 재검증

아래 준비 명령은 기존 개인 데이터 디렉터리를 덮어쓰지 않는다. 새 세트를 만들 때는 `--output-root`에 새 날짜 경로를 지정하고, 앞선 개인 세트도 `--exclude-private-manifest`로 제외한다. 이 옵션은 반복할 수 있다.

```bash
./.venv/bin/python experiments/phase1_5_preflight_2026-08-03/prepare_private_dataset.py \
  --count 1000 --session-count 200 --max-per-session 5 \
  --exclude-private-manifest "$HOME/.photos-mcp/validation/phase1_5_revalidation_<previous-date>/manifest-private.json" \
  --output-root "$HOME/.photos-mcp/validation/phase1_5_revalidation_<date>"

./.venv/bin/python experiments/phase1_5_preflight_2026-08-03/run_private_revalidation.py \
  --dataset-root "$HOME/.photos-mcp/validation/phase1_5_revalidation_<date>" \
  --summary-output experiments/phase1_5_preflight_2026-08-03/results/apple-photos-revalidation-summary.json
```

`run_private_revalidation.py`는 제품 앱과 같이 `select_best`, 상위 30% 정책을 적용한다. 원격 VLM이 필요할 경우 기존 on-demand Linux 연결 설정을 그대로 사용한다.

1,000장의 상세 VLM 분석은 현재 Linux Qwen 실행 속도 기준으로 약 70분 이상이 걸릴 수 있다. 데이터 준비와 실행을 분리해, 준비 단계에서는 Apple 사진 보관함만 읽고 원격 LLM을 깨우지 않는다.

재검증 실행기는 개인 데이터셋 폴더에 `revalidation-private-checkpoints.sqlite3`를 만든다. 필터와 VLM 단계가 사진 단위로 저장되므로 워크스테이션·네트워크가 중단되면 같은 명령을 다시 실행해 저장된 단계부터 재개한다. Linux 원격 실행에서는 응답이 성공할 때마다 활동 시각도 갱신해, 긴 분석이 30분 유휴 종료로 끊기지 않게 한다.

## 2026-08-03 새 Apple 사진 재검증 결과

| 항목 | 결과 |
| --- | ---: |
| 입력 사진 | 100장 |
| 이전 50장과 중복 | 0장 (준비 단계에서 제외) |
| VLM 상세 분석 완료 | 99장 |
| 장면 묶음 | 83개 |
| 여러 사진 장면 | 12개 |
| 최대 장면 크기 | 5장 |
| 장면 상세 후보 용량 | 99장 |
| 최종 추천 | 27장, 23개 장면 |
| 장면당 최대 추천 | 2장 |
| 총 실행 시간 | 420.61초 |

이 실행에서는 Vision FeaturePrint를 메인 스레드에서 한 번 초기화한 뒤 작업 스레드에 공유했다. 이전 실행에서 보였던 `VNGenerateImageFeaturePrintRequest` 부분 초기화 폴백은 재현되지 않았다.

단, 현재 Python 환경에 `insightface`, `mediapipe`, `face-recognition` 중 어느 것도 설치되어 있지 않아 기존 얼굴 엔진의 얼굴 검출 수는 0으로 기록됐다. 따라서 이 재검증은 장면 군집·VLM·장면당 추천 상한을 검증하지만, 얼굴 기반 완화 규칙이나 얼굴 촬영 품질의 효과를 검증한 것은 아니다. Phase 1.5에서 native Vision 얼굴 품질 계측을 결과 계약에 추가한 뒤 별도로 재검증해야 한다.

## 1,000장 종단 재검증 결과

| 항목 | 결과 |
| --- | ---: |
| 입력 사진 | 1,000장 |
| 장면 묶음 | 776개 |
| 여러 사진 장면 | 150개 |
| 최대 장면 크기 | 8장 |
| VLM 상세 분석 완료 | 986장 |
| 최종 추천 | 293장, 266개 장면 |
| 2장 추천 장면 | 27개 |
| 장면당 최대 추천 | 2장 |
| 종단 실행 시간 | 3,880.91초 (64분 41초) |

첫 1,000장 실행은 Linux 유휴 종료가 약 35분에 워크스테이션을 끄면서 실패했다. 활동 갱신과 개인 체크포인트를 추가한 뒤 다시 실행해 30분 경과 뒤에도 서버가 유지되는 것을 확인했고, 최종 실행은 중단 없이 완료됐다. 원격 VLM의 이벤트 분류는 `travel` 257장으로 치우쳐 있어, 중복 억제와 별개로 프롬프트·분류 기준 보정과 사람 검토가 필요하다.

후속 계약 백필 실행에서는 986개 상세 후보에 `detail_candidate=true`와 장면 내 `detail_candidate_rank` 1~4를 기록했다. 이 단계는 저장된 개인 체크포인트를 재사용해 3.84초에 완료됐고, 이제 사람 라벨이 추가되면 후보 `Recall@4`를 직접 계산할 수 있다.

## 얼굴 촬영 품질 1,000장 계측

`run_private_face_quality_preflight.py`는 Apple Vision 얼굴 촬영 품질을 개인 검증 세트에서 계측하고 비식별 집계만 저장한다.

```bash
./.venv/bin/python experiments/phase1_5_preflight_2026-08-03/run_private_face_quality_preflight.py \
  --dataset-root "$HOME/.photos-mcp/validation/phase1_5_revalidation_2026-08-03-1000" \
  --output experiments/phase1_5_preflight_2026-08-03/results/apple-photos-face-quality-1000-summary.json
```

2026-08-04 실행에서는 1,000장 중 485장, 총 822개 얼굴을 관측했다. 요청 시간 중앙값은 4.73ms, P95는 7.24ms였고, 얼굴 신호가 있는 359개 장면 중 단순 shadow Top-1은 28개 장면에서 달라졌다. 고정 품질 임계값은 사용하지 않는다. 이 값은 보관함 전체의 합격선이 아니라 같은 장면의 후보를 상대 비교하는 신호다.

## 검토 범위

1. `VNGenerateImageFeaturePrintRequest`의 공식 거리와 현재 raw FeaturePrint 코사인 거리의 분포·상관성
2. `VNDetectFaceCaptureQualityRequest`와 `VNCalculateImageAestheticsScoresRequest`의 실제 실행 가능 여부와 요청 시간
3. 현재 결과 산출물이 상세 분석 후보 4장과 `Recall@4` 평가에 필요한 사진별 계측값을 남기는지
4. Phase 1.5 구현에 앞서 고정해야 할 캐시 버전, 폴백, 정답 세트와 롤백 조건

거리 임계값의 실험 결과는 현재 장면 ID를 기준으로 한 **분포 매핑용 대리 지표**다. 현재 장면 ID 자체가 시간·인물·코사인 거리 신호를 이미 사용하므로, 이 수치만으로 공식 거리 방식의 품질 우위를 주장하지 않는다. 최종 선택에는 200~300개 장면의 사람 검토 정답 세트가 필요하다.
## 로컬 장면 검토 도구

사람 검토는 모든 사진을 평가하지 않고, 한 장면에서 가장 좋은 1장과 선택적인 2장을 고르는 방식으로 진행한다. 사진·사진 ID·선택 결과는 모두 개인 검증 디렉터리에만 저장된다.

먼저 다중 사진 장면을 우선 포함한 100개 큐를 생성한다.

```bash
./.venv/bin/python experiments/phase1_5_preflight_2026-08-03/prepare_private_ground_truth_review.py \
  --sample-size 100 \
  --output "$HOME/.photos-mcp/validation/phase1_5_revalidation_2026-08-03-1000/review-ground-truth-private-100.json"
```

그 다음 네이티브 검토 창을 연다.

```bash
./.venv/bin/python experiments/phase1_5_preflight_2026-08-03/review_private_ground_truth_app.py \
  --queue "$HOME/.photos-mcp/validation/phase1_5_revalidation_2026-08-03-1000/review-ground-truth-private-100.json"
```

- 기본 격자는 2열의 큰 미리보기로 표시된다. 각 사진의 `크게 보기`를 누르면 확대·축소·화면 맞춤·전체 화면이 가능한 네이티브 사진 뷰어가 열린다.
- 사진을 클릭하거나 `1`~`9`를 눌러 1순위를 선택한다.
- `Shift+1`~`Shift+9` 또는 각 사진의 `2순위` 버튼으로 선택적인 두 번째 사진을 추가한다.
- `Enter`는 저장 후 다음 장면, 오른쪽 화살표는 현재 장면 건너뛰기, 왼쪽 화살표는 이전 장면이다.
- 결과는 매 장면마다 원자적으로 저장되며, 앱을 닫은 뒤 다시 열면 첫 미검토 장면에서 이어진다.

512px 미리보기로 표정·초점을 정확히 판단하기 어려우면, 동일한 100개 장면의 251장만 2048px로 다시 내보내 별도 큐에서 검토한다. 기존 선택은 보존되며, 고해상도 큐의 선택은 의도적으로 비어 있는 상태에서 시작한다.

```bash
./.venv/bin/python experiments/phase1_5_preflight_2026-08-03/prepare_private_high_resolution_review.py
./.venv/bin/python experiments/phase1_5_preflight_2026-08-03/review_private_ground_truth_app.py \
  --queue "$HOME/.photos-mcp/validation/phase1_5_revalidation_2026-08-03-1000-review-hd2048/review-ground-truth-private-100-hd2048.json"
```
