# live validation report

- generated_at: 2026-05-23T01:05:23.887834+00:00
- endpoint: http://127.0.0.1:18791/mcp
- health_url: http://127.0.0.1:18791/health
- include_workflows: true

## runtime / transport

- [x] installed bundle --health responds
  - evidence: {"status": "ok", "app_name": "PhotosMcp", "bundle_id": "com.nanobot.photos-mcp", "bundle_path": "/Users/byoungyoungla/Applications/PhotosMcp.app", "endpoint": "http://127.0.0.1:18791/mcp", "health_endpoint": "http://127.0.0.1:18791/health"}
- [x] wrapper script launches the live app
  - evidence: PhotosMcp launched in background (pid=81242, health=http://127.0.0.1:18791/health, log=/Users/byoungyoungla/.photos-mcp/logs/launcher.log)
- [x] /health responds with ok transport
  - evidence: {"daemon_status": "ready", "preflight_status": "ok", "status": "ok"}
- [x] /health/capabilities exposes checks
  - evidence: {"check_count": 4, "status": "ok"}
- [x] MCP tool inventory exposes only 4 facade tools
  - evidence: ["photos_query", "photos_select", "photos_workflow", "photos_write"]

## photos_query/status

- [x] summary view returns transport, capabilities, running, latest
  - evidence: {"capabilities": {"checks": [{"detail": "PhotoKit status=authorized status_code=3 requested=false", "hint": "", "key": "photos_permission", "status": "ok", "summary": "Apple Photos permission is available.", "title": "Photos Permission"}...
- [x] checks view returns preflight entries
  - evidence: {"capabilities": {"checks": [{"detail": "PhotoKit status=authorized status_code=3 requested=false", "hint": "", "key": "photos_permission", "status": "ok", "summary": "Apple Photos permission is available.", "title": "Photos Permission"}...
- [x] running view returns running/latest shape
  - evidence: {"finished_at": "", "latest": {"request_kind": "person", "run_id": "3af62efd", "status": "completed"}, "request_kind": "photos_query", "result_available": false, "running": {"active": false, "count": 0, "current_run_id": ""}, "status": "...
- [x] latest view returns latest shape
  - evidence: {"finished_at": "", "latest": {"request_kind": "person", "run_id": "3af62efd", "status": "completed"}, "request_kind": "photos_query", "result_available": false, "status": "ok", "summary_available": false, "terminal": false}

## photos_query/library

- [x] list action returns items with source aliases
  - evidence: {"action": "list", "analyze_ready_count": 2, "count": 20, "download_required_count": 18, "items": [{"albums": [], "analyze_recommended": false, "date_taken": "2025-01-23T20:09:08.404000+09:00", "download_hint": "Open the asset in Photos ...
- [x] apple list excludes video assets from candidates
  - evidence: [{"albums": [], "analyze_recommended": false, "date_taken": "2025-01-23T20:09:08.404000+09:00", "download_hint": "Open the asset in Photos and wait for the original to download locally, then rerun photos_query(action=\"list\") and confir...
- [x] ready_only keeps only analyze-ready items
  - note: partial when no analyze-ready items are currently available
  - evidence: {"action": "ready_only", "analyze_ready_count": 2, "count": 2, "download_required_count": 0, "items": [{"albums": [], "analyze_recommended": true, "date_taken": "2024-03-31T10:28:14.746000+09:00", "filename": "5631D9C2-3322-4368-8CAB-BC1...
- [x] search action returns stable shape
  - evidence: {"action": "search", "analyze_ready_count": 5, "count": 10, "download_required_count": 5, "items": [{"albums": [], "analyze_recommended": true, "date_taken": "2024-03-31T10:28:14.746000+09:00", "filename": "5631D9C2-3322-4368-8CAB-BC158A...
- [x] inspect action returns metadata/thumbnail shape
  - evidence: {"action": "inspect", "item": {"metadata": {"albums": [], "camera_make": "Apple", "camera_model": "iPhone 14 Pro Max", "date_taken": "2024-03-31T10:28:14.746000+09:00", "exposure_time": "", "filename": "5631D9C2-3322-4368-8CAB-BC158A3121...
- [x] library item guidance fields are populated consistently
  - evidence: [{"albums": [], "analyze_recommended": false, "date_taken": "2025-01-23T20:09:08.404000+09:00", "download_hint": "Open the asset in Photos and wait for the original to download locally, then rerun photos_query(action=\"list\") and confir...

## photos_select/photos_write

- [!] local analyze succeeds immediately
  - evidence: {"text": "Error executing tool photos_select: mlx-vlm is not installed. Install with: uv pip install mlx-vlm"}
- [!] non-local analyze without wait returns structured blocked payload
  - evidence: {"text": "Error executing tool photos_select: mlx-vlm is not installed. Install with: uv pip install mlx-vlm"}
- [!] wait_for_local starts a synthetic waiting run
  - evidence: {"text": "Error executing tool photos_select: mlx-vlm is not installed. Install with: uv pip install mlx-vlm"}
- [!] synthetic wait run can be cancelled
  - evidence: {"action": "summary", "created_at": 1779498263.984256, "error_message": null, "finished_at": 1779498264.24988, "intent": "result", "job_id": "3af62efd", "photo_count": 20, "preview_path": "/Users/byoungyoungla/.photos-mcp/runtime/photo-r...
- [-] synthetic wait run can time out with structured failure
  - note: completed is possible if the asset downloads while polling
  - evidence: {"action": "summary", "created_at": 1779498263.984256, "error_message": null, "finished_at": 1779498264.24988, "intent": "result", "job_id": "3af62efd", "photo_count": 20, "preview_path": "/Users/byoungyoungla/.photos-mcp/runtime/photo-r...
- [!] background workflow intents are covered when explicitly enabled
  - note: import validation uses an empty input list to avoid mutating the live Photos library
  - evidence: {"classify": {"action": "classify_range", "finished_at": "", "intent": "classify", "job_id": "67d9a303", "request_kind": "photos_select", "result_available": false, "run_id": "67d9a303", "status": "pending", "summary_available": false, "...

## photos_query/result

- [x] synthetic wait summary exposes progress fields
  - evidence: {"action": "summary", "created_at": 1779498263.984256, "error_message": null, "finished_at": 1779498264.24988, "intent": "result", "job_id": "3af62efd", "photo_count": 20, "preview_path": "/Users/byoungyoungla/.photos-mcp/runtime/photo-r...
- [!] synthetic wait result stays unavailable before terminal completion
  - evidence: {"action": "result", "items": [{"capture_date": "", "event_score": 0.0, "event_type": "other", "faces_detected": 0, "family_score": 0.0, "known_persons": [], "meaningful_score": 5, "photo_id": "2EA2398F-3358-401F-9528-5448D9260A29", "qua...
- [!] synthetic wait cancel transitions to cancelled summary
  - evidence: {"created_at": 1779498263.984256, "error_message": null, "finished_at": 1779498264.24988, "id": "3af62efd", "intent": "result", "job_id": "3af62efd", "progress": {"completed": 12, "current_file": "058A7FDE-D9DB-43BC-8325-5C4F44D5DF28", "...
- [x] vendor-run result actions are available when workflow validation is enabled
  - evidence: {"artifacts": {"action": "artifacts", "preview_path": "/Users/byoungyoungla/.photos-mcp/runtime/photo-ranker/artifacts/67d9a303/previews/e5a3c0ba30996f1cb675.jpg", "run_id": "67d9a303", "selected_count": 0}, "result": {"action": "result"...
