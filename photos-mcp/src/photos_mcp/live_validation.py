from __future__ import annotations

import argparse
import asyncio
import json
import math
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable
from urllib.error import URLError
from urllib.request import urlopen

import anyio
import httpx
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client


PASS = "pass"
PARTIAL = "partial"
FAIL = "fail"
SKIP = "skip"

EXPECTED_TOOLS = ["photos_query", "photos_select", "photos_workflow", "photos_write"]
VIDEO_SUFFIXES = (".mov", ".mp4", ".m4v", ".avi", ".mkv")


@dataclass(slots=True)
class ValidationConfig:
    endpoint: str = "http://127.0.0.1:18791/mcp"
    health_url: str = "http://127.0.0.1:18791/health"
    capabilities_url: str = "http://127.0.0.1:18791/health/capabilities"
    bundle_path: str = ""
    wrapper_script: str = ""
    report_path: str = ""
    local_photo_id: str = ""
    non_local_photo_id: str = ""
    include_workflows: bool = False
    wait_timeout_seconds: float = 6.0
    wait_poll_interval_seconds: float = 1.0
    wait_poll_rounds: int = 8
    show_progress: bool = True


@dataclass(slots=True)
class CheckResult:
    key: str
    title: str
    status: str = SKIP
    evidence: list[str] = field(default_factory=list)
    note: str = ""

    def mark(self, status: str, *evidence: str, note: str = "") -> None:
        self.status = status
        self.evidence = [line for line in evidence if line]
        self.note = note


@dataclass(slots=True)
class ReportSection:
    title: str
    checks: list[CheckResult] = field(default_factory=list)


ProgressCallback = Callable[[str], None]


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


def pick_library_candidates(items: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    local_item = next(
        (
            item
            for item in items
            if isinstance(item, dict)
            and item.get("photo_id")
            and item.get("local_path_available") is True
        ),
        None,
    )
    non_local_item = next(
        (
            item
            for item in items
            if isinstance(item, dict)
            and item.get("photo_id")
            and item.get("local_path_available") is False
        ),
        None,
    )
    return local_item, non_local_item


def derive_search_seed(items: list[dict[str, Any]]) -> str:
    for item in items:
        persons = item.get("persons")
        if isinstance(persons, list):
            for person in persons:
                person_value = str(person or "").strip()
                if person_value and not person_value.startswith("_"):
                    return person_value

    for item in items:

        filename = str(item.get("filename") or "").strip()
        if filename:
            stem = Path(filename).stem
            if len(stem) >= 4:
                return stem[:4]
    return ""


def pick_local_source_path(item: dict[str, Any] | None) -> str:
    candidate = str((item or {}).get("path") or "").strip()
    if not candidate:
        return ""
    path = Path(candidate)
    return str(path) if path.is_file() else ""


def apple_items_are_photo_only(items: list[dict[str, Any]]) -> bool:
    if not items:
        return False

    for item in items:
        if not isinstance(item, dict):
            continue
        media_type = str(item.get("media_type") or "photo").strip().lower()
        filename = str(item.get("filename") or "").strip().lower()
        if media_type != "photo":
            return False
        if filename.endswith(VIDEO_SUFFIXES):
            return False
    return True


def is_workflow_classify_start_payload(payload: Any) -> bool:
    return (
        isinstance(payload, dict)
        and bool(payload.get("run_id") or payload.get("job_id"))
        and str(payload.get("status") or "") in {"pending", "running", "completed"}
    )


def wait_timeout_terminal_rounds(*, wait_timeout_seconds: float, poll_interval_seconds: float, minimum_rounds: int) -> int:
    interval = max(poll_interval_seconds, 0.1)
    timeout_window = max(wait_timeout_seconds, interval)
    # The runtime checks timeout after each probe iteration, so terminal timeout
    # can lag by roughly one extra timeout window when thumbnail/probe calls are slow.
    return max(minimum_rounds, int(math.ceil((wait_timeout_seconds + timeout_window + interval) / interval)))


def render_markdown_report(*, config: ValidationConfig, sections: list[ReportSection]) -> str:
    lines = [
        "# live validation report",
        "",
        f"- generated_at: {datetime.now(UTC).isoformat()}",
        f"- endpoint: {config.endpoint}",
        f"- health_url: {config.health_url}",
        f"- include_workflows: {str(config.include_workflows).lower()}",
        "",
    ]

    for section in sections:
        lines.append(f"## {section.title}")
        lines.append("")
        for check in section.checks:
            lines.append(f"- {_status_marker(check.status)} {check.title}")
            if check.note:
                lines.append(f"  - note: {check.note}")
            for evidence in check.evidence:
                lines.append(f"  - evidence: {evidence}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def format_progress_message(message: str, *, now: datetime | None = None) -> str:
    current = now or datetime.now()
    return f"[live-validate {current.strftime('%H:%M:%S')}] {message}"


def _make_progress_callback(config: ValidationConfig) -> ProgressCallback:
    def emit(message: str) -> None:
        if not config.show_progress:
            return
        print(format_progress_message(message), file=sys.stderr, flush=True)

    return emit


def _summary_progress_snapshot(payload: dict[str, Any]) -> str:
    status = str(payload.get("status") or "unknown")
    parts = [f"status={status}"]
    progress = payload.get("progress")
    if isinstance(progress, dict):
        completed = progress.get("completed")
        total = progress.get("total")
        if completed is not None or total is not None:
            parts.append(f"items={completed if completed is not None else '?'}" f"/{total if total is not None else '?'}")
        stage = str(progress.get("stage") or progress.get("label") or "").strip()
        if stage:
            parts.append(f"stage={stage}")
        percent = progress.get("percent")
        if percent is not None:
            parts.append(f"percent={percent}")
    return " ".join(parts)


def _mark_check(
    check: CheckResult,
    status: str,
    *evidence: str,
    note: str = "",
    progress: ProgressCallback | None = None,
) -> None:
    check.mark(status, *evidence, note=note)
    if progress is None:
        return
    suffix = f" ({note})" if note else ""
    progress(f"{_status_marker(status)} {check.title}{suffix}")


async def _fetch_json(url: str) -> dict[str, Any]:
    def _read() -> dict[str, Any]:
        with urlopen(url) as response:  # noqa: S310
            return json.loads(response.read().decode("utf-8"))

    return await asyncio.to_thread(_read)


def _check_bundle_health(bundle_path: str) -> tuple[bool, str]:
    exec_path = Path(bundle_path) / "Contents" / "MacOS" / "PhotosMcp"
    if not exec_path.exists():
        return False, f"missing executable: {exec_path}"

    completed = subprocess.run(
        [str(exec_path), "--health"],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.strip() or completed.stdout.strip()
        return False, stderr or f"bundle health failed with code {completed.returncode}"
    return True, (completed.stdout.strip() or completed.stderr.strip() or "bundle --health ok")


def _run_wrapper_script(wrapper_script: str) -> tuple[bool, str]:
    completed = subprocess.run(
        [wrapper_script],
        capture_output=True,
        text=True,
        check=False,
    )
    output = completed.stdout.strip() or completed.stderr.strip()
    if completed.returncode != 0:
        return False, output or f"wrapper exited with code {completed.returncode}"
    return True, output or "wrapper launch ok"


async def _call_tool(session: ClientSession, name: str, arguments: dict[str, Any]) -> Any:
    result = await session.call_tool(name, arguments)
    if result.structuredContent is not None:
        return result.structuredContent
    text_blocks = [block.text for block in result.content if getattr(block, "type", "") == "text"]
    if not text_blocks:
        return {}
    try:
        return json.loads(text_blocks[0])
    except json.JSONDecodeError:
        return {"text": text_blocks[0]}


async def _call_tool_with_test_approval(
    session: ClientSession,
    name: str,
    arguments: dict[str, Any],
) -> Any:
    plan = await _call_tool(session, name, arguments)
    if not isinstance(plan, dict) or plan.get("status") != "awaiting_approval":
        return plan

    approved_arguments = {
        "action": arguments["action"],
        "options": {
            **arguments.get("options", {}),
            "approval_token": plan["approval_token"],
        },
    }
    return await _call_tool(session, name, approved_arguments)


async def _wait_for_terminal_summary(
    session: ClientSession,
    *,
    run_id: str,
    rounds: int,
    poll_interval_seconds: float,
    progress: ProgressCallback | None = None,
    label: str = "",
) -> dict[str, Any]:
    summary_payload: dict[str, Any] = {}
    last_snapshot = ""
    for attempt in range(rounds):
        summary_payload = await _call_tool(session, "photos_query", {"action": "result_summary", "options": {"run_id": run_id}})
        snapshot = _summary_progress_snapshot(summary_payload)
        if progress is not None and snapshot != last_snapshot:
            prefix = f"{label}: " if label else ""
            progress(f"{prefix}poll {attempt + 1}/{rounds} {snapshot}")
            last_snapshot = snapshot
        if str(summary_payload.get("status") or "") in {"completed", "failed", "cancelled"}:
            return summary_payload
        await anyio.sleep(poll_interval_seconds)
    return summary_payload


async def _wait_for_summary_predicate(
    session: ClientSession,
    *,
    run_id: str,
    rounds: int,
    poll_interval_seconds: float,
    predicate,
    progress: ProgressCallback | None = None,
    label: str = "",
) -> dict[str, Any]:
    summary_payload: dict[str, Any] = {}
    last_snapshot = ""
    for attempt in range(rounds):
        summary_payload = await _call_tool(session, "photos_query", {"action": "result_summary", "options": {"run_id": run_id}})
        snapshot = _summary_progress_snapshot(summary_payload)
        if progress is not None and snapshot != last_snapshot:
            prefix = f"{label}: " if label else ""
            progress(f"{prefix}poll {attempt + 1}/{rounds} {snapshot}")
            last_snapshot = snapshot
        if predicate(summary_payload):
            return summary_payload
        await anyio.sleep(poll_interval_seconds)
    return summary_payload


async def _prepare_local_workflow_sample(local_item: dict[str, Any] | None) -> dict[str, Any] | None:
    source_path = pick_local_source_path(local_item)
    if not source_path:
        return None

    temp_root = tempfile.TemporaryDirectory(prefix="photos-mcp-live-")
    root = Path(temp_root.name)
    input_dir = root / "input"
    output_dir = root / "organized"
    input_dir.mkdir(parents=True, exist_ok=True)

    source = Path(source_path)
    sample_path = input_dir / source.name
    await asyncio.to_thread(shutil.copy2, source, sample_path)

    return {
        "temp_root": temp_root,
        "root": root,
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "sample_path": str(sample_path),
    }


async def run_live_validation(config: ValidationConfig) -> list[ReportSection]:
    progress = _make_progress_callback(config)

    def set_check(check: CheckResult, status: str, *evidence: str, note: str = "") -> None:
        _mark_check(check, status, *evidence, note=note, progress=progress)

    progress(
        "starting validation "
        f"endpoint={config.endpoint} include_workflows={str(config.include_workflows).lower()}"
    )
    runtime_section = ReportSection(
        title="runtime / transport",
        checks=[
            CheckResult("bundle-health", "installed bundle --health responds"),
            CheckResult("wrapper-launch", "wrapper script launches the live app"),
            CheckResult("health", "/health responds with ok transport"),
            CheckResult("capabilities", "/health/capabilities exposes checks"),
            CheckResult("tool-list", "MCP tool inventory exposes only 4 facade tools"),
        ],
    )
    status_section = ReportSection(
        title="photos_query/status",
        checks=[
            CheckResult("status-summary", "summary view returns transport, capabilities, running, latest"),
            CheckResult("status-checks", "checks view returns preflight entries"),
            CheckResult("status-running", "running view returns running/latest shape"),
            CheckResult("status-latest", "latest view returns latest shape"),
        ],
    )
    library_section = ReportSection(
        title="photos_query/library",
        checks=[
            CheckResult("library-list", "list action returns items with source aliases"),
            CheckResult("library-photo-only", "apple list excludes video assets from candidates"),
            CheckResult("library-ready-only", "ready_only keeps only analyze-ready items"),
            CheckResult("library-search", "search action returns stable shape"),
            CheckResult("library-inspect", "inspect action returns metadata/thumbnail shape"),
            CheckResult("library-guidance", "library item guidance fields are populated consistently"),
        ],
    )
    run_section = ReportSection(
        title="photos_select/photos_write",
        checks=[
            CheckResult("analyze-success", "local analyze succeeds immediately"),
            CheckResult("analyze-blocked", "non-local analyze without wait returns structured blocked payload"),
            CheckResult("wait-start", "wait_for_local starts a synthetic waiting run"),
            CheckResult("wait-cancel", "synthetic wait run can be cancelled"),
            CheckResult("wait-timeout", "synthetic wait run can time out with structured failure"),
            CheckResult("workflows", "background workflow intents are covered when explicitly enabled"),
        ],
    )
    result_section = ReportSection(
        title="photos_query/result",
        checks=[
            CheckResult("result-wait-summary", "synthetic wait summary exposes progress fields"),
            CheckResult("result-wait-result-pending", "synthetic wait result stays unavailable before terminal completion"),
            CheckResult("result-wait-cancel", "synthetic wait cancel transitions to cancelled summary"),
            CheckResult("result-vendor", "vendor-run result actions are available when workflow validation is enabled"),
        ],
    )
    sections = [runtime_section, status_section, library_section, run_section, result_section]

    if config.bundle_path:
        progress("runtime / transport: checking installed bundle health")
        bundle_ok, bundle_output = await asyncio.to_thread(_check_bundle_health, config.bundle_path)
        set_check(runtime_section.checks[0], PASS if bundle_ok else FAIL, bundle_output)
    else:
        set_check(runtime_section.checks[0], SKIP, note="bundle_path not provided")

    if config.wrapper_script:
        progress("runtime / transport: launching wrapper script")
        wrapper_ok, wrapper_output = await asyncio.to_thread(_run_wrapper_script, config.wrapper_script)
        set_check(runtime_section.checks[1], PASS if wrapper_ok else FAIL, wrapper_output)
    else:
        set_check(runtime_section.checks[1], SKIP, note="wrapper_script not provided")

    try:
        progress("runtime / transport: fetching /health")
        health_payload = await _fetch_json(config.health_url)
        set_check(
            runtime_section.checks[2],
            PASS if health_payload.get("status") == "ok" else PARTIAL,
            _json_preview({
                "status": health_payload.get("status"),
                "daemon_status": health_payload.get("daemon_status"),
                "preflight_status": health_payload.get("preflight_status"),
            }),
        )
    except (URLError, json.JSONDecodeError) as exc:
        set_check(runtime_section.checks[2], FAIL, str(exc))
        set_check(runtime_section.checks[3], FAIL, note="capabilities skipped because health fetch failed")
        for section in sections[1:]:
            for check in section.checks:
                if check.status == SKIP:
                    check.note = "skipped because live endpoint was unavailable"
        return sections

    try:
        progress("runtime / transport: fetching /health/capabilities")
        capabilities_payload = await _fetch_json(config.capabilities_url)
        checks = capabilities_payload.get("checks") or []
        set_check(
            runtime_section.checks[3],
            PASS if isinstance(checks, list) and bool(checks) else PARTIAL,
            _json_preview({"status": capabilities_payload.get("status"), "check_count": len(checks)}),
        )
    except (URLError, json.JSONDecodeError) as exc:
        set_check(runtime_section.checks[3], FAIL, str(exc))

    http_client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=600.0))
    async with http_client:
        progress("runtime / transport: opening MCP session")
        async with streamable_http_client(
            config.endpoint,
            http_client=http_client,
            terminate_on_close=False,
        ) as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                progress("runtime / transport: listing tools")
                tools_result = await session.list_tools()
                tool_names = sorted(tool.name for tool in tools_result.tools)
                set_check(
                    runtime_section.checks[4],
                    PASS if tool_names == EXPECTED_TOOLS else FAIL,
                    _json_preview(tool_names),
                )

                progress("photos_query: fetching status summary/checks/running/latest")
                status_summary = await _call_tool(session, "photos_query", {"action": "status", "options": {}})
                status_checks = await _call_tool(session, "photos_query", {"action": "status", "options": {"view": "checks"}})
                status_running = await _call_tool(session, "photos_query", {"action": "status", "options": {"view": "running"}})
                status_latest = await _call_tool(session, "photos_query", {"action": "status", "options": {"view": "latest"}})

                set_check(
                    status_section.checks[0],
                    PASS if all(key in status_summary for key in ["transport", "capabilities", "running", "latest"]) else FAIL,
                    _json_preview(status_summary),
                )
                set_check(
                    status_section.checks[1],
                    PASS if isinstance((status_checks.get("capabilities") or {}).get("checks"), list) else FAIL,
                    _json_preview(status_checks),
                )
                set_check(
                    status_section.checks[2],
                    PASS if all(key in status_running for key in ["running", "latest"]) else FAIL,
                    _json_preview(status_running),
                )
                set_check(
                    status_section.checks[3],
                    PASS if "latest" in status_latest else FAIL,
                    _json_preview(status_latest),
                )

                progress("photos_query: listing candidates")
                library_list = await _call_tool(
                    session,
                    "photos_query",
                    {"action": "list", "options": {"source": "apple", "limit": 20}},
                )
                items = library_list.get("items") if isinstance(library_list, dict) else []
                items = items if isinstance(items, list) else []
                local_item, non_local_item = pick_library_candidates(items)
                search_seed = derive_search_seed(items)

                set_check(
                    library_section.checks[0],
                    PASS if items and all("photo_id" in item for item in items if isinstance(item, dict)) else FAIL,
                    _json_preview(library_list),
                )
                set_check(
                    library_section.checks[1],
                    PASS if apple_items_are_photo_only(items) else FAIL,
                    _json_preview(items[:5]),
                )

                progress("photos_query: checking ready_only")
                ready_only_payload = await _call_tool(
                    session,
                    "photos_query",
                    {"action": "ready_only", "options": {"source": "apple", "limit": 20}},
                )
                ready_items = ready_only_payload.get("items") if isinstance(ready_only_payload, dict) else []
                ready_items = ready_items if isinstance(ready_items, list) else []
                ready_ok = bool(ready_items) and all(item.get("local_path_available") is True for item in ready_items if isinstance(item, dict))
                set_check(
                    library_section.checks[2],
                    PASS if ready_ok else PARTIAL,
                    _json_preview(ready_only_payload),
                    note="partial when no analyze-ready items are currently available",
                )

                if search_seed:
                    progress(f"photos_query: searching query={search_seed}")
                    search_payload = await _call_tool(
                        session,
                        "photos_query",
                        {"action": "search", "options": {"source": "apple", "query": search_seed, "limit": 10}},
                    )
                    search_ok = isinstance(search_payload, dict) and search_payload.get("action") == "search" and "items" in search_payload
                    set_check(library_section.checks[3], PASS if search_ok else FAIL, _json_preview(search_payload))
                else:
                    set_check(library_section.checks[3], SKIP, note="could not derive a stable search seed from current library items")

                inspect_target = config.local_photo_id or (local_item or {}).get("photo_id") or (items[0].get("photo_id") if items else "")
                if inspect_target:
                    progress(f"photos_query: inspecting photo_id={inspect_target}")
                    inspect_payload = await _call_tool(
                        session,
                        "photos_query",
                        {
                            "action": "inspect",
                            "options": {
                                "source": "apple",
                                "photo_id": inspect_target,
                                "include_metadata": True,
                                "include_thumbnail": bool(local_item),
                            },
                        },
                    )
                    inspect_item = inspect_payload.get("item") if isinstance(inspect_payload, dict) else {}
                    inspect_ok = isinstance(inspect_item, dict) and "metadata" in inspect_item
                    set_check(library_section.checks[4], PASS if inspect_ok else FAIL, _json_preview(inspect_payload))
                else:
                    set_check(library_section.checks[4], SKIP, note="no inspect target available from current library list")

                guidance_ok = bool(items) and all(
                    ("local_path_available" in item and "analyze_recommended" in item and "recommended_next_action" in item)
                    for item in items
                    if isinstance(item, dict)
                )
                set_check(library_section.checks[5], PASS if guidance_ok else FAIL, _json_preview(items[:2]))

                local_photo_id = config.local_photo_id or ((local_item or {}).get("photo_id") or "")
                non_local_photo_id = config.non_local_photo_id or ((non_local_item or {}).get("photo_id") or "")
                workflow_sample = await _prepare_local_workflow_sample(local_item)

                if local_photo_id:
                    progress(f"photos_select: analyzing local candidate photo_id={local_photo_id}")
                    analyze_success_payload = await _call_tool(
                        session,
                        "photos_select",
                        {"action": "analyze_photo", "options": {"source": "apple", "photo_id": local_photo_id}},
                    )
                    analyze_success_ok = (
                        isinstance(analyze_success_payload, dict)
                        and analyze_success_payload.get("status") == "completed"
                        and analyze_success_payload.get("result_available") is True
                    )
                    set_check(run_section.checks[0], PASS if analyze_success_ok else FAIL, _json_preview(analyze_success_payload))
                else:
                    set_check(run_section.checks[0], SKIP, note="no local photo candidate was discovered")

                wait_summary_payload: dict[str, Any] | None = None
                wait_result_payload: dict[str, Any] | None = None
                wait_cancel_summary_payload: dict[str, Any] | None = None

                if non_local_photo_id:
                    progress(f"photos_select: checking blocked non-local analyze photo_id={non_local_photo_id}")
                    blocked_payload = await _call_tool(
                        session,
                        "photos_select",
                        {"action": "analyze_photo", "options": {"source": "apple", "photo_id": non_local_photo_id}},
                    )
                    blocked_ok = (
                        isinstance(blocked_payload, dict)
                        and blocked_payload.get("status") == "blocked"
                        and bool(blocked_payload.get("error_code"))
                    )
                    set_check(run_section.checks[1], PASS if blocked_ok else FAIL, _json_preview(blocked_payload))

                    progress("photos_select: starting synthetic wait_for_local timeout probe")
                    timeout_run_payload = await _call_tool(
                        session,
                        "photos_select",
                        {
                            "action": "analyze_photo",
                            "options": {
                                "source": "apple",
                                "photo_id": non_local_photo_id,
                                "wait_for_local": True,
                                "wait_timeout_seconds": config.wait_timeout_seconds,
                                "wait_poll_interval_seconds": config.wait_poll_interval_seconds,
                            },
                        },
                    )
                    timeout_run_id = str(timeout_run_payload.get("run_id") or "")
                    wait_start_ok = (
                        isinstance(timeout_run_payload, dict)
                        and timeout_run_payload.get("status") == "running"
                        and timeout_run_payload.get("wait_status") == "waiting_for_local_download"
                    )
                    set_check(run_section.checks[2], PASS if wait_start_ok else FAIL, _json_preview(timeout_run_payload))

                    wait_pending_result = await _call_tool(
                        session,
                        "photos_query",
                        {"action": "result_detail", "options": {"run_id": timeout_run_id}},
                    )
                    pending_ok = isinstance(wait_pending_result, dict) and wait_pending_result.get("result_available") is False
                    set_check(result_section.checks[1], PASS if pending_ok else FAIL, _json_preview(wait_pending_result))

                    wait_summary_payload = await _wait_for_summary_predicate(
                        session,
                        run_id=timeout_run_id,
                        rounds=max(config.wait_poll_rounds, 3),
                        poll_interval_seconds=config.wait_poll_interval_seconds,
                        predicate=lambda payload: isinstance(payload.get("progress"), dict)
                        or str(payload.get("status") or "") in {"completed", "failed", "cancelled"},
                        progress=progress,
                        label=f"wait_for_local run_id={timeout_run_id}",
                    )
                    summary_ok = isinstance(wait_summary_payload, dict) and (
                        bool(wait_summary_payload.get("progress"))
                        or str(wait_summary_payload.get("status") or "") in {"completed", "failed", "cancelled"}
                    )
                    set_check(result_section.checks[0], PASS if summary_ok else FAIL, _json_preview(wait_summary_payload))

                    timeout_summary = await _wait_for_terminal_summary(
                        session,
                        run_id=timeout_run_id,
                        rounds=wait_timeout_terminal_rounds(
                            wait_timeout_seconds=config.wait_timeout_seconds,
                            poll_interval_seconds=config.wait_poll_interval_seconds,
                            minimum_rounds=config.wait_poll_rounds,
                        ),
                        poll_interval_seconds=config.wait_poll_interval_seconds,
                        progress=progress,
                        label=f"wait_for_local timeout run_id={timeout_run_id}",
                    )
                    wait_result_payload = await _call_tool(
                        session,
                        "photos_query",
                        {"action": "result_detail", "options": {"run_id": timeout_run_id}},
                    )
                    timeout_ok = timeout_summary.get("status") in {"failed", "completed"}
                    set_check(
                        run_section.checks[4],
                        PARTIAL if timeout_ok and timeout_summary.get("status") == "completed" else PASS if timeout_ok and timeout_summary.get("error_code") == "local_download_timeout" else FAIL,
                        _json_preview(timeout_summary),
                        note="completed is possible if the asset downloads while polling",
                    )

                    progress("photos_select: starting synthetic wait_for_local cancel probe")
                    cancel_run_payload = await _call_tool(
                        session,
                        "photos_select",
                        {
                            "action": "analyze_photo",
                            "options": {
                                "source": "apple",
                                "photo_id": non_local_photo_id,
                                "wait_for_local": True,
                                "wait_timeout_seconds": max(config.wait_timeout_seconds, 30.0),
                                "wait_poll_interval_seconds": max(config.wait_poll_interval_seconds, 3.0),
                            },
                        },
                    )
                    cancel_run_id = str(cancel_run_payload.get("run_id") or "")
                    cancel_summary_before = await _wait_for_summary_predicate(
                        session,
                        run_id=cancel_run_id,
                        rounds=max(config.wait_poll_rounds, 3),
                        poll_interval_seconds=config.wait_poll_interval_seconds,
                        predicate=lambda payload: isinstance(payload.get("progress"), dict)
                        or str(payload.get("status") or "") in {"completed", "failed", "cancelled"},
                        progress=progress,
                        label=f"wait_for_local cancel-prep run_id={cancel_run_id}",
                    )
                    cancel_payload = await _call_tool(
                        session,
                        "photos_query",
                        {"action": "cancel", "options": {"run_id": cancel_run_id}},
                    )
                    wait_cancel_summary_payload = await _wait_for_summary_predicate(
                        session,
                        run_id=cancel_run_id,
                        rounds=max(config.wait_poll_rounds, 3),
                        poll_interval_seconds=config.wait_poll_interval_seconds,
                        predicate=lambda payload: str(payload.get("status") or "") == "cancelled",
                        progress=progress,
                        label=f"wait_for_local cancel run_id={cancel_run_id}",
                    )
                    cancel_ok = (
                        isinstance(cancel_payload, dict)
                        and cancel_payload.get("action") == "cancel"
                        and str(cancel_summary_before.get("status") or "") == "running"
                        and wait_cancel_summary_payload.get("status") == "cancelled"
                    )
                    set_check(run_section.checks[3], PASS if cancel_ok else FAIL, _json_preview(wait_cancel_summary_payload))
                    set_check(result_section.checks[2], PASS if cancel_ok else FAIL, _json_preview(cancel_payload))
                    set_check(result_section.checks[3], SKIP, note="vendor-run result validation requires --include-workflows")
                else:
                    set_check(run_section.checks[1], SKIP, note="no non-local photo candidate was discovered")
                    set_check(run_section.checks[2], SKIP, note="no non-local photo candidate was discovered")
                    set_check(run_section.checks[3], SKIP, note="no non-local photo candidate was discovered")
                    set_check(run_section.checks[4], SKIP, note="no non-local photo candidate was discovered")
                    set_check(result_section.checks[0], SKIP, note="no synthetic wait run was started")
                    set_check(result_section.checks[1], SKIP, note="no synthetic wait run was started")
                    set_check(result_section.checks[2], SKIP, note="no synthetic wait run was started")
                    set_check(result_section.checks[3], SKIP, note="vendor-run result validation requires --include-workflows")

                if config.include_workflows:
                    if workflow_sample is None:
                        set_check(run_section.checks[5], PARTIAL, note="no local sample file was available for safe workflow validation")
                        set_check(result_section.checks[3], PARTIAL, note="vendor-run validation skipped because no local sample file was available")
                    else:
                        try:
                            workflow_poll_rounds = max(config.wait_poll_rounds, 20)
                            workflow_poll_interval_seconds = max(config.wait_poll_interval_seconds, 1.0)
                            progress(f"photos_select workflows: starting classify source_path={workflow_sample['input_dir']}")
                            classify_payload = await _call_tool(
                                session,
                                "photos_select",
                                {
                                    "action": "classify_range",
                                    "options": {
                                        "source": "local",
                                        "source_path": workflow_sample["input_dir"],
                                        "limit": 5,
                                        "selection_profile": "general",
                                    },
                                },
                            )
                            classify_run_id = str(classify_payload.get("run_id") or classify_payload.get("job_id") or "")
                            classify_summary = await _wait_for_terminal_summary(
                                session,
                                run_id=classify_run_id,
                                rounds=workflow_poll_rounds,
                                poll_interval_seconds=workflow_poll_interval_seconds,
                                progress=progress,
                                label=f"workflow classify run_id={classify_run_id}",
                            )
                            progress(f"photos_query workflows: fetching result artifacts for run_id={classify_run_id}")
                            classify_result = await _call_tool(
                                session,
                                "photos_query",
                                {"action": "result_detail", "options": {"run_id": classify_run_id, "top_n": 10}},
                            )
                            classify_selected = await _call_tool(
                                session,
                                "photos_query",
                                {"action": "selected", "options": {"run_id": classify_run_id, "top_n": 10}},
                            )
                            classify_artifacts = await _call_tool(
                                session,
                                "photos_query",
                                {"action": "artifacts", "options": {"run_id": classify_run_id}},
                            )
                            progress(f"photos_write workflows: organizing run_id={classify_run_id}")
                            organize_payload = await _call_tool_with_test_approval(
                                session,
                                "photos_write",
                                {
                                    "action": "organize_by_category",
                                    "options": {
                                        "run_id": classify_run_id,
                                        "folder": workflow_sample["output_dir"],
                                    },
                                },
                            )
                            progress("photos_select workflows: running curate(review)")
                            curate_payload = await _call_tool(
                                session,
                                "photos_select",
                                {
                                    "action": "select_best",
                                    "options": {
                                        "source": "local",
                                        "source_path": workflow_sample["input_dir"],
                                        "limit": 5,
                                        "selection_profile": "general",
                                    },
                                },
                            )
                            progress("photos_write workflows: running import no-op")
                            import_payload = await _call_tool_with_test_approval(
                                session,
                                "photos_write",
                                {
                                    "action": "import_to_album",
                                    "options": {
                                        "photo_paths": [],
                                        "target_album_name": "photos-mcp live validation noop",
                                    },
                                },
                            )

                            classify_started_ok = is_workflow_classify_start_payload(classify_payload)
                            classify_observable_ok = (
                                isinstance(classify_summary, dict)
                                and int(classify_summary.get("photo_count") or 0) >= 1
                                and bool(classify_summary.get("preview_path"))
                                and isinstance(classify_summary.get("result_summary"), dict)
                            )
                            vendor_result_ok = (
                                isinstance(classify_summary, dict)
                                and classify_summary.get("action") == "summary"
                                and classify_observable_ok
                                and isinstance(classify_result.get("items"), list)
                                and isinstance(classify_selected.get("items"), list)
                                and classify_artifacts.get("action") == "artifacts"
                            )
                            organize_ok = (
                                isinstance(organize_payload, dict)
                                and organize_payload.get("status") == "completed"
                                and str(organize_payload.get("output_dir") or "") == workflow_sample["output_dir"]
                                and int(organize_payload.get("copied") or 0) >= 1
                            )
                            curate_ok = (
                                isinstance(curate_payload, dict)
                                and not curate_payload.get("error")
                                and curate_payload.get("action") == "select_best"
                                and bool(curate_payload.get("job_id"))
                            )
                            import_ok = (
                                isinstance(import_payload, dict)
                                and import_payload.get("status") == "completed"
                                and int(import_payload.get("imported") or 0) == 0
                            )
                            workflow_ok = classify_started_ok and classify_observable_ok and organize_ok and curate_ok and import_ok
                            workflow_status = PASS if workflow_ok and classify_summary.get("status") == "completed" else PARTIAL if workflow_ok else FAIL
                            set_check(
                                run_section.checks[5],
                                workflow_status,
                                _json_preview(
                                    {
                                        "classify": classify_payload,
                                        "classify_summary": classify_summary,
                                        "organize": organize_payload,
                                        "curate": curate_payload,
                                        "import": import_payload,
                                    }
                                ),
                                note="import validation uses an empty input list to avoid mutating the live Photos library" if classify_summary.get("status") == "completed" else "local classify results became queryable, but summary status remained non-terminal during the validation window",
                            )
                            set_check(
                                result_section.checks[3],
                                PASS if vendor_result_ok else FAIL,
                                _json_preview(
                                    {
                                        "summary": classify_summary,
                                        "result": classify_result,
                                        "selected": classify_selected,
                                        "artifacts": classify_artifacts,
                                    }
                                ),
                            )
                        finally:
                            workflow_sample["temp_root"].cleanup()
                else:
                    set_check(run_section.checks[5], SKIP, note="re-run with --include-workflows to exercise classify/curate/organize/import")

                if wait_result_payload and wait_result_payload.get("status") == "failed":
                    result_section.checks[1].note = "pending result became terminal after timeout validation"

    progress("validation completed")
    return sections


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run reusable live validation against PhotosMcp MCP and health endpoints.")
    parser.add_argument("--endpoint", default="http://127.0.0.1:18791/mcp")
    parser.add_argument("--health-url", default="http://127.0.0.1:18791/health")
    parser.add_argument("--capabilities-url", default="http://127.0.0.1:18791/health/capabilities")
    parser.add_argument("--bundle-path", default="")
    parser.add_argument("--wrapper-script", default="")
    parser.add_argument("--report-path", default="")
    parser.add_argument("--local-photo-id", default="")
    parser.add_argument("--non-local-photo-id", default="")
    parser.add_argument("--include-workflows", action="store_true")
    parser.add_argument("--wait-timeout-seconds", type=float, default=6.0)
    parser.add_argument("--wait-poll-interval-seconds", type=float, default=1.0)
    parser.add_argument("--wait-poll-rounds", type=int, default=8)
    parser.add_argument("--quiet-progress", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = ValidationConfig(
        endpoint=args.endpoint,
        health_url=args.health_url,
        capabilities_url=args.capabilities_url,
        bundle_path=args.bundle_path,
        wrapper_script=args.wrapper_script,
        report_path=args.report_path,
        local_photo_id=args.local_photo_id,
        non_local_photo_id=args.non_local_photo_id,
        include_workflows=args.include_workflows,
        wait_timeout_seconds=args.wait_timeout_seconds,
        wait_poll_interval_seconds=args.wait_poll_interval_seconds,
        wait_poll_rounds=args.wait_poll_rounds,
        show_progress=not args.quiet_progress,
    )
    sections = anyio.run(run_live_validation, config)
    markdown = render_markdown_report(config=config, sections=sections)
    if config.report_path:
        Path(config.report_path).write_text(markdown, encoding="utf-8")
    print(markdown, end="")
    return 0
