"""Composite scoring for photo ranking."""

from __future__ import annotations

from models import (
    DuplicateGroup,
    EventType,
    FaceResult,
    QualityScore,
    RankedPhoto,
    SceneDescription,
)

# Weight configuration
# Retuned 2026-03-30: quality now has better spread (5-component technical
# + sigmoid aesthetic), and face detection fires more reliably (upscale retry
# + lower confidence), so shift weight from family→quality.
WEIGHT_QUALITY = 0.30
WEIGHT_FAMILY = 0.25
WEIGHT_EVENT = 0.25
WEIGHT_UNIQUENESS = 0.20

DEFAULT_SELECTION_PROFILE = "general"
SELECTION_PROFILES = ("general", "person", "landscape")

PROFILE_WEIGHTS: dict[str, tuple[float, float, float, float]] = {
    "general": (WEIGHT_QUALITY, WEIGHT_FAMILY, WEIGHT_EVENT, WEIGHT_UNIQUENESS),
    "person": (0.30, 0.40, 0.10, 0.20),
    "landscape": (0.45, 0.05, 0.30, 0.20),
}

EVENT_WEIGHTS: dict[EventType, float] = {
    EventType.BIRTHDAY: 90,
    EventType.GRADUATION: 90,
    EventType.TRAVEL: 80,
    EventType.CELEBRATION: 75,
    EventType.OUTDOOR: 60,
    EventType.MEAL: 60,
    EventType.PORTRAIT: 50,
    EventType.DAILY: 30,
    EventType.OTHER: 20,
}

LANDSCAPE_SCENE_KEYWORDS = (
    "landscape",
    "mountain",
    "ocean",
    "sea",
    "beach",
    "sunset",
    "sunrise",
    "forest",
    "sky",
    "river",
    "lake",
    "valley",
    "scenery",
    "nature",
)


def normalize_selection_profile(selection_profile: str | None) -> str:
    value = (selection_profile or DEFAULT_SELECTION_PROFILE).strip().lower()
    if value in SELECTION_PROFILES:
        return value
    return DEFAULT_SELECTION_PROFILE


def is_valid_selection_profile(selection_profile: str | None) -> bool:
    value = (selection_profile or DEFAULT_SELECTION_PROFILE).strip().lower()
    return value in SELECTION_PROFILES


def _score_profile_bonus(photo_score: dict, selection_profile: str) -> float:
    if selection_profile == "person":
        faces_detected = int(photo_score.get("faces_detected", 0) or 0)
        known_persons = len(photo_score.get("known_persons", []))
        event_type = str(photo_score.get("event_type", "")).lower()
        bonus = min(12.0, faces_detected * 4.0) + min(12.0, known_persons * 6.0)
        if event_type == EventType.PORTRAIT.value:
            bonus += 8.0
        elif event_type in {EventType.BIRTHDAY.value, EventType.CELEBRATION.value}:
            bonus += 4.0
        return min(25.0, bonus)

    if selection_profile == "landscape":
        faces_detected = int(photo_score.get("faces_detected", 0) or 0)
        event_type = str(photo_score.get("event_type", "")).lower()
        scene_description = str(photo_score.get("scene_description", "")).lower()
        bonus = 0.0
        if event_type in {EventType.OUTDOOR.value, EventType.TRAVEL.value}:
            bonus += 10.0
        if faces_detected == 0:
            bonus += 8.0
        elif faces_detected == 1:
            bonus += 2.0
        if any(keyword in scene_description for keyword in LANDSCAPE_SCENE_KEYWORDS):
            bonus += 10.0
        return min(25.0, bonus)

    return 0.0


def compute_quality_score(
    aesthetic_raw: float,
    technical_raw: float,
) -> QualityScore:
    """Compute quality score from aesthetic (0-10) and technical (0-50) raw scores.

    aesthetic_raw: LAION score 0-10 → mapped to 0-50 with sigmoid spread
    technical_raw: blur/exposure/noise/resolution/color 0-50
    """
    import math

    # Sigmoid-based mapping to expand differences in LAION score clusters.
    # steepness=1.5 (wider spread than 1.2), center=5.5 (corrects high bias
    # since typical real photos score slightly above 5.0 on LAION).
    # Range examples: score=3→1.1, 4→4.8, 5→16.0, 5.5→25.0, 6→34.0, 7→45.2
    sigmoid = 1.0 / (1.0 + math.exp(-1.5 * (aesthetic_raw - 5.5)))
    aesthetic_mapped = sigmoid * 50.0

    total = aesthetic_mapped + technical_raw
    return QualityScore(
        photo_id=None,
        aesthetic_score=round(aesthetic_mapped, 2),
        technical_score=round(technical_raw, 2),
        total=round(total, 2),
    )


def compute_family_score(
    faces: list[FaceResult],
    known_person_names: list[str] | None = None,
) -> float:
    """Compute family score 0-100 based on face information."""
    if not faces:
        return 0.0

    score = 0.0

    # face count bonus
    if len(faces) >= 2:
        score += 20.0

    # known person bonus: +25 per known person (max 100 total)
    if known_person_names:
        score += min(100.0, len(known_person_names) * 25.0)

    # expression bonus: positive expressions boost family photo value
    _POSITIVE_EXPRESSIONS = {"happy", "smiling", "laughing", "joyful", "excited"}
    positive_count = sum(
        1 for f in faces if f.expression.lower() in _POSITIVE_EXPRESSIONS
    )
    score += positive_count * 10.0

    return min(100.0, score)


def compute_event_score(scene: SceneDescription) -> float:
    """Compute event score 0-100 based on scene classification."""
    base = EVENT_WEIGHTS.get(scene.event_type, 20.0)
    confidence_factor = max(0.3, scene.event_confidence)
    return round(base * confidence_factor, 2)


def compute_uniqueness_score(
    photo_id: str,
    duplicate_groups: list[DuplicateGroup],
) -> float:
    """Compute uniqueness score 0-100.

    Representative photo in a group gets 100.
    Non-representative duplicates get a penalty.
    Photos not in any group get 100.
    """
    for group in duplicate_groups:
        if photo_id in group.photo_ids:
            if photo_id == group.representative_id:
                return 100.0
            else:
                idx = group.photo_ids.index(photo_id)
                penalty = min(30.0, idx * 15.0)
                return max(0.0, 100.0 - penalty)
    return 100.0


def rank_photos(
    photo_scores: list[dict],
    top_n: int | None = None,
    selection_profile: str = DEFAULT_SELECTION_PROFILE,
) -> list[RankedPhoto]:
    """Aggregate sub-scores and produce ranked results.

    Each dict in photo_scores should contain:
      - photo_id: str
      - quality_score: float (0-100)
      - family_score: float (0-100)
      - event_score: float (0-100)
      - uniqueness_score: float (0-100)
      - scene_description: str
      - event_type: str
      - faces_detected: int
      - known_persons: list[str]
    """
    normalized_profile = normalize_selection_profile(selection_profile)
    weight_quality, weight_family, weight_event, weight_uniqueness = PROFILE_WEIGHTS[normalized_profile]

    ranked = []
    for ps in photo_scores:
        total = (
            ps["quality_score"] * weight_quality
            + ps["family_score"] * weight_family
            + ps["event_score"] * weight_event
            + ps["uniqueness_score"] * weight_uniqueness
            + _score_profile_bonus(ps, normalized_profile)
        )
        ranked.append(
            RankedPhoto(
                photo_id=ps["photo_id"],
                total_score=round(total, 2),
                quality_score=round(ps["quality_score"], 2),
                family_score=round(ps["family_score"], 2),
                event_score=round(ps["event_score"], 2),
                uniqueness_score=round(ps["uniqueness_score"], 2),
                scene_description=ps.get("scene_description", ""),
                event_type=ps.get("event_type", "other"),
                faces_detected=ps.get("faces_detected", 0),
                known_persons=ps.get("known_persons", []),
                meaningful_score=ps.get("meaningful_score", 5),
                capture_date=ps.get("capture_date", ""),
            )
        )

    ranked.sort(key=lambda r: (r.total_score, r.meaningful_score), reverse=True)

    if top_n is not None:
        ranked = ranked[:top_n]

    return ranked
