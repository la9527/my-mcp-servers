from __future__ import annotations

import importlib
import json
import sys
import types
from datetime import UTC, datetime
from pathlib import Path

import pytest

from apple_terminal_helper import TerminalHelperError
from photos_mcp.vendor_loader import prepare_vendor_runtime


def _load_scoring_module():
    prepare_vendor_runtime("photo-ranker")
    module = importlib.import_module("photos_mcp_vendor_photo_ranker.scoring")
    return importlib.reload(module)


def _load_server_module():
    prepare_vendor_runtime("photo-ranker")
    sentinel = object()
    module_names = ("mcp", "mcp.server", "mcp.server.fastmcp")
    previous_modules = {name: sys.modules.get(name, sentinel) for name in module_names}
    mcp_module = types.ModuleType("mcp")
    mcp_server_module = types.ModuleType("mcp.server")
    mcp_fastmcp_module = types.ModuleType("mcp.server.fastmcp")
    class FastMCP:
        def __init__(self, *args, **kwargs):
            pass

        def tool(self, *args, **kwargs):
            def decorator(func):
                return func

            return decorator

    mcp_fastmcp_module.FastMCP = FastMCP
    sys.modules["mcp"] = mcp_module
    sys.modules["mcp.server"] = mcp_server_module
    sys.modules["mcp.server.fastmcp"] = mcp_fastmcp_module
    try:
        module = importlib.import_module("photos_mcp_vendor_photo_ranker.server")
        return importlib.reload(module)
    finally:
        for name, previous in previous_modules.items():
            if previous is sentinel:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


def test_general_profile_weights_do_not_overpenalize_faceless_memory_shot() -> None:
    scoring = _load_scoring_module()

    ranked = scoring.rank_photos(
        [
            {
                "photo_id": "memory-shot",
                "quality_score": 52.27,
                "family_score": 0.0,
                "event_score": 15.0,
                "uniqueness_score": 100.0,
                "event_type": "daily",
                "faces_detected": 0,
                "known_persons": [],
                "meaningful_score": 4,
            }
        ],
        selection_profile="general",
    )

    assert ranked[0].total_score == 51.29


def test_top_quality_selection_limits_each_scene_to_two_photos() -> None:
    server = _load_server_module()
    results = [
        {
            "photo_id": "scene-a-1",
            "scene_cluster_id": "scene-a",
            "cluster_rank": 1,
            "total_score": 99.0,
        },
        {
            "photo_id": "scene-a-2",
            "scene_cluster_id": "scene-a",
            "cluster_rank": 2,
            "total_score": 98.0,
        },
        {
            "photo_id": "scene-a-3",
            "scene_cluster_id": "scene-a",
            "cluster_rank": 3,
            "total_score": 97.0,
        },
        {
            "photo_id": "scene-b-1",
            "scene_cluster_id": "scene-b",
            "cluster_rank": 1,
            "total_score": 96.0,
        },
    ]

    _, _, selected = server._select_top_quality_results(
        results,
        quality_top_percent=100,
        score_field="total_score",
    )

    assert [item["photo_id"] for item in selected] == [
        "scene-a-1",
        "scene-a-2",
        "scene-b-1",
    ]


def test_top_quality_selection_respects_scene_diversity_decision() -> None:
    server = _load_server_module()
    results = [
        {
            "photo_id": "scene-a-1",
            "scene_cluster_id": "scene-a",
            "total_score": 99.0,
            "recommended_in_cluster": True,
        },
        {
            "photo_id": "scene-a-near-copy",
            "scene_cluster_id": "scene-a",
            "total_score": 98.0,
            "recommended_in_cluster": False,
        },
        {
            "photo_id": "scene-b-1",
            "scene_cluster_id": "scene-b",
            "total_score": 97.0,
            "recommended_in_cluster": True,
        },
    ]

    _, _, selected = server._select_top_quality_results(
        results,
        quality_top_percent=100,
        score_field="total_score",
    )

    assert [item["photo_id"] for item in selected] == ["scene-a-1", "scene-b-1"]


def test_job_execution_metrics_exposes_durations_without_source_path() -> None:
    server = _load_server_module()
    job = types.SimpleNamespace(
        created_at=100.0,
        started_at=102.25,
        result_summary={
            "source_load_s": 1.2,
            "stage1_s": 2.5,
            "dedup_s": 0.4,
            "stage2_s": 8.1,
            "total_s": 12.3,
            "source_path": "/private/photos",
        },
    )

    metrics = server._job_execution_metrics(job)

    assert metrics == {
        "queue_seconds": 2.25,
        "source_load_seconds": 1.2,
        "filter_seconds": 2.5,
        "dedup_seconds": 0.4,
        "inference_seconds": 8.1,
        "writeback_seconds": None,
        "total_seconds": 12.3,
    }


@pytest.mark.asyncio
async def test_job_queries_prefer_and_persist_completed_queue_state(monkeypatch, tmp_path) -> None:
    server = _load_server_module()
    db = server.JobDB(tmp_path / "jobs.db")
    queue = server.JobQueue()
    live_job = queue.create_job("local", "/private/photos", job_id="background-1")
    db.save_job(live_job)

    live_job.status = server.JobStatus.COMPLETED
    live_job.finished_at = 123.5
    live_job.result_summary = {"processed": 3, "selected": 1}

    monkeypatch.setattr(server, "_get_job_db", lambda: db)
    monkeypatch.setattr(server, "_get_job_queue", lambda: queue)

    status = json.loads(await server.get_job_status("background-1"))
    summary = json.loads(await server.get_job_summary("background-1"))

    assert status["status"] == "completed"
    assert summary["status"] == "completed"
    assert summary["result_summary"] == {"processed": 3, "selected": 1}
    assert db.load_job("background-1").status == server.JobStatus.COMPLETED
    assert db.load_job("background-1").finished_at == 123.5


def test_classify_job_records_safe_runtime_metadata_before_source_load(monkeypatch) -> None:
    server = _load_server_module()
    saved = []
    job = types.SimpleNamespace(
        result_summary=None,
        request_options={},
        progress=types.SimpleNamespace(stage="", completed=0, total=0),
        source="local",
        source_path="/private/photos",
        _filters={},
    )

    class FakeDB:
        def save_job(self, value):
            saved.append(dict(value.result_summary or {}))

        def load_known_faces(self):
            return {}

    monkeypatch.setattr(server, "_get_job_db", lambda: FakeDB())
    monkeypatch.setattr(server, "_get_pipeline", lambda: types.SimpleNamespace(register_known_face=lambda *_: None))
    monkeypatch.setitem(sys.modules, "photos_mcp_vendor_photo_ranker.sources", types.SimpleNamespace(load_photos=lambda *_args, **_kwargs: []))

    import asyncio

    result = asyncio.run(server._run_classify_job(job))

    assert result["source_load_s"] >= 0
    assert saved[1]["vlm_runtime"]["provider"] == "linux_qwen36"
    assert "/private/photos" not in str(saved[1])


def test_album_writer_timeout_preserves_safe_structured_error_code() -> None:
    server = _load_server_module()

    payload = json.loads(
        server._format_album_writer_error(
            "add_to_album",
            TerminalHelperError("timeout", "Terminal helper timed out after 240s"),
        )
    )

    assert payload["error_code"] == "terminal_helper_timeout"
    assert "다시 확인" in payload["hint"]
    assert "photo_id" not in payload["details"]


def test_default_runtime_logs_do_not_include_photo_identifiers_or_source_paths() -> None:
    root = Path(__file__).parents[1] / "src" / "photos_mcp" / "vendor" / "photo-ranker"
    sources = (root / "sources.py").read_text(encoding="utf-8")
    pipeline = (root / "pipeline.py").read_text(encoding="utf-8")
    server = (root / "server.py").read_text(encoding="utf-8")

    assert "Loaded %d photos from local: %s" not in sources
    assert "gs://%s/%s" not in sources
    assert "current=%s" not in pipeline
    assert "VLM not available for %s" not in pipeline
    assert "Preview cache failed for %s" not in server
    assert "Face crop cache failed for %s" not in server


def test_gcs_loader_builds_pipeline_ready_images_without_local_files(monkeypatch) -> None:
    prepare_vendor_runtime("photo-ranker")
    sources = importlib.import_module("photos_mcp_vendor_photo_ranker.sources")

    class FakeBlob:
        name = "photos/keep.jpg"
        time_created = datetime(2026, 8, 2, tzinfo=UTC)

        def download_as_bytes(self) -> bytes:
            return (
                b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
                b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
                b"\x00\x00\x00\x0dIDATx\x9cc\xf8\xcf\xc0\xf0\x1f\x00\x05\x00\x01"
                b"\xff\x89\x99=\x1d\x00\x00\x00\x00IEND\xaeB`\x82"
            )

    class FakeBucket:
        def list_blobs(self, *, prefix: str):
            assert prefix == "photos/"
            return [FakeBlob(), types.SimpleNamespace(name="photos/readme.txt", time_created=None)]

    class FakeClient:
        def bucket(self, name: str):
            assert name == "sample-bucket"
            return FakeBucket()

    google_module = types.ModuleType("google")
    cloud_module = types.ModuleType("google.cloud")
    storage_module = types.ModuleType("google.cloud.storage")
    storage_module.Client = FakeClient
    cloud_module.storage = storage_module
    google_module.cloud = cloud_module
    monkeypatch.setitem(sys.modules, "google", google_module)
    monkeypatch.setitem(sys.modules, "google.cloud", cloud_module)
    monkeypatch.setitem(sys.modules, "google.cloud.storage", storage_module)

    photos = sources.load_photos("gcs", "gs://sample-bucket/photos/", limit=5)

    assert len(photos) == 1
    assert photos[0]["photo_id"] == "photos/keep.jpg"
    assert photos[0]["source_photo_path"] == "gs://sample-bucket/photos/keep.jpg"
    assert photos[0]["image_b64"]


@pytest.mark.asyncio
async def test_curate_general_uses_total_score_for_auto_selection(monkeypatch) -> None:
    server = _load_server_module()

    class FakeJob:
        id = "job-1"

    class FakeDB:
        def update_photo_review(self, *args, **kwargs):
            return {}

    async def fake_run_sync_classification(*args, **kwargs):
        return FakeJob(), FakeDB(), [
            {
                "photo_id": "quality-only",
                "quality_score": 90.0,
                "total_score": 40.0,
            },
            {
                "photo_id": "balanced-winner",
                "quality_score": 70.0,
                "total_score": 80.0,
            },
        ]

    monkeypatch.setattr(server, "_run_sync_classification", fake_run_sync_classification)
    monkeypatch.setattr(server, "_apply_curated_selection", lambda *args, **kwargs: None)
    monkeypatch.setattr(server, "_finalize_sync_job", lambda *args, **kwargs: None)
    monkeypatch.setattr(server, "_log_workflow_step", lambda *args, **kwargs: None)

    payload = json.loads(
        await server.curate_best_photos(
            source="local",
            source_path="/tmp/input",
            writeback_mode="review",
            quality_top_percent=50,
            selection_profile="general",
        )
    )

    assert payload["selected_photo_ids"] == ["balanced-winner"]
    assert payload["selection_policy"]["score_field"] == "total_score"
    assert payload["quality_policy"]["mode"] == "profile_top_percent"


@pytest.mark.asyncio
async def test_curate_album_writeback_reports_single_touched_album(monkeypatch) -> None:
    server = _load_server_module()

    class FakeJob:
        id = "job-album"

    class FakeDB:
        def update_photo_review(self, *args, **kwargs):
            return {}

    class FakeAlbumWriter:
        def __init__(self) -> None:
            self.calls: list[tuple[list[str], str, str]] = []

        def add_photos_to_album(self, photo_ids: list[str], album_name: str, folder: str = "") -> dict:
            self.calls.append((photo_ids, album_name, folder))
            return {
                "album": album_name,
                "added": len(photo_ids),
                "failed": 0,
                "errors": [],
            }

        def organize_by_classification(self, *args, **kwargs):
            raise AssertionError("classification album flow must not run for curate album writeback")

    fake_writer = FakeAlbumWriter()

    async def fake_run_sync_classification(*args, **kwargs):
        return FakeJob(), FakeDB(), [
            {
                "photo_id": "winner-1",
                "quality_score": 88.0,
                "total_score": 92.0,
            },
            {
                "photo_id": "winner-2",
                "quality_score": 82.0,
                "total_score": 84.0,
            },
        ]

    monkeypatch.setattr(server, "_run_sync_classification", fake_run_sync_classification)
    monkeypatch.setattr(server, "_apply_curated_selection", lambda *args, **kwargs: None)
    monkeypatch.setattr(server, "_finalize_sync_job", lambda *args, **kwargs: None)
    monkeypatch.setattr(server, "_log_workflow_step", lambda *args, **kwargs: None)
    monkeypatch.setattr(server, "_get_album_writer", lambda: fake_writer)

    payload = json.loads(
        await server.curate_best_photos(
            source="apple",
            source_path="",
            writeback_mode="album",
            target_album_name="2025년 5월 4일 - PhotosMCP",
            quality_top_percent=100,
            selection_profile="general",
        )
    )

    assert fake_writer.calls == [
        (["winner-1", "winner-2"], "2025년 5월 4일 - PhotosMCP", "")
    ]
    assert payload["album_result"]["album"] == "2025년 5월 4일 - PhotosMCP"
    assert payload["touched_album_names"] == ["2025년 5월 4일 - PhotosMCP"]
    assert payload["classification_album_created"] is False


@pytest.mark.asyncio
async def test_sync_classification_resumes_same_job_and_keeps_checkpoints_until_finalize(monkeypatch, tmp_path) -> None:
    server = _load_server_module()
    sources = importlib.import_module(f"{server.__package__}.sources")
    db = server.JobDB(tmp_path / "jobs.db")
    queue = server.JobQueue()
    restored = queue.create_job("local", "/tmp/input", job_id="workflow-run-1")
    restored.status = server.JobStatus.FAILED
    restored.error_message = "simulated interruption"
    db.save_job(restored)
    db.save_checkpoint("workflow-run-1", "vlm", "photo-1", {"photo_id": "photo-1"})

    class FakeResult:
        def to_dict(self) -> dict:
            return {"photo_id": "photo-1", "total_score": 90.0}

    class FakePipeline:
        async def run(self, photos, job, *, selection_profile: str):
            assert photos == ["photo-1"]
            assert job.id == "workflow-run-1"
            assert job.error_message is None
            assert job.request_options["retain_checkpoints"] is True
            assert db.load_checkpoints(job.id, "vlm") == {"photo-1": {"photo_id": "photo-1"}}
            return [FakeResult()]

    monkeypatch.setattr(sources, "load_photos", lambda *_args, **_kwargs: ["photo-1"])
    monkeypatch.setattr(server, "_get_job_db", lambda: db)
    monkeypatch.setattr(server, "_get_job_queue", lambda: queue)
    monkeypatch.setattr(server, "_get_pipeline", lambda: FakePipeline())
    monkeypatch.setattr(server, "_register_known_faces", lambda *_args: None)
    monkeypatch.setattr(server, "_cache_job_review_assets", lambda *_args: None)
    monkeypatch.setattr(server, "_cache_face_review_assets", lambda *_args: None)
    monkeypatch.setattr(server, "_persist_job_result_artifact", lambda *_args, **_kwargs: None)

    job, _, results = await server._run_sync_classification(
        "local",
        "/tmp/input",
        run_id="workflow-run-1",
        retain_checkpoints=True,
    )

    assert job.id == "workflow-run-1"
    assert results[0]["photo_id"] == "photo-1"
    assert db.load_checkpoints(job.id, "vlm")

    server._finalize_sync_job(job, db, {"ranked_count": 1})
    assert db.load_checkpoints(job.id, "vlm") == {}
