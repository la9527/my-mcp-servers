"""Keyboard and zoom views used by the local single-photo browser."""

from __future__ import annotations

import objc
from AppKit import NSEventModifierFlagCommand, NSView

from photos_mcp.interfaces.appkit.results.photo_viewer import PhotosMcpZoomImageView


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
    """Reuse result-viewer gestures while preserving local-browser keys."""

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


__all__ = ["FlippedDocumentView", "LocalSinglePhotoZoomImageView", "SinglePhotoKeyView"]
