from __future__ import annotations

from typing import Any

from photos_mcp.infrastructure.persistence.state_store import JobSnapshot, is_terminal_job_status, job_snapshot_from_payload, job_status_value


MAX_REVIEW_RESULT_ITEMS = 1000


def synthetic_review_result(payload: dict[str, Any], *, preview_path: str = "") -> dict[str, Any]:
    """Convert a persisted single-photo analysis into the common review shape."""
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    quality = result.get("quality") if isinstance(result.get("quality"), dict) else {}
    scene = result.get("scene") if isinstance(result.get("scene"), dict) else {}
    event = result.get("event") if isinstance(result.get("event"), dict) else {}
    photo_id = str(payload.get("photo_id") or quality.get("photo_id") or "")
    items: list[dict[str, Any]] = []
    if result and photo_id:
        meaningful = float(scene.get("meaningful_score") or 0.0)
        items.append(
            {
                "photo_id": photo_id,
                "total_score": float(quality.get("total") or quality.get("total_score") or 0.0),
                "quality_score": float(quality.get("technical_score") or quality.get("quality_score") or 0.0),
                "scene_description": str(scene.get("scene") or "분석 설명이 없습니다."),
                "event_type": str(event.get("event_type") or scene.get("event_type") or "기타"),
                "meaningful_score": meaningful / 10.0 if meaningful > 1.0 else meaningful,
                "preview_path": preview_path,
                "selected": True,
            }
        )
    return {
        "job_id": str(payload.get("job_id") or payload.get("run_id") or ""),
        "status": job_status_value(payload.get("status") or "unknown"),
        "source": str(payload.get("source") or ""),
        "created_at": str(payload.get("started_at") or ""),
        "finished_at": str(payload.get("finished_at") or ""),
        "result_summary": {"result_count": len(items)},
        "result_count": len(items),
        "result_available": bool(items),
        "items": items,
    }


class PhotoRankerJobStore:
    """Adapter around photo-ranker's DB and in-memory queue.

    The vendored photo-ranker DB/queue remain the source of truth. PhotosMcpStateStore
    only keeps a UI/health projection built from this adapter.
    """

    def __init__(self, module: Any) -> None:
        self._module = module

    @property
    def db(self) -> Any:
        return self._module._get_job_db()

    @property
    def queue(self) -> Any:
        return self._module._get_job_queue()

    def list_snapshots(self) -> list[JobSnapshot]:
        db_jobs = {job.id: job.to_dict() for job in self.db.list_jobs()}
        queue_jobs = {job.id: job.to_dict() for job in self.queue.list_jobs()}
        merged_jobs = []
        for job_id, payload in {**db_jobs, **queue_jobs}.items():
            normalized = dict(payload)
            normalized.setdefault("job_id", job_id)
            if job_status_value(normalized.get("status")) == "completed":
                count_method = getattr(self.db, "count_photo_results", None)
                result_count = (
                    int(count_method(job_id))
                    if callable(count_method)
                    else len(self.db.load_photo_results(job_id))
                )
                normalized["result_count"] = result_count
                normalized["result_available"] = result_count > 0
            merged_jobs.append(job_snapshot_from_payload(normalized))
        return merged_jobs

    def get_review_result(self, job_id: str, *, top_n: int = 24) -> dict[str, Any]:
        """Return local review data without exposing source photo paths to the UI."""
        job = self.db.load_job(job_id) or self.queue.get_job(job_id)
        results = self.db.load_photo_results(job_id)
        results = self._backfill_relative_scene_recommendations(job, results)
        assets = self.db.list_job_assets(job_id)
        items: list[dict[str, Any]] = []
        item_limit = max(1, min(int(top_n), MAX_REVIEW_RESULT_ITEMS))
        for result in results[:item_limit]:
            asset = assets.get(str(result.get("photo_id") or ""), {})
            items.append(
                {
                    **result,
                    "preview_path": str(asset.get("preview_path") or ""),
                    "selected": bool(asset.get("selected", False)),
                    "review_tags": list(asset.get("tags") or []),
                    "note": str(asset.get("note") or ""),
                }
            )
        return {
            "job_id": job_id,
            "status": job_status_value(getattr(job, "status", "unknown")) if job else "unknown",
            "source": str(getattr(job, "source", "") or "") if job else "",
            "created_at": getattr(job, "created_at", "") if job else "",
            "finished_at": getattr(job, "finished_at", "") if job else "",
            "result_summary": dict(getattr(job, "result_summary", {}) or {}) if job else {},
            # ``result_count`` is the completed job total, not the transport
            # window. The gallery can load up to the product cap below.
            "result_count": len(results),
            "loaded_count": len(items),
            "result_available": bool(results),
            "items": items,
        }

    def _backfill_relative_scene_recommendations(
        self, job: Any | None, results: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Apply the current classify policy to pre-policy persisted jobs.

        Completed jobs from before relative recommendations stored a fixed
        80-point decision. Reusing their persisted scene cluster ranks avoids
        a costly VLM rerun while keeping the gallery and exports consistent.
        """
        if not job or not results:
            return results
        options = getattr(job, "request_options", {}) or {}
        if str(options.get("selection_mode") or "classify") != "classify":
            return results

        summary = dict(getattr(job, "result_summary", {}) or {})
        if summary.get("scene_recommendation_policy") == "relative_scene_top_2":
            return results

        by_cluster: dict[str, list[dict[str, Any]]] = {}
        for result in results:
            photo_id = str(result.get("photo_id") or "")
            cluster_id = str(result.get("scene_cluster_id") or photo_id)
            by_cluster.setdefault(cluster_id, []).append(result)

        changed = False
        recommended_count = 0
        for members in by_cluster.values():
            members.sort(
                key=lambda item: (
                    int(item.get("cluster_rank") or 1),
                    -float(item.get("total_score") or 0.0),
                    str(item.get("photo_id") or ""),
                )
            )
            is_multi_photo_scene = max(
                int(item.get("scene_cluster_size") or 1) for item in members
            ) > 1
            for index, result in enumerate(members, start=1):
                # Keep the first two only when the scene genuinely has
                # alternatives. The pipeline's diversity check governs new
                # jobs; legacy jobs lack visual vectors, so rank is the stable
                # and reproducible fallback.
                recommended = is_multi_photo_scene and index <= 2
                slot = index if recommended else 0
                if (
                    bool(result.get("recommended_in_cluster")) != recommended
                    or int(result.get("recommendation_slot") or 0) != slot
                ):
                    changed = True
                result["recommended_in_cluster"] = recommended
                result["recommendation_slot"] = slot
                result["selection_reason_codes"] = (
                    ["scene_best", "relative_scene_leader"]
                    if slot == 1
                    else ["scene_alternative", "relative_scene_second"]
                    if slot == 2
                    else ["same_scene_alternative"]
                )
                recommended_count += int(recommended)

        summary.update(
            {
                "scene_recommendation_policy": "relative_scene_top_2",
                "scene_recommendation_min_score": None,
                "scene_recommended_count": recommended_count,
            }
        )
        job.result_summary = summary
        if changed:
            save_results = getattr(self.db, "save_photo_results", None)
            if callable(save_results):
                save_results(str(getattr(job, "id", "")), results)
        save_job = getattr(self.db, "save_job", None)
        if callable(save_job):
            save_job(job)
        return results

    def cancel_job(self, job_id: str) -> bool:
        success = self.queue.cancel_job(job_id)
        if not success:
            return False

        job = self.queue.get_job(job_id) or self.db.load_job(job_id)
        if job:
            self.db.save_job(job)
        return True

    def delete_terminal_job(self, job_id: str) -> bool:
        job = self.db.load_job(job_id) or self.queue.get_job(job_id)
        if not job or not is_terminal_job_status(getattr(job, "status", "")):
            return False

        removed_from_queue = self.queue.remove_job(job_id)
        removed_from_db = self.db.delete_job(job_id)
        return removed_from_queue or removed_from_db

    def clear_terminal_history(self, statuses: tuple[str, ...] | None = None) -> list[str]:
        target_statuses = statuses or ("completed", "failed", "cancelled")
        deleted_from_db = self.db.clear_job_history(statuses=target_statuses)

        deleted_from_queue: list[str] = []
        target_status_set = set(target_statuses)
        for job in self.queue.list_jobs():
            if job_status_value(getattr(job, "status", "")) not in target_status_set:
                continue
            if self.queue.remove_job(job.id):
                deleted_from_queue.append(job.id)

        return sorted(set(deleted_from_db) | set(deleted_from_queue))
