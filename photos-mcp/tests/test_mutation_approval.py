from __future__ import annotations

from photos_mcp.mutation_approval import (
    clear_pending_mutation_plans,
    require_mutation_approval,
)


def setup_function() -> None:
    clear_pending_mutation_plans()


def test_write_requires_plan_before_execution() -> None:
    payload, approved = require_mutation_approval(
        "photos_write",
        "add_photo_ids_to_album",
        {
            "source": "apple",
            "photo_ids": ["photo-1", "photo-2"],
            "target_album_name": "가족 베스트",
        },
    )

    assert approved is None
    assert payload is not None
    assert payload["status"] == "awaiting_approval"
    assert payload["mutation_plan"]["target_album_name"] == "가족 베스트"
    assert payload["mutation_plan"]["photo_ids_count"] == 2


def test_unchanged_plan_can_be_approved_once() -> None:
    options = {
        "source": "apple",
        "photo_ids": ["photo-1"],
        "target_album_name": "가족 베스트",
    }
    plan, _ = require_mutation_approval(
        "photos_write",
        "add_photo_ids_to_album",
        options,
    )

    blocked, approved = require_mutation_approval(
        "photos_write",
        "add_photo_ids_to_album",
        {**options, "approval_token": plan["approval_token"]},
    )

    assert blocked is None
    assert approved is not None
    assert approved["photo_ids"] == ["photo-1"]
    assert "approval_token" not in approved


def test_changed_plan_rejects_previous_approval() -> None:
    options = {
        "source": "apple",
        "photo_ids": ["photo-1"],
        "target_album_name": "가족 베스트",
    }
    plan, _ = require_mutation_approval(
        "photos_write",
        "add_photo_ids_to_album",
        options,
    )

    blocked, approved = require_mutation_approval(
        "photos_write",
        "add_photo_ids_to_album",
        {
            **options,
            "photo_ids": ["photo-1", "photo-2"],
            "approval_token": plan["approval_token"],
        },
    )

    assert approved is None
    assert blocked is not None
    assert blocked["error_code"] == "mutation_plan_changed"
