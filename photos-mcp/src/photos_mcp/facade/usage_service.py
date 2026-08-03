from __future__ import annotations

from typing import Any

from photos_mcp.facade.action_options import ACTION_SPECS
from photos_mcp.vision_runtime import vision_runtime_summary


GOAL_GUIDES: dict[str, dict[str, Any]] = {
    "overview": {
        "description": "photos-mcp의 현재 기능과 안전한 기본 호출 순서를 확인합니다.",
        "steps": [
            {"tool": "photos_query", "action": "status"},
            {"tool": "photos_query", "action": "list"},
            {"tool": "photos_select", "action": "analyze_photo"},
        ],
    },
    "browse": {
        "description": "Apple Photos 사진을 변경 없이 조회합니다.",
        "steps": [
            {"tool": "photos_query", "action": "list"},
            {"tool": "photos_query", "action": "inspect"},
        ],
    },
    "analyze": {
        "description": "Linux Qwen3.6 VLM으로 단일 사진 또는 범위를 분석합니다.",
        "steps": [
            {"tool": "photos_query", "action": "status"},
            {"tool": "photos_select", "action": "analyze_photo"},
            {"tool": "photos_query", "action": "result_detail"},
        ],
    },
    "select": {
        "description": "사진을 변경하지 않고 우수 사진을 선별합니다.",
        "steps": [
            {"tool": "photos_select", "action": "select_best"},
            {"tool": "photos_query", "action": "selected"},
        ],
    },
    "album": {
        "description": "선별 결과를 확인한 뒤 단일 앨범 변경 계획을 승인해 적용합니다.",
        "steps": [
            {"tool": "photos_select", "action": "select_best"},
            {"tool": "photos_query", "action": "selected"},
            {"tool": "photos_write", "action": "add_selected_to_album"},
        ],
        "safeguard": "앨범 쓰기는 MutationPlan 확인과 사용자 승인을 거친 뒤 적용합니다.",
    },
    "categories": {
        "description": "범위를 분류한 뒤 카테고리별 앨범 변경 계획을 승인해 적용합니다.",
        "steps": [
            {"tool": "photos_select", "action": "classify_range"},
            {"tool": "photos_query", "action": "result_detail"},
            {"tool": "photos_write", "action": "organize_by_category"},
        ],
        "safeguard": "단일 앨범 요청에는 organize_by_category를 사용하지 않습니다.",
    },
    "troubleshoot": {
        "description": "transport, Photos 권한, VLM, 분석, 쓰기 순서로 장애를 분리합니다.",
        "steps": [
            {"tool": "photos_query", "action": "status", "options": {"view": "checks"}},
            {"tool": "photos_query", "action": "list", "options": {"limit": 1}},
            {"tool": "photos_select", "action": "analyze_photo"},
        ],
    },
}


def _action_catalog() -> dict[str, list[dict[str, Any]]]:
    catalog: dict[str, list[dict[str, Any]]] = {}
    for (tool, action), spec in sorted(ACTION_SPECS.items()):
        catalog.setdefault(tool, []).append(
            {
                "action": action,
                "allowed_options": sorted(spec.allowed),
                "required_options": sorted(spec.required),
                "defaults": dict(spec.defaults),
                "usage_hint": spec.usage_hint,
            }
        )
    return catalog


def photos_guide(goal: str = "overview") -> dict[str, Any]:
    selected_goal = (goal or "overview").strip().lower().replace("-", "_")
    guide = GOAL_GUIDES.get(selected_goal)
    if guide is None:
        return {
            "status": "blocked",
            "error_code": "unknown_usage_goal",
            "goal": selected_goal,
            "known_goals": sorted(GOAL_GUIDES),
            "next_suggested_action": "photos_query",
        }

    return {
        "status": "ok",
        "goal": selected_goal,
        "guide": guide,
        "vision_runtime": vision_runtime_summary(check_ready=True),
        "safety": {
            "write_plan_approval_required": True,
            "failed_workflow_resume_approval_required": True,
            "failed_workflow_resume_approval_status": "checkpoint_resume_same_run_available",
            "remote_vlm_default_allowed": True,
            "local_only_override": "PHOTOS_MCP_VLM_POLICY=local_only",
        },
        "action_catalog": _action_catalog(),
    }
