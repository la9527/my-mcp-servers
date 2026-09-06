from __future__ import annotations

from datetime import UTC, datetime
import json
from typing import Any
from urllib.parse import parse_qs

from mcp.server.fastmcp import FastMCP
from starlette.responses import JSONResponse
from starlette.applications import Starlette

from photos_mcp.app.config import load_config
from photos_mcp.interfaces.mcp.facade.public_tools import photos_query as facade_photos_query
from photos_mcp.interfaces.mcp.facade.public_tools import photos_select as facade_photos_select
from photos_mcp.interfaces.mcp.facade.public_tools import photos_workflow as facade_photos_workflow
from photos_mcp.interfaces.mcp.facade.public_tools import photos_write as facade_photos_write
from photos_mcp.application.run_support import call_vendor
from photos_mcp.domain.models.automation import validate_private_action_base_url
from photos_mcp.application.mutation_approval import (
    _safe_mutation_error,
    begin_mutation_receipt,
    finalize_mutation_receipt,
    require_mutation_approval,
)
from photos_mcp.application.mutation_service import resolve_mutation_plan
from photos_mcp.application.recommendation_storage import reconcile_pending_recommendations
from photos_mcp.application.story_generation import refresh_recommendation_story
from photos_mcp.infrastructure.persistence.state_store import PhotosMcpStateStore, TERMINAL_JOB_STATUSES, job_snapshot_from_payload
from photos_mcp.infrastructure.vision.runtime import vision_runtime_summary
from photos_mcp.application.share_image_service import ShareImageError
from photos_mcp.application.story_sharing import StoryShareService, build_recommendation_story
from photos_mcp.interfaces.http.story_web import (
    PUBLIC_HEADERS,
    STORY_CSS,
    STORY_JS,
    default_public_base_url,
    load_session_secret,
    owner_allowed,
    owner_assets,
    owner_mutation_allowed,
    render_owner,
)


async def _reconcile_album_mutation(receipt: dict[str, Any]) -> dict[str, Any]:
    """Resolve an uncertain album write against the current Photos album membership."""
    requested = [str(value) for value in receipt.get("requested_photo_ids") or []]
    album_name = str(receipt.get("target_album_name") or "")
    album_id = str(receipt.get("target_album_id") or "")
    action = str(receipt.get("action") or "")
    if (
        not requested
        or not (album_name or album_id)
        or action not in {"add_selected_to_album", "add_photo_ids_to_album"}
    ):
        return receipt

    reconciled = dict(receipt)
    reconciled["reconciliation_attempted"] = True
    reconciled["reconciled_at"] = datetime.now(UTC).isoformat()
    try:
        current = await call_vendor(
            "photo-ranker",
            "list_album_photo_ids",
            album_name,
            folder=str(receipt.get("folder") or ""),
            album_id=album_id,
        )
        if not isinstance(current, dict):
            raise RuntimeError("album membership query returned an invalid response")
        if current.get("error") or current.get("error_code"):
            raise RuntimeError(str(current.get("error") or "album membership query failed"))
        present = {str(value) for value in current.get("photo_ids") or []}
        confirmed = [photo_id for photo_id in requested if photo_id in present]
        unconfirmed = [photo_id for photo_id in requested if photo_id not in present]
        if not unconfirmed:
            status = "completed"
        elif confirmed:
            status = "partial"
        else:
            status = "failed"
        reconciled.update(
            {
                "status": status,
                "confirmed_photo_ids": confirmed,
                "unconfirmed_photo_ids": unconfirmed,
                "reconciliation_required": bool(unconfirmed),
                "album_exists": bool(current.get("exists")),
            }
        )
        if unconfirmed:
            reconciled["retry_requires_new_plan"] = True
            reconciled["retry_photo_ids"] = unconfirmed
    except Exception as exc:
        error_code, error_message = _safe_mutation_error(exc)
        reconciled.update(
            {
                "status": "reconciling",
                "reconciliation_required": True,
                "reconciliation_error_code": error_code,
                "reconciliation_error": error_message,
            }
        )
    return reconciled


def _normalize_job_payload(tool_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    if "job_id" not in normalized and "id" in normalized:
        normalized["job_id"] = normalized["id"]

    status = str(normalized.get("status") or "")
    terminal = bool(normalized.get("terminal", status in TERMINAL_JOB_STATUSES))
    normalized.setdefault("request_kind", tool_name)
    normalized["terminal"] = terminal
    normalized.setdefault("finished_at", normalized.get("finished_at") or "")
    normalized.setdefault("summary_available", terminal)
    normalized.setdefault("result_available", status == "completed")
    return normalized


def _serialize_response_like(original: Any, payload: Any) -> Any:
    if isinstance(original, str):
        return json.dumps(payload, ensure_ascii=False)
    return payload


def _is_job_payload(payload: dict[str, Any]) -> bool:
    """Keep read-only status payloads out of the durable job state path."""
    if payload.get("job_id") or payload.get("id"):
        return True
    if not payload.get("run_id"):
        return False
    return any(
        key in payload
        for key in (
            "intent",
            "progress",
            "wait_status",
            "summary_available",
            "result_available",
        )
    )


def _ingest_tool_response(tool_name: str, response: Any, state_store: PhotosMcpStateStore | None) -> Any:
    if state_store is None:
        return response

    parsed: Any = response
    if isinstance(response, str):
        try:
            parsed = json.loads(response)
        except json.JSONDecodeError:
            return response

    if isinstance(parsed, dict) and _is_job_payload(parsed):
        normalized = _normalize_job_payload(tool_name, parsed)
        if normalized.get("job_id") and normalized.get("status"):
            state_store.upsert_job(job_snapshot_from_payload(normalized))
        return _serialize_response_like(response, normalized)

    if isinstance(parsed, list) and parsed and all(isinstance(item, dict) for item in parsed):
        normalized_list = []
        for item in parsed:
            if _is_job_payload(item):
                normalized = _normalize_job_payload(tool_name, item)
                normalized_list.append(normalized)
                if normalized.get("job_id") and normalized.get("status"):
                    state_store.upsert_job(job_snapshot_from_payload(normalized))
            else:
                normalized_list.append(item)
        return _serialize_response_like(response, normalized_list)

    return response


def build_health_payload(config, state_store: PhotosMcpStateStore | None) -> dict[str, Any]:
    snapshot = state_store.snapshot() if state_store is not None else None
    daemon_status = snapshot.daemon_status if snapshot else "stopped"
    preflight_status = snapshot.preflight_status if snapshot else "pending"
    transport_status = "ok" if daemon_status in {"ready", "busy"} else daemon_status
    browser_missions = (
        state_store.run_repository.list_browser_mission_runs(limit=1)
        if state_store is not None
        else []
    )
    capabilities = {
        "status": preflight_status,
        "checks": snapshot.preflight_checks if snapshot else [],
        "last_checked_at": snapshot.last_preflight_at if snapshot else "",
        "vision_runtime": vision_runtime_summary(check_ready=True),
        "latest_browser_mission": browser_missions[0] if browser_missions else {},
    }
    return {
        "status": transport_status,
        "daemon_status": daemon_status,
        "preflight_status": preflight_status,
        "preflight_checks": capabilities["checks"],
        "last_preflight_at": capabilities["last_checked_at"],
        "transport": {
            "status": transport_status,
            "daemon_status": daemon_status,
            "endpoint": config.endpoint,
            "health_endpoint": config.health_endpoint,
        },
        "capabilities": capabilities,
        "app_name": config.app_name,
        "bundle_id": config.bundle_id,
        "endpoint": config.endpoint,
        "health_endpoint": config.health_endpoint,
        "background_job_running": snapshot.background_job_running if snapshot else False,
        "active_job_count": len(snapshot.active_jobs) if snapshot else 0,
        "recent_job_count": len(snapshot.recent_jobs) if snapshot else 0,
        "pending_mutation_count": len(snapshot.pending_mutation_plans) if snapshot else 0,
        "active_jobs": snapshot.active_jobs if snapshot else [],
        "recent_jobs": snapshot.recent_jobs if snapshot else [],
        "pending_mutation_plans": snapshot.pending_mutation_plans if snapshot else [],
        "last_updated_at": snapshot.last_updated_at if snapshot else "",
    }


def build_server(
    config=None,
    state_store: PhotosMcpStateStore | None = None,
) -> FastMCP:
    config = config or load_config()
    mcp = FastMCP(
        "photos-mcp",
        instructions=(
            "Photos MCP facade with four high-level tools. Start with "
            "photos_query(action='guide') when the correct flow is unclear. "
            "Read and analyze before writing, and require user approval of the "
            "mutation plan before any album change."
        ),
        host=config.host,
        port=config.port,
        streamable_http_path=config.streamable_http_path,
    )

    @mcp.tool()
    async def photos_query(action: str = "status", options: dict[str, Any] | None = None) -> dict[str, Any]:
        """Read-only guide, diagnostics, library browsing, inspection, and result lookup. Start with action='guide' when unsure."""

        payload = await facade_photos_query(
            state_store=state_store,
            health_payload=build_health_payload(config, state_store),
            action=action,
            options=options,
        )
        return _ingest_tool_response("photos_query", payload, state_store)

    @mcp.tool()
    async def photos_select(action: str = "select_best", options: dict[str, Any] | None = None) -> dict[str, Any]:
        """Analyze and select photos without writing albums or files. Use action plus action-specific options."""

        payload = await facade_photos_select(state_store=state_store, action=action, options=options)
        return _ingest_tool_response("photos_select", payload, state_store)

    @mcp.tool()
    async def photos_write(action: str = "add_selected_to_album", options: dict[str, Any] | None = None) -> dict[str, Any]:
        """Plan a write first and repeat unchanged options with approval_token after user approval. Use organize_by_category only for category albums. Do not pass target_album_name there."""

        mutation_plan = await resolve_mutation_plan(
            "photos_write",
            action,
            options,
            state_store=state_store,
        )
        approval_payload, approved_options = require_mutation_approval(
            "photos_write",
            action,
            options,
            repository=state_store.run_repository if state_store is not None else None,
            mutation_plan=mutation_plan,
        )
        if approval_payload is not None:
            if (
                state_store is not None
                and approval_payload.get("error_code") == "mutation_reconciliation_required"
                and isinstance(approval_payload.get("mutation_receipt"), dict)
            ):
                reconciled = await _reconcile_album_mutation(approval_payload["mutation_receipt"])
                reconciled = state_store.run_repository.save_mutation_receipt(reconciled)
                approval_payload["mutation_receipt"] = reconciled
                if reconciled.get("status") == "completed":
                    approval_payload.update(
                        {
                            "status": "completed",
                            "terminal": True,
                            "error_code": "",
                            "duplicate_suppressed": True,
                            "reconciled": True,
                        }
                    )
            return approval_payload
        if approved_options is None:
            return await facade_photos_write(
                state_store=state_store,
                action=action,
                options=options,
            )

        execution_options = dict(approved_options or options or {})
        mutation_context = dict(execution_options.pop("__mutation_context", {}))
        receipt = begin_mutation_receipt(mutation_context, execution_options)
        if state_store is not None:
            state_store.run_repository.save_mutation_receipt(receipt)
        try:
            payload = await facade_photos_write(
                state_store=state_store,
                action=action,
                options=execution_options,
                _mutation_plan=dict(mutation_context.get("mutation_plan") or {}),
                _mutation_receipt_id=str(receipt.get("receipt_id") or ""),
            )
        except BaseException as exc:
            failed_receipt = finalize_mutation_receipt(receipt, None, error=exc)
            if state_store is not None:
                state_store.run_repository.save_mutation_receipt(failed_receipt)
            raise
        normalized_payload = payload if isinstance(payload, dict) else {"result": payload}
        final_receipt = finalize_mutation_receipt(receipt, normalized_payload)
        if state_store is not None:
            final_receipt = state_store.run_repository.save_mutation_receipt(final_receipt)
        normalized_payload["mutation_receipt"] = final_receipt
        normalized_payload["idempotency_key"] = mutation_context.get("idempotency_key", "")
        payload = normalized_payload
        return _ingest_tool_response("photos_write", payload, state_store)

    @mcp.tool()
    async def photos_workflow(action: str = "curate_to_album", options: dict[str, Any] | None = None) -> dict[str, Any]:
        """Analyze first, then expose an exact write plan for approval. Use curate_to_album for exactly one target album with a flat options dict containing scope filters plus target_album_name; use the classify_then_organize_by_category category workflow only for category albums. Do not nest filters under scope or selection. Do not pass selected_photo_ids or prior result payloads. Resume only after explicit approval."""

        normalized_action = action.strip().lower().replace("-", "_")
        if normalized_action == "import_then_curate_to_album":
            workflow_options = dict(options or {})
            return await photos_write(
                action="import_to_album",
                options={
                    "photo_paths": workflow_options.get("photo_paths", []),
                    "target_album_name": workflow_options.get("target_album_name", ""),
                    "folder": workflow_options.get("folder", ""),
                    "approval_token": workflow_options.get("approval_token", ""),
                },
            )
        if normalized_action in {
            "daily_curate",
            "curate_to_album",
            "curate_to_directory",
            "classify_then_organize_by_category",
        }:
            payload = await facade_photos_workflow(
                state_store=state_store,
                action=action,
                options=options,
            )
            return _ingest_tool_response("photos_workflow", payload, state_store)

        mutation_plan = await resolve_mutation_plan(
            "photos_workflow",
            action,
            options,
            state_store=state_store,
        )
        approval_payload, approved_options = require_mutation_approval(
            "photos_workflow",
            action,
            options,
            repository=state_store.run_repository if state_store is not None else None,
            mutation_plan=mutation_plan,
        )
        if approval_payload is not None:
            if action.strip().lower().replace("-", "_") == "resume" and state_store is not None:
                run_id = str((options or {}).get("run_id") or "") if isinstance(options, dict) else ""
                recovery = state_store.get_recovery_plan(run_id)
                if recovery.get("status") != "ready_for_approval":
                    return recovery
                approval_payload["recovery_plan"] = recovery["recovery_plan"]
            return approval_payload

        execution_options = dict(approved_options or options or {})
        execution_options.pop("__mutation_context", None)
        payload = await facade_photos_workflow(
            state_store=state_store,
            action=action,
            options=execution_options,
        )
        return _ingest_tool_response("photos_workflow", payload, state_store)

    @mcp.custom_route(config.health_path, methods=["GET"], include_in_schema=False)
    async def http_health_status(_request) -> JSONResponse:
        return JSONResponse(build_health_payload(config, state_store))

    @mcp.custom_route("/story-assets/story.css", methods=["GET"], include_in_schema=False)
    async def http_story_css(_request):
        from starlette.responses import PlainTextResponse

        return PlainTextResponse(
            STORY_CSS,
            media_type="text/css",
            headers={"Cache-Control": "public, max-age=3600", "X-Content-Type-Options": "nosniff"},
        )

    @mcp.custom_route("/story-assets/story.js", methods=["GET"], include_in_schema=False)
    async def http_story_js(_request):
        from starlette.responses import PlainTextResponse

        return PlainTextResponse(
            STORY_JS,
            media_type="application/javascript",
            headers={"Cache-Control": "public, max-age=3600", "X-Content-Type-Options": "nosniff"},
        )

    @mcp.custom_route("/photos", methods=["GET"], include_in_schema=False)
    async def http_owner_story(request):
        from starlette.responses import HTMLResponse

        if not owner_allowed(request):
            return HTMLResponse("Forbidden", status_code=403, headers=PUBLIC_HEADERS)
        if state_store is None:
            return HTMLResponse("Unavailable", status_code=503, headers=PUBLIC_HEADERS)
        story = build_recommendation_story(state_store.run_repository)
        service = StoryShareService(
            state_store.run_repository,
            session_secret=load_session_secret(),
        )
        active_shares = []
        for candidate in state_store.run_repository.list_shared_story_packages(limit=100):
            candidate_id = str(candidate.get("share_id") or "")
            package, share_state = service.get_active(candidate_id)
            if share_state == "active" and package is not None:
                active_shares.append(service.public_metadata(package, include_story=False))
            elif share_state == "expired":
                owner_assets(state_store.run_repository).purge_share(candidate_id)
        return HTMLResponse(
            render_owner(
                story,
                public_base=default_public_base_url(),
                active_shares=active_shares,
            ),
            headers=PUBLIC_HEADERS,
        )

    @mcp.custom_route("/photos/share", methods=["POST"], include_in_schema=False)
    async def http_owner_create_share(request):
        from starlette.responses import HTMLResponse

        if not owner_mutation_allowed(request):
            return HTMLResponse("Forbidden", status_code=403, headers=PUBLIC_HEADERS)
        if state_store is None:
            return HTMLResponse("Unavailable", status_code=503, headers=PUBLIC_HEADERS)
        raw = await request.body()
        if len(raw) > 4096:
            return HTMLResponse("Request too large", status_code=413, headers=PUBLIC_HEADERS)
        values = parse_qs(raw.decode("utf-8", errors="replace"), keep_blank_values=True)
        try:
            duration_days = int((values.get("duration_days") or ["30"])[0])
        except ValueError:
            return HTMLResponse("Invalid duration", status_code=400, headers=PUBLIC_HEADERS)
        story = build_recommendation_story(state_store.run_repository)
        if not story.get("photos"):
            return HTMLResponse(
                render_owner(story),
                status_code=409,
                headers=PUBLIC_HEADERS,
            )
        service = StoryShareService(
            state_store.run_repository,
            session_secret=load_session_secret(),
        )
        created, passcode = service.create(
            story,
            duration_days=duration_days,
            download_enabled=(values.get("download_enabled") or [""])[0] == "1",
        )
        package = state_store.run_repository.get_shared_story_package(
            str(created["share_id"])
        ) or {}
        image_service = owner_assets(state_store.run_repository)
        try:
            for photo in package.get("photos") or []:
                kinds = ["thumb", "preview"]
                if package.get("download_enabled"):
                    kinds.append("download")
                for kind in kinds:
                    image_service.derivative(
                        share_id=str(package["share_id"]),
                        public_asset_id=str(photo["public_asset_id"]),
                        local_asset_id=str(photo["local_asset_id"]),
                        kind=kind,
                    )
        except (KeyError, ShareImageError):
            service.revoke(str(created["share_id"]))
            image_service.purge_share(str(created["share_id"]))
            return HTMLResponse(
                "공유 이미지를 안전하게 만들지 못했습니다.",
                status_code=500,
                headers=PUBLIC_HEADERS,
            )
        active_shares = []
        for candidate in state_store.run_repository.list_shared_story_packages(limit=100):
            candidate_id = str(candidate.get("share_id") or "")
            active_package, share_state = service.get_active(candidate_id)
            if share_state == "active" and active_package is not None:
                active_shares.append(
                    service.public_metadata(active_package, include_story=False)
                )
            elif share_state == "expired":
                image_service.purge_share(candidate_id)
        return HTMLResponse(
            render_owner(
                story,
                created=created,
                passcode=passcode,
                public_base=default_public_base_url(),
                active_shares=active_shares,
            ),
            status_code=201,
            headers=PUBLIC_HEADERS,
        )

    @mcp.custom_route("/photos/story/refresh", methods=["POST"], include_in_schema=False)
    async def http_owner_refresh_story(request):
        from starlette.responses import RedirectResponse, Response

        if not owner_mutation_allowed(request):
            return Response(status_code=403, headers=PUBLIC_HEADERS)
        if state_store is None:
            return Response(status_code=503, headers=PUBLIC_HEADERS)
        await refresh_recommendation_story(
            state_store.run_repository,
            force=True,
        )
        return RedirectResponse("/photos", status_code=303, headers=PUBLIC_HEADERS)

    @mcp.custom_route(
        "/photos/shares/{share_id}/revoke",
        methods=["POST"],
        include_in_schema=False,
    )
    async def http_owner_revoke_share(request):
        from starlette.responses import RedirectResponse, Response

        if not owner_mutation_allowed(request):
            return Response(status_code=403, headers=PUBLIC_HEADERS)
        if state_store is None:
            return Response(status_code=503, headers=PUBLIC_HEADERS)
        share_id = str(request.path_params.get("share_id") or "")
        service = StoryShareService(
            state_store.run_repository,
            session_secret=load_session_secret(),
        )
        if not service.revoke(share_id):
            return Response(status_code=404, headers=PUBLIC_HEADERS)
        owner_assets(state_store.run_repository).purge_share(share_id)
        return RedirectResponse("/photos", status_code=303, headers=PUBLIC_HEADERS)

    @mcp.custom_route("/photos/assets/{asset_id}/{kind}", methods=["GET"], include_in_schema=False)
    async def http_owner_asset(request):
        from starlette.responses import FileResponse, Response

        if not owner_allowed(request):
            return Response(status_code=403, headers=PUBLIC_HEADERS)
        if state_store is None:
            return Response(status_code=503, headers=PUBLIC_HEADERS)
        asset_id = str(request.path_params.get("asset_id") or "")
        kind = str(request.path_params.get("kind") or "")
        if kind not in {"thumb", "preview"}:
            return Response(status_code=404, headers=PUBLIC_HEADERS)
        try:
            path = owner_assets(state_store.run_repository).derivative(
                share_id="owner-gallery",
                public_asset_id=asset_id,
                local_asset_id=asset_id,
                kind=kind,
            )
        except ShareImageError:
            return Response(status_code=404, headers=PUBLIC_HEADERS)
        return FileResponse(path, media_type="image/jpeg", headers=PUBLIC_HEADERS)

    @mcp.custom_route("/actions/{request_id}", methods=["GET"], include_in_schema=False)
    async def http_user_action(request):
        from starlette.responses import HTMLResponse
        from photos_mcp.interfaces.http.user_actions import render_user_action_page

        request_id = str(request.path_params.get("request_id") or "")
        payload = (
            state_store.run_repository.get_user_action_request(request_id)
            if state_store is not None and request_id
            else None
        )
        html, status_code = render_user_action_page(payload, request_id=request_id)
        return HTMLResponse(
            html,
            status_code=status_code,
            headers={
                "Cache-Control": "no-store",
                "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
                "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "DENY",
            },
        )

    @mcp.custom_route("/automation/daily-curate", methods=["POST"], include_in_schema=False)
    async def http_daily_curate(request):
        # This resource-using but non-mutating trigger is deliberately loopback-only.
        if config.host not in {"127.0.0.1", "localhost", "::1"}:
            return JSONResponse({"status": "blocked", "error_code": "loopback_required"}, status_code=403)
        try:
            content_length = int(request.headers.get("content-length") or 0)
        except ValueError:
            content_length = 0
        if content_length > 8192:
            return JSONResponse({"status": "blocked", "error_code": "request_too_large"}, status_code=413)
        try:
            body = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            return JSONResponse({"status": "blocked", "error_code": "invalid_json"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"status": "blocked", "error_code": "invalid_json_object"}, status_code=400)
        source = str(body.get("source") or "apple").strip().lower()
        if source not in {"apple", "google"}:
            return JSONResponse({"status": "blocked", "error_code": "unsupported_daily_curate_source"}, status_code=400)
        try:
            options = {
                "source": source,
                "source_id": str(body.get("source_id") or ("system-library" if source == "apple" else "default-account")),
                "limit": max(1, min(int(body.get("limit") or 50), 500)),
                "selection_profile": str(body.get("selection_profile") or "general"),
                "exclude_screenshots": bool(body.get("exclude_screenshots", True)),
                "lookback_hours": max(1.0, min(float(body.get("lookback_hours") or 48.0), 24.0 * 31.0)),
                "overlap_hours": max(0.0, min(float(body.get("overlap_hours") or 6.0), 48.0)),
                "mode": "review_only",
            }
            if body.get("action_base_url"):
                options["action_base_url"] = validate_private_action_base_url(
                    str(body["action_base_url"])
                )
        except (TypeError, ValueError):
            return JSONResponse({"status": "blocked", "error_code": "invalid_daily_curate_options"}, status_code=400)
        payload = await facade_photos_workflow(
            state_store=state_store,
            action="daily_curate",
            options=options,
        )
        normalized = _ingest_tool_response("photos_workflow", payload, state_store)
        return JSONResponse(normalized if isinstance(normalized, dict) else {"result": normalized})

    @mcp.custom_route(
        "/automation/reconcile-recommendations",
        methods=["POST"],
        include_in_schema=False,
    )
    async def http_reconcile_recommendations(_request):
        # Local recommendation copies are a private background workflow and are
        # never exposed as a public/Tailscale write endpoint.
        if config.host not in {"127.0.0.1", "localhost", "::1"}:
            return JSONResponse(
                {"status": "blocked", "error_code": "loopback_required"},
                status_code=403,
            )
        if state_store is None:
            return JSONResponse(
                {"status": "blocked", "error_code": "state_store_unavailable"},
                status_code=503,
            )
        payload = await reconcile_pending_recommendations(
            repository=state_store.run_repository,
        )
        return JSONResponse(payload)

    @mcp.custom_route(f"{config.health_path}/capabilities", methods=["GET"], include_in_schema=False)
    async def http_health_capabilities(_request) -> JSONResponse:
        return JSONResponse(build_health_payload(config, state_store)["capabilities"])

    return mcp


def build_http_app(
    config=None,
    state_store: PhotosMcpStateStore | None = None,
    mcp: FastMCP | None = None,
) -> Starlette:
    config = config or load_config()
    mcp = mcp or build_server(config=config, state_store=state_store)
    return mcp.streamable_http_app()
