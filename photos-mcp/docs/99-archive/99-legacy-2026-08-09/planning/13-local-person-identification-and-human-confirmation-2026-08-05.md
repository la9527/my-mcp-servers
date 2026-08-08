# 로컬 인물 식별 및 사용자 확인 계획

- 작성일: 2026-08-05
- 상태: 구조 결정 완료, 인식 임계값은 개인 검증 후 확정
- 대상: `PhotosMcp.app` 인물 관리 화면, 얼굴 검출·임베딩·확인 대기열, 사진 분류 연계
- 연계 문서: `10-face-quality-and-group-photo-selection-2026-08-04.md`, `12-selection-and-dual-destination-original-export-2026-08-05.md`

## 1. 목표와 결정

사진에서 얼굴을 찾고 비슷한 얼굴을 묶은 뒤, 앱이 사용자에게 `누구인가요?` 또는 `OOO님이 맞나요?`라고 물어 최종 확인된 결과만 개인 인물 사전에 저장한다. 저장된 인물은 사진 검색·필터·장면 묶음·추천 설명에 활용한다.

핵심 원칙은 다음과 같다.

1. 얼굴 검출, crop, 임베딩, 후보 검색은 Mac 안에서만 수행한다. 얼굴 이미지나 임베딩을 Linux LLM, 외부 API 또는 클라우드 LLM으로 보내지 않는다.
2. 모델의 결과는 어디까지나 `후보`다. 사람이 확인하지 않은 결과를 최종 이름으로 저장하거나 사진 점수에 반영하지 않는다.
3. `Apple Vision`은 얼굴 위치·랜드마크·촬영 품질에 사용하고, 인물 비교 임베딩은 `OpenCV YuNet + SFace`를 첫 제품 후보로 검증한다.
4. Apple 사진에 이미 있는 인물명은 읽을 수 있는 경우 후보 힌트로만 사용한다. 사용자가 확인하기 전에는 PhotosMcp 인물 사전으로 가져오지 않는다.
5. Apple 사진의 시스템 `사람 및 반려동물` 분류를 직접 수정하지 않는다. 공개 PhotoKit은 사진 자산과 앨범 접근을 제공하지만 Apple의 인물 군집에 이름을 쓰는 계약은 제공하지 않으므로, PhotosMcp가 별도 로컬 인물 사전을 소유한다.

Apple Vision의 공개 얼굴 API는 얼굴 사각형과 눈·입 같은 랜드마크를 제공한다. 임의 인물의 이름을 식별하는 공개 API로 사용하지 않는다. [Apple 얼굴 검출 문서](https://developer.apple.com/documentation/vision/detectfacerectanglesrequest), [Apple 얼굴 랜드마크 문서](https://developer.apple.com/documentation/vision/detectfacelandmarksrequest)

## 2. 현재 코드에서 확인한 상태

현재 backend에는 기능의 초기 흔적이 이미 있다.

- `known_faces`: 이름을 기본 키처럼 사용해 여러 임베딩을 저장한다.
- `face_embeddings`: 사진별 얼굴 임베딩과 bbox를 저장한다.
- `face_reviews`: 얼굴 crop과 수동 이름을 저장한다.
- `register_face`, `register_face_from_job`, `label_face_in_job`: 얼굴 한 장을 이름에 연결한다.
- 분류 파이프라인: 알려진 얼굴과 cosine similarity가 `0.4`를 넘으면 이름을 `known_persons`에 추가한다.

하지만 그대로 제품 UI에 연결하면 안 된다.

- 현재 기본 설치에는 실제 임베딩 backend가 활성화되지 않아 1,000장 검증에서 얼굴 임베딩이 생성되지 않았다.
- 임베딩 모델·차원·전처리·버전이 DB에 없고, 서로 다른 모델의 벡터가 섞일 수 있다.
- 이름 문자열과 원시 float 임베딩을 평문 SQLite에 저장한다.
- `0.4` 고정 임계값은 현재 개인 사진으로 검증되지 않았다.
- 확인 전 후보와 사람이 확정한 이름을 구분하지 않는다.
- InsightFace 경로는 이름뿐 아니라 성별·나이까지 불필요하게 계산하고 저장한다.
- 잘못 묶인 얼굴을 분리하거나, 사람을 병합·이름 변경하거나, 특정 오답을 다시 제안하지 않게 하는 이력이 없다.

따라서 기존 테이블은 호환 입력으로 간주하지 않고 새 `PrivateIdentityStore`로 대체한다. 기존 임베딩은 모델 출처를 확인할 수 없으므로 자동 이관하지 않으며, 사용자에게 재색인을 안내한다.

환경 확인 결과 프로젝트 가상환경의 OpenCV `4.13.0`에는 `FaceRecognizerSF`가 포함되어 있다. SFace 모델은 아직 로컬 cache에 없으므로, 구현 단계에서 모델 파일·라이선스·SHA-256을 고정한 명시적 설치 절차가 필요하다.

## 3. 권장 인식 기술

### 3.1 제품 후보

첫 후보는 공식 OpenCV의 `YuNet + SFace` 조합이다.

- YuNet은 얼굴과 정렬에 필요한 5개 landmark를 검출한다.
- SFace는 정렬된 얼굴에서 비교용 특징을 만든다.
- OpenCV 공식 예제의 모델 크기는 얼굴 검출 약 338KB, 얼굴 인식 약 36.9MB다.
- OpenCV Zoo의 SFace 디렉터리는 모델을 포함한 전체 파일을 Apache 2.0으로 명시한다.
- 프로젝트 가상환경에 이미 호환 OpenCV API가 있어 별도 ONNX Runtime 없이 기준선을 만들 수 있다.

OpenCV 문서의 LFW cosine 기준 `0.363`은 실험 시작점일 뿐이다. 가족 사진은 어린이의 성장, 안경, 측면 얼굴, 역광, 작은 얼굴과 긴 시간 간격이 많으므로 이 값을 제품 임계값으로 그대로 사용하지 않는다. [OpenCV SFace 사용·성능 문서](https://docs.opencv.org/4.11.0/d0/dd4/tutorial_dnn_face.html), [OpenCV SFace 모델·라이선스](https://github.com/opencv/opencv_zoo/tree/main/models/face_recognition_sface)

Apple Vision 얼굴 사각형과 YuNet 결과는 IoU로 연결해 기존 얼굴 품질 신호를 재사용한다. 인식용 정렬은 SFace와 함께 검증된 YuNet 5-point 결과를 우선 사용하고, YuNet이 놓친 얼굴을 Vision landmark로 보완할 수 있는지는 별도 비교 실험으로 결정한다.

### 3.2 도입하지 않는 기본 경로

| 후보 | 결정 | 이유 |
| --- | --- | --- |
| Apple Vision 단독 | 검출·품질에만 사용 | 공개 Vision API는 얼굴 위치·랜드마크·품질을 제공하지만 앱 전용 인물 사전을 만드는 식별 임베딩 계약은 제공하지 않는다. |
| InsightFace `buffalo_l` | 제품 기본 제외 | 코드와 사전학습 모델의 라이선스가 다르다. 공식 pretrained 모델은 비상업 연구 전용이고 약 326MB이며 성별·나이 모델까지 포함한다. [InsightFace 공식 안내](https://github.com/deepinsight/insightface/blob/master/python-package/README.md) |
| `face-recognition`/dlib | 비교 기준선만 허용 | 현재 코드와 연결하기 쉽지만 오래된 전처리·모델이며 Apple Silicon 배포와 작은·측면 얼굴의 개선 근거가 약하다. |
| 외부 얼굴 인식 API | 제외 | 개인 얼굴 crop과 생체 특징을 외부로 보내야 하므로 제품의 로컬 전용 원칙에 맞지 않는다. |

대규모 인덱스는 처음부터 추가하지 않는다. 개인 보관함 규모에서 NumPy block cosine 검색의 실측 P95가 100ms를 넘거나 전체 미확인 군집 시간이 운영 목표를 넘을 때만 HNSW를 비교한다. HNSWLib은 cosine 검색과 증분 추가를 지원하지만, 새 native dependency가 생기므로 실측 이득이 있을 때 채택한다. [HNSWLib 공식 문서](https://github.com/nmslib/hnswlib/blob/master/README.md)

## 4. 인물 확인 UX

메인 앱 사이드바에 `인물` 탭을 추가한다. 이 탭은 세 화면으로 구성한다.

### 4.1 인물 시작 화면

- 기능이 Mac에서만 처리되고 외부 전송이 없다는 설명을 먼저 보여준다.
- 분석할 Apple 사진 앨범과 기간을 선택한다.
- `기존 Apple 사진 인물명을 후보로 사용`을 선택 항목으로 제공한다. 기본값은 켬으로 제안할 수 있지만, 가져오기 전에 사용자가 확인한다.
- 예상 사진 수, 원본 다운로드 필요 수, 예상 얼굴 수와 저장 공간을 preflight로 보여준다.
- `인물 찾기 시작`은 읽기 전용 작업이다. Apple 사진과 앨범을 변경하지 않는다.

사진 라이브러리 접근은 사용자의 명시적 권한을 전제로 한다. [Apple PhotoKit 자산 접근 안내](https://developer.apple.com/documentation/photokit/fetching-assets)

### 4.2 확인 대기열

대기열은 얼굴 한 장씩 무작정 묻지 않고 비슷한 얼굴 묶음 단위로 보여준다.

```text
┌──────────────────────────────────────────────────────────────┐
│ 누구인가요?                              미확인 18묶음       │
│                                                              │
│ [대표 얼굴] [다른 시기] [측면] [그룹 사진]                   │
│ 같은 사람으로 보이는 사진 37장 · 신뢰도 높음                 │
│                                                              │
│ 후보:  [김OO]  [박OO]  [새 인물 등록]  [나중에]  [무시]      │
│                                                              │
│                    [묶음 자세히 보기] [37장에 적용]          │
└──────────────────────────────────────────────────────────────┘
```

- 알려진 인물과 유사도가 충분하고 1·2위 차이가 크면 `OOO님이 맞나요?`로 묻는다.
- 애매하면 최대 3명의 후보를 나란히 보여준다.
- 알려진 인물과 충분히 다르면 이름 입력 또는 기존 인물 선택을 제공한다.
- `이 얼굴은 다른 사람`, `묶음 분리`, `인물 병합`, `사진에서 제외`, `다시 묻지 않음`을 제공한다.
- 묶음 적용 전에는 대표 얼굴만 보지 않고 서로 다른 날짜·각도·조명에서 뽑은 4~8개 표본을 보여준다.
- 사용자가 `37장에 적용`을 누른 뒤에만 해당 묶음을 확정한다. 낮은 신뢰도 outlier는 자동 적용하지 않고 다음 대기열로 남긴다.

### 4.3 인물 상세 화면

- 대표 얼굴, 표시 이름, 확인된 사진 수, 미확인 후보 수, 최근 확인일을 보여준다.
- 날짜순 사진 격자와 `잘못 연결된 사진 제거` 기능을 제공한다.
- 이름 변경, 다른 인물과 병합, 잘못 병합한 작업 취소, 인물 삭제를 제공한다.
- `이 인물의 생체 데이터 삭제`는 임베딩·후보·임시 crop·관련 이름 연결 이력을 제거하되 Apple 사진 원본은 변경하지 않는다.
- 사진 필터와 결과 Inspector에는 `확인됨`과 `추정` 상태를 명확히 구분한다. `추정` 이름은 추천 점수나 내보내기 태그에 사용하지 않는다.

## 5. 판정 상태와 학습 규칙

얼굴 관측값은 다음 상태 중 하나만 가진다.

| 상태 | 의미 | 제품 반영 |
| --- | --- | --- |
| `unknown` | 아직 비교하지 않았거나 후보가 없음 | 이름 없음 |
| `suggested` | 모델 또는 Apple 사진 메타데이터가 후보를 제안함 | UI에만 표시 |
| `confirmed` | 사용자가 사람과 얼굴 연결을 승인함 | 검색·분류·추천 설명에 사용 |
| `rejected` | 특정 후보가 아님을 사용자가 확인함 | 같은 오답 재제안 방지 |
| `ignored` | 얼굴이 아니거나 관리 대상이 아님 | 이후 대기열 제외 |

모델은 확인된 얼굴을 기반으로 인물별 여러 prototype을 유지한다. 하나의 평균 얼굴만 만들지 않고, 시간·시점 차이가 큰 대표 prototype을 남겨 어린이 성장과 안경·헤어스타일 변화에 대응한다.

고신뢰 후보 조건은 다음을 모두 만족해야 한다.

1. 얼굴 품질과 크기가 검증 기준을 통과한다.
2. 해당 인물에 서로 다른 촬영일의 확인 표본이 최소 3개 있다.
3. 최고 유사도가 개인 검증으로 정한 `T_high` 이상이다.
4. 1위와 2위의 차이가 `margin_min` 이상이다.
5. 과거 `rejected` pair 또는 인물 분리 제약과 충돌하지 않는다.

조건을 만족해도 자동 확정하지 않는다. UI에서 빠른 `맞음` 제안을 제공할 뿐이며, 사용자 확인이 최종 결정이다.

## 6. 로컬 데이터 구조와 보호

인물 데이터는 일반 작업 DB와 분리해 `$HOME/.photos-mcp/private/people.db` 계열 전용 저장소에 둔다. 실제 경로는 runtime helper로 해석하고 Git이나 공유 경로에 넣지 않는다.

```text
persons
  person_id, encrypted_display_name, status, created_at, updated_at

face_observations
  observation_id, encrypted_asset_ref, encrypted_bbox,
  encrypted_embedding, model_id, model_version, quality, state

person_face_links
  person_id, observation_id, decision, confidence,
  decision_source, confirmed_at

person_prototypes
  person_id, encrypted_embedding, model_version,
  sample_count, period_bucket

identity_events
  event_id, action, encrypted_payload, created_at
```

- 이름, Apple 사진 식별자, bbox와 임베딩은 앱 수준 AES-GCM으로 암호화한다.
- 암호화 키는 macOS Keychain에 보관하고 DB에는 넣지 않는다.
- DB와 임시 디렉터리 권한은 사용자만 읽고 쓸 수 있도록 제한한다.
- 얼굴 crop은 확인 대기열 동안만 암호화된 임시 artifact로 보관한다. 확인 또는 무시 후에는 기본 30일 안에 삭제하고 필요 시 Apple 사진에서 다시 만든다.
- 나이·성별·인종·감정은 추정하거나 저장하지 않는다.
- 모델 ID, 전처리 버전, embedding 차원을 각 벡터에 기록하고 버전이 다른 벡터를 비교하지 않는다.
- 모델을 바꾸면 기존 벡터를 조용히 재사용하지 않고 `재색인 필요` 상태로 전환한다.

개인 얼굴 데이터는 MCP의 일반 도구 응답에 포함하지 않는다. 로컬 앱 UI는 확인된 이름을 표시할 수 있지만, MCP는 기본적으로 인물 수와 익명 local person ID만 반환한다. 사용자가 신뢰하는 로컬 client에 한해 별도 scope를 명시적으로 켠 경우에만 확인된 표시 이름을 제공한다.

## 7. 기존 분류·추천·내보내기 연계

- 장면 묶음의 `known_persons`에는 `confirmed` 인물만 넣는다.
- 인물 확인 전후로 사진의 기술·얼굴 품질 점수는 변하지 않는다.
- `같은 인물이 있는 연속 촬영`의 장면 경계 보조에는 익명 person ID를 사용하고 이름은 필요하지 않다.
- 결과 갤러리에서 확인된 인물을 필터로 선택할 수 있다.
- 파일명과 XMP에는 인물 이름을 기본적으로 넣지 않는다. 사용자가 내보내기 시 `확인된 인물명 포함`을 별도로 켠 경우에만 복사본의 metadata에 기록한다.
- Apple 사진에 인물별 결과를 보관하려면 시스템 인물 분류가 아니라 일반 앨범을 생성·추가하는 별도 write plan을 사용한다.

## 8. 실험 및 확정 절차

사진·얼굴·이름·임베딩·개별 판정은 `$HOME/.photos-mcp/validation/identity/` 아래 개인 경로에만 둔다. 저장소에는 실행 스크립트, 합성 fixture와 비식별 집계만 남긴다.

### Phase P0. 환경·라이선스 고정

1. OpenCV 배포 wheel을 하나로 고정하고 `FaceRecognizerSF`의 py2app 번들 실행을 확인한다.
2. YuNet과 SFace 모델 파일, Apache 2.0 고지, SHA-256, 모델 크기와 다운로드 URL을 manifest에 기록한다.
3. 앱 실행 중 임의 자동 다운로드를 없애고, 기능 활성화 시 모델 설치 계획을 사용자에게 보여준다.
4. 기존 InsightFace·dlib 자동 fallback을 identity 제품 경로에서 제거한다.

### Phase P1. 개인 정답 세트

기존 1,000장 개인 사진 세트를 재사용하되, 인물 식별 정답은 새로 만든다.

- 반복 등장 인물 8~15명을 우선 선정한다.
- 인물별로 서로 다른 날짜의 확인 사진 20장 이상을 목표로 한다.
- 알려진 인물이 아닌 얼굴과 얼굴이 아닌 오검출도 함께 라벨링한다.
- 어린 시기와 최근 사진, 정면·측면, 안경·모자, 역광·야간, 작은 얼굴, 그룹 사진을 분리 태그한다.
- 동일 인물 positive pair와 다른 인물 negative pair를 시간축으로 분리해 train/calibration/held-out로 구성한다.

### Phase P2. 검출·정렬 비교

1. `YuNet + SFace` 전체 경로를 기준선으로 실행한다.
2. `Apple Vision 검출 + Vision landmark 정렬 + SFace`를 비교한다.
3. 얼굴 검출 recall, 오검출 수, 작은 얼굴·측면 얼굴 실패율, 정렬 실패율, 사진당 시간과 최대 RSS를 측정한다.
4. 결과가 비슷하면 중복 detector가 없는 Vision 경로를 선택하고, SFace 공식 정렬이 유의하게 좋으면 YuNet을 identity 전용으로 유지한다.

### Phase P3. 후보 임계값 보정

공개 LFW 기준이 아니라 개인 정답 세트에서 `T_high`, `T_review`, `margin_min`을 정한다.

- 고신뢰 suggestion precision 목표: 99% 이상
- 알려진 인물의 Top-3 후보 recall 목표: 95% 이상
- 미등록 인물을 기존 인물로 제안하는 오인식률: 1% 이하
- 사용자 확정 없이 최종 이름이 저장된 건수: 0건

목표를 충족하지 못하면 자동 범위 적용을 제공하지 않고 얼굴 한 장 또는 작은 묶음 단위의 수동 확인만 제공한다. 정확도와 함께 제안 coverage를 별도 보고해, 단순히 모든 후보를 `unknown`으로 보내 목표치를 맞추는 것을 방지한다.

### Phase P4. 군집·증분 인식

1. 미확인 얼굴을 유사도 graph로 묶고 대표 표본 4~8장을 선정한다.
2. 시간·포즈가 다른 표본을 우선해 사용자가 한 묶음을 빠르게 검토하게 한다.
3. 잘못 묶인 outlier 제거와 split/merge 이력이 재실행 후에도 유지되는지 검증한다.
4. 전체 index 검색이 느릴 때만 HNSW를 NumPy block cosine 기준선과 비교한다.

### Phase P5. UI·개인정보 검증

- VoiceOver label, 키보드 이동, 확대된 얼굴 crop, 이름 입력과 후보 버튼의 focus 순서를 검증한다.
- 이름 변경, 병합, 분리, 거부, 무시, 삭제와 undo가 정상 동작해야 한다.
- 네트워크 차단 상태에서도 전체 인물 확인 흐름이 동작해야 한다.
- Linux workstation이 꺼져 있어도 인물 식별은 Mac에서 완료돼야 한다.
- 앱 로그, MCP 응답, crash report, Git 변경에 이름·사진 ID·crop·embedding이 들어가지 않는지 검사한다.

## 9. 구현 순서

1. 기존 신원 관련 도구를 제품 surface에서 비활성 상태로 두고 `PrivateIdentityStore`와 암호화 키 관리부터 만든다.
2. 모델 manifest와 `YuNetSFaceIdentityEngine`을 추가해 정답 세트용 read-only 계측기를 만든다.
3. 개인 정답 세트 검토 UI와 임계값 보고서를 만든다.
4. `인물` 탭의 시작 화면, 확인 대기열, 인물 상세 화면을 구현한다.
5. confirmed/rejected/ignored 상태, split/merge, undo와 삭제를 구현한다.
6. 확인된 익명 person ID를 장면 묶음·검색에 연결하고, 표시 이름은 로컬 UI에서만 resolve한다.
7. 내보내기와 일반 Apple 사진 앨범 보관은 별도 승인 write plan으로 연결한다.

## 10. 완료 기준

- 모든 얼굴 처리와 이름 확인이 Mac 로컬에서 끝나며 외부 네트워크 요청에 사진·crop·embedding이 포함되지 않는다.
- 확인되지 않은 후보가 최종 이름, 추천 점수, 내보내기 metadata에 반영되지 않는다.
- 모델·전처리 버전이 다른 임베딩은 비교되지 않는다.
- 개인 held-out 검증에서 정한 precision·Top-3 recall·unknown 오인식 목표와 속도·메모리 결과가 문서화된다.
- 한 사람의 이름 변경·병합·분리·삭제가 사진 원본을 바꾸지 않고 로컬 인물 사전에만 적용된다.
- `모든 인물 데이터 삭제` 후 이름, 임베딩, crop, 후보 index와 관련 Keychain key가 남지 않는다.
- Apple 사진 권한이 없거나 모델이 설치되지 않았을 때 기능은 명확한 안내와 함께 안전하게 중단되고 기존 사진 분류는 계속 동작한다.

이 계획에서 확정된 것은 로컬 전용 처리, 사용자 최종 확인, 분리된 암호화 인물 저장소와 SFace 우선 검증이다. 최종 detector 조합과 similarity 임계값은 개인 정답 세트의 비식별 집계 결과를 확인한 뒤에만 제품 기본값으로 고정한다.
