from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class ActionSpec:
    tool: str
    action: str
    allowed: frozenset[str]
    required: frozenset[str] = field(default_factory=frozenset)
    defaults: Mapping[str, Any] = field(default_factory=dict)
    forbidden: frozenset[str] = field(default_factory=frozenset)
    usage_hint: str = ""


@dataclass(frozen=True, slots=True)
class ValidatedAction:
    action: str
    options: dict[str, Any]


class ActionValidationError(ValueError):
    def __init__(self, payload: dict[str, Any]) -> None:
        super().__init__(str(payload.get("error") or payload.get("error_code") or "invalid action options"))
        self.payload = payload


def _set(*values: str) -> frozenset[str]:
    return frozenset(values)


ACTION_SPECS: dict[tuple[str, str], ActionSpec] = {}


def _register(spec: ActionSpec) -> None:
    ACTION_SPECS[(spec.tool, spec.action)] = spec


COMMON_SCOPE = _set("source", "source_path", "album", "person", "date_from", "date_to", "limit")
WAIT_OPTIONS = _set("wait_for_local", "wait_timeout_seconds", "wait_poll_interval_seconds")
RESULT_OPTIONS = _set("run_id", "top_n", "output_dir", "min_score", "group_by_date", "mode")
WRITE_FORBIDDEN = _set("target_album_name", "album_prefix", "writeback_mode", "output_dir", "folder", "group_by_date", "results_json")


_register(ActionSpec(
    tool="photos_query",
    action="status",
    allowed=_set("view"),
    defaults={"view": "summary"},
))
_register(ActionSpec(
    tool="photos_query",
    action="list",
    allowed=_set("source", "album", "person", "date_from", "date_to", "limit", "include_thumbnail", "include_metadata", "max_size"),
    defaults={"source": "apple", "limit": 20, "include_thumbnail": False, "include_metadata": False, "max_size": 512},
))
_register(ActionSpec(
    tool="photos_query",
    action="ready_only",
    allowed=_set("source", "album", "person", "date_from", "date_to", "limit", "include_thumbnail", "include_metadata", "max_size"),
    defaults={"source": "apple", "limit": 20, "include_thumbnail": False, "include_metadata": False, "max_size": 512},
))
_register(ActionSpec(
    tool="photos_query",
    action="search",
    allowed=_set("source", "query", "album", "person", "date_from", "date_to", "limit", "include_thumbnail", "include_metadata", "max_size"),
    required=_set("query"),
    defaults={"source": "apple", "limit": 20, "include_thumbnail": False, "include_metadata": False, "max_size": 512},
))
_register(ActionSpec(
    tool="photos_query",
    action="inspect",
    allowed=_set("source", "photo_id", "include_thumbnail", "include_metadata", "max_size"),
    required=_set("photo_id"),
    defaults={"source": "apple", "include_thumbnail": True, "include_metadata": True, "max_size": 512},
))
_register(ActionSpec(
    tool="photos_query",
    action="prefetch",
    allowed=_set("source", "photo_id", "path_or_bucket", "album", "person", "date_from", "date_to", "limit"),
    defaults={"source": "apple", "photo_id": "", "path_or_bucket": "", "album": "", "person": "", "date_from": "", "date_to": "", "limit": 20},
))
for _action in ("result_summary", "result_detail", "selected", "artifacts", "cancel"):
    _register(ActionSpec(
        tool="photos_query",
        action=_action,
        allowed=RESULT_OPTIONS,
        defaults={"run_id": "latest", "top_n": 20, "output_dir": "", "min_score": 0.0, "group_by_date": False, "mode": "copy"},
    ))

_register(ActionSpec(
    tool="photos_select",
    action="analyze_photo",
    allowed=_set("source", "photo_id", "path_or_bucket", "prompt", "include_faces", "max_size", "wait_for_local", "wait_timeout_seconds", "wait_poll_interval_seconds", "run_id"),
    required=_set("photo_id"),
    defaults={"source": "apple", "path_or_bucket": "", "prompt": "", "include_faces": False, "max_size": 512, "wait_for_local": False, "wait_timeout_seconds": 120.0, "wait_poll_interval_seconds": 3.0, "run_id": ""},
))
_register(ActionSpec(
    tool="photos_select",
    action="classify_range",
    allowed=COMMON_SCOPE | _set("selection_profile"),
    defaults={"source": "apple", "source_path": "", "album": "", "person": "", "date_from": "", "date_to": "", "limit": 50, "selection_profile": "general"},
))
_register(ActionSpec(
    tool="photos_select",
    action="select_best",
    allowed=COMMON_SCOPE | _set("selection_profile", "exclude_screenshots") | WAIT_OPTIONS,
    forbidden=WRITE_FORBIDDEN,
    defaults={"source": "apple", "source_path": "", "album": "", "person": "", "date_from": "", "date_to": "", "limit": 50, "selection_profile": "general", "exclude_screenshots": True, "wait_for_local": False, "wait_timeout_seconds": 120.0, "wait_poll_interval_seconds": 3.0},
))
_register(ActionSpec(
    tool="photos_select",
    action="select_best_person",
    allowed=COMMON_SCOPE | _set("selection_profile", "exclude_screenshots") | WAIT_OPTIONS,
    required=_set("person"),
    forbidden=WRITE_FORBIDDEN,
    defaults={"source": "apple", "source_path": "", "album": "", "date_from": "", "date_to": "", "limit": 50, "selection_profile": "person", "exclude_screenshots": True, "wait_for_local": False, "wait_timeout_seconds": 120.0, "wait_poll_interval_seconds": 3.0},
))

_register(ActionSpec(
    tool="photos_write",
    action="add_selected_to_album",
    allowed=_set("run_id", "target_album_name", "folder"),
    required=_set("run_id", "target_album_name"),
    forbidden=_set("album_prefix", "group_by_date", "min_score", "selection_profile", "date_from", "date_to", "results_json"),
    defaults={"folder": ""},
))
_register(ActionSpec(
    tool="photos_write",
    action="add_photo_ids_to_album",
    allowed=_set("source", "photo_ids", "target_album_name", "folder"),
    required=_set("photo_ids", "target_album_name"),
    forbidden=_set("album_prefix", "group_by_date", "min_score", "selection_profile", "date_from", "date_to", "run_id"),
    defaults={"source": "apple", "folder": ""},
))
_register(ActionSpec(
    tool="photos_write",
    action="export_selected",
    allowed=_set("run_id", "output_dir", "top_n", "min_score", "group_by_date", "mode"),
    required=_set("run_id", "output_dir"),
    defaults={"top_n": 50, "min_score": 0.0, "group_by_date": False, "mode": "copy"},
))
_register(ActionSpec(
    tool="photos_write",
    action="organize_by_category",
    allowed=_set("run_id", "album_prefix", "folder", "min_score", "group_by_date"),
    required=_set("run_id"),
    forbidden=_set("target_album_name"),
    defaults={"album_prefix": "AI 분류", "folder": "", "min_score": 0.0, "group_by_date": False},
    usage_hint=(
        "Use this only when category albums are desired. Do not pass target_album_name. "
        "Retry with run_id plus album_prefix, and use curate_to_album or add_selected_to_album "
        "for exactly one target album."
    ),
))
_register(ActionSpec(
    tool="photos_write",
    action="import_to_album",
    allowed=_set("photo_paths", "target_album_name", "folder"),
    required=_set("photo_paths", "target_album_name"),
    forbidden=_set("album_prefix", "results_json", "group_by_date"),
    defaults={"folder": ""},
))
_register(ActionSpec(
    tool="photos_write",
    action="cleanup_album",
    allowed=_set("target_album_name", "folder"),
    required=_set("target_album_name"),
    defaults={"folder": ""},
))

_register(ActionSpec(
    tool="photos_workflow",
    action="curate_to_album",
    allowed=COMMON_SCOPE | _set("selection_profile", "exclude_screenshots", "target_album_name", "folder") | WAIT_OPTIONS,
    required=_set("target_album_name"),
    forbidden=_set("writeback_mode", "album_prefix", "results_json", "output_dir", "group_by_date"),
    defaults={"source": "apple", "source_path": "", "album": "", "person": "", "date_from": "", "date_to": "", "limit": 50, "selection_profile": "general", "exclude_screenshots": True, "folder": "", "wait_for_local": False, "wait_timeout_seconds": 120.0, "wait_poll_interval_seconds": 3.0},
    usage_hint=(
        "Use flat options only: scope filters plus target_album_name. Do not nest filters under "
        "scope or selection. Do not pass selected_photo_ids, results_json, or prior result payloads. "
        "This workflow already selects the best photos and writes them into exactly one target album."
    ),
))
_register(ActionSpec(
    tool="photos_workflow",
    action="curate_to_directory",
    allowed=COMMON_SCOPE | _set("selection_profile", "exclude_screenshots", "output_dir", "min_score", "group_by_date", "mode") | WAIT_OPTIONS,
    required=_set("output_dir"),
    forbidden=_set("target_album_name", "album_prefix", "writeback_mode", "results_json"),
    defaults={"source": "apple", "source_path": "", "album": "", "person": "", "date_from": "", "date_to": "", "limit": 50, "selection_profile": "general", "exclude_screenshots": True, "min_score": 0.0, "group_by_date": False, "mode": "copy", "wait_for_local": False, "wait_timeout_seconds": 120.0, "wait_poll_interval_seconds": 3.0},
))
_register(ActionSpec(
    tool="photos_workflow",
    action="classify_then_organize_by_category",
    allowed=COMMON_SCOPE | _set("selection_profile", "album_prefix", "folder", "min_score", "group_by_date"),
    forbidden=_set("target_album_name", "writeback_mode", "results_json", "output_dir"),
    defaults={"source": "apple", "source_path": "", "album": "", "person": "", "date_from": "", "date_to": "", "limit": 50, "selection_profile": "general", "album_prefix": "AI 분류", "folder": "", "min_score": 0.0, "group_by_date": False},
))
_register(ActionSpec(
    tool="photos_workflow",
    action="import_then_curate_to_album",
    allowed=_set("photo_paths", "target_album_name", "selection_profile", "exclude_screenshots", "folder"),
    required=_set("photo_paths", "target_album_name"),
    forbidden=_set("album_prefix", "writeback_mode", "results_json", "group_by_date"),
    defaults={"selection_profile": "general", "exclude_screenshots": True, "folder": ""},
))


def _parse_options(options: Any) -> dict[str, Any]:
    if options is None:
        return {}
    if isinstance(options, dict):
        return dict(options)
    if isinstance(options, str):
        try:
            parsed = json.loads(options)
        except json.JSONDecodeError:
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def _is_missing(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip()) or (isinstance(value, list) and not value)


def _blocked_payload(
    *,
    tool: str,
    action: str,
    error_code: str,
    error: str,
    spec: ActionSpec | None = None,
    invalid_options: list[str] | None = None,
    missing_options: list[str] | None = None,
    raw_options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": "blocked",
        "error_code": error_code,
        "error": error,
        "tool": tool,
        "action": action,
        "next_suggested_action": "retry_with_allowed_options",
    }
    if invalid_options is not None:
        payload["invalid_options"] = invalid_options
    if missing_options is not None:
        payload["missing_options"] = missing_options
    if spec is not None:
        payload["allowed_options"] = sorted(spec.allowed)
        payload["required_options"] = sorted(spec.required)
        if spec.usage_hint:
            payload["usage_hint"] = spec.usage_hint
        retry_example = _retry_example_options(spec, raw_options or {})
        if retry_example:
            payload["retry_example"] = retry_example
    return payload


def _retry_example_options(spec: ActionSpec, raw_options: dict[str, Any]) -> dict[str, Any]:
    example: dict[str, Any] = {}

    for key in (
        "source",
        "source_path",
        "album",
        "person",
        "date_from",
        "date_to",
        "limit",
        "selection_profile",
        "exclude_screenshots",
        "wait_for_local",
        "wait_timeout_seconds",
        "wait_poll_interval_seconds",
        "target_album_name",
        "album_prefix",
        "folder",
        "output_dir",
        "photo_paths",
        "run_id",
    ):
        if key in spec.allowed and key in raw_options:
            example[key] = raw_options[key]

    for key in spec.required:
        if _is_missing(example.get(key)) and not _is_missing(raw_options.get(key)):
            example[key] = raw_options[key]

    for key in (
        "source",
        "selection_profile",
        "exclude_screenshots",
        "wait_for_local",
        "wait_timeout_seconds",
        "wait_poll_interval_seconds",
        "album_prefix",
    ):
        if key in spec.allowed and key not in example and key in spec.defaults and not _is_missing(spec.defaults[key]):
            example[key] = spec.defaults[key]

    return example


def validate_action_options(tool: str, action: str, options: Any) -> ValidatedAction:
    normalized_action = (action or "").strip().lower().replace("-", "_")
    spec = ACTION_SPECS.get((tool, normalized_action))
    if spec is None:
        known_actions = sorted(item_action for item_tool, item_action in ACTION_SPECS if item_tool == tool)
        raise ActionValidationError({
            "status": "blocked",
            "error_code": "unknown_action",
            "error": "Unknown action for tool",
            "tool": tool,
            "action": normalized_action,
            "known_actions": known_actions,
            "next_suggested_action": "retry_with_known_action",
        })

    raw_options = _parse_options(options)
    invalid = sorted(key for key in raw_options if key not in spec.allowed or key in spec.forbidden)
    if invalid:
        raise ActionValidationError(_blocked_payload(
            tool=tool,
            action=normalized_action,
            error_code="invalid_options_for_action",
            error="Option is not allowed for this action",
            spec=spec,
            invalid_options=invalid,
            raw_options=raw_options,
        ))

    normalized_options = dict(spec.defaults)
    normalized_options.update(raw_options)
    missing = sorted(key for key in spec.required if _is_missing(normalized_options.get(key)))
    if missing:
        raise ActionValidationError(_blocked_payload(
            tool=tool,
            action=normalized_action,
            error_code="missing_required_options",
            error="Required option is missing for this action",
            spec=spec,
            missing_options=missing,
            raw_options=raw_options,
        ))

    return ValidatedAction(action=normalized_action, options=normalized_options)
