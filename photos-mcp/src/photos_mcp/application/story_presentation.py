"""Validated visual presentation settings for recommendation stories.

Story manifests already use ``theme`` for their narrative shape.  Visual themes
therefore live in a separate, revisioned projection so a reanalysis cannot
silently replace an owner's design choice.
"""

from __future__ import annotations

from typing import Any


PRESENTATION_SCHEMA_VERSION = 1
DEFAULT_STORY_THEME_ID = "scroll_cinema"
CURRENT_STORY_THEME_IDS = frozenset({
    "quiet_memories", "moment_clusters", "scroll_cinema", "spatial_ribbon", "memory_volume",
})

STORY_THEME_DEFINITIONS: tuple[dict[str, str], ...] = (
    {
        "theme_id": "quiet_memories",
        "design_preset": "quiet-memories-v1",
        "display_name": "고요한 추억",
        "description": "한 장의 사진과 짧은 이야기, 차분한 감상에 집중합니다.",
    },
    {
        "theme_id": "moment_clusters",
        "design_preset": "moment-clusters-v1",
        "display_name": "순간의 묶음",
        "description": "사진의 비율을 살려 같은 순간을 함께 펼칩니다.",
    },
    {
        "theme_id": "scroll_cinema",
        "design_preset": "scroll-cinema-v1",
        "display_name": "스크롤 시네마",
        "description": "사진과 이야기를 아래로 천천히 읽습니다.",
    },
    {
        "theme_id": "spatial_ribbon",
        "design_preset": "spatial-ribbon-v1",
        "display_name": "공간을 흐르는 사진",
        "description": "사진을 좌우로 넘기며 입체적으로 감상합니다.",
    },
    {
        "theme_id": "memory_volume",
        "design_preset": "memory-volume-v1",
        "display_name": "펼쳐보는 기억의 책",
        "description": "사진과 이야기를 담은 종이 책장을 한 장씩 넘깁니다.",
    },
    {
        "theme_id": "journal",
        "design_preset": "poster-v1",
        "display_name": "코발트 포스터",
        "description": "강한 색면과 비대칭 편집으로 하루를 잡지처럼 보여줍니다.",
    },
    {
        "theme_id": "cinema",
        "design_preset": "darkroom-v1",
        "display_name": "암실 시네마",
        "description": "어두운 배경과 큰 사진, 자연스러운 스와이프로 감상합니다.",
    },
    {
        "theme_id": "memory_book",
        "design_preset": "playbook-v1",
        "display_name": "팝업 플레이북",
        "description": "가족과 인물 중심 사진을 경쾌한 콜라주로 묶습니다.",
    },
    {
        "theme_id": "map_journey",
        "design_preset": "transit-v1",
        "display_name": "트랜짓 아틀라스",
        "description": "지도와 장소 흐름을 중심으로 이동 경로를 정리합니다.",
    },
    {
        "theme_id": "film_index",
        "design_preset": "silver-v1",
        "display_name": "실버 인덱스",
        "description": "많은 사진을 빠르게 훑는 고밀도 콘택트 시트입니다.",
    },
)

_THEMES_BY_ID = {item["theme_id"]: item for item in STORY_THEME_DEFINITIONS}


def story_theme_definition(theme_id: str) -> dict[str, str]:
    """Return a defensive copy of one allowed visual theme definition."""

    selected = _THEMES_BY_ID.get(str(theme_id or "").strip(), _THEMES_BY_ID[DEFAULT_STORY_THEME_ID])
    return dict(selected)


def default_story_presentation(*, selection_mode: str = "automatic") -> dict[str, Any]:
    definition = story_theme_definition(DEFAULT_STORY_THEME_ID)
    return {
        "schema_version": PRESENTATION_SCHEMA_VERSION,
        "theme_id": definition["theme_id"],
        "design_preset": definition["design_preset"],
        "presentation_revision": 1,
        "selection_mode": selection_mode,
    }


def normalize_story_presentation(
    value: Any,
    *,
    selection_mode: str = "automatic",
) -> dict[str, Any]:
    """Normalize untrusted stored or request data to the presentation allowlist."""

    raw = value if isinstance(value, dict) else {}
    definition = story_theme_definition(str(raw.get("theme_id") or ""))
    try:
        revision = max(1, int(raw.get("presentation_revision") or 1))
    except (TypeError, ValueError):
        revision = 1
    mode = str(raw.get("selection_mode") or selection_mode).strip()
    if mode not in {"automatic", "manual"}:
        mode = selection_mode if selection_mode in {"automatic", "manual"} else "automatic"
    return {
        "schema_version": PRESENTATION_SCHEMA_VERSION,
        "theme_id": definition["theme_id"],
        "design_preset": definition["design_preset"],
        "presentation_revision": revision,
        "selection_mode": mode,
    }


def recommend_story_theme(story: dict[str, Any]) -> str:
    """Start every new Story with the approved, predictable reading layout."""
    return DEFAULT_STORY_THEME_ID


def owner_story_presentation(value: Any) -> dict[str, Any]:
    """Project retired owner themes to the approved default without a DB write.

    Retain the revision for optimistic saves. Public packages use normal
    normalization instead so previously issued shares keep their snapshot.
    """
    visual = normalize_story_presentation(value)
    if visual["theme_id"] not in CURRENT_STORY_THEME_IDS:
        definition = story_theme_definition(DEFAULT_STORY_THEME_ID)
        visual.update(theme_id=definition["theme_id"], design_preset=definition["design_preset"])
    return visual


def automatic_story_presentation(story: dict[str, Any]) -> dict[str, Any]:
    definition = story_theme_definition(recommend_story_theme(story))
    return {
        "schema_version": PRESENTATION_SCHEMA_VERSION,
        "theme_id": definition["theme_id"],
        "design_preset": definition["design_preset"],
        "presentation_revision": 1,
        "selection_mode": "automatic",
    }
