from photos_mcp.local_file_selection_appkit import PhotosMcpLocalPhotoSelectionController
from photos_mcp.main_window_appkit import PhotosMcpMainWindowController
from photos_mcp.menu_app import PhotosMcpMenuController, run_menu_app
from photos_mcp.photo_viewer_appkit import (
    PhotosMcpPhotoViewerController,
    PhotosMcpZoomImageView,
)
from photos_mcp.result_gallery_appkit import PhotosMcpResultsController
from photos_mcp.app.config import PhotosMcpConfig as NewPhotosMcpConfig
from photos_mcp.app.config import load_config as new_load_config
from photos_mcp.app.main import run_cli as new_run_cli
from photos_mcp.config import PhotosMcpConfig as LegacyPhotosMcpConfig
from photos_mcp.config import load_config as legacy_load_config
from photos_mcp.domain.models.photo import PhotoAsset as NewPhotoAsset
from photos_mcp.infrastructure.vendor_adapter.loader import (
    load_vendor_server as new_load_vendor_server,
)
from photos_mcp.interfaces.mcp.facade.public_tools import (
    photos_query as new_photos_query,
)
from photos_mcp.interfaces.mcp.server import build_server as new_build_server
from photos_mcp.main import run_cli as legacy_run_cli
from photos_mcp.photo_assets import PhotoAsset as LegacyPhotoAsset
from photos_mcp.server import build_server as legacy_build_server
from photos_mcp.vendor_loader import load_vendor_server as legacy_load_vendor_server
from photos_mcp.facade.public_tools import photos_query as legacy_photos_query


def test_existing_appkit_imports_remain_available_during_refactor() -> None:
    assert PhotosMcpLocalPhotoSelectionController is not None
    assert PhotosMcpMainWindowController is not None
    assert PhotosMcpMenuController is not None
    assert PhotosMcpPhotoViewerController is not None
    assert PhotosMcpZoomImageView is not None
    assert PhotosMcpResultsController is not None
    assert callable(run_menu_app)


def test_existing_console_and_mcp_imports_reexport_new_implementations() -> None:
    assert legacy_run_cli is new_run_cli
    assert legacy_build_server is new_build_server
    assert legacy_photos_query is new_photos_query


def test_existing_domain_and_infrastructure_imports_reexport_new_implementations() -> None:
    assert LegacyPhotosMcpConfig is NewPhotosMcpConfig
    assert legacy_load_config is new_load_config
    assert LegacyPhotoAsset is NewPhotoAsset
    assert legacy_load_vendor_server is new_load_vendor_server
