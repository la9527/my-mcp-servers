# llm integration sample validation report

- generated_at: 2026-05-22T23:01:32.218392+00:00
- endpoint: http://127.0.0.1:18791/mcp

## sample results

- [x] 연결 상태 요약
  - sample_id: status-summary
  - user_prompt: photos-mcp 연결 상태와 현재 준비 상태를 알려줘.
  - expected_tools: photos_query(action=status)
  - evidence: {"capabilities": {"checks": [{"detail": "PhotoKit status=denied status_code=2 requested=false", "hint": "Open macOS Settings > Privacy & Security > Photos and allow PhotosMcp. Without this, iCloud-only originals may require Terminal fall...
- [x] 작년 4월 16일~4월 30일 잘 나온 사진 앨범 저장
  - sample_id: apple-apr16to30-best-to-album
  - user_prompt: iCloud 사진 중 작년 4월 16일~작년 4월30일 사진들만 잘 나온 사진들만 앨범을 따로 저장해 만들어줘.
  - expected_tools: photos_workflow(action=curate_to_album) -> photos_write(action=cleanup_album)
  - evidence: {"cleanup": {"action": "cleanup_album", "album": "photos-mcp llm apple-apr16to30-best-to-album 20260523-080003", "deleted": true, "finished_at": "", "intent": "cleanup_album", "request_kind": "photos_write", "result_available": true, "ru...
  - note: album is created for validation and should be cleaned up immediately
- [x] SamplePhotos 잘 나온 사진 iCloud 앨범 저장
  - sample_id: local-samplephotos-best-to-album
  - user_prompt: 로컬 ~/SamplePhotos 디렉토리에 잘 나온 사진들을 골라서 iCloud 에 적절한 이름으로 앨범을 만들어 저장해줘.
  - expected_tools: photos_select(action=select_best) -> photos_query(action=selected) -> photos_write(action=import_to_album) -> photos_write(action=cleanup_album)
  - evidence: {"cleanup": {"action": "cleanup_album", "album": "photos-mcp llm local-samplephotos-best-to-album 20260523-080039", "deleted": true, "finished_at": "", "intent": "cleanup_album", "request_kind": "photos_write", "result_available": true, ...
  - note: selected_paths=4 dropped_screen_captures=0
- [x] 작년 4월 16일~4월 30일 특정인 잘 나온 사진 로컬 저장
  - sample_id: apple-apr16to30-person-best-to-local-dir
  - user_prompt: iCloud 사진 중 작년 4월 16일~작년 4월30일 사진들 중 특정인의 사진만 뽑아서 잘 나온 사진들을 로컬의 특정(~/temp) 디렉토리에 저장해줘.
  - expected_tools: photos_select(action=select_best_person) -> photos_write(action=export_selected)
  - evidence: {"curate": {"action": "select_best_person", "excluded_screen_capture_count": 0, "excluded_screen_capture_ids": [], "finished_at": "", "intent": "curate", "job_id": "9d113af0", "quality_policy": {"mode": "profile_top_percent", "quality_mi...
  - note: target_person=라윤지 output_dir=/Users/byoungyoungla/temp/apple-apr16to30-person-best-to-local-dir-un17p0xr

## Nanobot WebUI rerun note

- [x] manual Nanobot WebUI rerun converges on grouped tools after Nanobot restart
  - evidence: pre-restart websocket session logged `mcp_photos-mcp_photos_status` -> `(MCP tool call failed: McpError)`
  - evidence: post-restart websocket session logged `mcp_photos-mcp_photos_query(action=status)` -> `mcp_photos-mcp_photos_query(action=list)` -> `mcp_photos-mcp_photos_select(action=select_best)` -> `mcp_photos-mcp_photos_workflow(action=curate_to_album)`
  - evidence: final workflow payload returned `request_kind="photos_workflow"`, `action="curate_to_album"`, `classification_album_created=false`, `touched_album_names=["2025년 4월 16일~4월 30일 - PhotosMCP"]`
  - note: the app log label `photos_run.curate` is an internal workflow step log, not proof of a legacy public MCP tool route.
