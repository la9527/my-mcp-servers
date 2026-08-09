#!/usr/bin/env python3
"""Run an auditable local PhotosMcp classification and export validation.

The script intentionally uses the same direct-classification and approved
desktop-export services as the macOS app. It never changes source files: all
test artifacts and copied originals are written below ``--output-root``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from photos_mcp.desktop_export_service import execute_selected_export, prepare_selected_export
from photos_mcp.direct_classification import (
    ClassificationCommand,
    DirectClassificationService,
    LOCAL_IMAGE_EXTENSIONS,
)
from photos_mcp.application.run_support import call_vendor
from photos_mcp.infrastructure.persistence.state_store import PhotosMcpStateStore
from photos_mcp.infrastructure.vision.runtime import vision_runtime_summary


def _utc_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _evenly_sample_paths(source_root: Path, requested_count: int) -> list[Path]:
    candidates = sorted(
        path.resolve()
        for path in source_root.rglob("*")
        if path.is_file() and path.suffix.lower() in LOCAL_IMAGE_EXTENSIONS
    )
    if len(candidates) < requested_count:
        raise ValueError(
            f"요청한 {requested_count}장보다 지원되는 원본이 적습니다: {len(candidates)}장"
        )
    if requested_count == 1:
        return [candidates[0]]
    indexes = [round(index * (len(candidates) - 1) / (requested_count - 1)) for index in range(requested_count)]
    return [candidates[index] for index in indexes]


async def _wait_for_terminal_job(
    job_id: str,
    *,
    timeout_seconds: float,
    poll_seconds: float,
) -> dict[str, Any]:
    started = time.monotonic()
    previous_progress: tuple[object, ...] | None = None
    while True:
        summary = await call_vendor("photo-ranker", "get_job_summary", job_id)
        if not isinstance(summary, dict):
            raise RuntimeError("분류 작업 상태를 읽지 못했습니다.")
        status = str(summary.get("status") or "")
        progress = dict(summary.get("progress") or {})
        progress_marker = (
            status,
            progress.get("stage"),
            progress.get("completed"),
            progress.get("total"),
        )
        if progress_marker != previous_progress:
            print(
                "status=%s stage=%s progress=%s/%s"
                % (
                    status,
                    progress.get("stage") or "-",
                    progress.get("completed") or 0,
                    progress.get("total") or 0,
                ),
                flush=True,
            )
            previous_progress = progress_marker
        if status in {"completed", "failed", "cancelled"}:
            return summary
        if time.monotonic() - started >= timeout_seconds:
            raise TimeoutError(f"분류 작업이 {timeout_seconds:.0f}초 안에 끝나지 않았습니다: {job_id}")
        await asyncio.sleep(poll_seconds)


def _validate_export(output_dir: Path, *, expected_count: int, local_result: dict[str, Any]) -> dict[str, Any]:
    manifest_name = str(local_result.get("manifest_path") or "")
    manifest_path = output_dir / manifest_name
    if not manifest_name or not manifest_path.is_file():
        raise RuntimeError("내보내기 매니페스트를 찾지 못했습니다.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    items = list(manifest.get("items") or [])
    exported_files = [output_dir / str(item.get("relative_path") or "") for item in items]
    sidecars = [output_dir / str(item.get("sidecar_path") or "") for item in items]
    missing_files = sum(not path.is_file() for path in exported_files)
    missing_sidecars = sum(not path.is_file() for path in sidecars)
    if len(items) != expected_count or missing_files or missing_sidecars:
        raise RuntimeError(
            "내보내기 검증 실패: "
            f"items={len(items)} expected={expected_count} "
            f"missing_files={missing_files} missing_sidecars={missing_sidecars}"
        )
    return {
        "manifest": manifest_name,
        "item_count": len(items),
        "missing_files": missing_files,
        "missing_sidecars": missing_sidecars,
        "status_counts": dict(Counter(str(item.get("status") or "") for item in items)),
        "event_counts": dict(Counter(str(item.get("event_type") or "") for item in items)),
    }


async def _run(args: argparse.Namespace) -> int:
    source_root = Path(args.source_root).expanduser().resolve()
    if not source_root.is_dir():
        raise ValueError(f"원본 폴더를 찾지 못했습니다: {source_root}")

    output_root = Path(args.output_root).expanduser().resolve()
    if output_root.exists():
        raise ValueError(f"테스트 출력 폴더가 이미 있습니다: {output_root}")
    output_root.mkdir(parents=True)
    export_dir = output_root / "exported-originals"

    selected_paths = _evenly_sample_paths(source_root, args.limit)
    input_manifest = {
        "schema": "photos-mcp-local-e2e-input/v1",
        "created_at": datetime.now(UTC).isoformat(),
        "source_root": str(source_root),
        "selected_count": len(selected_paths),
        "total_bytes": sum(path.stat().st_size for path in selected_paths),
        "formats": dict(Counter(path.suffix.lower() for path in selected_paths)),
        "source_folders": dict(Counter(path.parent.name for path in selected_paths)),
        "items": [
            {
                "relative_path": path.relative_to(source_root).as_posix(),
                "bytes": path.stat().st_size,
            }
            for path in selected_paths
        ],
    }
    _write_json(output_root / "input-manifest.json", input_manifest)

    coordinator = output_root / "runtime" / "coordinator.db"
    state_store = PhotosMcpStateStore(
        endpoint="http://127.0.0.1:18791/mcp",
        health_endpoint="http://127.0.0.1:18791/health",
        repository_path=coordinator,
    )
    state_store.set_daemon_status("ready")
    command = ClassificationCommand(
        source="local",
        source_path=str(source_root),
        mode="classify",
        selection_profile="general",
        limit=args.limit,
        selected_photo_ids=tuple(str(path) for path in selected_paths),
    )
    service = DirectClassificationService(state_store=state_store)
    preview = await service.preview(command)
    if not preview.can_run or preview.run_count != args.limit:
        raise RuntimeError(f"분류 범위 사전 확인 실패: {preview.as_payload()}")

    started_at = datetime.now(UTC).isoformat()
    started = await service.execute(command)
    job_id = str(started.get("job_id") or started.get("run_id") or "")
    if not job_id:
        raise RuntimeError(f"분류 작업을 시작하지 못했습니다: {started}")
    print(f"job_id={job_id}", flush=True)
    summary = await _wait_for_terminal_job(
        job_id,
        timeout_seconds=args.timeout_seconds,
        poll_seconds=args.poll_seconds,
    )
    if str(summary.get("status") or "") != "completed":
        raise RuntimeError(f"분류 작업 실패: {summary.get('error_message') or summary}")
    if int(summary.get("photo_count") or 0) != args.limit:
        raise RuntimeError(f"분류 결과 수가 다릅니다: {summary.get('photo_count')} / {args.limit}")

    review_items = await call_vendor("photo-ranker", "get_review_items", job_id, top_n=args.limit)
    if not isinstance(review_items, list) or len(review_items) != args.limit:
        raise RuntimeError("결과 갤러리용 검토 항목 수가 분류 결과와 다릅니다.")

    # Exercise the individual selection toggle before bulk-selecting the exact export set.
    probe_photo_id = str(review_items[0].get("photo_id") or "")
    if not probe_photo_id:
        raise RuntimeError("결과 항목에 사진 식별자가 없습니다.")
    await call_vendor("photo-ranker", "set_photo_review", job_id, probe_photo_id, "[]", True, "e2e toggle probe")
    await call_vendor("photo-ranker", "set_photo_review", job_id, probe_photo_id, "[]", False, "e2e toggle probe")
    bulk_selection = await call_vendor("photo-ranker", "set_all_photo_reviews", job_id, True)
    if not isinstance(bulk_selection, dict) or int(bulk_selection.get("selected") or 0) != args.limit:
        raise RuntimeError(f"전체 선택 검증 실패: {bulk_selection}")

    export_options = {
        "run_id": job_id,
        "output_dir": str(export_dir),
        "metadata_mode": "auto",
    }
    approval = await prepare_selected_export(state_store, export_options)
    if str(approval.get("status") or "") != "awaiting_approval":
        raise RuntimeError(f"내보내기 승인 계획을 만들지 못했습니다: {approval}")
    if int(dict(approval.get("mutation_plan") or {}).get("photo_count") or 0) != args.limit:
        raise RuntimeError("내보내기 승인 계획의 대상 수가 선택 수와 다릅니다.")
    approval_token = str(approval.get("approval_token") or "")
    if not approval_token or not state_store.decide_mutation_plan(approval_token, "approved"):
        raise RuntimeError("내보내기 승인 토큰을 승인하지 못했습니다.")
    export_result = await execute_selected_export(state_store, export_options, approval_token)
    if str(export_result.get("status") or "") != "completed":
        raise RuntimeError(f"내보내기 실패: {export_result}")
    local_result = dict(dict(export_result.get("destinations") or {}).get("local_directory") or {})
    if int(local_result.get("exported") or 0) != args.limit:
        raise RuntimeError(f"원본 복사 수가 다릅니다: {local_result}")
    export_validation = _validate_export(export_dir, expected_count=args.limit, local_result=local_result)

    report = {
        "schema": "photos-mcp-local-e2e-report/v1",
        "started_at": started_at,
        "finished_at": datetime.now(UTC).isoformat(),
        "job_id": job_id,
        "input": {
            "selected_count": args.limit,
            "formats": input_manifest["formats"],
            "source_folder_count": len(input_manifest["source_folders"]),
            "total_bytes": input_manifest["total_bytes"],
        },
        "preview": preview.as_payload(),
        "summary": summary,
        "review_item_count": len(review_items),
        "bulk_selection": bulk_selection,
        "export": export_result,
        "export_validation": export_validation,
        "vision_runtime": vision_runtime_summary(check_ready=False),
    }
    _write_json(output_root / "e2e-report.json", report)
    print(json.dumps({"job_id": job_id, "output_root": str(output_root), "export": export_validation}, ensure_ascii=False), flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", required=True, help="Read-only local photo source root")
    parser.add_argument("--output-root", required=True, help="New directory for all test output")
    parser.add_argument("--limit", type=int, default=500, help="Exact number of source photos to classify")
    parser.add_argument("--timeout-seconds", type=float, default=7200.0, help="Classification timeout")
    parser.add_argument("--poll-seconds", type=float, default=10.0, help="Job status polling interval")
    args = parser.parse_args()
    if not 1 <= args.limit <= 1000:
        parser.error("--limit must be between 1 and 1000")
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
