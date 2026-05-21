from __future__ import annotations

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
                    expected_tools=["photos_status"],
                    status=PASS,
                    evidence='{"status": "ok"}',
                ),
                SampleResult(
                    sample_id="apple-apr16to30-best-to-album",
                    title="작년 4월 16일~4월 30일 잘 나온 사진 앨범 저장",
                    user_prompt="iCloud 사진 중 작년 4월 16일~작년 4월30일 사진들만 잘 나온 사진들만 앨범을 따로 저장해 만들어줘.",
                    expected_tools=["photos_run", "photos_run"],
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
    assert "expected_tools: photos_run -> photos_run" in markdown
    assert "연결 상태 알려줘" in markdown
    assert "album cleanup is required after validation" in markdown