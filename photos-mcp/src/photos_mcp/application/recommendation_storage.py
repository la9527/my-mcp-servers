"""Materialize immutable scene recommendations into a private local store."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Awaitable, Callable
import uuid
from zoneinfo import ZoneInfo

from photos_mcp.domain.models.automation import validate_private_action_base_url
from photos_mcp.application.location_privacy import (
    build_location_snapshot,
    extract_file_location,
    infer_contextual_locations,
)
from photos_mcp.infrastructure.persistence.run_repository import RunRepository
from photos_mcp.infrastructure.runtime.paths import photos_mcp_runtime_root
from photos_mcp.infrastructure.sources.google_photos.import_repository import (
    GoogleImportLeaseRepository,
)
from photos_mcp.infrastructure.vendor_adapter.gateway import call_vendor
from photos_mcp.application.story_generation import refresh_recommendation_story
from photos_mcp.application.combined_curation import reconcile_combined_curation


DEFAULT_RECOMMENDATION_ROOT = Path(
    "/Volumes/ExtData/02_Services/PhotosMcp/recommendations"
)
DEFAULT_RECOMMENDATION_POLICY_VERSION = "scene-recommendations-v1"
DEFAULT_OWNER_STORY_URL = (
    "https://byoungyoung-macmini.tail53bcc7.ts.net/photos"
)
_SEOUL = ZoneInfo("Asia/Seoul")
_SAFE_SUFFIX = re.compile(r"^\.[a-z0-9]{1,10}$")


VendorCallable = Callable[..., Awaitable[Any]]


def recommendation_root() -> Path:
    configured = os.getenv("PHOTOS_MCP_RECOMMENDATION_ROOT", "").strip()
    return Path(configured).expanduser() if configured else DEFAULT_RECOMMENDATION_ROOT


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stable_id(prefix: str, *values: str, length: int = 20) -> str:
    value = "\0".join(values)
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]
    return f"{prefix}-{digest}"


def _provider_name(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"google", "google_photos"}:
        return "google_photos"
    if normalized in {"apple", "apple_photos"}:
        return "apple_photos"
    return "local"


def _provider_token(provider: str) -> str:
    return {
        "google_photos": "google",
        "apple_photos": "apple",
        "local": "local",
    }.get(provider, "local")


def _parse_capture_date(value: Any) -> tuple[datetime | None, str, str]:
    text = str(value or "").strip()
    if not text:
        return None, "undated", "missing"
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(text[:10], "%Y-%m-%d").replace(tzinfo=_SEOUL)
        except ValueError:
            return None, "undated", "invalid"
    confidence = "provider_or_exif_timezone"
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_SEOUL)
        confidence = "assumed_asia_seoul"
    local = parsed.astimezone(_SEOUL)
    return local, local.date().isoformat(), confidence


def _source_path(item: dict[str, Any]) -> Path | None:
    candidate = str(item.get("source_photo_path") or "").strip()
    if not candidate:
        photo_id = str(item.get("photo_id") or "").strip()
        candidate = photo_id if photo_id.startswith("/") else ""
    if not candidate:
        return None
    path = Path(candidate).expanduser()
    try:
        return path.resolve() if path.is_file() else None
    except OSError:
        return None


def _safe_suffix(path: Path) -> str:
    suffix = path.suffix.lower()
    return suffix if _SAFE_SUFFIX.fullmatch(suffix) else ".bin"


def _mime_type(path: Path) -> str:
    return str(mimetypes.guess_type(path.name)[0] or "application/octet-stream")


def _ensure_private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError:
        pass


def _validate_managed_root(root: Path) -> Path:
    expanded = root.expanduser()
    parts = expanded.parts
    if len(parts) >= 3 and parts[0] == "/" and parts[1] == "Volumes":
        volume = Path("/") / parts[1] / parts[2]
        if not volume.is_dir():
            raise RuntimeError("recommendation_volume_unavailable")
    _ensure_private_directory(expanded)
    return expanded.resolve()


def _copy_atomic(source: Path, destination: Path, expected_hash: str) -> str:
    _ensure_private_directory(destination.parent)
    if destination.is_file():
        return "existing" if _sha256(destination) == expected_hash else "conflict"
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=destination.parent,
            prefix=".photos-mcp-recommendation-",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
        shutil.copyfile(source, temporary_path)
        with temporary_path.open("rb") as handle:
            os.fsync(handle.fileno())
        if _sha256(temporary_path) != expected_hash:
            raise OSError("copied content hash mismatch")
        temporary_path.chmod(0o600)
        os.replace(temporary_path, destination)
        return "exported"
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _write_json_atomic(destination: Path, payload: dict[str, Any]) -> None:
    _ensure_private_directory(destination.parent)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=destination.parent,
            prefix=".photos-mcp-manifest-",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(encoded)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        temporary_path.chmod(0o600)
        os.replace(temporary_path, destination)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _google_lease_asset_map(job_id: str) -> dict[str, dict[str, str]]:
    path = photos_mcp_runtime_root() / "google-photos" / "import-leases.sqlite3"
    if not path.is_file():
        return {}
    repository = GoogleImportLeaseRepository(path)
    try:
        mapped: dict[str, dict[str, str]] = {}
        for lease in repository.list_job(job_id):
            try:
                resolved = str(Path(lease.local_path).expanduser().resolve())
            except OSError:
                continue
            mapped[resolved] = {
                "provider_asset_id": lease.asset_key,
                "mime_type": lease.mime_type,
            }
        return mapped
    finally:
        repository.close()


class RecommendationStorageService:
    """Copy exact recommendation members to a durable date-oriented store."""

    def __init__(
        self,
        *,
        repository: RunRepository,
        root: str | Path | None = None,
    ) -> None:
        self.repository = repository
        self.root = Path(root) if root is not None else recommendation_root()

    def materialize(
        self,
        *,
        analysis_run_id: str,
        automation_run_id: str,
        provider: str,
        source_id: str,
        items: list[dict[str, Any]],
        policy_version: str = DEFAULT_RECOMMENDATION_POLICY_VERSION,
        local_run_date: str = "",
        google_asset_map: dict[str, dict[str, str]] | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        observed = now or _utcnow()
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=UTC)
        normalized_provider = _provider_name(provider)
        exact = [
            dict(item)
            for item in items
            if isinstance(item, dict)
            and bool(item.get("recommended_in_cluster"))
            and int(item.get("recommendation_slot") or 0) in {1, 2}
            and str(item.get("photo_id") or "")
        ]
        collection_id = _stable_id(
            "recommendation",
            analysis_run_id,
            policy_version,
            length=24,
        )
        effective_run_date = local_run_date or observed.astimezone(_SEOUL).date().isoformat()
        collection = {
            "collection_id": collection_id,
            "analysis_run_id": analysis_run_id,
            "automation_run_id": automation_run_id,
            "policy_version": policy_version,
            "provider": normalized_provider,
            "source_id": source_id,
            "local_run_date": effective_run_date,
            "status": "materializing" if exact else "completed",
            "recommended_count": len(exact),
            "materialized_count": 0,
            "created_at": observed.isoformat(),
        }
        self.repository.upsert_recommendation_collection(collection)
        if not exact:
            return {
                "status": "completed",
                "collection_id": collection_id,
                "analysis_run_id": analysis_run_id,
                "recommended_count": 0,
                "materialized_count": 0,
                "new_file_count": 0,
                "duplicate_count": 0,
                "failed_count": 0,
                "located_count": 0,
                "inferred_location_count": 0,
                "groups": [],
                "local_root_ready": False,
            }

        try:
            root = _validate_managed_root(self.root)
        except (OSError, RuntimeError):
            failed = {**collection, "status": "failed", "error_code": "recommendation_root_unavailable"}
            self.repository.upsert_recommendation_collection(failed)
            return {
                "status": "failed",
                "collection_id": collection_id,
                "analysis_run_id": analysis_run_id,
                "recommended_count": len(exact),
                "materialized_count": 0,
                "new_file_count": 0,
                "duplicate_count": 0,
                "failed_count": len(exact),
                "located_count": 0,
                "inferred_location_count": 0,
                "error_code": "recommendation_root_unavailable",
                "groups": [],
                "local_root_ready": False,
            }

        lease_map = google_asset_map or {}
        new_files = 0
        duplicates = 0
        failed_count = 0
        materialized_count = 0
        located_count = 0
        touched_dates: set[str] = set()
        group_ids: set[str] = set()
        for item in exact:
            photo_id = str(item.get("photo_id") or "")
            source = _source_path(item)
            lease = lease_map.get(str(source), {}) if source is not None else {}
            provider_asset_id = str(
                lease.get("provider_asset_id")
                or item.get("provider_asset_id")
                or photo_id
            )
            member = {
                "collection_id": collection_id,
                "provider": normalized_provider,
                "provider_asset_id": provider_asset_id,
                "photo_id": photo_id,
                "recommendation_slot": int(item.get("recommendation_slot") or 0),
                "scene_cluster_id": str(item.get("scene_cluster_id") or ""),
                "selection_reason_codes": [
                    str(value) for value in item.get("selection_reason_codes") or []
                ],
                "total_score": float(item.get("total_score") or 0.0),
                "quality_score": float(item.get("quality_score") or 0.0),
                "technical_score": float(item.get("technical_score") or 0.0),
                "meaningful_score": int(item.get("meaningful_score") or 0),
                "scene_description": " ".join(
                    str(item.get("scene_description") or "").split()
                )[:320],
                "event_type": " ".join(
                    str(item.get("event_type") or "").split()
                )[:60],
                "capture_date": str(item.get("capture_date") or ""),
                "materialization_status": "pending",
            }
            if source is None:
                member.update(
                    {
                        "materialization_status": "failed",
                        "error_code": "source_file_unavailable",
                    }
                )
                self.repository.upsert_recommendation_member(member)
                failed_count += 1
                continue
            try:
                content_hash = _sha256(source)
                captured_at, capture_date, date_confidence = _parse_capture_date(
                    item.get("capture_date")
                )
                existing = self.repository.get_local_recommendation_asset(content_hash)
                if existing:
                    local_asset_id = str(existing.get("local_asset_id") or "")
                    relative_path = str(existing.get("relative_path") or "")
                    destination = root / relative_path
                    if not local_asset_id or not relative_path:
                        raise OSError("invalid existing local asset")
                    copy_state = _copy_atomic(source, destination, content_hash)
                    if copy_state == "conflict":
                        raise OSError("existing local asset hash conflict")
                    duplicates += 1
                    capture_date = str(existing.get("capture_date_local") or capture_date)
                else:
                    local_asset_id = f"local-{content_hash[:24]}"
                    provider_token = _provider_token(normalized_provider)
                    if captured_at is None:
                        date_directory = Path("undated")
                        stamp = "00000000_000000"
                    else:
                        date_directory = Path(f"{captured_at.year:04d}") / capture_date
                        stamp = captured_at.strftime("%Y%m%d_%H%M%S")
                    relative_path = str(
                        date_directory
                        / f"{stamp}_{provider_token}_{content_hash[:8]}{_safe_suffix(source)}"
                    )
                    destination = root / relative_path
                    copy_state = _copy_atomic(source, destination, content_hash)
                    if copy_state == "conflict":
                        raise OSError("recommendation destination conflict")
                    if copy_state == "exported":
                        new_files += 1
                    else:
                        duplicates += 1
                    asset_payload = {
                        "local_asset_id": local_asset_id,
                        "content_hash": content_hash,
                        "relative_path": relative_path,
                        "mime_type": str(lease.get("mime_type") or _mime_type(source)),
                        "byte_size": destination.stat().st_size,
                        "capture_date_local": capture_date,
                        "capture_timezone": (
                            str(captured_at.tzinfo) if captured_at is not None else ""
                        ),
                        "capture_date_confidence": date_confidence,
                        "resource_role": "primary",
                        "verified_at": observed.isoformat(),
                    }
                    self.repository.upsert_local_recommendation_asset(asset_payload)

                private_location = self.repository.get_photo_analysis_location_private(
                    job_id=analysis_run_id,
                    photo_id=photo_id,
                )
                if private_location is None:
                    embedded = extract_file_location(source)
                    if embedded is not None:
                        private_location = {
                            "latitude": embedded.latitude,
                            "longitude": embedded.longitude,
                            "provenance": embedded.provenance,
                        }
                if private_location is not None:
                    snapshot = build_location_snapshot(
                        latitude=private_location.get("latitude"),
                        longitude=private_location.get("longitude"),
                        provenance=str(private_location.get("provenance") or "unknown"),
                        capture_timezone=(
                            str(captured_at.tzinfo) if captured_at is not None else ""
                        ),
                        observed_at=observed.isoformat(),
                    )
                    if snapshot is not None:
                        self.repository.upsert_recommendation_asset_location_private(
                            local_asset_id,
                            snapshot,
                        )
                        located_count += 1

                member.update(
                    {
                        "capture_date_local": capture_date,
                        "capture_date_confidence": date_confidence,
                        "content_hash": content_hash,
                        "local_asset_id": local_asset_id,
                        "materialization_status": "completed",
                    }
                )
                self.repository.upsert_recommendation_member(member)
                receipt_id = _stable_id(
                    "destination",
                    collection_id,
                    local_asset_id,
                    "local_store",
                    str(root),
                    length=24,
                )
                self.repository.upsert_recommendation_destination_receipt(
                    {
                        "receipt_id": receipt_id,
                        "collection_id": collection_id,
                        "group_id": "",
                        "local_asset_id": local_asset_id,
                        "destination_type": "local_store",
                        "destination_id": str(root),
                        "state": "completed",
                        "content_hash": content_hash,
                        "copy_state": copy_state,
                        "reconciled_at": observed.isoformat(),
                    }
                )
                month = (
                    capture_date[:7]
                    if capture_date != "undated"
                    else effective_run_date[:7]
                )
                group_id = f"monthly:{month}"
                destination_provider = os.getenv(
                    "PHOTOS_MCP_RECOMMENDATION_DEFAULT_DESTINATION",
                    "apple_photos",
                ).strip()
                if destination_provider not in {
                    "apple_photos",
                    "google_photos",
                    "local_only",
                }:
                    destination_provider = "apple_photos"
                group_defaults = {
                    "group_id": group_id,
                    "group_type": "monthly",
                    "display_name": f"{month} 추천",
                    "date_from": f"{month}-01",
                    "date_to": "",
                    "destination_provider": destination_provider,
                    "destination_album_id": "",
                    "destination_album_name": f"{month} 추천",
                    "policy_state": "draft",
                }
                existing_group = self.repository.get_recommendation_group(group_id)
                self.repository.upsert_recommendation_group(
                    {**group_defaults, **dict(existing_group or {})}
                )
                self.repository.add_recommendation_group_member(
                    group_id=group_id,
                    local_asset_id=local_asset_id,
                    collection_id=collection_id,
                )
                group_ids.add(group_id)
                touched_dates.add(capture_date)
                materialized_count += 1
            except (OSError, ValueError):
                member.update(
                    {
                        "materialization_status": "failed",
                        "error_code": "recommendation_materialization_failed",
                    }
                )
                self.repository.upsert_recommendation_member(member)
                failed_count += 1

        inferred_location_count = infer_contextual_locations(
            self.repository,
            collection_id,
            observed_at=observed.isoformat(),
        )

        for capture_date in sorted(touched_dates):
            self._write_date_manifest(root, capture_date, observed)

        status = (
            "completed"
            if failed_count == 0
            else "failed" if materialized_count == 0 else "partial"
        )
        final_collection = {
            **collection,
            "status": status,
            "recommended_count": len(exact),
            "materialized_count": materialized_count,
            "new_file_count": new_files,
            "duplicate_count": duplicates,
            "failed_count": failed_count,
            "located_count": located_count,
            "inferred_location_count": inferred_location_count,
            "group_ids": sorted(group_ids),
            "completed_at": observed.isoformat(),
        }
        self.repository.upsert_recommendation_collection(final_collection)
        return {
            "status": status,
            "collection_id": collection_id,
            "analysis_run_id": analysis_run_id,
            "recommended_count": len(exact),
            "materialized_count": materialized_count,
            "new_file_count": new_files,
            "duplicate_count": duplicates,
            "failed_count": failed_count,
            "located_count": located_count,
            "inferred_location_count": inferred_location_count,
            "groups": sorted(group_ids),
            "local_root_ready": True,
        }

    def _write_date_manifest(
        self,
        root: Path,
        capture_date: str,
        observed: datetime,
    ) -> None:
        assets = self.repository.list_local_recommendation_assets(
            capture_date_local=capture_date
        )
        if not assets:
            return
        directory = root / (
            Path("undated")
            if capture_date == "undated"
            else Path(capture_date[:4]) / capture_date
        )
        items = [
            {
                "local_asset_id": str(item.get("local_asset_id") or ""),
                "relative_path": str(item.get("relative_path") or ""),
                "content_hash": str(item.get("content_hash") or ""),
                "byte_size": int(item.get("byte_size") or 0),
                "mime_type": str(item.get("mime_type") or ""),
                "capture_date_local": str(item.get("capture_date_local") or ""),
                "capture_date_confidence": str(
                    item.get("capture_date_confidence") or ""
                ),
                "origins": [
                    {
                        "provider": str(member.get("provider") or "local"),
                        "provider_asset_fingerprint": hashlib.sha256(
                            str(member.get("provider_asset_id") or "").encode("utf-8")
                        ).hexdigest()[:16],
                        "recommendation_slot": int(
                            member.get("recommendation_slot") or 0
                        ),
                        "scene_cluster_id": str(
                            member.get("scene_cluster_id") or ""
                        ),
                        "selection_reason_codes": list(
                            member.get("selection_reason_codes") or []
                        ),
                        "total_score": float(member.get("total_score") or 0.0),
                        "quality_score": float(
                            member.get("quality_score") or 0.0
                        ),
                        "technical_score": float(
                            member.get("technical_score") or 0.0
                        ),
                    }
                    for member in self.repository.list_recommendation_members_for_local_asset(
                        str(item.get("local_asset_id") or "")
                    )
                ],
            }
            for item in assets
        ]
        _write_json_atomic(
            directory / "manifest.json",
            {
                "schema": "photos-mcp-recommendation-date/v1",
                "capture_date_local": capture_date,
                "updated_at": observed.astimezone(UTC).isoformat(),
                "item_count": len(items),
                "items": items,
            },
        )


async def materialize_recommendations_for_run(
    *,
    repository: RunRepository,
    analysis_run_id: str,
    automation_run_id: str = "",
    source_id: str = "",
    local_run_date: str = "",
    root: str | Path | None = None,
    call_vendor_fn: VendorCallable = call_vendor,
) -> dict[str, Any]:
    """Resolve one completed analysis job and persist only exact recommendations."""

    summary = await call_vendor_fn("photo-ranker", "get_job_summary", analysis_run_id)
    if not isinstance(summary, dict) or summary.get("error") or summary.get("error_code"):
        return {
            "status": "failed",
            "analysis_run_id": analysis_run_id,
            "error_code": "recommendation_analysis_unavailable",
        }
    analysis_status = str(summary.get("status") or "")
    if analysis_status != "completed":
        active_statuses = {
            "pending",
            "running",
            "waiting_source",
            "waiting_model",
            "writing",
        }
        return {
            "status": "pending" if analysis_status in active_statuses else analysis_status,
            "analysis_status": analysis_status,
            "analysis_run_id": analysis_run_id,
            "error_code": "recommendation_analysis_not_completed",
        }
    items = await call_vendor_fn(
        "photo-ranker",
        "get_recommended_items",
        analysis_run_id,
        top_n=100000,
    )
    if not isinstance(items, list):
        return {
            "status": "failed",
            "analysis_run_id": analysis_run_id,
            "error_code": "invalid_recommendation_result",
        }
    request_options = (
        summary.get("request_options")
        if isinstance(summary.get("request_options"), dict)
        else {}
    )
    provider = _provider_name(
        str(request_options.get("origin_provider") or summary.get("source") or "local")
    )
    google_map = (
        _google_lease_asset_map(analysis_run_id)
        if provider == "google_photos"
        else {}
    )
    return RecommendationStorageService(repository=repository, root=root).materialize(
        analysis_run_id=analysis_run_id,
        automation_run_id=automation_run_id,
        provider=provider,
        source_id=source_id,
        items=items,
        local_run_date=local_run_date,
        google_asset_map=google_map,
    )


async def auto_publish_approved_groups(
    *,
    repository: RunRepository,
    group_ids: list[str] | tuple[str, ...],
    root: str | Path | None = None,
    call_vendor_fn: VendorCallable = call_vendor,
    publish_service_factory: Callable[..., Any] | None = None,
    photos_run_fn: VendorCallable | None = None,
) -> dict[str, Any]:
    """Append new recommendations only to a previously approved fixed album.

    First-time publication remains approval-gated. Automatic continuation is
    eligible only when an earlier successful write left both ``approved_once``
    policy state and a provider album identifier on the monthly group.
    """

    if publish_service_factory is None:
        # Delayed imports avoid the publisher's intentional dependency on the
        # local recommendation storage primitives in this module.
        from photos_mcp.application.recommendation_publish import (
            RecommendationGroupPublishService,
        )

        publish_service_factory = RecommendationGroupPublishService
    if photos_run_fn is None:
        from photos_mcp.application.run_service import photos_run

        photos_run_fn = photos_run
    service = None
    results: list[dict[str, Any]] = []
    eligible = 0
    published = 0
    failed = 0
    for group_id in sorted({str(value) for value in group_ids if str(value)}):
        group = repository.get_recommendation_group(group_id) or {}
        if (
            str(group.get("policy_state") or "") != "approved_once"
            or str(group.get("destination_provider") or "")
            not in {"apple_photos", "google_photos"}
            or not str(group.get("destination_album_id") or "")
        ):
            continue
        eligible += 1
        if service is None:
            service = publish_service_factory(
                repository=repository,
                root=root,
                call_vendor_fn=call_vendor_fn,
                photos_run_fn=photos_run_fn,
            )
        try:
            plan = service.prepare_plan(group_id)
            if str(plan.get("status") or "") == "ready":
                result = await service.execute(group_id, plan)
            else:
                result = plan
        except Exception as exc:  # keep verified local storage as the primary result
            result = {
                "status": "failed",
                "group_id": group_id,
                "error_code": "automatic_album_publish_failed",
                "error_type": type(exc).__name__,
            }
        result = dict(result) if isinstance(result, dict) else {
            "status": "failed",
            "group_id": group_id,
            "error_code": "invalid_automatic_publish_result",
        }
        published += max(0, int(result.get("published_count") or 0))
        if str(result.get("status") or "") in {"failed", "partial", "blocked"}:
            # A completed duplicate-suppressed plan is not a failure.
            failed += max(1, int(result.get("failed_count") or 0))
        results.append(
            {
                "group_id": group_id,
                "status": str(result.get("status") or "unknown"),
                "published_count": max(0, int(result.get("published_count") or 0)),
                "failed_count": max(0, int(result.get("failed_count") or 0)),
                "error_code": str(result.get("error_code") or "")[:80],
            }
        )
    return {
        "eligible_group_count": eligible,
        "published_count": published,
        "failed_count": failed,
        "groups": results,
    }


def queue_recommendation_storage_notification(
    *,
    repository: RunRepository,
    automation_run: dict[str, Any],
    storage_result: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Queue one redacted KST Telegram summary, including valid zero-result runs."""

    if str(automation_run.get("parent_run_id") or ""):
        # A combined parent emits the single user-facing result after every
        # requested provider child has reached its storage terminal state.
        return None

    recommended = max(0, int(storage_result.get("recommended_count") or 0))
    observed = now or _utcnow()
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=UTC)
    status = str(storage_result.get("status") or "failed")
    provider = _provider_name(str(automation_run.get("provider") or "local"))
    provider_label = {
        "apple_photos": "Apple Photos",
        "google_photos": "Google Photos",
        "local": "로컬 사진",
    }[provider]
    title = (
        f"{provider_label} 추천 사진 보관 완료"
        if status == "completed"
        else f"{provider_label} 추천 사진 보관 확인 필요"
    )
    message = (
        f"추천 {recommended}장 중 로컬 보관 "
        f"{max(0, int(storage_result.get('materialized_count') or 0))}장이 완료되었습니다. "
        f"신규 파일 {max(0, int(storage_result.get('new_file_count') or 0))}장, "
        f"중복 통합 {max(0, int(storage_result.get('duplicate_count') or 0))}장, "
        f"실패 {max(0, int(storage_result.get('failed_count') or 0))}장입니다."
    )
    result_url = validate_private_action_base_url(
        os.getenv("PHOTOS_MCP_OWNER_STORY_URL", DEFAULT_OWNER_STORY_URL)
    )
    collection_id = str(storage_result.get("collection_id") or "")
    request_id = f"recommendation-storage-{uuid.uuid4().hex}"
    payload = {
        "request_id": request_id,
        "dedupe_key": f"recommendation-storage:{collection_id}:{status}",
        "request_type": (
            "photos_automation_success"
            if status == "completed"
            else "photos_automation_failure"
        ),
        "provider": provider,
        "status": "pending",
        "reason_code": f"recommendation_storage_{status}",
        "title": title,
        "message": message,
        "action_url": result_url,
        "expires_at": (observed + timedelta(hours=24)).isoformat(),
        "automation_run_id": str(automation_run.get("automation_run_id") or ""),
        "collection_id": collection_id,
        "local_run_date": observed.astimezone(_SEOUL).date().isoformat(),
    }
    return repository.save_user_action_request(payload)


async def reconcile_pending_recommendations(
    *,
    repository: RunRepository,
    root: str | Path | None = None,
    call_vendor_fn: VendorCallable = call_vendor,
    limit: int = 20,
) -> dict[str, Any]:
    """Advance terminal daily analyses through the required local-store gate."""

    inspected = 0
    pending = 0
    completed = 0
    partial = 0
    failed = 0
    materialized = 0
    new_files = 0
    duplicates = 0
    story_refresh_count = 0
    story_fallback_count = 0
    story_failed_count = 0
    album_published_count = 0
    album_publish_failed_count = 0

    async def refresh_story_safely() -> None:
        nonlocal story_refresh_count, story_fallback_count, story_failed_count
        try:
            story = await refresh_recommendation_story(repository)
            story_refresh_count += 1
            generation = story.get("generation") if isinstance(story, dict) else {}
            if isinstance(generation, dict) and generation.get("source") == "deterministic_fallback":
                story_fallback_count += 1
        except Exception:  # story presentation must never change storage success
            story_failed_count += 1

    for automation_run in repository.list_automation_runs():
        if inspected >= max(1, min(int(limit), 100)):
            break
        analysis_run_id = str(automation_run.get("analysis_run_id") or "")
        if not analysis_run_id:
            continue
        existing = repository.get_recommendation_collection(
            analysis_run_id=analysis_run_id,
            policy_version=DEFAULT_RECOMMENDATION_POLICY_VERSION,
        )
        if existing and str(existing.get("status") or "") == "completed":
            storage_summary = {
                **(
                    dict(automation_run.get("recommendation_storage") or {})
                    if isinstance(automation_run.get("recommendation_storage"), dict)
                    else {}
                ),
                "status": "completed",
                "collection_id": str(existing.get("collection_id") or ""),
                "analysis_run_id": analysis_run_id,
                "recommended_count": int(existing.get("recommended_count") or 0),
                "materialized_count": int(existing.get("materialized_count") or 0),
                "new_file_count": int(existing.get("new_file_count") or 0),
                "duplicate_count": int(existing.get("duplicate_count") or 0),
                "failed_count": int(existing.get("failed_count") or 0),
                "groups": list(existing.get("group_ids") or []),
                "local_root_ready": True,
            }
            automatic_publish = await auto_publish_approved_groups(
                repository=repository,
                group_ids=list(existing.get("group_ids") or []),
                root=root,
                call_vendor_fn=call_vendor_fn,
            )
            album_published_count += int(automatic_publish["published_count"])
            album_publish_failed_count += int(automatic_publish["failed_count"])
            storage_summary["automatic_publish"] = automatic_publish
            repository.upsert_automation_run(
                {
                    **automation_run,
                    "status": "completed",
                    "terminal": True,
                    "recommendation_storage": storage_summary,
                }
            )
            await refresh_story_safely()
            continue
        inspected += 1
        storage_result = await materialize_recommendations_for_run(
            repository=repository,
            analysis_run_id=analysis_run_id,
            automation_run_id=str(automation_run.get("automation_run_id") or ""),
            source_id=str(automation_run.get("source_id") or ""),
            local_run_date=str(automation_run.get("local_run_date") or ""),
            root=root,
            call_vendor_fn=call_vendor_fn,
        )
        storage_status = str(storage_result.get("status") or "failed")
        if storage_status == "pending":
            pending += 1
            repository.upsert_automation_run(
                {
                    **automation_run,
                    "status": "running",
                    "terminal": False,
                    "recommendation_storage": storage_result,
                }
            )
            continue
        analysis_status = str(storage_result.get("analysis_status") or "")
        if (
            storage_result.get("error_code")
            == "recommendation_analysis_not_completed"
            and analysis_status in {"failed", "cancelled", "interrupted"}
        ):
            # The daily-analysis bridge owns the terminal analysis notification.
            # Recommendation storage must preserve that terminal state without
            # generating a second, misleading storage-failure notification.
            failed += 1
            repository.upsert_automation_run(
                {
                    **automation_run,
                    "status": analysis_status,
                    "terminal": True,
                    "analysis_status": analysis_status,
                    "recommendation_storage": storage_result,
                    "completed_at": _utcnow().isoformat(),
                }
            )
            repository.update_processed_photo_assets_status(
                str(automation_run.get("automation_run_id") or ""),
                "failed",
            )
            continue
        if storage_status == "completed":
            completed += 1
        elif storage_status == "partial":
            partial += 1
        else:
            failed += 1
        materialized += max(0, int(storage_result.get("materialized_count") or 0))
        new_files += max(0, int(storage_result.get("new_file_count") or 0))
        duplicates += max(0, int(storage_result.get("duplicate_count") or 0))
        if storage_status in {"completed", "partial"}:
            automatic_publish = await auto_publish_approved_groups(
                repository=repository,
                group_ids=list(storage_result.get("groups") or []),
                root=root,
                call_vendor_fn=call_vendor_fn,
            )
            storage_result = {
                **storage_result,
                "automatic_publish": automatic_publish,
            }
            album_published_count += int(automatic_publish["published_count"])
            album_publish_failed_count += int(automatic_publish["failed_count"])
        updated = {
            **automation_run,
            "status": storage_status,
            "terminal": storage_status in {"completed", "partial", "failed"},
            "analysis_status": "completed",
            "recommendation_storage": storage_result,
            "recommended_count": max(
                0, int(storage_result.get("recommended_count") or 0)
            ),
            "materialized_recommendation_count": max(
                0, int(storage_result.get("materialized_count") or 0)
            ),
            "completed_at": _utcnow().isoformat(),
        }
        repository.upsert_automation_run(updated)
        repository.update_processed_photo_assets_status(
            str(automation_run.get("automation_run_id") or ""),
            "completed",
        )
        queue_recommendation_storage_notification(
            repository=repository,
            automation_run=updated,
            storage_result=storage_result,
        )
        if storage_status in {"completed", "partial"}:
            await refresh_story_safely()
    combined = reconcile_combined_curation(repository=repository)
    return {
        "status": "completed" if not failed and not partial else "partial",
        "inspected_run_count": inspected,
        "pending_run_count": pending,
        "completed_run_count": completed,
        "partial_run_count": partial,
        "failed_run_count": failed,
        "materialized_photo_count": materialized,
        "new_file_count": new_files,
        "duplicate_count": duplicates,
        "story_refresh_count": story_refresh_count,
        "story_fallback_count": story_fallback_count,
        "story_failed_count": story_failed_count,
        "album_published_count": album_published_count,
        "album_publish_failed_count": album_publish_failed_count,
        **combined,
    }
