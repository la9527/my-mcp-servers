# live validation report

- generated_at: 2026-05-20T08:23:38.239092+00:00
- endpoint: http://127.0.0.1:18791/mcp
- health_url: http://127.0.0.1:18791/health
- include_workflows: true

## runtime / transport

- [x] installed bundle --health responds
  - evidence: {"status": "ok", "app_name": "PhotosMcp", "bundle_id": "com.nanobot.photos-mcp", "bundle_path": "/Users/byoungyoungla/Applications/PhotosMcp.app", "endpoint": "http://127.0.0.1:18791/mcp", "health_endpoint": "http://127.0.0.1:18791/health"}
- [ ] wrapper script launches the live app
  - note: wrapper_script not provided
- [x] /health responds with ok transport
  - evidence: {"daemon_status": "busy", "preflight_status": "ok", "status": "ok"}
- [x] /health/capabilities exposes checks
  - evidence: {"check_count": 3, "status": "ok"}
- [x] MCP tool inventory exposes only 4 facade tools
  - evidence: ["photos_library", "photos_result", "photos_run", "photos_status"]

## photos_status

- [x] summary view returns transport, capabilities, running, latest
  - evidence: {"capabilities": {"checks": [{"detail": "Sample photo loaded: 05C8616F-86B2-4DD2-BF49-88B189F0D2BF", "hint": "", "key": "photos_read", "status": "ok", "summary": "Apple Photos library is readable.", "title": "Photos Library Read"}, {"det...
- [x] checks view returns preflight entries
  - evidence: {"capabilities": {"checks": [{"detail": "Sample photo loaded: 05C8616F-86B2-4DD2-BF49-88B189F0D2BF", "hint": "", "key": "photos_read", "status": "ok", "summary": "Apple Photos library is readable.", "title": "Photos Library Read"}, {"det...
- [x] running view returns running/latest shape
  - evidence: {"latest": {"request_kind": "general", "run_id": "d7698590", "status": "running"}, "running": {"active": true, "count": 2, "current_run_id": "d7698590"}, "status": "ok"}
- [x] latest view returns latest shape
  - evidence: {"latest": {"request_kind": "general", "run_id": "d7698590", "status": "running"}, "status": "ok"}

## photos_library

- [x] list action returns items with source aliases
  - evidence: {"action": "list", "analyze_ready_count": 2, "count": 20, "download_required_count": 18, "items": [{"albums": ["윤지50일"], "analyze_recommended": false, "date_taken": "2023-12-03T10:18:07+09:00", "download_hint": "Open the asset in Photos ...
- [x] ready_only keeps only analyze-ready items
  - note: partial when no analyze-ready items are currently available
  - evidence: {"action": "ready_only", "analyze_ready_count": 2, "count": 2, "download_required_count": 0, "items": [{"albums": [], "analyze_recommended": true, "date_taken": "2025-05-17T18:17:38.110000+09:00", "filename": "FF768F09-40CE-4E59-B7A7-F42...
- [x] search action returns stable shape
  - evidence: {"action": "search", "analyze_ready_count": 2, "count": 10, "download_required_count": 8, "items": [{"albums": ["윤지50일"], "analyze_recommended": false, "date_taken": "2023-12-03T10:18:07+09:00", "download_hint": "Open the asset in Photos...
- [x] inspect action returns metadata/thumbnail shape
  - evidence: {"action": "inspect", "item": {"metadata": {"albums": [], "camera_make": "Apple", "camera_model": "iPhone 14 Pro Max", "date_taken": "2025-05-17T18:17:38.110000+09:00", "exposure_time": "", "filename": "FF768F09-40CE-4E59-B7A7-F42B34F494...
- [x] library item guidance fields are populated consistently
  - evidence: [{"albums": ["윤지50일"], "analyze_recommended": false, "date_taken": "2023-12-03T10:18:07+09:00", "download_hint": "Open the asset in Photos and wait for the original to download locally, then rerun photos_library and confirm local_path_av...

## photos_run

- [x] local analyze succeeds immediately
  - evidence: {"intent": "analyze", "job_id": "analyze-20260520082219576316", "photo_id": "FF768F09-40CE-4E59-B7A7-F42B34F4942B", "request_kind": "photos_run", "result": {"event": {"confidence": 0.9, "event_type": "daily"}, "faces": [], "quality": {"a...
- [x] non-local analyze without wait returns structured blocked payload
  - evidence: {"can_retry": true, "detail": "Photo metadata was readable, but thumbnail export returned no bytes. filename=05C8616F-86B2-4DD2-BF49-88B189F0D2BF.jpeg date_taken=2023-12-03T10:18:07+09:00 current_photo_local_path_available=false runtime_...
- [x] wait_for_local starts a synthetic waiting run
  - evidence: {"can_retry": true, "current_photo_local_path_available": false, "detail": "Photo metadata was readable, but thumbnail export returned no bytes. filename=05C8616F-86B2-4DD2-BF49-88B189F0D2BF.jpeg date_taken=2023-12-03T10:18:07+09:00 curr...
- [x] synthetic wait run can be cancelled
  - evidence: {"action": "summary", "can_retry": true, "detail": "The local download wait was cancelled before analyze could continue.", "download_hint": "Open the asset in Photos and wait for the original to download locally, then rerun photos_librar...
- [x] synthetic wait run can time out with structured failure
  - note: completed is possible if the asset downloads while polling
  - evidence: {"action": "summary", "can_retry": true, "detail": "Photo metadata was readable, but thumbnail export returned no bytes. filename=05C8616F-86B2-4DD2-BF49-88B189F0D2BF.jpeg date_taken=2023-12-03T10:18:07+09:00 current_photo_local_path_ava...
- [!] background workflow intents are covered when explicitly enabled
  - note: local classify results became queryable, but summary status remained non-terminal during the validation window
  - evidence: {"classify": {"finished_at": "", "intent": "classify", "job_id": "7c80196f", "request_kind": "photos_run", "result_available": false, "run_id": "7c80196f", "status": "pending", "summary_available": false, "terminal": false}, "classify_su...

## photos_result

- [x] synthetic wait summary exposes progress fields
  - evidence: {"action": "summary", "can_retry": true, "detail": "Photo metadata was readable, but thumbnail export returned no bytes. filename=05C8616F-86B2-4DD2-BF49-88B189F0D2BF.jpeg date_taken=2023-12-03T10:18:07+09:00 current_photo_local_path_ava...
- [x] synthetic wait result stays unavailable before terminal completion
  - note: pending result became terminal after timeout validation
  - evidence: {"action": "result", "finished_at": "", "request_kind": "photos_result", "result_available": false, "run_id": "analyze-20260520082241871309", "status": "failed", "summary_available": true, "terminal": true}
- [x] synthetic wait cancel transitions to cancelled summary
  - evidence: {"action": "cancel", "can_retry": true, "current_photo_local_path_available": false, "detail": "Photo metadata was readable, but thumbnail export returned no bytes. filename=05C8616F-86B2-4DD2-BF49-88B189F0D2BF.jpeg date_taken=2023-12-03...
- [x] vendor-run result actions are available when workflow validation is enabled
  - evidence: {"artifacts": {"action": "artifacts", "preview_path": "/Users/byoungyoungla/.photos-mcp/runtime/photo-ranker/artifacts/7c80196f/previews/ba741ad51e2995b6bc22.jpg", "run_id": "7c80196f", "selected_count": 0}, "result": {"action": "result"...
