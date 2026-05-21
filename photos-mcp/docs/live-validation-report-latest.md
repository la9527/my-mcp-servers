# live validation report

- generated_at: 2026-05-21T01:35:11.309148+00:00
- endpoint: http://127.0.0.1:18791/mcp
- health_url: http://127.0.0.1:18791/health
- include_workflows: true

## runtime / transport

- [ ] installed bundle --health responds
  - note: bundle_path not provided
- [ ] wrapper script launches the live app
  - note: wrapper_script not provided
- [x] /health responds with ok transport
  - evidence: {"daemon_status": "ready", "preflight_status": "warning", "status": "ok"}
- [x] /health/capabilities exposes checks
  - evidence: {"check_count": 3, "status": "warning"}
- [x] MCP tool inventory exposes only 4 facade tools
  - evidence: ["photos_library", "photos_result", "photos_run", "photos_status"]

## photos_status

- [x] summary view returns transport, capabilities, running, latest
  - evidence: {"capabilities": {"checks": [{"detail": "Sample photo loaded: 550F4053-D40B-43D9-A6FC-EFF2F1AD434A", "hint": "", "key": "photos_read", "status": "ok", "summary": "Apple Photos library is readable.", "title": "Photos Library Read"}, {"det...
- [x] checks view returns preflight entries
  - evidence: {"capabilities": {"checks": [{"detail": "Sample photo loaded: 550F4053-D40B-43D9-A6FC-EFF2F1AD434A", "hint": "", "key": "photos_read", "status": "ok", "summary": "Apple Photos library is readable.", "title": "Photos Library Read"}, {"det...
- [x] running view returns running/latest shape
  - evidence: {"latest": {"request_kind": "general", "run_id": "d99aba51", "status": "completed"}, "running": {"active": false, "count": 0, "current_run_id": ""}, "status": "ok"}
- [x] latest view returns latest shape
  - evidence: {"latest": {"request_kind": "general", "run_id": "d99aba51", "status": "completed"}, "status": "ok"}

## photos_library

- [x] list action returns items with source aliases
  - evidence: {"action": "list", "analyze_ready_count": 3, "count": 20, "download_required_count": 17, "items": [{"albums": [], "analyze_recommended": false, "date_taken": "2025-01-29T17:19:34.254000+09:00", "download_hint": "Open the asset in Photos ...
- [x] ready_only keeps only analyze-ready items
  - note: partial when no analyze-ready items are currently available
  - evidence: {"action": "ready_only", "analyze_ready_count": 3, "count": 3, "download_required_count": 0, "items": [{"albums": [], "analyze_recommended": true, "date_taken": "2025-06-27T18:55:19.038000+09:00", "filename": "EE8003D6-AF8C-41FC-87F8-863...
- [x] search action returns stable shape
  - evidence: {"action": "search", "analyze_ready_count": 0, "count": 10, "download_required_count": 10, "items": [{"albums": [], "analyze_recommended": false, "date_taken": "2025-01-29T17:19:34.254000+09:00", "download_hint": "Open the asset in Photo...
- [x] inspect action returns metadata/thumbnail shape
  - evidence: {"action": "inspect", "item": {"metadata": {"albums": [], "camera_make": "Apple", "camera_model": "iPhone 14 Pro Max", "date_taken": "2025-06-27T18:55:19.038000+09:00", "exposure_time": "", "filename": "EE8003D6-AF8C-41FC-87F8-863B4DA95D...
- [x] library item guidance fields are populated consistently
  - evidence: [{"albums": [], "analyze_recommended": false, "date_taken": "2025-01-29T17:19:34.254000+09:00", "download_hint": "Open the asset in Photos and wait for the original to download locally, then rerun photos_library and confirm local_path_av...

## photos_run

- [x] local analyze succeeds immediately
  - evidence: {"intent": "analyze", "job_id": "analyze-20260521013436220854", "photo_id": "EE8003D6-AF8C-41FC-87F8-863B4DA95DCC", "request_kind": "photos_run", "result": {"event": {"confidence": 0.9, "event_type": "portrait"}, "faces": [], "quality": ...
- [x] non-local analyze without wait returns structured blocked payload
  - evidence: {"can_retry": true, "detail": "Photo metadata was readable, but thumbnail export returned no bytes. filename=550F4053-D40B-43D9-A6FC-EFF2F1AD434A.jpeg date_taken=2025-01-29T17:19:34.254000+09:00 current_photo_local_path_available=false r...
- [x] wait_for_local starts a synthetic waiting run
  - evidence: {"can_retry": true, "current_photo_local_path_available": false, "detail": "Waiting for the selected photo to download locally.", "download_hint": "Open the asset in Photos and wait for the original to download locally, then rerun photos...
- [x] synthetic wait run can be cancelled
  - evidence: {"action": "summary", "can_retry": true, "detail": "The local download wait was cancelled before analyze could continue.", "download_hint": "Open the asset in Photos and wait for the original to download locally, then rerun photos_librar...
- [x] synthetic wait run can time out with structured failure
  - note: completed is possible if the asset downloads while polling
  - evidence: {"action": "summary", "can_retry": true, "detail": "Photo metadata was readable, but thumbnail export returned no bytes. filename=550F4053-D40B-43D9-A6FC-EFF2F1AD434A.jpeg date_taken=2025-01-29T17:19:34.254000+09:00 current_photo_local_p...
- [x] background workflow intents are covered when explicitly enabled
  - note: import validation uses an empty input list to avoid mutating the live Photos library
  - evidence: {"classify": {"finished_at": "", "intent": "classify", "job_id": "5364e051", "request_kind": "photos_run", "result_available": false, "run_id": "5364e051", "status": "pending", "summary_available": false, "terminal": false}, "classify_su...

## photos_result

- [x] synthetic wait summary exposes progress fields
  - evidence: {"action": "summary", "can_retry": true, "current_photo_local_path_available": false, "detail": "Waiting for the selected photo to download locally.", "download_hint": "Open the asset in Photos and wait for the original to download local...
- [x] synthetic wait result stays unavailable before terminal completion
  - note: pending result became terminal after timeout validation
  - evidence: {"action": "result", "finished_at": "", "request_kind": "photos_result", "result_available": false, "run_id": "analyze-20260521013450763546", "status": "running", "summary_available": true, "terminal": false}
- [x] synthetic wait cancel transitions to cancelled summary
  - evidence: {"action": "cancel", "can_retry": true, "current_photo_local_path_available": false, "detail": "Waiting for the selected photo to download locally.", "download_hint": "Open the asset in Photos and wait for the original to download locall...
- [x] vendor-run result actions are available when workflow validation is enabled
  - evidence: {"artifacts": {"action": "artifacts", "preview_path": "/Users/byoungyoungla/.photos-mcp/runtime/photo-ranker/artifacts/5364e051/previews/0d9db2614b4e20105e62.jpg", "run_id": "5364e051", "selected_count": 0}, "result": {"action": "result"...
