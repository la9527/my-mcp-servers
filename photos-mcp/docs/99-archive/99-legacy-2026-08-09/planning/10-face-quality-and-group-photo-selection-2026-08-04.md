# 얼굴 품질 기반 대표 사진 선택 계획

- 작성일: 2026-08-04
- 상태: Phase F1 계측 및 시각 감사 완료, 점수 반영 전
- 대상: `photo-ranker`의 장면별 후보 압축과 대표 1·2장 선택
- 목표: 같은 장면의 여러 사진 중 **사진에 나온 사람이 대체로 모두 잘 나온 사진**을 우선한다.

## 1. 결론

이 기능은 사람의 신원을 알아내는 기능이 아니라, 사진 한 장 안에서 얼굴들이 잘 찍혔는지를 평가하는 기능으로 설계한다. 기본 구현은 macOS의 Apple Vision `VNDetectFaceCaptureQualityRequest`를 사용한다. 이 API는 얼굴마다 0~1 품질 값을 반환하며, 값이 높을수록 조명·선명도·중앙 배치가 좋은 얼굴을 뜻한다. [Apple Vision 문서](https://developer.apple.com/documentation/vision/vndetectfacecapturequalityrequest?language=objc)

인물 신원 후보를 만들고 사용자가 이름을 확인하는 기능은 이 품질 점수와 분리해 `13-local-person-identification-and-human-confirmation-2026-08-05.md`에서 다룬다. 신원 후보는 얼굴 품질 점수의 의미를 바꾸지 않으며, 확인 전 이름은 추천 결과에 반영하지 않는다.

권장 순서는 다음과 같다.

1. **기본 경로**: Apple Vision 얼굴 검출·얼굴 촬영 품질을 로컬에서 계산한다.
2. **선택 보조**: 눈 감김·표정과 같은 세부 신호가 사람 검토에서 실제로 필요하다고 확인된 경우에만 MediaPipe Face Landmarker를 추가한다. 이 도구는 52개 blendshape 계수에 `eyeBlinkLeft`, `eyeBlinkRight` 등을 제공한다. [MediaPipe blendshape 문서](https://ai.google.dev/edge/api/mediapipe/python/mp/tasks/vision/drawing_styles/face_landmarker/Blendshapes)
3. **도입 보류**: InsightFace `buffalo_l`, MagFace와 같은 신원 인식 계열 모델은 기본 경로에 넣지 않는다. 현 모델 팩은 약 326MB이고 비상업 연구용으로 배포되며, 개인 사진에서 필요 없는 얼굴 임베딩을 만들게 된다. [InsightFace 모델 안내](https://github.com/deepinsight/insightface/blob/master/python-package/README.md) MagFace는 인식용 특징 벡터의 크기로 품질을 추정하는 연구 모델이므로, 사람 검토 기준선에서 native Vision보다 개선이 입증될 때만 별도 shadow 실험 대상으로 둔다. [MagFace 논문](https://openaccess.thecvf.com/content/CVPR2021/html/Meng_MagFace_A_Universal_Representation_for_Face_Recognition_and_Quality_Assessment_CVPR_2021_paper.html)

## 2. Phase F1 계측 결과

2026-08-04에 기존 1,000장 개인 검증 세트에서 `VNDetectFaceCaptureQualityRequest` revision 3을 실행했다. 원본·미리보기·사진 ID·얼굴 좌표·개별 얼굴 점수는 개인 검증 경로에만 두고, 아래 비식별 집계만 저장소에 남겼다.

| 항목 | 결과 | 해석 |
| --- | ---: | --- |
| 입력 사진 | 1,000장 | 기존 장면·VLM 재검증 세트와 동일하다. |
| 얼굴 포함 사진 | 485장 | 사진의 48.5%에서 하나 이상 얼굴을 관측했다. |
| 얼굴 관측값 | 822개 | 그룹 사진을 포함한 총 얼굴 수다. |
| 얼굴 품질 중앙값 | 0.215637 | 이 API 값은 전체 보관함의 절대 합격선이 아니라 같은 request revision 안의 상대 비교로 다뤄야 한다. |
| 얼굴 품질 P95 | 0.519043 | 상위 품질도 1에 가깝지 않아 0.35 같은 고정 합격선은 부적절하다. |
| 요청 시간 중앙/P95 | 4.73ms / 7.24ms | 1,000장 전체 계측 비용은 약 5.1초로 후보 압축 shadow 계측에 충분히 가볍다. |
| 프로세스 RSS | 55.41MB -> 137.25MB | Vision 프레임워크를 포함한 측정 프로세스의 상주량이다. |
| 얼굴 신호가 있는 장면 | 359개 | 이 장면에서만 얼굴 점수가 후보 순서에 영향을 줄 수 있다. |
| provisional Top-1 변동 | 30개 장면 | 동일 장면 내 상대 얼굴 품질 보너스(최대 1점)를 둔 shadow 비교 결과다. 더 낫다는 의미는 사람 라벨로 확인해야 한다. |
| provisional Top-4 구성 변동 | 2개 장면 | 얼굴 보조 신호는 우선 대표 후보 동점 해소에 주로 작동하고, 후보 보존 자체는 크게 흔들지 않았다. |

계측 결과는 [apple-photos-face-quality-1000-summary.json](../../experiments/phase1_5_preflight_2026-08-03/results/apple-photos-face-quality-1000-summary.json)에 저장했다.

### 2.1 직접 시각 감사

변경된 Top-1 장면 30개 중 시점이 고르게 분포한 12쌍을 원본 개인 검증 경로에서 직접 비교했다. 이 검토의 개별 사진·사진 ID·경로·얼굴 관측값은 저장소에 남기지 않았다.

| 판단 | 장면 수 | 해석 |
| --- | ---: | --- |
| 얼굴 품질 반영 후보가 명확히 더 좋음 | 3 | 정면성, 눈 방향 또는 그룹 내 인물 상태가 더 자연스러운 사례였다. |
| 사실상 동등/근접 중복 | 6 | 연속 촬영의 작은 차이여서 순서 변경 자체의 효용이 낮았다. |
| 교체 근거 부족 또는 기존 후보 선호 | 3 | 얼굴 값만으로는 표정, 전체 구도, 장면 의도를 충분히 대변하지 못했다. |

따라서 이 신호는 **자동 대표 선택을 바꾸지 않고 shadow 전용**으로 유지한다. 비식별 집계는 [apple-photos-face-quality-1000-visual-audit-summary.json](../../experiments/phase1_5_preflight_2026-08-03/results/apple-photos-face-quality-1000-visual-audit-summary.json)에 저장했다.

## 3. 현재 상태와 문제

현재 `FaceEngine`은 InsightFace, MediaPipe FaceDetector, `face-recognition` 순으로 얼굴을 찾는다. 현재 실행 환경에는 이 백엔드가 설치되지 않아 1,000장 재검증에서 `faces_detected=0`이었고, 장면 후보 압축은 얼굴 수가 없는 상태로 동작했다.

현재 구조의 한계는 다음과 같다.

- 얼굴 수만 반영하므로, 한 사람이 눈을 감았거나 흐릿한 그룹 사진을 구분하지 못한다.
- InsightFace 기본 모델은 embedding·성별·나이까지 계산해 목적에 비해 개인정보 처리 범위가 넓다.
- MediaPipe FaceDetector는 얼굴 위치만 제공하고 “잘 나온 얼굴” 판단에는 부족하다.
- 전역 점수에 단순히 얼굴 개수를 더하면 풍경·음식 사진이 부당하게 낮아질 수 있다.

## 4. 기능 범위

### 포함

- 사진별 얼굴 개수와 얼굴별 촬영 품질의 로컬 계산
- 그룹 사진에서 가장 약한 얼굴을 반영하는 집계 점수
- 작은 얼굴, 흐림, 노출, 눈 감김 가능성, 심한 측면·프레임 이탈의 실패 코드
- 같은 장면 안 후보 압축과 대표 1·2장 선택에서의 보조 점수
- 결과 화면에 “인물 품질 우수”, “한 명 이상 검토 필요”처럼 이유만 노출

### 제외

- 사람 이름·신원 자동 식별과 새 얼굴 임베딩의 장기 저장
- 성별·나이·감정의 저장 또는 추천 근거 사용
- 얼굴이 없는 사진의 감점
- 사진·앨범을 자동 수정하거나 외부 서버로 얼굴 이미지를 전송하는 동작

## 5. 점수 설계

### 4.1 얼굴별 점수

각 얼굴에 아래 0~1 신호를 계산한다.

| 신호 | 기본 출처 | 목적 | 초기 비중 |
| --- | --- | --- | ---: |
| `capture_quality` | Apple Vision | 조명·선명도·중앙 배치 | 0.55 |
| `crop_sharpness` | 로컬 Laplacian/고주파 지표 | 얼굴 영역 흐림 보정 | 0.15 |
| `face_size` | 얼굴 bounding box | 너무 작은 얼굴의 신뢰도 하향 | 0.10 |
| `framing_pose` | Vision bbox·landmark | 프레임 이탈·심한 회전 | 0.10 |
| `eyes_open` | 선택적 MediaPipe blendshape | 눈 감김·심한 squint 보조 | 0.10 |

기본 단계에서는 MediaPipe가 없으므로 `eyes_open` 비중을 나머지 신호에 다시 정규화한다. `capture_quality`는 절대 미학 점수가 아니라 **같은 Vision request revision 안에서 후보를 비교하는 상대 신호**로만 사용한다.

### 4.2 그룹 사진 집계

사진의 얼굴 점수는 평균만 쓰지 않는다. 가족·단체 사진은 한 사람만 좋고 다른 사람이 나쁘면 “대체로 모두 잘 나온 사진”이 아니기 때문이다.

```text
group_face_quality
  = 0.55 * 최저 얼굴 품질
  + 0.25 * 면적 가중 평균 얼굴 품질
  + 0.20 * 하위 25% 얼굴 품질
```

최저 얼굴이 너무 작아 검출 신뢰가 낮으면 작은 얼굴 가중치를 제한하고, 실패 코드 `small_face`를 남긴다. 얼굴이 하나도 없으면 이 축은 `not_applicable`이고 사진 점수를 올리거나 내리지 않는다.

### 4.3 장면 안 선택에만 적용

초기 적용은 전역 추천 점수가 아니라 같은 장면의 후보 압축과 대표 선택에 한정한다.

```text
within_scene_candidate_score
  = technical_score
  + relative_face_tie_break_bonus (0.0 ~ 1.0)
```

- `relative_face_tie_break_bonus`는 같은 장면에서 얼굴이 관측된 사진의
  `group_face_quality`를 0~1 사이로 정규화한다. 한 장만 얼굴이 관측되거나
  품질 차이가 없으면 보너스는 0이다.
- 얼굴 없는 풍경·음식 사진은 `technical_score`만 사용하며 감점하지 않는다.
- 얼굴 품질은 첫 번째 추천과 두 번째 추천의 동점 해소·근접 후보 압축에 우선 사용한다.
- 직접 시각 감사에서 근접 연사·구도 차이 사례가 확인됐으므로, 사람 라벨 평가 전에는 이 보너스로 자동 추천 순서를 바꾸지 않는다.
- `group_face_quality`의 절대값으로 고정 합격/불합격을 판단하지 않는다. 같은 장면의 얼굴 후보 사이에서 순위·분위수·점수 차이로만 우선순위를 조정하고, 사람 라벨 후에만 유형별 임계값을 보정한다.
- 눈 감김 가능성이 큰 얼굴이 확인된 경우에만 별도 실패 코드로 `review` 상태 후보를 만든다. Vision capture quality 값만으로 눈 감김을 단정하지 않는다.
- 사람 검토 데이터가 쌓일 때까지 전역 개인화 점수를 바꾸지 않는다.

## 6. 결과 계약과 개인정보

사진 결과에는 다음의 집계값만 추가한다.

```json
{
  "face_quality_available": true,
  "face_quality_backend": "apple-vision-face-capture-v2",
  "face_count": 3,
  "group_face_quality": 0.74,
  "face_quality_min": 0.61,
  "face_quality_mean": 0.79,
  "face_quality_failure_codes": ["eyes_closed_possible"]
}
```

원본 얼굴 crop, landmark 좌표, 눈·입 좌표, 얼굴 embedding, 이름, 성별·나이는 결과·SQLite 장기 저장·MCP 응답·Git에 넣지 않는다. 실행 중 메모리의 얼굴 관측값은 점수 계산 직후 해제한다. 디버그가 필요하면 사용자가 명시적으로 허용한 개인 로컬 검증 디렉터리에 짧은 기간만 저장하고, 기본값은 저장하지 않는다.

## 7. 구현 단계

### Phase F1. Apple Vision 기본 계측

1. [완료] private 1,000장 계측기에서 `VNDetectFaceCaptureQualityRequest` revision 3의 가용성·분포·시간·메모리를 확인했다.
2. `FaceQualityEngine`을 새로 만들고 얼굴 rectangle/landmark 요청을 메인 스레드에서 초기화한다.
3. `FaceQualityResult`에 집계 점수와 실패 코드만 정의한다.
4. `PhotoCandidate`, 결과 DB, AppKit Inspector에 집계 필드를 연결한다.
5. Vision API를 사용할 수 없는 환경은 `not_available`로 남기고 기존 기술 점수만 사용한다. RGB thumbnail 또는 얼굴 수로 품질을 추정하는 폴백은 사용하지 않는다.

### Phase F2. 장면 후보 shadow 적용

1. 동일 장면에서 기존 기술 점수와 얼굴 보조 점수의 후보 4장 차이를 나란히 저장한다.
2. 250개 장면 사람 검토 대기열에 얼굴 품질 실패 여부만 추가한다.
3. `Recall@4` 하락, 베스트 1·2장 품질, 한 사람이라도 나쁜 추천 비율을 측정한다.
4. 통과 전에는 UI에 진단만 표시하고 자동 추천 순서를 바꾸지 않는다.

### Phase F3. 선택적 MediaPipe 보조

사람 검토에서 눈 감김·표정 문제가 충분히 자주 확인될 때만 Face Landmarker의 모델 파일, 설치 크기, 배포 라이선스, macOS arm64 실행 안정성을 검증한다. `eyeBlink`·`eyeSquint` 계수는 확정 판정이 아니라 `eyes_closed_possible`의 보조 신호로만 사용한다.

### Phase F4. 도입 게이트

다음 조건을 모두 만족할 때만 기본 장면 선택에 반영한다.

- 후보 `Recall@4`가 기존 기준보다 낮아지지 않고 최소 98%
- 사람 검토 Top-1 또는 NDCG@2가 개선
- 그룹 사진에서 “한 명 이상 잘못 나온 추천”이 의미 있게 감소
- 인물 없는 사진 유형의 품질 저하 없음
- 얼굴 원본·embedding 외부 전송 및 장기 저장 없음
- Vision 미지원 환경에서 기존 결과와 동등하게 동작

## 8. 검증 계획

1. 1,000장 표본에서 Vision 요청 가용성·요청 시간·메모리를 별도 계측한다.
2. 사람 검토 250개 장면 중 그룹 사진, 단일 인물, 어린이, 역광, 야간, 작은 얼굴, 모션 블러 사례를 최소 100개 이상 확보한다.
3. 기존 후보 4장과 얼굴 보조 후보 4장의 `Recall@4`, Top-1, Recall@2, NDCG@2를 비교한다.
4. 한 장면에서 추천이 2장을 넘지 않는지와 얼굴 없는 사진의 점수 불변성을 회귀 테스트로 고정한다.
5. 얼굴 품질 API의 request revision·macOS 버전·백엔드를 결과 진단에 기록해 재현성을 확보한다.

## 9. 의사결정 기록

| 선택지 | 판단 |
| --- | --- |
| Apple Vision Face Capture Quality | **기본 채택 후보**. 현재 Mac에서 이미 Vision을 사용하고, 얼굴 품질 목적에 직접 맞으며 별도 모델 배포가 없다. |
| MediaPipe Face Landmarker | **선택적 보조 후보**. 눈 감김 가능성에는 유용하나 모델 자산과 런타임 의존성을 추가한다. |
| InsightFace `buffalo_l` | **기본 제외**. 현 모델 팩은 비상업 연구용이며 embedding·속성 정보를 만들어 목적과 개인정보 최소화 원칙에 맞지 않는다. |
| MagFace/기타 FIQA 모델 | **shadow 연구 후보**. 품질 연구 근거는 있으나 별도 모델·라이선스·검증이 필요하고, 신원 인식 feature를 전제로 한다. |
| `face-recognition`/dlib | **제외**. 얼굴 위치·embedding 중심이며 현대적인 촬영 품질·눈 감김 신호를 제공하지 않는다. |

## 10. 다음 행동

다음 구현은 Phase F1 계측값을 결과 계약에 연결하는 `FaceQualityEngine`이다. MediaPipe나 InsightFace 설치를 먼저 하지 않고, Vision 품질 신호가 250개 사람 검토 기준에서 실제로 Top-2 선택을 개선하는지부터 확인한다.

## 11. Apple Vision 외 모델 재검토 (2026-08-04)

최신 공개 자료를 다시 확인한 결과, 얼굴 품질 도구는 크게 두 부류다. 첫째는 **얼굴 인식에 적합한지**를 측정하는 생체 품질 도구이고, 둘째는 **눈·표정·시선** 같은 실패 원인을 보조하는 landmark 도구다. 개인 사진에서 “가장 잘 나온 사진”을 고르는 목적에는 두 점수를 단독으로 쓰지 않고, 전체 사진의 기술·구도 점수와 결합해야 한다.

| 후보 | 현재 평가 | 장점 | 제약 및 판단 |
| --- | --- | --- | --- |
| Apple Vision Face Capture Quality | 기본 유지 | 현 Mac에서 1,000장 중앙 약 5ms, 별도 모델·얼굴 crop 저장·외부 전송 없음 | 표정·눈 감김·전체 구도 판단에는 부족하다. |
| [OFIQ](https://github.com/BSI-OFIQ/OFIQ-Project) | **기준선 실행 완료, 제품 도입 보류** | C/C++ 기반 오픈소스 얼굴 품질 라이브러리이며 ISO/IEC 29794-5:2025의 참조 구현이다. [eu-LISA 안내](https://www.eulisa.europa.eu/activities/research-and-innovation/ofiq) | 1,000장 실측에서 294장만 통합 얼굴 품질 점수가 평가 가능했고 최대 RSS가 약 1.97GiB였다. 여권·정면 얼굴·인식 적합성을 중심으로 해 가족·여행의 자연스러운 측면·웃음 사진을 과도하게 낮출 수 있으며, 기본 제품 경로에는 넣지 않는다. |
| [MediaPipe Face Landmarker](https://ai.google.dev/edge/api/mediapipe/python/mp/tasks/vision/FaceLandmarker) | **선택 보조 실험 권장** | 단일 이미지에서 landmark를 찾고, 52개 blendshape에 `eyeBlinkLeft/Right`, `eyeSquint`, 시선·입 관련 신호가 있다. [blendshape 목록](https://ai.google.dev/edge/api/mediapipe/python/mp/tasks/vision/drawing_styles/face_landmarker/Blendshapes) | 종합 사진 품질 모델은 아니다. 모델 자산을 추가해야 하며, blink 값은 확정 판정이 아니라 `eyes_closed_possible` 후보 표시에만 사용한다. 얼굴 임베딩·신원 인식은 사용하지 않는다. |
| CLIB-FIQA, MR-FIQA, IG-FIQA 등 최신 연구 FIQA | 도입 보류 | 2024~2025 연구에서 다양한 얼굴 인식 벤치마크의 품질 평가를 개선했다. [CLIB-FIQA](https://openaccess.thecvf.com/content/CVPR2024/papers/Ou_CLIB-FIQA_Face_Image_Quality_Assessment_with_Confidence_Calibration_CVPR_2024_paper.pdf), [MR-FIQA](https://openaccess.thecvf.com/content/ICCV2025/papers/Ou_MR-FIQA_Face_Image_Quality_Assessment_with_Multi-Reference_Representations_from_Synthetic_Data_Generation_ICCV_2025_paper.pdf) | 대체로 얼굴 인식용 crop·특징 표현에 최적화돼 있고, 재현 가능한 배포 모델·상업 라이선스·Mac 런타임이 불명확하다. 사진 선별 품질을 직접 입증하지 못했다. |
| MagFace, SER-FIQ, InsightFace | 기본 제외 | 연구·비교 기준으로는 의미가 있다. | 인식용 embedding 또는 recognition backbone을 전제로 한다. InsightFace 기본 모델 팩은 사전학습 모델이 비상업 연구용이며 159~407MB 범위다. [공식 안내](https://github.com/deepinsight/insightface/blob/master/python-package/README.md) 개인 사진 선별에 불필요한 생체정보 처리 범위가 생긴다. |

NIST의 최신 FATE 품질 평가도 품질 벡터가 초점·조명·자세·표정·안경 같은 **실패 원인을 설명하는 데 유용**하지만, 하나의 품질 값으로 사용자의 미적 선호를 확정할 수는 없다고 구분한다. [NIST FATE Quality](https://pages.nist.gov/frvt/html/frvt_quality.html)

## 12. 수정된 진행 방안

1. **제품 경로 유지**: Apple Vision 품질은 전체 사진 입력에서 계산하되, 결과에는 집계값·실패 이유만 남기고 대표 선택은 계속 shadow로 유지한다.
2. **OFIQ 기준선 파일럿**: 완료. 격리된 로컬 빌드에서 1,000장 전체를 실행해 얼굴 품질 평가 가능 범위 자체를 측정했다. Mac arm64 빌드, 공식 conformance, 모델·설치 크기, 이미지당 시간, RSS를 기록했다. Vision과의 순위 상관 및 250개 사람 라벨의 Top-1/NDCG@2는 제품 도입을 검토할 경우에만 별도 shadow 실험으로 진행한다. 얼굴 crop·중간 산출물·원본은 저장하지 않는다.
3. **MediaPipe 실패 원인 파일럿**: OFIQ와 별개로 250개 사람 라벨 장면에서만 `eyeBlink`, `eyeSquint`, 얼굴 각도 신호를 shadow로 측정한다. 눈 감김을 잘못 경고하는 비율과 실제 놓친 사례를 확인하고, 효과가 있으면 `eyes_closed_possible`만 UI에 보인다.
4. **도입 기준**: OFIQ 또는 MediaPipe가 Vision 기준보다 사람 선택 Top-1 또는 NDCG@2를 개선하고, 풍경·음식·옆얼굴·자연스러운 단체 사진의 오판을 늘리지 않으며, 설치·라이선스·개인정보 조건을 통과할 때만 보조 신호로 채택한다.

현 시점의 권장 조합은 **Apple Vision(가벼운 기본 품질) + 전체 사진 기술·장면 점수 + MediaPipe의 선택적 눈 감김 보조**다. OFIQ는 매우 좋은 객관 기준선이지만, 일반 사진 보관함의 대표 사진 선택기 자체를 대체하는 모델로는 아직 적합성이 확인되지 않았다.

## 13. OFIQ macOS arm64 실측 (2026-08-04)

공식 [OFIQ Project](https://github.com/BSI-OFIQ/OFIQ-Project) 1.2.0을 별도 로컬 캐시에서 macOS arm64로 빌드했다. 공식 `conformance_tests.sh --os macos`는 **787건 통과**했다. 제품 저장소에는 개인 사진 경로·개별 점수·OFIQ CSV를 넣지 않았고, 비식별 집계만 [apple-photos-ofiq-1000-summary.json](../../experiments/phase1_5_preflight_2026-08-03/results/apple-photos-ofiq-1000-summary.json)에 남겼다.

| 항목 | 실측 | 해석 |
| --- | ---: | --- |
| 입력 | 1,000장 | 기존 Apple Photos 비공개 재검증 표본 전체를 한 프로세스 배치로 실행했다. |
| OFIQ 통합 점수 평가 가능 | 294장 (29.4%) | 나머지 706장은 얼굴이 없거나 ISO 생체 얼굴 품질의 적용 대상이 아니었다. Apple Vision의 얼굴 관측 485장보다도 좁은 범위다. |
| `UnifiedQualityScore` | 중앙 18, 평균 24.65, P95 73 | 점수 자체는 미적·전체 사진 점수가 아니라 생체 인식용 얼굴 이미지 품질이다. 절대 임계값으로 추천을 만들면 안 된다. |
| 실제 배치 시간 | 154.76초, 6.46장/초 | 초기화 약 2.18초를 포함한다. CLI 내부 평가 시간 중앙 12ms, P95 490ms, 평균 150.87ms였다. |
| 최대 RSS | 2,111,111,168 bytes (약 1.97GiB) | Apple Vision 얼굴 품질 1,000장 실험의 약 141MB보다 실행 메모리 부담이 크다. |
| 모델 파일 | 약 433MB | OFIQ `data/models`의 추가 로컬 모델 용량이다. |
| 설치 산출물 | 약 163MB | 컴파일된 실행 파일·라이브러리 산출물이며 Conan 빌드 캐시는 별도다. |

이전 Apple Vision shadow Top-1 변경 장면 12쌍에도 OFIQ를 대조했다. OFIQ가 양쪽 사진 모두에 점수를 낸 쌍은 5쌍뿐이었고, Vision 얼굴 품질 후보를 더 높게 둔 경우 3쌍, 기존 후보를 더 높게 둔 경우 2쌍, 나머지 7쌍은 동점 또는 한쪽 이상 평가 불가였다. 사람 정답 라벨이 없는 작은 표본이므로 우열을 결론내릴 수는 없지만, 대표 사진 순위를 안정적으로 비교할 만큼의 적용 범위가 아니라는 판단을 뒷받침한다.

### 13.1 macOS Python 바인딩 상태

공식 Python wheel은 macOS에서 `ofiq_capi.py`가 `libofiq.dylib`를 적재하려 하지만 wheel에는 `libofiq_lib.dylib`만 포함해 그대로 import하지 못했다. 반면 같은 빌드의 공식 C++ `OFIQSampleApp`은 정상 실행됐다. 이 문제는 제품 코드에서 비공식 파일명 보정으로 숨기지 않고, OFIQ 측 수정 또는 공식 macOS wheel 수정본이 나올 때까지 **CLI/네이티브 기준선 실험 한정**으로 둔다.

### 13.2 판정

- OFIQ는 ISO 기준의 얼굴 이미지 품질을 확인하는 **유효한 비교 기준선**으로는 쓸 수 있다.
- 그러나 약 29%만 평가 가능하고 약 1.97GiB RSS가 필요하며, 자연스러운 가족·여행 사진의 대표 선택 목적과 품질 정의가 다르다.
- 따라서 OFIQ를 `PhotoRanker` 기본 의존성, 사용자 결과 점수, 자동 추천 순위에 넣지 않는다.
- Apple Vision의 가벼운 상대 얼굴 품질을 유지하고, 눈 감김 같은 구체적 실패 원인은 필요할 때 MediaPipe shadow 실험으로 별도 확인한다.

## 14. 2048px 사람 검토 및 얼굴 품질 shadow 결과 (2026-08-04)

초기 512px 검토는 표정·초점·미세한 흔들림을 판단하기에 부족했다. 같은 100개 장면의 251장을 Apple Photos 원본 내보내기로 다시 준비해 실제 최소 변 1280px, 중앙·P95 2048px를 확인했다. 84개 장면은 완료, 16개 장면은 사용자가 건너뛰었으며 형식 오류는 없었다. 원본·사진 ID·개별 선택은 개인 검증 폴더에만 두고, 집계는 [apple-photos-high-resolution-face-shadow-summary.json](../../experiments/phase1_5_preflight_2026-08-03/results/apple-photos-high-resolution-face-shadow-summary.json)에 저장했다.

| 비교 | Top-1 일치 | Primary Recall@4 | NDCG@4 | 해석 |
| --- | ---: | ---: | ---: | --- |
| 현재 저장 후보 순위 | 54/84 (64.29%) | 82/84 (97.62%) | 0.8407 | 고해상도 사람 선택 기준의 현재 기준선이다. |
| 기술 점수만 shadow | 54/84 (64.29%) | 82/84 (97.62%) | 0.8407 | 현재 후보 순위와 동일했다. |
| 기술 점수 + 상대 얼굴 품질 | 49/84 (58.33%) | 82/84 (97.62%) | 0.8179 | 55개 장면에 얼굴 신호가 있었고 22개 Top-1이 바뀌었지만, 사람 선택과의 일치는 5개 장면 감소했다. |

얼굴 품질 요청은 215장에 대해 중앙 14.82ms, P95 24.24ms로 충분히 가벼웠다. 그러나 이번 정답 기준에서 상대 얼굴 품질 보너스는 전체 구도·표정·장면 의도를 대신하지 못했고 Top-4 후보 보존도 개선하지 못했다. 따라서 **Apple Vision 얼굴 품질도 자동 순위 보너스로는 적용하지 않고**, 결과 계약의 shadow 진단으로만 유지한다.

### 14.1 제품 입력 해상도

사람 검토에서 확인한 해상도 문제를 반영해 Photos MCP의 분류·선별 분석 입력과 결과 갤러리 미리보기는 기본 최대 변을 512px에서 **1024px**로 올린다. VLM 입력 상한도 1024px로 맞춘다. 일반 `photos_query`의 선택적 목록 썸네일 기본값은 MCP 응답 크기와 호출 안정성을 위해 512px로 유지한다. 이 정책은 원본이 1024px 이상 준비된 경우에만 세부 정보를 보존하며, iCloud 원본이 축소 파생본만 노출되는 경우에는 실제 크기를 확대하지 않는다.
