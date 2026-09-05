"""Approval-gated publication of locally verified recommendation groups."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Awaitable, Callable

from photos_mcp.application.recommendation_storage import _sha256, recommendation_root
from photos_mcp.domain.models.source import (
    MaterializedPhotoContent,
    PhotoAssetRef,
    PhotoContentState,
)
from photos_mcp.infrastructure.persistence.run_repository import RunRepository
from photos_mcp.infrastructure.sources.google_photos.runtime import (
    GooglePhotosRuntimeSettings,
    build_google_photos_runtime,
)
from photos_mcp.infrastructure.vendor_adapter.gateway import call_vendor


AsyncCallable = Callable[..., Awaitable[Any]]


def _destination_type(provider: str) -> str:
    return "apple_album" if provider == "apple_photos" else "google_album"


def _fingerprint(plan: dict[str, Any]) -> str:
    canonical = json.dumps(
        {
            "group_id": plan.get("group_id"),
            "destination_provider": plan.get("destination_provider"),
            "destination_album_id": plan.get("destination_album_id"),
            "destination_album_name": plan.get("destination_album_name"),
            "local_asset_ids": plan.get("local_asset_ids"),
            "content_hashes": plan.get("content_hashes"),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class RecommendationGroupPublishService:
    def __init__(
        self,
        *,
        repository: RunRepository,
        root: str | Path | None = None,
        call_vendor_fn: AsyncCallable = call_vendor,
        photos_run_fn: AsyncCallable | None = None,
        google_runtime_factory: Callable[..., Any] = build_google_photos_runtime,
    ) -> None:
        self.repository = repository
        self.root = Path(root) if root is not None else recommendation_root()
        self.call_vendor = call_vendor_fn
        self.photos_run = photos_run_fn
        self.google_runtime_factory = google_runtime_factory

    def prepare_destination_plan(
        self,
        *,
        group_id: str,
        destination_provider: str,
        destination_album_name: str = "",
        destination_album_id: str = "",
    ) -> dict[str, Any]:
        group = self.repository.get_recommendation_group(group_id)
        if group is None:
            return {
                "status": "blocked",
                "error_code": "recommendation_group_not_found",
                "group_id": group_id,
            }
        provider = str(destination_provider or "").strip()
        if provider not in {"apple_photos", "google_photos", "local_only"}:
            return {
                "status": "blocked",
                "error_code": "unsupported_recommendation_destination",
                "group_id": group_id,
            }
        completed_cloud_receipts = [
            receipt
            for receipt in self.repository.list_recommendation_destination_receipts(
                group_id=group_id
            )
            if str(receipt.get("destination_type") or "")
            in {"apple_album", "google_album"}
            and str(receipt.get("state") or "") == "completed"
        ]
        current_provider = str(group.get("destination_provider") or "local_only")
        current_album_id = str(group.get("destination_album_id") or "")
        requested_album_id = str(destination_album_id or "").strip()
        changing_bound_destination = (
            provider != current_provider
            or (bool(requested_album_id) and requested_album_id != current_album_id)
        )
        if completed_cloud_receipts and changing_bound_destination:
            return {
                "status": "blocked",
                "error_code": "published_recommendation_destination_is_fixed",
                "group_id": group_id,
                "completed_receipt_count": len(completed_cloud_receipts),
            }
        resolved_name = str(destination_album_name or "").strip()
        if provider != "local_only" and not resolved_name:
            resolved_name = str(
                group.get("destination_album_name")
                or group.get("display_name")
                or ""
            ).strip()
        if provider != "local_only" and not resolved_name:
            return {
                "status": "blocked",
                "error_code": "recommendation_album_name_required",
                "group_id": group_id,
            }
        plan = {
            "status": "ready",
            "action": "configure_recommendation_group",
            "destructive": False,
            "group_id": group_id,
            "group_name": str(group.get("display_name") or ""),
            "previous_destination_provider": current_provider,
            "previous_destination_album_id": current_album_id,
            "destination_provider": provider,
            "destination_album_name": resolved_name if provider != "local_only" else "",
            "destination_album_id": requested_album_id if provider != "local_only" else "",
            "member_count": len(
                self.repository.list_recommendation_group_members(group_id)
            ),
            "google_creates_new_copies": provider == "google_photos",
            "requires_exact_target_review": True,
        }
        plan["content_fingerprint"] = hashlib.sha256(
            json.dumps(plan, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        return plan

    def configure_destination(
        self,
        *,
        group_id: str,
        approved_plan: dict[str, Any],
    ) -> dict[str, Any]:
        current = self.prepare_destination_plan(
            group_id=group_id,
            destination_provider=str(
                approved_plan.get("destination_provider") or ""
            ),
            destination_album_name=str(
                approved_plan.get("destination_album_name") or ""
            ),
            destination_album_id=str(
                approved_plan.get("destination_album_id") or ""
            ),
        )
        if current.get("status") != "ready":
            return current
        if str(current.get("content_fingerprint") or "") != str(
            approved_plan.get("content_fingerprint") or ""
        ):
            return {
                "status": "blocked",
                "error_code": "recommendation_group_plan_changed",
                "group_id": group_id,
            }
        group = self.repository.get_recommendation_group(group_id) or {}
        self.repository.upsert_recommendation_group(
            {
                **group,
                "group_id": group_id,
                "group_type": str(group.get("group_type") or "monthly"),
                "display_name": str(group.get("display_name") or current["group_name"]),
                "destination_provider": str(current["destination_provider"]),
                "destination_album_name": str(current["destination_album_name"]),
                "destination_album_id": str(current["destination_album_id"]),
                "policy_state": "draft",
            }
        )
        return {
            "status": "completed",
            "terminal": True,
            "action": "configure_recommendation_group",
            "group_id": group_id,
            "destination_provider": str(current["destination_provider"]),
            "destination_album_name": str(current["destination_album_name"]),
            "destination_album_id": str(current["destination_album_id"]),
            "member_count": int(current["member_count"]),
            "google_creates_new_copies": bool(
                current["google_creates_new_copies"]
            ),
        }

    def prepare_plan(self, group_id: str) -> dict[str, Any]:
        group = self.repository.get_recommendation_group(group_id)
        if group is None:
            return {
                "status": "blocked",
                "error_code": "recommendation_group_not_found",
                "group_id": group_id,
            }
        provider = str(group.get("destination_provider") or "local_only")
        if provider == "local_only":
            return {
                "status": "blocked",
                "error_code": "recommendation_group_is_local_only",
                "group_id": group_id,
            }
        if provider not in {"apple_photos", "google_photos"}:
            return {
                "status": "blocked",
                "error_code": "unsupported_recommendation_destination",
                "group_id": group_id,
            }
        target_type = _destination_type(provider)
        completed_ids = {
            str(receipt.get("local_asset_id") or "")
            for receipt in self.repository.list_recommendation_destination_receipts(
                group_id=group_id
            )
            if str(receipt.get("destination_type") or "") == target_type
            and str(receipt.get("state") or "") == "completed"
        }
        root = self.root.expanduser().resolve()
        candidates: list[dict[str, Any]] = []
        invalid_count = 0
        for member in self.repository.list_recommendation_group_members(group_id):
            local_asset_id = str(member.get("local_asset_id") or "")
            if not local_asset_id or local_asset_id in completed_ids:
                continue
            asset = dict(member.get("asset") or {})
            relative_path = str(asset.get("relative_path") or "")
            content_hash = str(asset.get("content_hash") or "")
            path = root / relative_path
            try:
                valid = (
                    bool(relative_path)
                    and bool(content_hash)
                    and path.is_file()
                    and _sha256(path) == content_hash
                )
            except OSError:
                valid = False
            if not valid:
                invalid_count += 1
                continue
            candidates.append(
                {
                    "local_asset_id": local_asset_id,
                    "collection_id": str(member.get("collection_id") or ""),
                    "content_hash": content_hash,
                    "relative_path": relative_path,
                    "byte_size": int(asset.get("byte_size") or path.stat().st_size),
                    "mime_type": str(asset.get("mime_type") or "application/octet-stream"),
                    "capture_date_local": str(asset.get("capture_date_local") or ""),
                }
            )
        candidates.sort(key=lambda item: (item["relative_path"], item["local_asset_id"]))
        if not candidates:
            return {
                "status": "completed" if completed_ids and not invalid_count else "blocked",
                "error_code": "" if completed_ids and not invalid_count else "no_publishable_recommendations",
                "group_id": group_id,
                "duplicate_suppressed": bool(completed_ids),
                "already_published_count": len(completed_ids),
                "invalid_local_asset_count": invalid_count,
                "photo_ids": [],
                "photo_targets": [],
                "photo_count": 0,
            }
        plan = {
            "status": "ready",
            "action": "publish_recommendation_group",
            "destructive": False,
            "group_id": group_id,
            "group_name": str(group.get("display_name") or ""),
            "destination_provider": provider,
            "destination_album_id": str(group.get("destination_album_id") or ""),
            "destination_album_name": str(
                group.get("destination_album_name") or group.get("display_name") or ""
            ),
            "local_asset_ids": [item["local_asset_id"] for item in candidates],
            "content_hashes": [item["content_hash"] for item in candidates],
            "photo_ids": [item["local_asset_id"] for item in candidates],
            "photo_targets": [
                {
                    "photo_id": item["local_asset_id"],
                    "capture_date": item["capture_date_local"],
                    "content_hash_prefix": item["content_hash"][:12],
                }
                for item in candidates
            ],
            "photo_count": len(candidates),
            "total_bytes": sum(item["byte_size"] for item in candidates),
            "storage_warning_required": (
                provider == "google_photos"
                and sum(item["byte_size"] for item in candidates) > 25 * 1024 * 1024
            ),
            "already_published_count": len(completed_ids),
            "invalid_local_asset_count": invalid_count,
            "requires_exact_target_review": True,
            "items": candidates,
        }
        plan["content_fingerprint"] = _fingerprint(plan)
        return plan

    async def execute(self, group_id: str, approved_plan: dict[str, Any]) -> dict[str, Any]:
        current = self.prepare_plan(group_id)
        if current.get("status") != "ready":
            return current
        approved_ids = [str(value) for value in approved_plan.get("local_asset_ids") or []]
        if not approved_ids:
            approved_ids = [str(value) for value in approved_plan.get("photo_ids") or []]
        if approved_ids != list(current["local_asset_ids"]):
            return {
                "status": "blocked",
                "error_code": "recommendation_group_members_changed",
                "group_id": group_id,
            }
        if str(approved_plan.get("content_fingerprint") or "") != str(
            current.get("content_fingerprint") or ""
        ):
            return {
                "status": "blocked",
                "error_code": "recommendation_group_plan_changed",
                "group_id": group_id,
            }
        if current["destination_provider"] == "apple_photos":
            return await self._publish_apple(current)
        return await self._publish_google(current)

    async def _publish_apple(self, plan: dict[str, Any]) -> dict[str, Any]:
        if self.photos_run is None:
            return {"status": "blocked", "error_code": "photos_run_unavailable"}
        root = self.root.expanduser().resolve()
        apple_asset_ids: list[str] = []
        local_to_apple: dict[str, str] = {}
        external_items: list[dict[str, Any]] = []
        for item in plan["items"]:
            local_asset_id = str(item["local_asset_id"])
            source_members = self.repository.list_recommendation_members_for_local_asset(
                local_asset_id
            )
            apple_member = next(
                (
                    member
                    for member in source_members
                    if str(member.get("provider") or "") == "apple_photos"
                ),
                None,
            )
            if apple_member is not None:
                apple_id = str(apple_member.get("provider_asset_id") or "")
                if apple_id:
                    apple_asset_ids.append(apple_id)
                    local_to_apple[local_asset_id] = apple_id
                    continue
            external_items.append(item)

        album_name = str(plan["destination_album_name"])
        album_id = str(plan["destination_album_id"])
        folder = os.getenv("PHOTOS_MCP_RECOMMENDATION_APPLE_FOLDER", "Photos MCP").strip()
        apple_result: dict[str, Any] = {}
        apple_completed: set[str] = set()
        if apple_asset_ids:
            raw = await self.call_vendor(
                "photo-ranker",
                "add_to_album",
                json.dumps(apple_asset_ids, ensure_ascii=False),
                album_name,
                folder=folder,
                album_id=album_id,
            )
            apple_result = dict(raw) if isinstance(raw, dict) else {}
            if not apple_result.get("error") and not apple_result.get("error_code") and int(
                apple_result.get("failed") or 0
            ) == 0:
                apple_completed.update(local_to_apple)
            album_id = str(
                apple_result.get("album_id") or apple_result.get("uuid") or album_id
            )

        import_result: dict[str, Any] = {}
        imported_completed: set[str] = set()
        if external_items:
            paths = [str(root / str(item["relative_path"])) for item in external_items]
            raw = await self.photos_run(
                state_store=None,
                intent="import",
                photo_paths_json=json.dumps(paths, ensure_ascii=False),
                target_album_name=album_name,
                target_album_id=album_id,
                folder=folder,
            )
            import_result = dict(raw) if isinstance(raw, dict) else {}
            if not import_result.get("error") and not import_result.get("error_code") and int(
                import_result.get("imported") or 0
            ) == len(external_items):
                imported_completed.update(
                    str(item["local_asset_id"]) for item in external_items
                )
            album_id = str(import_result.get("album_id") or album_id)

        completed_ids = apple_completed | imported_completed
        self._save_destination_receipts(
            plan,
            completed_ids=completed_ids,
            destination_type="apple_album",
            destination_id=album_id or f"managed:{plan['group_id']}",
        )
        self._update_group_after_write(plan, album_id, bool(completed_ids))
        requested = len(plan["items"])
        status = (
            "completed"
            if len(completed_ids) == requested
            else "failed" if not completed_ids else "partial"
        )
        destination = {
            "status": status,
            "requested": requested,
            "completed": len(completed_ids),
            "failed": requested - len(completed_ids),
            "album_id": album_id,
            "album_name": album_name,
            "existing_apple_assets": len(apple_asset_ids),
            "external_imports": len(external_items),
        }
        result = {
            "status": status,
            "terminal": True,
            "action": "publish_recommendation_group",
            "group_id": plan["group_id"],
            "destination_provider": "apple_photos",
            "published_count": len(completed_ids),
            "failed_count": requested - len(completed_ids),
            "destination_receipts": {"apple_album": destination},
            "retry_available": status != "completed",
        }
        if status != "completed":
            safe_error_code = str(
                import_result.get("error_code")
                or apple_result.get("error_code")
                or ""
            )
            if safe_error_code:
                result["error_code"] = safe_error_code[:80]
        return result

    async def _publish_google(self, plan: dict[str, Any]) -> dict[str, Any]:
        settings = GooglePhotosRuntimeSettings.from_app_configuration()
        if not settings.configured:
            return {
                "status": "blocked",
                "error_code": "google_photos_not_configured",
                "group_id": plan["group_id"],
            }
        runtime = self.google_runtime_factory(settings=settings, state_store=None)
        root = self.root.expanduser().resolve()
        try:
            contents = tuple(
                MaterializedPhotoContent(
                    asset=PhotoAssetRef(
                        source_id=runtime.source.source_id,
                        provider_asset_id=str(item["local_asset_id"]),
                        content_state=PhotoContentState.MATERIALIZED,
                        filename=Path(str(item["relative_path"])).name,
                    ),
                    local_path=root / str(item["relative_path"]),
                    mime_type=str(item["mime_type"]),
                    delete_after_use=False,
                )
                for item in plan["items"]
            )
            write_plan = await runtime.destination.plan_write(
                runtime.source,
                contents,
                options={
                    "album_name": str(plan["destination_album_name"]),
                    "album_id": str(plan["destination_album_id"]),
                },
            )
            # Keep resumable-upload receipts stable across retries for this
            # logical group instead of using the destination adapter's random
            # one-shot plan identifier.
            write_plan = {
                **write_plan,
                "plan_id": "recommendation-"
                + hashlib.sha256(
                    str(plan["group_id"]).encode("utf-8")
                ).hexdigest()[:24],
            }
            result = await runtime.destination.execute_write(
                runtime.source,
                contents,
                approved_plan={**write_plan, "approved": True},
            )
        finally:
            runtime.close()
        created_keys = {str(value) for value in result.get("created_asset_keys") or []}
        completed_ids = {
            str(item["local_asset_id"])
            for item in plan["items"]
            if any(key.endswith(f":{item['local_asset_id']}") for key in created_keys)
        }
        album_id = str(result.get("album_id") or plan["destination_album_id"])
        self._save_destination_receipts(
            plan,
            completed_ids=completed_ids,
            destination_type="google_album",
            destination_id=album_id or f"managed:{plan['group_id']}",
            provider_media_ids=[str(value) for value in result.get("media_item_ids") or []],
        )
        self._update_group_after_write(plan, album_id, bool(completed_ids))
        requested = len(plan["items"])
        status = (
            "completed"
            if len(completed_ids) == requested
            else "failed" if not completed_ids else "partial"
        )
        destination = {
            "status": status,
            "requested": requested,
            "completed": len(completed_ids),
            "failed": requested - len(completed_ids),
            "album_id": album_id,
            "album_name": str(plan["destination_album_name"]),
            "app_created_content_only": True,
        }
        return {
            "status": status,
            "terminal": True,
            "action": "publish_recommendation_group",
            "group_id": plan["group_id"],
            "destination_provider": "google_photos",
            "published_count": len(completed_ids),
            "failed_count": requested - len(completed_ids),
            "destination_receipts": {"google_album": destination},
            "retry_available": status != "completed",
        }

    def _save_destination_receipts(
        self,
        plan: dict[str, Any],
        *,
        completed_ids: set[str],
        destination_type: str,
        destination_id: str,
        provider_media_ids: list[str] | None = None,
    ) -> None:
        media_ids = list(provider_media_ids or [])
        completed_index = 0
        now = datetime.now(UTC).isoformat()
        for item in plan["items"]:
            local_asset_id = str(item["local_asset_id"])
            state = "completed" if local_asset_id in completed_ids else "failed"
            provider_media_item_id = ""
            if state == "completed" and completed_index < len(media_ids):
                provider_media_item_id = media_ids[completed_index]
                completed_index += 1
            receipt_id = "publish-" + hashlib.sha256(
                f"{plan['group_id']}\0{local_asset_id}\0{destination_type}".encode("utf-8")
            ).hexdigest()[:24]
            self.repository.upsert_recommendation_destination_receipt(
                {
                    "receipt_id": receipt_id,
                    "collection_id": str(item["collection_id"]),
                    "group_id": str(plan["group_id"]),
                    "local_asset_id": local_asset_id,
                    "destination_type": destination_type,
                    "destination_id": destination_id,
                    "provider_media_item_id": provider_media_item_id,
                    "state": state,
                    "content_hash": str(item["content_hash"]),
                    "reconciled_at": now if state == "completed" else "",
                }
            )

    def _update_group_after_write(
        self,
        plan: dict[str, Any],
        album_id: str,
        wrote_any: bool,
    ) -> None:
        group = self.repository.get_recommendation_group(str(plan["group_id"])) or {}
        self.repository.upsert_recommendation_group(
            {
                **group,
                "group_id": str(plan["group_id"]),
                "group_type": str(group.get("group_type") or "monthly"),
                "display_name": str(group.get("display_name") or plan["group_name"]),
                "destination_provider": str(plan["destination_provider"]),
                "destination_album_name": str(plan["destination_album_name"]),
                "destination_album_id": album_id,
                "policy_state": "approved_once" if wrote_any else str(
                    group.get("policy_state") or "draft"
                ),
            }
        )
