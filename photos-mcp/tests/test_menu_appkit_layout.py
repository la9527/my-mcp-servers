from __future__ import annotations

import asyncio
from threading import Event
from types import SimpleNamespace

from AppKit import (
    NSApplication,
    NSButton,
    NSCollectionView,
    NSImageView,
    NSScrollView,
    NSStackView,
    NSTextField,
    NSWindowZoomButton,
)
from Foundation import NSMakeSize
import pytest

from photos_mcp.menu_app import (
    PhotosMcpEnvironmentController,
    PhotosMcpMenuController,
    PhotosMcpPopoverController,
    PhotosMcpResultsController,
)
from photos_mcp.direct_classification_appkit import PhotosMcpDirectClassificationController
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
    assert {"기간 지정", "스크린샷 제외", "취소", "분류 시작"}.issubset(
        str(button.title() or "") for button in buttons
    )
    assert all(str(button.accessibilityLabel() or "") for button in buttons)
    assert all(button.nextKeyView() is not None for button in buttons)
    controller.shutdown()


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
