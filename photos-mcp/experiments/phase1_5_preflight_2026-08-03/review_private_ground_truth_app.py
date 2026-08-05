#!/usr/bin/env python3
"""Review private scene candidates locally with a keyboard-first AppKit window.

The queue, previews, selected photo IDs, and optional notes stay below the
private validation directory. This tool never sends an image or review answer
to the MCP server, an LLM, or the Git working tree.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import objc
from AppKit import (
    NSAlert,
    NSApp,
    NSApplication,
    NSApplicationActivationPolicyRegular,
    NSBackingStoreBuffered,
    NSButton,
    NSButtonTypeSwitch,
    NSColor,
    NSEventModifierFlagShift,
    NSFont,
    NSImage,
    NSImageScaleProportionallyUpOrDown,
    NSImageView,
    NSMakeRect,
    NSScrollView,
    NSTextField,
    NSView,
    NSWindow,
    NSWindowController,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskMiniaturizable,
    NSWindowStyleMaskResizable,
    NSWindowStyleMaskTitled,
)
from Foundation import NSMakeSize

from photos_mcp.photo_viewer_appkit import PhotosMcpPhotoViewerController


DEFAULT_QUEUE = (
    Path.home()
    / ".photos-mcp"
    / "validation"
    / "phase1_5_revalidation_2026-08-03-1000"
    / "review-ground-truth-private-100.json"
)
_WINDOW_SIZE = (1260.0, 820.0)
_SIDEBAR_WIDTH = 272.0
_MARGIN = 22.0
_BACKGROUND = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.12, 0.12, 0.12, 1.0)
_PANEL = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.16, 0.16, 0.16, 1.0)
_BORDER = NSColor.colorWithCalibratedWhite_alpha_(0.65, 0.72)
_ACCENT = NSColor.systemGreenColor()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", type=Path, default=DEFAULT_QUEUE)
    parser.add_argument(
        "--start-index",
        type=int,
        default=None,
        help="One-based scene index. Defaults to the first unreviewed scene.",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Check private queue shape without opening a macOS window.",
    )
    return parser.parse_args()


def load_queue(path: Path) -> dict[str, Any]:
    queue_path = path.expanduser().resolve()
    payload = json.loads(queue_path.read_text(encoding="utf-8"))
    if not payload.get("private"):
        raise ValueError("Refusing a queue that is not marked private.")
    if not isinstance(payload.get("items"), list) or not payload["items"]:
        raise ValueError("The private review queue has no scene items.")
    for item in payload["items"]:
        if not isinstance(item.get("photos"), list) or not item["photos"]:
            raise ValueError("Every review scene needs at least one photo.")
        item.setdefault("labels", {})
    return payload


def write_queue(path: Path, payload: dict[str, Any]) -> None:
    """Atomically persist local choices so a quit never loses prior reviews."""
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


class ReviewContentView(NSView):
    def acceptsFirstResponder(self) -> bool:
        return True

    def keyDown_(self, event) -> None:
        controller = getattr(self, "_review_controller", None)
        if controller is None:
            objc.super(ReviewContentView, self).keyDown_(event)
            return
        characters = str(event.charactersIgnoringModifiers() or "")
        if characters in {"\r", "\n"}:
            controller.saveAndNext_(None)
            return
        if characters.isdigit() and characters != "0":
            controller.selectCandidateAtIndex_(
                int(characters) - 1,
                bool(int(event.modifierFlags()) & NSEventModifierFlagShift),
            )
            return
        if characters == "0":
            controller.clearPrimary_(None)
            return
        if int(event.keyCode()) == 123:
            controller.previousScene_(None)
            return
        if int(event.keyCode()) == 124:
            controller.skipScene_(None)
            return
        objc.super(ReviewContentView, self).keyDown_(event)


class PrivateGroundTruthReviewController(NSWindowController):
    """A small native reviewer for one scene at a time."""

    def initWithQueuePath_payload_startIndex_(
        self,
        queue_path: Path,
        payload: dict[str, Any],
        start_index: int,
    ):
        style = (
            NSWindowStyleMaskTitled
            | NSWindowStyleMaskClosable
            | NSWindowStyleMaskMiniaturizable
            | NSWindowStyleMaskResizable
        )
        window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0.0, 0.0, *_WINDOW_SIZE),
            style,
            NSBackingStoreBuffered,
            False,
        )
        self = objc.super(PrivateGroundTruthReviewController, self).initWithWindow_(window)
        if self is None:
            return None
        self._queue_path = queue_path
        self._queue_root = queue_path.parent
        self._payload = payload
        self._index = max(0, min(start_index, len(payload["items"]) - 1))
        self._primary_id = ""
        self._secondary_id = ""
        self._boundary = "correct"
        self._failure_codes: set[str] = set()
        self._photo_viewer = None
        self._is_rendering = False
        root = ReviewContentView.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, *_WINDOW_SIZE))
        root._review_controller = self
        window.setContentView_(root)
        window.setTitle_("사진 장면 검토")
        window.setMinSize_(NSMakeSize(960.0, 640.0))
        window.setFrameAutosaveName_("PhotosMcpPrivateGroundTruthReview")
        window.setDelegate_(self)
        self._load_current_draft()
        self.render()
        return self

    def windowDidResize_(self, _notification) -> None:
        if not self._is_rendering:
            self.render()

    def selectPrimary_(self, sender) -> None:
        self.selectCandidateAtIndex_(int(sender.tag()), False)

    def toggleSecondary_(self, sender) -> None:
        self.selectCandidateAtIndex_(int(sender.tag()), True)

    def openPhoto_(self, sender) -> None:
        self.openPhotoAtIndex_(int(sender.tag()))

    @objc.python_method
    def selectCandidateAtIndex_(self, index: int, as_secondary: bool) -> None:
        photos = self._current_item()["photos"]
        if index < 0 or index >= len(photos):
            return
        photo_id = str(photos[index].get("photo_id") or "")
        if not photo_id:
            return
        if as_secondary:
            if photo_id == self._primary_id:
                return
            self._secondary_id = "" if photo_id == self._secondary_id else photo_id
        else:
            self._primary_id = photo_id
            if self._secondary_id == photo_id:
                self._secondary_id = ""
        self.render()

    @objc.python_method
    def openPhotoAtIndex_(self, index: int) -> None:
        photos = self._current_item()["photos"]
        if index < 0 or index >= len(photos):
            return
        viewer_items = [
            {
                "photo_id": str(photo.get("photo_id") or ""),
                "preview_path": str(self._queue_root / str(photo.get("preview_file") or "")),
                "scene_description": "장면 검토용 로컬 미리보기",
                "event_type": str(photo.get("event_type") or "other"),
            }
            for photo in photos
        ]
        selected_photo_id = str(photos[index].get("photo_id") or "")
        if self._photo_viewer is None:
            self._photo_viewer = PhotosMcpPhotoViewerController.alloc().init()
        self._photo_viewer.show_items(viewer_items, selected_photo_id)

    def setBoundary_(self, sender) -> None:
        self._boundary = str(sender.identifier() or "correct")
        self.render()

    def toggleFailure_(self, sender) -> None:
        code = str(sender.identifier() or "")
        if not code:
            return
        if code in self._failure_codes:
            self._failure_codes.remove(code)
        else:
            self._failure_codes.add(code)

    def clearPrimary_(self, _sender) -> None:
        self._primary_id = ""
        self.render()

    def previousScene_(self, _sender) -> None:
        if self._index > 0:
            self._index -= 1
            self._load_current_draft()
            self.render()

    def skipScene_(self, _sender) -> None:
        item = self._current_item()
        item["labels"].update(
            {
                "review_status": "skipped",
                "best_photo_ids": [],
                "second_recommendation": "unreviewed",
                "failure_codes": [],
            }
        )
        write_queue(self._queue_path, self._payload)
        self._advance()

    def saveAndNext_(self, _sender) -> None:
        if not self._primary_id:
            alert = NSAlert.alloc().init()
            alert.setMessageText_("가장 좋은 사진을 한 장 선택하세요.")
            alert.setInformativeText_("판단하기 어려운 장면은 ‘건너뛰기’를 사용하면 됩니다.")
            alert.addButtonWithTitle_("확인")
            alert.runModal()
            return
        item = self._current_item()
        best_ids = [self._primary_id]
        if self._secondary_id and self._secondary_id != self._primary_id:
            best_ids.append(self._secondary_id)
        item["labels"].update(
            {
                "review_status": "completed",
                "scene_boundary": self._boundary,
                "best_photo_ids": best_ids,
                "second_recommendation": "needed" if len(best_ids) == 2 else "not_needed",
                "failure_codes": sorted(self._failure_codes),
            }
        )
        write_queue(self._queue_path, self._payload)
        self._advance()

    @objc.python_method
    def _advance(self) -> None:
        if self._index + 1 < len(self._payload["items"]):
            self._index += 1
            self._load_current_draft()
            self.render()
            return
        alert = NSAlert.alloc().init()
        alert.setMessageText_("모든 장면을 검토했습니다.")
        alert.setInformativeText_("선택 결과는 개인 검증 큐에 저장되었습니다.")
        alert.addButtonWithTitle_("확인")
        alert.runModal()

    @objc.python_method
    def _current_item(self) -> dict[str, Any]:
        return self._payload["items"][self._index]

    @objc.python_method
    def _load_current_draft(self) -> None:
        labels = self._current_item().get("labels") or {}
        selected = [str(value) for value in labels.get("best_photo_ids") or []]
        self._primary_id = selected[0] if selected else ""
        self._secondary_id = selected[1] if len(selected) > 1 else ""
        self._boundary = str(labels.get("scene_boundary") or "correct")
        if self._boundary == "unreviewed":
            self._boundary = "correct"
        self._failure_codes = set(str(value) for value in labels.get("failure_codes") or [])

    @objc.python_method
    def _progress(self) -> tuple[int, int, int]:
        items = self._payload["items"]
        complete = sum(
            (item.get("labels") or {}).get("review_status") == "completed"
            for item in items
        )
        return complete, len(items), sum(
            (item.get("labels") or {}).get("review_status") == "skipped"
            for item in items
        )

    @objc.python_method
    def render(self) -> None:
        self._is_rendering = True
        try:
            root = self.window().contentView()
            for view in list(root.subviews()):
                view.removeFromSuperview()
            root.setWantsLayer_(True)
            root.layer().setBackgroundColor_(_BACKGROUND.CGColor())
            width = float(root.bounds().size.width)
            height = float(root.bounds().size.height)
            item = self._current_item()
            complete, total, skipped = self._progress()

            self._label(root, _MARGIN, height - 46.0, width - 2 * _MARGIN, 30.0, "장면별 사진 선택", 22.0, True)
            self._label(
                root,
                _MARGIN,
                height - 72.0,
                width - 2 * _MARGIN,
                18.0,
                f"{self._index + 1} / {total} 장면  ·  완료 {complete}  ·  건너뜀 {skipped}",
                11.0,
                False,
                secondary=True,
            )
            self._label(
                root,
                _MARGIN,
                height - 94.0,
                width - _SIDEBAR_WIDTH - 2 * _MARGIN,
                17.0,
                "사진을 클릭하면 1순위로 선택됩니다. ‘크게 보기’에서 확대·전체 화면으로 세부를 확인할 수 있습니다.",
                10.0,
                False,
                secondary=True,
            )

            panel_x = width - _SIDEBAR_WIDTH - _MARGIN
            content_width = panel_x - 2 * _MARGIN
            grid_height = height - 130.0
            scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(_MARGIN, 22.0, content_width, grid_height))
            scroll.setHasVerticalScroller_(True)
            scroll.setAutohidesScrollers_(True)
            scroll.setDrawsBackground_(False)
            scroll.setBorderType_(0)
            grid = self._build_grid(content_width - 14.0, item)
            scroll.setDocumentView_(grid)
            root.addSubview_(scroll)
            self._build_sidebar(root, panel_x, 22.0, _SIDEBAR_WIDTH, grid_height)
            self.window().makeFirstResponder_(root)
        finally:
            self._is_rendering = False

    @objc.python_method
    def _build_grid(self, width: float, item: dict[str, Any]) -> Any:
        photos = item["photos"]
        # Two large columns make expression, focus, and eye state review practical.
        columns = min(2, max(1, int(width // 330.0)))
        gap = 14.0
        tile_width = (width - gap * (columns - 1)) / columns
        image_height = min(360.0, max(220.0, tile_width * 0.72))
        tile_height = image_height + 74.0
        rows = max(1, (len(photos) + columns - 1) // columns)
        grid = NSView.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, width, rows * (tile_height + gap) - gap))
        for index, photo in enumerate(photos):
            column = index % columns
            row = index // columns
            x = column * (tile_width + gap)
            y = grid.bounds().size.height - (row + 1) * tile_height - row * gap
            tile = NSView.alloc().initWithFrame_(NSMakeRect(x, y, tile_width, tile_height))
            tile.setWantsLayer_(True)
            photo_id = str(photo.get("photo_id") or "")
            selected = photo_id in {self._primary_id, self._secondary_id}
            tile.layer().setCornerRadius_(12.0)
            tile.layer().setBackgroundColor_(_PANEL.CGColor())
            tile.layer().setBorderWidth_(2.0 if selected else 1.0)
            tile.layer().setBorderColor_((_ACCENT if selected else _BORDER).CGColor())
            grid.addSubview_(tile)

            image_button = NSButton.alloc().initWithFrame_(NSMakeRect(7.0, 56.0, tile_width - 14.0, image_height - 10.0))
            image_button.setBordered_(False)
            image_button.setTag_(index)
            image_button.setTarget_(self)
            image_button.setAction_("selectPrimary:")
            image_button.setImageScaling_(NSImageScaleProportionallyUpOrDown)
            image_button.setAccessibilityLabel_(f"사진 {index + 1}, 1순위로 선택")
            preview = self._queue_root / str(photo.get("preview_file") or "")
            if preview.is_file():
                image_button.setImage_(NSImage.alloc().initWithContentsOfFile_(str(preview)))
            else:
                image_button.setTitle_("미리보기를 찾을 수 없습니다")
            tile.addSubview_(image_button)

            rank = "1순위" if photo_id == self._primary_id else "2순위" if photo_id == self._secondary_id else f"사진 {index + 1}"
            self._label(tile, 10.0, 28.0, tile_width - 160.0, 20.0, rank, 11.0, True, accent=selected)
            preview_button = self._button(tile, tile_width - 148.0, 20.0, 62.0, 27.0, "크게 보기", "openPhoto:")
            preview_button.setTag_(index)
            second = self._button(tile, tile_width - 78.0, 20.0, 66.0, 27.0, "2순위", "toggleSecondary:")
            second.setTag_(index)
            second.setEnabled_(photo_id != self._primary_id)
        return grid

    @objc.python_method
    def _build_sidebar(self, root: Any, x: float, y: float, width: float, height: float) -> None:
        panel = NSView.alloc().initWithFrame_(NSMakeRect(x, y, width, height))
        panel.setWantsLayer_(True)
        panel.layer().setCornerRadius_(14.0)
        panel.layer().setBackgroundColor_(_PANEL.CGColor())
        panel.layer().setBorderColor_(_BORDER.CGColor())
        panel.layer().setBorderWidth_(1.0)
        root.addSubview_(panel)
        self._label(panel, 18.0, height - 44.0, width - 36.0, 24.0, "검토 선택", 15.0, True)
        primary = "선택 안 됨" if not self._primary_id else "사진 선택됨"
        secondary = "없음" if not self._secondary_id else "사진 선택됨"
        self._label(panel, 18.0, height - 72.0, width - 36.0, 18.0, f"1순위  {primary}", 10.5, False, secondary=True)
        self._label(panel, 18.0, height - 94.0, width - 36.0, 18.0, f"2순위  {secondary}", 10.5, False, secondary=True)
        self._button(panel, 18.0, height - 132.0, width - 36.0, 30.0, "1순위 선택 해제 (0)", "clearPrimary:")

        self._label(panel, 18.0, height - 178.0, width - 36.0, 20.0, "장면 묶음", 12.0, True)
        boundaries = (("correct", "같은 장면"), ("over_merged", "분리 필요"), ("uncertain", "판단 보류"))
        row_y = height - 208.0
        for code, title in boundaries:
            button = self._button(panel, 18.0, row_y, width - 36.0, 27.0, title, "setBoundary:")
            button.setIdentifier_(code)
            if code == self._boundary and hasattr(button, "setContentTintColor_"):
                button.setContentTintColor_(_ACCENT)
            row_y -= 32.0

        self._label(panel, 18.0, row_y - 8.0, width - 36.0, 20.0, "선택 이유 (선택 사항)", 12.0, True)
        row_y -= 38.0
        for code, title in (("eyes_closed", "눈 감김"), ("blur", "흐림"), ("bad_expression", "표정"), ("duplicate", "중복")):
            toggle = NSButton.alloc().initWithFrame_(NSMakeRect(16.0, row_y, width - 32.0, 24.0))
            toggle.setButtonType_(NSButtonTypeSwitch)
            toggle.setTitle_(title)
            toggle.setState_(1 if code in self._failure_codes else 0)
            toggle.setTarget_(self)
            toggle.setAction_("toggleFailure:")
            toggle.setIdentifier_(code)
            toggle.setFont_(NSFont.systemFontOfSize_(11.0))
            panel.addSubview_(toggle)
            row_y -= 28.0

        self._button(panel, 18.0, 68.0, width - 36.0, 34.0, "저장하고 다음으로 (Enter)", "saveAndNext:")
        self._button(panel, 18.0, 30.0, (width - 42.0) / 2.0, 28.0, "이전", "previousScene:")
        self._button(panel, 24.0 + (width - 42.0) / 2.0, 30.0, (width - 42.0) / 2.0, 28.0, "건너뛰기", "skipScene:")

    @objc.python_method
    def _label(
        self,
        parent: Any,
        x: float,
        y: float,
        width: float,
        height: float,
        text: str,
        size: float,
        bold: bool,
        *,
        secondary: bool = False,
        accent: bool = False,
    ) -> Any:
        label = NSTextField.labelWithString_(text)
        label.setFrame_(NSMakeRect(x, y, width, height))
        label.setFont_(NSFont.systemFontOfSize_weight_(size, 0.4 if bold else 0.0))
        label.setTextColor_(_ACCENT if accent else NSColor.secondaryLabelColor() if secondary else NSColor.labelColor())
        label.setLineBreakMode_(4)
        parent.addSubview_(label)
        return label

    @objc.python_method
    def _button(self, parent: Any, x: float, y: float, width: float, height: float, title: str, action: str) -> Any:
        button = NSButton.alloc().initWithFrame_(NSMakeRect(x, y, width, height))
        button.setTitle_(title)
        button.setTarget_(self)
        button.setAction_(action)
        button.setFont_(NSFont.systemFontOfSize_weight_(11.0, 0.4))
        parent.addSubview_(button)
        return button


def first_unreviewed_index(payload: dict[str, Any]) -> int:
    return next(
        (
            index
            for index, item in enumerate(payload["items"])
            if (item.get("labels") or {}).get("review_status") == "unreviewed"
        ),
        0,
    )


def main() -> None:
    args = parse_args()
    queue_path = args.queue.expanduser().resolve()
    payload = load_queue(queue_path)
    if args.validate_only:
        print(json.dumps({"status": "ready", "scene_count": len(payload["items"]), "queue": str(queue_path)}))
        return
    start_index = first_unreviewed_index(payload) if args.start_index is None else args.start_index - 1
    application = NSApplication.sharedApplication()
    application.setActivationPolicy_(NSApplicationActivationPolicyRegular)
    controller = PrivateGroundTruthReviewController.alloc().initWithQueuePath_payload_startIndex_(
        queue_path,
        payload,
        start_index,
    )
    controller.showWindow_(None)
    NSApp.activateIgnoringOtherApps_(True)
    application.run()


if __name__ == "__main__":
    main()
