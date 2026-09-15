from __future__ import annotations

from pathlib import Path

import pytest

from photos_mcp.application.story_presentation import (
    STORY_THEME_DEFINITIONS,
    automatic_story_presentation,
    normalize_story_presentation,
    owner_story_presentation,
)
from photos_mcp.infrastructure.persistence.run_repository import RunRepository


def test_theme_registry_retains_legacy_share_ids_and_adds_approved_themes() -> None:
    assert [item["theme_id"] for item in STORY_THEME_DEFINITIONS] == [
        "quiet_memories",
        "moment_clusters",
        "scroll_cinema",
        "spatial_ribbon",
        "memory_volume",
        "journal",
        "cinema",
        "memory_book",
        "map_journey",
        "film_index",
    ]
    assert len({item["design_preset"] for item in STORY_THEME_DEFINITIONS}) == 10


def test_untrusted_presentation_falls_back_to_allowed_default() -> None:
    normalized = normalize_story_presentation(
        {
            "theme_id": "<script>",
            "design_preset": "attacker-controlled",
            "selection_mode": "unexpected",
            "presentation_revision": "broken",
        }
    )

    assert normalized == {
        "schema_version": 1,
        "theme_id": "scroll_cinema",
        "design_preset": "scroll-cinema-v1",
        "presentation_revision": 1,
        "selection_mode": "automatic",
    }


@pytest.mark.parametrize("theme_id", ["quiet_memories", "moment_clusters", "scroll_cinema", "spatial_ribbon", "memory_volume"])
def test_all_five_approved_themes_survive_owner_projection(theme_id: str) -> None:
    value = {"theme_id": theme_id, "presentation_revision": 9, "selection_mode": "manual"}
    assert owner_story_presentation(value) == normalize_story_presentation(value)
    assert owner_story_presentation(value)["theme_id"] == theme_id


def test_automatic_theme_uses_approved_reading_layout_for_all_content() -> None:
    assert automatic_story_presentation({"photos": [{}] * 10})["theme_id"] == "scroll_cinema"
    assert automatic_story_presentation(
        {"photos": [{}] * 30, "people_overview": [{"display_name": "가족"}]}
    )["theme_id"] == "scroll_cinema"
    assert automatic_story_presentation(
        {
            "photos": [{}] * 30,
            "location_overview": [
                {"status": "confirmed_gps"},
                {"status": "confirmed_gps"},
            ],
        }
    )["theme_id"] == "scroll_cinema"
    assert automatic_story_presentation({"photos": [{}] * 180})["theme_id"] == "scroll_cinema"


def test_repository_presentation_is_revisioned_and_conflict_safe(tmp_path: Path) -> None:
    repository = RunRepository(tmp_path / "jobs.db")
    first = repository.upsert_story_presentation(
        "story-1",
        normalize_story_presentation(
            {"theme_id": "cinema", "selection_mode": "manual"},
            selection_mode="manual",
        ),
    )
    second = repository.upsert_story_presentation(
        "story-1",
        normalize_story_presentation(
            {"theme_id": "film_index", "selection_mode": "manual"},
            selection_mode="manual",
        ),
        expected_revision=1,
    )

    assert first["presentation_revision"] == 1
    assert second["presentation_revision"] == 2
    assert repository.get_story_presentation("story-1") == second
    with pytest.raises(ValueError, match="story_presentation_revision_conflict"):
        repository.upsert_story_presentation(
            "story-1",
            normalize_story_presentation({"theme_id": "journal"}),
            expected_revision=1,
        )


def test_owner_migration_preserves_revision_and_legacy_public_snapshot() -> None:
    legacy = {"theme_id": "film_index", "presentation_revision": 7, "selection_mode": "manual"}
    assert owner_story_presentation(legacy)["theme_id"] == "scroll_cinema"
    assert owner_story_presentation(legacy)["presentation_revision"] == 7
    assert normalize_story_presentation(legacy)["theme_id"] == "film_index"
    chosen = {"theme_id": "spatial_ribbon", "presentation_revision": 8, "selection_mode": "manual"}
    assert owner_story_presentation(chosen) == normalize_story_presentation(chosen)
