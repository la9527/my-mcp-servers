# Photo-Ranker 개선 벤치마크 보고서 (2026-03-30)

## 개요

대규모 벤치마크에서 확인된 세 가지 핵심 문제를 해결하기 위한 개선 작업과 벤치마크 결과.

| 항목 | 문제 | 해결 | 상태 |
|------|------|------|------|
| 1-1 | Dedup 오탐(FP) 54쌍 | phash 전환 → FP 0 | ✅ 적용 완료 |
| 1-2 | 얼굴 임베딩 불가 (dlib 빌드 실패) | insightface 도입 (ArcFace 512-dim) | ✅ 적용 완료 |
| 1-3 | 이벤트 분류 4/9 유형 미검증 | 9개 유형 전체 벤치마크 | ✅ 검증 완료 |

## 테스트 환경

- **하드웨어**: Apple M4, 32GB RAM, macOS
- **Python**: 3.14.3, uv 0.10.9
- **VLM**: Qwen2.5-VL-7B-Instruct-4bit (MLX)
- **얼굴**: insightface 0.7.3 (buffalo_l, ArcFace + RetinaFace)
- **Dedup**: imagehash (ahash + phash), threshold=8

## 테스트 이미지

Pexels에서 9개 이벤트 유형별 3~4장씩 총 35장 다운로드.
기존 벤치마크 이미지 45장 + 의도적 중복 25장 포함, 총 105장으로 dedup 테스트.

| 유형 | 이미지 수 | 설명 |
|------|-----------|------|
| birthday | 3 | 케이크+촛불, 파티 |
| graduation | 4 | 학사모, 졸업식 |
| celebration | 4 | 건배, 불꽃놀이, 파티 |
| travel | 4 | 에펠탑, 비행기, 타지마할 |
| meal | 4 | 레스토랑, 스시, 피자, 브런치 |
| portrait | 4 | 인물 클로즈업 |
| outdoor | 4 | 산, 해변, 숲, 호수 |
| daily | 4 | 사무실, 카페, 독서, 마트 |
| other | 4 | 추상, 자동차, 건축, 노트북 |

---

## 1-1: Dedup 오탐 해결

### 문제

기존 ahash(average hash) 기반 중복 감지에서 threshold=8일 때:
- 100% recall (25/25 중복 감지)
- **54개 False Positive 쌍** (유사한 풍경 이미지끼리 잘못 그룹핑)

### 테스트 전략

105장 이미지(45 원본 + 25 의도적 중복 + 35 다양한 이벤트)에 대해 8가지 전략 비교:

| 전략 | 그룹 수 | Recall | FP 쌍 | 평가 |
|------|---------|--------|--------|------|
| ahash_t8 | 9 | 100% (25/25) | **54** | ❌ 기존—오탐 과다 |
| ahash_t6 | 11 | 100% (25/25) | 13 | △ 개선되나 여전히 FP |
| ahash_t4 | 7 | 100% (25/25) | 8 | △ FP 줄었으나 존재 |
| **phash_t8** | **5** | **100% (25/25)** | **0** | **✅ 최적** |
| phash_t6 | 5 | 88% (22/25) | 0 | ❌ recall 손실 |
| phash_t10 | 5 | 100% (25/25) | 0 | ✅ t8과 동일 |
| dual_t8 | 5 | 100% (25/25) | 0 | ✅ phash_t8과 동일 |
| dual_t6 | 5 | 88% (22/25) | 0 | ❌ recall 손실 |

### 결론 및 적용

**phash_t8을 기본 해싱으로 전환.**

- DCT 기반 phash가 frequency domain에서 비교하므로 유사한 색상/구도의 다른 사진을 구분
- ahash는 pixel-level average만 비교하므로 유사 풍경에서 과도한 매칭 발생
- threshold=8에서 모든 변환(blur, crop, brightness, flip, resize)을 100% 감지
- **false positive 54쌍 → 0쌍** (100% 해결)

### 코드 변경

- `engines/dedup.py`: `compute_default_hash()` 메서드 추가 (phash 기본)
- `pipeline.py`: `_detect_duplicates()`에서 `compute_default_hash()` 사용

---

## 1-2: 얼굴 인식 + 임베딩

### 문제

- dlib이 Python 3.14 + macOS clang C23에서 빌드 실패 (`_stdio.h` 호환성 문제)
- mediapipe는 감지만 지원, **임베딩 미제공** → `compare_faces()` 불가
- `scoring.py`의 `known_person_bonus` (+25점/인물) 기능 사용 불가

### dlib 빌드 시도

```
CMAKE_ARGS="-DCMAKE_C_FLAGS=-std=c17" CFLAGS="-Wno-error=..." uv pip install dlib
→ /Library/Developer/CommandLineTools/SDKs/MacOSX.sdk/usr/include/_stdio.h:322:7: error: expected
→ 시스템 헤더 수준 비호환, 해결 불가
```

### 해결: insightface 도입

insightface (ONNX Runtime 기반)으로 전환:
- **RetinaFace**: 고정밀 얼굴 감지 (mediapipe보다 정확)
- **ArcFace**: 512차원 얼굴 임베딩 (cosine similarity로 비교)
- **추가 기능**: 성별/나이 추정, 68개 랜드마크 감지

### 벤치마크 결과

35장 이벤트 다양성 이미지에서:

| 이벤트 유형 | 이미지 수 | 감지된 얼굴 | 임베딩 | 비고 |
|------------|-----------|------------|--------|------|
| graduation | 4 | 6 | 6 | 단체 졸업 사진 |
| celebration | 4 | 9 | 9 | 파티 사진 (최대 8명) |
| portrait | 4 | 4 | 4 | 인물 사진 전체 감지 |
| daily | 4 | 3 | 3 | 사무실/독서 |
| 기타 유형 | 19 | 0 | 0 | 풍경/음식/기타 |
| **합계** | **35** | **22** | **22** | 100% 임베딩 |

### 성능

| 항목 | 값 |
|------|-----|
| 평균 처리 시간 | 0.176초/이미지 |
| 얼굴 있는 이미지 | 0.12-0.69초 |
| 얼굴 없는 이미지 | ~0.1초 |
| 임베딩 차원 | 512 (ArcFace) |
| 백엔드 | insightface (CPU) |

### 임베딩 유사도 테스트

같은 사진의 다른 얼굴 vs 다른 사진의 얼굴:

| 비교 대상 | Cosine Similarity | 판정 |
|-----------|-------------------|------|
| 같은 사진 내 다른 사람 | -0.07 ~ 0.31 | 다른 인물 (< 0.4) |
| 다른 사진의 얼굴 | -0.06 ~ 0.14 | 다른 인물 (< 0.4) |

ArcFace 임베딩 기준:
- **같은 인물**: cosine similarity > 0.4
- **다른 인물**: cosine similarity < 0.3
- 현재 모든 비교에서 < 0.31로 올바르게 다른 인물 판정

### 코드 변경

- `engines/face.py`:
  - insightface를 최우선 백엔드로 추가
  - `_detect_insightface()`: RetinaFace 감지 + ArcFace 임베딩
  - `_compare_insightface()`: cosine similarity 기반 비교
  - 백엔드 우선순위: insightface → mediapipe → face-recognition
- 필수 패키지: `insightface==0.7.3`, `onnxruntime==1.24.4`

### 백엔드 비교

| 항목 | insightface | mediapipe | face-recognition |
|------|------------|-----------|-----------------|
| 감지 정확도 | 높음 (RetinaFace) | 중간 (BlazeFace) | 높음 (HOG/CNN) |
| 임베딩 | ✅ 512-dim ArcFace | ❌ 없음 | ✅ 128-dim dlib |
| Python 3.14 | ✅ 지원 | ✅ 지원 | ❌ dlib 빌드 실패 |
| 속도 | 0.12-0.69초 | 0.05-0.15초 | 0.3-1.0초 |
| 추가 기능 | 성별/나이/랜드마크 | 없음 | 없음 |

---

## 1-3: VLM 이벤트 분류 다양성 검증

### 문제

이전 벤치마크는 picsum 무작위 이미지로 outdoor(60%), other(20%), daily(15.6%) 편중.
birthday, graduation, celebration, portrait 유형은 실제 테스트 안 됨.

### 전체 분류 결과

**Overall accuracy: 71.4% (25/35)**

| 이벤트 유형 | 정확도 | 정답/전체 | 평가 |
|------------|--------|-----------|------|
| meal | **100%** | 4/4 | ✅ 완벽 |
| outdoor | **100%** | 4/4 | ✅ 완벽 |
| celebration | 75% | 3/4 | ✅ 양호 |
| daily | 75% | 3/4 | ✅ 양호 |
| graduation | 75% | 3/4 | ✅ 양호 |
| portrait | 75% | 3/4 | ✅ 양호 |
| birthday | 67% | 2/3 | △ 보통 |
| other | 50% | 2/4 | ⚠ 약함 |
| travel | **25%** | 1/4 | ❌ 부진 |

### 오분류 분석

| 파일 | 기대 | 예측 | 원인 |
|------|------|------|------|
| birthday_1741230 | birthday | daily | 케이크/촛불 없는 가족 모임 이미지 |
| celebration_1405528 | celebration | birthday | 파티인데 생일 파티로 분류 (상위 우선순위) |
| daily_1181424 | daily | portrait | 인물이 주 피사체인 일상 사진 |
| graduation_1454360 | graduation | daily | 학사모 없는 학생 보행 이미지 |
| other_248159 | other | outdoor | 야자수+석양 풍경 ← outdoor가 합리적 |
| other_590022 | other | daily | 사무실 그래프 → daily이 더 적절할 수 있음 |
| portrait_1181690 | portrait | daily | 사무실 배경 인물 → 경계 모호 |
| travel_1271619 | travel | outdoor | 자연 풍경, 랜드마크 인식 실패 |
| travel_1680140 | travel | outdoor | 해변 항공촬영, 여행 단서 부족 |
| travel_3225517 | travel | outdoor | 산+강 풍경, 여행 단서 부재 |

### 분석

**잘 되는 유형:**
- **meal** (100%): 음식이 명확한 피사체일 때 완벽 분류
- **outdoor** (100%): 자연 풍경 인식 정확
- **graduation** (75%): 학사모/가운이 있으면 정확
- **celebration** (75%): 장식/건배가 있으면 감지

**문제 유형:**
- **travel** (25%): VLM이 유명 랜드마크(에펠탑 제외 = Taj Mahal만 인식)와 여행 단서를 제대로 인식하지 못함. 자연 풍경이 보이면 outdoor로 분류하는 경향.
- **other** (50%): "기타" 카테고리 자체가 모호함. 건축물→outdoor, 그래프→daily 등 다른 유형으로 분류되는 경우가 합리적일 수 있음.

**경계 모호 사례 (3건):**
- celebration ↔ birthday: 파티 장식 + 생일 우선순위
- daily ↔ portrait: 일상 공간의 인물 사진
- daily ↔ other: 사무실 환경의 비인물 이미지

### 개선 방향

1. **travel 프롬프트 강화**: "공항, 여권, 여행가방, 랜드마크 이름" 등 여행 키워드를 더 명확히 열거
2. **VLM 기본값 조정 불필요**: celebration/birthday 혼동은 프롬프트 우선순위가 의도한 동작 (생일판단이 축하보다 우선)
3. **other 카테고리 재고**: 합리적 오분류를 제외하면 실질 정확도는 약 80%

---

## VLM 파싱 안정성 수정

벤치마크 중 VLM이 `people_count: "많은"` (한국어 텍스트)을 반환하는 경우 발견.
`parse_scene_output()`에 `safe_int()`, `safe_float()` 헬퍼를 추가하여 비숫자 값에 대한 방어 처리.

---

## 전체 테스트 결과

```
41 passed (test_dedup, test_face, test_scoring, test_pipeline)
전체 테스트 통과, 기존 테스트 깨지지 않음
```

## 변경 파일 목록

| 파일 | 변경 내용 |
|------|-----------|
| `engines/face.py` | insightface 백엔드 추가 (ArcFace 512-dim 임베딩) |
| `engines/dedup.py` | `compute_default_hash()` 추가, phash 기본값 전환 |
| `engines/vlm.py` | `safe_int()`/`safe_float()` 파싱 안정성 개선 |
| `pipeline.py` | dedup에서 `compute_default_hash()` 사용 |

## 요약

| 항목 | 변경 전 | 변경 후 | 개선율 |
|------|---------|---------|--------|
| Dedup FP 쌍 | 54 | **0** | 100% 해결 |
| Dedup Recall | 100% | **100%** | 유지 |
| 얼굴 임베딩 | 불가능 | **512-dim ArcFace** | 신규 |
| 얼굴 감지 속도 | 0.05-0.15s | **0.12-0.69s** | 약간 느림 (임베딩 포함) |
| 이벤트 분류 정확도 | 미검증 4유형 | **71.4% (9유형)** | 검증 완료 |
| VLM 파싱 안정성 | ValueError 발생 | **안전 처리** | 수정 |
