# 2026-09-13 PC 앱 사진 분석 얼굴 런타임 복구 검증

## 결과

2026-09-13 20:38 KST에 Android 수동 재분석으로 시작한 통합 작업은 정상 완료됐다.

- 작업 범위: `2026-05-01 ~ 2026-05-31`
- 통합 실행: `combined-f7a473f1a257437c8e6a`
- 처리 사진: 108장
- 추천 사진: 41장
- 미완료 사진: 0장
- Story: `ready`
- Google Photos: 80장 다운로드, 74장 상세 Vision 분석 완료
- Apple Photos: 25장 상세 Vision 분석 완료
- Linux Qwen3.8 Vision 호출: HTTP 200, 런타임 제한시간 이내 준비 완료

작업 결과와 기존 인물 저장소는 삭제하거나 초기화하지 않았다.

## 발견한 문제

Apple Photos 분석을 수행한 설치형 `PhotosMcp.app` 로그에 다음 경고가 기록됐다.

```text
No face detection backend available. Install insightface, mediapipe, or face-recognition.
```

설치 앱에는 OpenCV와 YuNet/SFace 모델이 이미 포함돼 있었고 인물관리 전용 런타임 검사도 통과하고 있었다. 하지만 photo-ranker의 `FaceEngine`은 InsightFace, MediaPipe, dlib 기반 `face-recognition`만 탐색하고 OpenCV 런타임을 사용하지 않았다. 따라서 사진 품질·장면·Story 분석은 성공해도 Apple 사진의 `faces_detected`, 얼굴 임베딩과 인물 중심 점수 신호가 비어 있을 수 있었다.

## 수정 내용

1. OpenCV YuNet 2023mar와 SFace 2021dec를 photo-ranker의 정식 얼굴 backend로 연결했다.
2. 원본 사진의 긴 변을 최대 1,600px로 제한한 proxy에서 얼굴을 검출하고, YuNet box와 landmark를 원본 좌표로 되돌린 다음 원본으로 SFace 정렬·임베딩을 수행한다.
3. 결과는 기존 계약과 호환되는 `(top, right, bottom, left)` box와 128차원 embedding으로 반환한다.
4. 사람 인덱싱과 photo-ranker가 같은 모델 검색, 크기 검증, 좌표 변환 코드를 사용하도록 공용 인프라 모듈로 분리했다.
5. vendor 구현은 상위 application 계층을 직접 참조하지 않고 vendor adapter 경계를 통해 공용 런타임을 사용한다.
6. 빌드 검증에서 인물관리용 `face_runtime_status()`뿐 아니라 photo-ranker `FaceEngine`이 실제로 `opencv` backend를 선택하는지도 확인한다. 향후 이 경로가 빠진 번들은 설치 전에 실패한다.

## 검증

### 자동 테스트

```text
pytest -q
1075 passed
```

추가한 회귀 테스트는 다음을 고정한다.

- 설치 앱에 포함되는 OpenCV 모델 backend 우선 선택
- YuNet 좌표의 원본 사진 좌표 복원
- 얼굴 box 계약
- SFace 128차원 embedding 생성
- OpenCV embedding의 cosine 동일인 비교
- vendor/application 의존성 경계
- 빌드 스크립트의 photo-ranker 얼굴 backend smoke gate

### 소스 런타임

운영 cache의 사진을 외부로 출력하지 않고 로컬에서만 읽어 다음을 확인했다.

```text
backend_available True
backend opencv
functional_detection True
faces 1
embedding_dimensions [128]
```

### 설치형 macOS 앱

`~/Applications/PhotosMcp.app`을 다시 빌드·depth-first 서명·설치했다. 설치본 Python과 설치본 모델만 사용한 smoke에서 다음을 확인했다.

```text
bundle_face_available True
bundle_face_backend opencv
bundle_functional_detection True
bundle_faces 1
bundle_embedding_dimensions [128]
```

추가 검증:

- `codesign --verify --deep --strict`: 통과
- osxphotos runtime smoke: 통과
- photo-source/photo-ranker vendor runtime smoke: 통과
- 인물 모델 fingerprint: `4b54cc7ef9477ab64415a9806e7d46764345f791cff694967a849c3341572d00`
- 설치 앱 재기동: 성공
- `/health`: `status=ok`, `daemon_status=ready`
- 실행 중 작업: 0
- 대기 중 mutation: 0

## 운영 의미

이번에 이미 완료된 108장 결과는 얼굴 backend가 없던 Apple 분석 구간을 포함하므로 사진 품질·추천·Story 결과는 유효하지만, 얼굴 신호를 보완하려면 같은 범위를 한 번 더 재분석해야 한다. 다음 재분석부터 PC 앱의 Apple Photos 경로도 얼굴 수와 SFace 임베딩을 정상 생성하며, 기존 인물 이름과 확인 기록은 그대로 재사용된다.
