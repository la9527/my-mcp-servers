# live validation report

- generated_at: 2026-05-22T22:59:51.964626+00:00
- endpoint: http://127.0.0.1:18791/mcp
- health_url: http://127.0.0.1:18791/health
- include_workflows: false

## runtime / transport

- [x] installed bundle --health responds
  - evidence: {"status": "ok", "app_name": "PhotosMcp", "bundle_id": "com.nanobot.photos-mcp", "bundle_path": "/Users/byoungyoungla/Applications/PhotosMcp.app", "endpoint": "http://127.0.0.1:18791/mcp", "health_endpoint": "http://127.0.0.1:18791/health"}
- [x] wrapper script launches the live app
  - evidence: PhotosMcp launched in background (pid=50979, health=http://127.0.0.1:18791/health, log=/Users/byoungyoungla/.photos-mcp/logs/launcher.log)
- [x] /health responds with ok transport
  - evidence: {"daemon_status": "ready", "preflight_status": "warning", "status": "ok"}
- [x] /health/capabilities exposes checks
  - evidence: {"check_count": 4, "status": "warning"}
- [x] MCP tool inventory exposes only 4 facade tools
  - evidence: ["photos_query", "photos_select", "photos_workflow", "photos_write"]

## photos_query/status

- [x] summary view returns transport, capabilities, running, latest
  - evidence: {"capabilities": {"checks": [{"detail": "PhotoKit status=denied status_code=2 requested=true", "hint": "Open macOS Settings > Privacy & Security > Photos and allow PhotosMcp. Without this, iCloud-only originals may require Terminal fallb...
- [x] checks view returns preflight entries
  - evidence: {"capabilities": {"checks": [{"detail": "PhotoKit status=denied status_code=2 requested=true", "hint": "Open macOS Settings > Privacy & Security > Photos and allow PhotosMcp. Without this, iCloud-only originals may require Terminal fallb...
- [x] running view returns running/latest shape
  - evidence: {"finished_at": "", "latest": {"request_kind": "general", "run_id": "ec4a785c", "status": "completed"}, "request_kind": "photos_query", "result_available": false, "running": {"active": false, "count": 0, "current_run_id": ""}, "status": ...
- [x] latest view returns latest shape
  - evidence: {"finished_at": "", "latest": {"request_kind": "general", "run_id": "ec4a785c", "status": "completed"}, "request_kind": "photos_query", "result_available": false, "status": "ok", "summary_available": false, "terminal": false}

## photos_query/library

- [x] list action returns items with source aliases
  - evidence: {"action": "list", "analyze_ready_count": 1, "count": 20, "download_required_count": 19, "items": [{"albums": [], "analyze_recommended": false, "date_taken": "2018-04-03T21:33:35.273151+09:00", "download_hint": "Open the asset in Photos ...
- [x] apple list excludes video assets from candidates
  - evidence: [{"albums": [], "analyze_recommended": false, "date_taken": "2018-04-03T21:33:35.273151+09:00", "download_hint": "Open the asset in Photos and wait for the original to download locally, then rerun photos_query(action=\"list\") and confir...
- [x] ready_only keeps only analyze-ready items
  - note: partial when no analyze-ready items are currently available
  - evidence: {"action": "ready_only", "analyze_ready_count": 1, "count": 1, "download_required_count": 0, "items": [{"albums": [], "analyze_recommended": true, "date_taken": "2023-10-18T08:32:15.906000+09:00", "filename": "1C263D4A-9AAF-4009-BEB7-72F...
- [x] search action returns stable shape
  - evidence: {"action": "search", "analyze_ready_count": 0, "count": 10, "download_required_count": 10, "items": [{"albums": [], "analyze_recommended": false, "date_taken": "2018-04-03T21:33:35.273151+09:00", "download_hint": "Open the asset in Photo...
- [x] inspect action returns metadata/thumbnail shape
  - evidence: {"action": "inspect", "item": {"metadata": {"albums": [], "camera_make": "Apple", "camera_model": "iPhone 14 Pro Max", "date_taken": "2023-10-18T08:32:15.906000+09:00", "exposure_time": "", "filename": "1C263D4A-9AAF-4009-BEB7-72F76F1FAD...
- [x] library item guidance fields are populated consistently
  - evidence: [{"albums": [], "analyze_recommended": false, "date_taken": "2018-04-03T21:33:35.273151+09:00", "download_hint": "Open the asset in Photos and wait for the original to download locally, then rerun photos_query(action=\"list\") and confir...

## photos_select/photos_write

- [x] local analyze succeeds immediately
  - evidence: {"action": "analyze_photo", "finished_at": "", "intent": "analyze", "job_id": "analyze-20260522225923934863", "photo_id": "1C263D4A-9AAF-4009-BEB7-72F76F1FAD2C", "request_kind": "photos_run", "result": {"event": {"confidence": 0.5, "even...
- [!] non-local analyze without wait returns structured blocked payload
  - evidence: {"text": "Error executing tool photos_select: cannot identify image file '/var/folders/b4/r2gvc5hs47944p2z9zt3ygv80000gn/T/photo-source-apple-cache-16n3d01h/E945E6B6-6AFB-44D9-B24E-38EF5F56C272/IMG_7573.HEIC'"}
- [!] wait_for_local starts a synthetic waiting run
  - evidence: {"text": "Error executing tool photos_select: cannot identify image file '/Users/byoungyoungla/Pictures/Photos Library.photoslibrary/originals/E/E945E6B6-6AFB-44D9-B24E-38EF5F56C272.heic'"}
- [!] synthetic wait run can be cancelled
  - evidence: {"action": "summary", "created_at": 1779453916.747283, "error_message": null, "finished_at": 1779453921.5068061, "intent": "result", "job_id": "ec4a785c", "photo_count": 26, "preview_path": "/Users/byoungyoungla/.photos-mcp/runtime/photo...
- [-] synthetic wait run can time out with structured failure
  - note: completed is possible if the asset downloads while polling
  - evidence: {"action": "summary", "created_at": 1779453916.747283, "error_message": null, "finished_at": 1779453921.5068061, "intent": "result", "job_id": "ec4a785c", "photo_count": 26, "preview_path": "/Users/byoungyoungla/.photos-mcp/runtime/photo...
- [ ] background workflow intents are covered when explicitly enabled
  - note: re-run with --include-workflows to exercise classify/curate/organize/import

## photos_query/result

- [x] synthetic wait summary exposes progress fields
  - evidence: {"action": "summary", "created_at": 1779453916.747283, "error_message": null, "finished_at": 1779453921.5068061, "intent": "result", "job_id": "ec4a785c", "photo_count": 26, "preview_path": "/Users/byoungyoungla/.photos-mcp/runtime/photo...
- [!] synthetic wait result stays unavailable before terminal completion
  - evidence: {"action": "result", "items": [{"capture_date": "", "event_score": 0.0, "event_type": "other", "faces_detected": 0, "family_score": 0.0, "known_persons": [], "meaningful_score": 5, "photo_id": "359D35BB-3A8A-47BB-9EBC-C31CEE49F7AB", "qua...
- [!] synthetic wait cancel transitions to cancelled summary
  - evidence: {"created_at": 1779453916.747283, "error_message": null, "finished_at": 1779453921.5068061, "id": "ec4a785c", "intent": "result", "job_id": "ec4a785c", "progress": {"completed": 22, "current_file": "1F910FC0-D951-4E35-AFF9-BA23A2913624",...
- [ ] vendor-run result actions are available when workflow validation is enabled
  - note: vendor-run result validation requires --include-workflows
