"""Folder-tree models and AppKit disclosure presentation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import objc
from AppKit import (
    NSFontWeightRegular,
    NSImage,
    NSImageScaleProportionallyUpOrDown,
    NSImageSymbolConfiguration,
    NSOutlineView,
    NSOutlineViewDisclosureButtonKey,
)


_DISCLOSURE_ICON_SIZE = 17.0


def configure_disclosure_button(button) -> None:
    configuration = NSImageSymbolConfiguration.configurationWithPointSize_weight_(
        _DISCLOSURE_ICON_SIZE,
        NSFontWeightRegular,
    )
    collapsed = NSImage.imageWithSystemSymbolName_accessibilityDescription_("chevron.right", "펼치기")
    expanded = NSImage.imageWithSystemSymbolName_accessibilityDescription_("chevron.down", "접기")
    if collapsed is None or expanded is None:
        return
    collapsed = collapsed.imageWithSymbolConfiguration_(configuration)
    expanded = expanded.imageWithSymbolConfiguration_(configuration)
    collapsed.setTemplate_(True)
    expanded.setTemplate_(True)
    button.setImage_(collapsed)
    button.setAlternateImage_(expanded)
    button.setImageScaling_(NSImageScaleProportionallyUpOrDown)


class LargeDisclosureOutlineView(NSOutlineView):
    """Keep native outline behavior while matching disclosure glyphs to body text."""

    def makeViewWithIdentifier_owner_(self, identifier, owner):
        view = objc.super(LargeDisclosureOutlineView, self).makeViewWithIdentifier_owner_(identifier, owner)
        if view is not None and str(identifier or "") == str(NSOutlineViewDisclosureButtonKey):
            configure_disclosure_button(view)
        return view


@dataclass(frozen=True)
class FolderNode:
    """A source-list group, a folder, or a temporary loading row."""

    key: str
    title: str
    path: str = ""
    kind: str = "folder"


def default_root_path() -> Path:
    pictures = Path.home() / "Pictures"
    return pictures if pictures.is_dir() else Path.home()


def folder_nodes_for_path(path: Path) -> list[FolderNode]:
    """Return direct child folders without following inaccessible entries."""

    try:
        children = [item for item in path.iterdir() if item.is_dir() and not item.name.startswith(".")]
    except (OSError, PermissionError):
        return []
    return [
        FolderNode(key=f"folder:{item.resolve()}", title=item.name, path=str(item.resolve()))
        for item in sorted(children, key=lambda item: item.name.casefold())
    ]


__all__ = [
    "FolderNode",
    "LargeDisclosureOutlineView",
    "configure_disclosure_button",
    "default_root_path",
    "folder_nodes_for_path",
]
