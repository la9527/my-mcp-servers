from photos_mcp.local_file_selection_appkit import PhotosMcpLocalPhotoSelectionController
from photos_mcp.main_window_appkit import PhotosMcpMainWindowController
from photos_mcp.menu_app import PhotosMcpMenuController, run_menu_app
from photos_mcp.photo_viewer_appkit import (
    PhotosMcpPhotoViewerController,
    PhotosMcpZoomImageView,
)
from photos_mcp.result_gallery_appkit import PhotosMcpResultsController


def test_existing_appkit_imports_remain_available_during_refactor() -> None:
    assert PhotosMcpLocalPhotoSelectionController is not None
    assert PhotosMcpMainWindowController is not None
    assert PhotosMcpMenuController is not None
    assert PhotosMcpPhotoViewerController is not None
    assert PhotosMcpZoomImageView is not None
    assert PhotosMcpResultsController is not None
    assert callable(run_menu_app)
