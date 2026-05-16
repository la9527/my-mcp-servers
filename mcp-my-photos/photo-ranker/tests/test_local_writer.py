"""Tests for local directory write-back."""

from local_writer import LocalDirectoryWriter


class TestLocalDirectoryWriter:
    def test_organize_by_event(self, tmp_path):
        src = tmp_path / "input"
        src.mkdir()
        photo = src / "a.jpg"
        photo.write_bytes(b"x")

        writer = LocalDirectoryWriter()
        result = writer.organize_by_classification(
            [
                {
                    "photo_id": str(photo),
                    "event_type": "travel",
                    "total_score": 88.0,
                }
            ],
            str(tmp_path / "out"),
        )

        assert result["copied"] == 1
        assert (tmp_path / "out" / "travel" / "a.jpg").exists()

    def test_group_by_date(self, tmp_path):
        src = tmp_path / "input"
        src.mkdir()
        photo = src / "b.jpg"
        photo.write_bytes(b"x")

        writer = LocalDirectoryWriter()
        writer.organize_by_classification(
            [
                {
                    "photo_id": str(photo),
                    "event_type": "birthday",
                    "capture_date": "2026-03-15",
                    "total_score": 70.0,
                }
            ],
            str(tmp_path / "out"),
            group_by_date=True,
        )

        assert (tmp_path / "out" / "birthday" / "2026-03" / "b.jpg").exists()

    def test_min_score_and_missing_file(self, tmp_path):
        writer = LocalDirectoryWriter()
        result = writer.organize_by_classification(
            [
                {
                    "photo_id": str(tmp_path / "missing.jpg"),
                    "event_type": "daily",
                    "total_score": 10.0,
                },
                {
                    "photo_id": str(tmp_path / "missing2.jpg"),
                    "event_type": "daily",
                    "total_score": 1.0,
                },
            ],
            str(tmp_path / "out"),
            min_score=5.0,
        )

        assert result["skipped"] == 1
        assert len(result["failed"]) == 1