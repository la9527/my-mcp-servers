from __future__ import annotations

from datetime import datetime
from pathlib import Path

from photos_mcp.live_validation import PASS, PARTIAL, SKIP, CheckResult, ReportSection, ValidationConfig, build_parser, derive_search_seed, format_progress_message, is_workflow_classify_start_payload, pick_library_candidates, pick_local_source_path, render_markdown_report, wait_timeout_terminal_rounds


def test_pick_library_candidates_prefers_local_and_non_local_items() -> None:
    local_item, non_local_item = pick_library_candidates(
        [
            {"photo_id": "a", "local_path_available": False},
            {"photo_id": "b", "local_path_available": True},
            {"photo_id": "c", "local_path_available": False},
        ]
    )

    assert local_item == {"photo_id": "b", "local_path_available": True}
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