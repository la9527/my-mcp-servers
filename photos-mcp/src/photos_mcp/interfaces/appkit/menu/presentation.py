from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


BASIC_CHECK_KEYS = ("photos_permission", "photos_read")
OPTIONAL_CHECK_KEYS = ("photos_thumbnail", "photos_automation")

_CHECK_TITLES = {
    "photos_permission": "사진 접근 권한",
    "photos_read": "사진 보관함 읽기",
    "photos_thumbnail": "사진 미리보기",
    "photos_automation": "앨범 변경 권한",
}

_CHECK_SUMMARIES = {
    "photos_permission": {
        "ok": "사진 접근 권한을 사용할 수 있습니다.",
        "warning": "사진 접근 권한 확인이 필요합니다.",
        "error": "사진 접근 권한을 사용할 수 없습니다.",
        "pending": "사진 접근 권한을 확인하고 있습니다.",
    },
    "photos_read": {
        "ok": "사진 보관함을 읽을 수 있습니다.",
        "warning": "사진 보관함 읽기 상태를 확인해야 합니다.",
        "error": "사진 보관함을 읽지 못했습니다.",
        "pending": "사진 보관함 연결을 확인하고 있습니다.",
    },
    "photos_thumbnail": {
        "ok": "사진 미리보기를 가져올 수 있습니다.",
        "warning": "사진 미리보기 접근을 확인해야 합니다.",
        "error": "사진 미리보기를 가져오지 못했습니다.",
        "pending": "사진 미리보기 접근을 확인하고 있습니다.",
    },
    "photos_automation": {
        "ok": "사진 앨범 변경 권한을 사용할 수 있습니다.",
        "warning": "사진 앨범 변경 권한을 확인해야 합니다.",
        "error": "사진 앨범 변경 권한을 사용할 수 없습니다.",
        "pending": "사진 앨범 변경 권한을 확인하고 있습니다.",
    },
}

_CHECK_HINTS = {
    "photos_permission": "macOS 시스템 설정 > 개인정보 보호 및 보안 > 사진에서 PhotosMcp 접근을 허용하세요.",
    "photos_read": "사진 앱과 보관함이 정상적으로 열리고 접근 권한이 허용되었는지 확인하세요.",
    "photos_thumbnail": "원본 사진을 로컬에 내려받고 PhotosMcp의 사진 접근 권한을 확인하세요.",
    "photos_automation": "macOS 시스템 설정 > 개인정보 보호 및 보안 > 자동화에서 Photos 제어를 허용하세요.",
}

_JOB_TITLES = {
    "photos_select": "사진 분석",
    "photos_workflow": "사진 작업",
    "photos_run": "사진 작업",
    "analyze_photo": "사진 분석",
    "classify": "사진 분류",
    "classify_and_organize": "사진 분류 및 정리",
    "curate": "우수 사진 선별",
    "curate_best_photos": "우수 사진 선별",
    "import_and_organize": "사진 가져오기 및 정리",
    "resume": "중단된 사진 작업 재개",
    "general": "사진 분석",
}

_STAGE_TITLES = {
    "filter": "기본 품질 분석",
    "quality": "사진 품질 분석",
    "vlm": "VLM 분석",
    "vision": "이미지 분석",
    "source": "사진 원본 준비",
    "waiting_source": "사진 원본 준비 대기",
    "waiting_for_local_download": "사진 원본 다운로드 대기",
    "waiting_model": "이미지 분석 모델 연결",
    "writing": "사진 변경 적용",
    "finalizing": "결과 정리",
}

_DEFERRED_MARKERS = (
    "deferred",
    "explicitly requested",
    "startup skipped",
    "until explicitly",
    "on demand",
    "미실행",
    "보류",
)

_JOB_REASON_LABELS = {
    "cancelled": "사용자가 작업을 취소했습니다",
    "canceled": "사용자가 작업을 취소했습니다",
    "local_download_probe_timeout": "사진 다운로드 상태 확인 시간이 초과되었습니다",
    "local_download_timeout": "원본 사진 다운로드 대기 시간이 초과되었습니다",
    "no photos found from source": "조건에 맞는 사진을 찾지 못했습니다",
    "app_restarted_before_completion": "앱 재시작으로 작업이 중단되었습니다",
    "recovered stale running job after restart or cancelled session": "앱 재시작으로 작업이 중단되었습니다",
}


@dataclass(frozen=True, slots=True)
class CheckViewModel:
    key: str
    title: str
    summary: str
    detail: str
    hint: str
    status: str
    tone: str
    status_label: str
    is_basic: bool
    is_deferred: bool
    action_label: str = ""


@dataclass(frozen=True, slots=True)
class JobViewModel:
    job_id: str
    title: str
    subtitle: str
    status: str
    tone: str
    progress_percent: float | None
    operation_detail: str
    result_available: bool
    can_cancel: bool


@dataclass(frozen=True, slots=True)
class MutationPlanViewModel:
    token: str
    title: str
    detail: str
    album_name: str
    photo_count: int
    preview_paths: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EnvironmentViewModel:
    headline: str
    summary: str
    tone: str
    status_label: str
    checked_label: str
    basic_checks: tuple[CheckViewModel, ...]
    optional_checks: tuple[CheckViewModel, ...]
    summary_label: str
    has_actionable_issue: bool


@dataclass(frozen=True, slots=True)
class MenuViewModel:
    headline: str
    summary: str
    tone: str
    icon_state: str
    active_jobs: tuple[JobViewModel, ...]
    mutation_plans: tuple[MutationPlanViewModel, ...]
    recent_jobs: tuple[JobViewModel, ...]
    environment: EnvironmentViewModel
    popover_height: float


def _text(value: Any) -> str:
    return str(value or "").strip()


def _is_deferred_check(check: dict[str, Any]) -> bool:
    key = _text(check.get("key"))
    if key not in OPTIONAL_CHECK_KEYS:
        return False
    status = _text(check.get("status")) or "pending"
    combined = " ".join(
        _text(check.get(field)).lower()
        for field in ("summary", "detail", "hint")
    )
    return status == "pending" or (
        status == "warning" and any(marker in combined for marker in _DEFERRED_MARKERS)
    )


def _check_tone_and_label(status: str, *, deferred: bool) -> tuple[str, str]:
    if deferred:
        return "neutral", "미실행"
    if status == "ok":
        return "success", "통과"
    if status == "error":
        return "error", "문제 발견"
    if status == "warning":
        return "warning", "확인 필요"
    return "neutral", "대기 중"


def _check_view_model(check: dict[str, Any], *, is_basic: bool) -> CheckViewModel:
    key = _text(check.get("key"))
    status = _text(check.get("status")) or "pending"
    deferred = _is_deferred_check(check)
    tone, status_label = _check_tone_and_label(status, deferred=deferred)
    summary = _CHECK_SUMMARIES.get(key, {}).get(status) or _text(check.get("summary"))
    if deferred:
        summary = "아직 검사하지 않았습니다."
    hint = _text(check.get("hint"))
    if status in {"warning", "error"} and key in _CHECK_HINTS:
        hint = _CHECK_HINTS[key]
    action_label = ""
    if deferred:
        action_label = "검사"
    elif status in {"warning", "error"}:
        action_label = "권한 열기" if key == "photos_permission" else "다시 검사"
    return CheckViewModel(
        key=key,
        title=_CHECK_TITLES.get(key, _text(check.get("title")) or "환경 검사"),
        summary=summary,
        detail=_text(check.get("detail")),
        hint=hint,
        status=status,
        tone=tone,
        status_label=status_label,
        is_basic=is_basic,
        is_deferred=deferred,
        action_label=action_label,
    )


def _placeholder_check(key: str, *, is_basic: bool) -> CheckViewModel:
    deferred = not is_basic
    return CheckViewModel(
        key=key,
        title=_CHECK_TITLES[key],
        summary="아직 검사하지 않았습니다.",
        detail="",
        hint="",
        status="pending",
        tone="neutral",
        status_label="미실행" if deferred else "대기 중",
        is_basic=is_basic,
        is_deferred=deferred,
        action_label="검사" if deferred else "",
    )


def _format_checked_at(value: Any, *, now: datetime | None = None) -> str:
    raw = _text(value)
    if not raw:
        return "아직 확인하지 않음"
    relative = format_relative_time(raw, now=now)
    return f"마지막 확인 · {relative}"


def build_environment_view_model(
    snapshot: Any,
    *,
    is_checking: bool = False,
    now: datetime | None = None,
) -> EnvironmentViewModel:
    check_map = {
        _text(check.get("key")): check
        for check in list(getattr(snapshot, "preflight_checks", []) or [])
        if isinstance(check, dict) and _text(check.get("key"))
    }
    basic_checks = tuple(
        _check_view_model(check_map[key], is_basic=True)
        if key in check_map
        else _placeholder_check(key, is_basic=True)
        for key in BASIC_CHECK_KEYS
    )
    optional_checks = tuple(
        _check_view_model(check_map[key], is_basic=False)
        if key in check_map
        else _placeholder_check(key, is_basic=False)
        for key in OPTIONAL_CHECK_KEYS
    )
    actionable = tuple(
        check
        for check in (*basic_checks, *optional_checks)
        if check.tone in {"warning", "error"}
    )
    basic_ready = all(check.status == "ok" for check in basic_checks)

    if is_checking:
        headline = "환경 검사를 실행하고 있습니다"
        summary = "사진 접근과 선택 기능을 순서대로 확인하는 중입니다."
        tone = "progress"
        status_label = "검사 중"
    elif any(check.tone == "error" for check in actionable):
        headline = "확인이 필요한 문제가 있습니다"
        summary = "실패한 항목의 해결 방법을 확인한 뒤 다시 검사하세요."
        tone = "error"
        status_label = "문제 발견"
    elif actionable:
        headline = "확인이 필요한 항목이 있습니다"
        summary = "사진 조회는 가능하지만 일부 기능에 추가 확인이 필요합니다."
        tone = "warning"
        status_label = "확인 필요"
    elif basic_ready:
        headline = "기본 기능을 사용할 수 있습니다"
        summary = "사진 조회와 MCP 요청을 받을 준비가 되었습니다."
        tone = "success"
        status_label = "사용 가능"
    else:
        headline = "기본 환경을 확인하고 있습니다"
        summary = "사진 접근 권한과 보관함 연결 결과를 기다리는 중입니다."
        tone = "neutral"
        status_label = "확인 중"

    basic_passed = sum(check.status == "ok" for check in basic_checks)
    optional_passed = sum(check.status == "ok" for check in optional_checks)
    optional_deferred = sum(check.is_deferred for check in optional_checks)
    summary_parts = [f"기본 {basic_passed}개 통과"]
    if optional_deferred:
        summary_parts.append(f"선택 {optional_deferred}개 미실행")
    elif optional_passed:
        summary_parts.append(f"선택 {optional_passed}개 통과")
    if actionable:
        summary_parts.append(f"확인 필요 {len(actionable)}개")

    return EnvironmentViewModel(
        headline=headline,
        summary=summary,
        tone=tone,
        status_label=status_label,
        checked_label=_format_checked_at(getattr(snapshot, "last_preflight_at", ""), now=now),
        basic_checks=basic_checks,
        optional_checks=optional_checks,
        summary_label=" · ".join(summary_parts),
        has_actionable_issue=bool(actionable),
    )


def _job_title(request_kind: Any) -> str:
    normalized = _text(request_kind).lower()
    if normalized in _JOB_TITLES:
        return _JOB_TITLES[normalized]
    if not normalized or normalized == "job":
        return "사진 작업"
    return "사진 작업"


def check_view_model_from_payload(check: Any) -> CheckViewModel:
    if isinstance(check, dict):
        payload = dict(check)
    else:
        payload = {
            field: getattr(check, field, "")
            for field in ("key", "title", "status", "summary", "detail", "hint")
        }
    return _check_view_model(
        payload,
        is_basic=_text(payload.get("key")) in BASIC_CHECK_KEYS,
    )


def _stage_title(stage: Any) -> str:
    normalized = _text(stage).lower()
    return _STAGE_TITLES.get(normalized, "작업 처리" if normalized else "")


def format_relative_time(value: Any, *, now: datetime | None = None) -> str:
    raw = _text(value)
    if not raw:
        return "시간 정보 없음"
    try:
        parsed = datetime.fromtimestamp(float(raw), UTC) if raw.replace(".", "", 1).isdigit() else datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, TypeError, OSError):
        return raw
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    reference = now or datetime.now(UTC)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=UTC)
    seconds = max(0, int((reference - parsed.astimezone(reference.tzinfo)).total_seconds()))
    if seconds < 60:
        return "방금 전"
    if seconds < 3600:
        return f"{seconds // 60}분 전"
    if seconds < 86400:
        return f"{seconds // 3600}시간 전"
    return f"{seconds // 86400}일 전"


def _job_view_model(job: dict[str, Any], *, active: bool, now: datetime | None = None) -> JobViewModel:
    status = _text(job.get("status")) or "unknown"
    title = _job_title(job.get("request_kind"))
    progress_percent = job.get("progress_percent")
    if isinstance(progress_percent, (int, float)):
        progress_percent = max(0.0, min(float(progress_percent), 100.0))
    else:
        progress_percent = None

    if active:
        parts = []
        stage = _stage_title(job.get("progress_stage"))
        if stage:
            parts.append(stage)
        current = job.get("progress_current")
        total = job.get("progress_total")
        if isinstance(total, (int, float)) and total:
            parts.append(f"{int(current or 0)} / {int(total)}")
        if progress_percent is not None:
            parts.append(f"{progress_percent:.0f}%")
        subtitle = " · ".join(parts) or _text(job.get("progress_label")) or "작업을 준비하고 있습니다"
        tone = "progress" if status not in {"failed", "degraded"} else "error"
        details = []
        waiting_reason = _text(job.get("waiting_reason"))
        if waiting_reason:
            details.append(waiting_reason)
        provider = _text(job.get("runtime_provider"))
        if provider:
            provider_label = {
                "linux_qwen36": "Linux Qwen3.8",
                "mlx_local": "Mac MLX",
                "local_openai_compat": "로컬 OpenAI 호환 모델",
                "openai_compat": "OpenAI 호환 모델",
            }.get(provider, provider)
            details.append(f"분석 모델: {provider_label}")
        operation_detail = " · ".join(details)
    else:
        timestamp = format_relative_time(job.get("finished_at") or job.get("started_at"), now=now)
        reason = _text(job.get("reason"))
        reason_label = _JOB_REASON_LABELS.get(reason.lower(), reason)
        raw_result_count = job.get("result_count")
        result_count = max(0, int(raw_result_count)) if isinstance(raw_result_count, (int, float)) else None
        if status == "completed":
            title = f"{title} 완료"
            if result_count == 0:
                detail = "사진 결과 0건"
                tone = "neutral"
            elif result_count is not None:
                detail = f"사진 결과 {result_count}건"
                tone = "success"
            else:
                detail = "결과 보기 가능" if job.get("result_available") else "요약만 확인 가능"
                tone = "success"
            subtitle = f"{timestamp} · {detail}"
        elif status == "awaiting_resume_approval":
            title = f"{title} 재개 확인 필요"
            subtitle = f"{timestamp} · 재개 전 승인이 필요합니다"
            tone = "warning"
        elif status == "cancelled":
            title = f"{title} 취소됨"
            cancel_reason = reason_label or "사용자가 작업을 취소했습니다"
            subtitle = f"{timestamp} · {cancel_reason}"
            tone = "neutral"
        elif status == "interrupted":
            title = f"{title} 중단됨"
            interruption_reason = reason_label or "작업이 완료되기 전에 중단되었습니다"
            subtitle = f"{timestamp} · {interruption_reason} · 다시 실행하세요"
            tone = "warning"
        else:
            title = f"{title} 실패"
            subtitle = f"{timestamp} · {reason_label or '실패 원인을 확인하세요'}"
            tone = "error"
        operation_detail = ""

    return JobViewModel(
        job_id=_text(job.get("job_id")),
        title=title,
        subtitle=subtitle,
        status=status,
        tone=tone,
        progress_percent=progress_percent,
        operation_detail=operation_detail,
        result_available=bool(job.get("result_available")) and result_count != 0,
        can_cancel=active and status in {"pending", "running", "waiting_source", "waiting_model", "writing"},
    )


def mutation_plan_view_model(record: dict[str, Any]) -> MutationPlanViewModel:
    plan = record.get("mutation_plan") if isinstance(record.get("mutation_plan"), dict) else {}
    action = _text(plan.get("action") or record.get("action"))
    album = _text(plan.get("target_album_name") or plan.get("album_prefix"))
    photo_ids = list(plan.get("photo_ids") or plan.get("photo_paths") or [])
    count = int(plan.get("photo_count") or len(photo_ids))
    preview_paths = tuple(
        _text(item.get("preview_path"))
        for item in list(plan.get("previews") or plan.get("preview_items") or [])
        if isinstance(item, dict) and _text(item.get("preview_path"))
    )
    if action in {"add_selected_to_album", "organize_to_albums", "curate_to_album"}:
        title = "사진 변경 승인 대기"
        detail = f"‘{album or '대상'}’ 앨범에 사진 {count}장 추가"
    elif action in {"export_selected", "curate_to_directory"}:
        title = "사진 내보내기 승인 대기"
        detail = f"사진 {count}장을 선택한 폴더로 내보내기"
    elif action in {"import_to_album", "import_then_curate_to_album"}:
        title = "사진 가져오기 승인 대기"
        detail = f"‘{album or '대상'}’ 앨범으로 사진 {count}장 가져오기"
    else:
        title = "사진 변경 승인 대기"
        detail = f"사진 {count}장에 변경 적용"
    return MutationPlanViewModel(
        token=_text(record.get("token")),
        title=title,
        detail=detail,
        album_name=album,
        photo_count=count,
        preview_paths=preview_paths,
    )


def build_menu_view_model(
    snapshot: Any,
    *,
    is_checking: bool = False,
    now: datetime | None = None,
) -> MenuViewModel:
    environment = build_environment_view_model(snapshot, is_checking=is_checking, now=now)
    active_jobs = tuple(
        _job_view_model(job, active=True, now=now)
        for job in list(getattr(snapshot, "active_jobs", []) or [])[:1]
    )
    mutation_plans = tuple(
        mutation_plan_view_model(record)
        for record in list(getattr(snapshot, "pending_mutation_plans", []) or [])[:1]
    )
    raw_recent_jobs = list(getattr(snapshot, "recent_jobs", []) or [])
    meaningful_recent_jobs = [
        job for job in raw_recent_jobs if _text(job.get("status")).lower() != "cancelled"
    ]
    recent_jobs = tuple(
        _job_view_model(job, active=False, now=now)
        for job in (meaningful_recent_jobs or raw_recent_jobs)[:3]
    )
    daemon_status = _text(getattr(snapshot, "daemon_status", "stopped")) or "stopped"

    if daemon_status in {"stopped", "stopping"}:
        headline = "서버가 중지되어 있습니다"
        summary = "MCP 요청을 받으려면 서버를 시작하세요."
        tone = "neutral"
        icon_state = "stopped"
    elif daemon_status in {"degraded"}:
        headline = "서버 연결을 확인해 주세요"
        summary = "환경 검사에서 원인과 해결 방법을 확인할 수 있습니다."
        tone = "error"
        icon_state = "attention"
    elif mutation_plans:
        headline = "사진 변경 승인이 필요합니다"
        summary = "계획을 검토하기 전에는 사진이나 앨범을 변경하지 않습니다."
        tone = "warning"
        icon_state = "attention"
    elif active_jobs:
        headline = "사진 작업을 진행하고 있습니다"
        summary = active_jobs[0].subtitle
        tone = "progress"
        icon_state = "busy"
    elif environment.has_actionable_issue:
        headline = "확인이 필요한 항목이 있습니다"
        summary = "환경 검사에서 해결 방법을 확인하세요."
        tone = environment.tone
        icon_state = "attention"
    else:
        headline = "사진 보관함에 연결됨"
        summary = "MCP 요청을 받을 준비가 되었습니다."
        tone = "success"
        icon_state = "ready"

    height = 116.0 + 58.0 + 32.0
    if mutation_plans:
        height += 32.0 + (72.0 * len(mutation_plans))
    if active_jobs:
        height += 32.0 + (92.0 * len(active_jobs))
    if recent_jobs:
        height += 32.0 + (58.0 * len(recent_jobs))
    height = max(260.0, min(620.0, height + 26.0))

    return MenuViewModel(
        headline=headline,
        summary=summary,
        tone=tone,
        icon_state=icon_state,
        active_jobs=active_jobs,
        mutation_plans=mutation_plans,
        recent_jobs=recent_jobs,
        environment=environment,
        popover_height=height,
    )


def build_job_history_view_models(
    snapshot: Any,
    *,
    now: datetime | None = None,
) -> tuple[JobViewModel, ...]:
    active = tuple(
        _job_view_model(job, active=True, now=now)
        for job in list(getattr(snapshot, "active_jobs", []) or [])
    )
    recent = tuple(
        _job_view_model(job, active=False, now=now)
        for job in list(getattr(snapshot, "recent_jobs", []) or [])
    )
    return (*active, *recent)
