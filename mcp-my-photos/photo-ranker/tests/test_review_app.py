"""Tests for review_app HTTP endpoints."""

from fastapi.testclient import TestClient

import review_app
from jobs import Job, JobProgress, JobStatus


class TestReviewApp:
    def test_health(self):
        client = TestClient(review_app.app)
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"ok": True}

    def test_review_items_endpoint(self, monkeypatch):
        class DummyDB:
            def close(self):
                pass

        monkeypatch.setattr(review_app, "_get_db", lambda: DummyDB())
        monkeypatch.setattr(
            review_app,
            "_build_review_items",
            lambda db, job_id, top_n=100, selected_only=False: [{"photo_id": "p1", "selected": False}],
        )

        client = TestClient(review_app.app)
        response = client.get("/api/jobs/job1/items")
        assert response.status_code == 200
        assert response.json()[0]["photo_id"] == "p1"

    def test_jobs_endpoint(self, monkeypatch):
        job = Job(
            id="job1",
            source="local",
            source_path="/photos",
            request_options={"selection_profile": "landscape"},
            status=JobStatus.COMPLETED,
            progress=JobProgress(total=10, completed=10, stage="done"),
        )

        class DummyDB:
            def list_jobs(self, status=None):
                assert status is None
                return [job]

            def load_photo_results(self, job_id):
                assert job_id == "job1"
                return [{"photo_id": "a"}, {"photo_id": "b"}]

            def list_job_assets(self, job_id):
                assert job_id == "job1"
                return {
                    "a": {"selected": True, "preview_path": "/tmp/a.jpg"},
                    "b": {"selected": False, "preview_path": ""},
                }

            def close(self):
                pass

        monkeypatch.setattr(review_app, "_get_db", lambda: DummyDB())

        client = TestClient(review_app.app)
        response = client.get("/api/jobs?limit=5")
        assert response.status_code == 200
        payload = response.json()
        assert payload[0]["job_id"] == "job1"
        assert payload[0]["photo_count"] == 2
        assert payload[0]["selected_count"] == 1
        assert payload[0]["preview_path"] == "/tmp/a.jpg"
        assert payload[0]["request_options"]["selection_profile"] == "landscape"

    def test_single_job_endpoint(self, monkeypatch):
        job = Job(
            id="job2",
            source="apple",
            source_path="recent",
            request_options={"selection_profile": "person"},
            status=JobStatus.RUNNING,
            progress=JobProgress(total=20, completed=5, stage="vlm"),
        )

        class DummyDB:
            def list_jobs(self, status=None):
                assert status is None
                return [job]

            def load_photo_results(self, job_id):
                return []

            def list_job_assets(self, job_id):
                return {}

            def close(self):
                pass

        monkeypatch.setattr(review_app, "_get_db", lambda: DummyDB())

        client = TestClient(review_app.app)
        response = client.get("/api/jobs/job2")
        assert response.status_code == 200
        assert response.json()["request_options"]["selection_profile"] == "person"

    def test_review_page(self):
        client = TestClient(review_app.app)
        response = client.get("/review/job1")
        assert response.status_code == 200
        assert "Photos Review" in response.text

    def test_review_page_respects_base_path(self):
        client = TestClient(review_app.app)
        response = client.get(
            "/review/job1?base_path=/plugins/photos-classify&auth_token=secret-token"
        )
        assert response.status_code == 200
        assert "const basePath = '/plugins/photos-classify';" in response.text
        assert "const authToken = 'secret-token';" in response.text
        assert "${basePath}/api/jobs/${jobId}/items" in response.text
        assert "${basePath}/artifacts/${jobId}" in response.text