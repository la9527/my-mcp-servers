from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

import photos_mcp.llm_sample_validation as llm_sample_validation
from photos_mcp.llm_sample_validation import FAIL, PASS, SampleResult, ValidationReport, render_markdown_report, sample_catalog


def test_sample_catalog_centers_the_confirmed_llm_target_prompts() -> None:
    scenarios = sample_catalog()

    assert [scenario.sample_id for scenario in scenarios] == [
        "status-summary",
        "apple-apr16to30-best-to-album",
        "local-samplephotos-best-to-album",
        "apple-apr16to30-person-best-to-local-dir",
    ]
    assert all(scenario.executed_by_default for scenario in scenarios)
    assert scenarios[1].user_prompt == "iCloud 사진 중 작년 4월 16일~작년 4월30일 사진들만 잘 나온 사진들만 앨범을 따로 저장해 만들어줘."
    assert scenarios[2].user_prompt == "로컬 ~/SamplePhotos 디렉토리에 잘 나온 사진들을 골라서 iCloud 에 적절한 이름으로 앨범을 만들어 저장해줘."
    assert scenarios[3].user_prompt == "iCloud 사진 중 작년 4월 16일~작년 4월30일 사진들 중 특정인의 사진만 뽑아서 잘 나온 사진들을 로컬의 특정(~/temp) 디렉토리에 저장해줘."


def test_render_markdown_report_includes_prompt_route_and_status() -> None:
    markdown = render_markdown_report(
        ValidationReport(
            endpoint="http://127.0.0.1:18791/mcp",
            sample_results=[
                SampleResult(
                    sample_id="status-summary",
                    title="상태 요약",
                    user_prompt="연결 상태 알려줘",
                    expected_tools=["photos_query(action=status)"],
                    status=PASS,
                    evidence='{"status": "ok"}',
                ),
                SampleResult(
                    sample_id="apple-apr16to30-best-to-album",
                    title="작년 4월 16일~4월 30일 잘 나온 사진 앨범 저장",
                    user_prompt="iCloud 사진 중 작년 4월 16일~작년 4월30일 사진들만 잘 나온 사진들만 앨범을 따로 저장해 만들어줘.",
                    expected_tools=["photos_workflow(action=curate_to_album)", "photos_write(action=cleanup_album)"],
                    status=FAIL,
                    evidence='{"error": "cleanup failed"}',
                    note="album cleanup is required after validation",
                ),
            ],
        )
    )

    assert "# llm integration sample validation report" in markdown
    assert "- [x] 상태 요약" in markdown
    assert "- [!] 작년 4월 16일~4월 30일 잘 나온 사진 앨범 저장" in markdown
    assert "expected_tools: photos_workflow(action=curate_to_album) -> photos_write(action=cleanup_album)" in markdown
    assert "연결 상태 알려줘" in markdown
    assert "album cleanup is required after validation" in markdown


@pytest.mark.asyncio
async def test_run_sample_validation_fails_when_single_album_writeback_touches_extra_albums(monkeypatch, tmp_path) -> None:
    class DummyClientSession:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

        async def initialize(self) -> None:
            return None

    @asynccontextmanager
    async def fake_streamable_http_client(*_args, **_kwargs):
        yield object(), object(), None

    async def fake_call_tool(_session, name: str, arguments: dict, *, context=None):
        del context
        if name == "photos_query" and arguments.get("action") == "status":
            return {"transport": {"status": "ok"}}
        if name == "photos_workflow" and arguments.get("action") == "curate_to_album":
            album_name = str(arguments["options"]["target_album_name"])
            return {
                "job_id": "job-album",
                "selected_count": 3,
                "action": "curate_to_album",
                "target_album_name": album_name,
                "touched_album_names": [album_name, "AI 분류 - family"],
                "classification_album_created": True,
                "album_result": {"album": album_name, "added": 3, "failed": 0},
            }
        if name == "photos_write" and arguments.get("action") == "cleanup_album":
            return {"deleted": True, "album": arguments.get("options", {}).get("target_album_name", "")}
        raise AssertionError(f"unexpected tool call: {name} {arguments}")

    async def fake_discover_target_person(*_args, **_kwargs) -> str:
        return ""

    monkeypatch.setattr(llm_sample_validation, "streamable_http_client", fake_streamable_http_client)
    monkeypatch.setattr(llm_sample_validation, "ClientSession", DummyClientSession)
    monkeypatch.setattr(llm_sample_validation, "_call_tool", fake_call_tool)
    monkeypatch.setattr(llm_sample_validation, "_discover_target_person", fake_discover_target_person)

    report = await llm_sample_validation.run_sample_validation(
        llm_sample_validation.ValidationConfig(
            endpoint="http://127.0.0.1:18791/mcp",
            show_progress=False,
            samplephotos_dir=str(tmp_path / "missing-samplephotos"),
            local_output_dir=str(tmp_path / "out"),
        )
    )

    scenario = next(result for result in report.sample_results if result.sample_id == "apple-apr16to30-best-to-album")

    assert scenario.status == FAIL
    assert "touched unexpected albums" in scenario.note
    assert "AI 분류 - family" in scenario.note