"""Keyboard-first AppKit review window for explicit face-pair labels."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import objc
from AppKit import (
    NSAlert,
    NSBackingStoreBuffered,
    NSButton,
    NSColor,
    NSImageScaleProportionallyUpOrDown,
    NSImageView,
    NSMakeRect,
    NSTextField,
    NSView,
    NSWindow,
    NSWindowCollectionBehaviorFullScreenPrimary,
    NSWindowController,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskMiniaturizable,
    NSWindowStyleMaskResizable,
    NSWindowStyleMaskTitled,
    NSWindowZoomButton,
)
from Foundation import NSMakeSize

from photos_mcp.application.face_identity_review import (
    first_unreviewed_face_pair_index,
    load_or_create_face_identity_review,
    summarize_face_identity_review,
    validate_face_identity_review_queue,
    write_face_identity_review_queue,
)
from photos_mcp.interfaces.appkit.results.collection_item import cached_image
from photos_mcp.interfaces.appkit.results.photo_viewer import PhotosMcpPhotoViewerController
from photos_mcp.interfaces.appkit.shared.theme import (
    accent_color,
    app_font,
    panel_background_color,
    subtle_border_color,
)


_WINDOW_SIZE = (1120.0, 760.0)
_MIN_SIZE = (900.0, 640.0)
_MARGIN = 28.0
_LABEL_TITLES = {
    "same_person": "같은 사람",
    "different_person": "다른 사람",
    "uncertain": "판단 어려움",
    "invalid_detection": "잘못 검출",
    "unreviewed": "아직 판단하지 않음",
}


class FaceIdentityReviewContentView(NSView):
    def acceptsFirstResponder(self) -> bool:
        return True

    def keyDown_(self, event) -> None:
        controller = getattr(self, "_review_controller", None)
        if controller is None:
            objc.super(FaceIdentityReviewContentView, self).keyDown_(event)
            return
        characters = str(event.charactersIgnoringModifiers() or "")
        labels = {
            "1": "same_person",
            "2": "different_person",
            "3": "uncertain",
            "4": "invalid_detection",
        }
        if characters in labels:
            controller.saveLabel_(labels[characters])
            return
        if int(event.keyCode()) == 123:
            controller.previousPair_(None)
            return
        if int(event.keyCode()) == 124:
            controller.nextPair_(None)
            return
        objc.super(FaceIdentityReviewContentView, self).keyDown_(event)


class PhotosMcpFaceIdentityReviewController(NSWindowController):
    """Show only two detected face crops and persist an exact identity label."""

    def initWithResultPayload_(self, result_payload: dict[str, Any]):
        queue_path, payload = load_or_create_face_identity_review(result_payload)
        return self._initialize_with_queue(queue_path, payload)

    def initWithReviewPayload_path_(self, payload: dict[str, Any], queue_path: str):
        validate_face_identity_review_queue(payload)
        return self._initialize_with_queue(Path(queue_path).expanduser(), payload)

    @objc.python_method
    def _initialize_with_queue(self, queue_path: Path, payload: dict[str, Any]):
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
        self = objc.super(PhotosMcpFaceIdentityReviewController, self).initWithWindow_(window)
        if self is None:
            return None
        self._queue_path, self._payload = queue_path, payload
        self._review_title = str(payload.get("review_title") or "얼굴 동일인 검토")
        self._review_question = str(payload.get("review_question") or "두 얼굴이 같은 사람인가요?")
        self._review_guidance = str(
            payload.get("review_guidance")
            or "얼굴만 비교하세요. 확신하기 어렵거나 얼굴이 아닌 경우 별도 항목을 선택합니다."
        )
        self._index = first_unreviewed_face_pair_index(self._payload)
        self._viewer_controller = PhotosMcpPhotoViewerController.alloc().init()
        self._is_rendering = False
        root = FaceIdentityReviewContentView.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, *_WINDOW_SIZE))
        root._review_controller = self
        window.setContentView_(root)
        window.setTitle_(self._review_title)
        window.setMinSize_(NSMakeSize(*_MIN_SIZE))
        window.setFrameAutosaveName_("PhotosMcpFaceIdentityReview")
        window.setCollectionBehavior_(NSWindowCollectionBehaviorFullScreenPrimary)
        window.setReleasedWhenClosed_(False)
        window.setDelegate_(self)
        zoom_button = window.standardWindowButton_(NSWindowZoomButton)
        if zoom_button is not None:
            zoom_button.setEnabled_(True)
        self.render()
        return self

    def windowDidResize_(self, _notification) -> None:
        if not self._is_rendering:
            self.render()

    def saveLabel_(self, sender) -> None:
        label = str(sender if isinstance(sender, str) else sender.identifier() or "")
        if label not in _LABEL_TITLES or label == "unreviewed":
            return
        self._current_item()["label"] = label
        write_face_identity_review_queue(self._queue_path, self._payload)
        if self._index + 1 < len(self._payload["items"]):
            self._index += 1
            self.render()
            return
        self.render()
        summary = summarize_face_identity_review(self._payload)
        self._alert(
            f"{self._review_title}를 완료했습니다",
            f"완료 {summary['completed_pair_count']} / {summary['pair_count']}쌍",
        )

    def previousPair_(self, _sender) -> None:
        if self._index > 0:
            self._index -= 1
            self.render()

    def nextPair_(self, _sender) -> None:
        if self._index + 1 < len(self._payload["items"]):
            self._index += 1
            self.render()

    def openSourcePhoto_(self, sender) -> None:
        face_index = int(sender.tag())
        faces = list(self._current_item().get("faces") or [])
        if not 0 <= face_index < len(faces):
            return
        face = faces[face_index]
        item = {
            "photo_id": str(face.get("photo_id") or ""),
            "preview_path": str(face.get("preview_path") or ""),
            "source_photo_path": str(face.get("source_photo_path") or ""),
            "scene_description": f"{self._review_title} 원본",
            "event_type": "other",
        }
        self._viewer_controller.show_items([item], str(item["photo_id"]))

    @objc.python_method
    def _current_item(self) -> dict[str, Any]:
        return self._payload["items"][self._index]

    @objc.python_method
    def render(self) -> None:
        self._is_rendering = True
        try:
            root = self.window().contentView()
            for view in list(root.subviews()):
                view.removeFromSuperview()
            root.setWantsLayer_(True)
            root.layer().setBackgroundColor_(NSColor.windowBackgroundColor().CGColor())
            width = float(root.bounds().size.width)
            height = float(root.bounds().size.height)
            item = self._current_item()
            summary = summarize_face_identity_review(self._payload)

            self._label(root, _MARGIN, height - 58.0, width - 2 * _MARGIN, 32.0, self._review_question, 23.0, True)
            self._label(
                root,
                _MARGIN,
                height - 88.0,
                width - 2 * _MARGIN,
                22.0,
                f"얼굴 쌍 {self._index + 1} / {len(self._payload['items'])} · 완료 {summary['completed_pair_count']} · 남음 {summary['remaining_pair_count']}",
                11.0,
                False,
                secondary=True,
            )
            self._label(
                root,
                _MARGIN,
                height - 116.0,
                width - 2 * _MARGIN,
                22.0,
                self._review_guidance,
                10.5,
                False,
                secondary=True,
            )

            card_gap = 24.0
            card_y = 190.0
            card_height = max(310.0, height - card_y - 140.0)
            card_width = (width - 2 * _MARGIN - card_gap) / 2.0
            for face_index, face in enumerate(item.get("faces") or []):
                card_x = _MARGIN + face_index * (card_width + card_gap)
                self._face_card(root, card_x, card_y, card_width, card_height, face, face_index)

            label = str(item.get("label") or "unreviewed")
            self._label(
                root,
                _MARGIN,
                151.0,
                width - 2 * _MARGIN,
                22.0,
                f"현재 판단 · {_LABEL_TITLES.get(label, '아직 판단하지 않음')}",
                10.5,
                True,
                secondary=label == "unreviewed",
                accent=label != "unreviewed",
            )
            button_gap = 10.0
            button_width = (width - 2 * _MARGIN - button_gap * 3.0) / 4.0
            for index, (code, title) in enumerate(
                (
                    ("same_person", "1  같은 사람"),
                    ("different_person", "2  다른 사람"),
                    ("uncertain", "3  판단 어려움"),
                    ("invalid_detection", "4  잘못 검출"),
                )
            ):
                button = self._button(
                    root,
                    _MARGIN + index * (button_width + button_gap),
                    94.0,
                    button_width,
                    42.0,
                    title,
                    "saveLabel:",
                    primary=index < 2,
                )
                button.setIdentifier_(code)

            self._button(root, _MARGIN, 36.0, 110.0, 34.0, "← 이전", "previousPair:")
            self._button(root, _MARGIN + 120.0, 36.0, 110.0, 34.0, "다음 →", "nextPair:")
            self._label(
                root,
                width - _MARGIN - 360.0,
                42.0,
                360.0,
                22.0,
                "숫자 1–4로 선택 · 좌우 화살표로 이동",
                9.5,
                False,
                secondary=True,
            )
        finally:
            self._is_rendering = False

    @objc.python_method
    def _face_card(
        self,
        root: Any,
        x: float,
        y: float,
        width: float,
        height: float,
        face: dict[str, Any],
        face_index: int,
    ) -> None:
        card = NSView.alloc().initWithFrame_(NSMakeRect(x, y, width, height))
        card.setWantsLayer_(True)
        card.layer().setCornerRadius_(16.0)
        card.layer().setBackgroundColor_(panel_background_color().CGColor())
        card.layer().setBorderColor_(subtle_border_color().CGColor())
        card.layer().setBorderWidth_(1.0)
        root.addSubview_(card)

        self._label(card, 18.0, height - 42.0, width - 36.0, 24.0, f"얼굴 {'A' if face_index == 0 else 'B'}", 13.0, True)
        image_view = NSImageView.alloc().initWithFrame_(NSMakeRect(16.0, 60.0, width - 32.0, height - 114.0))
        image_view.setImageScaling_(NSImageScaleProportionallyUpOrDown)
        image = cached_image(str(face.get("crop_path") or ""))
        if image is not None:
            image_view.setImage_(image)
        image_view.setAccessibilityLabel_(f"비교 얼굴 {'A' if face_index == 0 else 'B'}")
        card.addSubview_(image_view)
        source_button = self._button(card, width - 126.0, 16.0, 110.0, 32.0, "원본 사진 보기", "openSourcePhoto:")
        source_button.setTag_(face_index)

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
        label.setFont_(app_font(size, "semibold" if bold else "regular"))
        label.setTextColor_(accent_color() if accent else NSColor.secondaryLabelColor() if secondary else NSColor.labelColor())
        parent.addSubview_(label)
        return label

    @objc.python_method
    def _button(
        self,
        parent: Any,
        x: float,
        y: float,
        width: float,
        height: float,
        title: str,
        action: str,
        *,
        primary: bool = False,
    ) -> Any:
        button = NSButton.alloc().initWithFrame_(NSMakeRect(x, y, width, height))
        button.setTitle_(title)
        button.setTarget_(self)
        button.setAction_(action)
        button.setFont_(app_font(10.0, "semibold"))
        button.setAccessibilityLabel_(title)
        if primary:
            button.setContentTintColor_(accent_color())
        parent.addSubview_(button)
        return button

    @objc.python_method
    def _alert(self, title: str, detail: str) -> None:
        alert = NSAlert.alloc().init()
        alert.setMessageText_(title)
        alert.setInformativeText_(detail)
        alert.addButtonWithTitle_("확인")
        alert.runModal()
