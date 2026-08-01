# Linux VLM 비교 벤치마크

검증일: 2026-08-01

## 결론

`photos-mcp`의 사진 분류 기본 모델은 현재 Linux 워크스테이션의 `Qwen3.6-35B-A3B-Q4_K_M`을 유지한다. `Qwen3-VL-30B-A3B-Q4_K_M`은 더 빠르지만, 이번 Photos 미리보기 묶음에서 식사, 야외, 가족 셀카의 이벤트 분류를 `daily` 또는 `portrait`로 단순화하는 사례가 확인됐다.

`Qwen3-VL-30B-A3B`은 대량 처리의 후보로 남긴다. 다만 기본 모델 전환은 더 다양한 정답 라벨 세트에서 정확도 우위가 재현될 때만 검토한다.

## 범위와 조건

- 입력: Apple Photos에서 이미 생성된 2026-05-29 미리보기 26장. 원본 사진, 이미지 바이트, 개별 응답은 개인 정보이므로 Git에 저장하지 않는다.
- API: Linux `llama.cpp` OpenAI 호환 `/v1/chat/completions`, `image_url` data URI 입력.
- GPU: AMD Radeon 8060S Graphics, Vulkan RADV.
- 모델: 모두 Q4_K_M GGUF와 대응 `mmproj`, `--n-gpu-layers 99`, `--flash-attn on`, `--image-min-tokens 1024`, `--reasoning off`.
- 컨텍스트: 공정 비교를 위해 두 모델 모두 16K로 고정했다. 운영 기본 서버의 200K 컨텍스트 성능을 측정한 결과는 아니다.
- 프롬프트: `scene`, `people_count`, `is_family_photo`, `expressions`, `event_type`, `event_confidence`, `quality_notes`, `meaningful_score`를 포함하는 JSON 하나만 요구했다.

## 결과

| 모델 | 이미지 | 성공 | JSON 계약 | 평균 지연 | P95 지연 | 생성 처리량 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Qwen3.6-35B-A3B | 26 | 26/26 | 26/26 | 6.010초 | 6.257초 | 57.565 tok/s |
| Qwen3-VL-30B-A3B | 26 | 26/26 | 26/26 | 4.350초 | 4.584초 | 77.667 tok/s |

Qwen3-VL은 평균 지연시간이 약 27.6% 짧고 생성 처리량은 약 34.9% 높았다. 두 모델 모두 API 안정성과 구조화 출력 계약은 통과했다.

## 품질 판정

이 묶음에는 해변 산책, 공원 가족 셀카, 카페에서 아이가 음료를 마시는 장면이 반복적으로 포함된다. `photo-ranker`의 분류 규칙상 음식 또는 음료가 주 피사체면 `meal`, 자연 풍경 또는 야외 가족 장면이면 `outdoor`가 우선이다.

- Qwen3.6은 음료가 중심인 7장을 `meal`로, 공원 가족 셀카 5장을 `outdoor`로 분류했다. 장면 설명에도 인물, 음료, 해변, 데크 같은 구체적 단서가 대체로 보존됐다.
- Qwen3-VL은 같은 음료 장면 중 5장을 `daily`로, 공원 가족 셀카 일부를 `portrait` 또는 `daily`로 분류했다. 장면 설명은 대체로 사실이지만 인물 수나 가족 단서가 빠지는 경우가 있었다.
- 이전 `photo-ranker` 실행 결과의 비-`other` 이벤트 22건은 정답 라벨이 아니라 참고용 기존 출력이다. 이 참고 결과와의 일치 수는 Qwen3.6 10건, Qwen3-VL 3건이었다. 따라서 이 수치는 품질 보조 신호일 뿐 정확도로 해석하지 않는다.

## 장면 서술 시각 검토

모델의 JSON 형식 준수와 이벤트 라벨만으로 장면 이해 품질을 단정하지 않는다. 실제 미리보기를 보면서 서로 다른 두 모델의 장면 서술을 대조한 표적 검토도 수행했다.

- 카페에서 아이가 병 음료를 마시는 사진: 두 모델 모두 장면을 사실적으로 서술했다. Qwen3.6의 `meal`은 분류 규칙에 맞았고, Qwen3-VL의 `daily`는 핵심 행동을 덜 반영했다.
- 공원 가족 셀카: Qwen3.6은 가족, 3명, 야외를 서술했다. Qwen3-VL은 구조화 필드의 인물 수와 가족 여부는 맞췄지만 장면 문장에서는 나무가 많은 공원이라는 배경만 말해 핵심 피사체가 누락됐다.
- 해변 데크의 어린이: 두 모델 모두 해변, 나무, 데크, 어린이를 사실적으로 서술했다. 이벤트는 자연 야외 기준상 Qwen3.6의 `outdoor`가 더 적합했다.
- 어두운 실내에서 창밖으로 본 일몰: 두 모델 모두 사실적인 장면 서술을 했고, Qwen3-VL의 보수적인 `other`가 `travel`보다 규칙에 더 맞았다.

이 검토는 의도적으로 불일치 사례 4장을 뽑은 표적 점검이므로 전체 정확도 수치가 아니다. 장면 서술의 강점은 Qwen3.6이 더 풍부한 피사체 설명을 제공한다는 점이고, Qwen3-VL의 강점은 모호한 풍경에서 과도한 이벤트 추론을 덜 한다는 점이다.

비공개 검토 파일을 집계한 결과는 아래와 같다. 사실성은 두 모델 모두 4/4였으나, 핵심 피사체 포착도는 Qwen3.6이 2.00/2, Qwen3-VL이 1.75/2였다. 이벤트 정확성은 Qwen3.6이 3/4, Qwen3-VL이 1/4였다. Qwen3.6은 일몰 사진에서 해변과 여행을 다소 과도하게 추론해 근거 없는 세부 주장 없음 비율이 3/4였고, Qwen3-VL은 4/4였다.

향후에는 `scripts/review_vlm_descriptions.py`로 비공개 검토 템플릿을 만든다. 검토자는 각 응답에 대해 사진 사실성, 핵심 피사체 포착도, 이벤트 정확성, 근거 없는 주장을 점수화하고 집계를 생성한다. 검토 파일에는 개인 사진 관련 서술이 포함될 수 있으므로 Git에 커밋하지 않는다.

## 재현 방법

`scripts/benchmark_openai_compat_vlm.py`는 사진 원본을 복사하지 않고 지정한 이미지 경로를 OpenAI 호환 endpoint에 전송해 집계 JSON을 만든다.

```bash
python3 scripts/benchmark_openai_compat_vlm.py \
  --api-base http://127.0.0.1:18083/v1 \
  --model /path/to/model.gguf \
  --images-file /path/to/image-list.txt \
  --output /tmp/photos-mcp-vlm-benchmark.json
```

원격 Linux 서버를 Mac에서 시험할 때는 endpoint를 외부에 노출하지 말고 SSH 로컬 포워딩을 사용한다.

```bash
ssh -N -L 18083:127.0.0.1:8083 la9527@linux-workstation
```

장면 품질 검토 템플릿과 집계는 아래처럼 생성한다.

```bash
python3 scripts/review_vlm_descriptions.py \
  --result qwen36=/tmp/qwen36.json \
  --result qwen3vl=/tmp/qwen3vl.json \
  --write-template /tmp/private-vlm-review.json

# 사진을 보며 /tmp/private-vlm-review.json의 점수를 채운 뒤 실행한다.
python3 scripts/review_vlm_descriptions.py \
  --review-file /tmp/private-vlm-review.json
```

## 운영 적용 전 조건

1. `PHOTO_RANKER_VLM_BACKEND=openai_compat`로 전환한다.
2. Mac의 `photos-mcp`가 Linux loopback endpoint를 호출할 수 있도록 SSH 터널 또는 제한된 LAN 프록시를 둔다.
3. 원격 깨우기와 유휴 종료를 연동할 경우 VLM 요청도 Linux LLM 활동으로 기록한다.
4. 기본 모델 변경 전에는 생일, 졸업, 식사, 인물, 야외, 여행을 균형 있게 포함한 정답 라벨 세트로 재시험한다.
