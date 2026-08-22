from __future__ import annotations

import json

from AppKit import NSApplication
from Foundation import NSMakeSize

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


def test_person_composition_review_persists_label_without_changing_human_choice(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("PHOTOS_MCP_HOME", str(tmp_path))
    NSApplication.sharedApplication()
    controller = PhotosMcpRecommendationReviewController.alloc().initWithResultPayload_personCompositionReview_(
        _result_payload(),
        True,
    )

    controller._person_composition = "background_people_only"
    controller.saveAndNext_(None)

    stored = json.loads(
        (tmp_path / "validation/recommendation-quality/review-appkit/review-private.json").read_text(
            encoding="utf-8"
        )
    )
    assert stored["items"][0]["labels"]["person_composition"] == "background_people_only"
    assert stored["items"][0]["labels"]["best_photo_ids"] == []


def test_person_composition_review_header_uses_person_label_progress(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("PHOTOS_MCP_HOME", str(tmp_path))
    NSApplication.sharedApplication()
    controller = PhotosMcpRecommendationReviewController.alloc().initWithResultPayload_personCompositionReview_(
        _result_payload(),
        True,
    )
    controller._payload["items"][0]["labels"]["review_status"] = "completed"
    controller.render()

    labels = [
        str(view.stringValue() or "")
        for view in controller.window().contentView().subviews()
        if hasattr(view, "stringValue")
    ]
    assert any("인물 구성 완료 0 · 남음 2장면" in label for label in labels)
    controller.window().performClose_(None)


def test_results_gallery_exposes_review_button_only_for_multi_photo_scenes() -> None:
    NSApplication.sharedApplication()
    menu_controller = type("MenuController", (), {})()
    menu_controller._snapshot = type("Snapshot", (), {})()
    controller = PhotosMcpResultsController.alloc().initWithMenuController_(menu_controller)

    controller.showWithResult_(_result_payload(scene_count=1))
    assert controller._recommendation_review_button.isEnabled()
    assert controller._recommendation_review_button.accessibilityLabel() == "추천 품질 검토"
    assert controller._person_composition_review_button.isEnabled()
    assert controller._person_composition_review_button.accessibilityLabel() == "인물 구성 검토"
    assert controller._face_identity_review_button.accessibilityLabel() == "얼굴 동일인 검토"
    assert controller._face_identity_grouping_review_button.accessibilityLabel() == "복수 지지 검토"

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
    assert not controller._person_composition_review_button.isEnabled()


def test_results_gallery_opens_person_composition_review(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("PHOTOS_MCP_HOME", str(tmp_path))
    NSApplication.sharedApplication()
    menu_controller = type("MenuController", (), {})()
    menu_controller._snapshot = type("Snapshot", (), {})()
    controller = PhotosMcpResultsController.alloc().initWithMenuController_(menu_controller)
    controller.showWithResult_(_result_payload(scene_count=1))

    controller.openPersonCompositionReview_(None)

    review_controller = controller._person_composition_review_controller
    assert review_controller is not None
    assert review_controller._person_composition_review is True
    assert review_controller.window().title() == "인물 구성 검토"
    review_controller.window().performClose_(None)


def test_results_gallery_reflows_review_and_export_actions_at_minimum_width() -> None:
    NSApplication.sharedApplication()
    menu_controller = type("MenuController", (), {})()
    menu_controller._snapshot = type("Snapshot", (), {})()
    controller = PhotosMcpResultsController.alloc().initWithMenuController_(menu_controller)
    controller.showWithResult_(_result_payload(scene_count=1))
    controller.window().setContentSize_(NSMakeSize(1100.0, 680.0))
    controller._layout_view()

    review_frame = controller._face_identity_review_button.frame()
    person_composition_frame = controller._person_composition_review_button.frame()
    grouping_review_frame = controller._face_identity_grouping_review_button.frame()
    export_frame = controller._json_export_button.frame()
    # Review controls share the upper footer row with export actions but never overlap.
    assert review_frame.origin.x + review_frame.size.width <= export_frame.origin.x
    assert person_composition_frame.origin.x + person_composition_frame.size.width <= export_frame.origin.x
    assert grouping_review_frame.origin.x + grouping_review_frame.size.width <= export_frame.origin.x


def test_google_result_upload_action_is_source_gated_and_requires_selection() -> None:
    NSApplication.sharedApplication()
    menu_controller = type("MenuController", (), {})()
    menu_controller._snapshot = type("Snapshot", (), {})()
    controller = PhotosMcpResultsController.alloc().initWithMenuController_(menu_controller)

    local_payload = _result_payload(scene_count=1)
    controller.showWithResult_(local_payload)
    assert controller._google_upload_button.isHidden()

    google_payload = {**_result_payload(scene_count=1), "origin_provider": "google_photos"}
    controller.showWithResult_(google_payload)
    assert not controller._google_upload_button.isHidden()
    assert not controller._google_upload_button.isEnabled()
    controller._items[0]["selected"] = True
    controller._refresh_selection_controls()
    assert controller._google_upload_button.isEnabled()
    assert "1장" in str(controller._google_upload_button.title())


def test_grouping_review_uses_private_hydrated_source_paths() -> None:
    NSApplication.sharedApplication()
    menu_controller = type("MenuController", (), {})()
    menu_controller._snapshot = type("Snapshot", (), {})()
    controller = PhotosMcpResultsController.alloc().initWithMenuController_(menu_controller)
    controller.showWithResult_(_result_payload(scene_count=1))
    photo_id = str(controller._items[0]["photo_id"])
    controller._viewer_items_by_id[photo_id] = {
        **controller._items[0],
        "source_photo_path": "/private/source/original.heic",
    }

    review_items = controller._private_face_review_items()

    assert review_items[0]["source_photo_path"] == "/private/source/original.heic"
    assert "source_photo_path" not in controller._items[0]
