"""Pure result presentation rules shared by menu and gallery interfaces."""

from __future__ import annotations

from typing import Any


SAFE_RESULT_EXPORT_FIELDS = (
    "total_score",
    "quality_score",
    "family_score",
    "event_score",
    "uniqueness_score",
    "scene_description",
    "event_type",
    "meaningful_score",
    "faces_detected",
    "technical_score",
    "scene_cluster_size",
    "cluster_rank",
    "recommended_in_cluster",
    "recommendation_slot",
    "selection_reason_codes",
    "selected",
    "tags",
    "note",
    "status",
    "error_message",
    "can_retry",
)

_RECOMMENDATION_REASON_LABELS = {
    "scene_best": "같은 장면에서 가장 높은 종합 점수",
    "quality_leader": "장면 내 품질 선두",
    "relative_scene_leader": "같은 장면의 상대 1순위",
    "scene_alternative": "같은 장면의 보완 후보",
    "diverse_second": "서로 다른 구도를 고려한 두 번째 추천",
    "relative_scene_second": "같은 장면의 상대 2순위",
    "same_scene_alternative": "같은 장면의 대안 후보",
}


def result_item_failure(item: dict[str, Any]) -> str:
    status = str(item.get("status") or "").strip().lower()
    message = str(
        item.get("error_message")
        or item.get("error")
        or (item.get("reason") if status in {"failed", "error"} else "")
        or ""
    ).strip()
    if message:
        return message
    if status in {"failed", "error"}:
        return "분석에 실패했습니다."
    return ""


def recommendation_reason_summary(item: dict[str, Any]) -> str:
    """Return a short user-facing explanation from persisted selection policy."""

    if result_item_failure(item):
        return "분석 실패로 추천에서 제외됨"
    codes = [
        str(code)
        for code in item.get("selection_reason_codes") or []
        if str(code) in _RECOMMENDATION_REASON_LABELS
    ]
    labels = [_RECOMMENDATION_REASON_LABELS[code] for code in codes]
    if labels:
        return " · ".join(dict.fromkeys(labels))
    if bool(item.get("recommended_in_cluster")):
        slot = max(1, int(item.get("recommendation_slot") or 1))
        return f"같은 장면의 추천 {slot}순위"
    if int(item.get("scene_cluster_size") or 1) > 1:
        return "같은 장면의 대안 후보"
    return "단일 사진 결과"


def sanitized_result_export_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Build a portable result export without photo ids or local paths."""

    items = [item for item in list(payload.get("items") or []) if isinstance(item, dict)]
    exported_items = []
    for index, item in enumerate(items, start=1):
        exported = {
            "result_index": index,
            **{
                key: item.get(key)
                for key in SAFE_RESULT_EXPORT_FIELDS
                if item.get(key) not in (None, "", [], {})
            },
        }
        failure = result_item_failure(item)
        if failure:
            exported["analysis_status"] = "failed"
            exported.setdefault("error_message", failure)
        else:
            exported["analysis_status"] = "completed"
        exported_items.append(exported)
    return {
        "schema_version": 1,
        "job_id": str(payload.get("job_id") or ""),
        "photo_count": len(items),
        "privacy": "원본 경로와 사진 식별자는 제외되었습니다.",
        "items": exported_items,
    }


def sorted_result_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    items = [dict(item) for item in list(payload.get("items") or []) if isinstance(item, dict)]
    return sorted(items, key=result_sort_key)


def result_sort_key(item: dict[str, Any]) -> tuple[bool, float, float, int, str]:
    """Keep ranking stable for flat lists and scene representatives alike."""

    return (
        bool(result_item_failure(item)),
        -float(item.get("total_score") or item.get("quality_score") or 0.0),
        -float(item.get("quality_score") or 0.0),
        int(item.get("cluster_rank") or 9999),
        str(item.get("photo_id") or ""),
    )


def group_result_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return score-ordered scene groups without discarding alternate photos."""

    grouped: dict[str, list[dict[str, Any]]] = {}
    for position, raw_item in enumerate(items, start=1):
        item = dict(raw_item)
        photo_id = str(item.get("photo_id") or "")
        cluster_id = str(item.get("scene_cluster_id") or photo_id or f"result-{position}")
        grouped.setdefault(cluster_id, []).append(item)

    groups = []
    for cluster_id, members in grouped.items():
        ordered_members = sorted(members, key=result_sort_key)
        groups.append(
            {
                "scene_cluster_id": cluster_id,
                "items": ordered_members,
                "representative": dict(ordered_members[0]),
            }
        )
    return sorted(groups, key=lambda group: result_sort_key(group["representative"]))


def recommended_scene_best_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Choose one highest-scoring recommended photo from every recommended scene."""

    selected: list[dict[str, Any]] = []
    for group in group_result_items(items):
        best_recommended = next(
            (item for item in group["items"] if result_category(item) == "recommended"),
            None,
        )
        if best_recommended is not None:
            selected.append(dict(best_recommended))
    return sorted(selected, key=result_sort_key)


def result_category(item: dict[str, Any]) -> str:
    if result_item_failure(item):
        return "review"
    if "recommended_in_cluster" in item:
        return "recommended" if bool(item.get("recommended_in_cluster")) else "review"
    if float(item.get("total_score") or item.get("quality_score") or 0.0) >= 80.0:
        return "recommended"
    return "review"
