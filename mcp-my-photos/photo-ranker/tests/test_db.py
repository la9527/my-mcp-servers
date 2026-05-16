"""Tests for db.py — SQLite persistence."""

import pytest

from db import JobDB
from jobs import Job, JobProgress, JobStatus


@pytest.fixture
def db(tmp_path):
    """Create a temporary JobDB instance."""
    db_path = tmp_path / "test_jobs.db"
    instance = JobDB(db_path)
    yield instance
    instance.close()


class TestJobDB:
    def test_save_and_load_job(self, db):
        job = Job(
            id="t1",
            source="local",
            source_path="/photos",
            request_options={"selection_profile": "landscape"},
        )
        db.save_job(job)

        loaded = db.load_job("t1")
        assert loaded is not None
        assert loaded.id == "t1"
        assert loaded.source == "local"
        assert loaded.status == JobStatus.PENDING
        assert loaded.request_options["selection_profile"] == "landscape"

    def test_load_nonexistent(self, db):
        assert db.load_job("fake") is None

    def test_update_job(self, db):
        job = Job(id="t2", source="gcs", source_path="bucket")
        db.save_job(job)

        job.status = JobStatus.COMPLETED
        job.result_summary = {"count": 5}
        db.save_job(job)

        loaded = db.load_job("t2")
        assert loaded.status == JobStatus.COMPLETED
        assert loaded.result_summary == {"count": 5}

    def test_list_jobs(self, db):
        j1 = Job(id="a", source="local", source_path="/a")
        j2 = Job(id="b", source="local", source_path="/b")
        j2.status = JobStatus.COMPLETED
        db.save_job(j1)
        db.save_job(j2)

        all_jobs = db.list_jobs()
        assert len(all_jobs) == 2

        pending = db.list_jobs(status="pending")
        assert len(pending) == 1
        assert pending[0].id == "a"

    def test_save_and_load_photo_results(self, db):
        results = [
            {
                "photo_id": "p1",
                "total_score": 85.0,
                "quality_score": 70.0,
                "family_score": 90.0,
                "event_score": 80.0,
                "uniqueness_score": 100.0,
                "scene_description": "Birthday party",
                "event_type": "birthday",
                "faces_detected": 3,
                "known_persons": ["Alice", "Bob"],
            },
            {
                "photo_id": "p2",
                "total_score": 60.0,
                "quality_score": 55.0,
                "family_score": 40.0,
                "event_score": 70.0,
                "uniqueness_score": 75.0,
                "scene_description": "Outdoor",
                "event_type": "outdoor",
                "faces_detected": 0,
                "known_persons": [],
            },
        ]
        db.save_photo_results("job1", results)

        loaded = db.load_photo_results("job1")
        assert len(loaded) == 2
        assert loaded[0]["total_score"] == 85.0
        assert loaded[0]["known_persons"] == ["Alice", "Bob"]

    def test_load_empty_results(self, db):
        results = db.load_photo_results("nonexistent")
        assert results == []

    def test_progress_persistence(self, db):
        job = Job(
            id="t3",
            source="local",
            source_path="/tmp",
            request_options={"selection_profile": "person"},
            progress=JobProgress(
                total=100, completed=42, stage="vlm", current_file="x.jpg"
            ),
        )
        db.save_job(job)
        loaded = db.load_job("t3")
        assert loaded.progress.total == 100
        assert loaded.progress.completed == 42
        assert loaded.progress.stage == "vlm"
        assert loaded.request_options["selection_profile"] == "person"


class TestKnownFaces:
    def test_save_and_load(self, db):
        emb = [0.1] * 512
        face_idx = db.save_known_face("Alice", emb)
        assert face_idx == 0

        faces = db.load_known_faces()
        assert "Alice" in faces
        assert len(faces["Alice"]) == 1
        assert len(faces["Alice"][0]) == 512
        assert abs(faces["Alice"][0][0] - 0.1) < 1e-5

    def test_multiple_embeddings(self, db):
        db.save_known_face("Bob", [0.2] * 512)
        db.save_known_face("Bob", [0.3] * 512)

        faces = db.load_known_faces()
        assert len(faces["Bob"]) == 2

    def test_list_known_faces(self, db):
        db.save_known_face("Alice", [0.1] * 128)
        db.save_known_face("Bob", [0.2] * 128)
        db.save_known_face("Bob", [0.3] * 128)

        listing = db.list_known_faces()
        assert len(listing) == 2
        names = {e["name"]: e["embedding_count"] for e in listing}
        assert names["Alice"] == 1
        assert names["Bob"] == 2

    def test_delete_known_face(self, db):
        db.save_known_face("Charlie", [0.5] * 128)
        count = db.delete_known_face("Charlie")
        assert count == 1

        faces = db.load_known_faces()
        assert "Charlie" not in faces

    def test_delete_nonexistent(self, db):
        count = db.delete_known_face("Nobody")
        assert count == 0


class TestFaceEmbeddingCache:
    def test_save_and_load(self, db):
        emb = [0.5] * 512
        db.save_face_embedding(
            "photo1",
            0,
            emb,
            bbox=[10, 100, 90, 20],
            gender="female",
            age=25,
            expression="happy",
        )

        loaded = db.load_face_embeddings("photo1")
        assert len(loaded) == 1
        assert loaded[0]["face_idx"] == 0
        assert len(loaded[0]["embedding"]) == 512
        assert loaded[0]["bbox"] == [10, 100, 90, 20]
        assert loaded[0]["gender"] == "female"
        assert loaded[0]["age"] == 25
        assert loaded[0]["expression"] == "happy"

    def test_multiple_faces(self, db):
        db.save_face_embedding("photo2", 0, [0.1] * 512)
        db.save_face_embedding("photo2", 1, [0.2] * 512)

        loaded = db.load_face_embeddings("photo2")
        assert len(loaded) == 2
        assert loaded[0]["face_idx"] == 0
        assert loaded[1]["face_idx"] == 1

    def test_load_empty(self, db):
        loaded = db.load_face_embeddings("nonexistent")
        assert loaded == []

    def test_upsert(self, db):
        db.save_face_embedding("photo3", 0, [0.1] * 128, expression="neutral")
        db.save_face_embedding("photo3", 0, [0.2] * 128, expression="happy")

        loaded = db.load_face_embeddings("photo3")
        assert len(loaded) == 1
        assert loaded[0]["expression"] == "happy"


class TestStageCheckpoints:
    def test_save_and_load(self, db):
        cand = {"photo_id": "p1", "technical_score": 30.0, "event_type": "travel"}
        db.save_checkpoint("job1", "filter", "p1", cand)

        loaded = db.load_checkpoints("job1", "filter")
        assert "p1" in loaded
        assert loaded["p1"]["technical_score"] == 30.0

    def test_multiple_photos(self, db):
        db.save_checkpoint("job1", "filter", "p1", {"photo_id": "p1"})
        db.save_checkpoint("job1", "filter", "p2", {"photo_id": "p2"})

        loaded = db.load_checkpoints("job1", "filter")
        assert len(loaded) == 2

    def test_separate_stages(self, db):
        db.save_checkpoint("job1", "filter", "p1", {"stage": "filter"})
        db.save_checkpoint("job1", "vlm", "p1", {"stage": "vlm"})

        s1 = db.load_checkpoints("job1", "filter")
        s2 = db.load_checkpoints("job1", "vlm")
        assert s1["p1"]["stage"] == "filter"
        assert s2["p1"]["stage"] == "vlm"

    def test_clear_checkpoints(self, db):
        db.save_checkpoint("job1", "filter", "p1", {"photo_id": "p1"})
        db.save_checkpoint("job1", "vlm", "p1", {"photo_id": "p1"})
        db.clear_checkpoints("job1")

        assert db.load_checkpoints("job1", "filter") == {}
        assert db.load_checkpoints("job1", "vlm") == {}

    def test_upsert_checkpoint(self, db):
        db.save_checkpoint("job1", "filter", "p1", {"score": 10})
        db.save_checkpoint("job1", "filter", "p1", {"score": 20})

        loaded = db.load_checkpoints("job1", "filter")
        assert loaded["p1"]["score"] == 20

    def test_photo_results_with_meaningful_score(self, db):
        results = [
            {
                "photo_id": "p1",
                "total_score": 85.0,
                "quality_score": 70.0,
                "family_score": 90.0,
                "event_score": 80.0,
                "uniqueness_score": 100.0,
                "scene_description": "Birthday",
                "event_type": "birthday",
                "faces_detected": 2,
                "known_persons": [],
                "meaningful_score": 9,
                "capture_date": "2026-03-15",
            },
        ]
        db.save_photo_results("job1", results)
        loaded = db.load_photo_results("job1")
        assert loaded[0]["meaningful_score"] == 9
        assert loaded[0]["capture_date"] == "2026-03-15"


class TestReviewMetadata:
    def test_save_and_update_job_asset(self, db):
        db.save_job_asset("job1", "photo1", "/tmp/p1.jpg", "/photos/p1.jpg")
        updated = db.update_photo_review(
            "job1",
            "photo1",
            tags=["selected", "family"],
            selected=True,
            note="대표컷",
        )

        assets = db.list_job_assets("job1")
        assert assets["photo1"]["preview_path"] == "/tmp/p1.jpg"
        assert assets["photo1"]["source_photo_path"] == "/photos/p1.jpg"
        assert assets["photo1"]["selected"] is True
        assert assets["photo1"]["tags"] == ["selected", "family"]
        assert updated["note"] == "대표컷"

    def test_save_and_label_face_review(self, db):
        db.save_face_review(
            "job1",
            "photo1",
            0,
            bbox=[10, 100, 90, 20],
            crop_path="/tmp/f0.jpg",
        )
        db.label_face_review("job1", "photo1", 0, "Alice")

        reviews = db.list_face_reviews("job1", "photo1")
        assert reviews[0]["bbox"] == [10, 100, 90, 20]
        assert reviews[0]["crop_path"] == "/tmp/f0.jpg"
        assert reviews[0]["label_name"] == "Alice"


class TestStaleJobRepair:
    def test_repairs_pending_jobs_with_result_summary(self, tmp_path):
        db_path = tmp_path / "jobs.db"

        first = JobDB(db_path)
        job = Job(id="stale", source="local", source_path="/photos")
        job.result_summary = {"ranked_count": 1}
        first.save_job(job)
        first.close()

        repaired = JobDB(db_path)
        loaded = repaired.load_job("stale")

        assert loaded is not None
        assert loaded.status == JobStatus.COMPLETED
        assert loaded.started_at is not None
        assert loaded.finished_at is not None
        repaired.close()
