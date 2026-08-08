# live validation report

- generated_at: 2026-08-01T19:03:56.058185+00:00
- endpoint: http://127.0.0.1:18791/mcp
- health_url: http://127.0.0.1:18791/health
- include_workflows: true

## runtime / transport

- [x] installed bundle --health responds
  - evidence: {"status": "ok", "app_name": "PhotosMcp", "bundle_id": "com.nanobot.photos-mcp", "bundle_path": "/Users/byoungyoungla/Applications/PhotosMcp.app", "endpoint": "http://127.0.0.1:18791/mcp", "health_endpoint": "http://127.0.0.1:18791/health"}
- [x] wrapper script launches the live app
  - evidence: PhotosMcp launched in background (pid=84083, health=http://127.0.0.1:18791/health, log=/Users/byoungyoungla/.photos-mcp/logs/launcher.log)
- [x] /health responds with ok transport
  - evidence: {"daemon_status": "ready", "preflight_status": "warning", "status": "ok"}
- [x] /health/capabilities exposes checks
  - evidence: {"check_count": 4, "status": "warning"}
- [x] MCP tool inventory exposes only 4 facade tools
  - evidence: ["photos_query", "photos_select", "photos_workflow", "photos_write"]

## photos_query/status

- [x] summary view returns transport, capabilities, running, latest
  - evidence: {"capabilities": {"checks": [{"detail": "[비식별 처리됨]", "hint": "", "key": "photos_permission", "status": "ok", "summary": "Apple Photos permission is available.", "title": "Photos Permission"}, {"detail": "[비식별 처리됨]", "hint": "", "key": "p...
- [x] checks view returns preflight entries
  - evidence: {"capabilities": {"checks": [{"detail": "[비식별 처리됨]", "hint": "", "key": "photos_permission", "status": "ok", "summary": "Apple Photos permission is available.", "title": "Photos Permission"}, {"detail": "[비식별 처리됨]", "hint": "", "key": "p...
- [x] running view returns running/latest shape
  - evidence: {"latest": {"request_kind": "photos_run", "run_id": "analyze-20260801190244111808", "status": "cancelled"}, "running": {"active": false, "count": 0, "current_run_id": ""}, "status": "ok"}
- [x] latest view returns latest shape
  - evidence: {"latest": {"request_kind": "photos_run", "run_id": "analyze-20260801190244111808", "status": "cancelled"}, "status": "ok"}
- [x] running view exposes the active synthetic wait run
  - evidence: {"latest": {"request_kind": "photos_run", "run_id": "analyze-20260801190345250506", "status": "running"}, "running": {"active": true, "count": 1, "current_run_id": "analyze-20260801190345250506"}, "status": "ok"}
- [x] latest view exposes the cancelled synthetic wait run
  - evidence: {"latest": {"request_kind": "photos_run", "run_id": "analyze-20260801190351988581", "status": "cancelled"}, "status": "ok"}
- [x] running view exposes the active background workflow run
  - evidence: {"latest": {"request_kind": "photos_select", "run_id": "cc23162f", "status": "pending"}, "running": {"active": true, "count": 1, "current_run_id": "cc23162f"}, "status": "ok"}

## photos_query/library

- [x] list action returns items with source aliases
  - evidence: {"action": "list", "analyze_ready_count": 0, "count": 20, "download_required_count": 20, "items": [{"albums": "[비식별 항목 1개]", "analyze_recommended": false, "asset_id": "[비식별 처리됨]", "date_taken": "2018-06-08T05:50:00+00:00", "download_hint...
- [x] apple list excludes video assets from candidates
  - evidence: [{"albums": "[비식별 항목 1개]", "analyze_recommended": false, "asset_id": "[비식별 처리됨]", "date_taken": "2018-06-08T05:50:00+00:00", "download_hint": "Open the asset in Photos and wait for the original to download locally, then rerun photos_quer...
- [-] ready_only keeps only analyze-ready items
  - note: partial when no analyze-ready items are currently available
  - evidence: {"action": "ready_only", "analyze_ready_count": 0, "count": 0, "download_required_count": 0, "items": [], "next_suggested_action": "inspect_or_download", "source": "apple"}
- [x] search action returns stable shape
  - evidence: {"action": "search", "analyze_ready_count": 0, "count": 10, "download_required_count": 10, "items": [{"albums": "[비식별 항목 0개]", "analyze_recommended": false, "asset_id": "[비식별 처리됨]", "date_taken": "2011-05-09T01:27:52+00:00", "download_hi...
- [x] inspect action returns metadata/thumbnail shape
  - evidence: {"action": "inspect", "item": {"analyze_recommended": false, "asset_id": "[비식별 처리됨]", "download_hint": "Open the asset in Photos and wait for the original to download locally, then rerun photos_query(action=\"list\") and confirm local_pa...
- [x] library item guidance fields are populated consistently
  - evidence: [{"albums": "[비식별 항목 1개]", "analyze_recommended": false, "asset_id": "[비식별 처리됨]", "date_taken": "2018-06-08T05:50:00+00:00", "download_hint": "Open the asset in Photos and wait for the original to download locally, then rerun photos_quer...

## photos_select/photos_write

- [-] local analyze succeeds immediately
  - note: no local photo candidate was discovered
- [x] non-local analyze without wait returns structured blocked payload
  - evidence: {"action": "analyze_photo", "can_retry": true, "detail": "[비식별 처리됨]", "error": "[비식별 처리됨]", "error_code": "selected_photo_not_local", "error_stage": "photo_source.get_thumbnail", "hint": "Run photos_query(action=\"status\", options={\"vi...
- [x] wait_for_local starts a synthetic waiting run
  - evidence: {"action": "analyze_photo", "can_retry": true, "current_photo_local_path_available": false, "detail": "[비식별 처리됨]", "download_hint": "Open the asset in Photos and wait for the original to download locally, then rerun photos_query(action=\...
- [x] synthetic wait run can be cancelled
  - evidence: {"action": "summary", "can_retry": true, "detail": "[비식별 처리됨]", "download_hint": "Open the asset in Photos and wait for the original to download locally, then rerun photos_query(action=\"list\") and confirm local_path_available=true befo...
- [x] synthetic wait run can time out with structured failure
  - note: completed is possible if the asset downloads while polling
  - evidence: {"action": "summary", "can_retry": true, "detail": "[비식별 처리됨]", "download_hint": "Open the asset in Photos and wait for the original to download locally, then rerun photos_query(action=\"list\") and confirm local_path_available=true befo...
- [x] background workflow intents are covered when explicitly enabled
  - note: import_to_album requires non-empty paths and is skipped to avoid changing the live Photos library
  - evidence: {"classify": {"action": "classify_range", "finished_at": "", "intent": "classify", "job_id": "cc23162f", "request_kind": "photos_select", "result_available": false, "run_id": "cc23162f", "status": "pending", "summary_available": false, "...

## photos_query/result

- [x] synthetic wait summary exposes progress fields
  - evidence: {"action": "summary", "can_retry": true, "current_photo_local_path_available": false, "detail": "[비식별 처리됨]", "download_hint": "Open the asset in Photos and wait for the original to download locally, then rerun photos_query(action=\"list\...
- [x] synthetic wait result stays unavailable before terminal completion
  - note: pending result became terminal after timeout validation
  - evidence: {"action": "result", "finished_at": "", "request_kind": "photos_query", "result_available": false, "run_id": "analyze-20260801190345250506", "status": "running", "summary_available": true, "terminal": false}
- [x] synthetic wait cancel transitions to cancelled summary
  - evidence: {"action": "cancel", "can_retry": true, "current_photo_local_path_available": false, "detail": "[비식별 처리됨]", "download_hint": "Open the asset in Photos and wait for the original to download locally, then rerun photos_query(action=\"list\"...
- [x] vendor-run result actions are available when workflow validation is enabled
  - evidence: {"artifacts": {"action": "artifacts", "preview_path": "[비식별 처리됨]", "run_id": "cc23162f", "selected_count": 0}, "result": "[비식별 처리됨]", "selected": {"action": "selected", "items": [], "run_id": "cc23162f"}, "summary": {"action": "summary",...
