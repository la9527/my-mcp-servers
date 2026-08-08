# photo-ranker

## 1. 역할

`photo-ranker` 는 사진을 읽어 와 분석하고, 분류하고, review/write-back 결과까지 만드는 계층이다. `photos-mcp` 안에서는 가장 넓은 기능 표면을 가진 subsystem 이고, background jobs 와 Apple Photos write-back 까지 담당한다.

개념적으로는 아래 다섯 묶음으로 보면 이해가 쉽다.

- 단건 분석
- background jobs
- review / curation
- write-back / organize
- end-to-end workflow

## 2. 왜 `photo-ranker` 가 중요한가

사용자가 `photos-mcp` 에 기대하는 가치 대부분은 결국 `photo-ranker` 에서 나온다.

- 어떤 사진이 잘 나왔는지
- 어떤 이벤트/장면인지
- 얼굴이 누구인지
- 어떤 사진을 남길지
- 결과를 Apple Photos album 으로 어떻게 되돌릴지

`photo-source` 가 입력 계층이라면, `photo-ranker` 는 실제 판단과 정리 계층이다.

## 3. tool 그룹

현재 `photo-ranker` tool 은 32개다.

### 분석 tool

- `score_quality`
- `detect_faces`
- `describe_scene`
- `classify_event`
- `find_duplicates`
- `rank_best_shots`

이 그룹은 개별 사진 또는 사진 집합을 분석해 점수와 구조화 결과를 만든다.

### known face 관리

- `register_face`
- `list_known_faces`
- `register_face_from_job`
- `delete_known_face`

이 그룹은 family/person 중심 selection 에 필요한 face memory 계층이다.

### background job 관리

- `start_classify_job`
- `get_job_status`
- `get_job_summary`
- `get_job_result`
- `cancel_job`
- `delete_job`
- `clear_job_history`
- `list_jobs`

이 그룹은 `photos-mcp` 의 menu UI 와 가장 강하게 연결된다.

### review / curation

- `get_review_items`
- `set_photo_review`
- `export_selected_photos`
- `curate_best_photos`
- `list_photo_faces`
- `label_face_in_job`

이 그룹은 “분류 결과를 사람이 한 번 더 검토하고 선택한다”는 흐름을 담당한다.

### write-back / organize

- `create_album`
- `add_to_album`
- `organize_results`
- `organize_results_to_directory`
- `import_photos`
- `import_and_organize`
- `list_photo_albums`

이 그룹은 결과를 Apple Photos 또는 local directory 구조로 반영한다.

### end-to-end workflow

- `classify_and_organize`

이 tool 은 source 에서 불러오기, 분류, album organize 를 하나의 흐름으로 묶는다.

## 4. 단건 분석과 job 기반 분석의 차이

`photo-ranker` 를 이해할 때 가장 먼저 구분해야 할 것은 sync 분석과 job 기반 분석이다.

### sync 분석

예:

- `score_quality`
- `detect_faces`
- `describe_scene`
- `classify_event`
- `rank_best_shots`

특징:

- 현재 요청 안에서 바로 결과를 반환한다.
- thumbnail/base64 기반으로 빠르게 확인할 때 적합하다.
- UI state 나 job lifecycle 과 직접 연결되지 않을 수 있다.

### background job 분석

예:

- `start_classify_job`
- `get_job_status`
- `get_job_result`

특징:

- source 를 읽고, 많은 사진을 처리하고, 결과를 DB/queue 에 남긴다.
- `photos-mcp` UI 와 health 의 active/recent jobs 에 반영된다.
- review, export, organize 같은 후속 작업의 기반이 된다.

## 5. job source of truth

job 상태의 source of truth는 vendor `jobs` 테이블과 facade `workflow_runs`가 함께 들어 있는 단일 `photo-ranker/jobs.db`다. background workflow와 vendor pipeline은 같은 `run_id`를 사용하고 `PhotosMcpStateStore`는 이 저장소를 메뉴와 health용 snapshot으로 투영한다. in-memory queue는 현재 프로세스의 task 실행만 담당하며 영속 상태의 기준이 아니다.

`photos-mcp` 가 추가로 하는 일은 아래다.

- job 응답을 공통 envelope 로 정규화
- active/recent job snapshot 유지
- menu UI 와 `/health` 로 projection 노출
- cancel/delete/clear 같은 UI action 을 adapter 로 위임

따라서 job 문제가 생기면 아래 두 층을 구분해야 한다.

1. `jobs.db`의 workflow와 pipeline 상태 문제
2. 현재 프로세스 queue 또는 `photos-mcp` projection 반영 문제

## 6. review 흐름

review 계층은 단순 결과 조회보다 한 단계 위다. “좋은 사진을 골라 사람이 승인하고 내보내는 흐름”을 위해 존재한다.

대표 흐름:

1. `start_classify_job`
2. `get_job_summary`
3. `get_review_items`
4. `set_photo_review`
5. `export_selected_photos` 또는 `organize_results`

face review 가 필요하면 아래가 추가된다.

1. `list_photo_faces`
2. `label_face_in_job`
3. 필요 시 `register_face_from_job`

## 7. Apple Photos write-back

`photo-ranker` 의 강한 차별점은 분석에서 끝나지 않고 Apple Photos 에 결과를 다시 반영한다는 점이다.

대표 tool:

- `create_album`
- `add_to_album`
- `organize_results`
- `list_photo_albums`
- `curate_best_photos`
- `classify_and_organize`

이 경로는 아래 조건과 자주 얽힌다.

- Photos Automation permission
- Terminal helper bootstrap
- bundle import 안정성
- local job 과 apple job 의 write-back 차이

예를 들어 `organize_results_to_directory` 는 local job 전용이고, Apple Photos write-back 은 `organize_results` 쪽이 담당한다.

## 8. end-to-end workflow 도구를 어떻게 봐야 하는가

아래 tool 은 여러 단계를 하나로 합친 orchestration 도구다.

- `curate_best_photos`
- `classify_and_organize`
- `import_and_organize`

이 tool 들은 편리하지만, 문제를 디버깅할 때는 각 단계를 쪼개서 봐야 한다.

예:

1. source 에서 사진을 읽는가
2. classify 결과가 만들어지는가
3. review/export/write-back 이 되는가

문제가 생기면 `05-mcp-call-flows.md` 의 분해된 호출 흐름을 기준으로 좁혀 가는 편이 빠르다.

## 9. selection profile

여러 분류/선정 tool 은 `selection_profile` 을 사용한다. 현재 코드 기준 대표 profile 은 아래와 같다.

- `general`
- `person`
- `landscape`

이 값은 어떤 점수 필드를 더 강조할지, 어떤 사진을 우선 선택할지에 영향을 준다. 따라서 결과 비교를 할 때는 source 나 limit 뿐 아니라 profile 도 함께 봐야 한다.

## 10. 유사 장면 대표 선택

분류 결과는 촬영 시각, Apple 연사 관계, 인물 집합과 macOS Vision FeaturePrint를 이용해 유사 촬영 장면으로 묶는다. 각 장면에서는 비용이 큰 이미지 분석 후보를 최대 4장으로 줄이고, 품질 하한과 시각적 다양성을 통과한 사진만 최대 2장까지 추천한다.

결과 항목에는 `scene_cluster_id`, `scene_cluster_size`, `cluster_rank`, `recommended_in_cluster`, `recommendation_slot`, `selection_reason_codes`가 포함된다. 같은 장면의 나머지 사진은 삭제하거나 숨기지 않고 `같은 장면 대안`으로 유지한다. 이 단계는 읽기 전용이며 Apple 사진과 앨범을 변경하지 않는다.

## 11. 언제 `photo-ranker` 문서를 먼저 봐야 하는가

아래 상황이면 이 문서를 먼저 보는 것이 맞다.

- 품질 점수나 event 분류가 기대와 다르다.
- classify job 상태는 보이는데 결과가 이상하다.
- review item, selected photo, face label 흐름이 헷갈린다.
- Apple Photos album organize 가 실패한다.
- 단건 분석과 job 기반 workflow 의 차이를 알고 싶다.

`photo-ranker` 는 `photos-mcp` 의 가치가 실제로 발생하는 층이다. 따라서 기능 설명, 사용자 시나리오, 디버깅 모두 여기에서 중심이 잡힌다.
