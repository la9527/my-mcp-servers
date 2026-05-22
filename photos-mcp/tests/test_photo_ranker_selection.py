from __future__ import annotations

import importlib
import json
import sys
import types

import pytest

from photos_mcp.vendor_loader import prepare_vendor_runtime


def _load_scoring_module():
    prepare_vendor_runtime("photo-ranker")
    module = importlib.import_module("photos_mcp_vendor_photo_ranker.scoring")
    return importlib.reload(module)


def _load_server_module():
    prepare_vendor_runtime("photo-ranker")
    mcp_module = types.ModuleType("mcp")
    mcp_server_module = types.ModuleType("mcp.server")
    mcp_fastmcp_module = types.ModuleType("mcp.server.fastmcp")
    pil_module = types.ModuleType("PIL")
    pil_module.Image = object

    class FastMCP:
        def __init__(self, *args, **kwargs):
            pass

        def tool(self, *args, **kwargs):
            def decorator(func):
                return func

            return decorator

    mcp_fastmcp_module.FastMCP = FastMCP
    sys.modules.setdefault("mcp", mcp_module)
    sys.modules.setdefault("mcp.server", mcp_server_module)
    sys.modules["mcp.server.fastmcp"] = mcp_fastmcp_module
    sys.modules.setdefault("PIL", pil_module)

    module = importlib.import_module("photos_mcp_vendor_photo_ranker.server")
    return importlib.reload(module)


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