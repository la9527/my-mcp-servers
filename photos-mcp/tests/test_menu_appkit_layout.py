from __future__ import annotations

import asyncio
import base64
from threading import Event
from types import SimpleNamespace

from AppKit import (
    NSApplication,
    NSButton,
    NSCollectionView,
    NSControlSizeLarge,
    NSImageView,
    NSOutlineView,
    NSScrollView,
    NSStackView,
    NSSplitView,
    NSTextField,
    NSWindowZoomButton,
)
from Foundation import NSDate, NSIndexPath, NSMakeRect, NSMakeSize, NSRunLoop, NSSet
from PIL import Image
import pytest

from photos_mcp.menu_app import (
    PhotosMcpEnvironmentController,
    PhotosMcpMenuController,
    PhotosMcpPopoverController,
    PhotosMcpResultsController,
)
from photos_mcp.direct_classification_appkit import PhotosMcpDirectClassificationController
from photos_mcp.local_file_selection_appkit import (
    LocalPhoto,
    PhotosMcpLocalPhotoItem,
    PhotosMcpLocalPhotoSelectionController,
    _decode_thumbnail,
    _default_root_path,
    _maximum_sidebar_width,
    _scan_local_photos,
)
from photos_mcp.main_window_appkit import PhotosMcpMainWindowController
from photos_mcp.ui_theme import scaled_font_size


pytestmark = pytest.mark.filterwarnings("ignore::objc.ObjCPointerWarning")


def test_desktop_typography_scale_prioritizes_body_readability() -> None:
    assert scaled_font_size(9.5) == 12.1
    assert scaled_font_size(12.0) == 15.2
    assert scaled_font_size(16.0) == 19.2
    assert scaled_font_size(28.0) == 32.5


def _snapshot():
    return SimpleNamespace(
        daemon_status="ready",
        last_preflight_at="2026-08-02T01:00:00+09:00",
        preflight_checks=[],
        active_jobs=[
            {
                "job_id": "active-1",
                "request_kind": "curate_best_photos",
                "status": "running",
                "progress_stage": "vlm",
                "progress_current": 18,
                "progress_total": 42,
                "progress_percent": 43,
            }
        ],
        recent_jobs=[
            {
                "job_id": "done-1",
                "request_kind": "classify_and_organize",
                "status": "completed",
                "result_available": True,
            }
        ],
        pending_mutation_plans=[
            {
                "token": "approval-1",
                "mutation_plan": {
                    "action": "add_selected_to_album",
                    "target_album_name": "가족 여행",
                    "photo_count": 12,
                },
            }
        ],
    )


def _walk(view):
    yield view
    for child in view.subviews():
        yield from _walk(child)


def _menu_controller(snapshot):
    return PhotosMcpMenuController.alloc().initWithConfig_stateStore_daemonController_(
        SimpleNamespace(),
        SimpleNamespace(snapshot=lambda: snapshot),
        SimpleNamespace(),
    )


def test_popover_uses_scroll_stack_layout_with_visible_labels_and_accessibility() -> None:
    NSApplication.sharedApplication()
    snapshot = _snapshot()
    controller = PhotosMcpPopoverController.alloc().initWithMenuController_(
        _menu_controller(snapshot)
    )

    controller.rebuildWithSnapshot_(snapshot)
    root = controller.view()
    root.layoutSubtreeIfNeeded()
    descendants = list(_walk(root))

    assert any(isinstance(view, NSScrollView) for view in descendants)
    assert any(isinstance(view, NSStackView) for view in descendants)

    labels = [
        view
        for view in descendants
        if isinstance(view, NSTextField) and str(view.stringValue() or "") != "●"
    ]
    assert labels
    assert all(float(label.frame().size.height) > 0 for label in labels)

    buttons = [view for view in descendants if isinstance(view, NSButton)]
    assert buttons
    assert all(str(button.accessibilityLabel() or "") for button in buttons)
    assert all(button.nextKeyView() is not None for button in buttons)


def test_completed_result_card_has_visible_result_button() -> None:
    NSApplication.sharedApplication()
    snapshot = _snapshot()
    snapshot.recent_jobs[0]["result_count"] = 1
    controller = PhotosMcpPopoverController.alloc().initWithMenuController_(
        _menu_controller(snapshot)
    )

    controller.rebuildWithSnapshot_(snapshot)
    buttons = [
        view
        for view in _walk(controller.view())
        if isinstance(view, NSButton)
    ]
    result_button = next(button for button in buttons if str(button.title() or "") == "결과 보기")

    assert result_button.isBordered()
    assert not result_button.isTransparent()
    assert float(result_button.frame().size.width) >= 76.0


def test_environment_is_a_separate_section_with_visible_button() -> None:
    NSApplication.sharedApplication()
    controller = PhotosMcpPopoverController.alloc().initWithMenuController_(
        _menu_controller(_snapshot())
    )

    controller.rebuildWithSnapshot_(_snapshot())
    descendants = list(_walk(controller.view()))
    labels = {
        str(view.stringValue() or "")
        for view in descendants
        if isinstance(view, NSTextField)
    }
    buttons = [
        view
        for view in descendants
        if isinstance(view, NSButton) and str(view.title() or "") == "환경 검사"
    ]

    assert "환경 및 권한" in labels
    assert len(buttons) == 1
    assert buttons[0].isBordered()
    assert float(buttons[0].frame().size.width) >= 82.0


def test_main_window_has_native_sidebar_and_home_actions() -> None:
    NSApplication.sharedApplication()
    controller = PhotosMcpMainWindowController.alloc().initWithMenuController_(
        _menu_controller(_snapshot())
    )
    root = controller.window().contentView()
    descendants = list(_walk(root))
    labels = {
        str(view.stringValue() or "")
        for view in descendants
        if isinstance(view, NSTextField)
    }
    buttons = [view for view in descendants if isinstance(view, NSButton)]

    assert controller.window().title() == "Photos MCP"
    assert float(controller.window().minSize().width) == 1080.0
    assert {"Photos MCP", "최근 작업", "환경 및 권한"}.issubset(labels)
    assert {"홈", "사진 분류", "작업 기록", "환경 및 권한", "시작"}.issubset(
        str(button.title() or "") for button in buttons
    )
    sidebar_buttons = [button for button in buttons if str(button.identifier() or "")]
    assert all(button.image() is not None for button in sidebar_buttons[:4])
    assert all(float(button.frame().origin.x) == 20.0 for button in sidebar_buttons[:4])

    status_title = next(
        view
        for view in descendants
        if isinstance(view, NSTextField) and str(view.stringValue() or "") == "서버 실행 중"
    )
    status_summaries = [
        view
        for view in descendants
        if isinstance(view, NSTextField)
        and float(view.frame().origin.x) == 44.0
        and float(view.frame().origin.y) == 18.0
    ]
    assert float(status_title.frame().origin.x) == 44.0
    assert len(status_summaries) == 1


def test_main_window_hides_direct_classification_window_after_embedding() -> None:
    NSApplication.sharedApplication()
    menu_controller = _menu_controller(_snapshot())

    async def list_albums():
        return []

    direct = PhotosMcpDirectClassificationController.alloc().initWithMenuController_service_(
        menu_controller,
        SimpleNamespace(list_albums=list_albums),
    )
    direct.window().makeKeyAndOrderFront_(None)
    menu_controller._direct_classification_controller = direct
    controller = PhotosMcpMainWindowController.alloc().initWithMenuController_(menu_controller)

    controller.showTab_("classification")
    NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.05))

    assert direct.window().isVisible() is False
    direct.shutdown()


def test_main_window_keeps_controls_stable_when_snapshot_is_unchanged() -> None:
    NSApplication.sharedApplication()
    snapshot = _snapshot()
    controller = PhotosMcpMainWindowController.alloc().initWithMenuController_(
        _menu_controller(snapshot)
    )
    home_before = next(
        button
        for button in _walk(controller.window().contentView())
        if isinstance(button, NSButton) and str(button.identifier() or "") == "home"
    )

    controller.refreshWithSnapshot_(snapshot)

    home_after = next(
        button
        for button in _walk(controller.window().contentView())
        if isinstance(button, NSButton) and str(button.identifier() or "") == "home"
    )
    assert home_before == home_after


def test_main_window_job_and_environment_tabs_render_in_same_window() -> None:
    NSApplication.sharedApplication()
    snapshot = _snapshot()
    snapshot.recent_jobs = [
        {**snapshot.recent_jobs[0], "job_id": f"done-{index}"}
        for index in range(12)
    ]
    controller = PhotosMcpMainWindowController.alloc().initWithMenuController_(
        _menu_controller(snapshot)
    )

    controller.showTab_("jobs")
    job_scroll = next(
        view
        for view in _walk(controller.window().contentView())
        if isinstance(view, NSScrollView)
    )
    job_labels = {
        str(view.stringValue() or "")
        for view in _walk(controller.window().contentView())
        if isinstance(view, NSTextField)
    }
    controller.showTab_("environment")
    environment_labels = {
        str(view.stringValue() or "")
        for view in _walk(controller.window().contentView())
        if isinstance(view, NSTextField)
    }

    assert "작업 기록" in job_labels
    assert "사진 분류 및 정리 완료" in job_labels
    assert float(job_scroll.contentView().bounds().origin.y) > 0.0
    assert {
        "환경 및 권한",
        "준비 상태",
        "이미지 분석 모델",
        "Mac mini",
        "Linux workstation",
        "추가 점검",
    }.issubset(
        environment_labels
    )
    environment_views = list(_walk(controller.window().contentView()))
    assert sum(isinstance(view, NSImageView) for view in environment_views) >= 6
    environment_buttons = {
        str(view.title() or "")
        for view in environment_views
        if isinstance(view, NSButton)
    }
    assert {"전체 검사 실행", "연결 확인", "진단 정보 복사"}.issubset(
        environment_buttons
    )


def test_main_window_job_filters_and_selection_update_detail_panel() -> None:
    NSApplication.sharedApplication()
    snapshot = _snapshot()
    controller = PhotosMcpMainWindowController.alloc().initWithMenuController_(
        _menu_controller(snapshot)
    )
    controller.showTab_("jobs")

    completed_filter = next(
        button
        for button in _walk(controller.window().contentView())
        if isinstance(button, NSButton) and str(button.identifier() or "") == "completed"
    )
    completed_filter.performClick_(None)

    assert controller._job_filter == "completed"
    assert controller._selected_job_id == "done-1"
    labels = {
        str(view.stringValue() or "")
        for view in _walk(controller.window().contentView())
        if isinstance(view, NSTextField)
    }
    assert {"최근 작업", "작업 상세", "사진 분류 및 정리 완료"}.issubset(labels)


def test_results_gallery_scrolls_all_items_without_pagination() -> None:
    NSApplication.sharedApplication()
    snapshot = _snapshot()
    controller = PhotosMcpResultsController.alloc().initWithMenuController_(
        _menu_controller(snapshot)
    )
    controller.window().setContentSize_(NSMakeSize(1280.0, 850.0))
    payload = {
        "job_id": "job-1",
        "items": [
            {
                "photo_id": f"photo-{index}",
                "total_score": 100 - index,
                "scene_description": f"사진 {index}",
            }
            for index in range(1000)
        ],
    }

    controller.showWithResult_(payload)
    root = controller.window().contentView()
    root.layoutSubtreeIfNeeded()
    controller._collection_view.layoutSubtreeIfNeeded()
    buttons = [
        view
        for view in _walk(root)
        if isinstance(view, NSButton)
    ]
    collection = next(view for view in _walk(root) if isinstance(view, NSCollectionView))

    assert controller.collectionView_numberOfItemsInSection_(collection, 0) == 1000
    assert 0 < len(collection.visibleItems()) < 1000
    assert float(controller._flow_layout.collectionViewContentSize().height) > float(
        controller._scroll_view.contentView().bounds().size.height
    )
    assert all(str(button.identifier() or "") not in {"previous", "next"} for button in buttons)
    assert all(str(button.accessibilityLabel() or "") for button in buttons)


def test_results_gallery_computes_columns_from_available_screen_width() -> None:
    NSApplication.sharedApplication()
    controller = PhotosMcpResultsController.alloc().initWithMenuController_(
        _menu_controller(_snapshot())
    )
    controller._density_index = 2
    controller.window().setContentSize_(NSMakeSize(1100.0, 800.0))
    controller._layout_view()
    narrow_columns = controller._computed_columns
    controller.window().setContentSize_(NSMakeSize(1800.0, 900.0))
    controller._layout_view()
    wide_columns = controller._computed_columns

    assert narrow_columns == 3
    assert wide_columns > narrow_columns
    controller._density_index = 0
    controller._layout_view()
    assert controller._computed_columns == 6
    assert controller._density_label.stringValue() == "자동 6열"
    assert controller.window().standardWindowButton_(NSWindowZoomButton).isEnabled()


def test_results_gallery_uses_readable_default_density_when_no_preference() -> None:
    from photos_mcp.result_gallery_appkit import initial_density_index

    assert initial_density_index(None) == 2
    assert initial_density_index("invalid") == 2
    assert initial_density_index(99) == 2
    assert initial_density_index(0) == 0


def test_results_resize_keeps_selection_and_inspector_content() -> None:
    NSApplication.sharedApplication()
    controller = PhotosMcpResultsController.alloc().initWithMenuController_(
        _menu_controller(_snapshot())
    )
    controller.window().setContentSize_(NSMakeSize(1280.0, 850.0))
    controller.showWithResult_(
        {
            "job_id": "job-resize",
            "items": [
                {
                    "photo_id": f"photo-{index}",
                    "total_score": 100 - index,
                    "scene_description": f"사진 {index}",
                }
                for index in range(12)
            ],
        }
    )

    controller._selected_photo_id = "photo-6"
    controller._sync_selection()
    controller._update_inspector()
    assert controller._inspector_scene.stringValue() == "사진 6"

    controller.window().setContentSize_(NSMakeSize(1800.0, 900.0))
    controller._density_index = 0
    controller._layout_view(anchor_index=6)
    controller._collection_view.layoutSubtreeIfNeeded()

    assert controller._computed_columns == 6
    assert controller._selected_photo_id == "photo-6"
    assert controller._inspector_scene.stringValue() == "사진 6"
    assert 6 in {int(path.item()) for path in controller._collection_view.indexPathsForVisibleItems()}


def test_popover_content_remains_scrollable_at_minimum_height() -> None:
    NSApplication.sharedApplication()
    snapshot = _snapshot()
    controller = PhotosMcpPopoverController.alloc().initWithMenuController_(
        _menu_controller(snapshot)
    )

    controller.rebuildWithSnapshot_(snapshot)
    root = controller.view()
    root.setFrameSize_((390.0, 260.0))
    root.layoutSubtreeIfNeeded()
    scroll = next(
        view for view in _walk(root) if isinstance(view, NSScrollView)
    )
    document = scroll.documentView()

    assert scroll.hasVerticalScroller()
    assert float(document.frame().size.height) > float(scroll.contentView().bounds().size.height)


def test_environment_check_window_has_operational_sections_and_accessible_actions() -> None:
    NSApplication.sharedApplication()
    snapshot = _snapshot()
    controller = PhotosMcpEnvironmentController.alloc().initWithMenuController_(
        _menu_controller(snapshot)
    )

    controller.rebuildWithSnapshot_(snapshot)
    root = controller.window().contentView()
    root.layoutSubtreeIfNeeded()
    descendants = list(_walk(root))
    labels = [
        str(view.stringValue() or "")
        for view in descendants
        if isinstance(view, NSTextField)
    ]
    buttons = [view for view in descendants if isinstance(view, NSButton)]

    assert controller.window().title() == "환경 검사"
    assert float(root.frame().size.width) >= 600.0
    assert float(root.frame().size.height) >= 650.0
    assert {"준비 상태", "추가 점검", "이미지 분석 모델"}.issubset(labels)
    assert {"진단 정보 복사", "전체 검사 실행"}.issubset(
        str(button.title() or "") for button in buttons
    )
    assert all(str(button.accessibilityLabel() or "") for button in buttons)
    assert all(button.nextKeyView() is not None for button in buttons)


def test_direct_classification_window_has_complete_native_input_flow() -> None:
    NSApplication.sharedApplication()
    snapshot = _snapshot()
    controller = PhotosMcpDirectClassificationController.alloc().initWithMenuController_service_(
        _menu_controller(snapshot),
        SimpleNamespace(),
    )
    root = controller.window().contentView()
    root.layoutSubtreeIfNeeded()
    descendants = list(_walk(root))
    labels = {
        str(view.stringValue() or "")
        for view in descendants
        if isinstance(view, NSTextField)
    }
    buttons = [view for view in descendants if isinstance(view, NSButton)]

    assert controller.window().title() == "사진 분류"
    assert float(root.frame().size.width) == 860.0
    assert float(root.frame().size.height) == 720.0
    assert {
        "사진 분류",
        "분류 범위",
        "작업 설정",
        "분류 기준",
        "범위 요약",
        "분류 범위를 확인해 주세요",
    }.issubset(labels)
    assert {"기간 지정", "스크린샷 제외", "취소", "분류 시작", "폴더 열기"}.issubset(
        str(button.title() or "") for button in buttons
    )
    assert all(str(button.accessibilityLabel() or "") for button in buttons)
    assert all(button.nextKeyView() is not None for button in buttons)
    controller.shutdown()


def test_direct_classification_read_only_notice_does_not_overlap_scope_summary() -> None:
    NSApplication.sharedApplication()
    controller = PhotosMcpDirectClassificationController.alloc().initWithMenuController_service_(
        _menu_controller(_snapshot()),
        SimpleNamespace(),
    )
    notice = next(
        view
        for view in _walk(controller.window().contentView())
        if isinstance(view, NSTextField)
        and str(view.stringValue() or "").startswith("분류 결과는 읽기 전용")
    )

    assert float(notice.frame().origin.y) == 58.0
    assert float(notice.frame().size.height) == 16.0
    controller.shutdown()


def test_embedding_direct_classification_hides_its_standalone_window() -> None:
    NSApplication.sharedApplication()

    async def list_albums():
        return []

    controller = PhotosMcpDirectClassificationController.alloc().initWithMenuController_service_(
        _menu_controller(_snapshot()),
        SimpleNamespace(list_albums=list_albums),
    )
    controller.window().makeKeyAndOrderFront_(None)

    controller.embeddedContentView()

    assert controller.window().isVisible() is False
    controller.shutdown()


def test_direct_classification_reuses_open_local_photo_browser() -> None:
    NSApplication.sharedApplication()
    menu_controller = _menu_controller(_snapshot())
    controller = PhotosMcpDirectClassificationController.alloc().initWithMenuController_service_(
        menu_controller,
        SimpleNamespace(),
    )

    controller.openLocalPhotoBrowser_(None)
    first_browser = menu_controller._local_photo_selection_controller
    controller.openLocalPhotoBrowser_(None)

    assert menu_controller._local_photo_selection_controller == first_browser
    assert first_browser.window().isVisible()

    first_browser.window().orderOut_(None)
    first_browser.shutdown()
    controller.shutdown()


def test_local_file_selection_window_shows_three_pane_browser_and_read_only_action(tmp_path) -> None:
    NSApplication.sharedApplication()
    root_path = tmp_path / "photos"
    root_path.mkdir()
    selected = root_path / "selected.jpg"
    selected.write_bytes(b"image")
    controller = PhotosMcpLocalPhotoSelectionController.alloc().initWithMenuController_service_sourcePath_selectedPhotoIds_(
        _menu_controller(_snapshot()),
        SimpleNamespace(),
        str(root_path),
        (str(selected),),
    )
    root = controller.window().contentView()
    descendants = list(_walk(root))
    labels = {
        str(view.stringValue() or "")
        for view in descendants
        if isinstance(view, NSTextField)
    }
    buttons = [view for view in descendants if isinstance(view, NSButton)]

    assert controller.window().title() == "로컬 사진 분류"
    assert float(controller.window().minSize().width) == 1180.0
    assert float(controller.window().minSize().height) == 700.0
    assert {"폴더", "보고 있는 사진", "작업 설정"}.issubset(labels)
    assert {"사진을 선택하세요"}.issubset(str(button.title() or "") for button in buttons)
    assert any(isinstance(view, NSOutlineView) for view in descendants)
    assert any(isinstance(view, NSCollectionView) for view in descendants)
    assert controller._collection.allowsMultipleSelection() is False
    split_view = next(view for view in descendants if isinstance(view, NSSplitView))
    assert len(split_view.subviews()) == 3
    assert all(float(view.frame().size.width) > 0.0 for view in split_view.subviews())
    assert float(controller._sidebar.frame().size.width) == 280.0
    assert float(controller._inspector.frame().size.width) == 360.0
    assert float(controller._content.frame().size.width) >= 500.0
    controller.shutdown()


def test_local_photo_browser_owns_navigation_controls_in_relevant_panes(tmp_path) -> None:
    NSApplication.sharedApplication()
    root_path = tmp_path / "photos"
    root_path.mkdir()
    controller = PhotosMcpLocalPhotoSelectionController.alloc().initWithMenuController_service_sourcePath_selectedPhotoIds_(
        _menu_controller(_snapshot()),
        SimpleNamespace(),
        str(root_path),
        (),
    )

    assert not hasattr(controller, "_toolbar")
    assert controller._add_location_button.superview() == controller._sidebar
    assert controller._back_button.superview() == controller._content
    assert controller._forward_button.superview() == controller._content
    assert controller._search_field.superview() == controller._content
    assert float(controller._split_view.frame().size.height) == float(
        controller.window().contentView().bounds().size.height
    )
    assert float(controller._split_view.dividerThickness()) == 1.0
    controller.shutdown()


def test_local_photo_browser_uses_large_icon_controls(tmp_path) -> None:
    NSApplication.sharedApplication()
    root_path = tmp_path / "photos"
    root_path.mkdir()
    controller = PhotosMcpLocalPhotoSelectionController.alloc().initWithMenuController_service_sourcePath_selectedPhotoIds_(
        _menu_controller(_snapshot()),
        SimpleNamespace(),
        str(root_path),
        (),
    )
    controller._layout_panes()

    for button in (
        controller._add_location_button,
        controller._back_button,
        controller._forward_button,
        controller._density_smaller,
        controller._density_larger,
        controller._previous_photo_button,
        controller._next_photo_button,
    ):
        assert float(button.frame().size.width) >= 40.0
        assert float(button.frame().size.height) >= 36.0
        assert float(button.font().pointSize()) >= scaled_font_size(17.0)
    assert str(controller._density_smaller.accessibilityLabel()) == "사진 작게 보기"
    assert str(controller._density_larger.accessibilityLabel()) == "사진 크게 보기"
    controller.shutdown()


def test_local_photo_browser_search_matches_the_scaled_toolbar_typography(tmp_path) -> None:
    NSApplication.sharedApplication()
    root_path = tmp_path / "photos"
    root_path.mkdir()
    controller = PhotosMcpLocalPhotoSelectionController.alloc().initWithMenuController_service_sourcePath_selectedPhotoIds_(
        _menu_controller(_snapshot()),
        SimpleNamespace(),
        str(root_path),
        (),
    )

    search_image = controller._search_field.cell().searchButtonCell().image()

    assert controller._search_field.controlSize() == NSControlSizeLarge
    assert float(controller._search_field.font().pointSize()) >= scaled_font_size(12.0)
    assert search_image is not None
    assert float(search_image.size().width) >= 17.0
    assert float(search_image.size().height) >= 17.0
    controller.shutdown()


@pytest.mark.parametrize(
    ("content_width", "content_height"),
    ((500.0, 616.0), (560.0, 680.0), (700.0, 804.0), (1040.0, 944.0)),
)
def test_local_photo_browser_toolbar_has_no_overlap_at_supported_sizes(
    tmp_path,
    content_width: float,
    content_height: float,
) -> None:
    NSApplication.sharedApplication()
    root_path = tmp_path / "photos"
    root_path.mkdir()
    controller = PhotosMcpLocalPhotoSelectionController.alloc().initWithMenuController_service_sourcePath_selectedPhotoIds_(
        _menu_controller(_snapshot()),
        SimpleNamespace(),
        str(root_path),
        (),
    )
    controller._content.setFrame_(NSMakeRect(0.0, 0.0, content_width, content_height))

    controller._layout_content()

    def left(view) -> float:
        return float(view.frame().origin.x)

    def right(view) -> float:
        return left(view) + float(view.frame().size.width)

    def bottom(view) -> float:
        return float(view.frame().origin.y)

    def top(view) -> float:
        return bottom(view) + float(view.frame().size.height)

    def assert_inside(view, width: float, height: float) -> None:
        assert left(view) >= 0.0
        assert bottom(view) >= 0.0
        assert right(view) <= width
        assert top(view) <= height

    for view in (
        controller._back_button,
        controller._forward_button,
        controller._folder_title,
        controller._search_field,
        controller._view_mode_control,
        controller._include_subfolders,
        controller._sort_popup,
        controller._density_smaller,
        controller._density_larger,
        controller._collection_scroll,
        controller._selection_label,
        controller._select_all_button,
        controller._clear_button,
    ):
        assert_inside(view, content_width, content_height)

    assert right(controller._back_button) + 6.0 <= left(controller._forward_button)
    assert right(controller._forward_button) + 12.0 <= left(controller._folder_title)
    assert right(controller._folder_title) + 10.0 <= left(controller._search_field)
    assert right(controller._view_mode_control) + 8.0 <= left(controller._include_subfolders)
    assert right(controller._density_smaller) + 8.0 <= left(controller._density_larger)
    if content_width < 700.0:
        assert top(controller._sort_popup) + 8.0 <= bottom(controller._include_subfolders)
    else:
        assert right(controller._include_subfolders) + 12.0 <= left(controller._sort_popup)
        assert right(controller._sort_popup) + 12.0 <= left(controller._density_smaller)
    collection_top = top(controller._collection_scroll)
    assert collection_top + 10.0 <= min(bottom(controller._sort_popup), bottom(controller._include_subfolders))
    assert right(controller._selection_label) + 14.0 <= left(controller._select_all_button)
    assert right(controller._select_all_button) + 6.0 <= left(controller._clear_button)
    controller.shutdown()


@pytest.mark.parametrize(
    ("window_width", "content_height"),
    ((1180.0, 616.0), (1440.0, 804.0), (1920.0, 944.0)),
)
def test_local_photo_browser_three_pane_resize_keeps_each_pane_usable(
    tmp_path,
    window_width: float,
    content_height: float,
) -> None:
    NSApplication.sharedApplication()
    root_path = tmp_path / "photos"
    root_path.mkdir()
    controller = PhotosMcpLocalPhotoSelectionController.alloc().initWithMenuController_service_sourcePath_selectedPhotoIds_(
        _menu_controller(_snapshot()),
        SimpleNamespace(),
        str(root_path),
        (),
    )
    controller.window().setContentSize_(NSMakeSize(window_width, content_height))

    controller.splitView_resizeSubviewsWithOldSize_(
        controller._split_view,
        NSMakeSize(1440.0, 804.0),
    )
    controller._layout_sidebar()
    controller._layout_content()
    controller._layout_inspector()

    divider = float(controller._split_view.dividerThickness())
    sidebar_maximum = _maximum_sidebar_width(window_width, divider)
    assert 240.0 <= float(controller._sidebar.frame().size.width) <= sidebar_maximum
    assert float(controller._content.frame().size.width) >= 500.0
    assert 320.0 <= float(controller._inspector.frame().size.width) <= 440.0
    assert float(controller._add_location_button.frame().origin.x) >= 0.0
    assert float(controller._add_location_button.frame().origin.x + controller._add_location_button.frame().size.width) <= float(
        controller._sidebar.bounds().size.width
    )
    inspector_right = float(controller._inspector.frame().origin.x + controller._inspector.frame().size.width)
    assert inspector_right == window_width
    controller.shutdown()


def test_local_photo_browser_constrains_all_three_panes(tmp_path) -> None:
    NSApplication.sharedApplication()
    root_path = tmp_path / "photos"
    root_path.mkdir()
    controller = PhotosMcpLocalPhotoSelectionController.alloc().initWithMenuController_service_sourcePath_selectedPhotoIds_(
        _menu_controller(_snapshot()),
        SimpleNamespace(),
        str(root_path),
        (),
    )
    controller.window().setContentSize_(NSMakeSize(1180.0, 644.0))
    split_width = float(controller._split_view.bounds().size.width)
    divider = float(controller._split_view.dividerThickness())
    sidebar_width = float(controller._sidebar.frame().size.width)
    inspector_width = float(controller._inspector.frame().size.width)
    sidebar_maximum = _maximum_sidebar_width(split_width, divider, inspector_width)

    assert controller.splitView_constrainSplitPosition_ofSubviewAt_(controller._split_view, 100.0, 0) == 240.0
    assert controller.splitView_constrainSplitPosition_ofSubviewAt_(controller._split_view, 500.0, 0) == sidebar_maximum
    second_min = max(sidebar_width + divider + 500.0, split_width - divider - 440.0)
    second_max = split_width - divider - 320.0
    assert controller.splitView_constrainSplitPosition_ofSubviewAt_(controller._split_view, 100.0, 1) == second_min
    assert controller.splitView_constrainSplitPosition_ofSubviewAt_(controller._split_view, 2000.0, 1) == second_max
    assert controller.splitView_shouldAdjustSizeOfSubview_(controller._split_view, controller._sidebar) is False
    assert controller.splitView_shouldAdjustSizeOfSubview_(controller._split_view, controller._content) is True
    assert controller.splitView_shouldAdjustSizeOfSubview_(controller._split_view, controller._inspector) is False
    controller.shutdown()


def test_local_photo_browser_sidebar_can_expand_to_forty_percent_on_a_wide_window(tmp_path) -> None:
    NSApplication.sharedApplication()
    root_path = tmp_path / "photos"
    root_path.mkdir()
    controller = PhotosMcpLocalPhotoSelectionController.alloc().initWithMenuController_service_sourcePath_selectedPhotoIds_(
        _menu_controller(_snapshot()),
        SimpleNamespace(),
        str(root_path),
        (),
    )
    controller.window().setContentSize_(NSMakeSize(1920.0, 944.0))
    divider = float(controller._split_view.dividerThickness())

    maximum = controller.splitView_constrainSplitPosition_ofSubviewAt_(
        controller._split_view,
        1200.0,
        0,
    )

    assert maximum == 1920.0 * 0.40
    assert maximum == _maximum_sidebar_width(1920.0, divider)
    controller._sidebar.setFrame_(NSMakeRect(0.0, 0.0, maximum, 944.0))
    second_minimum = controller.splitView_constrainSplitPosition_ofSubviewAt_(
        controller._split_view,
        100.0,
        1,
    )
    assert second_minimum >= maximum + divider + 500.0
    controller.shutdown()


def test_local_photo_browser_normalizes_other_panes_after_sidebar_drag(tmp_path) -> None:
    NSApplication.sharedApplication()
    root_path = tmp_path / "photos"
    root_path.mkdir()
    controller = PhotosMcpLocalPhotoSelectionController.alloc().initWithMenuController_service_sourcePath_selectedPhotoIds_(
        _menu_controller(_snapshot()),
        SimpleNamespace(),
        str(root_path),
        (),
    )
    controller.window().setContentSize_(NSMakeSize(1920.0, 944.0))
    divider = float(controller._split_view.dividerThickness())
    sidebar_width = 1920.0 * 0.40
    controller._inspector_width_preference = 360.0
    controller._sidebar.setFrame_(NSMakeRect(0.0, 0.0, sidebar_width, 944.0))
    controller._content.setFrame_(NSMakeRect(sidebar_width + divider, 0.0, 22.0, 944.0))
    controller._inspector.setFrame_(
        NSMakeRect(sidebar_width + divider + 22.0 + divider, 0.0, 1128.0, 944.0)
    )

    controller.splitViewDidResizeSubviews_(None)

    assert float(controller._sidebar.frame().size.width) == sidebar_width
    assert float(controller._content.frame().size.width) >= 500.0
    assert float(controller._inspector.frame().size.width) == 360.0
    inspector_right = float(controller._inspector.frame().origin.x + controller._inspector.frame().size.width)
    assert inspector_right == 1920.0
    controller.shutdown()


def test_local_photo_browser_reclaims_side_pane_width_when_window_shrinks(tmp_path) -> None:
    NSApplication.sharedApplication()
    root_path = tmp_path / "photos"
    root_path.mkdir()
    controller = PhotosMcpLocalPhotoSelectionController.alloc().initWithMenuController_service_sourcePath_selectedPhotoIds_(
        _menu_controller(_snapshot()),
        SimpleNamespace(),
        str(root_path),
        (),
    )
    controller.window().setContentSize_(NSMakeSize(1180.0, 616.0))
    controller._sidebar.setFrame_(NSMakeRect(0.0, 0.0, 700.0, 616.0))
    controller._content.setFrame_(NSMakeRect(701.0, 0.0, 38.0, 616.0))
    controller._inspector.setFrame_(NSMakeRect(740.0, 0.0, 440.0, 616.0))
    controller._inspector_width_preference = 440.0

    controller.splitView_resizeSubviewsWithOldSize_(controller._split_view, NSMakeSize(1440.0, 804.0))

    assert float(controller._content.frame().size.width) >= 500.0
    divider = float(controller._split_view.dividerThickness())
    assert float(controller._sidebar.frame().size.width) == _maximum_sidebar_width(1180.0, divider)
    assert float(controller._inspector.frame().size.width) == 320.0
    inspector_frame = controller._inspector.frame()
    assert float(inspector_frame.origin.x + inspector_frame.size.width) == float(controller._split_view.bounds().size.width)
    controller.shutdown()


def test_local_photo_checkbox_selection_is_independent_from_inspector_focus(tmp_path) -> None:
    NSApplication.sharedApplication()
    root_path = tmp_path / "photos"
    root_path.mkdir()
    first = root_path / "first.jpg"
    second = root_path / "second.jpg"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    photos = [
        LocalPhoto(str(first.resolve()), first.name, first.stat().st_mtime, first.stat().st_size, 4032, 3024),
        LocalPhoto(str(second.resolve()), second.name, second.stat().st_mtime, second.stat().st_size, 4032, 3024),
    ]
    controller = PhotosMcpLocalPhotoSelectionController.alloc().initWithMenuController_service_sourcePath_selectedPhotoIds_(
        _menu_controller(_snapshot()),
        SimpleNamespace(),
        str(root_path),
        (),
    )
    controller._photos = photos
    controller._focused_path = photos[0].path

    controller._set_photo_checked(photos[0].path, True)
    controller._focused_path = photos[1].path
    controller._update_inspector()

    assert controller._selected_paths == {photos[0].path}
    assert controller._focused_path == photos[1].path
    assert str(controller._selected_count.stringValue()) == "분류 대상 1장"
    assert str(controller._run_button.title()) == "선택한 1장 분류"
    assert str(controller._file_resolution.stringValue()) == "4032 × 3024 px"

    item = PhotosMcpLocalPhotoItem.alloc().init()
    item.loadView()
    item.configure(photos[0], controller)
    item_buttons = [view for view in _walk(item.view()) if isinstance(view, NSButton)]
    assert any(str(button.accessibilityLabel() or "").endswith("분류 대상으로 선택") for button in item_buttons)
    controller.shutdown()


def test_local_photo_single_view_navigates_focus_and_syncs_selection(tmp_path) -> None:
    NSApplication.sharedApplication()
    root_path = tmp_path / "photos"
    root_path.mkdir()
    photos = []
    for name in ("first.jpg", "second.jpg", "third.jpg"):
        path = root_path / name
        path.write_bytes(name.encode())
        photos.append(
            LocalPhoto(str(path.resolve()), name, path.stat().st_mtime, path.stat().st_size, 1600, 900)
        )
    controller = PhotosMcpLocalPhotoSelectionController.alloc().initWithMenuController_service_sourcePath_selectedPhotoIds_(
        _menu_controller(_snapshot()),
        SimpleNamespace(),
        str(root_path),
        (),
    )
    controller._photos = photos
    controller._focused_path = photos[0].path

    controller._view_mode_control.setSelectedSegment_(1)
    controller.viewModeChanged_(controller._view_mode_control)

    assert controller._view_mode == "single"
    assert controller._collection_scroll.isHidden()
    assert not controller._single_view.isHidden()
    assert not controller._previous_photo_button.isEnabled()
    assert controller._next_photo_button.isEnabled()
    assert str(controller._single_counter.stringValue()) == "1 / 3"

    controller.showNextPhoto_(None)
    controller._single_check_button.setState_(1)
    controller.toggleFocusedPhotoCheck_(controller._single_check_button)

    assert controller._focused_path == photos[1].path
    assert controller._selected_paths == {photos[1].path}
    assert str(controller._single_counter.stringValue()) == "2 / 3"
    assert controller._previous_photo_button.isEnabled()

    controller.showNextPhoto_(None)

    assert controller._focused_path == photos[2].path
    assert not controller._next_photo_button.isEnabled()
    controller.shutdown()


def test_local_photo_single_view_supports_arrow_return_and_space_keys(tmp_path) -> None:
    NSApplication.sharedApplication()
    root_path = tmp_path / "photos"
    root_path.mkdir()
    photos = []
    for name in ("first.jpg", "second.jpg", "third.jpg"):
        path = root_path / name
        path.write_bytes(name.encode())
        photos.append(LocalPhoto(str(path.resolve()), name, path.stat().st_mtime, path.stat().st_size, 1600, 900))
    controller = PhotosMcpLocalPhotoSelectionController.alloc().initWithMenuController_service_sourcePath_selectedPhotoIds_(
        _menu_controller(_snapshot()),
        SimpleNamespace(),
        str(root_path),
        (),
    )
    controller._photos = photos
    controller._focused_path = photos[0].path
    controller._view_mode_control.setSelectedSegment_(1)
    controller.viewModeChanged_(controller._view_mode_control)

    controller._single_view.keyDown_(SimpleNamespace(keyCode=lambda: 124))
    controller._single_view.keyDown_(SimpleNamespace(keyCode=lambda: 36))
    controller._single_view.keyDown_(SimpleNamespace(keyCode=lambda: 123))
    controller._single_view.keyDown_(SimpleNamespace(keyCode=lambda: 76))

    assert controller._focused_path == photos[0].path
    assert controller._selected_paths == {photos[0].path, photos[1].path}
    assert controller.window().firstResponder() == controller._single_view

    controller._single_view.keyDown_(SimpleNamespace(keyCode=lambda: 49))

    assert controller._selected_paths == {photos[1].path}
    assert controller.window().firstResponder() == controller._single_view
    controller.shutdown()


def test_local_photo_grid_view_return_and_space_toggle_focused_photo(tmp_path) -> None:
    NSApplication.sharedApplication()
    root_path = tmp_path / "photos"
    root_path.mkdir()
    photos = []
    for name in ("first.jpg", "second.jpg"):
        path = root_path / name
        path.write_bytes(name.encode())
        photos.append(LocalPhoto(str(path.resolve()), name, path.stat().st_mtime, path.stat().st_size, 1600, 900))
    controller = PhotosMcpLocalPhotoSelectionController.alloc().initWithMenuController_service_sourcePath_selectedPhotoIds_(
        _menu_controller(_snapshot()),
        SimpleNamespace(),
        str(root_path),
        (),
    )
    controller._photos = photos
    controller._collection.reloadData()
    controller._focused_path = photos[0].path
    controller._view_mode = "grid"
    second_index = NSIndexPath.indexPathForItem_inSection_(1, 0)
    controller._collection.selectItemsAtIndexPaths_scrollPosition_(
        NSSet.setWithObject_(second_index),
        0,
    )

    controller._collection.keyDown_(SimpleNamespace(keyCode=lambda: 36))

    assert controller._selected_paths == {photos[1].path}
    assert controller._focused_path == photos[1].path
    assert controller.window().firstResponder() == controller._collection

    controller._collection.keyDown_(SimpleNamespace(keyCode=lambda: 76))

    assert controller._selected_paths == set()
    assert controller._focused_path == photos[1].path
    assert controller.window().firstResponder() == controller._collection

    controller._collection.keyDown_(SimpleNamespace(keyCode=lambda: 49))

    assert controller._selected_paths == {photos[1].path}
    assert controller._focused_path == photos[1].path
    assert controller.window().firstResponder() == controller._collection
    controller.shutdown()


def test_local_photo_single_view_layout_fits_minimum_center_pane(tmp_path) -> None:
    NSApplication.sharedApplication()
    root_path = tmp_path / "photos"
    root_path.mkdir()
    controller = PhotosMcpLocalPhotoSelectionController.alloc().initWithMenuController_service_sourcePath_selectedPhotoIds_(
        _menu_controller(_snapshot()),
        SimpleNamespace(),
        str(root_path),
        (),
    )
    controller._content.setFrame_(NSMakeRect(0.0, 0.0, 500.0, 616.0))
    controller._view_mode = "single"

    controller._layout_content()

    content_width = float(controller._content.bounds().size.width)
    single_width = float(controller._single_view.frame().size.width)
    single_height = float(controller._single_view.frame().size.height)
    assert single_width > 0.0
    assert single_height > 0.0
    for view in (
        controller._previous_photo_button,
        controller._next_photo_button,
        controller._single_check_button,
        controller._single_filename,
        controller._single_counter,
    ):
        frame = view.frame()
        assert float(frame.origin.x) >= 0.0
        assert float(frame.origin.y) >= 0.0
        assert float(frame.origin.x + frame.size.width) <= single_width
        assert float(frame.origin.y + frame.size.height) <= single_height
    assert float(controller._single_image.frame().size.width) > 0.0
    assert float(controller._single_image.frame().size.height) > 0.0
    assert float(controller._single_view.frame().origin.x + single_width) <= content_width
    assert float(controller._view_mode_control.frame().origin.x + controller._view_mode_control.frame().size.width) <= float(
        controller._include_subfolders.frame().origin.x
    ) - 8.0
    controller.shutdown()


def test_local_photo_browser_stacks_content_controls_in_a_narrow_center_pane(tmp_path) -> None:
    NSApplication.sharedApplication()
    root_path = tmp_path / "photos"
    root_path.mkdir()
    controller = PhotosMcpLocalPhotoSelectionController.alloc().initWithMenuController_service_sourcePath_selectedPhotoIds_(
        _menu_controller(_snapshot()),
        SimpleNamespace(),
        str(root_path),
        (),
    )
    controller._content.setFrame_(NSMakeRect(0.0, 0.0, 560.0, 680.0))

    controller._layout_content()

    assert float(controller._include_subfolders.frame().origin.y) < float(controller._folder_title.frame().origin.y)
    folder_title_right = float(
        controller._folder_title.frame().origin.x + controller._folder_title.frame().size.width
    )
    assert float(controller._folder_title.frame().size.width) > 0.0
    assert folder_title_right <= float(controller._search_field.frame().origin.x) - 10.0
    assert float(controller._collection_scroll.frame().size.height) > 0.0
    controls_bottom = float(controller._include_subfolders.frame().origin.y)
    collection_top = float(controller._collection_scroll.frame().origin.y) + float(
        controller._collection_scroll.frame().size.height
    )
    assert collection_top <= controls_bottom - 8.0
    controller.shutdown()


def test_local_photo_browser_keeps_settings_fixed_below_scrollable_inspector(tmp_path) -> None:
    NSApplication.sharedApplication()
    root_path = tmp_path / "photos"
    root_path.mkdir()
    controller = PhotosMcpLocalPhotoSelectionController.alloc().initWithMenuController_service_sourcePath_selectedPhotoIds_(
        _menu_controller(_snapshot()),
        SimpleNamespace(),
        str(root_path),
        (),
    )
    controller._focused_path = ""

    controller._layout_inspector()

    card_top = float(controller._settings_card.frame().origin.y + controller._settings_card.frame().size.height)
    assert float(controller._settings_card.frame().origin.y) == 38.0
    assert float(controller._photo_scroll.frame().origin.y) >= card_top + 14.0
    assert float(controller._selection_scroll.frame().origin.y) >= card_top + 14.0
    controller.shutdown()


def test_local_photo_browser_focus_populates_scrollable_photo_inspector_without_moving_settings(tmp_path) -> None:
    NSApplication.sharedApplication()
    root_path = tmp_path / "photos"
    root_path.mkdir()
    image_path = root_path / "focused.jpg"
    image_path.write_bytes(b"image")
    photo = LocalPhoto(str(image_path.resolve()), image_path.name, image_path.stat().st_mtime, 1024, 4032, 3024)
    controller = PhotosMcpLocalPhotoSelectionController.alloc().initWithMenuController_service_sourcePath_selectedPhotoIds_(
        _menu_controller(_snapshot()),
        SimpleNamespace(),
        str(root_path),
        (),
    )
    controller._photos = []
    controller._focused_path = ""
    controller._update_inspector()
    empty_card_y = float(controller._settings_card.frame().origin.y)

    controller._photos = [photo]
    controller._focused_path = photo.path
    controller._update_inspector()

    card_frame = controller._settings_card.frame()
    preview_frame = controller._inspector_image.frame()
    card_top = float(card_frame.origin.y + card_frame.size.height)
    assert float(card_frame.origin.y) == empty_card_y == 38.0
    assert float(controller._photo_scroll.frame().origin.y) >= card_top + 14.0
    assert float(preview_frame.origin.y) == 0.0
    assert float(controller._file_name.frame().origin.y) > float(preview_frame.size.height)
    assert float(controller._photo_document.frame().size.height) >= float(controller._photo_scroll.contentSize().height)
    controller.shutdown()


def test_local_photo_browser_keeps_focused_metadata_above_settings_at_minimum_height(tmp_path) -> None:
    NSApplication.sharedApplication()
    root_path = tmp_path / "photos"
    root_path.mkdir()
    controller = PhotosMcpLocalPhotoSelectionController.alloc().initWithMenuController_service_sourcePath_selectedPhotoIds_(
        _menu_controller(_snapshot()),
        SimpleNamespace(),
        str(root_path),
        (),
    )
    focused_path = root_path / "focused.jpg"
    focused_path.write_bytes(b"image")
    focused_photo = LocalPhoto(
        str(focused_path.resolve()),
        focused_path.name,
        focused_path.stat().st_mtime,
        focused_path.stat().st_size,
        4032,
        3024,
    )
    controller._photos = [focused_photo]
    controller._focused_path = focused_photo.path
    controller._inspector.setFrame_(NSMakeRect(0.0, 0.0, 320.0, 616.0))

    controller._layout_inspector()

    card_top = float(controller._settings_card.frame().origin.y + controller._settings_card.frame().size.height)
    assert float(controller._photo_scroll.frame().origin.y) >= card_top + 14.0
    assert float(controller._photo_scroll.frame().size.height) >= 120.0
    assert controller._selected_count.superview() == controller._settings_card
    settings_title_right = float(controller._settings_title.frame().origin.x + controller._settings_title.frame().size.width)
    selected_count_left = float(controller._selected_count.frame().origin.x)
    assert settings_title_right + 12.0 <= selected_count_left
    run_top = float(controller._run_button.frame().origin.y + controller._run_button.frame().size.height)
    limit_bottom = float(controller._limit.frame().origin.y)
    assert run_top + 8.0 <= limit_bottom
    controller.shutdown()


def test_local_photo_browser_hides_preview_without_a_focused_photo(tmp_path) -> None:
    NSApplication.sharedApplication()
    root_path = tmp_path / "photos"
    root_path.mkdir()
    controller = PhotosMcpLocalPhotoSelectionController.alloc().initWithMenuController_service_sourcePath_selectedPhotoIds_(
        _menu_controller(_snapshot()),
        SimpleNamespace(),
        str(root_path),
        (),
    )
    controller._focused_path = ""

    controller._update_inspector()

    assert controller._inspector_image.isHidden()
    assert not controller._inspector_empty.isHidden()
    assert str(controller._inspector_empty.stringValue()) == "사진을 클릭하면 상세 정보를 볼 수 있습니다."
    controller.shutdown()


def test_local_photo_browser_preserves_selection_across_folder_navigation(tmp_path) -> None:
    NSApplication.sharedApplication()
    first_folder = tmp_path / "first"
    second_folder = tmp_path / "second"
    first_folder.mkdir()
    second_folder.mkdir()
    first_path = first_folder / "first.jpg"
    second_path = second_folder / "second.jpg"
    first_path.write_bytes(b"first")
    second_path.write_bytes(b"second")
    first_photo = LocalPhoto(str(first_path.resolve()), first_path.name, first_path.stat().st_mtime, 5, 100, 100)
    second_photo = LocalPhoto(str(second_path.resolve()), second_path.name, second_path.stat().st_mtime, 6, 100, 100)
    controller = PhotosMcpLocalPhotoSelectionController.alloc().initWithMenuController_service_sourcePath_selectedPhotoIds_(
        _menu_controller(_snapshot()), SimpleNamespace(), str(first_folder), ()
    )
    controller._photos = [first_photo]
    controller._set_photo_checked(first_photo.path, True)

    controller._set_current_folder(str(second_folder), add_history=True)
    controller._photos = [second_photo]
    controller._set_photo_checked(second_photo.path, True)

    assert controller._selected_paths == {first_photo.path, second_photo.path}
    assert set(controller._selected_photos) == controller._selected_paths
    assert controller._selected_folder_count() == 2
    assert "선택 목록 2" == str(controller._inspector_mode_control.labelForSegment_(1))
    controller.shutdown()


def test_local_photo_browser_clear_current_view_keeps_other_folder_selection(tmp_path) -> None:
    NSApplication.sharedApplication()
    first_folder = tmp_path / "first"
    second_folder = tmp_path / "second"
    first_folder.mkdir()
    second_folder.mkdir()
    first_path = first_folder / "first.jpg"
    second_path = second_folder / "second.jpg"
    first_path.write_bytes(b"first")
    second_path.write_bytes(b"second")
    first_photo = LocalPhoto(str(first_path.resolve()), first_path.name, 1.0, 5, 100, 100)
    second_photo = LocalPhoto(str(second_path.resolve()), second_path.name, 2.0, 6, 100, 100)
    controller = PhotosMcpLocalPhotoSelectionController.alloc().initWithMenuController_service_sourcePath_selectedPhotoIds_(
        _menu_controller(_snapshot()), SimpleNamespace(), str(first_folder), ()
    )
    controller._photos = [first_photo]
    controller._set_photo_checked(first_photo.path, True)
    controller._photos = [second_photo]
    controller._set_photo_checked(second_photo.path, True)

    controller.clearSelection_(None)

    assert controller._selected_paths == {first_photo.path}
    assert set(controller._selected_photos) == {first_photo.path}
    controller.shutdown()


def test_local_photo_browser_uses_a_finder_style_folder_outline(tmp_path) -> None:
    NSApplication.sharedApplication()
    root_path = tmp_path / "photos"
    root_path.mkdir()
    controller = PhotosMcpLocalPhotoSelectionController.alloc().initWithMenuController_service_sourcePath_selectedPhotoIds_(
        _menu_controller(_snapshot()),
        SimpleNamespace(),
        str(root_path),
        (),
    )
    favorites = controller._root_nodes[0]
    first_folder = controller._folder_children[favorites.key][0]

    group_row = controller._outline.rowForItem_(favorites)
    folder_row = controller._outline.rowForItem_(first_folder)
    group_slot = controller._outline.frameOfOutlineCellAtRow_(group_row)
    folder_slot = controller._outline.frameOfOutlineCellAtRow_(folder_row)
    group_view = controller.outlineView_viewForTableColumn_item_(controller._outline, None, favorites)
    folder_view = controller.outlineView_viewForTableColumn_item_(controller._outline, None, first_folder)

    controller._folder_counts[first_folder.path] = 12

    assert float(controller._outline.indentationPerLevel()) == 16.0
    assert float(folder_slot.origin.x) > float(group_slot.origin.x)
    assert str(group_view.identifier()) == "folder-row"
    assert str(folder_view.identifier()) == "folder-row"
    assert len(group_view.subviews()) == 1
    assert len(folder_view.subviews()) == 2
    assert isinstance(folder_view.subviews()[0], NSImageView)
    assert float(controller._outline.rowHeight()) == 44.0
    assert float(folder_view.subviews()[0].frame().size.width) == 20.0
    assert float(folder_view.subviews()[0].frame().size.height) == 20.0
    assert float(folder_view.subviews()[1].frame().origin.x) == 36.0
    disclosure = next(
        view
        for view in controller._outline.rowViewAtRow_makeIfNecessary_(folder_row, True).subviews()
        if str(view.identifier() or "") == "NSOutlineViewDisclosureButtonKey"
    )
    assert float(disclosure.image().size().width) >= 12.0
    assert float(disclosure.image().size().height) >= 17.0
    assert str(controller.outlineView_objectValueForTableColumn_byItem_(None, None, first_folder)) == "사진"
    controller.shutdown()


def test_local_photo_browser_selects_the_current_folder_in_the_outline() -> None:
    NSApplication.sharedApplication()
    current_folder = _default_root_path()
    controller = PhotosMcpLocalPhotoSelectionController.alloc().initWithMenuController_service_sourcePath_selectedPhotoIds_(
        _menu_controller(_snapshot()),
        SimpleNamespace(),
        str(current_folder),
        (),
    )
    favorites = controller._root_nodes[0]
    pictures = controller._folder_children[favorites.key][0]

    controller._sync_outline_selection()

    assert controller._outline.itemAtRow_(controller._outline.selectedRow()) == pictures
    controller.shutdown()


def test_local_photo_scan_honors_recursive_scope_and_supported_extensions(tmp_path) -> None:
    root = tmp_path / "photos"
    nested = root / "trip"
    nested.mkdir(parents=True)
    (root / "cover.JPG").write_bytes(b"image")
    Image.new("RGB", (32, 18), "navy").save(root / "capture.ARW", format="PNG")
    (root / "notes.txt").write_text("not a photo")
    (nested / "detail.heic").write_bytes(b"image")

    direct = _scan_local_photos(str(root), include_subfolders=False)
    recursive = _scan_local_photos(str(root), include_subfolders=True)

    assert {photo.name for photo in direct} == {"capture.ARW", "cover.JPG"}
    assert {photo.name for photo in recursive} == {"capture.ARW", "cover.JPG", "detail.heic"}


def test_local_photo_thumbnail_decodes_sony_arw_with_imageio(tmp_path) -> None:
    image_path = tmp_path / "capture.ARW"
    Image.new("RGB", (320, 180), color=(40, 90, 140)).save(image_path, format="PNG")
    photo = _scan_local_photos(str(tmp_path), include_subfolders=False)[0]

    thumbnail = _decode_thumbnail(photo, 160)

    assert photo.name == "capture.ARW"
    assert thumbnail is not None
    assert float(thumbnail.size().width / thumbnail.size().height) == pytest.approx(16 / 9, abs=0.01)


def test_local_photo_scan_reads_pixel_dimensions_from_image_metadata(tmp_path) -> None:
    image = tmp_path / "pixel.png"
    image.write_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
        )
    )

    photos = _scan_local_photos(str(tmp_path), include_subfolders=False)

    assert [(photo.pixel_width, photo.pixel_height) for photo in photos] == [(1, 1)]


def test_local_photo_thumbnail_preserves_source_aspect_ratio(tmp_path) -> None:
    image_path = tmp_path / "landscape.png"
    Image.new("RGB", (320, 180), color=(40, 90, 140)).save(image_path)
    stat = image_path.stat()
    photo = LocalPhoto(
        path=str(image_path),
        name=image_path.name,
        modified_at=stat.st_mtime,
        size_bytes=stat.st_size,
        pixel_width=320,
        pixel_height=180,
    )

    thumbnail = _decode_thumbnail(photo, 160)

    assert thumbnail is not None
    assert float(thumbnail.size().width / thumbnail.size().height) == pytest.approx(16 / 9, abs=0.01)


def test_direct_classification_album_and_period_controls_build_command() -> None:
    NSApplication.sharedApplication()
    controller = PhotosMcpDirectClassificationController.alloc().initWithMenuController_service_(
        _menu_controller(_snapshot()),
        SimpleNamespace(),
    )
    controller._pending_album_payload = {
        "status": "ready",
        "albums": [{"id": "album-1", "name": "여행", "photo_count": 12}],
    }
    controller.albumsLoaded_(None)
    controller._album_popup.selectItemAtIndex_(1)
    controller._period_checkbox.setState_(1)
    controller._start_field.setStringValue_("2026-07-01")
    controller._end_field.setStringValue_("2026-08-02")
    controller._mode_control.setSelectedSegment_(1)
    controller._profile_popup.selectItemWithTitle_("풍경")
    controller._limit_popup.selectItemWithTitle_("25장")

    command = controller.commandFromControls()

    assert command.album == "여행"
    assert command.date_from == "2026-07-01"
    assert command.date_to == "2026-08-02"
    assert command.mode == "select_best"
    assert command.selection_profile == "landscape"
    assert command.limit == 25
    assert controller._limit_popup.itemTitles()[-1] == "1000장"
    controller.shutdown()


def test_direct_classification_async_runtime_survives_request_completion() -> None:
    NSApplication.sharedApplication()
    controller = PhotosMcpDirectClassificationController.alloc().initWithMenuController_service_(
        _menu_controller(_snapshot()),
        SimpleNamespace(),
    )
    child_completed = Event()

    async def starts_background_child():
        async def child():
            await asyncio.sleep(0.01)
            child_completed.set()

        asyncio.create_task(child())
        return "accepted"

    assert controller._run_async(starts_background_child()) == "accepted"
    assert child_completed.wait(timeout=1.0)
    controller.shutdown()


def test_direct_classification_window_closes_after_job_is_accepted() -> None:
    NSApplication.sharedApplication()
    refreshed = Event()
    rebuilt = Event()
    menu_controller = SimpleNamespace(
        _daemon_controller=SimpleNamespace(refresh_jobs_once=refreshed.set),
        rebuildMenu=rebuilt.set,
    )
    controller = PhotosMcpDirectClassificationController.alloc().initWithMenuController_service_(
        menu_controller,
        SimpleNamespace(),
    )
    controller._pending_run_payload = {"job_id": "accepted-job"}
    controller.window().makeKeyAndOrderFront_(None)

    controller.classificationStarted_(None)

    assert refreshed.is_set()
    assert rebuilt.is_set()
    assert not controller.window().isVisible()
    controller.shutdown()
