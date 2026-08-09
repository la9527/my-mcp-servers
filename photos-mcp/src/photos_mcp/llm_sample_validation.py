from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Callable

from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

from photos_mcp.logging_setup import ToolLogContext, build_dated_log_path, configure_root_logging, log_context
from photos_mcp.infrastructure.runtime.paths import photos_mcp_logs_root


PASS = "pass"
PARTIAL = "partial"
FAIL = "fail"
SKIP = "skip"

SCREEN_CAPTURE_KEYWORDS = (
    "screenshot",
    "screen shot",
    "screen-shot",
    "screen_capture",
    "screen capture",
    "screenrecord",
    "screen recording",
    "screen_recording",
    "desktop screenshot",
    "phone screenshot",
    "mobile screenshot",
    "monitor screenshot",
    "browser window",
    "application window",
)

ProgressCallback = Callable[[ToolLogContext, str], None]


logger = logging.getLogger(__name__)

SCENARIO_STEP_TOTALS = {
    "status-summary": 1,
    "apple-apr16to30-best-to-album": 5,
    "local-samplephotos-best-to-album": 5,
    "apple-apr16to30-person-best-to-local-dir": 5,
}


@dataclass(frozen=True, slots=True)
class SelectionPolicy:
    selection_profile: str = "general"
    quality_top_percent: int = 30
    exclude_screenshots: bool = True


@dataclass(frozen=True, slots=True)
class SampleScenario:
    sample_id: str
    title: str
    user_prompt: str
    expected_tools: list[str]
    policy: SelectionPolicy
    executed_by_default: bool = True
    prerequisites: str = ""


@dataclass(slots=True)
class SampleResult:
    sample_id: str
    title: str
    user_prompt: str
    expected_tools: list[str]
    status: str = SKIP
    evidence: str = ""
    note: str = ""


@dataclass(slots=True)
class ValidationReport:
    endpoint: str
    sample_results: list[SampleResult] = field(default_factory=list)
    generated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass(slots=True)
class ValidationConfig:
    endpoint: str = "http://127.0.0.1:18791/mcp"
    report_path: str = ""
    log_path: str = ""
    show_progress: bool = True
    target_year: int = field(default_factory=lambda: datetime.now().year - 1)
    target_start_month: int = 4
    target_start_day: int = 16
    target_end_month: int = 4
    target_end_day: int = 30
    target_person: str = field(default_factory=lambda: os.getenv("PHOTOS_MCP_LLM_TARGET_PERSON", "").strip())
    samplephotos_dir: str = field(default_factory=lambda: os.getenv("PHOTOS_MCP_LLM_SAMPLEPHOTOS_DIR", str(Path.home() / "SamplePhotos")))
    local_output_dir: str = field(default_factory=lambda: os.getenv("PHOTOS_MCP_LLM_OUTPUT_DIR", str(Path.home() / "temp")))


def sample_catalog() -> list[SampleScenario]:
    return [
        SampleScenario(
            sample_id="status-summary",
            title="연결 상태 요약",
            user_prompt="photos-mcp 연결 상태와 현재 준비 상태를 알려줘.",
            expected_tools=["photos_query(action=status)"],
            policy=SelectionPolicy(selection_profile="general", quality_top_percent=0, exclude_screenshots=False),
            prerequisites="app 이 실행 중이고 MCP endpoint 가 열려 있어야 한다.",
        ),
        SampleScenario(
            sample_id="apple-apr16to30-best-to-album",
            title="작년 4월 16일~4월 30일 잘 나온 사진 앨범 저장",
            user_prompt="iCloud 사진 중 작년 4월 16일~작년 4월30일 사진들만 잘 나온 사진들만 앨범을 따로 저장해 만들어줘.",
            expected_tools=["photos_workflow(action=curate_to_album)", "photos_write(action=cleanup_album)"],
            policy=SelectionPolicy(selection_profile="general", quality_top_percent=30, exclude_screenshots=True),
            prerequisites="Apple Photos 에 작년 4월 16일~4월 30일 샘플이 있고 album write-back 이 허용되어야 한다.",
        ),
        SampleScenario(
            sample_id="local-samplephotos-best-to-album",
            title="SamplePhotos 잘 나온 사진 iCloud 앨범 저장",
            user_prompt="로컬 ~/SamplePhotos 디렉토리에 잘 나온 사진들을 골라서 iCloud 에 적절한 이름으로 앨범을 만들어 저장해줘.",
            expected_tools=["photos_select(action=select_best)", "photos_query(action=selected)", "photos_write(action=import_to_album)", "photos_write(action=cleanup_album)"],
            policy=SelectionPolicy(selection_profile="general", quality_top_percent=30, exclude_screenshots=True),
            prerequisites="~/SamplePhotos 가 존재하고 import/write-back 이 허용되어야 한다.",
        ),
        SampleScenario(
            sample_id="apple-apr16to30-person-best-to-local-dir",
            title="작년 4월 16일~4월 30일 특정인 잘 나온 사진 로컬 저장",
            user_prompt="iCloud 사진 중 작년 4월 16일~작년 4월30일 사진들 중 특정인의 사진만 뽑아서 잘 나온 사진들을 로컬의 특정(~/temp) 디렉토리에 저장해줘.",
            expected_tools=["photos_select(action=select_best_person)", "photos_write(action=export_selected)"],
            policy=SelectionPolicy(selection_profile="person", quality_top_percent=30, exclude_screenshots=True),
            prerequisites="Apple Photos person metadata 와 target person 값이 필요하다.",
        ),
    ]


def _status_marker(status: str) -> str:
    if status == PASS:
        return "[x]"
    if status == PARTIAL:
        return "[-]"
    if status == FAIL:
        return "[!]"
    return "[ ]"


def _json_preview(payload: Any, *, max_length: int = 240) -> str:
    try:
        text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    except TypeError:
        text = str(payload)
    if len(text) <= max_length:
        return text
    return f"{text[: max_length - 3]}..."


def _scenario_context(
    *,
    scenario_index: int,
    scenario_total: int,
    step_index: int,
    total_steps: int,
    stage_name: str,
    started_at: float,
) -> ToolLogContext:
    return ToolLogContext(
        tool_name="llm-samples",
        scenario_index=scenario_index,
        scenario_total=scenario_total,
        step_index=step_index,
        total_steps=total_steps,
        stage_name=stage_name,
        elapsed_seconds=time.perf_counter() - started_at,
    )


def _make_progress_callback(config: ValidationConfig) -> ProgressCallback | None:
    if not config.show_progress:
        return None

    def _progress(context: ToolLogContext, message: str) -> None:
        log_context(logger, logging.INFO, context, message)

    return _progress


def _emit_progress(
    progress: ProgressCallback | None,
    *,
    scenario_index: int,
    scenario_total: int,
    sample_id: str,
    step_index: int,
    stage_name: str,
    started_at: float,
    message: str,
) -> ToolLogContext:
    context = _scenario_context(
        scenario_index=scenario_index,
        scenario_total=scenario_total,
        step_index=step_index,
        total_steps=SCENARIO_STEP_TOTALS[sample_id],
        stage_name=stage_name,
        started_at=started_at,
    )
    if progress is not None:
        progress(context, message)
    return context


def render_markdown_report(report: ValidationReport) -> str:
    lines = [
        "# llm integration sample validation report",
        "",
        f"- generated_at: {report.generated_at}",
        f"- endpoint: {report.endpoint}",
        "",
        "## sample results",
        "",
    ]
    for result in report.sample_results:
        lines.append(f"- {_status_marker(result.status)} {result.title}")
        lines.append(f"  - sample_id: {result.sample_id}")
        lines.append(f"  - user_prompt: {result.user_prompt}")
        lines.append(f"  - expected_tools: {' -> '.join(result.expected_tools)}")
        if result.evidence:
            lines.append(f"  - evidence: {result.evidence}")
        if result.note:
            lines.append(f"  - note: {result.note}")
    return "\n".join(lines) + "\n"


async def _call_tool(
    session: ClientSession,
    name: str,
    arguments: dict[str, Any],
    *,
    context: ToolLogContext | None = None,
) -> Any:
    if context is not None:
        log_context(logger, logging.INFO, context, "call %s args=%s", name, _json_preview(arguments, max_length=400))
    else:
        logger.info("call %s args=%s", name, _json_preview(arguments, max_length=400))
    result = await session.call_tool(name, arguments)
    if result.structuredContent is not None:
        if context is not None:
            log_context(logger, logging.INFO, context, "done %s structured=true", name)
        else:
            logger.info("done %s structured=true", name)
        return result.structuredContent
    text_blocks = [block.text for block in result.content if getattr(block, "type", "") == "text"]
    if not text_blocks:
        if context is not None:
            log_context(logger, logging.INFO, context, "done %s empty-content=true", name)
        else:
            logger.info("done %s empty-content=true", name)
        return {}
    try:
        if context is not None:
            log_context(logger, logging.INFO, context, "done %s parse=json", name)
        else:
            logger.info("done %s parse=json", name)
        return json.loads(text_blocks[0])
    except json.JSONDecodeError:
        if context is not None:
            log_context(logger, logging.INFO, context, "done %s parse=text", name)
        else:
            logger.info("done %s parse=text", name)
        return {"text": text_blocks[0]}


async def _call_tool_with_test_approval(
    session: ClientSession,
    name: str,
    arguments: dict[str, Any],
    *,
    context: ToolLogContext | None = None,
) -> Any:
    plan = await _call_tool(session, name, arguments, context=context)
    if not isinstance(plan, dict) or plan.get("status") != "awaiting_approval":
        return plan

    approved_arguments = {
        "action": arguments["action"],
        "options": {
            **arguments.get("options", {}),
            "approval_token": plan["approval_token"],
        },
    }
    return await _call_tool(session, name, approved_arguments, context=context)


def _payload_has_error(payload: Any) -> bool:
    return isinstance(payload, dict) and bool(payload.get("error"))


def _target_date_bounds(config: ValidationConfig) -> tuple[str, str]:
    start = date(config.target_year, config.target_start_month, config.target_start_day)
    end = date(config.target_year, config.target_end_month, config.target_end_day)
    return start.isoformat(), end.isoformat()


def _album_name(config: ValidationConfig, scenario: SampleScenario) -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"photos-mcp llm {scenario.sample_id} {stamp}"


def _validate_single_album_writeback(curate_payload: Any, *, target_album_name: str) -> tuple[bool, str]:
    if not isinstance(curate_payload, dict):
        return False, "curate payload was not a structured object"

    public_action = str(curate_payload.get("action") or "")
    writeback_mode = str(curate_payload.get("writeback_mode") or "")
    if public_action != "curate_to_album" and writeback_mode != "album":
        return False, "single-album sample must return action=curate_to_album or writeback_mode=album"

    if str(curate_payload.get("target_album_name") or "") != target_album_name:
        return False, "single-album sample returned a mismatched target_album_name"

    raw_touched = curate_payload.get("touched_album_names")
    touched_album_names = [
        str(name).strip()
        for name in raw_touched
        if isinstance(name, str) and str(name).strip()
    ] if isinstance(raw_touched, list) else []
    if touched_album_names != [target_album_name]:
        return False, f"single-album sample touched unexpected albums: {touched_album_names or raw_touched!r}"

    if curate_payload.get("classification_album_created") is not False:
        return False, "single-album sample reported classification_album_created=true"

    album_result = curate_payload.get("album_result")
    if isinstance(album_result, dict):
        album_name = str(album_result.get("album") or "")
        if album_name and album_name != target_album_name:
            return False, "single-album sample returned an album_result.album that did not match target_album_name"

    return True, ""


def _looks_like_screen_capture(item: dict[str, Any]) -> bool:
    parts = [
        str(item.get("photo_id") or ""),
        str(item.get("source_photo_path") or ""),
        str(item.get("scene_description") or ""),
        str(item.get("note") or ""),
    ]
    combined = " ".join(parts).lower()
    return any(keyword in combined for keyword in SCREEN_CAPTURE_KEYWORDS)


def _selected_source_paths(items: list[dict[str, Any]], *, exclude_screenshots: bool) -> tuple[list[str], list[str]]:
    selected_paths: list[str] = []
    dropped_paths: list[str] = []
    for item in items:
        candidate = str(item.get("source_photo_path") or item.get("photo_id") or "")
        if not candidate:
            continue
        if exclude_screenshots and _looks_like_screen_capture(item):
            dropped_paths.append(candidate)
            continue
        selected_paths.append(candidate)
    return selected_paths, dropped_paths


def _scenario_output_dir(config: ValidationConfig, scenario: SampleScenario) -> str:
    root = Path(config.local_output_dir).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    return tempfile.mkdtemp(prefix=f"{scenario.sample_id}-", dir=str(root))


async def _discover_target_person(
    session: ClientSession,
    *,
    date_from: str,
    date_to: str,
    context: ToolLogContext | None = None,
) -> str:
    payload = await _call_tool(
        session,
        "photos_query",
        {
            "action": "list",
            "options": {
                "source": "apple",
                "date_from": date_from,
                "date_to": date_to,
                "limit": 50,
                "include_metadata": True,
            },
        },
        context=context,
    )
    items = payload.get("items") if isinstance(payload, dict) else []
    if not isinstance(items, list):
        return ""
    for item in items:
        if not isinstance(item, dict):
            continue
        persons = item.get("persons")
        if not isinstance(persons, list):
            metadata = item.get("metadata")
            persons = metadata.get("persons") if isinstance(metadata, dict) else []
        if not isinstance(persons, list):
            continue
        for person in persons:
            if isinstance(person, str) and person.strip():
                return person.strip()
    return ""


def _append_result(
    results: list[SampleResult],
    scenario: SampleScenario,
    *,
    status: str,
    evidence: Any = "",
    note: str = "",
) -> None:
    results.append(
        SampleResult(
            sample_id=scenario.sample_id,
            title=scenario.title,
            user_prompt=scenario.user_prompt,
            expected_tools=scenario.expected_tools,
            status=status,
            evidence=_json_preview(evidence) if evidence else "",
            note=note,
        )
    )


async def run_sample_validation(config: ValidationConfig) -> ValidationReport:
    progress = _make_progress_callback(config)
    results: list[SampleResult] = []
    scenarios = sample_catalog()
    scenario_map = {scenario.sample_id: scenario for scenario in scenarios}
    date_from, date_to = _target_date_bounds(config)
    scenario_total = len(scenarios)

    async with streamable_http_client(config.endpoint, terminate_on_close=False) as (read_stream, write_stream, _):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            status_scenario = scenario_map["status-summary"]
            status_started = time.perf_counter()
            status_context = _emit_progress(
                progress,
                scenario_index=1,
                scenario_total=scenario_total,
                sample_id=status_scenario.sample_id,
                step_index=1,
                stage_name="status.summary",
                started_at=status_started,
                message="requesting photos_query status summary",
            )
            summary_payload = await _call_tool(session, "photos_query", {"action": "status", "options": {"view": "summary"}}, context=status_context)
            summary_ok = isinstance(summary_payload, dict) and bool(summary_payload.get("transport"))
            _append_result(
                results,
                status_scenario,
                status=PASS if summary_ok else FAIL,
                evidence=summary_payload,
            )

            scenario = scenario_map["apple-apr16to30-best-to-album"]
            scenario_started = time.perf_counter()
            _emit_progress(
                progress,
                scenario_index=2,
                scenario_total=scenario_total,
                sample_id=scenario.sample_id,
                step_index=1,
                stage_name="scenario.start",
                started_at=scenario_started,
                message=f"prompt={scenario.sample_id}",
            )
            curate_context = _emit_progress(
                progress,
                scenario_index=2,
                scenario_total=scenario_total,
                sample_id=scenario.sample_id,
                step_index=2,
                stage_name="curate.load-apple",
                started_at=scenario_started,
                message=f"date_range={date_from}..{date_to}",
            )
            _emit_progress(
                progress,
                scenario_index=2,
                scenario_total=scenario_total,
                sample_id=scenario.sample_id,
                step_index=3,
                stage_name="curate.await-curate",
                started_at=scenario_started,
                message="waiting for photos_workflow curate_to_album response",
            )
            album_name = _album_name(config, scenario)
            curate_payload = await _call_tool_with_test_approval(
                session,
                "photos_workflow",
                {
                    "action": "curate_to_album",
                    "options": {
                        "source": "apple",
                        "date_from": date_from,
                        "date_to": date_to,
                        "limit": 20,
                        "selection_profile": scenario.policy.selection_profile,
                        "target_album_name": album_name,
                        "exclude_screenshots": scenario.policy.exclude_screenshots,
                    },
                },
                context=curate_context,
            )
            selected_count = int(curate_payload.get("selected_count") or 0) if isinstance(curate_payload, dict) else 0
            if _payload_has_error(curate_payload):
                _append_result(results, scenario, status=FAIL, evidence=curate_payload)
            elif selected_count < 1:
                _append_result(
                    results,
                    scenario,
                    status=SKIP,
                    evidence=curate_payload,
                    note=f"no selected Apple Photos candidates were found for {date_from}",
                )
            else:
                strict_ok, strict_note = _validate_single_album_writeback(
                    curate_payload,
                    target_album_name=album_name,
                )
                cleanup_context = _emit_progress(
                    progress,
                    scenario_index=2,
                    scenario_total=scenario_total,
                    sample_id=scenario.sample_id,
                    step_index=4,
                    stage_name="curate.cleanup-album",
                    started_at=scenario_started,
                    message=f"target_album_name={album_name}",
                )
                cleanup_payload = await _call_tool_with_test_approval(
                    session,
                    "photos_write",
                    {
                        "action": "cleanup_album",
                        "options": {"target_album_name": album_name},
                    },
                    context=cleanup_context,
                )
                cleanup_ok = isinstance(cleanup_payload, dict) and bool(cleanup_payload.get("deleted"))
                _emit_progress(
                    progress,
                    scenario_index=2,
                    scenario_total=scenario_total,
                    sample_id=scenario.sample_id,
                    step_index=5,
                    stage_name="result.record",
                    started_at=scenario_started,
                    message=f"selected_count={selected_count} cleanup_ok={cleanup_ok} strict_ok={strict_ok}",
                )
                note_parts = ["album is created for validation and should be cleaned up immediately"]
                if not strict_ok:
                    note_parts.insert(0, strict_note)
                _append_result(
                    results,
                    scenario,
                    status=PASS if cleanup_ok and strict_ok else PARTIAL if strict_ok else FAIL,
                    evidence={"curate": curate_payload, "cleanup": cleanup_payload},
                    note="; ".join(part for part in note_parts if part),
                )

            scenario = scenario_map["local-samplephotos-best-to-album"]
            sample_root = Path(config.samplephotos_dir).expanduser()
            scenario_started = time.perf_counter()
            _emit_progress(
                progress,
                scenario_index=3,
                scenario_total=scenario_total,
                sample_id=scenario.sample_id,
                step_index=1,
                stage_name="scenario.start",
                started_at=scenario_started,
                message=f"sample_root={sample_root}",
            )
            if not sample_root.is_dir():
                _emit_progress(
                    progress,
                    scenario_index=3,
                    scenario_total=scenario_total,
                    sample_id=scenario.sample_id,
                    step_index=5,
                    stage_name="result.record",
                    started_at=scenario_started,
                    message="sample photos directory is missing",
                )
                _append_result(
                    results,
                    scenario,
                    status=SKIP,
                    note=f"sample photos directory is missing: {sample_root}",
                )
            else:
                album_name = _album_name(config, scenario)
                curate_context = _emit_progress(
                    progress,
                    scenario_index=3,
                    scenario_total=scenario_total,
                    sample_id=scenario.sample_id,
                    step_index=2,
                    stage_name="curate.local-samplephotos",
                    started_at=scenario_started,
                    message="starting local sample curation",
                )
                curate_payload = await _call_tool(
                    session,
                    "photos_select",
                    {
                        "action": "select_best",
                        "options": {
                            "source": "local",
                            "source_path": str(sample_root),
                            "limit": 20,
                            "selection_profile": scenario.policy.selection_profile,
                            "exclude_screenshots": scenario.policy.exclude_screenshots,
                        },
                    },
                    context=curate_context,
                )
                run_id = str(curate_payload.get("run_id") or curate_payload.get("job_id") or "") if isinstance(curate_payload, dict) else ""
                if _payload_has_error(curate_payload) or not run_id:
                    _append_result(results, scenario, status=FAIL, evidence=curate_payload)
                else:
                    selected_context = _emit_progress(
                        progress,
                        scenario_index=3,
                        scenario_total=scenario_total,
                        sample_id=scenario.sample_id,
                        step_index=3,
                        stage_name="result.selected",
                        started_at=scenario_started,
                        message=f"run_id={run_id}",
                    )
                    selected_payload = await _call_tool(
                        session,
                        "photos_query",
                        {"action": "selected", "options": {"run_id": run_id, "top_n": 200}},
                        context=selected_context,
                    )
                    selected_items = selected_payload.get("items") if isinstance(selected_payload, dict) else []
                    selected_items = selected_items if isinstance(selected_items, list) else []
                    selected_paths, dropped_paths = _selected_source_paths(
                        selected_items,
                        exclude_screenshots=scenario.policy.exclude_screenshots,
                    )
                    if not selected_paths:
                        _append_result(
                            results,
                            scenario,
                            status=SKIP,
                            evidence={"curate": curate_payload, "selected": selected_payload},
                            note="no importable selected local photos were available after exclusions",
                        )
                    else:
                        import_context = _emit_progress(
                            progress,
                            scenario_index=3,
                            scenario_total=scenario_total,
                            sample_id=scenario.sample_id,
                            step_index=4,
                            stage_name="import.album",
                            started_at=scenario_started,
                            message=(
                                f"selected_paths={len(selected_paths)} dropped_screen_captures={len(dropped_paths)}"
                            ),
                        )
                        import_payload = await _call_tool_with_test_approval(
                            session,
                            "photos_write",
                            {
                                "action": "import_to_album",
                                "options": {
                                    "photo_paths": selected_paths,
                                    "target_album_name": album_name,
                                },
                            },
                            context=import_context,
                        )
                        cleanup_context = _emit_progress(
                            progress,
                            scenario_index=3,
                            scenario_total=scenario_total,
                            sample_id=scenario.sample_id,
                            step_index=5,
                            stage_name="cleanup.album",
                            started_at=scenario_started,
                            message=f"target_album_name={album_name}",
                        )
                        cleanup_payload = await _call_tool_with_test_approval(
                            session,
                            "photos_write",
                            {
                                "action": "cleanup_album",
                                "options": {"target_album_name": album_name},
                            },
                            context=cleanup_context,
                        )
                        imported_count = int(import_payload.get("imported") or 0) if isinstance(import_payload, dict) else 0
                        cleanup_ok = isinstance(cleanup_payload, dict) and bool(cleanup_payload.get("deleted"))
                        status = PASS if imported_count > 0 and cleanup_ok else PARTIAL if imported_count > 0 else FAIL
                        _append_result(
                            results,
                            scenario,
                            status=status,
                            evidence={
                                "curate": curate_payload,
                                "selected": selected_payload,
                                "import": import_payload,
                                "cleanup": cleanup_payload,
                            },
                            note=(
                                f"selected_paths={len(selected_paths)} dropped_screen_captures={len(dropped_paths)}"
                            ),
                        )

            scenario = scenario_map["apple-apr16to30-person-best-to-local-dir"]
            scenario_started = time.perf_counter()
            resolve_context = _emit_progress(
                progress,
                scenario_index=4,
                scenario_total=scenario_total,
                sample_id=scenario.sample_id,
                step_index=1,
                stage_name="person.resolve",
                started_at=scenario_started,
                message="discovering target person",
            )
            resolved_target_person = config.target_person or await _discover_target_person(
                session,
                date_from=date_from,
                date_to=date_to,
                context=resolve_context,
            )
            if not resolved_target_person:
                _emit_progress(
                    progress,
                    scenario_index=4,
                    scenario_total=scenario_total,
                    sample_id=scenario.sample_id,
                    step_index=5,
                    stage_name="result.record",
                    started_at=scenario_started,
                    message="target person unavailable",
                )
                _append_result(
                    results,
                    scenario,
                    status=SKIP,
                    note="target person is not configured and no person metadata was discoverable for the target date",
                )
            else:
                curate_context = _emit_progress(
                    progress,
                    scenario_index=4,
                    scenario_total=scenario_total,
                    sample_id=scenario.sample_id,
                    step_index=2,
                    stage_name="curate.load-apple",
                    started_at=scenario_started,
                    message=f"person={resolved_target_person}",
                )
                _emit_progress(
                    progress,
                    scenario_index=4,
                    scenario_total=scenario_total,
                    sample_id=scenario.sample_id,
                    step_index=3,
                    stage_name="curate.await-curate",
                    started_at=scenario_started,
                    message="waiting for photos_select select_best_person response",
                )
                curate_payload = await _call_tool(
                    session,
                    "photos_select",
                    {
                        "action": "select_best_person",
                        "options": {
                            "source": "apple",
                            "person": resolved_target_person,
                            "date_from": date_from,
                            "date_to": date_to,
                            "limit": 20,
                            "selection_profile": scenario.policy.selection_profile,
                            "exclude_screenshots": scenario.policy.exclude_screenshots,
                        },
                    },
                    context=curate_context,
                )
                run_id = str(curate_payload.get("run_id") or curate_payload.get("job_id") or "") if isinstance(curate_payload, dict) else ""
                selected_count = int(curate_payload.get("selected_count") or 0) if isinstance(curate_payload, dict) else 0
                if _payload_has_error(curate_payload) or not run_id:
                    _append_result(results, scenario, status=FAIL, evidence=curate_payload)
                elif selected_count < 1:
                    _append_result(
                        results,
                        scenario,
                        status=SKIP,
                        evidence=curate_payload,
                        note=f"no selected Apple Photos candidates were found for person={resolved_target_person!r}",
                    )
                else:
                    output_dir = _scenario_output_dir(config, scenario)
                    export_context = _emit_progress(
                        progress,
                        scenario_index=4,
                        scenario_total=scenario_total,
                        sample_id=scenario.sample_id,
                        step_index=4,
                        stage_name="export.artifacts",
                        started_at=scenario_started,
                        message=f"output_dir={output_dir}",
                    )
                    export_payload = await _call_tool_with_test_approval(
                        session,
                        "photos_write",
                        {
                            "action": "export_selected",
                            "options": {
                                "run_id": run_id,
                                "output_dir": output_dir,
                            },
                        },
                        context=export_context,
                    )
                    copied_count = int(export_payload.get("copied") or 0) if isinstance(export_payload, dict) else 0
                    _emit_progress(
                        progress,
                        scenario_index=4,
                        scenario_total=scenario_total,
                        sample_id=scenario.sample_id,
                        step_index=5,
                        stage_name="result.record",
                        started_at=scenario_started,
                        message=f"copied={copied_count} target_person={resolved_target_person}",
                    )
                    _append_result(
                        results,
                        scenario,
                        status=PASS if copied_count > 0 else FAIL,
                        evidence={"curate": curate_payload, "export": export_payload},
                        note=f"target_person={resolved_target_person} output_dir={output_dir}",
                    )

    return ValidationReport(endpoint=config.endpoint, sample_results=results)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate confirmed LLM prompt routes against the live photos-mcp MCP endpoint.")
    parser.add_argument("--endpoint", default="http://127.0.0.1:18791/mcp")
    parser.add_argument("--report-path", default="")
    parser.add_argument("--log-path", default="")
    parser.add_argument("--quiet-progress", action="store_true")
    parser.add_argument("--target-year", type=int, default=datetime.now().year - 1)
    parser.add_argument("--target-start-month", type=int, default=4)
    parser.add_argument("--target-start-day", type=int, default=16)
    parser.add_argument("--target-end-month", type=int, default=4)
    parser.add_argument("--target-end-day", type=int, default=30)
    parser.add_argument("--target-person", default=os.getenv("PHOTOS_MCP_LLM_TARGET_PERSON", ""))
    parser.add_argument("--samplephotos-dir", default=os.getenv("PHOTOS_MCP_LLM_SAMPLEPHOTOS_DIR", str(Path.home() / "SamplePhotos")))
    parser.add_argument("--local-output-dir", default=os.getenv("PHOTOS_MCP_LLM_OUTPUT_DIR", str(Path.home() / "temp")))
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    log_path = Path(args.log_path) if args.log_path else build_dated_log_path(photos_mcp_logs_root(), "llm-sample-validation.log")
    configure_root_logging(log_path, console=not args.quiet_progress)
    logger.info("[llm-samples] - start endpoint=%s report_path=%s log_path=%s", args.endpoint, args.report_path, log_path)
    report = asyncio.run(
        run_sample_validation(
            ValidationConfig(
                endpoint=args.endpoint,
                report_path=args.report_path,
                log_path=str(log_path),
                show_progress=not args.quiet_progress,
                target_year=args.target_year,
                target_start_month=args.target_start_month,
                target_start_day=args.target_start_day,
                target_end_month=args.target_end_month,
                target_end_day=args.target_end_day,
                target_person=args.target_person.strip(),
                samplephotos_dir=args.samplephotos_dir,
                local_output_dir=args.local_output_dir,
            )
        )
    )
    markdown = render_markdown_report(report)
    if args.report_path:
        Path(args.report_path).write_text(markdown, encoding="utf-8")
        logger.info("[llm-samples] - wrote markdown report path=%s", args.report_path)
    print(markdown, end="")
    logger.info("[llm-samples] - finished")
    return 0
