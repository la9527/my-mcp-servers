"""Compatibility exports for the menu bar application."""

from photos_mcp.interfaces.appkit.menu.controller import (
    PhotosMcpEnvironmentController,
    PhotosMcpMenuController,
    PhotosMcpPopoverController,
    connection_info_text,
    environment_check_view_model,
    environment_diagnostics_text,
    mutation_plan_display,
    result_item_failure,
    run_menu_app,
    sanitized_result_export_payload,
    sorted_result_items,
)
from photos_mcp.interfaces.appkit.results.controller import PhotosMcpResultsController

__all__ = [
    "PhotosMcpEnvironmentController",
    "PhotosMcpMenuController",
    "PhotosMcpPopoverController",
    "PhotosMcpResultsController",
    "connection_info_text",
    "environment_check_view_model",
    "environment_diagnostics_text",
    "mutation_plan_display",
    "result_item_failure",
    "run_menu_app",
    "sanitized_result_export_payload",
    "sorted_result_items",
]
