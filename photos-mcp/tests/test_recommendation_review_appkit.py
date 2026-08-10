from __future__ import annotations

import json

from AppKit import NSApplication

from photos_mcp.interfaces.appkit.recommendation_review import (
    PhotosMcpRecommendationReviewController,
)
from photos_mcp.interfaces.appkit.results.controller import PhotosMcpResultsController


def _result_payload(scene_count: int = 2) -> dict:
    items = []
    for scene_index in range(scene_count):
        for rank in (1, 2, 3):
            items.append(
                {
                    "photo_id": f"scene-{scene_index}-photo-{rank}",
                    "scene_cluster_id": f"scene-{scene_index}",
                    "scene_cluster_size": 3,
                    "cluster_rank": rank,
                    "total_score": 90 - rank,
                    "preview_path": "",
                    "recommended_in_cluster": rank <= 2,
                    "recommendation_slot": rank if rank <= 2 else 0,
                }
            )
    return {"job_id": "review-appkit", "items": items}


def test_review_window_persists_human_choice_and_advances(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("PHOTOS_MCP_HOME", str(tmp_path))
    NSApplication.sharedApplication()
    controller = PhotosMcpRecommendationReviewController.alloc().initWithResultPayload_(
        _result_payload()
    )

    controller.selectCandidateAtIndex_(2, False)
    controller.selectCandidateAtIndex_(1, True)
    controller.saveAndNext_(None)

    stored = json.loads(
        (tmp_path / "validation/recommendation-quality/review-appkit/review-private.json").read_text(
            encoding="utf-8"
        )
    )
    assert controller._index == 1
    assert stored["items"][0]["labels"]["best_photo_ids"] == [
        "scene-0-photo-3",
        "scene-0-photo-2",
    ]
    assert stored["items"][0]["labels"]["review_status"] == "completed"
    assert oct(
        (tmp_path / "validation/recommendation-quality/review-appkit/review-private.json").stat().st_mode
        & 0o777
    ) == "0o600"


def test_results_gallery_exposes_review_button_only_for_multi_photo_scenes() -> None:
    NSApplication.sharedApplication()
    menu_controller = type("MenuController", (), {})()
    menu_controller._snapshot = type("Snapshot", (), {})()
    controller = PhotosMcpResultsController.alloc().initWithMenuController_(menu_controller)

    controller.showWithResult_(_result_payload(scene_count=1))
    assert controller._recommendation_review_button.isEnabled()
    assert controller._recommendation_review_button.accessibilityLabel() == "추천 품질 검토"

    controller.showWithResult_(
        {
            "job_id": "single-only",
            "items": [
                {
                    "photo_id": "single",
                    "scene_cluster_id": "single",
                    "scene_cluster_size": 1,
                    "cluster_rank": 1,
                }
            ],
        }
    )
    assert not controller._recommendation_review_button.isEnabled()
