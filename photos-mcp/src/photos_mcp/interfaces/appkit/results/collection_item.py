"""Reusable collection item for the result gallery."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import objc
from AppKit import (
    NSButton,
    NSCache,
    NSColor,
    NSCollectionViewItem,
    NSImage,
    NSImageScaleProportionallyUpOrDown,
    NSImageView,
    NSLineBreakByWordWrapping,
    NSMakeRect,
    NSTextField,
    NSView,
)

from photos_mcp.application.result_presenter import (
    recommendation_reason_summary,
    result_category,
    result_item_failure,
)
from photos_mcp.interfaces.appkit.shared.theme import accent_color, app_font


_IMAGE_CACHE = NSCache.alloc().init()
_IMAGE_CACHE.setCountLimit_(128)


def status_color(status: str) -> Any:
    return {
        "success": NSColor.systemGreenColor(),
        "warning": NSColor.systemYellowColor(),
        "error": NSColor.systemRedColor(),
    }.get(status, NSColor.secondaryLabelColor())


def cached_image(path: str) -> Any | None:
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
    """Load only the preview currently represented by this card."""

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
        self._scene_button = NSButton.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, 1.0, 1.0))
        self._scene_button.setBezelStyle_(0)
        self._scene_button.setFont_(app_font(9.5, "medium"))
        self._scene_button.setAccessibilityLabel_("같은 장면의 다른 사진 보기")
        root.addSubview_(self._scene_button)

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
        image_y = 112.0
        image_height = max(92.0, height - 120.0)
        self._image_view.setFrame_(NSMakeRect(7.0, image_y, max(1.0, width - 14.0), image_height))
        self._placeholder.setFrame_(NSMakeRect(14.0, image_y + image_height / 2.0 - 10.0, width - 28.0, 20.0))
        self._badge.setFrame_(NSMakeRect(12.0, 86.0, max(70.0, width - 88.0), 18.0))
        self._score.setFrame_(NSMakeRect(max(12.0, width - 64.0), 84.0, 52.0, 21.0))
        self._reason.setFrame_(NSMakeRect(12.0, 46.0, max(1.0, width - 24.0), 34.0))
        self._scene_button.setFrame_(NSMakeRect(12.0, 12.0, max(1.0, width - 24.0), 28.0))
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
        scene_gallery = bool(item.get("_scene_gallery"))
        grouped_scene = scene_gallery and cluster_size > 1
        if not failure and grouped_scene and category == "recommended":
            category_label = f"BEST · 같은 장면 {cluster_size}장"
        elif not failure and grouped_scene:
            category_label = f"검토 대표 · 같은 장면 {cluster_size}장"
        elif not failure and cluster_size > 1:
            if category == "recommended":
                slot = recommendation_slot or min(cluster_rank, 2)
                category_label = f"추천 {slot}/2 · 같은 장면 {cluster_size}장"
            else:
                category_label = f"대안 · {cluster_size}장 중 {cluster_rank}위"
        tone = "error" if failure else {
            "recommended": "success",
            "review": "warning",
        }[category]
        reason = failure or recommendation_reason_summary(item)
        score = float(item.get("total_score") or item.get("quality_score") or 0.0)
        image = None if failure else cached_image(str(item.get("preview_path") or ""))
        self._image_view.setImage_(image)
        self._placeholder.setHidden_(image is not None)
        self._placeholder.setStringValue_("분석 실패" if failure else "미리보기 없음")
        self._placeholder.setTextColor_(status_color(tone))
        self._badge.setStringValue_(category_label)
        self._badge.setTextColor_(status_color(tone))
        self._score.setStringValue_(f"{score:.0f}")
        self._score.setTextColor_(status_color(tone))
        self._reason.setStringValue_(reason)
        self._reason.setToolTip_(reason)
        alternative_count = int(item.get("_scene_alternative_count") or 0)
        show_scene_button = scene_gallery and alternative_count > 0
        self._scene_button.setHidden_(not show_scene_button)
        self._scene_button.setTitle_(f"같은 장면 대안 {alternative_count}장 보기")
        self._scene_button.setIdentifier_(str(item.get("photo_id") or ""))
        self._scene_button.setTarget_(controller)
        self._scene_button.setAction_("openSceneComparison:")
        self._scene_button.setEnabled_(show_scene_button)
        self._scene_button.setToolTip_(
            "같은 장면으로 묶인 사진을 점수순으로 비교합니다." if show_scene_button else ""
        )
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


__all__ = ["PhotosMcpResultCollectionItem", "cached_image", "status_color"]
