"""Tests for MCP server tool registration and basic invocation."""

import json

import pytest

from scoring import rank_photos


class TestRankBestShotsLogic:
    """Test the core ranking logic used by the rank_best_shots tool."""

    def test_rank_returns_sorted(self):
        scores = [
            {
                "photo_id": "low",
                "quality_score": 10.0,
                "family_score": 10.0,
                "event_score": 10.0,
                "uniqueness_score": 10.0,
                "scene_description": "",
                "event_type": "daily",
                "faces_detected": 0,
                "known_persons": [],
            },
            {
                "photo_id": "high",
                "quality_score": 90.0,
                "family_score": 90.0,
                "event_score": 90.0,
                "uniqueness_score": 90.0,
                "scene_description": "",
                "event_type": "birthday",
                "faces_detected": 3,
                "known_persons": ["Alice"],
            },
        ]
        ranked = rank_photos(scores, top_n=2)
        assert ranked[0].photo_id == "high"
        assert ranked[1].photo_id == "low"

    def test_rank_json_roundtrip(self):
        scores = [
            {
                "photo_id": "p1",
                "quality_score": 75.0,
                "family_score": 60.0,
                "event_score": 80.0,
                "uniqueness_score": 100.0,
                "scene_description": "family at park",
                "event_type": "outdoor",
                "faces_detected": 3,
                "known_persons": ["Alice", "Bob"],
            }
        ]
        ranked = rank_photos(scores)
        result_json = json.dumps([r.to_dict() for r in ranked])
        parsed = json.loads(result_json)
        assert len(parsed) == 1
        assert parsed[0]["photo_id"] == "p1"
        assert "total_score" in parsed[0]

    @pytest.mark.asyncio
    async def test_rank_best_shots_rejects_invalid_profile(self):
        import server

        result = await server.rank_best_shots("[]", selection_profile="invalid")
        parsed = json.loads(result)
        assert parsed["error"] == "Unsupported selection_profile"


class TestToolRegistration:
    """Verify FastMCP server has expected tools (import-only check)."""

    def test_import_server(self):
        # Ensure server module loads without errors
        import server

        assert hasattr(server, "mcp")

    def test_mcp_has_tools(self):
        import server

        # FastMCP registers tools via decorators
        # Just verify the module has tool functions
        assert callable(getattr(server, "score_quality", None))
        assert callable(getattr(server, "detect_faces", None))
        assert callable(getattr(server, "describe_scene", None))
        assert callable(getattr(server, "classify_event", None))
        assert callable(getattr(server, "find_duplicates", None))
        assert callable(getattr(server, "rank_best_shots", None))

    def test_mcp_has_album_tools(self):
        import server

        assert callable(getattr(server, "create_album", None))
        assert callable(getattr(server, "add_to_album", None))
        assert callable(getattr(server, "organize_results", None))
        assert callable(getattr(server, "organize_results_to_directory", None))
        assert callable(getattr(server, "import_photos", None))
        assert callable(getattr(server, "import_and_organize", None))
        assert callable(getattr(server, "list_photo_albums", None))

    def test_mcp_has_workflow_tool(self):
        import server

        assert callable(getattr(server, "classify_and_organize", None))

    def test_mcp_has_known_face_tools(self):
        import server

        assert callable(getattr(server, "register_face", None))
        assert callable(getattr(server, "list_known_faces", None))
        assert callable(getattr(server, "register_face_from_job", None))
        assert callable(getattr(server, "delete_known_face", None))
        assert callable(getattr(server, "list_photo_faces", None))
        assert callable(getattr(server, "label_face_in_job", None))

    def test_mcp_has_review_tools(self):
        import server

        assert callable(getattr(server, "get_review_items", None))
        assert callable(getattr(server, "set_photo_review", None))
        assert callable(getattr(server, "export_selected_photos", None))


class TestAlbumToolErrors:
    @pytest.mark.asyncio
    async def test_list_photo_albums_surfaces_permission_error(self):
        import server
        from unittest.mock import MagicMock, patch

        mock_writer = MagicMock()
        mock_writer.list_albums.side_effect = Exception(
            "Photos에 Apple 이벤트를 보낼 권한이 없습니다. (-1743)"
        )

        with patch.object(server, "_get_album_writer", return_value=mock_writer):
            result = await server.list_photo_albums()

        parsed = json.loads(result)
        assert parsed["error"] == "Apple Photos list_photo_albums failed"
        assert parsed["code"] == "apple_events_permission_denied"
        assert "-1743" in parsed["details"]
        assert "Terminal.app" in parsed["hint"]


class TestVLMToolLifecycle:
    @pytest.mark.asyncio
    async def test_describe_scene_releases_vlm_after_call(self):
        import server
        from unittest.mock import MagicMock, patch

        mock_vlm = MagicMock()
        mock_scene = MagicMock()
        mock_scene.to_dict.return_value = {"scene": "test"}
        mock_vlm.describe_scene.return_value = mock_scene

        with patch.object(server, "get_vlm", return_value=mock_vlm), patch.object(
            server, "release_vlm"
        ) as mock_release:
            result = await server.describe_scene("abc")

        parsed = json.loads(result)
        assert parsed["scene"] == "test"
        mock_release.assert_called_once()

    @pytest.mark.asyncio
    async def test_classify_event_releases_vlm_after_call(self):
        import server
        from unittest.mock import MagicMock, patch
        from models import EventType

        mock_vlm = MagicMock()
        mock_vlm.classify_event.return_value = (EventType.TRAVEL, 0.8)

        with patch.object(server, "get_vlm", return_value=mock_vlm), patch.object(
            server, "release_vlm"
        ) as mock_release:
            result = await server.classify_event("abc")

        parsed = json.loads(result)
        assert parsed["event_type"] == "travel"
        assert parsed["confidence"] == 0.8
        mock_release.assert_called_once()


class TestRegisterFaceFromJob:
    """Test register_face_from_job MCP tool logic."""

    @pytest.mark.asyncio
    async def test_register_from_cached_embedding(self):
        """Should register a face from cached embeddings in DB."""
        import server
        from unittest.mock import MagicMock, patch

        mock_db = MagicMock()
        mock_db.load_face_embeddings.return_value = [
            {"face_idx": 0, "embedding": [0.1] * 512, "gender": "male", "age": 30, "expression": "unknown"},
            {"face_idx": 1, "embedding": [0.2] * 512, "gender": "female", "age": 28, "expression": "happy"},
        ]
        mock_db.save_known_face.return_value = 0

        with patch.object(server, "_get_job_db", return_value=mock_db):
            result = await server.register_face_from_job("photo_1", 1, "Alice")

        parsed = json.loads(result)
        assert parsed["name"] == "Alice"
        assert parsed["embedding_dim"] == 512
        assert parsed["source_photo"] == "photo_1"
        assert parsed["source_face_idx"] == 1

    @pytest.mark.asyncio
    async def test_register_from_missing_photo(self):
        """Should return error for missing photo embeddings."""
        import server
        from unittest.mock import MagicMock, patch

        mock_db = MagicMock()
        mock_db.load_face_embeddings.return_value = []

        with patch.object(server, "_get_job_db", return_value=mock_db):
            result = await server.register_face_from_job("missing_photo", 0, "Alice")

        parsed = json.loads(result)
        assert "error" in parsed

    @pytest.mark.asyncio
    async def test_register_from_wrong_index(self):
        """Should return error for invalid face_idx."""
        import server
        from unittest.mock import MagicMock, patch

        mock_db = MagicMock()
        mock_db.load_face_embeddings.return_value = [
            {"face_idx": 0, "embedding": [0.1] * 128, "gender": "", "age": 0, "expression": "unknown"},
        ]

        with patch.object(server, "_get_job_db", return_value=mock_db):
            result = await server.register_face_from_job("photo_1", 5, "Alice")

        parsed = json.loads(result)
        assert "error" in parsed
        assert 0 in parsed["available_indices"]


class TestDeleteKnownFace:
    """Test delete_known_face MCP tool logic."""

    @pytest.mark.asyncio
    async def test_delete_known_face(self):
        import server
        from unittest.mock import MagicMock, patch

        mock_db = MagicMock()
        mock_db.delete_known_face.return_value = 3

        with patch.object(server, "_get_job_db", return_value=mock_db):
            result = await server.delete_known_face("Alice")

        parsed = json.loads(result)
        assert parsed["name"] == "Alice"
        assert parsed["deleted_embeddings"] == 3


class TestReviewTools:
    @pytest.mark.asyncio
    async def test_get_review_items_merges_assets(self):
        import server
        from unittest.mock import MagicMock, patch

        mock_db = MagicMock()
        mock_db.load_photo_results.return_value = [
            {"photo_id": "p1", "total_score": 90.0, "event_type": "travel"},
        ]
        mock_db.list_job_assets.return_value = {
            "p1": {
                "preview_path": "/tmp/p1.jpg",
                "source_photo_path": "/photos/p1.jpg",
                "tags": ["family"],
                "selected": True,
                "note": "pick",
            }
        }

        with patch.object(server, "_get_job_db", return_value=mock_db):
            result = await server.get_review_items("job1")

        parsed = json.loads(result)
        assert parsed[0]["preview_path"] == "/tmp/p1.jpg"
        assert parsed[0]["selected"] is True
        assert parsed[0]["review_tags"] == ["family"]

    @pytest.mark.asyncio
    async def test_label_face_in_job_registers_known_face(self):
        import server
        from unittest.mock import MagicMock, patch

        mock_db = MagicMock()
        mock_db.load_face_embeddings.return_value = [
            {"face_idx": 1, "embedding": [0.1] * 128},
        ]
        mock_db.save_known_face.return_value = 2

        with patch.object(server, "_get_job_db", return_value=mock_db):
            result = await server.label_face_in_job("job1", "photo1", 1, "Alice")

        parsed = json.loads(result)
        assert parsed["label_name"] == "Alice"
        assert parsed["known_face_registration"]["face_idx"] == 2

    @pytest.mark.asyncio
    async def test_export_selected_photos_uses_source_paths(self):
        import server
        from unittest.mock import MagicMock, patch

        mock_db = MagicMock()
        mock_writer = MagicMock()
        mock_writer.organize_by_classification.return_value = {"copied": 1, "skipped": 0, "failed": []}
        mock_db.load_photo_results.return_value = [
            {"photo_id": "uuid-1", "event_type": "travel", "total_score": 88.0},
        ]
        mock_db.list_job_assets.return_value = {
            "uuid-1": {
                "preview_path": "/tmp/p1.jpg",
                "source_photo_path": "/photos/p1.jpg",
                "tags": ["best"],
                "selected": True,
                "note": "",
            }
        }

        with patch.object(server, "_get_job_db", return_value=mock_db), patch.object(server, "_get_local_writer", return_value=mock_writer):
            result = await server.export_selected_photos("job1", "/tmp/out")

        parsed = json.loads(result)
        assert parsed["selected_count"] == 1
        called_results = mock_writer.organize_by_classification.call_args.args[0]
        assert called_results[0]["photo_id"] == "/photos/p1.jpg"


class TestClassifyAndOrganizeWorkflow:
    @pytest.mark.asyncio
    async def test_classify_and_organize_marks_job_completed(self):
        import server
        from jobs import Job
        from unittest.mock import AsyncMock, MagicMock, patch

        job = Job(id="job-sync", source="local", source_path="/photos")
        mock_queue = MagicMock()
        mock_queue.create_job.return_value = job

        mock_db = MagicMock()
        mock_db.load_known_faces.return_value = {}

        mock_ranked = [MagicMock(total_score=88.0, to_dict=lambda: {
            "photo_id": "p1",
            "total_score": 88.0,
        })]
        mock_pipeline = MagicMock()
        mock_pipeline.run = AsyncMock(return_value=mock_ranked)

        with patch("sources.load_photos", return_value=[{
            "photo_id": "p1",
            "image_b64": "abc",
            "source_photo_path": "/photos/p1.jpg",
        }]), patch.object(server, "_get_job_queue", return_value=mock_queue), patch.object(
            server,
            "_get_job_db",
            return_value=mock_db,
        ), patch.object(server, "_get_pipeline", return_value=mock_pipeline), patch.object(
            server,
            "_cache_job_review_assets",
        ), patch.object(server, "_cache_face_review_assets"):
            result = await server.classify_and_organize(
                source="local",
                source_path="/photos",
                limit=1,
                selection_profile="person",
            )

        parsed = json.loads(result)
        assert parsed["job_id"] == "job-sync"
        assert parsed["ranked_count"] == 1
        assert job.status.value == "completed"
        assert job.started_at is not None
        assert job.finished_at is not None
        assert job.result_summary == parsed
        assert job.request_options["selection_profile"] == "person"
        assert mock_db.save_job.call_count >= 2


class TestCurateBestPhotos:
    @pytest.mark.asyncio
    async def test_curate_best_photos_marks_top_quality_percent_selected(self):
        import server
        from jobs import Job
        from unittest.mock import AsyncMock, MagicMock, patch

        job = Job(id="job-curate", source="apple", source_path="")
        mock_db = MagicMock()
        mock_db.update_photo_review.return_value = {}

        results = [
            {"photo_id": "p1", "quality_score": 95.0, "total_score": 80.0},
            {"photo_id": "p2", "quality_score": 90.0, "total_score": 78.0},
            {"photo_id": "p3", "quality_score": 70.0, "total_score": 76.0},
            {"photo_id": "p4", "quality_score": 60.0, "total_score": 74.0},
            {"photo_id": "p5", "quality_score": 50.0, "total_score": 72.0},
            {"photo_id": "p6", "quality_score": 40.0, "total_score": 70.0},
            {"photo_id": "p7", "quality_score": 30.0, "total_score": 68.0},
            {"photo_id": "p8", "quality_score": 20.0, "total_score": 66.0},
            {"photo_id": "p9", "quality_score": 10.0, "total_score": 64.0},
            {"photo_id": "p10", "quality_score": 5.0, "total_score": 62.0},
        ]

        with patch.object(
            server,
            "_run_sync_classification",
            AsyncMock(return_value=(job, mock_db, results)),
        ):
            result = await server.curate_best_photos(
                source="apple",
                limit=10,
                quality_top_percent=30,
                writeback_mode="review",
            )

        parsed = json.loads(result)
        assert parsed["selected_count"] == 3
        assert parsed["selected_photo_ids"] == ["p1", "p2", "p3"]
        assert parsed["quality_policy"]["quality_min_score"] == 70.0
        assert mock_db.update_photo_review.call_count == 10

    @pytest.mark.asyncio
    async def test_curate_best_photos_uses_profile_score_for_landscape(self):
        import server
        from jobs import Job
        from unittest.mock import AsyncMock, MagicMock, patch

        job = Job(id="job-curate-landscape", source="apple", source_path="")
        mock_db = MagicMock()
        mock_db.update_photo_review.return_value = {}

        results = [
            {"photo_id": "portrait", "quality_score": 90.0, "total_score": 70.0},
            {"photo_id": "view", "quality_score": 70.0, "total_score": 88.0},
        ]

        with patch.object(
            server,
            "_run_sync_classification",
            AsyncMock(return_value=(job, mock_db, results)),
        ):
            result = await server.curate_best_photos(
                source="apple",
                limit=2,
                quality_top_percent=50,
                selection_profile="landscape",
                writeback_mode="review",
            )

        parsed = json.loads(result)
        assert parsed["selected_photo_ids"] == ["view"]
        assert parsed["selection_policy"]["score_field"] == "total_score"

    @pytest.mark.asyncio
    async def test_delete_job_rejects_active_jobs(self):
        import server
        from jobs import Job, JobStatus
        from unittest.mock import MagicMock, patch

        job = Job(id="job-running", source="apple", source_path="")
        job.status = JobStatus.RUNNING
        mock_db = MagicMock()
        mock_db.load_job.return_value = job
        mock_queue = MagicMock()

        with patch.object(server, "_get_job_db", return_value=mock_db), patch.object(
            server,
            "_get_job_queue",
            return_value=mock_queue,
        ):
            result = await server.delete_job("job-running")

        parsed = json.loads(result)
        assert parsed["deleted"] is False
        assert "active job" in parsed["error"]

    @pytest.mark.asyncio
    async def test_delete_job_removes_terminal_job_from_db_and_queue(self):
        import server
        from jobs import Job, JobStatus
        from unittest.mock import MagicMock, patch

        job = Job(id="job-done", source="apple", source_path="")
        job.status = JobStatus.COMPLETED
        mock_db = MagicMock()
        mock_db.load_job.return_value = job
        mock_db.delete_job.return_value = True
        mock_queue = MagicMock()
        mock_queue.remove_job.return_value = True

        with patch.object(server, "_get_job_db", return_value=mock_db), patch.object(
            server,
            "_get_job_queue",
            return_value=mock_queue,
        ):
            result = await server.delete_job("job-done")

        parsed = json.loads(result)
        assert parsed == {"job_id": "job-done", "deleted": True}
        mock_queue.remove_job.assert_called_once_with("job-done")
        mock_db.delete_job.assert_called_once_with("job-done")

    @pytest.mark.asyncio
    async def test_clear_job_history_validates_status_filter(self):
        import server

        result = await server.clear_job_history("running")

        parsed = json.loads(result)
        assert parsed["deleted_count"] == 0
        assert parsed["error"] == "status must be one of completed, failed, cancelled"

    @pytest.mark.asyncio
    async def test_clear_job_history_removes_terminal_rows(self):
        import server
        from unittest.mock import MagicMock, patch

        mock_db = MagicMock()
        mock_db.clear_job_history.return_value = ["job-2", "job-1"]
        mock_queue = MagicMock()
        mock_queue.clear_jobs.return_value = ["job-2"]

        with patch.object(server, "_get_job_db", return_value=mock_db), patch.object(
            server,
            "_get_job_queue",
            return_value=mock_queue,
        ):
            result = await server.clear_job_history()

        parsed = json.loads(result)
        assert parsed["deleted_count"] == 2
        assert parsed["deleted_job_ids"] == ["job-1", "job-2"]
        assert parsed["status_filter"] == "terminal"
        mock_db.clear_job_history.assert_called_once_with(
            statuses=("completed", "failed", "cancelled")
        )

    @pytest.mark.asyncio
    async def test_start_classify_job_persists_selection_profile(self):
        import server
        from unittest.mock import AsyncMock, MagicMock, patch

        mock_job = MagicMock(id="job-123", status=MagicMock(value="pending"))
        mock_queue = MagicMock()
        mock_queue.create_job.return_value = mock_job
        mock_queue.submit = AsyncMock(return_value=None)
        mock_db = MagicMock()

        with patch.object(server, "_get_job_queue", return_value=mock_queue), patch.object(
            server,
            "_get_job_db",
            return_value=mock_db,
        ):
            result = await server.start_classify_job(
                source="apple",
                source_path="앨범",
                selection_profile="landscape",
            )

        parsed = json.loads(result)
        assert parsed["job_id"] == "job-123"
        assert mock_job.request_options["selection_profile"] == "landscape"

    @pytest.mark.asyncio
    async def test_get_job_summary_merges_status_and_selection_profile(self):
        import server
        from jobs import Job, JobProgress, JobStatus
        from unittest.mock import MagicMock, patch

        job = Job(
            id="job-summary",
            source="apple",
            source_path="recent",
            request_options={"selection_profile": "person"},
            status=JobStatus.RUNNING,
            progress=JobProgress(total=10, completed=4, stage="vlm"),
        )
        mock_db = MagicMock()
        mock_db.load_job.return_value = job
        mock_db.load_photo_results.return_value = [{"photo_id": "p1"}, {"photo_id": "p2"}]
        mock_db.list_job_assets.return_value = {
            "p1": {"selected": True, "preview_path": "/tmp/p1.jpg"},
            "p2": {"selected": False, "preview_path": ""},
        }

        with patch.object(server, "_get_job_db", return_value=mock_db):
            result = await server.get_job_summary("job-summary")

        parsed = json.loads(result)
        assert parsed["request_options"]["selection_profile"] == "person"
        assert parsed["selected_count"] == 1

    @pytest.mark.asyncio
    async def test_curate_best_photos_can_write_selected_apple_photos_to_target_album(self):
        import server
        from jobs import Job
        from unittest.mock import AsyncMock, MagicMock, patch

        job = Job(id="job-curate-album", source="apple", source_path="")
        mock_db = MagicMock()
        mock_db.update_photo_review.return_value = {}
        mock_writer = MagicMock()
        mock_writer.add_photos_to_album.return_value = {
            "album": "잘나온사진1",
            "added": 2,
            "failed": 0,
            "errors": [],
        }

        results = [
            {"photo_id": "uuid-1", "quality_score": 90.0, "total_score": 88.0},
            {"photo_id": "uuid-2", "quality_score": 85.0, "total_score": 84.0},
            {"photo_id": "uuid-3", "quality_score": 20.0, "total_score": 40.0},
            {"photo_id": "uuid-4", "quality_score": 10.0, "total_score": 20.0},
            {"photo_id": "uuid-5", "quality_score": 5.0, "total_score": 10.0},
        ]

        with patch.object(
            server,
            "_run_sync_classification",
            AsyncMock(return_value=(job, mock_db, results)),
        ), patch.object(server, "_get_album_writer", return_value=mock_writer):
            result = await server.curate_best_photos(
                source="apple",
                limit=5,
                quality_top_percent=30,
                writeback_mode="album",
                target_album_name="잘나온사진1",
            )

        parsed = json.loads(result)
        assert parsed["selected_photo_ids"] == ["uuid-1", "uuid-2"]
        assert parsed["album_result"]["added"] == 2
        mock_writer.add_photos_to_album.assert_called_once_with(
            ["uuid-1", "uuid-2"],
            "잘나온사진1",
            "",
        )
