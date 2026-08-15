"""Collection view and reusable local-photo grid item."""

from __future__ import annotations

from typing import Any

import objc
from AppKit import (
    NSButton,
    NSButtonTypeSwitch,
    NSCollectionView,
    NSCollectionViewItem,
    NSColor,
    NSControlSizeLarge,
    NSControlStateValueOff,
    NSControlStateValueOn,
    NSImageScaleProportionallyUpOrDown,
    NSImageView,
    NSMakeRect,
    NSTextField,
    NSView,
)

from photos_mcp.infrastructure.sources.local_files.models import LocalPhoto
from photos_mcp.interfaces.appkit.shared.theme import accent_color, app_font, subtle_border_color


class PhotoCollectionView(NSCollectionView):
    """Add selection toggling without replacing native grid navigation."""

    def keyDown_(self, event) -> None:
        owner = getattr(self, "_browser_owner", None)
        if owner is not None and int(event.keyCode()) in {36, 49, 76}:
            owner.toggleGridPhotoFromKeyboard_(self)
            return
        objc.super(PhotoCollectionView, self).keyDown_(event)


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
        self._check_button.setHidden_(False)
        self._check_button.setEnabled_(True)

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
        preview_only = bool(
            hasattr(controller, "is_read_only_preview") and controller.is_read_only_preview()
        )
        self._check_button.setHidden_(preview_only)
        self._check_button.setEnabled_(not preview_only)
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


__all__ = ["PhotoCollectionView", "PhotosMcpLocalPhotoItem"]
