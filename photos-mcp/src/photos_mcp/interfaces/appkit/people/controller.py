"""AppKit view for user-managed local face groups."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import objc
from AppKit import (
    NSAlert,
    NSAlertFirstButtonReturn,
    NSButton,
    NSButtonTypeSwitch,
    NSColor,
    NSImageScaleProportionallyUpOrDown,
    NSImageView,
    NSMakeRect,
    NSPopUpButton,
    NSScrollView,
    NSTextField,
    NSView,
)
from Foundation import NSObject

from photos_mcp.application.face_identity_review import face_measurements_path
from photos_mcp.application.person_identity_management import (
    PeopleCatalog,
    PersonIdentity,
    PersonIdentityRegistry,
    build_people_catalog,
)
from photos_mcp.interfaces.appkit.results.collection_item import cached_image
from photos_mcp.interfaces.appkit.results.photo_viewer import PhotosMcpPhotoViewerController
from photos_mcp.interfaces.appkit.shared.theme import accent_color, app_font, panel_background_color, subtle_border_color


_MARGIN = 28.0
_LIST_WIDTH = 286.0
_FACE_CARD_WIDTH = 142.0
_FACE_CARD_HEIGHT = 184.0
_FACE_CARD_GAP = 12.0


class PhotosMcpPeopleManagerController(NSObject):
    """Render and edit private person labels without touching source libraries."""

    def initWithMainController_(self, main_controller: Any):
        self = objc.super(PhotosMcpPeopleManagerController, self).init()
        if self is None:
            return None
        self._main_controller = main_controller
        self._menu_controller = main_controller._menu_controller
        self._registry = PersonIdentityRegistry()
        self._catalog = PeopleCatalog(identities=(), face_count=0, source_job_count=0)
        self._selected_identity_id = ""
        self._selected_face_ids: set[str] = set()
        self._merge_targets: list[str] = []
        self._merge_popup = None
        self._name_field = None
        self._viewer_controller = PhotosMcpPhotoViewerController.alloc().init()
        self._load_catalog()
        return self

    @objc.python_method
    def renderInParent_width_height_(self, parent: Any, width: float, height: float) -> None:
        for view in list(parent.subviews()):
            view.removeFromSuperview()
        parent.setWantsLayer_(True)
        parent.layer().setBackgroundColor_(NSColor.windowBackgroundColor().CGColor())
        usable = width - (_MARGIN * 2.0)
        self._label(parent, _MARGIN, height - 58.0, usable - 132.0, 36.0, "인물 관리", 27.0, True)
        self._label(
            parent,
            _MARGIN,
            height - 84.0,
            usable - 132.0,
            20.0,
            "얼굴 묶음을 직접 확인하고 이름, 병합, 분리를 로컬에서 관리합니다.",
            11.0,
            False,
            secondary=True,
        )
        self._button(parent, width - _MARGIN - 110.0, height - 66.0, 110.0, 32.0, "새로 고침", "refreshCatalog:")

        status = f"{len(self._catalog.identities)}개 묶음 · 얼굴 {self._catalog.face_count}개 · 최근 작업 {self._catalog.source_job_count}개"
        self._label(parent, _MARGIN, height - 112.0, usable, 18.0, status, 9.8, False, secondary=True)
        content_y = 26.0
        content_height = max(260.0, height - 150.0)
        self._identity_list(parent, _MARGIN, content_y, _LIST_WIDTH, content_height)
        self._detail_panel(parent, _MARGIN + _LIST_WIDTH + 18.0, content_y, usable - _LIST_WIDTH - 18.0, content_height)

    def refreshCatalog_(self, _sender) -> None:
        self._load_catalog()
        self._main_controller.rebuild()

    def selectIdentity_(self, sender) -> None:
        identity_id = str(sender.identifier() or "")
        if self._catalog.identity(identity_id) is None:
            return
        self._selected_identity_id = identity_id
        self._selected_face_ids.clear()
        self._main_controller.rebuild()

    def toggleFace_(self, sender) -> None:
        face_id = str(sender.identifier() or "")
        if not face_id:
            return
        if bool(sender.state()):
            self._selected_face_ids.add(face_id)
        else:
            self._selected_face_ids.discard(face_id)
        self._refresh_controls_only()

    def saveName_(self, _sender) -> None:
        identity = self._selected_identity()
        if identity is None or self._name_field is None:
            return
        name = str(self._name_field.stringValue() or "").strip()
        self._selected_identity_id = self._registry.assign_name(identity, name)
        self._selected_face_ids.clear()
        self._load_catalog()
        self._main_controller.rebuild()

    def splitSelectedFaces_(self, _sender) -> None:
        identity = self._selected_identity()
        if identity is None:
            return
        try:
            self._selected_identity_id = self._registry.split_faces(identity, self._selected_face_ids)
        except ValueError as exc:
            self._alert("얼굴을 분리할 수 없습니다", str(exc))
            return
        self._selected_face_ids.clear()
        self._load_catalog()
        self._main_controller.rebuild()

    def mergeIntoSelected_(self, _sender) -> None:
        identity = self._selected_identity()
        if identity is None:
            return
        target_index = int(self._merge_popup.indexOfSelectedItem()) if self._merge_popup is not None else -1
        if not 0 <= target_index < len(self._merge_targets):
            return
        target = self._catalog.identity(self._merge_targets[target_index])
        if target is None:
            return
        self._selected_identity_id = self._registry.merge_identities(identity, target)
        self._selected_face_ids.clear()
        self._load_catalog()
        self._main_controller.rebuild()

    def clearManualChanges_(self, _sender) -> None:
        identity = self._selected_identity()
        if identity is None or not identity.is_manual:
            return
        alert = NSAlert.alloc().init()
        alert.setMessageText_("이름과 수동 변경을 지울까요?")
        alert.setInformativeText_("이 묶음의 이름, 직접 병합, 직접 분리를 지우고 자동 묶음으로 되돌립니다. 원본 사진은 변경하지 않습니다.")
        alert.addButtonWithTitle_("지우기")
        alert.addButtonWithTitle_("취소")
        if alert.runModal() != NSAlertFirstButtonReturn:
            return
        self._registry.clear_manual_changes(identity)
        self._selected_identity_id = ""
        self._selected_face_ids.clear()
        self._load_catalog()
        self._main_controller.rebuild()

    def openFaceSource_(self, sender) -> None:
        identity = self._selected_identity()
        if identity is None:
            return
        face_id = str(sender.identifier() or "")
        face = next((item for item in identity.faces if item.face_id == face_id), None)
        if face is None:
            return
        item = {
            "photo_id": face.photo_id,
            "preview_path": face.preview_path,
            "source_photo_path": face.source_photo_path,
            "scene_description": f"{identity.display_name}의 원본 사진",
            "event_type": "other",
        }
        self._viewer_controller.show_items([item], face.photo_id)

    @objc.python_method
    def _load_catalog(self) -> None:
        sources: list[tuple[str, dict[str, Any], Path]] = []
        snapshot = self._menu_controller._state_store.snapshot()
        for job in snapshot.recent_jobs:
            if str(job.get("status") or "") != "completed" or not bool(job.get("result_available")):
                continue
            job_id = str(job.get("job_id") or "")
            if not job_id:
                continue
            try:
                payload = self._menu_controller._daemon_controller.get_job_review_result(job_id, top_n=1000)
            except (AttributeError, OSError, ValueError):
                continue
            if payload.get("error"):
                continue
            sources.append((job_id, payload, face_measurements_path(job_id)))
        self._catalog = build_people_catalog(sources, registry=self._registry)
        if self._catalog.identity(self._selected_identity_id) is None:
            self._selected_identity_id = self._catalog.identities[0].identity_id if self._catalog.identities else ""
        selected = self._selected_identity()
        allowed = {face.face_id for face in selected.faces} if selected else set()
        self._selected_face_ids.intersection_update(allowed)

    @objc.python_method
    def _selected_identity(self) -> PersonIdentity | None:
        return self._catalog.identity(self._selected_identity_id)

    @objc.python_method
    def _identity_list(self, parent: Any, x: float, y: float, width: float, height: float) -> None:
        card = self._card(parent, x, y, width, height, selected=False)
        self._label(card, 18.0, height - 40.0, width - 36.0, 24.0, "인물 묶음", 14.5, True)
        self._label(card, 18.0, height - 60.0, width - 36.0, 18.0, "이름은 사용자가 직접 지정합니다.", 8.8, False, secondary=True)
        scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(10.0, 12.0, width - 20.0, max(60.0, height - 82.0)))
        scroll.setHasVerticalScroller_(True)
        scroll.setDrawsBackground_(False)
        document_height = max(1.0, len(self._catalog.identities) * 58.0 + 8.0)
        document = NSView.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, width - 34.0, document_height))
        scroll.setDocumentView_(document)
        for index, identity in enumerate(self._catalog.identities):
            row_y = document_height - ((index + 1) * 58.0)
            selected = identity.identity_id == self._selected_identity_id
            row = self._card(document, 0.0, row_y, width - 34.0, 52.0, selected=selected)
            button = NSButton.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, width - 34.0, 52.0))
            button.setBordered_(False)
            button.setTitle_("")
            button.setTarget_(self)
            button.setAction_("selectIdentity:")
            button.setIdentifier_(identity.identity_id)
            button.setAccessibilityLabel_(f"{identity.display_name}, 얼굴 {len(identity.faces)}개")
            row.addSubview_(button)
            self._label(row, 14.0, 27.0, width - 86.0, 18.0, identity.display_name, 11.2, True)
            self._label(row, 14.0, 9.0, width - 86.0, 16.0, f"얼굴 {len(identity.faces)}개 · {'수동 관리' if identity.is_manual else '자동 묶음'}", 8.8, False, secondary=True)
            count = self._label(row, width - 68.0, 17.0, 48.0, 18.0, str(len(identity.faces)), 11.0, True, accent=selected)
            count.setAlignment_(2)
        card.addSubview_(scroll)

    @objc.python_method
    def _detail_panel(self, parent: Any, x: float, y: float, width: float, height: float) -> None:
        identity = self._selected_identity()
        if identity is None:
            card = self._card(parent, x, y, width, height, selected=False)
            self._label(card, 24.0, height - 50.0, width - 48.0, 28.0, "관리할 얼굴이 없습니다", 17.0, True)
            self._label(card, 24.0, height - 78.0, width - 48.0, 22.0, "얼굴이 포함된 사진 분석을 완료하면 이곳에 자동 묶음이 표시됩니다.", 10.5, False, secondary=True)
            return
        card = self._card(parent, x, y, width, height, selected=True)
        self._label(card, 20.0, height - 42.0, width - 40.0, 24.0, identity.display_name, 16.0, True)
        self._label(card, 20.0, height - 64.0, width - 40.0, 18.0, f"얼굴 {len(identity.faces)}개 · {'수동 관리 중' if identity.is_manual else '자동 묶음'}", 9.0, False, secondary=True)
        self._label(card, 20.0, height - 100.0, 58.0, 20.0, "이름", 10.2, True)
        self._name_field = NSTextField.alloc().initWithFrame_(NSMakeRect(72.0, height - 106.0, max(120.0, width - 286.0), 28.0))
        self._name_field.setStringValue_(identity.name)
        self._name_field.setPlaceholderString_("예: 엄마, 민서")
        self._name_field.setFont_(app_font(10.5, "regular"))
        self._name_field.setAccessibilityLabel_("인물 이름")
        card.addSubview_(self._name_field)
        self._button(card, width - 198.0, height - 106.0, 86.0, 28.0, "이름 저장", "saveName:", primary=True)
        self._button(card, width - 102.0, height - 106.0, 82.0, 28.0, "원본 보기", "openFaceSource:", identifier=identity.faces[0].face_id)

        controls_y = 18.0
        # Keep person actions outside the scrollable face grid so merge controls
        # remain visible when the selected group contains many faces.
        gallery_y = controls_y + 96.0
        gallery_height = max(120.0, height - gallery_y - 124.0)
        self._face_gallery(card, 20.0, gallery_y, width - 40.0, gallery_height, identity)
        self._label(card, 20.0, 76.0, width - 40.0, 16.0, "얼굴을 선택해 새 묶음으로 분리하거나 현재 묶음을 다른 묶음에 병합할 수 있습니다.", 8.8, False, secondary=True)
        self._button(card, 20.0, 42.0, 168.0, 28.0, "선택 얼굴 새 묶음", "splitSelectedFaces:", enabled=0 < len(self._selected_face_ids) < len(identity.faces))
        self._merge_targets = [item.identity_id for item in self._catalog.identities if item.identity_id != identity.identity_id]
        popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSMakeRect(198.0, 42.0, max(120.0, width - 514.0), 28.0),
            False,
        )
        popup.setFont_(app_font(9.0, "regular"))
        popup.addItemsWithTitles_([self._catalog.identity(item).display_name for item in self._merge_targets])
        popup.setEnabled_(bool(self._merge_targets))
        popup.setAccessibilityLabel_("병합할 인물 묶음")
        card.addSubview_(popup)
        self._merge_popup = popup
        self._button(card, width - 202.0, 42.0, 110.0, 28.0, "선택 묶음 병합", "mergeIntoSelected:", enabled=bool(self._merge_targets))
        self._button(card, width - 82.0, 42.0, 62.0, 28.0, "되돌리기", "clearManualChanges:", enabled=identity.is_manual)

    @objc.python_method
    def _face_gallery(self, parent: Any, x: float, y: float, width: float, height: float, identity: PersonIdentity) -> None:
        scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(x, y, width, height))
        scroll.setHasVerticalScroller_(True)
        scroll.setDrawsBackground_(False)
        columns = max(1, int((width + _FACE_CARD_GAP) // (_FACE_CARD_WIDTH + _FACE_CARD_GAP)))
        rows = max(1, (len(identity.faces) + columns - 1) // columns)
        document_height = rows * (_FACE_CARD_HEIGHT + _FACE_CARD_GAP) + _FACE_CARD_GAP
        document = NSView.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, width - 14.0, document_height))
        scroll.setDocumentView_(document)
        for index, face in enumerate(identity.faces):
            column, row = index % columns, index // columns
            card_x = column * (_FACE_CARD_WIDTH + _FACE_CARD_GAP)
            card_y = document_height - ((row + 1) * (_FACE_CARD_HEIGHT + _FACE_CARD_GAP))
            self._face_card(document, card_x, card_y, face)
        parent.addSubview_(scroll)

    @objc.python_method
    def _face_card(self, parent: Any, x: float, y: float, face: Any) -> None:
        selected = face.face_id in self._selected_face_ids
        card = self._card(parent, x, y, _FACE_CARD_WIDTH, _FACE_CARD_HEIGHT, selected=selected)
        image_view = NSImageView.alloc().initWithFrame_(NSMakeRect(8.0, 40.0, _FACE_CARD_WIDTH - 16.0, 132.0))
        image_view.setImageScaling_(NSImageScaleProportionallyUpOrDown)
        image_view.setImage_(cached_image(face.crop_path))
        image_view.setAccessibilityLabel_("감지된 얼굴")
        card.addSubview_(image_view)
        checkbox = NSButton.alloc().initWithFrame_(NSMakeRect(8.0, 12.0, 54.0, 22.0))
        checkbox.setButtonType_(NSButtonTypeSwitch)
        checkbox.setTitle_("선택")
        checkbox.setState_(1 if selected else 0)
        checkbox.setTarget_(self)
        checkbox.setAction_("toggleFace:")
        checkbox.setIdentifier_(face.face_id)
        checkbox.setFont_(app_font(8.8, "medium"))
        checkbox.setAccessibilityLabel_("얼굴 선택")
        card.addSubview_(checkbox)
        self._button(card, 70.0, 10.0, 64.0, 24.0, "사진 보기", "openFaceSource:", identifier=face.face_id)

    @objc.python_method
    def _refresh_controls_only(self) -> None:
        self._main_controller.rebuild()

    @objc.python_method
    def _card(self, parent: Any, x: float, y: float, width: float, height: float, *, selected: bool) -> Any:
        card = NSView.alloc().initWithFrame_(NSMakeRect(x, y, width, height))
        card.setWantsLayer_(True)
        card.layer().setCornerRadius_(12.0)
        card.layer().setBackgroundColor_(panel_background_color().CGColor())
        border = accent_color() if selected else subtle_border_color()
        card.layer().setBorderColor_(border.colorWithAlphaComponent_(0.7 if selected else 1.0).CGColor())
        card.layer().setBorderWidth_(2.0 if selected else 1.0)
        parent.addSubview_(card)
        return card

    @objc.python_method
    def _label(self, parent: Any, x: float, y: float, width: float, height: float, text: str, size: float, bold: bool, *, secondary: bool = False, accent: bool = False) -> Any:
        label = NSTextField.labelWithString_(text)
        label.setFrame_(NSMakeRect(x, y, width, height))
        label.setFont_(app_font(size, "semibold" if bold else "regular"))
        label.setTextColor_(accent_color() if accent else NSColor.secondaryLabelColor() if secondary else NSColor.labelColor())
        parent.addSubview_(label)
        return label

    @objc.python_method
    def _button(self, parent: Any, x: float, y: float, width: float, height: float, title: str, action: str, *, identifier: str = "", primary: bool = False, enabled: bool = True) -> Any:
        button = NSButton.alloc().initWithFrame_(NSMakeRect(x, y, width, height))
        button.setTitle_(title)
        button.setTarget_(self)
        button.setAction_(action)
        button.setEnabled_(enabled)
        button.setFont_(app_font(9.5, "semibold"))
        button.setAccessibilityLabel_(title)
        if identifier:
            button.setIdentifier_(identifier)
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
