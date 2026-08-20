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
    NSImage,
    NSImageView,
    NSOutlineView,
    NSScrollView,
    NSStackView,
    NSSplitView,
    NSTextField,
    NSWindowZoomButton,
)
from Foundation import NSDate, NSIndexPath, NSMakePoint, NSMakeRect, NSMakeSize, NSRunLoop, NSSet
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
from photos_mcp.interfaces.appkit.main.controller import PhotosMcpMainWindowController
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
    assert float(controller.window().minSize().width) == 1180.0
    assert float(controller.window().minSize().height) == 760.0
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


def test_main_window_minimum_size_keeps_classification_footer_visible() -> None:
    NSApplication.sharedApplication()
    menu_controller = _menu_controller(_snapshot())

    async def list_albums():
        return []

    direct = PhotosMcpDirectClassificationController.alloc().initWithMenuController_service_(
        menu_controller,
        SimpleNamespace(list_albums=list_albums),
    )
    menu_controller._direct_classification_controller = direct
    controller = PhotosMcpMainWindowController.alloc().initWithMenuController_(menu_controller)
    controller.window().setFrame_display_(NSMakeRect(0.0, 0.0, 1180.0, 760.0), False)

    controller.showTab_("classification")

    assert float(controller.window().contentView().frame().size.height) >= 720.0
    assert float(direct.embeddedContentView().frame().size.height) == 720.0
    assert float(direct._cancel_button.frame().origin.y) == 6.0
    assert float(direct._run_button.frame().origin.y) == 6.0
    scroll = direct.embeddedContentView().superview().superview().superview()
    assert isinstance(scroll, NSScrollView)
    assert scroll.hasVerticalScroller() is False
    direct.shutdown()


def test_main_window_top_aligns_classification_form_when_window_is_tall() -> None:
    NSApplication.sharedApplication()
    menu_controller = _menu_controller(_snapshot())

    async def list_albums():
        return []

    direct = PhotosMcpDirectClassificationController.alloc().initWithMenuController_service_(
        menu_controller,
        SimpleNamespace(list_albums=list_albums),
    )
    menu_controller._direct_classification_controller = direct
    controller = PhotosMcpMainWindowController.alloc().initWithMenuController_(menu_controller)
    controller.window().setContentSize_(NSMakeSize(1500.0, 1100.0))

    controller.showTab_("classification")

    document = direct.embeddedContentView().superview()
    form_frame = direct.embeddedContentView().frame()
    top_gap = float(document.frame().size.height) - float(
        form_frame.origin.y + form_frame.size.height
    )
    assert top_gap == pytest.approx(20.0)
    assert float(form_frame.origin.y) > 0.0
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


def test_main_window_job_selection_preserves_scroll_position() -> None:
    NSApplication.sharedApplication()
    snapshot = _snapshot()
    snapshot.recent_jobs = [
        {**snapshot.recent_jobs[0], "job_id": f"done-{index}"}
        for index in range(18)
    ]
    controller = PhotosMcpMainWindowController.alloc().initWithMenuController_(
        _menu_controller(snapshot)
    )
    controller.showTab_("jobs")
    scroll = controller._job_scroll_view
    clip = scroll.contentView()
    max_origin_y = float(scroll.documentView().frame().size.height) - float(
        clip.bounds().size.height
    )
    expected_offset = 234.0
    clip.scrollToPoint_(NSMakePoint(0.0, max_origin_y - expected_offset))
    scroll.reflectScrolledClipView_(clip)

    controller.selectJob_(SimpleNamespace(identifier=lambda: "done-9"))

    rebuilt_scroll = controller._job_scroll_view
    rebuilt_clip = rebuilt_scroll.contentView()
    rebuilt_max_origin_y = float(
        rebuilt_scroll.documentView().frame().size.height
    ) - float(rebuilt_clip.bounds().size.height)
    actual_offset = rebuilt_max_origin_y - float(rebuilt_clip.bounds().origin.y)
    assert controller._selected_job_id == "done-9"
    assert actual_offset == pytest.approx(expected_offset)


def test_main_window_job_removal_selects_adjacent_row_and_preserves_scroll() -> None:
    NSApplication.sharedApplication()
    snapshot = _snapshot()
    snapshot.recent_jobs = [
        {**snapshot.recent_jobs[0], "job_id": f"done-{index}"}
        for index in range(18)
    ]
    controller = PhotosMcpMainWindowController.alloc().initWithMenuController_(
        _menu_controller(snapshot)
    )
    controller.showTab_("jobs")
    removed_id = controller._job_visible_ids[9]
    expected_selected_id = controller._job_visible_ids[10]
    controller._selected_job_id = removed_id
    scroll = controller._job_scroll_view
    clip = scroll.contentView()
    max_origin_y = float(scroll.documentView().frame().size.height) - float(
        clip.bounds().size.height
    )
    expected_offset = 312.0
    clip.scrollToPoint_(NSMakePoint(0.0, max_origin_y - expected_offset))
    scroll.reflectScrolledClipView_(clip)
    snapshot.recent_jobs = [
        job for job in snapshot.recent_jobs if job["job_id"] != removed_id
    ]

    controller.refreshWithSnapshot_(snapshot)

    rebuilt_scroll = controller._job_scroll_view
    rebuilt_clip = rebuilt_scroll.contentView()
    rebuilt_max_origin_y = float(
        rebuilt_scroll.documentView().frame().size.height
    ) - float(rebuilt_clip.bounds().size.height)
    actual_offset = rebuilt_max_origin_y - float(rebuilt_clip.bounds().origin.y)
    assert controller._selected_job_id == expected_selected_id
    assert actual_offset == pytest.approx(expected_offset)


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
        "사진 위치",
        "분석할 사진",
        "분석 방법",
        "실행 전 확인",
        "분류 기준",
        "분류 범위를 확인해 주세요",
    }.issubset(labels)
    assert {"기간 지정", "스크린샷 제외", "취소", "50장 분류 시작", "열기", "선택"}.issubset(
        str(button.title() or "") for button in buttons
    )
    assert "Google Photos" in labels
    assert controller._progress_status_labels[1].stringValue() == "진행 중"
    assert controller._progress_status_labels[2].stringValue() == ""
    assert all(str(button.accessibilityLabel() or "") for button in buttons)
    assert all(button.nextKeyView() is not None for button in buttons)
    controller.shutdown()


def _frames_overlap(first, second) -> bool:
    first_frame = first.frame()
    second_frame = second.frame()
    return not (
        float(first_frame.origin.x + first_frame.size.width) <= float(second_frame.origin.x)
        or float(second_frame.origin.x + second_frame.size.width) <= float(first_frame.origin.x)
        or float(first_frame.origin.y + first_frame.size.height) <= float(second_frame.origin.y)
        or float(second_frame.origin.y + second_frame.size.height) <= float(first_frame.origin.y)
    )


def _frame_mid_y(view) -> float:
    frame = view.frame()
    return float(frame.origin.y + (frame.size.height / 2.0))


@pytest.mark.parametrize("width", [860.0, 1080.0, 1320.0])
def test_direct_classification_layout_prevents_text_and_control_overlap(width) -> None:
    NSApplication.sharedApplication()
    controller = PhotosMcpDirectClassificationController.alloc().initWithMenuController_service_(
        _menu_controller(_snapshot()),
        SimpleNamespace(),
    )

    controller.layoutForWidth_(width)

    assert float(controller._content_root.frame().size.width) == width
    for title, description, button in (
        (
            controller._local_title_label,
            controller._local_description_label,
            controller._local_folder_button,
        ),
        (
            controller._google_title_label,
            controller._google_description_label,
            controller._google_photos_button,
        ),
    ):
        assert not _frames_overlap(title, button)
        assert not _frames_overlap(description, button)

    for step in range(1, 5):
        assert not _frames_overlap(controller._section_helpers[step], controller._section_status_labels[step])
    assert not _frames_overlap(controller._section_status_labels[4], controller._refresh_button)
    assert not _frames_overlap(controller._read_only_label, controller._cancel_button)
    controller.shutdown()


@pytest.mark.parametrize("width", [860.0, 1080.0, 1320.0])
def test_direct_classification_step_labels_share_badge_centerline(width) -> None:
    NSApplication.sharedApplication()
    controller = PhotosMcpDirectClassificationController.alloc().initWithMenuController_service_(
        _menu_controller(_snapshot()),
        SimpleNamespace(),
    )

    controller.layoutForWidth_(width)

    for step in range(1, 5):
        progress_badge, progress_value, _ = controller._progress_badges[step]
        assert _frame_mid_y(progress_value) == pytest.approx(14.0, abs=0.1)
        assert _frame_mid_y(controller._progress_labels[step]) == pytest.approx(
            _frame_mid_y(progress_badge), abs=0.1
        )
        assert _frame_mid_y(controller._progress_status_labels[step]) == pytest.approx(
            _frame_mid_y(progress_badge), abs=0.1
        )

        section_badge, section_value, _ = controller._section_badges[step]
        assert _frame_mid_y(section_value) == pytest.approx(14.0, abs=0.1)
        assert _frame_mid_y(controller._section_titles[step]) == pytest.approx(
            _frame_mid_y(section_badge), abs=0.1
        )
        assert _frame_mid_y(controller._section_status_labels[step]) == pytest.approx(
            _frame_mid_y(section_badge), abs=0.1
        )

    controller.shutdown()


def test_direct_classification_labels_truncate_safely_and_keep_full_tooltips() -> None:
    NSApplication.sharedApplication()
    controller = PhotosMcpDirectClassificationController.alloc().initWithMenuController_service_(
        _menu_controller(_snapshot()),
        SimpleNamespace(),
    )

    label = controller._google_description_label

    assert label.maximumNumberOfLines() == 1
    assert label.toolTip() == "직접 선택한 사진 가져오기"
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
        and "사진과 앨범은 변경되지 않습니다" in str(view.stringValue() or "")
    )

    assert float(notice.frame().origin.y) == 15.0
    assert float(notice.frame().size.height) == 20.0
    controller.shutdown()


def test_direct_classification_marks_completed_steps_after_scope_preview() -> None:
    NSApplication.sharedApplication()
    controller = PhotosMcpDirectClassificationController.alloc().initWithMenuController_service_(
        _menu_controller(_snapshot()),
        SimpleNamespace(),
    )
    controller._albums_loaded = True
    controller._preview_generation = 1
    command = controller.commandFromControls()
    preview = SimpleNamespace(
        candidate_count=200,
        count_is_lower_bound=True,
        run_count=50,
        analyze_ready_count=200,
        download_required_count=0,
        message="선택한 범위를 분석할 수 있습니다.",
        can_run=True,
    )
    controller._pending_preview = (1, command, preview)

    controller.previewFinished_(None)

    assert [controller._progress_status_labels[index].stringValue() for index in range(1, 5)] == [
        "완료",
        "완료",
        "완료",
        "준비됨",
    ]
    assert controller._summary_candidate.stringValue() == "200장 이상"
    assert controller._summary_run.stringValue() == "50장"
    assert controller._summary_download.stringValue() == "0장"
    assert controller._run_button.title() == "50장 분류 시작"
    assert controller._run_button.isEnabled() is True
    for step in range(1, 4):
        badge, label, icon = controller._progress_badges[step]
        assert label.isHidden() is False
        assert label.stringValue() == str(step)
        assert icon.isHidden() is True
    controller.shutdown()


def test_direct_classification_progress_status_stays_with_its_title() -> None:
    NSApplication.sharedApplication()
    controller = PhotosMcpDirectClassificationController.alloc().initWithMenuController_service_(
        _menu_controller(_snapshot()),
        SimpleNamespace(),
    )

    for width in (860.0, 1440.0, 2200.0):
        controller.layoutForWidth_(width)
        for step in range(1, 5):
            title = controller._progress_labels[step]
            status = controller._progress_status_labels[step]
            title_max_x = float(title.frame().origin.x + title.frame().size.width)
            status_x = float(status.frame().origin.x)
            assert 4.0 <= status_x - title_max_x <= 8.0
            if step < 4:
                connector = controller._progress_connectors[step]
                status_max_x = float(status.frame().origin.x + status.frame().size.width)
                assert float(connector.frame().origin.x) >= status_max_x + 7.0

    controller.shutdown()


def test_direct_classification_only_completes_final_step_after_job_is_accepted() -> None:
    NSApplication.sharedApplication()
    controller = PhotosMcpDirectClassificationController.alloc().initWithMenuController_service_(
        _menu_controller(_snapshot()),
        SimpleNamespace(),
    )
    controller._albums_loaded = True
    command = controller.commandFromControls()
    controller._last_preview_command = command
    controller._last_preview = SimpleNamespace(can_run=True, run_count=50)
    controller._update_step_states()

    assert controller._progress_status_labels[4].stringValue() == "준비됨"
    assert controller._progress_badges[4][0].accessibilityLabel() == "4단계 준비됨"

    controller._run_accepted = True
    controller._update_step_states()

    assert controller._progress_status_labels[4].stringValue() == "완료"
    assert controller._progress_badges[4][0].accessibilityLabel() == "4단계 완료"
    badge, label, icon = controller._progress_badges[4]
    assert label.isHidden() is False
    assert label.stringValue() == "4"
    assert icon.isHidden() is True
    controller.shutdown()


@pytest.mark.parametrize("width", [860.0, 1320.0, 2200.0])
def test_direct_classification_source_cards_are_vertically_centered(width) -> None:
    NSApplication.sharedApplication()
    controller = PhotosMcpDirectClassificationController.alloc().initWithMenuController_service_(
        _menu_controller(_snapshot()),
        SimpleNamespace(),
    )

    controller.layoutForWidth_(width)

    card_center_y = 39.0
    for symbol in (
        controller._apple_source_symbol,
        controller._local_source_symbol,
        controller._google_source_symbol,
    ):
        assert _frame_mid_y(symbol) == pytest.approx(card_center_y, abs=0.1)
    assert _frame_mid_y(controller._local_folder_button) == pytest.approx(card_center_y, abs=0.1)
    assert _frame_mid_y(controller._google_photos_button) == pytest.approx(card_center_y, abs=0.1)
    assert _frame_mid_y(controller._apple_status_dot) == pytest.approx(29.0, abs=0.1)
    for title, description in (
        (controller._apple_title_label, controller._album_status_label),
        (controller._local_title_label, controller._local_description_label),
        (controller._google_title_label, controller._google_description_label),
    ):
        assert _frame_mid_y(title) == pytest.approx(49.0, abs=0.1)
        assert _frame_mid_y(description) == pytest.approx(29.0, abs=0.1)

    controller.shutdown()


@pytest.mark.parametrize("width", [860.0, 1080.0, 1320.0, 2200.0])
def test_direct_classification_form_rows_and_refresh_action_are_centered(width) -> None:
    NSApplication.sharedApplication()
    controller = PhotosMcpDirectClassificationController.alloc().initWithMenuController_service_(
        _menu_controller(_snapshot()),
        SimpleNamespace(),
    )

    controller.layoutForWidth_(width)

    assert _frame_mid_y(controller._album_field_label) == pytest.approx(127.0, abs=0.1)
    assert _frame_mid_y(controller._album_popup) == pytest.approx(127.0, abs=0.1)
    for control in (
        controller._period_checkbox,
        controller._start_field,
        controller._date_separator_label,
        controller._end_field,
    ):
        assert _frame_mid_y(control) == pytest.approx(86.0, abs=0.1)
    for control in (
        controller._recent_button,
        controller._year_button,
        controller._period_helper_label,
    ):
        assert _frame_mid_y(control) == pytest.approx(45.0, abs=0.1)

    assert _frame_mid_y(controller._profile_field_label) == pytest.approx(88.0, abs=0.1)
    assert _frame_mid_y(controller._profile_popup) == pytest.approx(88.0, abs=0.1)
    for control in (
        controller._exclude_checkbox,
        controller._limit_field_label,
        controller._limit_popup,
    ):
        assert _frame_mid_y(control) == pytest.approx(44.0, abs=0.1)

    assert _frame_mid_y(controller._refresh_button) == pytest.approx(84.0, abs=0.1)
    preview_width = float(controller._preview_card.frame().size.width)
    assert float(controller._section_status_labels[4].frame().origin.x) == pytest.approx(
        preview_width - 86.0, abs=0.1
    )
    for title, value, _divider in controller._metric_items:
        assert not _frames_overlap(title, controller._refresh_button)
        assert not _frames_overlap(value, controller._refresh_button)

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


def test_local_photo_single_view_supports_point_zoom_and_drag_pan(tmp_path) -> None:
    NSApplication.sharedApplication()
    root_path = tmp_path / "photos"
    root_path.mkdir()
    controller = PhotosMcpLocalPhotoSelectionController.alloc().initWithMenuController_service_sourcePath_selectedPhotoIds_(
        _menu_controller(_snapshot()),
        SimpleNamespace(),
        str(root_path),
        (),
    )
    controller._content.setFrame_(NSMakeRect(0.0, 0.0, 700.0, 616.0))
    controller._view_mode = "single"
    controller._layout_content()
    controller._single_set_photo_image(NSImage.alloc().initWithSize_(NSMakeSize(1600.0, 1200.0)))

    assert controller._single_scroll.documentView() is controller._single_image
    assert controller._single_is_fit is True
    click_point = controller._single_visible_center_point()
    controller.toggle_zoom_at_view_point(click_point)
    assert controller._single_is_fit is False
    assert controller.can_pan_image() is True

    controller.begin_pan_at_window_point(NSMakePoint(300.0, 240.0))
    start_origin = controller._single_scroll.contentView().bounds().origin
    controller.pan_image_to_window_point(NSMakePoint(220.0, 180.0))
    moved_origin = controller._single_scroll.contentView().bounds().origin

    assert float(moved_origin.x) == pytest.approx(float(start_origin.x) + 80.0)
    assert float(moved_origin.y) == pytest.approx(float(start_origin.y) - 60.0)
    controller.end_pan()
    controller.singleFitPhoto_(None)
    assert controller._single_is_fit is True
    controller.shutdown()


def test_local_photo_single_canvas_keeps_navigation_and_selection_keys(tmp_path) -> None:
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
    controller._focused_path = photos[0].path
    controller._view_mode_control.setSelectedSegment_(1)
    controller.viewModeChanged_(controller._view_mode_control)

    controller._single_image.keyDown_(SimpleNamespace(keyCode=lambda: 124))
    controller._single_image.keyDown_(SimpleNamespace(keyCode=lambda: 49))

    assert controller._focused_path == photos[1].path
    assert controller._selected_paths == {photos[1].path}
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
    preview_height = float(controller._inspector_image.frame().size.height)
    viewport_height = float(controller._photo_scroll.contentSize().height)
    assert float(controller._photo_scroll.frame().origin.y) >= card_top + 14.0
    assert float(controller._photo_scroll.frame().size.height) >= 120.0
    assert preview_height == 120.0
    assert preview_height <= viewport_height * 0.55
    assert float(controller._file_date.frame().origin.y + controller._file_date.frame().size.height) <= viewport_height
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


def test_direct_classification_local_source_replaces_apple_scope_and_keeps_common_options(tmp_path) -> None:
    NSApplication.sharedApplication()
    photos = []
    for index in range(3):
        path = tmp_path / f"photo-{index}.jpg"
        path.write_bytes(b"photo")
        photos.append(str(path))
    controller = PhotosMcpDirectClassificationController.alloc().initWithMenuController_service_(
        _menu_controller(_snapshot()),
        SimpleNamespace(),
    )

    controller.localPhotoSelectionPrepared_({"paths": photos})
    controller._limit_popup.selectItemWithTitle_("10장")
    command = controller.commandFromControls()

    assert controller._selected_source == "local"
    assert controller._album_popup.isHidden() is True
    assert controller._source_scope_title.isHidden() is False
    assert controller._source_scope_status.stringValue() == "분석 가능 3장"
    assert command.source == "local"
    assert command.selected_photo_ids == tuple(photos)
    assert command.limit == 10
    controller.shutdown()


def test_direct_classification_source_change_clears_previous_apple_preview() -> None:
    NSApplication.sharedApplication()
    controller = PhotosMcpDirectClassificationController.alloc().initWithMenuController_service_(
        _menu_controller(_snapshot()),
        SimpleNamespace(),
    )
    controller._summary_candidate.setStringValue_("200장 이상")
    controller._summary_download.setStringValue_("1장")
    controller._run_button.setEnabled_(True)

    controller._set_selected_source("local", request_preview=False)

    assert controller._summary_candidate.stringValue() == "확인 필요"
    assert controller._summary_download.stringValue() == "-"
    assert controller._run_button.isEnabled() is False
    assert controller._progress_status_labels[2].stringValue() == "진행 중"
    controller.shutdown()


def test_direct_classification_source_cards_select_without_opening_editors() -> None:
    NSApplication.sharedApplication()
    controller = PhotosMcpDirectClassificationController.alloc().initWithMenuController_service_(
        _menu_controller(_snapshot()),
        SimpleNamespace(),
    )

    controller._local_card_button.performClick_(None)
    assert controller._selected_source == "local"
    assert controller._source_scope_title.stringValue() == "로컬 사진을 선택해 주세요"
    controller._google_card_button.performClick_(None)
    assert controller._selected_source == "google_photos"
    assert controller._source_scope_title.stringValue() == "Google Photos에서 사진을 선택해 주세요"
    controller._apple_card_button.performClick_(None)
    assert controller._selected_source == "apple"
    assert controller._apple_source_button.isHidden() is True
    assert controller._local_card_button.accessibilityLabel() == "로컬 폴더 소스로 선택"
    assert controller._google_card_button.accessibilityLabel() == "Google Photos 소스로 선택"
    controller.shutdown()


def test_direct_classification_google_source_uses_prepared_paths_and_video_summary(tmp_path) -> None:
    NSApplication.sharedApplication()
    photos = []
    for index in range(2):
        path = tmp_path / f"google-{index}.jpg"
        path.write_bytes(b"photo")
        photos.append(str(path))
    controller = PhotosMcpDirectClassificationController.alloc().initWithMenuController_service_(
        _menu_controller(_snapshot()),
        SimpleNamespace(),
    )

    controller.googlePhotosSelectionPrepared_(
        {
            "session_id": "session-1",
            "paths": photos,
            "materialized_photo_count": 2,
            "excluded_video_count": 4,
        }
    )
    command = controller.commandFromControls()

    assert controller._selected_source == "google_photos"
    assert controller._source_scope_detail.stringValue() == "사진 2장 준비됨 · 동영상 4개 제외 · 임시 저장"
    assert controller._source_scope_status.stringValue() == "다운로드 완료 2 / 2"
    assert controller._source_progress.isHidden() is False
    assert controller._source_preview_button.isHidden() is False
    assert command.source == "local"
    assert command.selected_photo_ids == tuple(photos)
    controller.shutdown()


def test_direct_classification_google_reselection_clears_prepared_summary(tmp_path) -> None:
    NSApplication.sharedApplication()
    photo = tmp_path / "google.jpg"
    photo.write_bytes(b"photo")
    controller = PhotosMcpDirectClassificationController.alloc().initWithMenuController_service_(
        _menu_controller(_snapshot()),
        SimpleNamespace(),
    )
    controller.googlePhotosSelectionPrepared_(
        {
            "session_id": "session-1",
            "paths": [str(photo)],
            "materialized_photo_count": 1,
        }
    )

    controller.googlePhotosSelectionReset_(None)

    assert controller._google_prepared == {}
    assert controller._google_preparation_progress == {}
    assert controller._source_scope_title.stringValue() == "Google Photos에서 사진을 선택해 주세요"
    assert controller._run_button.isEnabled() is False
    controller.shutdown()


def test_direct_classification_google_progress_is_visible_before_preparation() -> None:
    NSApplication.sharedApplication()
    controller = PhotosMcpDirectClassificationController.alloc().initWithMenuController_service_(
        _menu_controller(_snapshot()),
        SimpleNamespace(),
    )
    controller.selectGoogleSource_(None)

    controller.googlePhotosPreparationProgress_(
        {
            "state": "downloading",
            "total_photo_count": 10,
            "completed_photo_count": 4,
            "excluded_video_count": 2,
            "progress_percent": 40.0,
        }
    )

    assert controller._source_scope_title.stringValue() == "Google Photos 사진 다운로드 중"
    assert controller._source_scope_status.stringValue() == "다운로드 4 / 10 · 40%"
    assert controller._source_progress.doubleValue() == 4.0
    assert controller._source_preview_button.isHidden() is True
    assert controller._run_button.isEnabled() is False
    controller.shutdown()


def test_local_browser_read_only_preview_filters_paths_and_hides_classification_controls(tmp_path) -> None:
    NSApplication.sharedApplication()
    first = tmp_path / "first.jpg"
    second = tmp_path / "second.jpg"
    other = tmp_path / "other.jpg"
    for path in (first, second, other):
        path.write_bytes(b"photo")
    allowed = (str(first.resolve()), str(second.resolve()))
    controller = PhotosMcpLocalPhotoSelectionController.alloc().initWithMenuController_service_sourcePath_selectedPhotoIds_(
        _menu_controller(_snapshot()),
        SimpleNamespace(),
        str(tmp_path),
        allowed,
    )
    controller._photos = [
        LocalPhoto(str(path.resolve()), path.name, path.stat().st_mtime, path.stat().st_size, 100, 100)
        for path in (first, second, other)
    ]

    controller.enableReadOnlyPreviewMode_({"title": "Google Photos 미리보기", "paths": allowed})

    assert controller.window().title() == "Google Photos 미리보기"
    assert [photo.path for photo in controller._visible_photos()] == list(allowed)
    assert controller._settings_card.isHidden() is True
    assert controller._select_all_button.isHidden() is True
    assert controller._run_button.isEnabled() is False
    assert controller.is_read_only_preview() is True
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
