# llm integration sample validation report

- generated_at: 2026-05-20T21:42:47.400994+00:00
- endpoint: http://127.0.0.1:18791/mcp

## sample results

- [x] 연결 상태 요약
  - sample_id: status-summary
  - user_prompt: photos-mcp 연결 상태와 현재 준비 상태를 알려줘.
  - expected_tools: photos_status
  - evidence: {"capabilities": {"checks": [{"detail": "Sample photo loaded: D58AF76F-AC27-4578-85F3-3AE3C905ABF0", "hint": "", "key": "photos_read", "status": "ok", "summary": "Apple Photos library is readable.", "title": "Photos Library Read"}, {"det...
- [x] 5월 3일 잘 나온 사진 앨범 저장
  - sample_id: apple-may3-best-to-album
  - user_prompt: iCloud 사진 중 올해 5월 3일 사진들만 잘 나온 사진들만 앨범을 따로 저장해 만들어줘.
  - expected_tools: photos_run -> photos_run
  - evidence: {"cleanup": {"album": "photos-mcp llm apple-may3-best-to-album 20260521-064228", "deleted": true, "finished_at": "", "intent": "cleanup_album", "request_kind": "photos_run", "result_available": true, "run_id": "cleanup_album-202605202142...
  - note: album is created for validation and should be cleaned up immediately
- [ ] SamplePhotos 잘 나온 사진 iCloud 앨범 저장
  - sample_id: local-samplephotos-best-to-album
  - user_prompt: 로컬 ~/SamplePhotos 디렉토리에 잘 나온 사진들을 골라서 iCloud 에 적절한 이름으로 앨범을 만들어 저장해줘.
  - expected_tools: photos_run -> photos_result -> photos_run -> photos_run
  - note: sample photos directory is missing: /Users/byoungyoungla/SamplePhotos
- [ ] 5월 3일 특정인 잘 나온 사진 로컬 저장
  - sample_id: apple-may3-person-best-to-local-dir
  - user_prompt: iCloud 사진 중 올해 5월 3일 사진들 중 특정인의 사진만 뽑아서 잘 나온 사진들을 로컬의 특정(~/temp) 디렉토리에 저장해줘.
  - expected_tools: photos_run -> photos_result
  - note: target person is not configured and no person metadata was discoverable for the target date
