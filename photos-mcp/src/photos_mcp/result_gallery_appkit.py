"""Compatibility exports for the result gallery."""

from photos_mcp.interfaces.appkit.results.controller import (
    PhotosMcpResultCollectionItem,
    PhotosMcpResultsController,
    group_result_items,
    initial_density_index,
    result_category,
    result_item_failure,
    sanitized_result_export_payload,
    sorted_result_items,
)

__all__ = [
    "PhotosMcpResultCollectionItem",
    "PhotosMcpResultsController",
    "group_result_items",
    "initial_density_index",
    "result_category",
    "result_item_failure",
    "sanitized_result_export_payload",
    "sorted_result_items",
]
