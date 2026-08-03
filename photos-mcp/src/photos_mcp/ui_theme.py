"""Shared visual tokens for the native Photos MCP interfaces."""

from __future__ import annotations

from typing import Any

from AppKit import (
    NSColor,
    NSFont,
    NSFontWeightBold,
    NSFontWeightHeavy,
    NSFontWeightMedium,
    NSFontWeightRegular,
    NSFontWeightSemibold,
)


SPACING = {
    "xs": 4.0,
    "sm": 8.0,
    "md": 12.0,
    "lg": 16.0,
    "xl": 24.0,
    "xxl": 32.0,
}

ICON_SIZE = {
    "small": 16.0,
    "medium": 20.0,
    "large": 28.0,
}

_FONT_WEIGHTS = {
    "regular": NSFontWeightRegular,
    "medium": NSFontWeightMedium,
    "semibold": NSFontWeightSemibold,
    "bold": NSFontWeightBold,
    "extrabold": NSFontWeightHeavy,
}


def scaled_font_size(size: float) -> float:
    """Scale typography for a large desktop canvas without flattening hierarchy."""

    if size >= 24.0:
        scale = 1.16
    elif size >= 16.0:
        scale = 1.20
    else:
        scale = 1.27
    return round(size * scale, 1)


def app_font(size: float, weight: str = "regular") -> Any:
    """Return the native macOS system font with automatic Korean fallback."""

    return NSFont.systemFontOfSize_weight_(
        scaled_font_size(size),
        _FONT_WEIGHTS.get(weight, NSFontWeightRegular),
    )


def accent_color() -> Any:
    return NSColor.controlAccentColor()


def sidebar_background_color() -> Any:
    return NSColor.windowBackgroundColor()


def selected_sidebar_color() -> Any:
    return NSColor.controlAccentColor().colorWithAlphaComponent_(0.16)


def panel_background_color() -> Any:
    return NSColor.controlBackgroundColor().colorWithAlphaComponent_(0.72)


def subtle_border_color() -> Any:
    return NSColor.separatorColor().colorWithAlphaComponent_(0.56)
