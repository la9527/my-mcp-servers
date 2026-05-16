"""Tests for scoring module."""

from models import DuplicateGroup, EventType, FaceResult, SceneDescription
from scoring import (
    DEFAULT_SELECTION_PROFILE,
    is_valid_selection_profile,
    normalize_selection_profile,
    WEIGHT_EVENT,
    WEIGHT_FAMILY,
    WEIGHT_QUALITY,
    WEIGHT_UNIQUENESS,
    compute_event_score,
    compute_family_score,
    compute_quality_score,
    compute_uniqueness_score,
    rank_photos,
)


class TestComputeQualityScore:
    def test_perfect_scores(self):
        qs = compute_quality_score(aesthetic_raw=10.0, technical_raw=50.0)
        # Sigmoid mapping: score 10 → ~49.94 (steepness=1.5, center=5.5)
        assert qs.total >= 99.5
        assert qs.aesthetic_score >= 49.5
        assert qs.technical_score == 50.0

    def test_zero_scores(self):
        qs = compute_quality_score(aesthetic_raw=0.0, technical_raw=0.0)
        # Sigmoid mapping: score 0 → ~0.01 (steeper drop with center=5.5)
        assert qs.total <= 0.1

    def test_midrange(self):
        qs = compute_quality_score(aesthetic_raw=5.5, technical_raw=25.0)
        # Sigmoid center at 5.5 → exactly 25.0 aesthetic
        assert qs.total == 50.0

    def test_photo_id_is_none(self):
        qs = compute_quality_score(0, 0)
        assert qs.photo_id is None

    def test_wider_spread_at_typical_range(self):
        """LAION 4.5-6.5 should map to a wider spread than before."""
        qs_low = compute_quality_score(aesthetic_raw=4.5, technical_raw=0.0)
        qs_high = compute_quality_score(aesthetic_raw=6.5, technical_raw=0.0)
        spread = qs_high.aesthetic_score - qs_low.aesthetic_score
        # With steepness=1.5, center=5.5: 4.5→~10.1, 6.5→~39.9 → spread ~29.8
        assert spread >= 25.0  # much wider than old ~8 spread


class TestComputeFamilyScore:
    def test_no_faces(self):
        assert compute_family_score([]) == 0.0

    def test_single_face(self):
        faces = [FaceResult(bbox=(0, 0, 0, 0))]
        score = compute_family_score(faces)
        assert score == 0.0  # 1 face, no known, no group bonus

    def test_two_faces_with_known(self):
        faces = [
            FaceResult(bbox=(0, 0, 0, 0)),
            FaceResult(bbox=(0, 0, 0, 0)),
        ]
        score = compute_family_score(faces, ["Alice", "Bob"])
        # 20 (2+ faces) + 50 (2 known * 25) = 70
        assert score == 70.0

    def test_cap_at_100(self):
        faces = [FaceResult(bbox=(0, 0, 0, 0))] * 5
        score = compute_family_score(faces, ["A", "B", "C", "D", "E"])
        assert score == 100.0

    def test_happy_expression_bonus(self):
        faces = [FaceResult(bbox=(0, 0, 0, 0), expression="happy")]
        score = compute_family_score(faces)
        assert score == 10.0

    def test_smiling_expression_bonus(self):
        """smiling, laughing, joyful, excited also count as positive."""
        for expr in ["smiling", "laughing", "joyful", "excited"]:
            faces = [FaceResult(bbox=(0, 0, 0, 0), expression=expr)]
            score = compute_family_score(faces)
            assert score == 10.0, f"{expr} should give +10 bonus"

    def test_neutral_expression_no_bonus(self):
        """neutral/unknown/serious should NOT get expression bonus."""
        for expr in ["unknown", "neutral", "serious"]:
            faces = [FaceResult(bbox=(0, 0, 0, 0), expression=expr)]
            score = compute_family_score(faces)
            assert score == 0.0, f"{expr} should not give bonus"

    def test_known_person_plus_expression(self):
        """Known person + happy expression should stack."""
        faces = [
            FaceResult(bbox=(0, 0, 0, 0), expression="happy"),
            FaceResult(bbox=(0, 0, 0, 0), expression="happy"),
        ]
        score = compute_family_score(faces, ["Alice"])
        # 20 (2+ faces) + 25 (1 known) + 20 (2 happy * 10) = 65
        assert score == 65.0

    def test_gender_age_preserved(self):
        """Face with gender/age should serialize correctly."""
        face = FaceResult(
            bbox=(10, 100, 80, 20),
            gender="male",
            age=35,
        )
        d = face.to_dict()
        assert d["gender"] == "male"
        assert d["age"] == 35


class TestComputeEventScore:
    def test_birthday_high_confidence(self):
        scene = SceneDescription(
            scene="",
            people_count=0,
            is_family_photo=False,
            expressions=[],
            event_type=EventType.BIRTHDAY,
            event_confidence=1.0,
            quality_notes="",
            meaningful_score=5,
        )
        assert compute_event_score(scene) == 90.0

    def test_daily_low_confidence(self):
        scene = SceneDescription(
            scene="",
            people_count=0,
            is_family_photo=False,
            expressions=[],
            event_type=EventType.DAILY,
            event_confidence=0.3,
            quality_notes="",
            meaningful_score=5,
        )
        assert compute_event_score(scene) == 9.0  # 30 * 0.3

    def test_minimum_confidence_floor(self):
        scene = SceneDescription(
            scene="",
            people_count=0,
            is_family_photo=False,
            expressions=[],
            event_type=EventType.TRAVEL,
            event_confidence=0.0,
            quality_notes="",
            meaningful_score=5,
        )
        # confidence floored to 0.3
        assert compute_event_score(scene) == 24.0  # 80 * 0.3


class TestComputeUniquenessScore:
    def test_not_in_any_group(self):
        assert compute_uniqueness_score("p1", []) == 100.0

    def test_representative_gets_100(self):
        groups = [
            DuplicateGroup(
                group_id="g1",
                photo_ids=["p1", "p2"],
                representative_id="p1",
            )
        ]
        assert compute_uniqueness_score("p1", groups) == 100.0

    def test_duplicate_gets_penalty(self):
        groups = [
            DuplicateGroup(
                group_id="g1",
                photo_ids=["p1", "p2", "p3"],
                representative_id="p1",
            )
        ]
        assert compute_uniqueness_score("p2", groups) == 85.0  # 100 - 15
        assert compute_uniqueness_score("p3", groups) == 70.0  # 100 - 30


class TestRankPhotos:
    def _make_score(self, pid: str, **overrides) -> dict:
        defaults = {
            "photo_id": pid,
            "quality_score": 50.0,
            "family_score": 50.0,
            "event_score": 50.0,
            "uniqueness_score": 100.0,
            "scene_description": "",
            "event_type": "daily",
            "faces_detected": 0,
            "known_persons": [],
        }
        defaults.update(overrides)
        return defaults

    def test_ranking_order(self):
        scores = [
            self._make_score("low", quality_score=10.0),
            self._make_score("high", quality_score=90.0),
            self._make_score("mid", quality_score=50.0),
        ]
        ranked = rank_photos(scores)
        assert ranked[0].photo_id == "high"
        assert ranked[-1].photo_id == "low"

    def test_top_n(self):
        scores = [self._make_score(f"p{i}") for i in range(20)]
        ranked = rank_photos(scores, top_n=5)
        assert len(ranked) == 5

    def test_weight_sum(self):
        total = WEIGHT_QUALITY + WEIGHT_FAMILY + WEIGHT_EVENT + WEIGHT_UNIQUENESS
        assert total == 1.0

    def test_total_score_calculation(self):
        scores = [
            self._make_score(
                "p1",
                quality_score=100.0,
                family_score=100.0,
                event_score=100.0,
                uniqueness_score=100.0,
            )
        ]
        ranked = rank_photos(scores)
        assert ranked[0].total_score == 100.0

    def test_zero_scores(self):
        scores = [
            self._make_score(
                "p1",
                quality_score=0.0,
                family_score=0.0,
                event_score=0.0,
                uniqueness_score=0.0,
            )
        ]
        ranked = rank_photos(scores)
        assert ranked[0].total_score == 0.0

    def test_meaningful_score_tiebreaker(self):
        """When total scores are equal, higher meaningful_score ranks first."""
        scores = [
            {**self._make_score("low_m", quality_score=50), "meaningful_score": 3},
            {**self._make_score("high_m", quality_score=50), "meaningful_score": 9},
        ]
        ranked = rank_photos(scores)
        assert ranked[0].photo_id == "high_m"
        assert ranked[0].meaningful_score == 9

    def test_capture_date_propagated(self):
        scores = [
            {**self._make_score("p1"), "capture_date": "2026-03-15"},
        ]
        ranked = rank_photos(scores)
        assert ranked[0].capture_date == "2026-03-15"

    def test_person_profile_prefers_people_photos(self):
        scores = [
            self._make_score(
                "landscape",
                quality_score=85.0,
                family_score=0.0,
                event_score=65.0,
                faces_detected=0,
                scene_description="mountain lake landscape",
                event_type="outdoor",
            ),
            self._make_score(
                "portrait",
                quality_score=78.0,
                family_score=60.0,
                event_score=40.0,
                faces_detected=2,
                known_persons=["Alice"],
                event_type="portrait",
            ),
        ]
        ranked = rank_photos(scores, selection_profile="person")
        assert ranked[0].photo_id == "portrait"

    def test_landscape_profile_prefers_outdoor_scene(self):
        scores = [
            self._make_score(
                "portrait",
                quality_score=82.0,
                family_score=65.0,
                event_score=45.0,
                faces_detected=2,
                known_persons=["Alice"],
                event_type="portrait",
            ),
            self._make_score(
                "landscape",
                quality_score=80.0,
                family_score=0.0,
                event_score=75.0,
                faces_detected=0,
                scene_description="sunset over the ocean landscape",
                event_type="outdoor",
            ),
        ]
        ranked = rank_photos(scores, selection_profile="landscape")
        assert ranked[0].photo_id == "landscape"


class TestSelectionProfiles:
    def test_normalize_selection_profile(self):
        assert normalize_selection_profile("PERSON") == "person"
        assert normalize_selection_profile("unknown") == DEFAULT_SELECTION_PROFILE

    def test_is_valid_selection_profile(self):
        assert is_valid_selection_profile("general") is True
        assert is_valid_selection_profile("landscape") is True
        assert is_valid_selection_profile("cinematic") is False
