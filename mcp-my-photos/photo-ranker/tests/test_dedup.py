"""Tests for dedup engine."""

from engines.dedup import DedupEngine


class TestDedupEngine:
    def test_compute_hash(self, sample_photo_b64):
        engine = DedupEngine()
        h = engine.compute_hash(sample_photo_b64)
        assert isinstance(h, str)
        assert len(h) == 16  # average_hash produces 16 hex chars

    def test_compute_phash(self, sample_photo_b64):
        engine = DedupEngine()
        h = engine.compute_phash(sample_photo_b64)
        assert isinstance(h, str)
        assert len(h) == 16

    def test_identical_images_zero_distance(self, sample_photo_b64):
        engine = DedupEngine()
        h1 = engine.compute_hash(sample_photo_b64)
        h2 = engine.compute_hash(sample_photo_b64)
        assert engine.hash_distance(h1, h2) == 0

    def test_find_duplicates_identical(self, sample_photo_b64):
        engine = DedupEngine(threshold=0)
        h = engine.compute_hash(sample_photo_b64)
        photo_hashes = {"p1": h, "p2": h, "p3": h}
        groups = engine.find_duplicates(photo_hashes)
        assert len(groups) == 1
        assert len(groups[0].photo_ids) == 3

    def test_find_duplicates_no_match(self):
        engine = DedupEngine(threshold=0)
        # Very different hashes
        photo_hashes = {
            "p1": "0000000000000000",
            "p2": "ffffffffffffffff",
        }
        groups = engine.find_duplicates(photo_hashes)
        assert len(groups) == 0

    def test_find_duplicates_empty(self):
        engine = DedupEngine()
        groups = engine.find_duplicates({})
        assert groups == []

    def test_find_duplicates_single(self):
        engine = DedupEngine()
        groups = engine.find_duplicates({"p1": "0000000000000000"})
        assert groups == []
