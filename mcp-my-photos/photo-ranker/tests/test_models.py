"""Tests for data models."""

from models import (
    DuplicateGroup,
    EventType,
    FaceResult,
    QualityScore,
    RankedPhoto,
    SceneDescription,
)


class TestEventType:
    def test_enum_values(self):
        assert EventType.BIRTHDAY.value == "birthday"
        assert EventType.TRAVEL.value == "travel"
        assert EventType("other") == EventType.OTHER

    def test_string_comparison(self):
        assert EventType.DAILY == "daily"


class TestQualityScore:
    def test_to_dict(self):
        qs = QualityScore(
            photo_id="p1",
            aesthetic_score=35.123,
            technical_score=42.567,
            total=77.69,
        )
        d = qs.to_dict()
        assert d["photo_id"] == "p1"
        assert d["aesthetic_score"] == 35.12
        assert d["technical_score"] == 42.57
        assert d["total"] == 77.69


class TestFaceResult:
    def test_to_dict_no_embedding(self):
        f = FaceResult(bbox=(10, 100, 80, 20))
        d = f.to_dict()
        assert d["bbox"] == [10, 100, 80, 20]
        assert d["embedding_dim"] == 0

    def test_to_dict_with_embedding(self):
        f = FaceResult(bbox=(0, 0, 0, 0), embedding=[0.1] * 128)
        assert f.to_dict()["embedding_dim"] == 128


class TestSceneDescription:
    def test_to_dict(self):
        s = SceneDescription(
            scene="test",
            people_count=2,
            is_family_photo=True,
            expressions=["happy"],
            event_type=EventType.BIRTHDAY,
            event_confidence=0.9,
            quality_notes="",
            meaningful_score=8,
        )
        d = s.to_dict()
        assert d["event_type"] == "birthday"
        assert d["people_count"] == 2


class TestDuplicateGroup:
    def test_to_dict(self):
        g = DuplicateGroup(
            group_id="g1",
            photo_ids=["a", "b", "c"],
            representative_id="a",
        )
        d = g.to_dict()
        assert d["representative_id"] == "a"
        assert len(d["photo_ids"]) == 3


class TestRankedPhoto:
    def test_to_dict_rounding(self):
        r = RankedPhoto(
            photo_id="p1",
            total_score=72.345,
            quality_score=80.0,
            family_score=60.123,
            event_score=70.0,
            uniqueness_score=100.0,
            scene_description="test scene",
            event_type="birthday",
            faces_detected=3,
            known_persons=["Alice"],
        )
        d = r.to_dict()
        assert d["total_score"] == 72.34  # round(72.345, 2) = 72.34 (banker's)
        assert d["family_score"] == 60.12
        assert d["known_persons"] == ["Alice"]

    def test_meaningful_score_default(self):
        r = RankedPhoto(
            photo_id="p1", total_score=50, quality_score=50,
            family_score=50, event_score=50, uniqueness_score=50,
            scene_description="", event_type="other", faces_detected=0,
        )
        assert r.meaningful_score == 5
        assert r.to_dict()["meaningful_score"] == 5

    def test_capture_date_in_to_dict(self):
        r = RankedPhoto(
            photo_id="p1", total_score=50, quality_score=50,
            family_score=50, event_score=50, uniqueness_score=50,
            scene_description="", event_type="other", faces_detected=0,
            capture_date="2026-03-15",
        )
        d = r.to_dict()
        assert d["capture_date"] == "2026-03-15"

    def test_capture_date_omitted_when_empty(self):
        r = RankedPhoto(
            photo_id="p1", total_score=50, quality_score=50,
            family_score=50, event_score=50, uniqueness_score=50,
            scene_description="", event_type="other", faces_detected=0,
        )
        assert "capture_date" not in r.to_dict()
