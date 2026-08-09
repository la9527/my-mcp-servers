from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from photos_mcp.application.mutation_approval import (
    clear_pending_mutation_plans,
    require_mutation_approval,
)
from photos_mcp.infrastructure.persistence.run_repository import RunRepository


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


def test_unchanged_plan_can_be_approved_once(tmp_path) -> None:
    repository = RunRepository(tmp_path / "runs.sqlite3")
    options = {
        "source": "apple",
        "photo_ids": ["photo-1"],
        "target_album_name": "가족 베스트",
    }
    plan, _ = require_mutation_approval(
        "photos_write",
        "add_photo_ids_to_album",
        options,
        repository=repository,
    )
    assert repository.decide_mutation_plan(plan["approval_token"], "approved") is True

    blocked, approved = require_mutation_approval(
        "photos_write",
        "add_photo_ids_to_album",
        {**options, "approval_token": plan["approval_token"]},
        repository=repository,
    )

    assert blocked is None
    assert approved is not None
    assert approved["photo_ids"] == ["photo-1"]
    assert "approval_token" not in approved


def test_pending_plan_token_cannot_execute(tmp_path) -> None:
    repository = RunRepository(tmp_path / "runs.sqlite3")
    options = {
        "source": "apple",
        "photo_ids": ["photo-1"],
        "target_album_name": "가족 베스트",
    }
    plan, _ = require_mutation_approval(
        "photos_write",
        "add_photo_ids_to_album",
        options,
        repository=repository,
    )

    blocked, approved = require_mutation_approval(
        "photos_write",
        "add_photo_ids_to_album",
        {**options, "approval_token": plan["approval_token"]},
        repository=repository,
    )

    assert approved is None
    assert blocked["error_code"] == "mutation_not_approved"
    assert repository.get_mutation_plan(plan["approval_token"])["status"] == "pending"


def test_approved_token_is_consumed_exactly_once_under_race(tmp_path) -> None:
    repository = RunRepository(tmp_path / "runs.sqlite3")
    options = {
        "source": "apple",
        "photo_ids": ["photo-1"],
        "target_album_name": "가족 베스트",
    }
    plan, _ = require_mutation_approval(
        "photos_write",
        "add_photo_ids_to_album",
        options,
        repository=repository,
    )
    token = plan["approval_token"]
    assert repository.decide_mutation_plan(token, "approved") is True

    with ThreadPoolExecutor(max_workers=8) as executor:
        consumed = list(executor.map(lambda _index: repository.consume_mutation_plan(token), range(16)))

    assert consumed.count(True) == 1
    assert consumed.count(False) == 15


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
