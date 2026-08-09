"""Native three-pane local photo browser for direct classification."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import Event, Thread
from typing import Any

import objc
from AppKit import (
    NSApp,
    NSBackingStoreBuffered,
    NSButton,
    NSButtonTypeSwitch,
    NSCache,
    NSClickGestureRecognizer,
    NSColor,
    NSCollectionView,
    NSCollectionViewFlowLayout,
    NSCollectionViewItem,
    NSCollectionViewScrollDirectionVertical,
    NSControlStateValueOff,
    NSControlStateValueOn,
    NSControlSizeLarge,
    NSEdgeInsetsMake,
    NSEventModifierFlagCommand,
    NSImage,
    NSImageScaleProportionallyUpOrDown,
    NSImageSymbolConfiguration,
    NSImageView,
    NSMakeRect,
    NSModalResponseOK,
    NSOpenPanel,
    NSOutlineView,
    NSOutlineViewDisclosureButtonKey,
    NSPasteboard,
    NSPasteboardTypeString,
    NSPopUpButton,
    NSSearchField,
    NSSegmentedControl,
    NSSegmentStyleRounded,
    NSSplitView,
    NSSplitViewDividerStyleThin,
    NSScrollView,
    NSTableColumn,
    NSTableViewStyleSourceList,
    NSTextField,
    NSFontWeightRegular,
    NSView,
    NSViewHeightSizable,
    NSViewWidthSizable,
    NSWindow,
    NSWindowCollectionBehaviorFullScreenPrimary,
    NSWindowController,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskMiniaturizable,
    NSWindowStyleMaskResizable,
    NSWindowStyleMaskTitled,
)
from Foundation import NSIndexPath, NSIndexSet, NSMakePoint, NSMakeSize, NSSet, NSURL
from Quartz import (
    CGImageGetHeight,
    CGImageGetWidth,
    CGImageSourceCreateThumbnailAtIndex,
    CGImageSourceCreateWithURL,
    kCGImageSourceCreateThumbnailFromImageAlways,
    kCGImageSourceCreateThumbnailFromImageIfAbsent,
    kCGImageSourceCreateThumbnailWithTransform,
    kCGImageSourceThumbnailMaxPixelSize,
)

from photos_mcp.application.classification_service import (
    ClassificationCommand,
    DirectClassificationService,
    common_local_source_path,
)
from photos_mcp.infrastructure.sources.local_files.catalog import (
    LOCAL_IMAGE_EXTENSIONS,
    local_photo_from_path as _local_photo_from_path,
    scan_local_photos as _scan_local_photos,
)
from photos_mcp.infrastructure.sources.local_files.models import LocalPhoto
from photos_mcp.local_photo_metadata import LocalPhotoMetadata, extract_local_photo_metadata
from photos_mcp.interfaces.appkit.results.photo_viewer import PhotosMcpZoomImageView
from photos_mcp.raw_image import RAW_IMAGE_EXTENSIONS
from photos_mcp.interfaces.appkit.shared.theme import accent_color, app_font, panel_background_color, subtle_border_color


_WINDOW_WIDTH = 1440.0
_WINDOW_HEIGHT = 860.0
_WINDOW_MIN_WIDTH = 1180.0
_WINDOW_MIN_HEIGHT = 700.0
_SIDEBAR_MIN_WIDTH = 240.0
_SIDEBAR_MAX_FRACTION = 0.40
_CONTENT_MIN_WIDTH = 500.0
_INSPECTOR_MIN_WIDTH = 320.0
_INSPECTOR_MAX_WIDTH = 440.0
_ITEM_IDENTIFIER = "PhotosMcpLocalPhotoItem"
_THUMBNAIL_CACHE = NSCache.alloc().init()
_THUMBNAIL_CACHE.setCountLimit_(256)
_DENSITY_WIDTHS = (156.0, 188.0, 224.0, 264.0)
_DEFAULT_DENSITY_INDEX = 1
_ICON_BUTTON_WIDTH = 40.0
_ICON_BUTTON_HEIGHT = 36.0
_DISCLOSURE_ICON_SIZE = 17.0
_FOLDER_ICON_SIZE = 20.0
_SEARCH_ICON_SIZE = 17.0
_CONTENT_TOOLBAR_BREAKPOINT = 700.0
_SINGLE_ABSOLUTE_MIN_ZOOM = 0.1
_SINGLE_MAX_ZOOM = 8.0
_SINGLE_ZOOM_STEP = 1.25
_SINGLE_DOUBLE_CLICK_ZOOM_STEP = 2.0
_SINGLE_ZOOM_EPSILON = 0.005


def _configure_disclosure_button(button) -> None:
    configuration = NSImageSymbolConfiguration.configurationWithPointSize_weight_(
        _DISCLOSURE_ICON_SIZE,
        NSFontWeightRegular,
    )
    collapsed = NSImage.imageWithSystemSymbolName_accessibilityDescription_(
        "chevron.right",
        "펼치기",
    )
    expanded = NSImage.imageWithSystemSymbolName_accessibilityDescription_(
        "chevron.down",
        "접기",
    )
    if collapsed is None or expanded is None:
        return
    collapsed = collapsed.imageWithSymbolConfiguration_(configuration)
    expanded = expanded.imageWithSymbolConfiguration_(configuration)
    collapsed.setTemplate_(True)
    expanded.setTemplate_(True)
    button.setImage_(collapsed)
    button.setAlternateImage_(expanded)
    button.setImageScaling_(NSImageScaleProportionallyUpOrDown)


class LargeDisclosureOutlineView(NSOutlineView):
    """Keep native outline behavior while matching disclosure glyphs to body text."""

    def makeViewWithIdentifier_owner_(self, identifier, owner):
        view = objc.super(LargeDisclosureOutlineView, self).makeViewWithIdentifier_owner_(identifier, owner)
        if view is not None and str(identifier or "") == str(NSOutlineViewDisclosureButtonKey):
            _configure_disclosure_button(view)
        return view


class PhotoCollectionView(NSCollectionView):
    """Add selection toggling without replacing native grid navigation."""

    def keyDown_(self, event) -> None:
        owner = getattr(self, "_browser_owner", None)
        if owner is not None and int(event.keyCode()) in {36, 49, 76}:
            owner.toggleGridPhotoFromKeyboard_(self)
            return
        objc.super(PhotoCollectionView, self).keyDown_(event)


class SinglePhotoKeyView(NSView):
    """Keep single-photo navigation available from the keyboard."""

    def acceptsFirstResponder(self) -> bool:
        return True

    def keyDown_(self, event) -> None:
        owner = getattr(self, "_browser_owner", None)
        key_code = int(event.keyCode())
        if owner is not None and key_code == 123:
            owner.showPreviousPhoto_(None)
            return
        if owner is not None and key_code == 124:
            owner.showNextPhoto_(None)
            return
        if owner is not None and key_code in {36, 49, 76}:
            owner.toggleFocusedPhotoFromKeyboard_(None)
            return
        if owner is not None and hasattr(event, "modifierFlags"):
            if int(event.modifierFlags()) & NSEventModifierFlagCommand:
                characters = str(event.charactersIgnoringModifiers() or "")
                if characters in {"+", "="}:
                    owner.singleZoomIn_(None)
                    return
                if characters == "-":
                    owner.singleZoomOut_(None)
                    return
        objc.super(SinglePhotoKeyView, self).keyDown_(event)


class LocalSinglePhotoZoomImageView(PhotosMcpZoomImageView):
    """Reuse the result viewer gestures while preserving local-browser keys."""

    def keyDown_(self, event) -> None:
        owner = getattr(self, "_viewer_owner", None)
        key_code = int(event.keyCode())
        if owner is not None and key_code == 123:
            owner.showPreviousPhoto_(None)
            return
        if owner is not None and key_code == 124:
            owner.showNextPhoto_(None)
            return
        if owner is not None and key_code in {36, 49, 76}:
            owner.toggleFocusedPhotoFromKeyboard_(None)
            return
        if owner is not None and key_code == 53:
            owner._view_mode_control.setSelectedSegment_(0)
            owner.viewModeChanged_(owner._view_mode_control)
            return
        if key_code == 34:
            return
        if owner is not None and hasattr(event, "modifierFlags"):
            if int(event.modifierFlags()) & NSEventModifierFlagCommand:
                characters = str(event.charactersIgnoringModifiers() or "")
                if characters in {"+", "="}:
                    owner.singleZoomIn_(None)
                    return
                if characters == "-":
                    owner.singleZoomOut_(None)
                    return
        objc.super(LocalSinglePhotoZoomImageView, self).keyDown_(event)


class FlippedDocumentView(NSView):
    """Lay out scroll document content from the visible top edge."""

    def isFlipped(self) -> bool:
        return True


def _maximum_sidebar_width(
    total_width: float,
    divider_width: float,
    inspector_width: float = _INSPECTOR_MIN_WIDTH,
) -> float:
    available_with_minimum_center = (
        total_width
        - (divider_width * 2.0)
        - _CONTENT_MIN_WIDTH
        - max(_INSPECTOR_MIN_WIDTH, inspector_width)
    )
    return max(
        _SIDEBAR_MIN_WIDTH,
        min(total_width * _SIDEBAR_MAX_FRACTION, available_with_minimum_center),
    )


@dataclass(frozen=True)
class FolderNode:
    """A source-list group, a folder, or a temporary loading row."""

    key: str
    title: str
    path: str = ""
    kind: str = "folder"


def _default_root_path() -> Path:
    pictures = Path.home() / "Pictures"
    return pictures if pictures.is_dir() else Path.home()


def _folder_nodes_for_path(path: Path) -> list[FolderNode]:
    """Return direct child folders without following inaccessible entries."""

    try:
        children = [item for item in path.iterdir() if item.is_dir() and not item.name.startswith(".")]
    except (OSError, PermissionError):
        return []
    return [
        FolderNode(key=f"folder:{item.resolve()}", title=item.name, path=str(item.resolve()))
        for item in sorted(children, key=lambda item: item.name.casefold())
    ]


def _thumbnail_cache_key(photo: LocalPhoto, max_pixels: int) -> str:
    return f"{photo.path}:{int(photo.modified_at)}:{max(64, int(max_pixels))}"


def _decode_thumbnail(photo: LocalPhoto, max_pixels: int) -> Any | None:
    """Build a display-only thumbnail without loading the full original into the UI."""

    source = CGImageSourceCreateWithURL(NSURL.fileURLWithPath_(photo.path), None)
    if source is None:
        return None
    thumbnail_policy = (
        kCGImageSourceCreateThumbnailFromImageIfAbsent
        if Path(photo.path).suffix.lower() in RAW_IMAGE_EXTENSIONS
        else kCGImageSourceCreateThumbnailFromImageAlways
    )
    image = CGImageSourceCreateThumbnailAtIndex(
        source,
        0,
        {
            thumbnail_policy: True,
            kCGImageSourceCreateThumbnailWithTransform: True,
            kCGImageSourceThumbnailMaxPixelSize: max(64, int(max_pixels)),
        },
    )
    if image is None:
        return None
    return NSImage.alloc().initWithCGImage_size_(
        image,
        NSMakeSize(float(CGImageGetWidth(image)), float(CGImageGetHeight(image))),
    )


class PhotosMcpLocalPhotoItem(NSCollectionViewItem):
    """Reusable image-first card with independent focus and job-selection state."""

    def loadView(self) -> None:
        root = NSView.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, 188.0, 142.0))
        root.setWantsLayer_(True)
        root.layer().setCornerRadius_(10.0)
        root.layer().setBorderWidth_(1.0)
        root.layer().setBorderColor_(subtle_border_color().CGColor())
        root.layer().setBackgroundColor_(NSColor.clearColor().CGColor())
        self.setView_(root)
        self._image_view = NSImageView.alloc().initWithFrame_(NSMakeRect(5.0, 5.0, 178.0, 132.0))
        self._image_view.setImageScaling_(NSImageScaleProportionallyUpOrDown)
        self._image_view.setImageAlignment_(0)
        self._image_view.setAccessibilityElement_(False)
        self._image_view.setWantsLayer_(True)
        self._image_view.layer().setCornerRadius_(7.0)
        self._image_view.layer().setMasksToBounds_(True)
        root.addSubview_(self._image_view)
        self._placeholder = NSTextField.labelWithString_("미리보기 불러오는 중")
        self._placeholder.setFont_(app_font(10.0, "regular"))
        self._placeholder.setTextColor_(NSColor.secondaryLabelColor())
        self._placeholder.setAlignment_(1)
        root.addSubview_(self._placeholder)
        self._check_button = NSButton.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, 28.0, 28.0))
        self._check_button.setButtonType_(NSButtonTypeSwitch)
        self._check_button.setControlSize_(NSControlSizeLarge)
        self._check_button.setTitle_("")
        self._check_button.setTarget_(None)
        self._check_button.setAction_("togglePhotoCheck:")
        self._check_button.setAccessibilityLabel_("분류 대상으로 선택")
        self._check_button.setToolTip_("이 사진을 분류 대상에 추가하거나 해제합니다.")
        root.addSubview_(self._check_button)
        self._photo_path = ""
        self._controller: Any | None = None

    def prepareForReuse(self) -> None:
        objc.super(PhotosMcpLocalPhotoItem, self).prepareForReuse()
        self._photo_path = ""
        self._image_view.setImage_(None)
        self._placeholder.setHidden_(False)
        self._check_button.setState_(NSControlStateValueOff)
        self._check_button.setIdentifier_("")

    def setSelected_(self, selected: bool) -> None:
        objc.super(PhotosMcpLocalPhotoItem, self).setSelected_(selected)
        self.view().layer().setBorderColor_((accent_color() if selected else subtle_border_color()).CGColor())
        self.view().layer().setBorderWidth_(2.0 if selected else 1.0)

    def viewDidLayout(self) -> None:
        objc.super(PhotosMcpLocalPhotoItem, self).viewDidLayout()
        bounds = self.view().bounds()
        width = float(bounds.size.width)
        height = float(bounds.size.height)
        self._image_view.setFrame_(NSMakeRect(5.0, 5.0, max(1.0, width - 10.0), max(60.0, height - 10.0)))
        self._placeholder.setFrame_(self._image_view.frame())
        self._check_button.setFrame_(NSMakeRect(max(5.0, width - 36.0), max(5.0, height - 36.0), 28.0, 28.0))

    @objc.python_method
    def configure(self, photo: LocalPhoto, controller: Any) -> None:
        self._photo_path = photo.path
        self._controller = controller
        self.view().setToolTip_(photo.name)
        self.view().setAccessibilityLabel_(photo.name)
        self._check_button.setTarget_(controller)
        self._check_button.setIdentifier_(photo.path)
        self._check_button.setState_(
            NSControlStateValueOn if controller.is_photo_checked(photo.path) else NSControlStateValueOff
        )
        self._check_button.setAccessibilityLabel_(f"{photo.name} 분류 대상으로 선택")
        image = controller.thumbnail_for(photo, controller.thumbnail_pixels_for_visible_item())
        self.set_thumbnail(image)

    @objc.python_method
    def refresh_checked_state(self) -> None:
        checked = bool(self._controller and self._controller.is_photo_checked(self._photo_path))
        self._check_button.setState_(NSControlStateValueOn if checked else NSControlStateValueOff)

    @objc.python_method
    def set_thumbnail(self, image: Any | None) -> None:
        self._image_view.setImage_(image)
        self._placeholder.setHidden_(image is not None)


class PhotosMcpLocalPhotoSelectionController(NSWindowController):
    """Browse local folders, select exact photos, and submit the shared command."""

    def initWithMenuController_service_(self, menu_controller: Any, service: DirectClassificationService | None):
        return self.initWithMenuController_service_sourcePath_selectedPhotoIds_(
            menu_controller,
            service,
            str(_default_root_path()),
            (),
        )

    def initWithMenuController_service_sourcePath_selectedPhotoIds_(
        self,
        menu_controller: Any,
        service: DirectClassificationService | None,
        source_path: str,
        selected_photo_ids: tuple[str, ...],
    ):
        style = (
            NSWindowStyleMaskTitled
            | NSWindowStyleMaskClosable
            | NSWindowStyleMaskMiniaturizable
            | NSWindowStyleMaskResizable
        )
        window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0.0, 0.0, _WINDOW_WIDTH, _WINDOW_HEIGHT), style, NSBackingStoreBuffered, False
        )
        self = objc.super(PhotosMcpLocalPhotoSelectionController, self).initWithWindow_(window)
        if self is None:
            return None
        self._menu_controller = menu_controller
        self._service = service or DirectClassificationService(state_store=getattr(menu_controller, "_state_store", None))
        requested_root = Path(source_path).expanduser()
        self._current_folder = str(requested_root.resolve()) if requested_root.is_dir() else str(_default_root_path())
        self._selected_paths = {str(Path(path).expanduser().resolve()) for path in selected_photo_ids}
        self._selected_photos = {
            photo.path: photo
            for path in self._selected_paths
            if (photo := _local_photo_from_path(path)) is not None
        }
        self._selected_paths = set(self._selected_photos)
        self._focused_path = next(iter(self._selected_paths), "")
        self._photos: list[LocalPhoto] = []
        self._folder_children: dict[str, list[FolderNode]] = {}
        self._folder_counts: dict[str, int] = {}
        self._folder_loads_inflight: set[str] = set()
        self._thumbnail_inflight: set[str] = set()
        self._thumbnail_failures: set[str] = set()
        self._pending_folder_results: dict[str, list[FolderNode]] = {}
        self._pending_photo_results: tuple[int, str, list[LocalPhoto]] | None = None
        self._pending_thumbnail_results: dict[str, Any | None] = {}
        self._pending_metadata_results: dict[str, tuple[int, LocalPhotoMetadata]] = {}
        self._photo_generation = 0
        self._metadata_generation = 0
        self._metadata_cache: OrderedDict[str, LocalPhotoMetadata] = OrderedDict()
        self._metadata_current: LocalPhotoMetadata | None = None
        self._metadata_requested_key = ""
        self._metadata_inflight: set[str] = set()
        self._metadata_section_expanded = {"file", "camera", "exposure"}
        self._metadata_dynamic_views: list[Any] = []
        self._metadata_layout_rows: list[tuple[str, Any]] = []
        self._selection_dynamic_views: list[Any] = []
        self._selection_layout_rows: list[tuple[str, Any]] = []
        self._selection_image_views: dict[str, list[Any]] = {}
        self._selection_expanded_folders: set[str] = set()
        self._inspector_mode = "photo"
        self._density_index = _DEFAULT_DENSITY_INDEX
        self._view_mode = "grid"
        self._initial_split_installed = False
        self._normalizing_split_view = False
        self._inspector_width_preference = 360.0
        self._single_is_fit = True
        self._single_fit_zoom_factor = _SINGLE_ABSOLUTE_MIN_ZOOM
        self._single_zoom_factor = _SINGLE_ABSOLUTE_MIN_ZOOM
        self._single_image_size = NSMakeSize(0.0, 0.0)
        self._single_display_rect = NSMakeRect(0.0, 0.0, 0.0, 0.0)
        self._single_pan_start_window_point = None
        self._single_pan_start_scroll_origin = None
        self._single_displayed_thumbnail_key = ""
        self._run_worker: Thread | None = None
        self._pending_run: dict[str, Any] = {}
        self._thumbnail_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="photos-mcp-thumbnail")
        self._event_loop = asyncio.new_event_loop()
        self._event_loop_ready = Event()
        self._event_loop_thread = Thread(target=self._run_event_loop, name="photos-mcp-local-browser-runtime", daemon=True)
        self._event_loop_thread.start()
        self._event_loop_ready.wait(timeout=1.0)
        window.setTitle_("로컬 사진 분류")
        window.setMinSize_(NSMakeSize(_WINDOW_MIN_WIDTH, _WINDOW_MIN_HEIGHT))
        window.setCollectionBehavior_(NSWindowCollectionBehaviorFullScreenPrimary)
        window.setReleasedWhenClosed_(False)
        window.setDelegate_(self)
        self._build_window()
        return self

    def showWindow_(self, _sender) -> None:
        self.window().center()
        self._install_initial_split_positions()
        self._layout_panes()
        self._sync_outline_selection()
        self._load_current_folder(clear_selection=False)
        self.focusWindow()

    @objc.python_method
    def focusWindow(self) -> None:
        NSApp.activateIgnoringOtherApps_(True)
        self.window().makeKeyAndOrderFront_(None)

    def windowWillClose_(self, _notification) -> None:
        if getattr(self._menu_controller, "_local_photo_selection_controller", None) == self:
            self._menu_controller._local_photo_selection_controller = None
        direct = getattr(self._menu_controller, "_direct_classification_controller", None)
        if direct is not None and hasattr(direct, "localPhotoBrowserDidClose_"):
            direct.localPhotoBrowserDidClose_(None)
        self.shutdown()

    def shutdown(self) -> None:
        self._thumbnail_executor.shutdown(wait=False, cancel_futures=True)
        if self._event_loop.is_running():
            self._event_loop.call_soon_threadsafe(self._event_loop.stop)
            self._event_loop_thread.join(timeout=1.0)

    def windowDidResize_(self, _notification) -> None:
        self._layout_panes()

    def splitViewDidResizeSubviews_(self, _notification) -> None:
        if self._normalizing_split_view:
            return
        self._normalizing_split_view = True
        try:
            self.splitView_resizeSubviewsWithOldSize_(
                self._split_view,
                self._split_view.bounds().size,
            )
            self._layout_panes()
        finally:
            self._normalizing_split_view = False

    def splitView_constrainSplitPosition_ofSubviewAt_(self, split_view, proposed: float, divider_index: int) -> float:
        width = float(split_view.bounds().size.width)
        divider = float(split_view.dividerThickness())
        if divider_index == 0:
            inspector_width = min(
                _INSPECTOR_MAX_WIDTH,
                float(self._inspector.frame().size.width),
            )
            maximum = _maximum_sidebar_width(width, divider, inspector_width)
            return max(_SIDEBAR_MIN_WIDTH, min(float(proposed), maximum))
        sidebar_width = max(_SIDEBAR_MIN_WIDTH, float(self._sidebar.frame().size.width))
        minimum = max(
            sidebar_width + divider + _CONTENT_MIN_WIDTH,
            width - divider - _INSPECTOR_MAX_WIDTH,
        )
        maximum = width - divider - _INSPECTOR_MIN_WIDTH
        constrained = max(minimum, min(float(proposed), maximum))
        self._inspector_width_preference = max(
            _INSPECTOR_MIN_WIDTH,
            min(_INSPECTOR_MAX_WIDTH, width - divider - constrained),
        )
        return constrained

    def splitView_shouldAdjustSizeOfSubview_(self, _split_view, subview) -> bool:
        return subview == self._content

    def splitView_canCollapseSubview_(self, _split_view, _subview) -> bool:
        return False

    def splitView_resizeSubviewsWithOldSize_(self, split_view, _old_size) -> None:
        width = float(split_view.bounds().size.width)
        height = float(split_view.bounds().size.height)
        divider = float(split_view.dividerThickness())
        available = max(0.0, width - (divider * 2.0))
        sidebar_maximum = _maximum_sidebar_width(width, divider)
        sidebar_width = max(
            _SIDEBAR_MIN_WIDTH,
            min(float(self._sidebar.frame().size.width), sidebar_maximum),
        )
        inspector_width = max(
            _INSPECTOR_MIN_WIDTH,
            min(self._inspector_width_preference, _INSPECTOR_MAX_WIDTH),
        )
        maximum_side_total = max(
            _SIDEBAR_MIN_WIDTH + _INSPECTOR_MIN_WIDTH,
            available - _CONTENT_MIN_WIDTH,
        )
        excess = sidebar_width + inspector_width - maximum_side_total
        if excess > 0.0:
            inspector_reduction = min(excess, inspector_width - _INSPECTOR_MIN_WIDTH)
            inspector_width -= inspector_reduction
            remaining_excess = excess - inspector_reduction
            if remaining_excess > 0.0:
                sidebar_width = max(_SIDEBAR_MIN_WIDTH, sidebar_width - remaining_excess)
        content_width = max(0.0, available - sidebar_width - inspector_width)
        content_x = sidebar_width + divider
        inspector_x = content_x + content_width + divider
        self._sidebar.setFrame_(NSMakeRect(0.0, 0.0, sidebar_width, height))
        self._content.setFrame_(NSMakeRect(content_x, 0.0, content_width, height))
        self._inspector.setFrame_(NSMakeRect(inspector_x, 0.0, inspector_width, height))

    @objc.python_method
    def _build_window(self) -> None:
        root = self.window().contentView()
        root.setWantsLayer_(True)
        root.layer().setBackgroundColor_(NSColor.windowBackgroundColor().CGColor())

        self._history: list[str] = [self._current_folder]
        self._history_index = 0

        self._split_view = NSSplitView.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, _WINDOW_WIDTH, _WINDOW_HEIGHT))
        self._split_view.setVertical_(True)
        self._split_view.setDividerStyle_(NSSplitViewDividerStyleThin)
        self._split_view.setDelegate_(self)
        self._split_view.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        root.addSubview_(self._split_view)

        self._sidebar = NSView.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, 280.0, _WINDOW_HEIGHT))
        self._content = NSView.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, 800.0, _WINDOW_HEIGHT))
        self._inspector = NSView.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, 360.0, _WINDOW_HEIGHT))
        for pane in (self._sidebar, self._content, self._inspector):
            pane.setWantsLayer_(True)
            pane.layer().setBackgroundColor_(NSColor.windowBackgroundColor().CGColor())
            self._split_view.addSubview_(pane)

        self._build_sidebar()
        self._build_content()
        self._build_inspector()
        self._update_history_controls()

    @objc.python_method
    def _build_sidebar(self) -> None:
        self._sidebar_title = self._label(self._sidebar, "폴더", 15.0, bold=True)
        self._add_location_button = self._button(
            self._sidebar,
            "+",
            "requestFolderAccess:",
            accessibility_label="위치 추가",
            icon=True,
        )
        self._add_location_button.setToolTip_("위치 추가")
        favorites = FolderNode(key="group:favorites", title="즐겨찾기", kind="group")
        locations = FolderNode(key="group:locations", title="위치", kind="group")
        self._root_nodes = [favorites, locations]
        self._folder_children[favorites.key] = [
            FolderNode(key=f"folder:{_default_root_path()}", title="사진", path=str(_default_root_path())),
            FolderNode(key=f"folder:{Path.home() / 'Desktop'}", title="데스크탑", path=str(Path.home() / "Desktop")),
            FolderNode(key=f"folder:{Path.home() / 'Downloads'}", title="다운로드", path=str(Path.home() / "Downloads")),
        ]
        location_nodes = [FolderNode(key="folder:/", title="Macintosh HD", path="/")]
        volumes = Path("/Volumes")
        if volumes.is_dir():
            location_nodes.extend(
                FolderNode(key=f"folder:{item.resolve()}", title=item.name, path=str(item.resolve()))
                for item in sorted(volumes.iterdir(), key=lambda item: item.name.casefold())
                if item.is_dir() and not item.name.startswith(".")
            )
        self._folder_children[locations.key] = location_nodes
        column = NSTableColumn.alloc().initWithIdentifier_("folders")
        self._outline = LargeDisclosureOutlineView.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, 1.0, 1.0))
        self._outline.addTableColumn_(column)
        self._outline.setOutlineTableColumn_(column)
        self._outline.setHeaderView_(None)
        if hasattr(self._outline, "setStyle_"):
            self._outline.setStyle_(NSTableViewStyleSourceList)
        self._outline.setDataSource_(self)
        self._outline.setDelegate_(self)
        self._outline.setRowHeight_(44.0)
        self._outline.setIndentationPerLevel_(16.0)
        self._outline.setAutosaveExpandedItems_(False)
        self._outline.setAllowsEmptySelection_(True)
        self._outline.setAccessibilityLabel_("로컬 사진 폴더")
        self._outline_scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, 1.0, 1.0))
        self._outline_scroll.setHasVerticalScroller_(True)
        self._outline_scroll.setAutohidesScrollers_(True)
        self._outline_scroll.setDrawsBackground_(False)
        self._outline_scroll.setDocumentView_(self._outline)
        self._sidebar.addSubview_(self._outline_scroll)
        self._sidebar_status = self._label(self._sidebar, "준비", 11.0, secondary=True)
        self._outline.reloadData()
        self._outline.expandItem_(favorites)
        self._outline.expandItem_(locations)

    @objc.python_method
    def _build_content(self) -> None:
        self._back_button = self._button(
            self._content,
            "‹",
            "goBack:",
            accessibility_label="이전 폴더",
            icon=True,
        )
        self._forward_button = self._button(
            self._content,
            "›",
            "goForward:",
            accessibility_label="다음 폴더",
            icon=True,
        )
        self._folder_title = self._label(self._content, "사진", 17.0, bold=True)
        self._search_field = NSSearchField.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, 260.0, 30.0))
        self._search_field.setControlSize_(NSControlSizeLarge)
        self._search_field.setFont_(app_font(12.0))
        self._search_field.setPlaceholderString_("검색")
        search_symbol = NSImage.imageWithSystemSymbolName_accessibilityDescription_("magnifyingglass", "검색")
        if search_symbol is not None:
            search_configuration = NSImageSymbolConfiguration.configurationWithPointSize_weight_(
                _SEARCH_ICON_SIZE,
                NSFontWeightRegular,
            )
            search_symbol = search_symbol.imageWithSymbolConfiguration_(search_configuration)
            search_symbol.setTemplate_(True)
            self._search_field.cell().searchButtonCell().setImage_(search_symbol)
        self._search_field.setTarget_(self)
        self._search_field.setAction_("searchChanged:")
        self._search_field.setAccessibilityLabel_("현재 폴더 사진 검색")
        self._content.addSubview_(self._search_field)
        self._view_mode_control = NSSegmentedControl.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, 1.0, 1.0))
        self._view_mode_control.setSegmentCount_(2)
        self._view_mode_control.setLabel_forSegment_("격자", 0)
        self._view_mode_control.setLabel_forSegment_("한 장", 1)
        self._view_mode_control.setSelectedSegment_(0)
        self._view_mode_control.setSegmentStyle_(NSSegmentStyleRounded)
        self._view_mode_control.setTarget_(self)
        self._view_mode_control.setAction_("viewModeChanged:")
        self._view_mode_control.setAccessibilityLabel_("사진 보기 방식")
        self._content.addSubview_(self._view_mode_control)
        self._include_subfolders = NSButton.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, 128.0, 26.0))
        self._include_subfolders.setButtonType_(NSButtonTypeSwitch)
        self._include_subfolders.setTitle_("하위 폴더 포함")
        self._include_subfolders.setState_(NSControlStateValueOff)
        self._include_subfolders.setTarget_(self)
        self._include_subfolders.setAction_("scopeChanged:")
        self._include_subfolders.setAccessibilityLabel_("하위 폴더 포함")
        self._content.addSubview_(self._include_subfolders)
        self._sort_popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(NSMakeRect(0.0, 0.0, 140.0, 28.0), False)
        self._sort_popup.addItemsWithTitles_(["파일 날짜: 최신순", "파일 날짜: 오래된순", "파일명", "파일 크기"])
        self._sort_popup.setTarget_(self)
        self._sort_popup.setAction_("sortChanged:")
        self._sort_popup.setAccessibilityLabel_("사진 정렬")
        self._content.addSubview_(self._sort_popup)
        self._density_smaller = self._button(
            self._content,
            "−",
            "changeDensity:",
            identifier="smaller",
            accessibility_label="사진 작게 보기",
            icon=True,
        )
        self._density_larger = self._button(
            self._content,
            "+",
            "changeDensity:",
            identifier="larger",
            accessibility_label="사진 크게 보기",
            icon=True,
        )

        self._flow_layout = NSCollectionViewFlowLayout.alloc().init()
        self._flow_layout.setScrollDirection_(NSCollectionViewScrollDirectionVertical)
        self._flow_layout.setMinimumInteritemSpacing_(10.0)
        self._flow_layout.setMinimumLineSpacing_(10.0)
        self._flow_layout.setSectionInset_(NSEdgeInsetsMake(2.0, 2.0, 12.0, 2.0))
        self._collection = PhotoCollectionView.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, 1.0, 1.0))
        self._collection._browser_owner = self
        self._collection.setCollectionViewLayout_(self._flow_layout)
        self._collection.registerClass_forItemWithIdentifier_(PhotosMcpLocalPhotoItem, _ITEM_IDENTIFIER)
        self._collection.setDataSource_(self)
        self._collection.setDelegate_(self)
        self._collection.setSelectable_(True)
        # Collection selection is Inspector focus only. Job targets are the
        # independent checkboxes rendered by each photo item.
        self._collection.setAllowsMultipleSelection_(False)
        self._collection.setAllowsEmptySelection_(True)
        self._collection_double_click = NSClickGestureRecognizer.alloc().initWithTarget_action_(
            self,
            "openFocusedPhoto:",
        )
        self._collection_double_click.setNumberOfClicksRequired_(2)
        self._collection.addGestureRecognizer_(self._collection_double_click)
        self._collection.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        if hasattr(self._collection, "setBackgroundColors_"):
            self._collection.setBackgroundColors_([NSColor.clearColor()])
        self._collection_scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, 1.0, 1.0))
        self._collection_scroll.setHasVerticalScroller_(True)
        self._collection_scroll.setAutohidesScrollers_(True)
        self._collection_scroll.setDrawsBackground_(False)
        self._collection_scroll.setDocumentView_(self._collection)
        self._content.addSubview_(self._collection_scroll)
        self._single_view = SinglePhotoKeyView.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, 1.0, 1.0))
        self._single_view._browser_owner = self
        self._single_view.setWantsLayer_(True)
        self._single_view.layer().setBackgroundColor_(
            NSColor.blackColor().colorWithAlphaComponent_(0.82).CGColor()
        )
        self._single_scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, 1.0, 1.0))
        self._single_scroll.setDrawsBackground_(True)
        self._single_scroll.setBackgroundColor_(NSColor.blackColor())
        self._single_scroll.setHasHorizontalScroller_(True)
        self._single_scroll.setHasVerticalScroller_(True)
        self._single_scroll.setAutohidesScrollers_(True)
        self._single_image = LocalSinglePhotoZoomImageView.alloc().initWithFrame_(
            NSMakeRect(0.0, 0.0, 1.0, 1.0)
        )
        self._single_image._viewer_owner = self
        self._single_image.setAccessibilityLabel_("현재 사진 크게 보기")
        self._single_scroll.setDocumentView_(self._single_image)
        self._single_view.addSubview_(self._single_scroll)
        self._single_zoom_out_button = self._button(
            self._single_view,
            "−",
            "singleZoomOut:",
            accessibility_label="한 장 보기 축소",
        )
        self._single_zoom_in_button = self._button(
            self._single_view,
            "+",
            "singleZoomIn:",
            accessibility_label="한 장 보기 확대",
        )
        self._single_fit_button = self._button(
            self._single_view,
            "화면 맞춤",
            "singleFitPhoto:",
            accessibility_label="한 장 보기 화면 맞춤",
        )
        self._single_actual_button = self._button(
            self._single_view,
            "100%",
            "singleActualSize:",
            accessibility_label="한 장 보기 실제 크기",
        )
        self._single_zoom_out_button.setToolTip_("축소 (Command -)")
        self._single_zoom_in_button.setToolTip_("확대 (Command +)")
        self._single_fit_button.setToolTip_("사진을 화면에 맞춤")
        self._single_actual_button.setToolTip_("사진을 실제 크기로 표시")
        self._previous_photo_button = self._button(
            self._single_view,
            "‹",
            "showPreviousPhoto:",
            accessibility_label="이전 사진",
            icon=True,
        )
        self._next_photo_button = self._button(
            self._single_view,
            "›",
            "showNextPhoto:",
            accessibility_label="다음 사진",
            icon=True,
        )
        self._single_check_button = NSButton.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, 1.0, 1.0))
        self._single_check_button.setButtonType_(NSButtonTypeSwitch)
        self._single_check_button.setTitle_("분류 대상")
        self._single_check_button.setTarget_(self)
        self._single_check_button.setAction_("toggleFocusedPhotoCheck:")
        self._single_view.addSubview_(self._single_check_button)
        self._single_filename = self._label(self._single_view, "", 11.0)
        self._single_filename.setTextColor_(NSColor.whiteColor())
        self._single_counter = self._label(self._single_view, "", 11.0)
        self._single_counter.setTextColor_(NSColor.whiteColor())
        self._single_counter.setAlignment_(1)
        self._single_view.setHidden_(True)
        self._content.addSubview_(self._single_view)
        self._empty_label = self._label(self._content, "폴더를 선택하면 사진을 표시합니다.", 12.0, secondary=True)
        self._selection_label = self._label(self._content, "사진 0장 · 분류 대상 0장", 12.0, secondary=True)
        self._select_all_button = self._button(self._content, "전체 선택", "selectAllPhotos:")
        self._clear_button = self._button(self._content, "전체 해제", "clearSelection:")

    @objc.python_method
    def _build_inspector(self) -> None:
        self._inspector_title = self._label(self._inspector, "보고 있는 사진", 17.0, bold=True)
        self._inspector_mode_control = NSSegmentedControl.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, 1.0, 1.0))
        self._inspector_mode_control.setSegmentCount_(2)
        self._inspector_mode_control.setLabel_forSegment_("보고 있는 사진", 0)
        self._inspector_mode_control.setLabel_forSegment_("선택 목록 0", 1)
        self._inspector_mode_control.setSelectedSegment_(0)
        self._inspector_mode_control.setSegmentStyle_(NSSegmentStyleRounded)
        self._inspector_mode_control.setTarget_(self)
        self._inspector_mode_control.setAction_("inspectorModeChanged:")
        self._inspector_mode_control.setAccessibilityLabel_("인스펙터 보기")
        self._inspector.addSubview_(self._inspector_mode_control)

        self._photo_scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, 1.0, 1.0))
        self._photo_scroll.setHasVerticalScroller_(True)
        self._photo_scroll.setAutohidesScrollers_(True)
        self._photo_scroll.setDrawsBackground_(False)
        self._photo_document = FlippedDocumentView.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, 1.0, 1.0))
        self._photo_scroll.setDocumentView_(self._photo_document)
        self._inspector.addSubview_(self._photo_scroll)
        self._inspector_image = NSImageView.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, 1.0, 1.0))
        self._inspector_image.setImageScaling_(NSImageScaleProportionallyUpOrDown)
        self._photo_document.addSubview_(self._inspector_image)
        self._inspector_empty = self._label(self._photo_document, "사진을 클릭하면 상세 정보를 볼 수 있습니다.", 12.0, secondary=True)
        self._inspector_empty.setAlignment_(1)
        self._file_name = self._label(self._photo_document, "", 13.0, bold=True)
        self._file_name.setMaximumNumberOfLines_(2)
        self._file_date = self._label(self._photo_document, "", 11.0, secondary=True)
        self._file_size = self._label(self._photo_document, "", 11.0, secondary=True)
        self._file_resolution = self._label(self._photo_document, "", 11.0, secondary=True)
        self._metadata_summary = self._label(self._photo_document, "", 11.5, bold=True)
        self._metadata_status = self._label(self._photo_document, "", 10.5, secondary=True)
        self._copy_metadata_button = self._button(self._photo_document, "정보 복사", "copyMetadata:")
        self._copy_metadata_button.setHidden_(True)

        self._selection_scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, 1.0, 1.0))
        self._selection_scroll.setHasVerticalScroller_(True)
        self._selection_scroll.setAutohidesScrollers_(True)
        self._selection_scroll.setDrawsBackground_(False)
        self._selection_document = FlippedDocumentView.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, 1.0, 1.0))
        self._selection_scroll.setDocumentView_(self._selection_document)
        self._selection_scroll.setHidden_(True)
        self._inspector.addSubview_(self._selection_scroll)
        self._selected_count = self._label(self._inspector, "분류 대상 0장", 13.0, bold=True)
        self._selected_count.setTextColor_(accent_color())
        self._settings_card = self._card(self._inspector)
        self._selected_count.removeFromSuperview()
        self._settings_card.addSubview_(self._selected_count)
        self._settings_title = self._label(self._settings_card, "작업 설정", 15.0, bold=True)
        self._mode = NSSegmentedControl.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, 1.0, 1.0))
        self._mode.setSegmentCount_(2)
        self._mode.setLabel_forSegment_("사진 분류", 0)
        self._mode.setLabel_forSegment_("우수 사진 선별", 1)
        self._mode.setSelectedSegment_(0)
        self._mode.setSegmentStyle_(NSSegmentStyleRounded)
        self._mode.setAccessibilityLabel_("작업 방식")
        self._settings_card.addSubview_(self._mode)
        self._profile_label = self._label(self._settings_card, "프로필", 10.0, bold=True)
        self._profile = NSPopUpButton.alloc().initWithFrame_pullsDown_(NSMakeRect(0.0, 0.0, 1.0, 1.0), False)
        self._profile.addItemsWithTitles_(["일반", "인물", "풍경"])
        self._profile.setAccessibilityLabel_("분류 기준")
        self._settings_card.addSubview_(self._profile)
        self._limit_label = self._label(self._settings_card, "최대 처리 사진 수", 10.0, bold=True)
        self._limit = NSPopUpButton.alloc().initWithFrame_pullsDown_(NSMakeRect(0.0, 0.0, 1.0, 1.0), False)
        self._limit.addItemsWithTitles_(["10장", "25장", "50장", "100장", "250장", "500장", "1000장"])
        self._limit.selectItemWithTitle_("50장")
        self._limit.setTarget_(self)
        self._limit.setAction_("limitChanged:")
        self._limit.setAccessibilityLabel_("최대 처리 사진 수")
        self._settings_card.addSubview_(self._limit)
        self._run_button = self._button(self._settings_card, "사진을 선택하세요", "startClassification:", primary=True)
        self._run_button.setEnabled_(False)
        self._run_status = self._label(self._inspector, "원본 파일은 변경하지 않습니다.", 10.5, secondary=True)
        self._rebuild_selection_document()

    @objc.python_method
    def _install_initial_split_positions(self) -> None:
        if self._initial_split_installed:
            return
        # NSSplitView derives its initial divider positions from its child view
        # frames. Calling setPosition for the second divider interprets the
        # value relative to that pane and can collapse the Inspector.
        self._initial_split_installed = True

    @objc.python_method
    def _layout_panes(self) -> None:
        root = self.window().contentView()
        width = float(root.bounds().size.width)
        height = float(root.bounds().size.height)
        self._split_view.setFrame_(NSMakeRect(0.0, 0.0, width, max(1.0, height)))

        self._layout_sidebar()
        self._layout_content()
        self._layout_inspector()
        self._update_collection_layout()

    @objc.python_method
    def _layout_sidebar(self) -> None:
        bounds = self._sidebar.bounds()
        width = float(bounds.size.width)
        height = float(bounds.size.height)
        self._sidebar_title.setFrame_(NSMakeRect(20.0, height - 48.0, max(1.0, width - 90.0), 28.0))
        self._add_location_button.setFrame_(
            NSMakeRect(width - 60.0, height - 52.0, _ICON_BUTTON_WIDTH, _ICON_BUTTON_HEIGHT)
        )
        self._outline_scroll.setFrame_(NSMakeRect(8.0, 42.0, max(1.0, width - 16.0), max(1.0, height - 98.0)))
        self._sidebar_status.setFrame_(NSMakeRect(20.0, 16.0, max(1.0, width - 40.0), 18.0))

    @objc.python_method
    def _layout_content(self) -> None:
        bounds = self._content.bounds()
        width = float(bounds.size.width)
        height = float(bounds.size.height)
        search_width = 140.0 if width < 680.0 else min(220.0, width * 0.28)
        search_x = width - search_width - 20.0
        header_y = height - 52.0
        self._back_button.setFrame_(
            NSMakeRect(18.0, header_y, _ICON_BUTTON_WIDTH, _ICON_BUTTON_HEIGHT)
        )
        self._forward_button.setFrame_(
            NSMakeRect(64.0, header_y, _ICON_BUTTON_WIDTH, _ICON_BUTTON_HEIGHT)
        )
        self._folder_title.setFrame_(
            NSMakeRect(116.0, height - 47.0, max(1.0, search_x - 126.0), 28.0)
        )
        self._search_field.setFrame_(NSMakeRect(search_x, header_y, search_width, _ICON_BUTTON_HEIGHT))

        control_y = height - 94.0
        density_larger_x = width - 20.0 - _ICON_BUTTON_WIDTH
        density_smaller_x = density_larger_x - 8.0 - _ICON_BUTTON_WIDTH
        self._view_mode_control.setFrame_(NSMakeRect(20.0, control_y, 136.0, _ICON_BUTTON_HEIGHT))
        self._include_subfolders.setFrame_(NSMakeRect(164.0, control_y, 140.0, _ICON_BUTTON_HEIGHT))
        self._density_smaller.setFrame_(
            NSMakeRect(density_smaller_x, control_y, _ICON_BUTTON_WIDTH, _ICON_BUTTON_HEIGHT)
        )
        self._density_larger.setFrame_(
            NSMakeRect(density_larger_x, control_y, _ICON_BUTTON_WIDTH, _ICON_BUTTON_HEIGHT)
        )
        if width < _CONTENT_TOOLBAR_BREAKPOINT:
            sort_y = control_y - 44.0
            self._sort_popup.setFrame_(NSMakeRect(20.0, sort_y, max(1.0, width - 40.0), _ICON_BUTTON_HEIGHT))
            collection_top_inset = 202.0
        else:
            sort_x = 316.0
            sort_width = max(96.0, density_smaller_x - sort_x - 12.0)
            self._sort_popup.setFrame_(NSMakeRect(sort_x, control_y, sort_width, _ICON_BUTTON_HEIGHT))
            collection_top_inset = 158.0
        content_frame = NSMakeRect(
            18.0,
            54.0,
            max(1.0, width - 36.0),
            max(1.0, height - collection_top_inset),
        )
        self._collection_scroll.setFrame_(content_frame)
        self._single_view.setFrame_(content_frame)
        single_width = float(content_frame.size.width)
        single_height = float(content_frame.size.height)
        footer_height = 34.0
        zoom_anchor = None
        if (
            hasattr(self, "_single_scroll")
            and not self._single_is_fit
            and float(self._single_image_size.width) > 0.0
        ):
            zoom_anchor = self._single_visible_image_center()
        self._single_scroll.setFrame_(
            NSMakeRect(58.0, footer_height + 10.0, max(1.0, single_width - 116.0), max(1.0, single_height - footer_height - 20.0))
        )
        if float(self._single_image_size.width) > 0.0:
            if self._single_is_fit:
                self._single_apply_fit_zoom()
            else:
                self._single_layout_document(self._single_zoom_factor)
                if zoom_anchor is not None:
                    self._single_center_image_point(zoom_anchor)
        arrow_height = 48.0
        arrow_y = max(footer_height + 8.0, (single_height + footer_height - arrow_height) / 2.0)
        self._previous_photo_button.setFrame_(NSMakeRect(8.0, arrow_y, _ICON_BUTTON_WIDTH, arrow_height))
        self._next_photo_button.setFrame_(
            NSMakeRect(max(8.0, single_width - 48.0), arrow_y, _ICON_BUTTON_WIDTH, arrow_height)
        )
        zoom_y = max(footer_height, single_height - 38.0)
        self._single_zoom_out_button.setFrame_(NSMakeRect(12.0, zoom_y, 38.0, 28.0))
        self._single_zoom_in_button.setFrame_(NSMakeRect(54.0, zoom_y, 38.0, 28.0))
        self._single_fit_button.setFrame_(NSMakeRect(98.0, zoom_y, 88.0, 28.0))
        self._single_actual_button.setFrame_(NSMakeRect(192.0, zoom_y, 68.0, 28.0))
        self._single_check_button.setFrame_(NSMakeRect(max(8.0, single_width - 138.0), max(footer_height, single_height - 38.0), 126.0, 28.0))
        self._single_filename.setFrame_(NSMakeRect(12.0, 8.0, max(1.0, single_width - 150.0), 20.0))
        self._single_counter.setFrame_(NSMakeRect(max(12.0, single_width - 126.0), 8.0, 114.0, 20.0))
        self._empty_label.setFrame_(NSMakeRect(24.0, (height / 2.0) - 10.0, max(1.0, width - 48.0), 20.0))
        self._empty_label.setAlignment_(1)
        self._selection_label.setFrame_(NSMakeRect(20.0, 16.0, max(150.0, width - 328.0), 20.0))
        self._select_all_button.setFrame_(NSMakeRect(max(176.0, width - 294.0), 11.0, 138.0, 32.0))
        self._clear_button.setFrame_(NSMakeRect(max(320.0, width - 150.0), 11.0, 130.0, 32.0))

    @objc.python_method
    def _layout_inspector(self) -> None:
        bounds = self._inspector.bounds()
        width = float(bounds.size.width)
        height = float(bounds.size.height)
        margin = 22.0
        self._inspector_title.setFrame_(NSMakeRect(margin, height - 46.0, max(1.0, width - margin * 2), 28.0))
        self._inspector_mode_control.setFrame_(NSMakeRect(margin, height - 88.0, max(1.0, width - margin * 2), 30.0))
        card_y = 38.0
        card_height = max(236.0, min(270.0, height * 0.34))
        scroll_y = card_y + card_height + 14.0
        scroll_height = max(120.0, height - scroll_y - 106.0)
        scroll_frame = NSMakeRect(margin, scroll_y, max(1.0, width - margin * 2), scroll_height)
        self._photo_scroll.setFrame_(scroll_frame)
        self._selection_scroll.setFrame_(scroll_frame)
        self._settings_card.setFrame_(NSMakeRect(margin, card_y, max(1.0, width - margin * 2), card_height))
        card_width = float(self._settings_card.bounds().size.width)
        selected_count_x = max(120.0, card_width - 164.0)
        self._settings_title.setFrame_(NSMakeRect(18.0, card_height - 40.0, max(80.0, selected_count_x - 30.0), 22.0))
        self._selected_count.setFrame_(NSMakeRect(selected_count_x, card_height - 40.0, 146.0, 22.0))
        self._selected_count.setAlignment_(2)
        self._mode.setFrame_(NSMakeRect(18.0, card_height - 82.0, max(1.0, card_width - 36.0), 32.0))
        self._profile_label.setFrame_(NSMakeRect(18.0, card_height - 120.0, 78.0, 20.0))
        self._profile.setFrame_(NSMakeRect(98.0, card_height - 126.0, max(1.0, card_width - 116.0), 30.0))
        self._limit_label.setFrame_(NSMakeRect(18.0, card_height - 164.0, 128.0, 20.0))
        self._limit.setFrame_(NSMakeRect(148.0, card_height - 170.0, max(1.0, card_width - 166.0), 30.0))
        self._run_button.setFrame_(NSMakeRect(18.0, 18.0, max(1.0, card_width - 36.0), 38.0))
        self._run_status.setFrame_(NSMakeRect(margin, 12.0, max(1.0, width - margin * 2), 20.0))
        self._layout_photo_document()
        self._layout_selection_document()

    @objc.python_method
    def _layout_photo_document(self) -> None:
        width = max(1.0, float(self._photo_scroll.contentSize().width))
        viewport_height = max(1.0, float(self._photo_scroll.contentSize().height))
        has_photo = bool(self._focused_photo())
        y = 0.0
        # Preserve room for the filename and key capture details at the minimum
        # window height instead of letting the preview consume the viewport.
        preview_height = max(120.0, min(260.0, width * 0.72, viewport_height * 0.54))
        self._inspector_image.setFrame_(NSMakeRect(0.0, y, width, preview_height))
        self._inspector_empty.setFrame_(NSMakeRect(8.0, 24.0, max(1.0, width - 16.0), 40.0))
        if not has_photo:
            self._photo_document.setFrameSize_(NSMakeSize(width, max(100.0, viewport_height)))
            return
        y += preview_height + 14.0
        self._file_name.setFrame_(NSMakeRect(0.0, y, width, 40.0))
        y += 44.0
        for view in (self._file_date, self._file_size, self._file_resolution):
            view.setFrame_(NSMakeRect(0.0, y, width, 18.0))
            y += 22.0
        self._metadata_summary.setFrame_(NSMakeRect(0.0, y + 4.0, max(1.0, width - 90.0), 22.0))
        self._copy_metadata_button.setFrame_(NSMakeRect(max(0.0, width - 82.0), y, 82.0, 28.0))
        y += 38.0
        self._metadata_status.setFrame_(NSMakeRect(0.0, y, width, 22.0))
        if not self._metadata_status.isHidden():
            y += 28.0
        for kind, view in self._metadata_layout_rows:
            if kind == "section":
                view.setFrame_(NSMakeRect(0.0, y, width, 30.0))
                y += 34.0
            else:
                view.setFrame_(NSMakeRect(12.0, y, max(1.0, width - 12.0), 34.0))
                y += 38.0
        document_height = max(y + 12.0, viewport_height)
        self._photo_document.setFrameSize_(NSMakeSize(width, document_height))

    @objc.python_method
    def _layout_selection_document(self) -> None:
        width = max(1.0, float(self._selection_scroll.contentSize().width))
        y = 0.0
        for kind, view in self._selection_layout_rows:
            height = 42.0 if kind == "summary" else (34.0 if kind in {"header", "more", "empty"} else 58.0)
            view.setFrame_(NSMakeRect(0.0, y, width, height))
            if kind == "summary":
                subviews = list(view.subviews())
                if len(subviews) >= 2:
                    subviews[0].setFrame_(NSMakeRect(0.0, 8.0, max(1.0, width - 92.0), 24.0))
                    subviews[1].setFrame_(NSMakeRect(max(0.0, width - 86.0), 4.0, 86.0, 32.0))
            if kind == "header":
                subviews = list(view.subviews())
                if len(subviews) >= 2:
                    subviews[0].setFrame_(NSMakeRect(0.0, 0.0, max(1.0, width - 88.0), 34.0))
                    subviews[1].setFrame_(NSMakeRect(max(0.0, width - 82.0), 1.0, 82.0, 32.0))
            if kind == "photo":
                subviews = list(view.subviews())
                if len(subviews) >= 3:
                    subviews[0].setFrame_(NSMakeRect(0.0, 4.0, 64.0, 50.0))
                    subviews[1].setFrame_(NSMakeRect(74.0, 8.0, max(1.0, width - 112.0), 40.0))
                    subviews[2].setFrame_(NSMakeRect(max(0.0, width - 32.0), 12.0, 32.0, 32.0))
            y += height + 6.0
        self._selection_document.setFrameSize_(
            NSMakeSize(width, max(y, float(self._selection_scroll.contentSize().height)))
        )

    @objc.python_method
    def _focused_photo(self) -> LocalPhoto | None:
        return next((item for item in self._photos if item.path == self._focused_path), None)

    def inspectorModeChanged_(self, sender) -> None:
        self._inspector_mode = "selection" if int(sender.selectedSegment()) == 1 else "photo"
        self._photo_scroll.setHidden_(self._inspector_mode != "photo")
        self._selection_scroll.setHidden_(self._inspector_mode != "selection")
        self._inspector_title.setStringValue_("선택한 사진" if self._inspector_mode == "selection" else "보고 있는 사진")
        if self._inspector_mode == "selection":
            self._rebuild_selection_document()
        self._layout_inspector()

    @objc.python_method
    def _clear_dynamic_views(self, attribute: str) -> None:
        for view in getattr(self, attribute, []):
            view.removeFromSuperview()
        setattr(self, attribute, [])

    @objc.python_method
    def _rebuild_metadata_document(self) -> None:
        self._clear_dynamic_views("_metadata_dynamic_views")
        self._metadata_layout_rows = []
        metadata = self._metadata_current
        if metadata is None:
            self._layout_photo_document()
            return
        for section in metadata.sections:
            expanded = section.key in self._metadata_section_expanded
            header = self._button(
                self._photo_document,
                f"{'▾' if expanded else '▸'}  {section.title}  {len(section.fields)}",
                "metadataSectionToggled:",
                identifier=section.key,
            )
            header.setAlignment_(0)
            self._metadata_dynamic_views.append(header)
            self._metadata_layout_rows.append(("section", header))
            if not expanded:
                continue
            for field in section.fields:
                value = self._label(self._photo_document, f"{field.label}   {field.value}", 10.5, secondary=True)
                value.setMaximumNumberOfLines_(2)
                value.setToolTip_(f"{field.label}: {field.value}")
                self._metadata_dynamic_views.append(value)
                self._metadata_layout_rows.append(("field", value))
        self._layout_photo_document()

    def metadataSectionToggled_(self, sender) -> None:
        key = str(sender.identifier() or "")
        if key in self._metadata_section_expanded:
            self._metadata_section_expanded.remove(key)
        else:
            self._metadata_section_expanded.add(key)
        self._rebuild_metadata_document()

    def copyMetadata_(self, _sender) -> None:
        if self._metadata_current is None:
            return
        pasteboard = NSPasteboard.generalPasteboard()
        pasteboard.clearContents()
        pasteboard.setString_forType_(
            self._metadata_current.clipboard_text(include_sensitive=False),
            NSPasteboardTypeString,
        )
        self._metadata_status.setStringValue_("경로·위치·일련번호를 제외한 정보를 복사했습니다.")
        self._metadata_status.setHidden_(False)
        self._layout_photo_document()

    @objc.python_method
    def _request_photo_metadata(self, photo: LocalPhoto) -> None:
        key = f"{photo.path}:{int(photo.modified_at)}:{photo.size_bytes}"
        if key == self._metadata_requested_key:
            return
        self._metadata_requested_key = key
        self._metadata_generation += 1
        generation = self._metadata_generation
        cached = self._metadata_cache.get(key)
        if cached is not None:
            self._metadata_cache.move_to_end(key)
            self._apply_photo_metadata(cached)
            return
        self._metadata_current = None
        self._metadata_summary.setStringValue_("")
        self._metadata_status.setStringValue_("카메라와 렌즈 정보를 불러오는 중입니다.")
        self._metadata_status.setHidden_(False)
        self._copy_metadata_button.setHidden_(True)
        self._rebuild_metadata_document()
        if key in self._metadata_inflight:
            return
        self._metadata_inflight.add(key)
        self._thumbnail_executor.submit(self._metadata_worker, key, photo.path, generation)

    @objc.python_method
    def _metadata_worker(self, key: str, path: str, generation: int) -> None:
        self._pending_metadata_results[key] = (generation, extract_local_photo_metadata(path))
        self.performSelectorOnMainThread_withObject_waitUntilDone_("metadataReady:", key, False)

    def metadataReady_(self, payload) -> None:
        key = str(payload)
        self._metadata_inflight.discard(key)
        result = self._pending_metadata_results.pop(key, None)
        if result is None:
            return
        generation, metadata = result
        self._metadata_cache[key] = metadata
        self._metadata_cache.move_to_end(key)
        while len(self._metadata_cache) > 64:
            self._metadata_cache.popitem(last=False)
        if generation != self._metadata_generation or metadata.path != self._focused_path:
            return
        self._apply_photo_metadata(metadata)

    @objc.python_method
    def _apply_photo_metadata(self, metadata: LocalPhotoMetadata) -> None:
        self._metadata_current = metadata
        self._metadata_summary.setStringValue_(metadata.summary or "촬영 정보 없음")
        self._metadata_status.setStringValue_(metadata.error)
        self._metadata_status.setHidden_(not bool(metadata.error))
        self._copy_metadata_button.setHidden_(not bool(metadata.sections))
        self._rebuild_metadata_document()

    @objc.python_method
    def _selected_folder_count(self) -> int:
        return len({str(Path(path).parent) for path in self._selected_paths})

    @objc.python_method
    def _rebuild_selection_document(self) -> None:
        if not hasattr(self, "_selection_document"):
            return
        self._clear_dynamic_views("_selection_dynamic_views")
        self._selection_layout_rows = []
        self._selection_image_views = {}
        groups: dict[str, list[LocalPhoto]] = {}
        for photo in self._selected_photos.values():
            groups.setdefault(str(Path(photo.path).parent), []).append(photo)
        if not groups:
            empty = self._label(self._selection_document, "선택한 사진이 없습니다.", 12.0, secondary=True)
            empty.setAlignment_(1)
            self._selection_dynamic_views.append(empty)
            self._selection_layout_rows.append(("empty", empty))
            self._layout_selection_document()
            return
        summary = NSView.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, 1.0, 1.0))
        summary_label = self._label(
            summary,
            f"전체 {len(self._selected_paths)}장 · {len(groups)}개 폴더",
            12.0,
            bold=True,
        )
        clear_all = self._button(summary, "전체 해제", "clearAllSelection:")
        self._selection_document.addSubview_(summary)
        self._selection_dynamic_views.append(summary)
        self._selection_layout_rows.append(("summary", summary))
        for folder in sorted(groups, key=str.casefold):
            photos = sorted(groups[folder], key=lambda item: item.name.casefold())
            if not self._selection_expanded_folders:
                self._selection_expanded_folders.add(folder)
            expanded = folder in self._selection_expanded_folders
            header = NSView.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, 1.0, 1.0))
            toggle = self._button(
                header,
                f"{'▾' if expanded else '▸'}  {Path(folder).name or folder} · {len(photos)}장",
                "toggleSelectionGroup:",
                identifier=folder,
            )
            toggle.setAlignment_(0)
            self._button(header, "폴더 보기", "showSelectionFolder:", identifier=folder)
            self._selection_document.addSubview_(header)
            self._selection_dynamic_views.append(header)
            self._selection_layout_rows.append(("header", header))
            if not expanded:
                continue
            for photo in photos[:60]:
                row = NSView.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, 1.0, 1.0))
                image = NSImageView.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, 1.0, 1.0))
                image.setImageScaling_(NSImageScaleProportionallyUpOrDown)
                image.setImage_(self.thumbnail_for(photo, 160))
                row.addSubview_(image)
                modified = datetime.fromtimestamp(photo.modified_at).strftime("%Y. %m. %d. %H:%M")
                label = self._label(row, f"{photo.name}\n{modified}", 10.5)
                label.setMaximumNumberOfLines_(2)
                remove = self._button(row, "×", "removeSelectedPhoto:", identifier=photo.path, accessibility_label=f"{photo.name} 선택 해제")
                self._selection_document.addSubview_(row)
                self._selection_image_views.setdefault(photo.path, []).append(image)
                self._selection_dynamic_views.append(row)
                self._selection_layout_rows.append(("photo", row))
            if len(photos) > 60:
                more = self._label(self._selection_document, f"이 폴더에 {len(photos) - 60}장 더 선택되어 있습니다.", 10.5, secondary=True)
                self._selection_dynamic_views.append(more)
                self._selection_layout_rows.append(("more", more))
        self._layout_selection_document()

    def toggleSelectionGroup_(self, sender) -> None:
        folder = str(sender.identifier() or "")
        if folder in self._selection_expanded_folders:
            self._selection_expanded_folders.remove(folder)
        else:
            self._selection_expanded_folders.add(folder)
        self._rebuild_selection_document()

    def showSelectionFolder_(self, sender) -> None:
        folder = str(sender.identifier() or "")
        if not folder:
            return
        self._set_current_folder(folder, add_history=True)

    def removeSelectedPhoto_(self, sender) -> None:
        self._set_photo_checked(str(sender.identifier() or ""), False, allow_external=True)

    def clearAllSelection_(self, _sender) -> None:
        self._selected_paths.clear()
        self._selected_photos.clear()
        self._refresh_visible_checkboxes()
        self._sync_collection_selection()

    @objc.python_method
    def _update_collection_layout(self) -> None:
        width = float(self._collection_scroll.bounds().size.width)
        if width <= 1.0:
            return
        spacing = 12.0
        preferred = _DENSITY_WIDTHS[self._density_index]
        columns = max(3, min(6, int((width + spacing) // (preferred + spacing))))
        item_width = max(100.0, (width - spacing * (columns - 1) - 6.0) / columns)
        self._flow_layout.setItemSize_(NSMakeSize(item_width, max(116.0, item_width * 0.74)))
        self._density_smaller.setEnabled_(self._density_index > 0)
        self._density_larger.setEnabled_(self._density_index < len(_DENSITY_WIDTHS) - 1)
        self._flow_layout.invalidateLayout()

    def numberOfSectionsInCollectionView_(self, _collection_view) -> int:
        return 1

    def collectionView_numberOfItemsInSection_(self, _collection_view, _section: int) -> int:
        return len(self._visible_photos())

    def collectionView_itemForRepresentedObjectAtIndexPath_(self, collection_view, index_path):
        item = collection_view.makeItemWithIdentifier_forIndexPath_(_ITEM_IDENTIFIER, index_path)
        photos = self._visible_photos()
        index = int(index_path.item())
        if 0 <= index < len(photos):
            item.configure(photos[index], self)
        return item

    def collectionView_didSelectItemsAtIndexPaths_(self, _collection_view, index_paths) -> None:
        paths = list(index_paths or [])
        if paths:
            photos = self._visible_photos()
            index = int(paths[0].item())
            if 0 <= index < len(photos):
                self._focused_path = photos[index].path
        self._update_inspector()

    def collectionView_didDeselectItemsAtIndexPaths_(self, _collection_view, _index_paths) -> None:
        self._update_inspector()

    def openFocusedPhoto_(self, _sender) -> None:
        if not self._focused_path:
            return
        self._view_mode_control.setSelectedSegment_(1)
        self.viewModeChanged_(self._view_mode_control)

    def viewModeChanged_(self, sender) -> None:
        visible = self._visible_photos()
        wants_single = sender.selectedSegment() == 1 and bool(visible)
        self._view_mode = "single" if wants_single else "grid"
        self._view_mode_control.setSelectedSegment_(1 if wants_single else 0)
        if wants_single and self._focused_path not in {photo.path for photo in visible}:
            self._focused_path = visible[0].path
        self._sync_view_mode()
        self._layout_content()
        if wants_single:
            self.window().makeFirstResponder_(self._single_view)

    def showPreviousPhoto_(self, _sender) -> None:
        self._move_single_photo(-1)

    def showNextPhoto_(self, _sender) -> None:
        self._move_single_photo(1)

    def singleZoomIn_(self, _sender) -> None:
        self.zoom_by_factor_at_view_point(
            _SINGLE_ZOOM_STEP,
            self._single_visible_center_point(),
        )
        self.window().makeFirstResponder_(self._single_image)

    def singleZoomOut_(self, _sender) -> None:
        self.zoom_by_factor_at_view_point(
            1.0 / _SINGLE_ZOOM_STEP,
            self._single_visible_center_point(),
        )
        self.window().makeFirstResponder_(self._single_image)

    def singleFitPhoto_(self, _sender) -> None:
        self._single_apply_fit_zoom()
        self.window().makeFirstResponder_(self._single_image)

    def singleActualSize_(self, _sender) -> None:
        self._single_set_zoom_at_view_point(1.0, self._single_visible_center_point())
        self.window().makeFirstResponder_(self._single_image)

    @objc.python_method
    def zoom_by_factor_at_view_point(self, factor: float, view_point) -> None:
        self._single_set_zoom_at_view_point(self._single_zoom_factor * factor, view_point)

    @objc.python_method
    def toggle_zoom_at_view_point(self, view_point) -> None:
        if not self._single_is_fit:
            self._single_apply_fit_zoom()
            return
        target_zoom = max(
            1.0,
            self._single_zoom_factor * _SINGLE_DOUBLE_CLICK_ZOOM_STEP,
        )
        self._single_set_zoom_at_view_point(target_zoom, view_point, center_anchor=True)

    @objc.python_method
    def _single_set_zoom_at_view_point(
        self,
        requested_zoom: float,
        view_point,
        *,
        center_anchor: bool = False,
    ) -> None:
        if float(self._single_image_size.width) <= 0.0:
            return
        minimum_zoom = self._single_minimum_manual_zoom()
        target_zoom = max(minimum_zoom, min(_SINGLE_MAX_ZOOM, requested_zoom))
        if target_zoom <= minimum_zoom + _SINGLE_ZOOM_EPSILON:
            self._single_apply_fit_zoom()
            return
        image_point = self._single_image_point_at_document_point(view_point)
        clip_view = self._single_scroll.contentView()
        clip_bounds = clip_view.bounds()
        if center_anchor:
            anchor_in_clip = NSMakePoint(
                float(clip_bounds.size.width) / 2.0,
                float(clip_bounds.size.height) / 2.0,
            )
        else:
            anchor_in_clip = NSMakePoint(
                float(view_point.x) - float(clip_bounds.origin.x),
                float(view_point.y) - float(clip_bounds.origin.y),
            )
        self._single_layout_document(target_zoom, center_margin=True)
        target_document_point = self._single_document_point_for_image_point(image_point)
        self._single_scroll_to_origin(
            NSMakePoint(
                float(target_document_point.x) - float(anchor_in_clip.x),
                float(target_document_point.y) - float(anchor_in_clip.y),
            )
        )
        self._single_is_fit = False
        self._single_update_zoom_controls()

    @objc.python_method
    def can_pan_image(self) -> bool:
        clip_bounds = self._single_scroll.contentView().bounds()
        document_bounds = self._single_image.bounds()
        return not self._single_is_fit and (
            float(document_bounds.size.width) > float(clip_bounds.size.width) + 0.5
            or float(document_bounds.size.height) > float(clip_bounds.size.height) + 0.5
        )

    @objc.python_method
    def is_panning_image(self) -> bool:
        return (
            self._single_pan_start_window_point is not None
            and self._single_pan_start_scroll_origin is not None
        )

    @objc.python_method
    def begin_pan_at_window_point(self, window_point) -> None:
        if not self.can_pan_image():
            return
        self._single_pan_start_window_point = window_point
        self._single_pan_start_scroll_origin = self._single_scroll.contentView().bounds().origin

    @objc.python_method
    def pan_image_to_window_point(self, window_point) -> None:
        if not self.is_panning_image():
            return
        start = self._single_pan_start_window_point
        origin = self._single_pan_start_scroll_origin
        self._single_scroll_to_origin(
            NSMakePoint(
                float(origin.x) - (float(window_point.x) - float(start.x)),
                float(origin.y) + (float(window_point.y) - float(start.y)),
            )
        )

    @objc.python_method
    def end_pan(self) -> None:
        self._single_pan_start_window_point = None
        self._single_pan_start_scroll_origin = None

    @objc.python_method
    def _single_apply_fit_zoom(self) -> None:
        self.end_pan()
        self._single_is_fit = True
        if float(self._single_image_size.width) <= 0.0 or float(self._single_image_size.height) <= 0.0:
            self._single_update_zoom_controls()
            return
        clip_size = self._single_scroll.contentSize()
        fit_zoom = min(
            float(clip_size.width) / float(self._single_image_size.width),
            float(clip_size.height) / float(self._single_image_size.height),
        )
        self._single_fit_zoom_factor = max(_SINGLE_ABSOLUTE_MIN_ZOOM, fit_zoom)
        self._single_layout_document(self._single_fit_zoom_factor, center_margin=False)
        self._single_scroll_to_origin(NSMakePoint(0.0, 0.0))
        self._single_update_zoom_controls()

    @objc.python_method
    def _single_minimum_manual_zoom(self) -> float:
        return max(_SINGLE_ABSOLUTE_MIN_ZOOM, self._single_fit_zoom_factor)

    @objc.python_method
    def _single_visible_center_point(self):
        bounds = self._single_scroll.contentView().bounds()
        return NSMakePoint(
            float(bounds.origin.x) + float(bounds.size.width) / 2.0,
            float(bounds.origin.y) + float(bounds.size.height) / 2.0,
        )

    @objc.python_method
    def _single_visible_image_center(self):
        return self._single_image_point_at_document_point(self._single_visible_center_point())

    @objc.python_method
    def _single_update_zoom_controls(self) -> None:
        if not hasattr(self, "_single_zoom_out_button"):
            return
        has_image = float(self._single_image_size.width) > 0.0
        self._single_zoom_out_button.setEnabled_(
            has_image
            and self._single_zoom_factor > self._single_minimum_manual_zoom() + _SINGLE_ZOOM_EPSILON
        )
        self._single_zoom_in_button.setEnabled_(
            has_image and self._single_zoom_factor < _SINGLE_MAX_ZOOM - _SINGLE_ZOOM_EPSILON
        )
        self._single_fit_button.setEnabled_(has_image and not self._single_is_fit)
        self._single_actual_button.setEnabled_(has_image)

    @objc.python_method
    def _single_layout_document(
        self,
        zoom_factor: float,
        *,
        center_margin: bool | None = None,
    ) -> None:
        self._single_zoom_factor = zoom_factor
        clip_size = self._single_scroll.contentSize()
        scaled_width = max(1.0, float(self._single_image_size.width) * zoom_factor)
        scaled_height = max(1.0, float(self._single_image_size.height) * zoom_factor)
        if center_margin is None:
            center_margin = not self._single_is_fit
        horizontal_margin = float(clip_size.width) if center_margin else 0.0
        vertical_margin = float(clip_size.height) if center_margin else 0.0
        document_width = max(float(clip_size.width), scaled_width + horizontal_margin)
        document_height = max(float(clip_size.height), scaled_height + vertical_margin)
        self._single_image.setFrameSize_(NSMakeSize(document_width, document_height))
        self._single_display_rect = NSMakeRect(
            (document_width - scaled_width) / 2.0,
            (document_height - scaled_height) / 2.0,
            scaled_width,
            scaled_height,
        )
        self._single_image.set_display_rect(self._single_display_rect)

    @objc.python_method
    def _single_image_point_at_document_point(self, document_point):
        zoom_factor = max(_SINGLE_ABSOLUTE_MIN_ZOOM, self._single_zoom_factor)
        return NSMakePoint(
            (float(document_point.x) - float(self._single_display_rect.origin.x)) / zoom_factor,
            (float(document_point.y) - float(self._single_display_rect.origin.y)) / zoom_factor,
        )

    @objc.python_method
    def _single_document_point_for_image_point(self, image_point):
        return NSMakePoint(
            float(self._single_display_rect.origin.x) + float(image_point.x) * self._single_zoom_factor,
            float(self._single_display_rect.origin.y) + float(image_point.y) * self._single_zoom_factor,
        )

    @objc.python_method
    def _single_center_image_point(self, image_point) -> None:
        document_point = self._single_document_point_for_image_point(image_point)
        clip_size = self._single_scroll.contentSize()
        self._single_scroll_to_origin(
            NSMakePoint(
                float(document_point.x) - float(clip_size.width) / 2.0,
                float(document_point.y) - float(clip_size.height) / 2.0,
            )
        )

    @objc.python_method
    def _single_scroll_to_origin(self, requested_origin) -> None:
        clip_view = self._single_scroll.contentView()
        clip_size = clip_view.bounds().size
        document_size = self._single_image.bounds().size
        maximum_x = max(0.0, float(document_size.width) - float(clip_size.width))
        maximum_y = max(0.0, float(document_size.height) - float(clip_size.height))
        origin = NSMakePoint(
            max(0.0, min(maximum_x, float(requested_origin.x))),
            max(0.0, min(maximum_y, float(requested_origin.y))),
        )
        clip_view.scrollToPoint_(origin)
        self._single_scroll.reflectScrolledClipView_(clip_view)

    @objc.python_method
    def _single_set_photo_image(self, image) -> None:
        self.end_pan()
        self._single_image.set_photo_image(image)
        self._single_image_size = self._single_image.image_size()
        if image is None:
            self._single_display_rect = NSMakeRect(0.0, 0.0, 0.0, 0.0)
            self._single_image.setFrameSize_(self._single_scroll.contentSize())
            self._single_image.set_display_rect(self._single_display_rect)
            self._single_is_fit = True
            self._single_update_zoom_controls()
            return
        self._single_is_fit = True
        self._single_apply_fit_zoom()

    def toggleFocusedPhotoCheck_(self, sender) -> None:
        self._set_photo_checked(
            self._focused_path,
            sender.state() == NSControlStateValueOn,
        )

    def toggleFocusedPhotoFromKeyboard_(self, _sender) -> None:
        if not self._focused_path:
            return
        self._set_photo_checked(
            self._focused_path,
            self._focused_path not in self._selected_paths,
        )
        responder = self._single_view if self._view_mode == "single" else self._collection
        self.window().makeFirstResponder_(responder)

    def toggleGridPhotoFromKeyboard_(self, sender) -> None:
        selected_indexes = list(sender.selectionIndexPaths() or [])
        visible = self._visible_photos()
        if selected_indexes:
            index = int(selected_indexes[0].item())
            if 0 <= index < len(visible):
                self._focused_path = visible[index].path
        focused_path = self._focused_path
        if not focused_path:
            return
        self._set_photo_checked(
            focused_path,
            focused_path not in self._selected_paths,
        )
        # Collection selection callbacks can run while checkbox state refreshes.
        # Restore the keyboard target so Inspector and selection stay in sync.
        self._focused_path = focused_path
        self._restore_focus_selection()
        self._update_inspector()
        self.window().makeFirstResponder_(self._collection)

    @objc.python_method
    def _move_single_photo(self, offset: int) -> None:
        visible = self._visible_photos()
        paths = [photo.path for photo in visible]
        if self._focused_path not in paths:
            return
        next_index = max(0, min(paths.index(self._focused_path) + offset, len(paths) - 1))
        self._focused_path = paths[next_index]
        self._restore_focus_selection()
        self._update_inspector()
        self._sync_single_view()
        if self._view_mode == "single":
            self.window().makeFirstResponder_(self._single_view)

    def outlineView_numberOfChildrenOfItem_(self, _outline, item) -> int:
        if item is None:
            return len(self._root_nodes)
        if item.kind == "loading":
            return 0
        if item.kind == "group":
            return len(self._folder_children.get(item.key, []))
        self._ensure_folder_children(item)
        return len(self._folder_children.get(item.key, []))

    def outlineView_child_ofItem_(self, _outline, index: int, item):
        nodes = self._root_nodes if item is None else self._folder_children.get(item.key, [])
        return nodes[index]

    def outlineView_isItemExpandable_(self, _outline, item) -> bool:
        if item.kind == "group":
            return bool(self._folder_children.get(item.key))
        return item.kind == "folder" and Path(item.path).is_dir()

    def outlineView_objectValueForTableColumn_byItem_(self, _outline, _column, item):
        return item.title

    def outlineView_viewForTableColumn_item_(self, _outline, _column, item):
        row = NSView.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, 1.0, 44.0))
        row.setIdentifier_("folder-row")
        row.setAccessibilityLabel_(self.outlineView_objectValueForTableColumn_byItem_(None, None, item))
        label_x = 8.0
        if item.kind == "folder":
            symbol = NSImage.imageWithSystemSymbolName_accessibilityDescription_("folder", item.title)
            if symbol is not None:
                symbol_configuration = NSImageSymbolConfiguration.configurationWithPointSize_weight_(
                    _FOLDER_ICON_SIZE,
                    NSFontWeightRegular,
                )
                symbol = symbol.imageWithSymbolConfiguration_(symbol_configuration)
                symbol.setTemplate_(True)
                icon = NSImageView.alloc().initWithFrame_(
                    NSMakeRect(8.0, 12.0, _FOLDER_ICON_SIZE, _FOLDER_ICON_SIZE)
                )
                icon.setImage_(symbol)
                icon.setImageScaling_(NSImageScaleProportionallyUpOrDown)
                icon.setAccessibilityElement_(False)
                row.addSubview_(icon)
            label_x = 36.0
        label = NSTextField.labelWithString_(self.outlineView_objectValueForTableColumn_byItem_(None, None, item))
        label.setFrame_(NSMakeRect(label_x, 10.0, 1.0, 24.0))
        label.setAutoresizingMask_(NSViewWidthSizable)
        label.setFont_(app_font(12.0, "semibold" if item.kind == "group" else "regular"))
        label.setTextColor_(NSColor.secondaryLabelColor() if item.kind == "group" else NSColor.labelColor())
        label.setLineBreakMode_(5)
        label.setToolTip_(str(label.stringValue() or ""))
        row.addSubview_(label)
        return row

    def outlineViewSelectionDidChange_(self, notification) -> None:
        outline = notification.object()
        item = outline.itemAtRow_(outline.selectedRow())
        if item is None or item.kind != "folder" or not Path(item.path).is_dir():
            return
        if item.path == self._current_folder:
            return
        self._navigate_to_folder(item.path)

    def outlineViewItemWillExpand_(self, notification) -> None:
        item = notification.userInfo().get("NSObject")
        if item is not None and item.kind == "folder":
            self._ensure_folder_children(item)

    def goBack_(self, _sender) -> None:
        if self._history_index <= 0:
            return
        self._history_index -= 1
        self._set_current_folder(self._history[self._history_index], add_history=False)
        self._update_history_controls()

    def goForward_(self, _sender) -> None:
        if self._history_index + 1 >= len(self._history):
            return
        self._history_index += 1
        self._set_current_folder(self._history[self._history_index], add_history=False)
        self._update_history_controls()

    def requestFolderAccess_(self, _sender) -> None:
        panel = NSOpenPanel.openPanel()
        panel.setTitle_("접근할 사진 폴더 추가")
        panel.setMessage_("접근 권한이 필요한 사진 폴더만 추가하세요.")
        panel.setPrompt_("위치 추가")
        panel.setCanChooseFiles_(False)
        panel.setCanChooseDirectories_(True)
        panel.setAllowsMultipleSelection_(False)
        if panel.runModal() != NSModalResponseOK or not panel.URLs():
            return
        self._set_current_folder(str(panel.URLs()[0].path()), add_history=True)

    def searchChanged_(self, _sender) -> None:
        self._collection.reloadData()
        self._restore_focus_selection()
        self._sync_collection_selection()

    def scopeChanged_(self, _sender) -> None:
        self._load_current_folder(clear_selection=False)

    def sortChanged_(self, _sender) -> None:
        self._sort_photos()
        self._collection.reloadData()
        self._restore_focus_selection()
        self._sync_collection_selection()

    def limitChanged_(self, _sender) -> None:
        self._sync_collection_selection()

    def changeDensity_(self, sender) -> None:
        identifier = str(sender.identifier() or "")
        if identifier == "smaller":
            self._density_index = max(0, self._density_index - 1)
        else:
            self._density_index = min(len(_DENSITY_WIDTHS) - 1, self._density_index + 1)
        self._update_collection_layout()

    def selectAllPhotos_(self, _sender) -> None:
        for photo in self._visible_photos():
            self._selected_paths.add(photo.path)
            self._selected_photos[photo.path] = photo
            self._selection_expanded_folders.add(str(Path(photo.path).parent))
        self._refresh_visible_checkboxes()
        self._sync_collection_selection()

    def clearSelection_(self, _sender) -> None:
        visible_paths = {photo.path for photo in self._visible_photos()}
        self._selected_paths.difference_update(visible_paths)
        for path in visible_paths:
            self._selected_photos.pop(path, None)
        self._refresh_visible_checkboxes()
        self._sync_collection_selection()

    def togglePhotoCheck_(self, sender) -> None:
        path = str(sender.identifier() or "")
        self._set_photo_checked(path, sender.state() == NSControlStateValueOn)

    @objc.python_method
    def _set_photo_checked(self, path: str, checked: bool, *, allow_external: bool = False) -> None:
        photo = next((item for item in self._photos if item.path == path), None)
        if not path or (photo is None and not allow_external):
            return
        if checked:
            self._selected_paths.add(path)
            if photo is not None:
                self._selected_photos[path] = photo
                self._selection_expanded_folders.add(str(Path(path).parent))
        else:
            self._selected_paths.discard(path)
            self._selected_photos.pop(path, None)
        self._refresh_visible_checkboxes()
        self._sync_collection_selection()

    @objc.python_method
    def is_photo_checked(self, path: str) -> bool:
        return path in self._selected_paths

    def startClassification_(self, _sender) -> None:
        if not self._selected_paths or self._thread_alive(self._run_worker):
            return
        limit = self._selection_limit()
        profile = {"일반": "general", "인물": "person", "풍경": "landscape"}.get(
            str(self._profile.titleOfSelectedItem() or "일반"), "general"
        )
        try:
            command = ClassificationCommand(
                source="local",
                source_path=common_local_source_path(tuple(sorted(self._selected_paths))),
                selected_photo_ids=tuple(sorted(self._selected_paths)),
                mode="classify" if self._mode.selectedSegment() == 0 else "select_best",
                selection_profile=profile,
                limit=limit,
            )
            command.validate()
        except Exception as exc:
            self._run_status.setStringValue_(str(exc))
            return
        self._run_button.setEnabled_(False)
        self._run_status.setStringValue_("선택한 사진 분류 작업을 시작하고 있습니다.")
        self._run_worker = Thread(target=self._run_worker_main, args=(command,), name="photos-mcp-local-browser-run", daemon=True)
        self._run_worker.start()

    @objc.python_method
    def _run_worker_main(self, command: ClassificationCommand) -> None:
        try:
            self._pending_run = self._run_async(self._service.execute(command))
        except Exception as exc:
            self._pending_run = {"error": str(exc)}
        self.performSelectorOnMainThread_withObject_waitUntilDone_("classificationStarted:", None, False)

    def classificationStarted_(self, _payload) -> None:
        self._run_worker = None
        if self._pending_run.get("error") or self._pending_run.get("error_code"):
            self._sync_collection_selection()
            self._run_status.setStringValue_(str(self._pending_run.get("error") or "분류 작업을 시작하지 못했습니다."))
            return
        daemon = getattr(self._menu_controller, "_daemon_controller", None)
        if daemon is not None and hasattr(daemon, "refresh_jobs_once"):
            daemon.refresh_jobs_once()
        self._sync_collection_selection()
        self._run_status.setStringValue_("작업을 시작했습니다. 작업 기록에서 진행 상황과 결과를 확인할 수 있습니다.")

    @objc.python_method
    def _navigate_to_folder(self, path: str) -> None:
        self._set_current_folder(path, add_history=True)

    @objc.python_method
    def _set_current_folder(self, path: str, *, add_history: bool) -> None:
        folder = Path(path).expanduser()
        if not folder.is_dir():
            self._sidebar_status.setStringValue_("이 폴더에 접근할 수 없습니다.")
            return
        resolved = str(folder.resolve())
        self._current_folder = resolved
        if add_history and (not self._history or self._history[self._history_index] != resolved):
            self._history = self._history[: self._history_index + 1] + [resolved]
            self._history_index = len(self._history) - 1
        self._update_history_controls()
        self._sync_outline_selection()
        self._load_current_folder(clear_selection=False)

    @objc.python_method
    def _sync_outline_selection(self) -> None:
        for nodes in self._folder_children.values():
            for node in nodes:
                if node.kind != "folder" or node.path != self._current_folder:
                    continue
                row = self._outline.rowForItem_(node)
                if row >= 0 and self._outline.selectedRow() != row:
                    self._outline.selectRowIndexes_byExtendingSelection_(
                        NSIndexSet.indexSetWithIndex_(row),
                        False,
                    )
                return
        self._outline.deselectAll_(None)

    @objc.python_method
    def _load_current_folder(self, *, clear_selection: bool) -> None:
        self._photo_generation += 1
        generation = self._photo_generation
        folder = self._current_folder
        if clear_selection:
            self._focused_path = ""
        self._folder_title.setStringValue_(f"{Path(folder).name or folder} · 사진 불러오는 중")
        self._empty_label.setStringValue_("사진 목록을 불러오는 중입니다.")
        self._empty_label.setHidden_(False)
        self._collection_scroll.setHidden_(True)
        self._sync_collection_selection()
        include_subfolders = self._include_subfolders.state() == NSControlStateValueOn
        worker = Thread(
            target=self._photo_scan_worker,
            args=(generation, folder, include_subfolders),
            name="photos-mcp-local-photo-scan",
            daemon=True,
        )
        worker.start()

    @objc.python_method
    def _photo_scan_worker(self, generation: int, folder: str, include_subfolders: bool) -> None:
        photos = _scan_local_photos(folder, include_subfolders)
        self._pending_photo_results = (generation, folder, photos)
        self.performSelectorOnMainThread_withObject_waitUntilDone_("photoScanFinished:", None, False)

    def photoScanFinished_(self, _payload) -> None:
        if self._pending_photo_results is None:
            return
        generation, folder, photos = self._pending_photo_results
        if generation != self._photo_generation or folder != self._current_folder:
            return
        self._photos = photos
        self._folder_counts[folder] = len(photos)
        self._sort_photos()
        self._folder_title.setStringValue_(f"{Path(folder).name or folder} · 사진 {len(photos)}장")
        self._sidebar_status.setStringValue_("준비")
        self._outline.reloadData()
        self._sync_outline_selection()
        self._collection.reloadData()
        self._collection_scroll.setHidden_(not bool(self._visible_photos()))
        self._empty_label.setHidden_(bool(self._visible_photos()))
        if not self._visible_photos():
            self._empty_label.setStringValue_("이 폴더에서 지원되는 사진을 찾지 못했습니다.")
        valid_paths = {photo.path for photo in photos}
        for photo in photos:
            if photo.path in self._selected_paths:
                self._selected_photos[photo.path] = photo
        if self._focused_path not in valid_paths:
            self._focused_path = photos[0].path if photos else ""
        self._restore_focus_selection()
        self._sync_collection_selection()

    @objc.python_method
    def _sort_photos(self) -> None:
        selected = str(self._sort_popup.titleOfSelectedItem() or "")
        if selected == "파일 날짜: 오래된순":
            self._photos.sort(key=lambda photo: (photo.modified_at, photo.name.casefold()))
        elif selected == "파일명":
            self._photos.sort(key=lambda photo: photo.name.casefold())
        elif selected == "파일 크기":
            self._photos.sort(key=lambda photo: (-photo.size_bytes, photo.name.casefold()))
        else:
            self._photos.sort(key=lambda photo: (-photo.modified_at, photo.name.casefold()))

    @objc.python_method
    def _visible_photos(self) -> list[LocalPhoto]:
        query = str(self._search_field.stringValue() or "").strip().casefold()
        return self._photos if not query else [photo for photo in self._photos if query in photo.name.casefold()]

    @objc.python_method
    def _sync_collection_selection(self) -> None:
        visible = self._visible_photos()
        selected_count = len(self._selected_paths)
        visible_selected_count = sum(photo.path in self._selected_paths for photo in visible)
        folder_count = self._selected_folder_count()
        query_active = bool(str(self._search_field.stringValue() or "").strip())
        prefix = "검색 결과" if query_active else "사진"
        self._selection_label.setStringValue_(
            f"{prefix} {len(visible)}장 · 현재 보기 선택 {visible_selected_count}장 · 전체 선택 {selected_count}장 / {folder_count}개 폴더"
        )
        self._select_all_button.setTitle_("검색 결과 전체 선택" if query_active else "현재 보기 전체 선택")
        self._clear_button.setTitle_("검색 결과 선택 해제" if query_active else "현재 보기 선택 해제")
        self._selected_count.setStringValue_(f"분류 대상 {selected_count}장")
        self._inspector_mode_control.setLabel_forSegment_(f"선택 목록 {selected_count}", 1)
        limit = self._selection_limit()
        within_limit = selected_count <= limit
        can_run = bool(selected_count) and within_limit and not self._thread_alive(self._run_worker)
        self._run_button.setEnabled_(can_run)
        run_title = f"선택한 {selected_count}장 분류" if selected_count else "사진을 선택하세요"
        self._run_button.setTitle_(run_title)
        self._run_button.setAccessibilityLabel_(run_title)
        self._run_button.setToolTip_(run_title)
        self._select_all_button.setEnabled_(bool(visible))
        self._clear_button.setEnabled_(bool(visible_selected_count))
        if selected_count > limit:
            over = selected_count - limit
            self._run_status.setStringValue_(
                f"최대 {limit}장보다 {over}장 많습니다. 선택을 줄이거나 최대 처리 수를 늘리세요."
            )
        elif not self._thread_alive(self._run_worker):
            self._run_status.setStringValue_("원본 파일은 변경하지 않습니다.")
        self._rebuild_selection_document()
        self._update_inspector()
        self._sync_view_mode()

    @objc.python_method
    def _sync_view_mode(self) -> None:
        visible = self._visible_photos()
        if self._view_mode == "single" and not visible:
            self._view_mode = "grid"
            self._view_mode_control.setSelectedSegment_(0)
        single = self._view_mode == "single"
        self._collection_scroll.setHidden_(single or not bool(visible))
        self._single_view.setHidden_(not single)
        self._empty_label.setHidden_(single or bool(visible))
        self._sync_single_view()

    @objc.python_method
    def _sync_single_view(self) -> None:
        visible = self._visible_photos()
        paths = [photo.path for photo in visible]
        if self._focused_path not in paths:
            self._single_displayed_thumbnail_key = ""
            self._single_set_photo_image(None)
            self._single_filename.setStringValue_("")
            self._single_counter.setStringValue_("")
            self._previous_photo_button.setEnabled_(False)
            self._next_photo_button.setEnabled_(False)
            self._single_check_button.setEnabled_(False)
            return
        index = paths.index(self._focused_path)
        photo = visible[index]
        thumbnail_key = _thumbnail_cache_key(photo, 1600)
        if thumbnail_key != self._single_displayed_thumbnail_key:
            self._single_displayed_thumbnail_key = thumbnail_key
            self._single_set_photo_image(self.thumbnail_for(photo, 1600))
        self._single_filename.setStringValue_(photo.name)
        self._single_filename.setToolTip_(photo.name)
        self._single_counter.setStringValue_(f"{index + 1} / {len(visible)}")
        self._previous_photo_button.setEnabled_(index > 0)
        self._next_photo_button.setEnabled_(index + 1 < len(visible))
        self._single_check_button.setEnabled_(True)
        self._single_check_button.setState_(
            NSControlStateValueOn if photo.path in self._selected_paths else NSControlStateValueOff
        )
        self._single_check_button.setAccessibilityLabel_(f"{photo.name} 분류 대상으로 선택")

    @objc.python_method
    def _selection_limit(self) -> int:
        return int(str(self._limit.titleOfSelectedItem() or "50장").replace("장", ""))

    @objc.python_method
    def _refresh_visible_checkboxes(self) -> None:
        for item in self._collection.visibleItems():
            if isinstance(item, PhotosMcpLocalPhotoItem):
                item.refresh_checked_state()

    @objc.python_method
    def _restore_focus_selection(self) -> None:
        self._collection.deselectAll_(None)
        visible = self._visible_photos()
        for index, photo in enumerate(visible):
            if photo.path != self._focused_path:
                continue
            index_path = NSIndexPath.indexPathForItem_inSection_(index, 0)
            self._collection.selectItemsAtIndexPaths_scrollPosition_(
                NSSet.setWithObject_(index_path), 0
            )
            break

    @objc.python_method
    def _update_history_controls(self) -> None:
        self._back_button.setEnabled_(self._history_index > 0)
        self._forward_button.setEnabled_(self._history_index + 1 < len(self._history))

    @objc.python_method
    def _update_inspector(self) -> None:
        photo = self._focused_photo()
        if photo is None:
            self._metadata_generation += 1
            self._metadata_requested_key = ""
            self._metadata_current = None
            self._inspector_image.setImage_(None)
            self._inspector_image.setHidden_(True)
            self._inspector_empty.setHidden_(False)
            self._file_name.setStringValue_("")
            self._file_date.setStringValue_("")
            self._file_size.setStringValue_("")
            self._file_resolution.setStringValue_("")
            self._metadata_summary.setStringValue_("")
            self._metadata_status.setStringValue_("")
            self._metadata_status.setHidden_(True)
            self._copy_metadata_button.setHidden_(True)
            self._rebuild_metadata_document()
            self._layout_inspector()
            return
        self._inspector_image.setHidden_(False)
        self._inspector_empty.setHidden_(True)
        self._file_name.setStringValue_(photo.name)
        self._file_date.setStringValue_(datetime.fromtimestamp(photo.modified_at).strftime("%Y. %m. %d. %H:%M"))
        self._file_size.setStringValue_(f"{photo.size_bytes / (1024 * 1024):.1f}MB")
        resolution = f"{photo.pixel_width} × {photo.pixel_height} px" if photo.pixel_width and photo.pixel_height else "해상도 정보 없음"
        self._file_resolution.setStringValue_(resolution)
        self._inspector_image.setImage_(self.thumbnail_for(photo, 900))
        self._layout_inspector()
        self._request_photo_metadata(photo)

    @objc.python_method
    def _ensure_folder_children(self, node: FolderNode) -> None:
        if node.key in self._folder_children or node.key in self._folder_loads_inflight:
            return
        self._folder_loads_inflight.add(node.key)
        self._folder_children[node.key] = [FolderNode(key=f"loading:{node.key}", title="불러오는 중…", kind="loading")]
        worker = Thread(target=self._folder_scan_worker, args=(node,), name="photos-mcp-local-folder-scan", daemon=True)
        worker.start()

    @objc.python_method
    def _folder_scan_worker(self, node: FolderNode) -> None:
        self._pending_folder_results[node.key] = _folder_nodes_for_path(Path(node.path))
        self.performSelectorOnMainThread_withObject_waitUntilDone_("folderScanFinished:", node.key, False)

    def folderScanFinished_(self, key) -> None:
        key = str(key)
        children = self._pending_folder_results.pop(key, [])
        self._folder_children[key] = children
        self._folder_loads_inflight.discard(key)
        self._outline.reloadData()

    @objc.python_method
    def thumbnail_pixels_for_visible_item(self) -> int:
        return max(160, int(_DENSITY_WIDTHS[self._density_index] * 2.0))

    @objc.python_method
    def thumbnail_for(self, photo: LocalPhoto, max_pixels: int) -> Any | None:
        key = _thumbnail_cache_key(photo, max_pixels)
        cached = _THUMBNAIL_CACHE.objectForKey_(key)
        if cached is not None:
            return cached
        if key not in self._thumbnail_failures and key not in self._thumbnail_inflight:
            self._thumbnail_inflight.add(key)
            self._thumbnail_executor.submit(self._thumbnail_worker, key, photo, max_pixels)
        return None

    @objc.python_method
    def _thumbnail_worker(self, key: str, photo: LocalPhoto, max_pixels: int) -> None:
        try:
            image = _decode_thumbnail(photo, max_pixels)
        except Exception:
            image = None
        self._pending_thumbnail_results[key] = image
        self.performSelectorOnMainThread_withObject_waitUntilDone_("thumbnailReady:", key, False)

    def thumbnailReady_(self, key) -> None:
        key = str(key)
        image = self._pending_thumbnail_results.pop(key, None)
        self._thumbnail_inflight.discard(key)
        if image is None:
            self._thumbnail_failures.add(key)
            return
        _THUMBNAIL_CACHE.setObject_forKey_(image, key)
        for item in self._collection.visibleItems():
            if not isinstance(item, PhotosMcpLocalPhotoItem):
                continue
            photo = next((candidate for candidate in self._photos if candidate.path == item._photo_path), None)
            if photo is not None and _thumbnail_cache_key(photo, self.thumbnail_pixels_for_visible_item()) == key:
                item.set_thumbnail(image)
        focused = next((photo for photo in self._photos if photo.path == self._focused_path), None)
        if focused is not None and _thumbnail_cache_key(focused, 900) == key:
            self._inspector_image.setImage_(image)
        if focused is not None and _thumbnail_cache_key(focused, 1600) == key:
            self._single_displayed_thumbnail_key = key
            self._single_set_photo_image(image)
        for path, image_views in self._selection_image_views.items():
            selected = self._selected_photos.get(path)
            if selected is not None and _thumbnail_cache_key(selected, 160) == key:
                for image_view in image_views:
                    image_view.setImage_(image)

    @objc.python_method
    def _run_event_loop(self) -> None:
        asyncio.set_event_loop(self._event_loop)
        self._event_loop_ready.set()
        self._event_loop.run_forever()

    @objc.python_method
    def _run_async(self, coroutine: Any) -> Any:
        return asyncio.run_coroutine_threadsafe(coroutine, self._event_loop).result()

    @staticmethod
    def _thread_alive(thread: Thread | None) -> bool:
        return thread is not None and thread.is_alive()

    @objc.python_method
    def _card(self, parent: Any) -> Any:
        card = NSView.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, 1.0, 1.0))
        card.setWantsLayer_(True)
        card.layer().setCornerRadius_(10.0)
        card.layer().setBackgroundColor_(panel_background_color().CGColor())
        card.layer().setBorderColor_(subtle_border_color().CGColor())
        card.layer().setBorderWidth_(1.0)
        parent.addSubview_(card)
        return card

    @objc.python_method
    def _label(self, parent: Any, text: str, size: float, *, bold: bool = False, secondary: bool = False) -> Any:
        label = NSTextField.labelWithString_(text)
        label.setFont_(app_font(size, "semibold" if bold else "regular"))
        label.setTextColor_(NSColor.secondaryLabelColor() if secondary else NSColor.labelColor())
        label.setToolTip_(text)
        parent.addSubview_(label)
        return label

    @objc.python_method
    def _button(
        self,
        parent: Any,
        title: str,
        selector: str,
        *,
        identifier: str = "",
        primary: bool = False,
        accessibility_label: str = "",
        icon: bool = False,
    ) -> Any:
        button = NSButton.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, 1.0, 1.0))
        button.setTitle_(title)
        button.setTarget_(self)
        button.setAction_(selector)
        button.setIdentifier_(identifier)
        button.setAccessibilityLabel_(accessibility_label or title)
        if icon:
            button.setControlSize_(NSControlSizeLarge)
            button.setFont_(app_font(17.0, "semibold"))
        if primary and hasattr(button, "setBezelColor_"):
            button.setBezelColor_(accent_color())
        parent.addSubview_(button)
        return button
