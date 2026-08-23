"""Small native AppKit drag-and-drop views for private face grouping."""

from __future__ import annotations

import json
from typing import Any

import objc
from AppKit import (
    NSButton,
    NSColor,
    NSDraggingItem,
    NSDragOperationMove,
    NSImage,
    NSImageView,
    NSMakeRect,
    NSPasteboardItem,
    NSView,
)
from Foundation import NSPointInRect
FACE_DRAG_TYPE = "com.nanobot.photos-mcp.people.face"
IDENTITY_DRAG_TYPE = "com.nanobot.photos-mcp.people.identity"
DRAG_PAYLOAD_VERSION = 1


class _DragHandle(NSView):
    """Initiate an opaque-id drag without ever putting photo data on the pasteboard."""

    drag_type = ""
    accessibility_label = "인물 이동"
    handle_symbol = "line.3.horizontal"

    def initWithPayload_controller_(self, payload: dict[str, Any], controller: Any):
        self = objc.super(_DragHandle, self).initWithFrame_(NSMakeRect(0.0, 0.0, 32.0, 32.0))
        if self is None:
            return None
        self._payload = dict(payload)
        self._payload["version"] = DRAG_PAYLOAD_VERSION
        self._payload["drag_type"] = self.drag_type
        self._controller = controller
        self.setWantsLayer_(True)
        self.layer().setCornerRadius_(5.0)
        self.layer().setBackgroundColor_(NSColor.tertiaryLabelColor().colorWithAlphaComponent_(0.18).CGColor())
        if self.handle_symbol:
            image = NSImage.imageWithSystemSymbolName_accessibilityDescription_(
                self.handle_symbol,
                self.accessibility_label,
            )
            image_view = NSImageView.alloc().initWithFrame_(NSMakeRect(7.0, 7.0, 18.0, 18.0))
            image_view.setImage_(image)
            if hasattr(image_view, "setContentTintColor_"):
                image_view.setContentTintColor_(NSColor.secondaryLabelColor())
            self.addSubview_(image_view)
        self.setAccessibilityLabel_(self.accessibility_label)
        self.setToolTip_(self.accessibility_label)
        return self

    def mouseDown_(self, event) -> None:
        item = NSPasteboardItem.alloc().init()
        item.setString_forType_(json.dumps(self._payload, sort_keys=True), self.drag_type)
        dragging_item = NSDraggingItem.alloc().initWithPasteboardWriter_(item)
        dragging_item.setDraggingFrame_contents_(self.bounds(), self)
        self.beginDraggingSessionWithItems_event_source_([dragging_item], event, self)

    def hitTest_(self, point):
        # AppKit supplies hit-test points in the receiver's superview coordinates.
        # Keep the whole visible handle active, including its decorative child.
        return self if NSPointInRect(point, self.frame()) else None

    def draggingSession_sourceOperationMaskForDraggingContext_(self, _session, _context):
        return NSDragOperationMove


class FaceDragHandle(_DragHandle):
    drag_type = FACE_DRAG_TYPE
    accessibility_label = "이 얼굴을 드래그하여 이동"


class IdentityDragHandle(_DragHandle):
    drag_type = IDENTITY_DRAG_TYPE
    accessibility_label = "이 그룹 전체를 드래그하여 합치기"


class FaceDragSurface(_DragHandle):
    """Make the face image itself a generous drag source."""

    drag_type = FACE_DRAG_TYPE
    accessibility_label = "이 얼굴을 드래그하여 이동"
    handle_symbol = ""


class _DropTargetView(NSButton):
    """A selected identity row or the new-group zone that receives opaque ids."""

    def initWithIdentityId_controller_(self, identity_id: str, controller: Any):
        self = objc.super(_DropTargetView, self).initWithFrame_(NSMakeRect(0.0, 0.0, 10.0, 10.0))
        if self is None:
            return None
        self.identity_id = identity_id
        self._controller = controller
        self._drop_highlighted = False
        self._resting_border_width = 0.0
        self._resting_border_color = None
        self.setBordered_(False)
        self.setTitle_("")
        self.setIdentifier_(identity_id)
        if identity_id:
            self.setTarget_(controller)
            self.setAction_("selectIdentity:")
        self.registerForDraggedTypes_([FACE_DRAG_TYPE, IDENTITY_DRAG_TYPE])
        return self

    def hitTest_(self, point):
        # AppKit calls hitTest_ with a point in the row's superview coordinates.
        # Reject points outside this row first; otherwise the last row in the
        # document intercepts every click in the list.
        if not NSPointInRect(point, self.frame()):
            return None
        local_point = self.convertPoint_fromView_(point, self.superview())
        for child in self.subviews():
            if isinstance(child, _DragHandle) and NSPointInRect(local_point, child.frame()):
                return child
        # Image and text children are decorative; the whole row selects.
        return self

    def mouseDown_(self, event) -> None:
        # This composite button rebuilds the detail panel when selected. AppKit's
        # normal button tracking waits for mouse-up, by which time that rebuild
        # can replace the row and silently lose its action. Select on mouse-down
        # instead; hitTest_ still routes the dedicated drag handle separately.
        if self.identity_id:
            self._controller.selectIdentityId_(self.identity_id)

    def keyDown_(self, event) -> None:
        if str(event.charactersIgnoringModifiers() or "") in {" ", "\r", "\n"}:
            self._controller.selectIdentityId_(self.identity_id)
            return
        objc.super(_DropTargetView, self).keyDown_(event)

    def draggingEntered_(self, sender):
        payload = self._payload_from_sender(sender)
        if payload is None or not self._controller.canAcceptPersonDrop_payload_(self.identity_id, payload):
            return 0
        self._set_drop_highlight(True)
        return NSDragOperationMove

    def draggingExited_(self, _sender) -> None:
        self._set_drop_highlight(False)

    def prepareForDragOperation_(self, _sender) -> bool:
        payload = self._payload_from_sender(_sender)
        return bool(payload and self._controller.canAcceptPersonDrop_payload_(self.identity_id, payload))

    def performDragOperation_(self, sender) -> bool:
        payload = self._payload_from_sender(sender)
        self._set_drop_highlight(False)
        if payload is None:
            return False
        return bool(self._controller.acceptPersonDrop_payload_(self.identity_id, payload))

    def _payload_from_sender(self, sender) -> dict[str, Any] | None:
        pasteboard = sender.draggingPasteboard()
        for drag_type in (FACE_DRAG_TYPE, IDENTITY_DRAG_TYPE):
            raw = pasteboard.stringForType_(drag_type)
            if not raw:
                continue
            try:
                payload = json.loads(str(raw))
            except (TypeError, ValueError):
                return None
            if isinstance(payload, dict):
                payload["drag_type"] = drag_type
                return payload
        return None

    def _set_drop_highlight(self, active: bool) -> None:
        self.setWantsLayer_(True)
        if active and not self._drop_highlighted:
            self._resting_border_width = float(self.layer().borderWidth())
            self._resting_border_color = self.layer().borderColor()
        self._drop_highlighted = active
        if active:
            self.layer().setBorderWidth_(3.0)
            self.layer().setBorderColor_(NSColor.controlAccentColor().CGColor())
            return
        self.layer().setBorderWidth_(self._resting_border_width)
        if self._resting_border_color is not None:
            self.layer().setBorderColor_(self._resting_border_color)


class IdentityDropRowView(_DropTargetView):
    pass


class NewIdentityDropZoneView(_DropTargetView):
    def initWithController_(self, controller: Any):
        self = self.initWithIdentityId_controller_("", controller)
        if self is not None:
            self.setRefusesFirstResponder_(True)
            self.setAccessibilityElement_(False)
        return self

    def performDragOperation_(self, sender) -> bool:
        payload = self._payload_from_sender(sender)
        self._set_drop_highlight(False)
        return bool(payload and self._controller.acceptNewIdentityDrop_(payload))

    def draggingEntered_(self, sender):
        payload = self._payload_from_sender(sender)
        if payload is None or not self._controller.canAcceptNewIdentityDrop_(payload):
            return 0
        self._set_drop_highlight(True)
        return NSDragOperationMove

    def prepareForDragOperation_(self, sender) -> bool:
        payload = self._payload_from_sender(sender)
        return bool(payload and self._controller.canAcceptNewIdentityDrop_(payload))
