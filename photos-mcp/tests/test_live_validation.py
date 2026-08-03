from __future__ import annotations

from datetime import datetime
from pathlib import Path

from photos_mcp.live_validation import PASS, PARTIAL, SKIP, CheckResult, ReportSection, ValidationConfig, _json_preview, _prepare_local_workflow_sample, apple_items_are_photo_only, build_parser, derive_search_seed, format_progress_message, is_workflow_classify_start_payload, pick_library_candidates, pick_local_source_path, render_markdown_report, status_latest_matches_run, status_running_matches_run, wait_timeout_terminal_rounds


def test_pick_library_candidates_prefers_local_and_non_local_items() -> None:
    local_item, non_local_item = pick_library_candidates(
        [
            {"photo_id": "a", "local_path_available": False},
            {"photo_id": "b", "local_path_available": True, "analyze_recommended": False},
            {"photo_id": "c", "local_path_available": False},
            {"photo_id": "d", "local_path_available": True, "analyze_recommended": True},
        ]
    )

    assert local_item == {"photo_id": "d", "local_path_available": True, "analyze_recommended": True}
    assert non_local_item == {"photo_id": "a", "local_path_available": False}


def test_derive_search_seed_prefers_person_name_then_filename() -> None:
    seed = derive_search_seed(
        [
            {"filename": "ABCD1234.heic", "persons": ["_UNKNOWN_"]},
            {"filename": "photo-name.jpeg", "persons": ["라윤지"]},
        ]
    )

    assert seed == "라윤지"


def test_render_markdown_report_uses_checkbox_markers() -> None:
    check_a = CheckResult("a", "runtime ok", PASS, ["health ok"])
    check_b = CheckResult("b", "wait timeout", PARTIAL, ["local_download_timeout"], note="timed out as expected")
    check_c = CheckResult("c", "workflow suite", SKIP, note="not enabled")
    markdown = render_markdown_report(
        config=ValidationConfig(endpoint="http://127.0.0.1:18791/mcp"),
        sections=[ReportSection("runtime", [check_a, check_b, check_c])],
    )

    assert "# live validation report" in markdown
    assert "- [x] runtime ok" in markdown
    assert "- [-] wait timeout" in markdown
    assert "- [ ] workflow suite" in markdown
    assert "timed out as expected" in markdown


def test_pick_local_source_path_requires_existing_file(tmp_path: Path) -> None:
    sample = tmp_path / "sample.jpeg"
    sample.write_bytes(b"x")

    assert pick_local_source_path({"path": str(sample)}) == str(sample)
    assert pick_local_source_path({"path": str(tmp_path / "missing.jpeg")}) == ""
    assert pick_local_source_path({}) == ""


def test_status_run_helpers_require_exact_run_identity_and_state() -> None:
    assert status_running_matches_run(
        {"running": {"active": True, "count": 1, "current_run_id": "run-1"}},
        "run-1",
    )
    assert not status_running_matches_run({"running": {"active": False, "current_run_id": "run-1"}}, "run-1")
    assert status_latest_matches_run({"latest": {"run_id": "run-1", "status": "cancelled"}}, "run-1", status="cancelled")
    assert not status_latest_matches_run({"latest": {"run_id": "run-2", "status": "cancelled"}}, "run-1", status="cancelled")


def test_prepare_local_workflow_sample_uses_a_generated_non_personal_image() -> None:
    sample = _prepare_local_workflow_sample()
    try:
        sample_path = Path(sample["sample_path"])
        assert sample_path.is_file()
        assert sample_path.parent == Path(sample["input_dir"])
        assert sample_path.read_bytes().startswith(b"\x89PNG")
        assert sample_path.stat().st_size > 1024
    finally:
        sample["temp_root"].cleanup()


def test_apple_items_are_photo_only_rejects_video_candidates() -> None:
    assert apple_items_are_photo_only(
        [
            {"filename": "sample.jpeg", "media_type": "photo"},
            {"filename": "clip.mov", "media_type": "video"},
        ]
    ) is False

    assert apple_items_are_photo_only(
        [
            {"filename": "sample.jpeg", "media_type": "photo"},
            {"filename": "clip.mov"},
        ]
    ) is False

    assert apple_items_are_photo_only(
        [
            {"filename": "sample.jpeg", "media_type": "photo"},
            {"filename": "sample2.png", "media_type": "photo"},
        ]
    ) is True


def test_format_progress_message_uses_timestamp_prefix() -> None:
    message = format_progress_message("photos_run: waiting", now=datetime(2026, 5, 20, 8, 30, 45))

    assert message == "[live-validate 08:30:45] photos_run: waiting"


def test_build_parser_supports_quiet_progress_flag() -> None:
    args = build_parser().parse_args(["--quiet-progress"])

    assert args.quiet_progress is True


def test_workflow_classify_start_payload_accepts_pending_status() -> None:
    payload = {"job_id": "job-1", "run_id": "job-1", "status": "pending"}

    assert is_workflow_classify_start_payload(payload) is True


def test_wait_timeout_terminal_rounds_includes_probe_slack() -> None:
    assert wait_timeout_terminal_rounds(wait_timeout_seconds=6.0, poll_interval_seconds=1.0, minimum_rounds=8) == 13


def test_live_validation_preview_redacts_personal_photo_data() -> None:
    preview = _json_preview(
        {
            "status": "completed",
            "photo_id": "private-photo-id",
            "filename": "family-home.jpeg",
            "path": "/private/Photos/family-home.jpeg",
            "gps": {"lat": 37.5},
            "query": "가족 이름",
            "result": {"scene": "아이와 집"},
            "error_code": "local_download_timeout",
        },
        max_length=1000,
    )

    assert '"status": "completed"' in preview
    assert '"error_code": "local_download_timeout"' in preview
    assert "private-photo-id" not in preview
    assert "family-home" not in preview
    assert "/private" not in preview
    assert "가족 이름" not in preview
