# legacy to facade mapping

## 1. 목적

이 문서는 현재 공개된 38개 MCP tool 을 새 facade surface 4개로 어떻게 흡수할지 정리한다. 단순히 “어느 tool 로 묶을 것인가”만이 아니라, public facade 로 남길지, internal substep 으로 내릴지, 1차 public surface 에서 제외할지도 함께 표시한다.

상태 표기:

- `facade-direct`: facade tool 의 직접 action 또는 intent 로 노출
- `facade-internal`: facade workflow 내부에서만 호출
- `defer-v1`: 1차 public surface 에서 제외

## 2. diagnostic

| legacy tool | target | status | note |
| --- | --- | --- | --- |
| `health_status` | `photos_status` | `facade-direct` | `view=summary/checks` 로 흡수 |

## 3. `photo-source` 5개

| legacy tool | target | status | note |
| --- | --- | --- | --- |
| `list_photos` | `photos_library` | `facade-direct` | `action=list` |
| `get_metadata` | `photos_library` | `facade-direct` | `action=inspect`, `include=metadata` |
| `get_thumbnail` | `photos_library` | `facade-direct` | `action=inspect`, `include=thumbnail` |
| `search_photos` | `photos_library` | `facade-direct` | `action=search` |
| `export_photos` | `photos_run` or `photos_result` | `facade-internal` | export 는 browse 보다는 output step 에 가까움 |

## 4. `photo-ranker` analysis

| legacy tool | target | status | note |
| --- | --- | --- | --- |
| `score_quality` | `photos_run` | `facade-direct` | `intent=analyze` 내부 step |
| `detect_faces` | `photos_run` | `facade-internal` | analyze detail 또는 advanced output |
| `describe_scene` | `photos_run` | `facade-direct` | `intent=analyze` summary 일부 |
| `classify_event` | `photos_run` | `facade-direct` | `intent=analyze` summary 일부 |
| `find_duplicates` | `photos_run` | `facade-direct` | `intent=analyze` 또는 `curate` 하위 step |
| `rank_best_shots` | `photos_run` | `facade-direct` | `intent=curate` 또는 `analyze` |

## 5. known faces

| legacy tool | target | status | note |
| --- | --- | --- | --- |
| `register_face` | none | `defer-v1` | advanced face memory 기능 |
| `list_known_faces` | none | `defer-v1` | advanced 관리 기능 |
| `register_face_from_job` | none | `defer-v1` | review/debug 경로 |
| `delete_known_face` | none | `defer-v1` | 운영성 관리 기능 |

## 6. background jobs

| legacy tool | target | status | note |
| --- | --- | --- | --- |
| `start_classify_job` | `photos_run` | `facade-direct` | `intent=classify` |
| `get_job_status` | `photos_status` or `photos_result` | `facade-direct` | `running` / `summary` view 로 단순화 |
| `get_job_summary` | `photos_result` | `facade-direct` | `action=summary` |
| `get_job_result` | `photos_result` | `facade-direct` | `action=result` |
| `cancel_job` | `photos_result` | `facade-direct` | `action=cancel` |
| `delete_job` | none | `defer-v1` | app UI 로 유지 |
| `clear_job_history` | none | `defer-v1` | app UI 로 유지 |
| `list_jobs` | none | `defer-v1` | current/latest 중심 모델과 충돌 |

## 7. review and curation

| legacy tool | target | status | note |
| --- | --- | --- | --- |
| `get_review_items` | `photos_result` | `facade-direct` | `action=selected` 또는 `action=result` 일부 |
| `set_photo_review` | none | `defer-v1` | fine-grained review editing 은 제외 |
| `export_selected_photos` | `photos_result` | `facade-direct` | `action=artifacts` 또는 `intent=curate` 결과 단계 |
| `curate_best_photos` | `photos_run` | `facade-direct` | `intent=curate` |
| `list_photo_faces` | none | `defer-v1` | advanced face review |
| `label_face_in_job` | none | `defer-v1` | advanced face review |

## 8. write-back and organize

| legacy tool | target | status | note |
| --- | --- | --- | --- |
| `create_album` | `photos_run` | `facade-internal` | organize/write-back step 으로 숨김 |
| `add_to_album` | `photos_run` | `facade-internal` | organize/write-back step 으로 숨김 |
| `organize_results` | `photos_run` | `facade-direct` | `intent=organize` |
| `organize_results_to_directory` | `photos_run` | `facade-direct` | `intent=organize` + local target |
| `import_photos` | `photos_run` | `facade-direct` | `intent=import` |
| `import_and_organize` | `photos_run` | `facade-direct` | `intent=import` + organize |
| `list_photo_albums` | `photos_library` or `photos_run` | `facade-internal` | 1차에서는 별도 public tool 로 두지 않음 |

## 9. end-to-end workflow

| legacy tool | target | status | note |
| --- | --- | --- | --- |
| `classify_and_organize` | `photos_run` | `facade-direct` | `intent=organize` 또는 `intent=classify` + writeback |

## 10. facade 별 수용 범위 요약

### `photos_status`

- `health_status`
- `get_job_status` 일부 의미

### `photos_library`

- `list_photos`
- `get_metadata`
- `get_thumbnail`
- `search_photos`

### `photos_run`

- 분석 tool 대부분
- `start_classify_job`
- `curate_best_photos`
- `organize_results`
- `organize_results_to_directory`
- `import_photos`
- `import_and_organize`
- `classify_and_organize`
- 내부적으로 `create_album`, `add_to_album` 등 호출

### `photos_result`

- `get_job_summary`
- `get_job_result`
- `get_review_items`
- `export_selected_photos`
- `cancel_job`

## 11. 1차에서 public surface 에서 빠지는 기능 요약

1차 제외 대상은 10개다.

- `register_face`
- `list_known_faces`
- `register_face_from_job`
- `delete_known_face`
- `delete_job`
- `clear_job_history`
- `list_jobs`
- `set_photo_review`
- `list_photo_faces`
- `label_face_in_job`

이 범위는 기능 삭제가 아니라 public MCP surface 축소다. 내부 구현이나 app UI, 이후 advanced/debug surface 에서는 계속 재사용할 수 있다.