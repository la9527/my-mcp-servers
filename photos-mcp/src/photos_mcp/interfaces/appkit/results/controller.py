"""Responsive, continuously scrolling AppKit result gallery."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
from threading import Thread
from typing import Any

import objc
from AppKit import (
    NSAlert,
    NSAlertFirstButtonReturn,
    NSAlertSecondButtonReturn,
    NSAlertThirdButtonReturn,
    NSAlertStyleWarning,
    NSBackingStoreBuffered,
    NSButton,
    NSCache,
    NSColor,
    NSCollectionView,
    NSCollectionViewFlowLayout,
    NSCollectionViewItem,
    NSCollectionViewScrollDirectionVertical,
    NSCollectionViewScrollPositionTop,
    NSEdgeInsetsMake,
    NSImage,
    NSImageScaleProportionallyUpOrDown,
    NSImageView,
    NSLineBreakByWordWrapping,
    NSMakeRect,
    NSModalResponseOK,
    NSOpenPanel,
    NSPasteboard,
    NSPasteboardTypeString,
    NSPopUpButton,
    NSSavePanel,
    NSScrollView,
    NSTextField,
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
    NSWindowZoomButton,
)
from Foundation import NSIndexPath, NSMakeSize, NSSet, NSUserDefaults

from photos_mcp.application.result_presenter import (
    result_category,
    result_item_failure,
    sanitized_result_export_payload,
    sorted_result_items,
)
from photos_mcp.interfaces.appkit.results.photo_viewer import PhotosMcpPhotoViewerController
from photos_mcp.application.export_service import (
    execute_selected_export,
    prepare_retry_originals,
    prepare_selected_export,
)
from photos_mcp.application.run_support import call_vendor
from photos_mcp.interfaces.appkit.shared.theme import accent_color, app_font
from photos_mcp.application.viewer_asset_service import hydrate_viewer_source_paths


_RESULT_WINDOW_WIDTH = 1320.0
_RESULT_WINDOW_HEIGHT = 820.0
_RESULT_MIN_WIDTH = 1100.0
_RESULT_MIN_HEIGHT = 680.0
_ITEM_IDENTIFIER = "PhotosMcpResultItem"
_DENSITY_DEFAULTS_KEY = "PhotosMcpResultGalleryDensity"
_DENSITY_WIDTHS = (178.0, 208.0, 238.0, 276.0)
_DEFAULT_DENSITY_INDEX = 2
_IMAGE_CACHE = NSCache.alloc().init()
_IMAGE_CACHE.setCountLimit_(128)

def initial_density_index(stored_value: Any) -> int:
    if stored_value is None:
        return _DEFAULT_DENSITY_INDEX
    try:
        value = int(stored_value)
    except (TypeError, ValueError):
        return _DEFAULT_DENSITY_INDEX
    return value if 0 <= value < len(_DENSITY_WIDTHS) else _DEFAULT_DENSITY_INDEX


def _status_color(status: str) -> Any:
    return {
        "success": NSColor.systemGreenColor(),
        "warning": NSColor.systemYellowColor(),
        "error": NSColor.systemRedColor(),
    }.get(status, NSColor.secondaryLabelColor())


def _cached_image(path: str) -> Any | None:
    if not path or not Path(path).is_file():
        return None
    cached = _IMAGE_CACHE.objectForKey_(path)
    if cached is not None:
        return cached
    image = NSImage.alloc().initWithContentsOfFile_(path)
    if image is not None:
        _IMAGE_CACHE.setObject_forKey_(image, path)
    return image


class PhotosMcpResultCollectionItem(NSCollectionViewItem):
    """Reusable card that loads only the preview currently represented by the cell."""

    def loadView(self) -> None:
        root = NSView.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, 220.0, 260.0))
        root.setWantsLayer_(True)
        root.layer().setCornerRadius_(13.0)
        root.layer().setBorderWidth_(1.0)
        root.layer().setBackgroundColor_(
            NSColor.controlBackgroundColor().colorWithAlphaComponent_(0.48).CGColor()
        )
        self.setView_(root)

        self._image_view = NSImageView.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, 1.0, 1.0))
        self._image_view.setImageScaling_(NSImageScaleProportionallyUpOrDown)
        self._image_view.setAccessibilityElement_(False)
        root.addSubview_(self._image_view)
        self._placeholder = self._label(root, "미리보기 없음", 9.2)
        self._placeholder.setAlignment_(1)
        self._badge = self._label(root, "", 9.4, bold=True)
        self._score = self._label(root, "", 13.0, bold=True)
        self._score.setAlignment_(2)
        self._reason = self._label(root, "", 9.0)
        self._reason.setTextColor_(NSColor.secondaryLabelColor())
        self._reason.setLineBreakMode_(NSLineBreakByWordWrapping)
        self._reason.setMaximumNumberOfLines_(2)
        self._reason.setUsesSingleLineMode_(False)
        self._export_check = NSButton.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, 28.0, 28.0))
        self._export_check.setButtonType_(3)
        self._export_check.setTitle_("")
        self._export_check.setAccessibilityLabel_("내보내기 선택")
        root.addSubview_(self._export_check)

    def prepareForReuse(self) -> None:
        objc.super(PhotosMcpResultCollectionItem, self).prepareForReuse()
        self._image_view.setImage_(None)
        self._placeholder.setHidden_(False)

    def setSelected_(self, selected: bool) -> None:
        objc.super(PhotosMcpResultCollectionItem, self).setSelected_(selected)
        self._apply_selection_style()

    def viewDidLayout(self) -> None:
        objc.super(PhotosMcpResultCollectionItem, self).viewDidLayout()
        root = self.view()
        width = float(root.bounds().size.width)
        height = float(root.bounds().size.height)
        image_y = 82.0
        image_height = max(92.0, height - 90.0)
        self._image_view.setFrame_(NSMakeRect(7.0, image_y, max(1.0, width - 14.0), image_height))
        self._placeholder.setFrame_(NSMakeRect(14.0, image_y + image_height / 2.0 - 10.0, width - 28.0, 20.0))
        self._badge.setFrame_(NSMakeRect(12.0, 58.0, max(70.0, width - 88.0), 18.0))
        self._score.setFrame_(NSMakeRect(max(12.0, width - 64.0), 56.0, 52.0, 21.0))
        self._reason.setFrame_(NSMakeRect(12.0, 12.0, max(1.0, width - 24.0), 38.0))
        self._export_check.setFrame_(NSMakeRect(max(8.0, width - 36.0), max(8.0, height - 36.0), 28.0, 28.0))

    @objc.python_method
    def configure(self, item: dict[str, Any], controller: Any | None = None) -> None:
        self.setRepresentedObject_(item)
        self._export_check.setState_(1 if bool(item.get("selected")) else 0)
        self._export_check.setIdentifier_(str(item.get("_export_token") or ""))
        self._export_check.setAccessibilityLabel_(
            f"내보내기 선택 {str(item.get('_export_token') or '').replace('result-', '')}"
        )
        self._export_check.setTarget_(controller)
        self._export_check.setAction_("toggleItemSelection:")
        self._export_check.setEnabled_(not bool(getattr(controller, "_export_in_progress", False)))
        failure = result_item_failure(item)
        category = result_category(item)
        category_label = "실패" if failure else {
            "recommended": "추천",
            "review": "검토 필요",
        }[category]
        cluster_size = max(1, int(item.get("scene_cluster_size") or 1))
        cluster_rank = max(1, int(item.get("cluster_rank") or 1))
        recommendation_slot = max(0, int(item.get("recommendation_slot") or 0))
        if not failure and cluster_size > 1:
            if category == "recommended":
                slot = recommendation_slot or min(cluster_rank, 2)
                category_label = f"추천 {slot}/2 · 같은 장면 {cluster_size}장"
            else:
                category_label = f"대안 · {cluster_size}장 중 {cluster_rank}위"
        tone = "error" if failure else {
            "recommended": "success",
            "review": "warning",
        }[category]
        reason = failure or str(item.get("scene_description") or "분석 결과를 확인하세요.")
        score = float(item.get("total_score") or item.get("quality_score") or 0.0)
        image = None if failure else _cached_image(str(item.get("preview_path") or ""))
        self._image_view.setImage_(image)
        self._placeholder.setHidden_(image is not None)
        self._placeholder.setStringValue_("분석 실패" if failure else "미리보기 없음")
        self._placeholder.setTextColor_(_status_color(tone))
        self._badge.setStringValue_(category_label)
        self._badge.setTextColor_(_status_color(tone))
        self._score.setStringValue_(f"{score:.0f}")
        self._score.setTextColor_(_status_color(tone))
        self._reason.setStringValue_(reason)
        self._reason.setToolTip_(reason)
        self.view().setAccessibilityLabel_(f"{category_label}, 점수 {score:.0f}, {reason}")
        self._apply_selection_style()
        self.viewDidLayout()

    @objc.python_method
    def _apply_selection_style(self) -> None:
        if self.view() is None:
            return
        selected = bool(self.isSelected())
        self.view().layer().setBorderWidth_(2.0 if selected else 1.0)
        color = accent_color() if selected else NSColor.separatorColor()
        self.view().layer().setBorderColor_(color.colorWithAlphaComponent_(0.72 if selected else 0.46).CGColor())

    @objc.python_method
    def _label(self, parent: Any, text: str, size: float, *, bold: bool = False) -> Any:
        label = NSTextField.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, 1.0, 1.0))
        label.setBezeled_(False)
        label.setDrawsBackground_(False)
        label.setEditable_(False)
        label.setSelectable_(False)
        label.setStringValue_(text)
        label.setFont_(app_font(size, "semibold" if bold else "regular"))
        parent.addSubview_(label)
        return label


class PhotosMcpResultsController(NSWindowController):
    """Browse up to 1,000 persisted results without paging or eager image loading."""

    def initWithMenuController_(self, menu_controller: Any):
        style = (
            NSWindowStyleMaskTitled
            | NSWindowStyleMaskClosable
            | NSWindowStyleMaskMiniaturizable
            | NSWindowStyleMaskResizable
        )
        window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0.0, 0.0, _RESULT_WINDOW_WIDTH, _RESULT_WINDOW_HEIGHT),
            style,
            NSBackingStoreBuffered,
            False,
        )
        self = objc.super(PhotosMcpResultsController, self).initWithWindow_(window)
        if self is None:
            return None
        self._menu_controller = menu_controller
        self._payload: dict[str, Any] = {}
        self._items: list[dict[str, Any]] = []
        self._visible_items: list[dict[str, Any]] = []
        self._viewer_items_by_id: dict[str, dict[str, Any]] = {}
        self._photo_id_by_export_token: dict[str, str] = {}
        self._filter = "all"
        self._selected_photo_id = ""
        defaults = NSUserDefaults.standardUserDefaults()
        stored_density = defaults.objectForKey_(_DENSITY_DEFAULTS_KEY)
        self._density_index = initial_density_index(stored_density)
        self._computed_columns = 3
        self._viewer_controller = PhotosMcpPhotoViewerController.alloc().init()
        self._selection_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="photos-mcp-review-selection",
        )
        self._export_worker: Thread | None = None
        self._pending_export_options: dict[str, Any] = {}
        self._result_generation = 0
        self._export_generation = 0
        self._export_in_progress = False
        self._selection_persist_error = ""
        self._is_laying_out = False
        window.setTitle_("사진 분류 결과")
        window.setMinSize_(NSMakeSize(_RESULT_MIN_WIDTH, _RESULT_MIN_HEIGHT))
        window.setFrameAutosaveName_("PhotosMcpResultsWindow")
        window.setCollectionBehavior_(NSWindowCollectionBehaviorFullScreenPrimary)
        zoom_button = window.standardWindowButton_(NSWindowZoomButton)
        if zoom_button is not None:
            zoom_button.setEnabled_(True)
        window.setDelegate_(self)
        window.setReleasedWhenClosed_(False)
        self._build_view()
        return self

    def showWithResult_(self, payload: dict[str, Any]) -> None:
        if self._export_in_progress:
            self.window().makeKeyAndOrderFront_(None)
            self._show_alert(
                "내보내기가 진행 중입니다",
                "현재 내보내기가 끝난 뒤 다른 사진 결과를 열어주세요.",
            )
            return
        self._result_generation += 1
        self._export_generation += 1
        self._export_in_progress = False
        self._selection_persist_error = ""
        self._payload = dict(payload or {})
        self._items = sorted_result_items(self._payload)[:1000]
        self._photo_id_by_export_token = {}
        for index, item in enumerate(self._items, start=1):
            token = f"result-{index}"
            item["_export_token"] = token
            self._photo_id_by_export_token[token] = str(item.get("photo_id") or "")
        private_items = hydrate_viewer_source_paths(self._payload, self._items)
        self._viewer_items_by_id = {
            str(item.get("photo_id") or ""): item for item in private_items
        }
        self._filter = "all"
        self._selected_photo_id = str(self._items[0].get("photo_id") or "") if self._items else ""
        self._reload_results(scroll_to_top=True)
        window = self.window()
        window.center()
        window.makeKeyAndOrderFront_(None)

    def closeWindow_(self, _sender) -> None:
        self.window().performClose_(None)

    def filterResults_(self, sender) -> None:
        value = self._sender_identifier(sender)
        if value:
            self._filter = value
        filtered = self._filtered_items()
        if filtered and not any(str(item.get("photo_id") or "") == self._selected_photo_id for item in filtered):
            self._selected_photo_id = str(filtered[0].get("photo_id") or "")
        if not filtered:
            self._selected_photo_id = ""
        self._reload_results(scroll_to_top=True)

    def changeDensity_(self, sender) -> None:
        direction = self._sender_identifier(sender)
        anchor = self._top_visible_index()
        if direction == "smaller":
            self._density_index = max(0, self._density_index - 1)
        elif direction == "larger":
            self._density_index = min(len(_DENSITY_WIDTHS) - 1, self._density_index + 1)
        NSUserDefaults.standardUserDefaults().setInteger_forKey_(self._density_index, _DENSITY_DEFAULTS_KEY)
        self._layout_view(anchor_index=anchor)

    def selectResultItem_(self, sender) -> None:
        photo_id = self._sender_identifier(sender)
        if photo_id:
            self._selected_photo_id = photo_id
            self._sync_selection()
            self._update_inspector()

    def openSelectedViewer_(self, _sender) -> None:
        if self._selected_item() is not None:
            viewer_items = [
                self._viewer_items_by_id.get(str(item.get("photo_id") or ""), item)
                for item in self._visible_items
            ]
            self._viewer_controller.show_items(viewer_items, self._selected_photo_id)

    def revealSelected_(self, _sender) -> None:
        selected = self._selected_item()
        preview_path = str(selected.get("preview_path") or "") if selected else ""
        if preview_path and Path(preview_path).exists():
            subprocess.Popen(
                ["/usr/bin/open", "-R", preview_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

    def copyResultSummary_(self, _sender) -> None:
        selected = self._selected_item()
        selected_export = sanitized_result_export_payload(
            {"job_id": self._payload.get("job_id"), "items": [selected] if selected else []}
        )
        payload = {
            "job_id": str(self._payload.get("job_id") or ""),
            "photo_count": len(self._items),
            "selected_photo": selected_export["items"][0] if selected_export["items"] else {},
        }
        pasteboard = NSPasteboard.generalPasteboard()
        pasteboard.clearContents()
        pasteboard.setString_forType_(json.dumps(payload, ensure_ascii=False, indent=2), NSPasteboardTypeString)

    def exportResults_(self, _sender) -> None:
        panel = NSSavePanel.savePanel()
        job_id = str(self._payload.get("job_id") or "results")[:12]
        panel.setNameFieldStringValue_(f"photos-mcp-{job_id}-results.json")
        panel.setAllowedFileTypes_(["json"])
        panel.setCanCreateDirectories_(True)
        if panel.runModal() != NSModalResponseOK or panel.URL() is None:
            return
        try:
            Path(str(panel.URL().path())).write_text(
                json.dumps(sanitized_result_export_payload(self._payload), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            alert = NSAlert.alloc().init()
            alert.setMessageText_("결과를 내보내지 못했습니다")
            alert.setInformativeText_(str(exc))
            alert.setAlertStyle_(NSAlertStyleWarning)
            alert.addButtonWithTitle_("확인")
            alert.runModal()

    def toggleItemSelection_(self, sender) -> None:
        if self._export_in_progress:
            return
        photo_id = self._photo_id_by_export_token.get(self._sender_identifier(sender), "")
        selected = bool(sender.state())
        if not photo_id:
            return
        for item in self._items:
            if str(item.get("photo_id") or "") == photo_id:
                item["selected"] = selected
                break
        self._refresh_selection_controls()
        self._persist_single_selection(photo_id, selected)

    def selectAllExport_(self, _sender) -> None:
        self._set_all_selection(True)

    def clearAllExport_(self, _sender) -> None:
        self._set_all_selection(False)

    def exportSelected_(self, _sender) -> None:
        result_generation = self._result_generation
        selected_count = self._selected_export_count()
        if selected_count <= 0:
            self._show_alert("내보낼 사진이 없습니다", "사진 카드에서 내보낼 사진을 선택하세요.")
            return

        chooser = NSAlert.alloc().init()
        chooser.setMessageText_(f"선택한 {selected_count}장 내보내기")
        chooser.setInformativeText_(
            "Apple 사진 앨범과 로컬 분류 폴더를 함께 선택하거나 한 곳만 선택할 수 있습니다."
        )
        chooser.addButtonWithTitle_("두 곳 모두")
        chooser.addButtonWithTitle_("Apple 사진 앨범")
        chooser.addButtonWithTitle_("로컬 분류 폴더")
        chooser.addButtonWithTitle_("취소")
        response = chooser.runModal()
        if result_generation != self._result_generation:
            return
        if response not in {NSAlertFirstButtonReturn, NSAlertSecondButtonReturn, NSAlertThirdButtonReturn}:
            return

        wants_album = response in {NSAlertFirstButtonReturn, NSAlertSecondButtonReturn}
        wants_local = response in {NSAlertFirstButtonReturn, NSAlertThirdButtonReturn}
        options: dict[str, Any] = {
            "run_id": str(self._payload.get("job_id") or self._payload.get("run_id") or ""),
            "metadata_mode": "auto",
        }
        if wants_album:
            if wants_local:
                output_dir = self._choose_export_directory()
                if result_generation != self._result_generation:
                    return
                if not output_dir:
                    return
                options["output_dir"] = output_dir
            self._begin_export_flow()
            self._start_album_target_choice(options)
            return
        if wants_local:
            output_dir = self._choose_export_directory()
            if result_generation != self._result_generation:
                return
            if not output_dir:
                return
            options["output_dir"] = output_dir
        self._begin_export_flow()
        self._start_export_plan(options)

    def windowDidResize_(self, _notification) -> None:
        if not self._is_laying_out:
            self._layout_view(anchor_index=self._top_visible_index())

    @objc.python_method
    def rebuild(self) -> None:
        self._reload_results(scroll_to_top=False)

    def numberOfSectionsInCollectionView_(self, _collection_view) -> int:
        return 1

    def collectionView_numberOfItemsInSection_(self, _collection_view, _section: int) -> int:
        return len(self._visible_items)

    def collectionView_itemForRepresentedObjectAtIndexPath_(self, collection_view, index_path):
        item = collection_view.makeItemWithIdentifier_forIndexPath_(_ITEM_IDENTIFIER, index_path)
        index = int(index_path.item())
        if 0 <= index < len(self._visible_items):
            item.configure(self._visible_items[index], self)
        return item

    def collectionView_didSelectItemsAtIndexPaths_(self, _collection_view, index_paths) -> None:
        paths = list(index_paths or [])
        if not paths:
            return
        index = int(paths[0].item())
        if not 0 <= index < len(self._visible_items):
            return
        self._selected_photo_id = str(self._visible_items[index].get("photo_id") or "")
        self._update_inspector()
        self.openSelectedViewer_(None)

    @objc.python_method
    def _build_view(self) -> None:
        root = self.window().contentView()
        root.setWantsLayer_(True)
        root.layer().setBackgroundColor_(NSColor.windowBackgroundColor().CGColor())
        self._focusable: list[Any] = []

        self._title_label = self._label(root, "사진 분석 완료", 22.0, bold=True)
        self._subtitle_label = self._label(root, "", 11.0, secondary=True)
        self._summary_cards: dict[str, Any] = {}
        self._summary_labels: dict[str, Any] = {}
        for key, title, tone in (
            ("recommended", "추천", "success"),
            ("review", "검토 필요", "warning"),
        ):
            card = self._card(root, tone=tone)
            self._summary_cards[key] = card
            label = self._label(card, title, 13.5, bold=True)
            label.setTextColor_(_status_color(tone) if tone != "neutral" else NSColor.labelColor())
            self._summary_labels[key] = label

        self._filter_buttons: dict[str, Any] = {}
        for key in ("all", "recommended", "review"):
            self._filter_buttons[key] = self._button(root, "", "filterResults:", identifier=key)
        self._density_smaller = self._button(root, "−", "changeDensity:", identifier="smaller")
        self._density_label = self._label(root, "자동 3열", 9.5, bold=True)
        self._density_label.setAlignment_(1)
        self._density_larger = self._button(root, "+", "changeDensity:", identifier="larger")
        self._selection_label = self._label(root, "선택한 0장", 10.0, bold=True)
        self._select_all_button = self._button(root, "전체 선택", "selectAllExport:")
        self._clear_all_button = self._button(root, "전체 해제", "clearAllExport:")

        self._flow_layout = NSCollectionViewFlowLayout.alloc().init()
        self._flow_layout.setScrollDirection_(NSCollectionViewScrollDirectionVertical)
        self._flow_layout.setMinimumInteritemSpacing_(12.0)
        self._flow_layout.setMinimumLineSpacing_(12.0)
        self._flow_layout.setSectionInset_(NSEdgeInsetsMake(0.0, 0.0, 12.0, 0.0))
        self._collection_view = NSCollectionView.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, 1.0, 1.0))
        self._collection_view.setCollectionViewLayout_(self._flow_layout)
        self._collection_view.registerClass_forItemWithIdentifier_(PhotosMcpResultCollectionItem, _ITEM_IDENTIFIER)
        self._collection_view.setDataSource_(self)
        self._collection_view.setDelegate_(self)
        self._collection_view.setSelectable_(True)
        self._collection_view.setAllowsMultipleSelection_(False)
        self._collection_view.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        if hasattr(self._collection_view, "setBackgroundColors_"):
            self._collection_view.setBackgroundColors_([NSColor.clearColor()])

        self._scroll_view = NSScrollView.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, 1.0, 1.0))
        self._scroll_view.setHasVerticalScroller_(True)
        self._scroll_view.setAutohidesScrollers_(True)
        self._scroll_view.setDrawsBackground_(False)
        self._scroll_view.setDocumentView_(self._collection_view)
        root.addSubview_(self._scroll_view)

        self._empty_card = self._card(root, tone="neutral")
        self._empty_title = self._label(self._empty_card, "표시할 사진 결과가 없습니다", 15.0, bold=True)
        self._empty_title.setAlignment_(1)
        self._empty_detail = self._label(
            self._empty_card,
            "필터를 변경하거나 완료된 작업에서 다시 결과 보기를 선택하세요.",
            10.2,
            secondary=True,
        )
        self._empty_detail.setAlignment_(1)

        self._inspector_card = self._card(root, tone="neutral")
        self._inspector_image = NSImageView.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, 1.0, 1.0))
        self._inspector_image.setImageScaling_(NSImageScaleProportionallyUpOrDown)
        self._inspector_card.addSubview_(self._inspector_image)
        self._inspector_open = self._button(
            self._inspector_card,
            "",
            "openSelectedViewer:",
            accessibility_label="선택 사진 전체 화면으로 보기",
        )
        self._inspector_open.setBordered_(False)
        self._inspector_open.setTransparent_(True)
        self._inspector_title = self._label(self._inspector_card, "분석 요약", 12.0, bold=True)
        self._inspector_scene = self._label(self._inspector_card, "", 10.0, secondary=True)
        self._inspector_scene.setLineBreakMode_(NSLineBreakByWordWrapping)
        self._inspector_scene.setMaximumNumberOfLines_(3)
        self._inspector_scene.setUsesSingleLineMode_(False)
        self._inspector_metrics = self._label(self._inspector_card, "", 9.5, secondary=True)
        self._inspector_event = self._label(self._inspector_card, "", 9.5, secondary=True)

        self._finder_button = self._button(root, "Finder에서 보기", "revealSelected:")
        self._json_export_button = self._button(root, "결과 JSON", "exportResults:")
        self._export_button = self._button(root, "선택한 사진 내보내기", "exportSelected:", primary=True)
        self._close_button = self._button(root, "닫기", "closeWindow:", primary=True)
        self._layout_view()

    @objc.python_method
    def _layout_view(self, anchor_index: int | None = None) -> None:
        if self._is_laying_out:
            return
        self._is_laying_out = True
        try:
            root = self.window().contentView()
            width = max(_RESULT_MIN_WIDTH, float(root.bounds().size.width))
            height = max(_RESULT_MIN_HEIGHT, float(root.bounds().size.height))
            margin = 28.0
            gap = 18.0
            inspector_width = max(360.0, min(520.0, width * 0.29))
            gallery_width = width - margin * 2.0 - gap - inspector_width
            self._title_label.setFrame_(NSMakeRect(margin, height - 62.0, width - margin * 2.0, 30.0))
            self._subtitle_label.setFrame_(NSMakeRect(margin, height - 88.0, width - margin * 2.0, 20.0))

            tile_y = height - 162.0
            tile_gap = 10.0
            tile_width = (gallery_width - tile_gap) / 2.0
            for index, key in enumerate(("recommended", "review")):
                self._summary_cards[key].setFrame_(
                    NSMakeRect(margin + index * (tile_width + tile_gap), tile_y, tile_width, 58.0)
                )
                self._summary_labels[key].setFrame_(NSMakeRect(18.0, 18.0, tile_width - 36.0, 24.0))

            toolbar_y = tile_y - 48.0
            density_width = 150.0
            filter_area_width = gallery_width - density_width - 10.0
            filter_gap = 6.0
            filter_width = (filter_area_width - filter_gap * 2.0) / 3.0
            for index, key in enumerate(("all", "recommended", "review")):
                self._filter_buttons[key].setFrame_(
                    NSMakeRect(margin + index * (filter_width + filter_gap), toolbar_y, filter_width, 32.0)
                )
            density_x = margin + gallery_width - density_width
            self._density_smaller.setFrame_(NSMakeRect(density_x, toolbar_y, 36.0, 32.0))
            self._density_label.setFrame_(NSMakeRect(density_x + 40.0, toolbar_y + 7.0, 70.0, 20.0))
            self._density_larger.setFrame_(NSMakeRect(density_x + 114.0, toolbar_y, 36.0, 32.0))

            body_y = 76.0
            body_top = toolbar_y - 14.0
            body_height = max(240.0, body_top - body_y)
            self._scroll_view.setFrame_(NSMakeRect(margin, body_y, gallery_width, body_height))
            self._empty_card.setFrame_(NSMakeRect(margin, body_y, gallery_width, body_height))
            self._empty_title.setFrame_(NSMakeRect(20.0, body_height / 2.0 + 6.0, gallery_width - 40.0, 24.0))
            self._empty_detail.setFrame_(NSMakeRect(20.0, body_height / 2.0 - 26.0, gallery_width - 40.0, 22.0))

            inspector_x = margin + gallery_width + gap
            self._inspector_card.setFrame_(NSMakeRect(inspector_x, body_y, inspector_width, height - body_y - 32.0))
            self._layout_inspector(inspector_width, height - body_y - 32.0)

            footer_y = 24.0
            self._finder_button.setFrame_(NSMakeRect(margin, footer_y, 150.0, 34.0))
            self._selection_label.setFrame_(NSMakeRect(margin + 166.0, footer_y + 7.0, 100.0, 20.0))
            self._select_all_button.setFrame_(NSMakeRect(margin + 270.0, footer_y, 86.0, 34.0))
            self._clear_all_button.setFrame_(NSMakeRect(margin + 362.0, footer_y, 86.0, 34.0))
            self._json_export_button.setFrame_(NSMakeRect(width - margin - 414.0, footer_y, 96.0, 34.0))
            self._export_button.setFrame_(NSMakeRect(width - margin - 308.0, footer_y, 198.0, 34.0))
            self._close_button.setFrame_(NSMakeRect(width - margin - 100.0, footer_y, 100.0, 34.0))
            self._update_collection_layout(gallery_width)
            if anchor_index is not None and self._visible_items:
                self._collection_view.layoutSubtreeIfNeeded()
                safe_index = min(max(0, anchor_index), len(self._visible_items) - 1)
                path = NSIndexPath.indexPathForItem_inSection_(safe_index, 0)
                self._collection_view.scrollToItemsAtIndexPaths_scrollPosition_(
                    NSSet.setWithObject_(path), NSCollectionViewScrollPositionTop
                )
        finally:
            self._is_laying_out = False

    @objc.python_method
    def _layout_inspector(self, width: float, height: float) -> None:
        preview_height = max(180.0, height * 0.50)
        preview_y = height - preview_height - 16.0
        self._inspector_image.setFrame_(NSMakeRect(16.0, preview_y, width - 32.0, preview_height))
        self._inspector_open.setFrame_(NSMakeRect(16.0, preview_y, width - 32.0, preview_height))
        self._inspector_title.setFrame_(NSMakeRect(20.0, preview_y - 36.0, width - 40.0, 22.0))
        self._inspector_scene.setFrame_(NSMakeRect(20.0, preview_y - 112.0, width - 40.0, 66.0))
        self._inspector_metrics.setFrame_(NSMakeRect(20.0, preview_y - 154.0, width - 40.0, 36.0))
        self._inspector_event.setFrame_(NSMakeRect(20.0, max(20.0, preview_y - 184.0), width - 40.0, 20.0))

    @objc.python_method
    def _update_collection_layout(self, gallery_width: float) -> None:
        spacing = 12.0
        preferred_width = _DENSITY_WIDTHS[self._density_index]
        columns = int((gallery_width + spacing) // (preferred_width + spacing))
        self._computed_columns = max(3, min(6, columns))
        card_width = (gallery_width - spacing * (self._computed_columns - 1)) / self._computed_columns
        card_height = max(220.0, card_width * 0.70 + 92.0)
        self._flow_layout.setItemSize_(NSMakeSize(card_width, card_height))
        self._flow_layout.invalidateLayout()
        self._density_label.setStringValue_(f"자동 {self._computed_columns}열")
        self._density_smaller.setEnabled_(self._density_index > 0)
        self._density_larger.setEnabled_(self._density_index + 1 < len(_DENSITY_WIDTHS))

    @objc.python_method
    def _reload_results(self, *, scroll_to_top: bool) -> None:
        self._visible_items = self._filtered_items()
        counts = self._category_counts()
        has_items = bool(self._items)
        self._title_label.setStringValue_("사진 분석 완료" if has_items else "표시할 사진 결과가 없습니다")
        self._subtitle_label.setStringValue_(
            f"사진 {len(self._items)}장을 분석했습니다 · 결과는 읽기 전용입니다."
            if has_items
            else "선택한 작업에 저장된 분석 결과가 없습니다."
        )
        self._summary_labels["recommended"].setStringValue_(f"추천  {counts['recommended']}")
        self._summary_labels["review"].setStringValue_(f"검토 필요  {counts['review']}")
        labels = {
            "all": f"전체 {len(self._items)}",
            "recommended": f"추천 {counts['recommended']}",
            "review": f"검토 필요 {counts['review']}",
        }
        for key, button in self._filter_buttons.items():
            button.setTitle_(labels[key])
            button.setState_(1 if self._filter == key else 0)
        self._scroll_view.setHidden_(not self._visible_items)
        self._empty_card.setHidden_(bool(self._visible_items))
        self._collection_view.reloadData()
        self._refresh_selection_controls()
        self._sync_selection()
        self._update_inspector()
        self._layout_view()
        if scroll_to_top and self._visible_items:
            path = NSIndexPath.indexPathForItem_inSection_(0, 0)
            self._collection_view.scrollToItemsAtIndexPaths_scrollPosition_(
                NSSet.setWithObject_(path), NSCollectionViewScrollPositionTop
            )

    @objc.python_method
    def _sync_selection(self) -> None:
        index = next(
            (
                index
                for index, item in enumerate(self._visible_items)
                if str(item.get("photo_id") or "") == self._selected_photo_id
            ),
            None,
        )
        if index is None:
            self._collection_view.setSelectionIndexPaths_(NSSet.set())
            return
        path = NSIndexPath.indexPathForItem_inSection_(index, 0)
        self._collection_view.setSelectionIndexPaths_(NSSet.setWithObject_(path))

    @objc.python_method
    def _update_inspector(self) -> None:
        item = self._selected_item()
        if item is None:
            self._inspector_image.setImage_(None)
            self._inspector_title.setStringValue_("사진을 선택하세요")
            self._inspector_scene.setStringValue_("필터에 표시할 사진이 없습니다.")
            self._inspector_metrics.setStringValue_("")
            self._inspector_event.setStringValue_("")
            self._inspector_open.setEnabled_(False)
            return
        failure = result_item_failure(item)
        self._inspector_image.setImage_(_cached_image(str(item.get("preview_path") or "")))
        cluster_size = max(1, int(item.get("scene_cluster_size") or 1))
        cluster_rank = max(1, int(item.get("cluster_rank") or 1))
        inspector_title = "분석 실패" if failure else "분석 요약"
        if not failure and cluster_size > 1:
            inspector_title = f"분석 요약 · 같은 장면 {cluster_size}장 중 {cluster_rank}위"
        self._inspector_title.setStringValue_(inspector_title)
        scene = failure or str(item.get("scene_description") or "분석 설명이 없습니다.")
        self._inspector_scene.setStringValue_(scene)
        self._inspector_scene.setToolTip_(scene)
        score = float(item.get("total_score") or 0.0)
        quality = float(item.get("quality_score") or 0.0)
        meaningful = float(item.get("meaningful_score") or 0.0) * 10.0
        technical = float(item.get("technical_score") or 0.0)
        self._inspector_metrics.setStringValue_(
            f"다시 분석 {'가능' if item.get('can_retry') else '불가'}"
            if failure
            else f"종합 {score:.0f} · 품질 {quality:.0f} · 기술 {technical:.0f}\n의미 {meaningful:.0f}"
        )
        self._inspector_event.setStringValue_(f"분류 · {str(item.get('event_type') or '기타')}")
        self._inspector_open.setEnabled_(True)

    @objc.python_method
    def _top_visible_index(self) -> int | None:
        paths = list(self._collection_view.indexPathsForVisibleItems() or [])
        return min((int(path.item()) for path in paths), default=None)

    @objc.python_method
    def _selected_item(self) -> dict[str, Any] | None:
        return next(
            (
                item
                for item in self._visible_items
                if str(item.get("photo_id") or "") == self._selected_photo_id
            ),
            None,
        )

    @objc.python_method
    def _filtered_items(self) -> list[dict[str, Any]]:
        if self._filter == "all":
            return list(self._items)
        return [item for item in self._items if result_category(item) == self._filter]

    @objc.python_method
    def _category_counts(self) -> dict[str, int]:
        counts = {"recommended": 0, "review": 0}
        for item in self._items:
            counts[result_category(item)] += 1
        return counts

    @objc.python_method
    def _selected_export_count(self) -> int:
        return sum(1 for item in self._items if bool(item.get("selected")))

    @objc.python_method
    def _refresh_selection_controls(self) -> None:
        count = self._selected_export_count()
        if hasattr(self, "_selection_label"):
            self._selection_label.setStringValue_(f"선택한 {count}장")
            self._export_button.setTitle_(f"선택한 {count}장 내보내기")
            self._export_button.setEnabled_(count > 0 and not self._export_in_progress)
            self._select_all_button.setEnabled_(not self._export_in_progress)
            self._clear_all_button.setEnabled_(not self._export_in_progress)
        if hasattr(self, "_collection_view"):
            self._collection_view.reloadData()

    @objc.python_method
    def _set_all_selection(self, selected: bool) -> None:
        if self._export_in_progress:
            return
        for item in self._items:
            item["selected"] = selected
        self._refresh_selection_controls()
        run_id = str(self._payload.get("job_id") or self._payload.get("run_id") or "")
        generation = self._result_generation

        def worker() -> None:
            try:
                asyncio.run(call_vendor("photo-ranker", "set_all_photo_reviews", run_id, selected))
            except Exception as exc:
                self._selection_persist_error = str(exc)
                self.performSelectorOnMainThread_withObject_waitUntilDone_(
                    "selectionPersistFailed:", {"message": str(exc), "generation": generation}, False
                )

        self._selection_executor.submit(worker)

    @objc.python_method
    def _persist_single_selection(self, photo_id: str, selected: bool) -> None:
        run_id = str(self._payload.get("job_id") or self._payload.get("run_id") or "")
        generation = self._result_generation

        def worker() -> None:
            try:
                asyncio.run(
                    call_vendor(
                        "photo-ranker",
                        "set_photo_review",
                        run_id,
                        photo_id,
                        selected=selected,
                    )
                )
            except Exception as exc:
                self._selection_persist_error = str(exc)
                self.performSelectorOnMainThread_withObject_waitUntilDone_(
                    "selectionPersistFailed:", {"message": str(exc), "generation": generation}, False
                )

        self._selection_executor.submit(worker)

    def selectionPersistFailed_(self, message) -> None:
        payload = dict(message or {}) if isinstance(message, dict) else {"message": str(message)}
        if int(payload.get("generation") or -1) != self._result_generation:
            return
        self._show_alert(
            "선택 상태를 저장하지 못했습니다",
            f"결과를 다시 열면 이전 선택 상태로 돌아갈 수 있습니다.\n\n{payload.get('message', '')}",
        )

    @objc.python_method
    def _begin_export_flow(self) -> None:
        self._export_generation += 1
        self._export_in_progress = True
        self._refresh_selection_controls()

    @objc.python_method
    def _finish_export_flow(self) -> None:
        self._export_in_progress = False
        self._refresh_selection_controls()

    @objc.python_method
    def _start_album_target_choice(self, options: dict[str, Any]) -> None:
        self._pending_export_options = dict(options)
        self._export_button.setEnabled_(False)
        self._export_button.setTitle_("앨범 목록 불러오는 중…")
        generation = self._result_generation
        export_generation = self._export_generation

        def worker() -> None:
            try:
                albums = asyncio.run(call_vendor("photo-ranker", "list_photo_albums"))
                if isinstance(albums, dict) and (albums.get("error") or albums.get("error_code")):
                    payload = {"albums": [], "error": str(albums.get("error") or albums.get("error_code"))}
                else:
                    payload = {"albums": albums if isinstance(albums, list) else []}
            except Exception as exc:
                payload = {"albums": [], "error": str(exc)}
            payload["generation"] = generation
            payload["export_generation"] = export_generation
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "albumsLoadedForExport:", payload, False
            )

        self._export_worker = Thread(target=worker, name="photos-mcp-export-albums", daemon=True)
        self._export_worker.start()

    def albumsLoadedForExport_(self, payload) -> None:
        result = dict(payload or {})
        if int(result.get("generation") or -1) != self._result_generation:
            return
        if int(result.get("export_generation") or -1) != self._export_generation:
            return
        if result.get("error"):
            self._finish_export_flow()
            self._show_alert("Apple 사진 앨범을 불러오지 못했습니다", str(result["error"]))
            return
        albums = [dict(item) for item in result.get("albums") or [] if isinstance(item, dict)]
        options = dict(self._pending_export_options)
        accessory = NSView.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, 420.0, 72.0))
        popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSMakeRect(0.0, 40.0, 420.0, 28.0), False
        )
        popup.setAccessibilityLabel_("기존 Apple 사진 앨범")
        popup.addItemWithTitle_("새 앨범 만들기…")
        for album in albums:
            folder = str(album.get("folder") or "")
            name = str(album.get("name") or "이름 없는 앨범")
            count = int(album.get("count") or 0)
            prefix = f"{folder} / " if folder else ""
            popup.addItemWithTitle_(f"{prefix}{name} ({count}장)")
        if albums:
            popup.selectItemAtIndex_(1)
        accessory.addSubview_(popup)
        new_name = NSTextField.alloc().initWithFrame_(NSMakeRect(0.0, 4.0, 420.0, 28.0))
        new_name.setPlaceholderString_("새 앨범을 선택한 경우 앨범명 입력")
        new_name.setAccessibilityLabel_("새 Apple 사진 앨범명")
        accessory.addSubview_(new_name)

        alert = NSAlert.alloc().init()
        alert.setMessageText_("Apple 사진 앨범 선택")
        alert.setInformativeText_("기존 앨범은 UUID로 정확히 지정됩니다.")
        alert.setAccessoryView_(accessory)
        alert.addButtonWithTitle_("계속")
        alert.addButtonWithTitle_("취소")
        response = alert.runModal()
        if (
            int(result.get("generation") or -1) != self._result_generation
            or int(result.get("export_generation") or -1) != self._export_generation
        ):
            return
        if response != NSAlertFirstButtonReturn:
            self._finish_export_flow()
            return

        selected_index = int(popup.indexOfSelectedItem())
        if selected_index > 0 and selected_index - 1 < len(albums):
            album = albums[selected_index - 1]
            options["target_album_id"] = str(album.get("uuid") or album.get("album_id") or "")
            options["target_album_name"] = str(album.get("name") or "")
        else:
            album_name = str(new_name.stringValue() or "").strip()
            if not album_name:
                self._show_alert("앨범명이 필요합니다", "새로 만들 Apple 사진 앨범명을 입력하세요.")
                self._finish_export_flow()
                return
            options["target_album_name"] = album_name
        self._start_export_plan(options)

    @objc.python_method
    def _choose_export_directory(self) -> str:
        panel = NSOpenPanel.openPanel()
        panel.setCanChooseDirectories_(True)
        panel.setCanChooseFiles_(False)
        panel.setCanCreateDirectories_(True)
        panel.setAllowsMultipleSelection_(False)
        panel.setPrompt_("선택")
        if panel.runModal() != NSModalResponseOK or panel.URL() is None:
            return ""
        return str(panel.URL().path() or "")

    @objc.python_method
    def _start_export_plan(self, options: dict[str, Any]) -> None:
        self._pending_export_options = dict(options)
        self._export_button.setEnabled_(False)
        self._export_button.setTitle_("내보내기 계획 확인 중…")
        state_store = self._menu_controller._state_store
        generation = self._result_generation
        export_generation = self._export_generation
        selection_barrier = self._selection_executor.submit(
            lambda: self._selection_persist_error
        )

        def worker() -> None:
            try:
                persist_error = str(selection_barrier.result() or "")
                if persist_error:
                    payload = {
                        "status": "failed",
                        "error_code": "selection_persistence_failed",
                        "error": persist_error,
                    }
                else:
                    payload = asyncio.run(prepare_selected_export(state_store, options))
            except Exception as exc:
                payload = {"status": "failed", "error": str(exc)}
            payload["generation"] = generation
            payload["export_generation"] = export_generation
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "exportPlanReady:", payload, False
            )

        self._export_worker = Thread(target=worker, name="photos-mcp-export-plan", daemon=True)
        self._export_worker.start()

    def exportPlanReady_(self, payload) -> None:
        result = dict(payload or {})
        if int(result.get("generation") or -1) != self._result_generation:
            return
        if int(result.get("export_generation") or -1) != self._export_generation:
            return
        if result.get("status") != "awaiting_approval":
            self._finish_export_flow()
            self._show_alert(
                "내보내기 계획을 만들지 못했습니다",
                str(result.get("error") or result.get("error_code") or "알 수 없는 오류"),
            )
            return
        plan = dict(result.get("mutation_plan") or {})
        destinations = dict(plan.get("destinations") or {})
        destination_text = []
        if destinations.get("local_directory"):
            destination_text.append("로컬 분류 폴더")
        if destinations.get("apple_album"):
            destination_text.append("Apple 사진 앨범")
        destination_details = []
        if plan.get("target_album_name"):
            folder_prefix = f"{plan.get('folder')} / " if plan.get("folder") else ""
            destination_details.append(f"앨범: {folder_prefix}{plan.get('target_album_name')}")
        if plan.get("output_dir"):
            destination_details.append(f"로컬 폴더: {plan.get('output_dir')}")
        if plan.get("metadata_mode"):
            destination_details.append(f"메타데이터: {plan.get('metadata_mode')}")
        alert = NSAlert.alloc().init()
        alert.setAlertStyle_(NSAlertStyleWarning)
        alert.setMessageText_(f"선택한 {int(plan.get('photo_count') or 0)}장을 내보낼까요?")
        approval_detail = (
            f"대상: {', '.join(destination_text)}\n"
            f"원본 준비 완료 {int(plan.get('originals_ready_count') or 0)}장 · "
            f"준비 필요 {int(plan.get('originals_pending_count') or 0)}장\n\n"
        )
        if destination_details:
            approval_detail += "\n".join(destination_details) + "\n\n"
        approval_detail += "승인 후에만 사진 앨범과 파일 시스템이 변경됩니다."
        alert.setInformativeText_(approval_detail)
        alert.addButtonWithTitle_("승인하고 내보내기")
        alert.addButtonWithTitle_("취소")
        response = alert.runModal()
        if (
            int(result.get("generation") or -1) != self._result_generation
            or int(result.get("export_generation") or -1) != self._export_generation
        ):
            self._menu_controller._state_store.decide_mutation_plan(
                result["approval_token"], "rejected"
            )
            return
        if response != NSAlertFirstButtonReturn:
            self._menu_controller._state_store.decide_mutation_plan(result["approval_token"], "rejected")
            self._finish_export_flow()
            return
        self._menu_controller._state_store.decide_mutation_plan(result["approval_token"], "approved")
        self._start_export_execution(str(result["approval_token"]))

    @objc.python_method
    def _start_export_execution(self, approval_token: str) -> None:
        options = dict(self._pending_export_options)
        state_store = self._menu_controller._state_store
        self._export_button.setEnabled_(False)
        self._export_button.setTitle_("내보내는 중…")
        generation = self._result_generation
        export_generation = self._export_generation

        def worker() -> None:
            try:
                payload = asyncio.run(
                    execute_selected_export(state_store, options, approval_token)
                )
            except Exception as exc:
                payload = {"status": "failed", "error": str(exc)}
            payload["generation"] = generation
            payload["export_generation"] = export_generation
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "exportExecutionFinished:", payload, False
            )

        self._export_worker = Thread(target=worker, name="photos-mcp-export-write", daemon=True)
        self._export_worker.start()

    def exportExecutionFinished_(self, payload) -> None:
        result = dict(payload or {})
        if int(result.get("generation") or -1) != self._result_generation:
            return
        if int(result.get("export_generation") or -1) != self._export_generation:
            return
        destinations = dict(result.get("destinations") or {})
        local = dict(destinations.get("local_directory") or {})
        album = dict(destinations.get("apple_album") or {})
        detail = (
            f"로컬 폴더: {local.get('status', '선택 안 함')} · {int(local.get('exported') or 0)}장\n"
            f"Apple 앨범: {album.get('status', '선택 안 함')} · {int(album.get('added') or 0)}장"
        )
        if result.get("status") == "completed":
            self._finish_export_flow()
            self._show_alert("내보내기가 완료되었습니다", detail)
            return

        alert = NSAlert.alloc().init()
        alert.setAlertStyle_(NSAlertStyleWarning)
        alert.setMessageText_("일부 내보내기를 확인해야 합니다")
        alert.setInformativeText_(detail)
        receipt = dict(result.get("mutation_receipt") or {})
        receipt_id = str(receipt.get("receipt_id") or "")
        if result.get("retry_available") and receipt_id:
            needs_original_preparation = bool(
                self._pending_export_options.get("output_dir")
                and str(local.get("status") or "") in {"failed", "partial", "pending"}
            )
            alert.addButtonWithTitle_(
                "원본 준비 후 다시 실행"
                if needs_original_preparation
                else "미완료 대상 다시 실행"
            )
            alert.addButtonWithTitle_("닫기")
            response = alert.runModal()
            if (
                int(result.get("generation") or -1) != self._result_generation
                or int(result.get("export_generation") or -1) != self._export_generation
            ):
                return
            if response == NSAlertFirstButtonReturn:
                retry_options = dict(self._pending_export_options)
                retry_options["resume_from_receipt_id"] = receipt_id
                if needs_original_preparation:
                    self._start_original_preparation(retry_options, receipt_id)
                else:
                    self._start_export_plan(retry_options)
            else:
                self._finish_export_flow()
            return
        alert.addButtonWithTitle_("확인")
        alert.runModal()
        self._finish_export_flow()

    @objc.python_method
    def _start_original_preparation(
        self,
        options: dict[str, Any],
        receipt_id: str,
    ) -> None:
        self._pending_export_options = dict(options)
        self._export_button.setEnabled_(False)
        self._export_button.setTitle_("iCloud 원본 준비 중…")
        state_store = self._menu_controller._state_store
        generation = self._result_generation
        export_generation = self._export_generation
        run_id = str(options.get("run_id") or "")

        def worker() -> None:
            try:
                payload = asyncio.run(
                    prepare_retry_originals(state_store, run_id, receipt_id)
                )
            except Exception as exc:
                payload = {"status": "failed", "error": str(exc)}
            payload["generation"] = generation
            payload["export_generation"] = export_generation
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "originalPreparationFinished:", payload, False
            )

        self._export_worker = Thread(
            target=worker,
            name="photos-mcp-original-preparation",
            daemon=True,
        )
        self._export_worker.start()

    def originalPreparationFinished_(self, payload) -> None:
        result = dict(payload or {})
        if int(result.get("generation") or -1) != self._result_generation:
            return
        if int(result.get("export_generation") or -1) != self._export_generation:
            return

        ready = int(result.get("ready") or 0)
        downloaded = int(result.get("downloaded") or 0)
        pending = int(result.get("pending") or 0)
        if result.get("status") in {"completed", "partial"} and ready > 0:
            if pending > 0:
                self._show_alert(
                    "준비된 원본부터 다시 실행합니다",
                    f"새로 준비 {downloaded}장 · 준비 완료 {ready}장 · 아직 대기 {pending}장",
                )
            self._start_export_plan(dict(self._pending_export_options))
            return

        self._finish_export_flow()
        if pending > 0:
            self._show_alert(
                "iCloud 원본이 아직 준비되지 않았습니다",
                f"준비 완료 {ready}장 · 아직 대기 {pending}장\n\n"
                "네트워크와 Apple 사진의 다운로드 상태를 확인한 뒤 다시 시도하세요.",
            )
            return
        self._show_alert(
            "원본 준비를 시작하지 못했습니다",
            str(result.get("error") or result.get("error_code") or "알 수 없는 오류"),
        )

    @objc.python_method
    def _show_alert(self, title: str, detail: str) -> None:
        alert = NSAlert.alloc().init()
        alert.setMessageText_(title)
        alert.setInformativeText_(detail)
        alert.addButtonWithTitle_("확인")
        alert.runModal()

    @objc.python_method
    def _sender_identifier(self, sender) -> str:
        if sender is None or not hasattr(sender, "identifier"):
            return ""
        return str(sender.identifier() or "")

    @objc.python_method
    def _card(self, parent: Any, *, tone: str) -> Any:
        card = NSView.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, 1.0, 1.0))
        card.setWantsLayer_(True)
        card.layer().setCornerRadius_(13.0)
        card.layer().setBorderWidth_(1.0)
        color = _status_color(tone) if tone != "neutral" else NSColor.separatorColor()
        card.layer().setBorderColor_(color.colorWithAlphaComponent_(0.42).CGColor())
        card.layer().setBackgroundColor_(
            NSColor.controlBackgroundColor().colorWithAlphaComponent_(0.48).CGColor()
        )
        parent.addSubview_(card)
        return card

    @objc.python_method
    def _label(
        self,
        parent: Any,
        text: str,
        size: float,
        *,
        bold: bool = False,
        secondary: bool = False,
    ) -> Any:
        label = NSTextField.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, 1.0, 1.0))
        label.setBezeled_(False)
        label.setDrawsBackground_(False)
        label.setEditable_(False)
        label.setSelectable_(False)
        label.setStringValue_(text)
        label.setFont_(app_font(size, "semibold" if bold else "regular"))
        if secondary:
            label.setTextColor_(NSColor.secondaryLabelColor())
        parent.addSubview_(label)
        return label

    @objc.python_method
    def _button(
        self,
        parent: Any,
        title: str,
        action: str,
        *,
        identifier: str = "",
        primary: bool = False,
        accessibility_label: str = "",
    ) -> Any:
        button = NSButton.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, 1.0, 1.0))
        button.setTitle_(title)
        button.setTarget_(self)
        button.setAction_(action)
        button.setFont_(app_font(10.0, "medium"))
        button.setBezelStyle_(1 if primary else 0)
        if primary and hasattr(button, "setBezelColor_"):
            button.setBezelColor_(accent_color())
        if identifier:
            button.setIdentifier_(identifier)
        button.setAccessibilityLabel_(accessibility_label or title or "사진 결과 열기")
        self._focusable.append(button)
        parent.addSubview_(button)
        return button
