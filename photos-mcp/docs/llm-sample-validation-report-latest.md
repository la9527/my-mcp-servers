# llm integration sample validation report

- generated_at: 2026-05-21T01:38:04.014576+00:00
- endpoint: http://127.0.0.1:18791/mcp

## sample results

- [x] 연결 상태 요약
  - sample_id: status-summary
  - user_prompt: photos-mcp 연결 상태와 현재 준비 상태를 알려줘.
  - expected_tools: photos_status
  - evidence: {"capabilities": {"checks": [{"detail": "Sample photo loaded: 550F4053-D40B-43D9-A6FC-EFF2F1AD434A", "hint": "", "key": "photos_read", "status": "ok", "summary": "Apple Photos library is readable.", "title": "Photos Library Read"}, {"det...
- [x] 작년 4월 16일~4월 30일 잘 나온 사진 앨범 저장
  - sample_id: apple-apr16to30-best-to-album
  - user_prompt: iCloud 사진 중 작년 4월 16일~작년 4월30일 사진들만 잘 나온 사진들만 앨범을 따로 저장해 만들어줘.
  - expected_tools: photos_run -> photos_run
  - evidence: {"cleanup": {"album": "photos-mcp llm apple-apr16to30-best-to-album 20260521-103519", "deleted": true, "finished_at": "", "intent": "cleanup_album", "request_kind": "photos_run", "result_available": true, "run_id": "cleanup_album-2026052...
  - note: album is created for validation and should be cleaned up immediately
- [x] SamplePhotos 잘 나온 사진 iCloud 앨범 저장
  - sample_id: local-samplephotos-best-to-album
  - user_prompt: 로컬 ~/SamplePhotos 디렉토리에 잘 나온 사진들을 골라서 iCloud 에 적절한 이름으로 앨범을 만들어 저장해줘.
  - expected_tools: photos_run -> photos_result -> photos_run -> photos_run
  - evidence: {"cleanup": {"album": "photos-mcp llm local-samplephotos-best-to-album 20260521-103728", "deleted": true, "finished_at": "", "intent": "cleanup_album", "request_kind": "photos_run", "result_available": true, "run_id": "cleanup_album-2026...
  - note: selected_paths=4 dropped_screen_captures=0
- [x] 작년 4월 16일~4월 30일 특정인 잘 나온 사진 로컬 저장
  - sample_id: apple-apr16to30-person-best-to-local-dir
  - user_prompt: iCloud 사진 중 작년 4월 16일~작년 4월30일 사진들 중 특정인의 사진만 뽑아서 잘 나온 사진들을 로컬의 특정(~/temp) 디렉토리에 저장해줘.
  - expected_tools: photos_run -> photos_result
  - evidence: {"curate": {"album_result": null, "excluded_screen_capture_count": 0, "excluded_screen_capture_ids": [], "finished_at": "", "intent": "curate", "job_id": "e20509a0", "quality_policy": {"mode": "profile_top_percent", "quality_min_score": ...
  - note: target_person=지수 output_dir=/Users/byoungyoungla/temp/apple-apr16to30-person-best-to-local-dir-jt4kiqop
