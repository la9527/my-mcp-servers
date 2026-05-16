# Phase 5: Photo-Ranker 개선 작업 계획 (2026-03-30)

## 작업 순서 및 상태

| # | 항목 | 설명 | 상태 |
|---|------|------|------|
| A-1 | Travel/Outdoor 분류 개선 | EXIF GPS 메타데이터 보조 판단으로 travel/outdoor 구분 | ✅ |
| A-2 | Other 카테고리 정리 | other 폐지, confidence 최고 유형으로 분류 | ✅ |
| A-3 | Portrait/Daily 경계 명확화 | 인물 비율 정량 기준 프롬프트 추가 | ✅ |
| B-1 | Known Person 파이프라인 연결 | 가족 임베딩 등록 → 파이프라인 비교 → family_score 활용 | ✅ |
| B-2 | 표정 분석 기능 | insightface gender/age + VLM expressions 하이브리드 | ✅ |
| B-3 | Apple Photos 소스 연동 | osxphotos 소스 + photoscript 앨범 쓰기 | ✅ |
| C-1 | EXIF 메타데이터 엔진 | GPS, 촬영일, 회전정보 추출 엔진 | ✅ |
| C-2 | 임베딩 영속화 | SQLite에 얼굴 임베딩 캐시 | ✅ |
| C-3 | VLM 모델 버전 고정 | model_path를 config로 분리 | ✅ |
| C-4 | Stage1 병렬화 | face/dedup/technical 병렬 처리 | ✅ |
| D | 테스트 보강 | 142 tests 통과 (photo-ranker) + 44 tests (photo-source) | ✅ |
| E | E2E 워크플로우 | classify_and_organize: 소스→분류→앨범 정리 일괄 처리 | ✅ |

---

## 상세 작업 내역

### A-1: Travel/Outdoor EXIF 보조 판단
- `engines/exif.py` 신규 생성 (C-1과 연동)
- GPS 좌표가 존재하면 travel 가중치 부여
- VLM이 outdoor 판정 + GPS 존재 시 → travel로 보정
- `pipeline.py` Stage2에서 EXIF 정보 반영

### A-2: Other 카테고리 정리
- `SCENE_PROMPT`에서 other 정의를 "확신이 없으면 가장 유사한 유형 + confidence 낮춤"으로 변경
- VLM이 other 반환 시 confidence가 가장 높은 대안 유형으로 재분류

### A-3: Portrait/Daily 경계
- `SCENE_PROMPT`에 "인물이 화면의 50% 이상 차지하면 portrait 우선" 기준 추가
- "배경이 일상 공간이라도 인물이 주제면 portrait" 명시

### B-1: Known Person 파이프라인 연결
- `engines/face.py`에 `register_known_face()` / `identify_known_faces()` 추가
- `db.py`에 `known_faces` 테이블 추가
- `server.py`에 `register_face` MCP 도구 추가
- `pipeline.py` Stage1에서 known person 매칭 → `PhotoCandidate.known_persons` 채움

### B-2: 표정 분석
- insightface에 표정 모델이 포함되어 있는지 확인
- 없으면 VLM expressions 필드 활용하여 face expression 매핑
- `scoring.py`의 `happy_expression_bonus` 활성화

### C-1: EXIF 메타데이터 엔진
- `engines/exif.py` 신규: Pillow EXIF 파싱
- GPS 좌표, 촬영일(DateTimeOriginal), 방향(Orientation) 추출
- `models.py`에 `ExifData` 데이터클래스 추가

### C-2: 임베딩 영속화
- `db.py`에 `face_embeddings` 테이블 추가 (photo_id, face_idx, embedding_blob)
- `pipeline.py`에서 감지된 임베딩 저장/조회 로직

### C-3: VLM 모델 버전 고정
- `PipelineConfig`에 `vlm_model_path` 필드 추가
- `VLMEngine.__init__`에서 config 값 사용
- `DEFAULT_MODEL` 유지하되 config 오버라이드 가능

### C-4: Stage1 병렬화
- `asyncio.gather()`로 technical_score, face_detection, hash_compute 병렬 실행
- VLM Stage2는 그대로 순차 유지 (메모리 제약)

### D: 테스트 보강
- `test_exif.py`: EXIF 파싱, GPS 추출, 회전 보정
- `test_face.py` 보강: insightface mock, 표정 분석, known person 비교
- `test_db.py` 보강: 임베딩 저장/조회, known_faces CRUD
- `test_pipeline.py` 보강: 병렬 Stage1, EXIF 연동
- `test_scoring.py` 보강: 표정 보너스 활성화 검증
- 손상 이미지 에러 핸들링 테스트
