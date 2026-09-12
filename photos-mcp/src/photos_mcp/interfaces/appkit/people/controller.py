"""AppKit view for user-managed local face groups."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sqlite3
from threading import Thread
from typing import Any
import uuid

import objc
from AppKit import (
    NSAlert,
    NSAlertFirstButtonReturn,
    NSButton,
    NSButtonTypeSwitch,
    NSColor,
    NSControlStateValueOn,
    NSEventModifierFlagCommand,
    NSImage,
    NSImageAbove,
    NSImageOnly,
    NSImageScaleProportionallyUpOrDown,
    NSImageView,
    NSMakePoint,
    NSMakeRect,
    NSPopUpButton,
    NSScrollView,
    NSTextField,
    NSView,
)
from Foundation import NSMakeRange, NSObject

from photos_mcp.application.face_identity_review import face_measurements_path
from photos_mcp.application.person_identity_management import (
    PeopleCatalog,
    PersonIdentity,
    PersonIdentityRegistry,
    build_people_catalog,
    merge_stable_people_catalog,
)
from photos_mcp.application.person_indexing import PersonIndexingService, face_runtime_status
from photos_mcp.application.people_workspace import (
    PeopleWorkspaceService,
    provider_alias_matches_identity_name,
)
from photos_mcp.application.share_image_service import ShareImageError, ShareImageService
from photos_mcp.application.story_generation import refresh_all_story_location_projections
from photos_mcp.interfaces.appkit.results.collection_item import cached_image
from photos_mcp.interfaces.appkit.results.photo_viewer import PhotosMcpPhotoViewerController
from photos_mcp.interfaces.appkit.shared.theme import accent_color, app_font, panel_background_color, subtle_border_color
from photos_mcp.interfaces.appkit.people.drag_views import (
    DRAG_PAYLOAD_VERSION,
    FACE_DRAG_TYPE,
    IDENTITY_DRAG_TYPE,
    FaceDragHandle,
    IdentityDragHandle,
    IdentityDropRowView,
    NewIdentityDropZoneView,
)


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
        self._identity_repository = getattr(self._menu_controller, "_identity_repository", None)
        self._catalog = PeopleCatalog(identities=(), face_count=0, source_job_count=0)
        self._selected_identity_id = ""
        self._focused_identity_id = ""
        self._focused_face_selection_id = ""
        self._selected_face_ids: set[str] = set()
        self._name_field = None
        self._name_draft_identity_id = ""
        self._name_draft = ""
        self._name_selection_range: tuple[int, int] | None = None
        self._name_restore_pending_identity_id = ""
        self._move_popup = None
        self._alias_identity_popup = None
        self._aliases = ()
        self._selected_alias_id = ""
        self._selected_alias_face_id = ""
        self._deferred_alias_ids: set[str] = set()
        self._face_runtime = face_runtime_status()
        self._index_running = False
        self._index_message = ""
        self._alias_indexing_asset_id = ""
        self._alias_index_message = ""
        self._move_target_identity_id = ""
        self._identity_scroll_view = None
        self._gallery_scroll_view = None
        self._identity_scroll_origin = None
        self._gallery_scroll_origin = None
        self._identity_labels: dict[str, str] = {}
        self._undo_snapshot: dict[str, Any] | None = None
        self._stable_name_undo: tuple[str, str, str] | None = None
        self._undo_message = ""
        self._people_dashboard: dict[str, Any] = {}
        self._view_mode = "home"
        self._viewer_controller = PhotosMcpPhotoViewerController.alloc().init()
        self._load_catalog()
        return self

    @objc.python_method
    def renderInParent_width_height_(self, parent: Any, width: float, height: float) -> None:
        self._capture_transient_state()
        self._name_field = None
        self._move_popup = None
        self._identity_scroll_view = None
        self._gallery_scroll_view = None
        for view in list(parent.subviews()):
            view.removeFromSuperview()
        parent.setWantsLayer_(True)
        parent.layer().setBackgroundColor_(NSColor.windowBackgroundColor().CGColor())
        if self._view_mode == "detail" and (
            self._selected_identity() is not None or self._selected_alias() is not None
        ):
            self._render_person_detail(parent, width, height)
            return
        self._view_mode = "home"
        self._render_people_home(parent, width, height)

    @objc.python_method
    def _render_people_home(self, parent: Any, width: float, height: float) -> None:
        usable = width - (_MARGIN * 2.0)
        self._label(parent, _MARGIN, height - 58.0, usable - 330.0, 36.0, "인물 관리", 27.0, True)
        self._label(
            parent,
            _MARGIN,
            height - 84.0,
            usable - 132.0,
            20.0,
            "자주 등장하는 사람은 자동으로 정리하고, 애매한 경우만 확인합니다.",
            11.0,
            False,
            secondary=True,
        )
        self._button(parent, width - _MARGIN - 86.0, height - 66.0, 86.0, 32.0, "새로 고침", "refreshCatalog:")
        exception_count = int(self._people_dashboard.get("exception_count") or 0)
        review_count = exception_count + len(self._aliases)
        review_y = height - 220.0
        review = self._card(parent, _MARGIN, review_y, usable, 108.0, selected=bool(review_count))
        review_title = f"확인할 내용이 {review_count}개 있어요" if review_count else "지금은 모두 정리됐어요"
        review_detail = (
            "애매한 얼굴과 새로 자주 보이는 사람만 확인하면 됩니다."
            if review_count
            else "새 사진에서 확실한 인물은 자동으로 연결하고, 애매한 경우만 여기에 모읍니다."
        )
        self._label(review, 24.0, 58.0, max(260.0, usable - 230.0), 26.0, review_title, 16.0, True)
        self._label(review, 24.0, 30.0, max(260.0, usable - 230.0), 22.0, review_detail, 10.5, False, secondary=True)
        self._button(
            review,
            usable - 190.0,
            36.0,
            166.0,
            36.0,
            "빠르게 확인하기",
            "reviewFirstException:",
            primary=True,
            enabled=bool(review_count),
        )

        managed = self._managed_identities()
        section_y = review_y - 54.0
        self._label(parent, _MARGIN, section_y, usable - 240.0, 26.0, f"관리 중인 인물 {len(managed)}명", 16.0, True)
        self._label(parent, _MARGIN, section_y - 22.0, usable - 240.0, 18.0, "대표 얼굴을 선택하면 이름과 자동 인식 설정을 확인할 수 있습니다.", 9.5, False, secondary=True)
        self._button(
            parent,
            width - _MARGIN - 164.0,
            section_y - 8.0,
            164.0,
            30.0,
            "인물 분석 다시 실행",
            "indexPeople:",
            enabled=not self._index_running and self._face_runtime.status == "ready",
        )
        if self._index_message:
            self._label(parent, _MARGIN, section_y - 42.0, usable, 18.0, self._index_message, 9.2, False, secondary=True)
        grid_top = section_y - (58.0 if self._index_message else 42.0)
        self._people_card_grid(parent, _MARGIN, 30.0, usable, max(180.0, grid_top - 30.0), managed)

    @objc.python_method
    def _render_person_detail(self, parent: Any, width: float, height: float) -> None:
        usable = width - (_MARGIN * 2.0)
        identity = self._selected_identity()
        alias = self._selected_alias()
        title = (
            "사진 속 이름 확인"
            if alias is not None
            else self._identity_labels.get(identity.identity_id, identity.display_name)
            if identity is not None
            else "인물"
        )
        self._button(parent, _MARGIN, height - 66.0, 116.0, 32.0, "← 인물 목록", "backToPeopleHome:")
        self._label(parent, _MARGIN + 136.0, height - 58.0, usable - 350.0, 36.0, title, 25.0, True)
        self._label(parent, _MARGIN + 136.0, height - 82.0, usable - 180.0, 20.0, "이름, 자동 인식과 Story 표시 설정을 관리합니다.", 10.0, False, secondary=True)
        self._button(parent, width - _MARGIN - 86.0, height - 66.0, 86.0, 32.0, "새로 고침", "refreshCatalog:")
        self._detail_panel(parent, _MARGIN, 26.0, usable, max(320.0, height - 126.0))

    def backToPeopleHome_(self, _sender) -> None:
        self._freeze_name_state()
        self._view_mode = "home"
        self._selected_alias_id = ""
        self._selected_alias_face_id = ""
        self._selected_face_ids.clear()
        self._main_controller.rebuild()

    def refreshCatalog_(self, _sender) -> None:
        self._load_catalog()
        self._main_controller.rebuild()

    def showFirstAlias_(self, _sender) -> None:
        if not self._aliases:
            return
        self._deferred_alias_ids.clear()
        self._selected_alias_id = self._aliases[0].alias_id
        self._selected_alias_face_id = ""
        self._selected_identity_id = ""
        self._view_mode = "detail"
        self._main_controller.rebuild()

    def reviewFirstException_(self, _sender) -> None:
        if self._identity_repository is None:
            return
        workspace = PeopleWorkspaceService(
            self._identity_repository,
            run_repository=self._menu_controller._state_store.run_repository,
        )
        try:
            page = workspace.list_asset_reviews(state="pending", limit=1)
        except (OSError, sqlite3.Error, ValueError):
            self._alert("확인할 내용을 불러오지 못했습니다", "잠시 후 다시 시도해 주세요.")
            return
        if not page.items:
            if self._aliases:
                self.showFirstAlias_(None)
                return
            self._alert("지금 확인할 내용이 없습니다", "새 사진을 분석하면 애매한 얼굴만 이곳에 표시됩니다.")
            return
        detail = page.items[0]
        faces = list(detail.get("faces") or [])
        if not faces:
            self._load_catalog()
            self._main_controller.rebuild()
            return
        face = faces[0]
        suggestion_name = str(face.get("suggested_display_name") or "").strip()
        review_kind = str(face.get("review_kind") or "quick_confirmation")
        alert = NSAlert.alloc().init()
        if review_kind == "promoted_new_person":
            alert.setMessageText_("새로 자주 보이는 사람")
            alert.setInformativeText_("서로 다른 좋은 사진에서 3번 이상 확인된 얼굴입니다. 이름을 입력해 주세요.")
        elif review_kind == "ambiguous_identity_match":
            alert.setMessageText_("이 얼굴은 한 번 확인이 필요합니다")
            alert.setInformativeText_(f"가장 가까운 인물은 {suggestion_name or '선택 필요'}입니다.")
        else:
            alert.setMessageText_(f"{suggestion_name or '이 인물'}이 맞나요?")
            alert.setInformativeText_("직접 확인한 얼굴 표본을 기준으로 찾았습니다.")
        cluster_evidence = list(face.get("cluster_evidence") or [])
        accessory = NSView.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, 390.0, 210.0))
        image_refs = (
            [str(item.get("review_crop_ref") or "") for item in cluster_evidence[:3]]
            if review_kind == "promoted_new_person" and cluster_evidence
            else [str(face.get("review_crop_ref") or "")]
        )
        image_size = 112.0 if len(image_refs) > 1 else 180.0
        gap = 10.0
        total_width = len(image_refs) * image_size + max(0, len(image_refs) - 1) * gap
        start_x = max(0.0, (390.0 - total_width) / 2.0)
        for index, image_ref in enumerate(image_refs):
            image = NSImageView.alloc().initWithFrame_(
                NSMakeRect(start_x + index * (image_size + gap), 50.0, image_size, image_size)
            )
            image.setImageScaling_(NSImageScaleProportionallyUpOrDown)
            try:
                image.setImage_(cached_image(str(workspace.artifact_path(image_ref))))
            except (ValueError, FileNotFoundError):
                pass
            image.setAccessibilityLabel_(f"확인할 얼굴 {index + 1}")
            accessory.addSubview_(image)
        name_field = None
        if not suggestion_name:
            name_field = NSTextField.alloc().initWithFrame_(NSMakeRect(35.0, 0.0, 320.0, 28.0))
            name_field.setPlaceholderString_("이름 또는 가족 호칭")
            accessory.addSubview_(name_field)
        alert.setAccessoryView_(accessory)
        alert.addButtonWithTitle_("맞아요" if suggestion_name else "이름 등록")
        alert.addButtonWithTitle_("나중에")
        alert.addButtonWithTitle_("취소")
        response = alert.runModal()
        if response == NSAlertFirstButtonReturn:
            display_name = str(name_field.stringValue() or "").strip() if name_field is not None else ""
            if not suggestion_name and not display_name:
                self._alert("이름이 필요합니다", "등록할 이름 또는 가족 호칭을 입력해 주세요.")
                return
            assignment = {
                "face_observation_id": face["face_observation_id"],
                "target_kind": "existing" if suggestion_name else "new",
            }
            if suggestion_name:
                assignment["person_identity_id"] = face["suggested_person_identity_id"]
            else:
                assignment["display_name"] = display_name
            self._apply_exception_review(workspace, detail, assignments=[assignment], decisions=[])
        elif response == NSAlertFirstButtonReturn + 1:
            self._apply_exception_review(
                workspace,
                detail,
                assignments=[],
                decisions=[{"face_observation_id": face["face_observation_id"], "decision": "defer"}],
            )

    @objc.python_method
    def _apply_exception_review(
        self,
        workspace: PeopleWorkspaceService,
        detail: dict[str, Any],
        *,
        assignments: list[dict[str, Any]],
        decisions: list[dict[str, Any]],
    ) -> None:
        try:
            result = workspace.apply_asset_review(
                local_asset_id=str(detail["local_asset_id"]),
                expected_review_revision=int(detail["review_revision"]),
                assignments=assignments,
                face_decisions=decisions,
                device_fingerprint="mac-owner-app",
                idempotency_key=f"mac-exception-review-{uuid.uuid4()}",
                request_hash=uuid.uuid4().hex,
                actor="owner:mac-app",
            )
            refresh_all_story_location_projections(
                self._menu_controller._state_store.run_repository,
                identity_repository=self._identity_repository,
            )
            self._identity_repository.complete_story_refresh_outbox(
                str(result["decision_group_id"])
            )
        except (KeyError, OSError, sqlite3.Error, ValueError) as exc:
            self._alert("인물 정보를 저장하지 못했습니다", str(exc))
            return
        self._load_catalog()
        self._main_controller.rebuild()

    def deferAlias_(self, _sender) -> None:
        alias = self._selected_alias()
        if alias is not None:
            self._deferred_alias_ids.add(alias.alias_id)
        self._select_next_alias_or_close()

    def connectAlias_(self, _sender) -> None:
        alias = self._selected_alias()
        item = self._alias_identity_popup.selectedItem() if self._alias_identity_popup else None
        identity_id = str(item.representedObject() or "") if item is not None else ""
        detail, face = self._selected_alias_face_review(alias)
        if alias is None or not identity_id or self._identity_repository is None:
            self._alert("인물을 연결할 수 없습니다", "연결할 기존 인물을 선택해 주세요.")
            return
        if detail is None or face is None:
            self._alert("얼굴을 먼저 선택해 주세요", "인물 찾기를 실행한 뒤 이 이름에 해당하는 얼굴 crop을 선택해 주세요.")
            return
        try:
            result = PeopleWorkspaceService(
                self._identity_repository,
                run_repository=self._menu_controller._state_store.run_repository,
            ).apply_asset_review(
                local_asset_id=alias.local_asset_id,
                expected_review_revision=int(detail["review_revision"]),
                assignments=[
                    {
                        "face_observation_id": face["face_observation_id"],
                        "target_kind": "existing",
                        "person_identity_id": identity_id,
                        "alias_id": alias.alias_id,
                    }
                ],
                face_decisions=[],
                device_fingerprint="mac-owner-app",
                idempotency_key=f"mac-face-review-{uuid.uuid4()}",
                request_hash=uuid.uuid4().hex,
                actor="owner:mac-app",
            )
            refresh_all_story_location_projections(
                self._menu_controller._state_store.run_repository,
                identity_repository=self._identity_repository,
            )
            self._identity_repository.complete_story_refresh_outbox(
                str(result["decision_group_id"])
            )
        except (KeyError, OSError, sqlite3.Error, ValueError) as exc:
            self._alert("인물을 연결할 수 없습니다", str(exc))
            return
        self._advance_alias_after_change()

    def completeResolvedAliases_(self, _sender) -> None:
        """Finish provider hints whose exact faces were already owner-confirmed."""

        alias = self._selected_alias()
        detail, _face = self._selected_alias_face_review(alias)
        if alias is None or self._identity_repository is None or detail is None:
            self._alert(
                "이름 후보를 완료할 수 없습니다",
                "최신 인물 정보를 다시 불러온 뒤 시도해 주세요.",
            )
            return
        resolutions = list(detail.get("resolved_alias_assignments") or [])
        if (
            not resolutions
            or not bool(detail.get("can_complete_resolved_aliases"))
        ):
            self._alert(
                "이름 연결을 한 번 더 확인해 주세요",
                "이름이 한 얼굴에만 명확하게 대응될 때 일괄 완료할 수 있습니다.",
            )
            return
        assignments = [
            {
                "face_observation_id": str(item["face_observation_id"]),
                "target_kind": "existing",
                "person_identity_id": str(item["person_identity_id"]),
                "alias_id": str(item["alias_id"]),
            }
            for item in resolutions
        ]
        try:
            result = PeopleWorkspaceService(
                self._identity_repository,
                run_repository=self._menu_controller._state_store.run_repository,
            ).apply_asset_review(
                local_asset_id=alias.local_asset_id,
                expected_review_revision=int(detail["review_revision"]),
                assignments=assignments,
                face_decisions=[],
                device_fingerprint="mac-owner-app",
                idempotency_key=f"mac-resolved-aliases-{uuid.uuid4()}",
                request_hash=uuid.uuid4().hex,
                actor="owner:mac-app",
            )
            refresh_all_story_location_projections(
                self._menu_controller._state_store.run_repository,
                identity_repository=self._identity_repository,
            )
            self._identity_repository.complete_story_refresh_outbox(
                str(result["decision_group_id"])
            )
        except (KeyError, OSError, sqlite3.Error, ValueError) as exc:
            self._alert("이름 후보를 완료할 수 없습니다", str(exc))
            return
        self._advance_alias_after_change()

    def createPersonFromAlias_(self, _sender) -> None:
        alias = self._selected_alias()
        detail, face = self._selected_alias_face_review(alias)
        if alias is None or self._identity_repository is None:
            return
        if detail is None or face is None:
            self._alert("얼굴을 먼저 선택해 주세요", "인물 찾기를 실행한 뒤 이 이름에 해당하는 얼굴 crop을 선택해 주세요.")
            return
        field = NSTextField.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, 320.0, 28.0))
        field.setStringValue_(alias.private_display_label)
        field.setPlaceholderString_("인물 이름 또는 가족 호칭")
        alert = NSAlert.alloc().init()
        alert.setMessageText_("새 인물로 등록")
        alert.setInformativeText_("사진을 확인한 뒤 사용할 이름을 입력해 주세요. 개인 Story 이름 표시는 켜지고 가족 공유는 별도 설정입니다.")
        alert.setAccessoryView_(field)
        alert.addButtonWithTitle_("등록")
        alert.addButtonWithTitle_("취소")
        if alert.runModal() != NSAlertFirstButtonReturn:
            return
        name = str(field.stringValue() or "").strip()
        if not name:
            self._alert("이름이 필요합니다", "새 인물에 사용할 이름을 입력해 주세요.")
            return
        try:
            result = PeopleWorkspaceService(
                self._identity_repository,
                run_repository=self._menu_controller._state_store.run_repository,
            ).apply_asset_review(
                local_asset_id=alias.local_asset_id,
                expected_review_revision=int(detail["review_revision"]),
                assignments=[
                    {
                        "face_observation_id": face["face_observation_id"],
                        "target_kind": "new",
                        "display_name": name,
                        "alias_id": alias.alias_id,
                    }
                ],
                face_decisions=[],
                device_fingerprint="mac-owner-app",
                idempotency_key=f"mac-face-review-{uuid.uuid4()}",
                request_hash=uuid.uuid4().hex,
                actor="owner:mac-app",
            )
            refresh_all_story_location_projections(
                self._menu_controller._state_store.run_repository,
                identity_repository=self._identity_repository,
            )
            self._identity_repository.complete_story_refresh_outbox(
                str(result["decision_group_id"])
            )
        except (KeyError, OSError, sqlite3.Error, ValueError) as exc:
            self._alert("새 인물을 등록할 수 없습니다", str(exc))
            return
        self._advance_alias_after_change()

    def selectAliasFace_(self, sender) -> None:
        self._selected_alias_face_id = str(sender.identifier() or "")
        self._main_controller.rebuild()

    def ignoreUnknownAliasFace_(self, _sender) -> None:
        alias = self._selected_alias()
        detail, face = self._selected_alias_face_review(alias)
        if alias is None or self._identity_repository is None or detail is None or face is None:
            self._alert(
                "얼굴을 먼저 선택해 주세요",
                "실제 얼굴이지만 내가 아는 사람이 아닌 경우에만 무시할 얼굴 crop을 선택해 주세요.",
            )
            return
        alert = NSAlert.alloc().init()
        alert.setMessageText_("이 얼굴을 모르는 사람으로 무시할까요?")
        alert.setInformativeText_(
            "얼굴로 검출된 사실은 유지하지만 인물 목록과 Story에서는 제외합니다. "
            "원본 사진과 사진 속 다른 얼굴은 변경하지 않습니다."
        )
        alert.addButtonWithTitle_("모르는 사람 · 무시")
        alert.addButtonWithTitle_("취소")
        if alert.runModal() != NSAlertFirstButtonReturn:
            return
        try:
            result = PeopleWorkspaceService(
                self._identity_repository,
                run_repository=self._menu_controller._state_store.run_repository,
            ).apply_asset_review(
                local_asset_id=alias.local_asset_id,
                expected_review_revision=int(detail["review_revision"]),
                assignments=[],
                face_decisions=[
                    {
                        "face_observation_id": face["face_observation_id"],
                        "decision": "ignore_unknown",
                    }
                ],
                device_fingerprint="mac-owner-app",
                idempotency_key=f"mac-face-review-{uuid.uuid4()}",
                request_hash=uuid.uuid4().hex,
                actor="owner:mac-app",
            )
            refresh_all_story_location_projections(
                self._menu_controller._state_store.run_repository,
                identity_repository=self._identity_repository,
            )
            self._identity_repository.complete_story_refresh_outbox(
                str(result["decision_group_id"])
            )
        except (KeyError, OSError, sqlite3.Error, ValueError) as exc:
            self._alert("모르는 사람으로 무시할 수 없습니다", str(exc))
            return
        self._selected_alias_face_id = ""
        self._load_catalog()
        self._main_controller.rebuild()

    def rejectAlias_(self, _sender) -> None:
        alias = self._selected_alias()
        if alias is None or self._identity_repository is None:
            return
        alert = NSAlert.alloc().init()
        alert.setMessageText_("이 이름 후보를 제외할까요?")
        alert.setInformativeText_("사진이나 기존 인물 정보는 삭제하지 않습니다.")
        alert.addButtonWithTitle_("후보 제외")
        alert.addButtonWithTitle_("취소")
        if alert.runModal() != NSAlertFirstButtonReturn:
            return
        try:
            self._identity_repository.review_provider_person_alias(
                alias.alias_id,
                decision="rejected",
                actor="owner:mac-app",
                request_id="mac-provider-alias-reject",
            )
        except (KeyError, OSError, sqlite3.Error, ValueError) as exc:
            self._alert("후보를 제외할 수 없습니다", str(exc))
            return
        self._advance_alias_after_change()

    def indexPeople_(self, _sender) -> None:
        if self._index_running or self._identity_repository is None:
            return
        self._face_runtime = face_runtime_status()
        if self._face_runtime.status != "ready":
            self._alert("인물 모델을 준비하지 못했습니다", ", ".join(self._face_runtime.missing_components))
            return
        self._index_running = True
        self._index_message = "현재 추천 사진 최대 50장에서 인물을 찾는 중입니다…"
        self._main_controller.rebuild()
        Thread(target=self._run_people_index, daemon=True).start()

    @objc.python_method
    def _run_people_index(self) -> None:
        try:
            result = PersonIndexingService(
                self._menu_controller._state_store.run_repository,
                self._identity_repository,
            ).index_recommendation_assets(limit=50)
            message = (
                f"인물 찾기 완료 · 사진 {result.asset_count}장 · 얼굴 {result.detected_face_count}개 · "
                f"새 후보 {result.candidate_count}명 · 확인 {result.review_count}건"
            )
        except Exception as exc:
            message = f"인물 찾기 실패 · {type(exc).__name__}"
        self.performSelectorOnMainThread_withObject_waitUntilDone_(
            "finishPeopleIndex:", message, False
        )

    def finishPeopleIndex_(self, message) -> None:
        self._index_running = False
        self._index_message = str(message or "")
        self._load_catalog()
        self._main_controller.rebuild()

    def reindexAliasPhoto_(self, _sender) -> None:
        alias = self._selected_alias()
        if alias is None or self._identity_repository is None or self._index_running:
            return
        self._face_runtime = face_runtime_status()
        if self._face_runtime.status != "ready":
            self._alert(
                "인물 모델을 준비하지 못했습니다",
                ", ".join(self._face_runtime.missing_components),
            )
            return
        self._index_running = True
        self._alias_indexing_asset_id = alias.local_asset_id
        self._alias_index_message = "이 사진에서 얼굴을 다시 찾는 중입니다…"
        self._selected_alias_face_id = ""
        self._main_controller.rebuild()
        Thread(
            target=self._run_alias_photo_index,
            args=(alias.local_asset_id,),
            daemon=True,
        ).start()

    @objc.python_method
    def _run_alias_photo_index(self, local_asset_id: str) -> None:
        try:
            result = PersonIndexingService(
                self._menu_controller._state_store.run_repository,
                self._identity_repository,
            ).index_assets(
                local_asset_ids=(local_asset_id,),
                scope_kind="provider_alias_photo_retry",
            )
            if result.failure_count:
                message = "얼굴을 다시 찾지 못했습니다. 원본 사진 접근 상태를 확인해 주세요."
            else:
                message = f"다시 찾기 완료 · 얼굴 {result.detected_face_count}개"
        except Exception as exc:
            message = f"다시 찾기 실패 · {type(exc).__name__}: {exc}"
        self.performSelectorOnMainThread_withObject_waitUntilDone_(
            "finishAliasPhotoIndex:", message, False
        )

    def finishAliasPhotoIndex_(self, message) -> None:
        self._index_running = False
        self._alias_indexing_asset_id = ""
        self._alias_index_message = str(message or "")
        self._selected_alias_face_id = ""
        self._load_catalog()
        self._main_controller.rebuild()

    def selectIdentity_(self, sender) -> None:
        self.selectIdentityId_(str(sender.identifier() or ""))

    @objc.python_method
    def selectIdentityId_(self, identity_id: str) -> None:
        if self._catalog.identity(identity_id) is None:
            return
        if identity_id != self._selected_identity_id:
            # Capture the current editor while it still belongs to the source
            # identity. Otherwise the resize-style rebuild can attach that
            # draft to the newly selected row.
            self._freeze_name_state()
            self._name_restore_pending_identity_id = ""
            self._selected_face_ids.clear()
            self._focused_face_selection_id = ""
            self._gallery_scroll_origin = None
        self._selected_identity_id = identity_id
        self._selected_alias_id = ""
        self._focused_identity_id = identity_id
        self._view_mode = "detail"
        self._main_controller.rebuild()

    def restoreIdentityRowFocus_(self, row) -> None:
        if row is None or str(row.identifier() or "") != self._focused_identity_id:
            return
        window = self._main_controller.window()
        if window is not None:
            window.makeFirstResponder_(row)

    def restoreFaceSelectionFocus_(self, selection) -> None:
        if selection is None or str(selection.identifier() or "") != self._focused_face_selection_id:
            return
        window = self._main_controller.window()
        if window is not None:
            window.makeFirstResponder_(selection)

    def toggleFaceSelection_(self, sender) -> None:
        face_id = str(sender.identifier() or "")
        identity = self._selected_identity()
        if identity is None or face_id not in {face.face_id for face in identity.faces}:
            return
        if sender.state() == NSControlStateValueOn:
            self._selected_face_ids.add(face_id)
        else:
            self._selected_face_ids.discard(face_id)
        self._focused_face_selection_id = face_id
        self._main_controller.rebuild()

    def createSelectedIdentity_(self, _sender) -> None:
        identity = self._selected_identity()
        if identity is None:
            return
        payload = self._face_drag_payload(identity, self._selected_face_ids)
        if not self.acceptNewIdentityDrop_(payload):
            self._alert("새 인물 그룹을 만들 수 없습니다", "현재 묶음의 일부 얼굴을 선택해 주세요.")

    def moveSelectedFaces_(self, _sender) -> None:
        identity = self._selected_identity()
        item = self._move_popup.selectedItem() if self._move_popup is not None else None
        target_id = str(item.representedObject() or "") if item is not None else ""
        if identity is None or not target_id:
            self._alert("얼굴을 이동할 수 없습니다", "이동할 인물 그룹을 먼저 선택해 주세요.")
            return
        payload = self._face_drag_payload(identity, self._selected_face_ids)
        if not self.acceptPersonDrop_payload_(target_id, payload):
            self._alert("얼굴을 이동할 수 없습니다", "선택한 얼굴과 대상 인물 그룹을 다시 확인해 주세요.")

    def mergeSelectedIdentity_(self, _sender) -> None:
        identity = self._selected_identity()
        item = self._move_popup.selectedItem() if self._move_popup is not None else None
        target_id = str(item.representedObject() or "") if item is not None else ""
        target = self._catalog.identity(target_id)
        if identity is None or target is None:
            self._alert("인물 그룹을 합칠 수 없습니다", "합칠 대상 인물 그룹을 먼저 선택해 주세요.")
            return
        source_label = self._identity_labels.get(identity.identity_id, identity.display_name)
        target_label = self._identity_labels.get(target.identity_id, target.display_name)
        alert = NSAlert.alloc().init()
        alert.setMessageText_(f"{source_label} 그룹을 {target_label} 그룹에 합칠까요?")
        alert.setInformativeText_(
            f"{source_label}의 얼굴 {len(identity.faces)}개가 모두 이동하고 현재 그룹은 사라집니다. "
            "원본 사진은 변경하지 않으며 실행 취소할 수 있습니다."
        )
        alert.addButtonWithTitle_("그룹 전체 병합")
        alert.addButtonWithTitle_("취소")
        if alert.runModal() != NSAlertFirstButtonReturn:
            return
        payload = {
            "version": DRAG_PAYLOAD_VERSION,
            "source_identity_id": identity.identity_id,
            "face_ids": [],
            "drag_type": IDENTITY_DRAG_TYPE,
        }
        if not self.acceptPersonDrop_payload_(target_id, payload):
            self._alert("인물 그룹을 합칠 수 없습니다", "현재 그룹과 대상 그룹을 다시 확인해 주세요.")

    def selectMoveTarget_(self, sender) -> None:
        item = sender.selectedItem() if sender is not None else None
        self._move_target_identity_id = str(item.representedObject() or "") if item is not None else ""
        self._main_controller.rebuild()

    def restoreNameEditorState_(self, field) -> None:
        """Restore edit focus after AppKit finishes the resize-driven view rebuild."""

        if field is None or field is not self._name_field or self._name_selection_range is None:
            return
        window = self._main_controller.window()
        if window is None:
            return
        window.makeFirstResponder_(field)
        editor = field.currentEditor()
        if editor is not None:
            editor.setSelectedRange_(NSMakeRange(*self._name_selection_range))
            self._name_restore_pending_identity_id = ""

    def excludeSelectedFaces_(self, _sender) -> None:
        identity = self._selected_identity()
        face_ids = sorted(self._selected_face_ids)
        if identity is None or not face_ids:
            return
        snapshot = self._registry.snapshot()
        self._freeze_name_state()
        try:
            count = self._registry.exclude_faces(identity, face_ids)
        except (OSError, ValueError) as exc:
            self._alert("얼굴을 제외할 수 없습니다", str(exc))
            return
        self._remember_successful_change(snapshot, f"얼굴 아님으로 표시한 항목 {count}개를 인물 관리에서 숨겼습니다.")
        self._selected_face_ids.clear()
        self._load_catalog()
        self._main_controller.rebuild()

    def saveName_(self, _sender) -> None:
        identity = self._selected_identity()
        if identity is None or self._name_field is None:
            return
        name = str(self._name_field.stringValue() or "").strip()
        if identity.stable_identity_id and self._identity_repository is not None:
            try:
                before = self._identity_repository.get_identity(identity.stable_identity_id)
                self._identity_repository.set_name(
                    identity.stable_identity_id,
                    name,
                    name_status="user_confirmed" if name else "unlabeled",
                    expected_identity_revision=before.identity_revision,
                    actor="owner:mac-app",
                    request_id="mac-people-name",
                )
            except (KeyError, OSError, sqlite3.Error, ValueError) as exc:
                self._alert("인물 이름을 저장할 수 없습니다", str(exc))
                return
            self._stable_name_undo = (
                identity.stable_identity_id,
                before.display_name,
                before.name_status,
            )
            self._undo_snapshot = None
            self._undo_message = "인물 이름을 변경했습니다.  ⌘Z로 실행 취소할 수 있습니다."
            self._name_draft_identity_id = ""
            self._name_draft = ""
            self._name_selection_range = None
            self._name_restore_pending_identity_id = ""
            self._load_catalog()
            self._main_controller.rebuild()
            return
        snapshot = self._registry.snapshot()
        try:
            self._selected_identity_id = self._registry.assign_name(identity, name)
        except (OSError, ValueError) as exc:
            self._alert("인물 이름을 저장할 수 없습니다", str(exc))
            return
        self._remember_successful_change(snapshot, "인물 이름을 변경했습니다.")
        self._name_draft_identity_id = ""
        self._name_draft = ""
        self._name_selection_range = None
        self._name_restore_pending_identity_id = ""
        self._load_catalog()
        self._main_controller.rebuild()

    def toggleStableConsent_(self, sender) -> None:
        identity = self._selected_identity()
        audience = str(sender.identifier() or "") if sender is not None else ""
        if (
            identity is None
            or not identity.stable_identity_id
            or self._identity_repository is None
            or audience not in {"owner", "family_share"}
        ):
            return
        allowed = sender.state() == NSControlStateValueOn
        try:
            current = self._identity_repository.get_identity(identity.stable_identity_id)
            if self._identity_repository.current_consent(
                identity.stable_identity_id,
                audience,
            ) == allowed:
                return
            self._identity_repository.set_story_name_consent(
                identity.stable_identity_id,
                audience,
                allowed,
                expected_identity_revision=current.identity_revision,
                actor="owner:mac-app",
                request_id=f"mac-people-consent-{audience}",
            )
        except (KeyError, OSError, sqlite3.Error, ValueError) as exc:
            self._alert("Story 이름 사용 설정을 저장할 수 없습니다", str(exc))
            self._main_controller.rebuild()
            return
        self._load_catalog()
        self._main_controller.rebuild()

    def toggleIdentityAuto_(self, sender) -> None:
        identity = self._selected_identity()
        if identity is None or not identity.stable_identity_id or self._identity_repository is None:
            return
        profile = self._identity_repository.latest_identity_automation_profile(
            identity.stable_identity_id
        )
        if profile is None:
            return
        try:
            self._identity_repository.set_identity_auto_enabled(
                identity.stable_identity_id,
                enabled=sender.state() == NSControlStateValueOn,
                expected_profile_revision=int(profile["profile_revision"]),
                actor="owner:mac-app",
                request_id="mac-people-auto-toggle",
            )
        except (KeyError, OSError, sqlite3.Error, ValueError) as exc:
            self._alert("자동 인식 설정을 저장할 수 없습니다", str(exc))
        self._load_catalog()
        self._main_controller.rebuild()

    @objc.python_method
    def _add_identity_auto_toggle(
        self,
        card: Any,
        y: float,
        width: float,
        identity: PersonIdentity,
        *,
        x: float = 20.0,
        control_width: float | None = None,
    ) -> None:
        if self._identity_repository is None or not identity.stable_identity_id:
            return
        try:
            profile = self._identity_repository.latest_identity_automation_profile(
                identity.stable_identity_id
            )
        except (KeyError, OSError, sqlite3.Error, ValueError):
            profile = None
        if profile is None:
            return
        maturity_labels = {
            "learning": "학습 중",
            "review_ready": "확인 가능",
            "auto_ready": "안정됨",
        }
        title = (
            "자동 인식 · "
            + maturity_labels.get(str(profile["maturity"]), "학습 중")
            + f" · 직접 확인 {int(profile['owner_confirmed_anchor_count'])}장"
        )
        toggle = NSButton.alloc().initWithFrame_(
            NSMakeRect(x, y, control_width if control_width is not None else width - 40.0, 28.0)
        )
        toggle.setButtonType_(NSButtonTypeSwitch)
        toggle.setTitle_(title)
        toggle.setTarget_(self)
        toggle.setAction_("toggleIdentityAuto:")
        toggle.setState_(NSControlStateValueOn if bool(profile["auto_enabled"]) else 0)
        toggle.setEnabled_(not bool(profile["suspended"]))
        toggle.setAccessibilityLabel_(title)
        card.addSubview_(toggle)

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
        snapshot = self._registry.snapshot()
        self._freeze_name_state()
        try:
            self._registry.clear_manual_changes(identity)
        except (OSError, ValueError) as exc:
            self._alert("자동 그룹으로 되돌릴 수 없습니다", str(exc))
            return
        self._remember_successful_change(snapshot, "자동 그룹으로 되돌렸습니다.")
        self._selected_identity_id = ""
        self._load_catalog()
        self._main_controller.rebuild()

    def excludeFace_(self, sender) -> None:
        identity = self._selected_identity()
        face_id = str(sender.identifier() or "")
        if identity is None or not face_id:
            return
        snapshot = self._registry.snapshot()
        self._freeze_name_state()
        try:
            self._registry.exclude_faces(identity, [face_id])
        except (OSError, ValueError) as exc:
            self._alert("얼굴을 제외할 수 없습니다", str(exc))
            return
        self._remember_successful_change(snapshot, "얼굴 아님으로 표시해 인물 관리에서 숨겼습니다.")
        self._load_catalog()
        self._main_controller.rebuild()

    def restoreExcludedFaces_(self, _sender) -> None:
        if not self._catalog.excluded_face_count:
            return
        snapshot = self._registry.snapshot()
        try:
            count = self._registry.restore_excluded_faces()
        except OSError as exc:
            self._alert("제외한 얼굴을 복원할 수 없습니다", str(exc))
            return
        self._remember_successful_change(snapshot, f"제외한 얼굴 {count}개를 모두 복원했습니다.")
        self._load_catalog()
        self._main_controller.rebuild()

    def undoLastChange_(self, _sender) -> None:
        if self._stable_name_undo is not None and self._identity_repository is not None:
            identity_id, previous_name, previous_status = self._stable_name_undo
            try:
                current = self._identity_repository.get_identity(identity_id)
                self._identity_repository.set_name(
                    identity_id,
                    previous_name,
                    name_status=previous_status,
                    expected_identity_revision=current.identity_revision,
                    actor="owner:mac-app",
                    request_id="mac-people-name-undo",
                )
            except (KeyError, OSError, sqlite3.Error, ValueError) as exc:
                self._alert("변경을 되돌릴 수 없습니다", str(exc))
                return
            self._stable_name_undo = None
            self._undo_message = ""
            self._load_catalog()
            self._main_controller.rebuild()
            return
        if self._undo_snapshot is None:
            return
        try:
            self._registry.restore_snapshot(self._undo_snapshot)
        except (OSError, ValueError) as exc:
            self._alert("변경을 되돌릴 수 없습니다", str(exc))
            return
        self._undo_snapshot = None
        self._stable_name_undo = None
        self._undo_message = ""
        self._load_catalog()
        self._main_controller.rebuild()

    @objc.python_method
    def canAcceptPersonDrop_payload_(self, target_identity_id: str, payload: dict[str, Any]) -> bool:
        if payload.get("version") != DRAG_PAYLOAD_VERSION:
            return False
        source = self._catalog.identity(str(payload.get("source_identity_id") or ""))
        target = self._catalog.identity(target_identity_id)
        if (
            source is None
            or target is None
            or source.identity_id == target.identity_id
            or not source.faces
            or not target.faces
        ):
            return False
        drag_type = str(payload.get("drag_type") or "")
        if drag_type == IDENTITY_DRAG_TYPE:
            return bool(source.faces)
        if drag_type != FACE_DRAG_TYPE:
            return False
        face_ids = payload.get("face_ids") if isinstance(payload.get("face_ids"), list) else []
        requested = [str(face_id) for face_id in face_ids if str(face_id)]
        valid = {face.face_id for face in source.faces}
        return bool(requested) and len(requested) == len(set(requested)) and set(requested) <= valid

    @objc.python_method
    def canAcceptNewIdentityDrop_(self, payload: dict[str, Any]) -> bool:
        if payload.get("version") != DRAG_PAYLOAD_VERSION:
            return False
        source = self._catalog.identity(str(payload.get("source_identity_id") or ""))
        if source is None or str(payload.get("drag_type") or "") != FACE_DRAG_TYPE:
            return False
        face_ids = payload.get("face_ids") if isinstance(payload.get("face_ids"), list) else []
        requested = [str(face_id) for face_id in face_ids if str(face_id)]
        valid = {face.face_id for face in source.faces}
        return (
            bool(requested)
            and len(requested) == len(set(requested))
            and set(requested) <= valid
            and len(requested) < len(source.faces)
        )

    @objc.python_method
    def acceptPersonDrop_payload_(self, target_identity_id: str, payload: dict[str, Any]) -> bool:
        if not self.canAcceptPersonDrop_payload_(target_identity_id, payload):
            return False
        source = self._catalog.identity(str(payload.get("source_identity_id") or ""))
        target = self._catalog.identity(target_identity_id)
        if source is None or target is None:
            return False
        face_ids = payload.get("face_ids") if isinstance(payload.get("face_ids"), list) else []
        if payload.get("drag_type", "").endswith("identity"):
            face_ids = [face.face_id for face in source.faces]
        snapshot = self._registry.snapshot()
        self._freeze_name_state()
        try:
            self._selected_identity_id = self._registry.move_faces(source, target, face_ids)
        except (OSError, ValueError) as exc:
            self._alert("얼굴을 이동할 수 없습니다", str(exc))
            return False
        target_name = self._identity_labels.get(target.identity_id, target.display_name)
        self._remember_successful_change(snapshot, f"얼굴 {len(face_ids)}개를 {target_name} 그룹으로 이동했습니다.")
        self._selected_face_ids.clear()
        self._load_catalog()
        self._main_controller.rebuild()
        return True

    @objc.python_method
    def acceptNewIdentityDrop_(self, payload: dict[str, Any]) -> bool:
        if not self.canAcceptNewIdentityDrop_(payload):
            return False
        source = self._catalog.identity(str(payload.get("source_identity_id") or ""))
        if source is None:
            return False
        face_ids = payload.get("face_ids") if isinstance(payload.get("face_ids"), list) else []
        if payload.get("drag_type", "").endswith("identity"):
            face_ids = [face.face_id for face in source.faces]
        snapshot = self._registry.snapshot()
        self._freeze_name_state()
        try:
            self._selected_identity_id = self._registry.create_identity_from_faces(source, face_ids)
        except (OSError, ValueError) as exc:
            self._alert("새 인물 그룹을 만들 수 없습니다", str(exc))
            return False
        self._remember_successful_change(snapshot, f"선택한 얼굴 {len(face_ids)}개로 새 인물 그룹을 만들었습니다.")
        self._selected_face_ids.clear()
        self._load_catalog()
        self._main_controller.rebuild()
        return True

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
        catalog = build_people_catalog(sources, registry=self._registry)
        if self._identity_repository is not None:
            try:
                catalog = merge_stable_people_catalog(
                    catalog,
                    repository=self._identity_repository,
                    registry=self._registry,
                )
                catalog = replace(
                    catalog,
                    identities=tuple(
                        identity
                        for identity in catalog.identities
                        if identity.stable_identity_status != "candidate"
                        or len(identity.faces) >= 3
                    ),
                )
            except (OSError, sqlite3.Error, ValueError):
                # Keep the face-backed legacy catalog available if the durable
                # repository is damaged or temporarily locked.
                pass
        self._catalog = catalog
        self._face_runtime = face_runtime_status()
        if self._identity_repository is not None:
            try:
                self._aliases = self._identity_repository.list_provider_person_aliases()
                self._people_dashboard = PeopleWorkspaceService(
                    self._identity_repository,
                    run_repository=getattr(
                        self._menu_controller._state_store, "run_repository", None
                    ),
                ).dashboard()
            except (OSError, sqlite3.Error, ValueError):
                self._aliases = ()
                self._people_dashboard = {}
        else:
            self._aliases = ()
            self._people_dashboard = {}
        if self._selected_alias_id and not any(
            alias.alias_id == self._selected_alias_id for alias in self._aliases
        ):
            self._selected_alias_id = ""
        unnamed_index = 0
        self._identity_labels = {}
        for identity in self._catalog.identities:
            if identity.name.strip():
                label = identity.name.strip()
            else:
                unnamed_index += 1
                label = f"이름 없는 인물 {unnamed_index}"
            self._identity_labels[identity.identity_id] = label
        if self._catalog.identity(self._selected_identity_id) is None:
            self._selected_identity_id = self._catalog.identities[0].identity_id if self._catalog.identities else ""
            self._selected_face_ids.clear()
        selected = self._catalog.identity(self._selected_identity_id)
        valid_face_ids = {face.face_id for face in selected.faces} if selected is not None else set()
        self._selected_face_ids.intersection_update(valid_face_ids)

    @objc.python_method
    def _selected_identity(self) -> PersonIdentity | None:
        return self._catalog.identity(self._selected_identity_id)

    @objc.python_method
    def _managed_identities(self) -> tuple[PersonIdentity, ...]:
        """Keep the home screen focused on people the owner actually manages."""

        dashboard_order = {
            str(item.get("person_identity_id") or ""): index
            for index, item in enumerate(self._people_dashboard.get("people") or ())
        }
        managed = [
            identity
            for identity in self._catalog.identities
            if identity.stable_identity_status == "user_confirmed"
            or (not identity.stable_identity_id and bool(identity.name.strip()))
            or identity.is_manual
        ]
        managed.sort(
            key=lambda identity: (
                dashboard_order.get(identity.stable_identity_id, 100_000),
                self._identity_labels.get(identity.identity_id, identity.display_name),
                identity.identity_id,
            )
        )
        return tuple(managed)

    @objc.python_method
    def _dashboard_person(self, identity: PersonIdentity) -> dict[str, Any]:
        if not identity.stable_identity_id:
            return {}
        return next(
            (
                dict(item)
                for item in self._people_dashboard.get("people") or ()
                if str(item.get("person_identity_id") or "") == identity.stable_identity_id
            ),
            {},
        )

    @objc.python_method
    def _identity_metrics(self, identity: PersonIdentity) -> tuple[int, int, str, bool]:
        person = self._dashboard_person(identity)
        profile = dict(person.get("automation_profile") or {})
        photo_count = int(person.get("linked_photo_count") or len(identity.faces))
        anchor_count = int(profile.get("owner_confirmed_anchor_count") or len(identity.faces))
        maturity = str(profile.get("maturity") or "learning")
        enabled = bool(profile.get("auto_enabled", False)) and not bool(profile.get("suspended", False))
        return photo_count, anchor_count, maturity, enabled

    @objc.python_method
    def _representative_crop_path(self, identity: PersonIdentity) -> str:
        # Stable identities use the repository's quality-ranked representative.
        # The in-memory face list is job-scoped and may still start with an old,
        # blurred, or profile crop.
        person = self._dashboard_person(identity)
        artifact_ref = str(person.get("representative_face_ref") or "")
        if artifact_ref and self._identity_repository is not None:
            try:
                return str(
                    PeopleWorkspaceService(
                        self._identity_repository,
                        run_repository=self._menu_controller._state_store.run_repository,
                    ).artifact_path(artifact_ref)
                )
            except (FileNotFoundError, OSError, ValueError):
                pass
        representative = identity.representative_face
        if representative is not None and representative.crop_path:
            return representative.crop_path
        return ""

    @objc.python_method
    def _people_card_grid(
        self,
        parent: Any,
        x: float,
        y: float,
        width: float,
        height: float,
        identities: tuple[PersonIdentity, ...],
    ) -> None:
        container = self._card(parent, x, y, width, height, selected=False)
        if not identities:
            image = NSImage.imageWithSystemSymbolName_accessibilityDescription_(
                "person.2.crop.square.stack", "등록된 인물 없음"
            )
            image_view = NSImageView.alloc().initWithFrame_(
                NSMakeRect((width - 64.0) / 2.0, max(86.0, height / 2.0 + 12.0), 64.0, 64.0)
            )
            image_view.setImage_(image)
            if hasattr(image_view, "setContentTintColor_"):
                image_view.setContentTintColor_(NSColor.tertiaryLabelColor())
            container.addSubview_(image_view)
            self._label(container, 24.0, max(54.0, height / 2.0 - 18.0), width - 48.0, 28.0, "등록된 인물이 없습니다", 15.0, True).setAlignment_(2)
            self._label(container, 24.0, max(30.0, height / 2.0 - 42.0), width - 48.0, 20.0, "사진을 분석하면 자주 등장한 사람만 여기에 표시됩니다.", 9.5, False, secondary=True).setAlignment_(2)
            return

        scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(12.0, 12.0, width - 24.0, height - 24.0))
        scroll.setHasVerticalScroller_(True)
        scroll.setDrawsBackground_(False)
        self._identity_scroll_view = scroll
        document_width = max(240.0, width - 42.0)
        gap = 16.0
        minimum_card_width = 250.0
        columns = max(1, min(4, int((document_width + gap) // (minimum_card_width + gap))))
        card_width = (document_width - ((columns - 1) * gap)) / columns
        card_height = 154.0
        rows = (len(identities) + columns - 1) // columns
        content_height = rows * (card_height + gap) + gap
        document_height = max(float(scroll.contentSize().height), content_height, 1.0)
        document = NSView.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, document_width, document_height))
        scroll.setDocumentView_(document)
        for index, identity in enumerate(identities):
            column, row_index = index % columns, index // columns
            card_x = column * (card_width + gap)
            card_y = document_height - ((row_index + 1) * (card_height + gap))
            card = IdentityDropRowView.alloc().initWithIdentityId_controller_(identity.identity_id, self)
            card.setFrame_(NSMakeRect(card_x, card_y, card_width, card_height))
            self._style_card(card, selected=False)
            label = self._identity_labels.get(identity.identity_id, identity.display_name)
            photo_count, anchor_count, maturity, auto_enabled = self._identity_metrics(identity)
            maturity_label = {"auto_ready": "안정됨", "review_ready": "확인 가능", "learning": "학습 중"}.get(maturity, "학습 중")
            card.setAccessibilityLabel_(
                f"{label}, 사진 {photo_count}장, 자동 인식 {maturity_label}, 상세 보기"
            )
            document.addSubview_(card)

            crop_path = self._representative_crop_path(identity)
            portrait = NSImageView.alloc().initWithFrame_(NSMakeRect(16.0, 46.0, 88.0, 92.0))
            portrait.setImageScaling_(NSImageScaleProportionallyUpOrDown)
            portrait.setWantsLayer_(True)
            portrait.layer().setCornerRadius_(10.0)
            portrait.layer().setMasksToBounds_(True)
            if crop_path:
                portrait.setImage_(cached_image(crop_path))
            else:
                portrait.setImage_(NSImage.imageWithSystemSymbolName_accessibilityDescription_("person.crop.square", f"{label} 대표 얼굴 준비 중"))
                if hasattr(portrait, "setContentTintColor_"):
                    portrait.setContentTintColor_(NSColor.tertiaryLabelColor())
            portrait.setAccessibilityLabel_(f"{label} 대표 얼굴")
            card.addSubview_(portrait)

            text_x = 120.0
            text_width = max(90.0, card_width - text_x - 16.0)
            # IdentityDropRowView is an NSButton and therefore uses a flipped
            # content coordinate system. Keep the most important value first.
            self._label(card, text_x, 22.0, text_width, 24.0, label, 15.0, True)
            self._label(card, text_x, 52.0, text_width, 20.0, f"사진 {photo_count}장 · 직접 확인 {anchor_count}장", 9.2, False, secondary=True)
            auto_text = f"자동 인식 · {maturity_label}" if auto_enabled else "자동 인식 꺼짐"
            self._label(card, text_x, 78.0, text_width, 20.0, auto_text, 9.4, True, accent=auto_enabled)
            self._label(card, text_x, 108.0, text_width, 20.0, "설정 보기  →", 9.4, True, secondary=True)
        container.addSubview_(scroll)
        if self._identity_scroll_origin is not None:
            scroll.contentView().scrollToPoint_(NSMakePoint(*self._identity_scroll_origin))
            scroll.reflectScrolledClipView_(scroll.contentView())

    @objc.python_method
    def _selected_alias(self):
        return next(
            (alias for alias in self._aliases if alias.alias_id == self._selected_alias_id),
            None,
        )

    @objc.python_method
    def _selected_alias_face_review(self, alias: Any) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        if alias is None or self._identity_repository is None:
            return None, None
        try:
            detail = PeopleWorkspaceService(
                self._identity_repository,
                run_repository=self._menu_controller._state_store.run_repository,
            ).provider_alias_review_detail(alias.local_asset_id)
        except (KeyError, OSError, sqlite3.Error, ValueError):
            return None, None
        faces = list(detail.get("faces") or [])
        if not faces:
            return detail, None
        face = next(
            (
                item
                for item in faces
                if str(item.get("face_observation_id") or "") == self._selected_alias_face_id
            ),
            None,
        )
        if face is None:
            matching_faces = []
            for item in faces:
                if provider_alias_matches_identity_name(
                    str(alias.private_display_label or ""),
                    str(item.get("suggested_display_name") or ""),
                ):
                    matching_faces.append(item)
            if len(matching_faces) == 1:
                face = matching_faces[0]
            elif len(faces) == 1:
                face = faces[0]
            if face is not None:
                self._selected_alias_face_id = str(
                    face.get("face_observation_id") or ""
                )
        return detail, face

    @objc.python_method
    def _advance_alias_after_change(self) -> None:
        self._selected_alias_face_id = ""
        self._load_catalog()
        self._select_next_alias_or_close(rebuild=False)
        self._main_controller.rebuild()

    @objc.python_method
    def _select_next_alias_or_close(self, *, rebuild: bool = True) -> None:
        next_alias = next(
            (
                alias
                for alias in self._aliases
                if alias.alias_id not in self._deferred_alias_ids
            ),
            None,
        )
        self._selected_alias_id = next_alias.alias_id if next_alias is not None else ""
        self._selected_alias_face_id = ""
        if not self._selected_alias_id and self._catalog.identities:
            self._selected_identity_id = self._catalog.identities[0].identity_id
            self._view_mode = "home"
        if rebuild:
            self._main_controller.rebuild()

    @objc.python_method
    def _identity_summary(self, identity: PersonIdentity) -> str:
        if identity.stable_identity_id:
            photo_count, anchor_count, maturity, auto_enabled = self._identity_metrics(identity)
            maturity_label = {
                "auto_ready": "안정됨",
                "review_ready": "확인 가능",
                "learning": "학습 중",
            }.get(maturity, "학습 중")
            auto_label = f"자동 인식 {maturity_label}" if auto_enabled else "자동 인식 꺼짐"
            return f"사진 {photo_count}장 · 직접 확인 {anchor_count}장 · {auto_label}"
        return f"얼굴 {len(identity.faces)}개 · {'직접 수정됨' if identity.is_manual else '자동 그룹'}"

    @objc.python_method
    def _face_drag_payload(self, identity: PersonIdentity, face_ids: Any) -> dict[str, Any]:
        return {
            "version": DRAG_PAYLOAD_VERSION,
            "source_identity_id": identity.identity_id,
            "face_ids": sorted({str(face_id) for face_id in face_ids if str(face_id)}),
            "drag_type": FACE_DRAG_TYPE,
        }

    @objc.python_method
    def _capture_name_state(self) -> None:
        if self._name_field is None or not self._selected_identity_id:
            return
        self._name_draft_identity_id = self._selected_identity_id
        self._name_draft = str(self._name_field.stringValue() or "")
        editor = self._name_field.currentEditor()
        if editor is None:
            if self._name_restore_pending_identity_id != self._selected_identity_id:
                self._name_selection_range = None
            return
        selected_range = editor.selectedRange()
        self._name_selection_range = (int(selected_range.location), int(selected_range.length))
        self._name_restore_pending_identity_id = self._selected_identity_id

    @objc.python_method
    def _freeze_name_state(self) -> None:
        """Capture the source draft before a mutation changes the selected identity."""

        self._capture_name_state()
        self._name_field = None

    @objc.python_method
    def _capture_transient_state(self) -> None:
        self._capture_name_state()
        if self._move_popup is not None:
            item = self._move_popup.selectedItem()
            self._move_target_identity_id = str(item.representedObject() or "") if item is not None else ""
        if self._identity_scroll_view is not None:
            origin = self._identity_scroll_view.contentView().bounds().origin
            self._identity_scroll_origin = (float(origin.x), float(origin.y))
        if self._gallery_scroll_view is not None:
            origin = self._gallery_scroll_view.contentView().bounds().origin
            self._gallery_scroll_origin = (float(origin.x), float(origin.y))

    @objc.python_method
    def _identity_list(self, parent: Any, x: float, y: float, width: float, height: float) -> None:
        card = self._card(parent, x, y, width, height, selected=False)
        self._label(card, 18.0, height - 40.0, width - 36.0, 24.0, "인물", 14.5, True)
        subtitle = f"확인할 Apple 이름 후보 {len(self._aliases)}건"
        self._label(card, 18.0, height - 60.0, width - 36.0, 18.0, subtitle, 8.8, False, secondary=True)
        scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(10.0, 12.0, width - 20.0, max(60.0, height - 82.0)))
        scroll.setHasVerticalScroller_(True)
        scroll.setDrawsBackground_(False)
        self._identity_scroll_view = scroll
        content_height = len(self._catalog.identities) * 58.0 + 8.0
        # A document shorter than its clip view is bottom-aligned by AppKit.
        # Keep it at least viewport-height so short identity lists start at top.
        document_height = max(float(scroll.contentSize().height), content_height, 1.0)
        document = NSView.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, width - 34.0, document_height))
        scroll.setDocumentView_(document)
        for index, identity in enumerate(self._catalog.identities):
            row_y = document_height - ((index + 1) * 58.0)
            selected = identity.identity_id == self._selected_identity_id
            row = IdentityDropRowView.alloc().initWithIdentityId_controller_(identity.identity_id, self)
            row_width = width - 34.0
            row.setFrame_(NSMakeRect(0.0, row_y, row_width, 52.0))
            self._style_card(row, selected=selected)
            label = self._identity_labels.get(identity.identity_id, identity.display_name)
            row.setAccessibilityLabel_(f"{label}, {self._identity_summary(identity)}")
            row.setAccessibilityValue_("선택됨" if selected else "선택 안 됨")
            document.addSubview_(row)
            if identity.identity_id == self._focused_identity_id:
                self.performSelector_withObject_afterDelay_("restoreIdentityRowFocus:", row, 0.0)
            representative = identity.representative_face
            if representative is not None:
                image_view = NSImageView.alloc().initWithFrame_(NSMakeRect(10.0, 9.0, 34.0, 34.0))
                image_view.setImageScaling_(NSImageScaleProportionallyUpOrDown)
                image_view.setImage_(cached_image(representative.crop_path))
                image_view.setAccessibilityLabel_(f"{label} 대표 얼굴")
                row.addSubview_(image_view)
            self._label(row, 54.0, 27.0, max(60.0, row_width - 140.0), 18.0, label, 11.2, True)
            self._label(row, 54.0, 9.0, max(60.0, row_width - 140.0), 16.0, self._identity_summary(identity), 8.8, False, secondary=True)
            count = self._label(row, row_width - 76.0, 17.0, 32.0, 18.0, str(len(identity.faces)), 11.0, True, accent=selected)
            count.setAlignment_(2)
            if identity.faces:
                drag_handle = IdentityDragHandle.alloc().initWithPayload_controller_(
                    {"source_identity_id": identity.identity_id, "face_ids": []}, self
                )
                drag_handle.setFrame_(NSMakeRect(row_width - 40.0, 10.0, 32.0, 32.0))
                drag_handle.setAccessibilityLabel_(f"{label} 그룹 전체를 드래그하여 합치기")
                drag_handle.setToolTip_(f"{label} 그룹 전체를 다른 인물 그룹으로 드래그")
                row.addSubview_(drag_handle)
        card.addSubview_(scroll)
        if self._identity_scroll_origin is not None:
            scroll.contentView().scrollToPoint_(NSMakePoint(*self._identity_scroll_origin))
            scroll.reflectScrolledClipView_(scroll.contentView())

    @objc.python_method
    def _detail_panel(self, parent: Any, x: float, y: float, width: float, height: float) -> None:
        alias = self._selected_alias()
        if alias is not None:
            self._alias_detail_panel(parent, x, y, width, height, alias)
            return
        identity = self._selected_identity()
        if identity is None:
            card = self._card(parent, x, y, width, height, selected=False)
            self._label(card, 24.0, height - 50.0, width - 48.0, 28.0, "등록된 인물이 없습니다", 17.0, True)
            self._label(card, 24.0, height - 78.0, width - 48.0, 22.0, "인물 사진 분석이 완료되면 후보를 확인하고 이름을 지정할 수 있습니다.", 10.5, False, secondary=True)
            if self._catalog.excluded_face_count:
                self._button(card, 24.0, height - 124.0, 220.0, 30.0, f"제외한 얼굴 모두 복원 ({self._catalog.excluded_face_count})", "restoreExcludedFaces:")
            self._label(card, 24.0, 50.0, max(180.0, width - 180.0), 18.0, self._undo_message, 8.8, False, secondary=True)
            undo_button = self._button(card, width - 140.0, 38.0, 116.0, 30.0, "실행 취소", "undoLastChange:", enabled=self._undo_snapshot is not None)
            undo_button.setKeyEquivalent_("z")
            undo_button.setKeyEquivalentModifierMask_(NSEventModifierFlagCommand)
            return
        card = self._card(parent, x, y, width, height, selected=True)
        identity_label = self._identity_labels.get(identity.identity_id, identity.display_name)
        self._label(card, 20.0, height - 42.0, max(180.0, width - 390.0), 24.0, identity_label, 16.0, True)
        self._label(card, 20.0, height - 64.0, max(180.0, width - 390.0), 18.0, self._identity_summary(identity), 9.0, False, secondary=True)
        if self._catalog.excluded_face_count:
            self._button(card, width - 400.0, height - 68.0, 210.0, 28.0, f"제외한 얼굴 모두 복원 ({self._catalog.excluded_face_count})", "restoreExcludedFaces:")
        if identity.is_manual and not identity.stable_identity_id:
            self._button(card, width - 180.0, height - 68.0, 160.0, 28.0, "자동 그룹으로 되돌리기", "clearManualChanges:")
        self._label(card, 20.0, height - 100.0, 58.0, 20.0, "이름", 10.2, True)
        self._name_field = NSTextField.alloc().initWithFrame_(NSMakeRect(72.0, height - 106.0, max(120.0, width - 292.0), 28.0))
        draft = self._name_draft if self._name_draft_identity_id == identity.identity_id else identity.name
        self._name_field.setStringValue_(draft)
        self._name_field.setPlaceholderString_("예: 엄마, 민서")
        self._name_field.setFont_(app_font(10.5, "regular"))
        self._name_field.setAccessibilityLabel_("인물 이름")
        card.addSubview_(self._name_field)
        if (
            self._name_draft_identity_id == identity.identity_id
            and self._name_restore_pending_identity_id == identity.identity_id
            and self._name_selection_range is not None
        ):
            self.performSelector_withObject_afterDelay_("restoreNameEditorState:", self._name_field, 0.0)
        self._button(card, width - 210.0, height - 106.0, 92.0, 28.0, "이름 저장", "saveName:", primary=True)
        if not identity.faces:
            self._stable_identity_details(card, width, height, identity)
            return
        representative = identity.representative_face
        assert representative is not None
        representative_button = self._button(
            card,
            width - 128.0,
            height - 106.0,
            108.0,
            28.0,
            "대표 사진 보기",
            "openFaceSource:",
            identifier=representative.face_id,
        )
        representative_button.setToolTip_("이 인물 묶음의 대표 얼굴이 포함된 원본 사진 보기")

        self._add_identity_auto_toggle(card, height - 146.0, width, identity)

        drop_zone = NewIdentityDropZoneView.alloc().initWithController_(self)
        drop_zone.setFrame_(NSMakeRect(20.0, height - 196.0, width - 40.0, 40.0))
        self._style_card(drop_zone, selected=False)
        drop_zone.setAccessibilityLabel_("얼굴을 놓아 새 인물 그룹 만들기")
        card.addSubview_(drop_zone)
        self._label(drop_zone, 14.0, 11.0, width - 68.0, 18.0, "얼굴을 여기에 놓아 새 인물 그룹 만들기", 9.6, True, secondary=True)
        gallery_y = 124.0
        gallery_height = max(120.0, height - 334.0)
        self._face_gallery(card, 20.0, gallery_y, width - 40.0, gallery_height, identity)
        self._label(card, 20.0, 104.0, width - 40.0, 16.0, "드래그하거나 얼굴을 선택해 이동할 수 있습니다. 원본 사진은 변경하지 않습니다.", 8.8, False, secondary=True)
        selected_count = len(self._selected_face_ids)
        self._label(card, 20.0, 72.0, 80.0, 20.0, f"선택 {selected_count}개", 9.5, True, accent=bool(selected_count))
        new_group_enabled = 0 < selected_count < len(identity.faces)
        self._button(card, 104.0, 68.0, 114.0, 28.0, "새 그룹 만들기", "createSelectedIdentity:", enabled=new_group_enabled)
        exclude_selected = self._button(
            card,
            228.0,
            68.0,
            142.0,
            28.0,
            "얼굴 아님으로 표시",
            "excludeSelectedFaces:",
            enabled=bool(selected_count),
        )
        exclude_selected.setToolTip_("선택한 얼굴을 인물 관리에서 숨깁니다. 원본 사진은 변경하지 않습니다.")
        exclude_selected.setAccessibilityHelp_("선택한 얼굴만 인물 관리에서 숨깁니다. 실행 취소하거나 제외한 얼굴 복원으로 되돌릴 수 있습니다.")
        self._move_popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSMakeRect(20.0, 34.0, 188.0, 28.0),
            False,
        )
        self._move_popup.setAccessibilityLabel_("이동할 인물 그룹")
        self._move_popup.setTarget_(self)
        self._move_popup.setAction_("selectMoveTarget:")
        self._move_popup.addItemWithTitle_("이동할 그룹 선택")
        peer_ids: list[str] = []
        for peer in self._catalog.identities:
            if peer.identity_id == identity.identity_id or not peer.faces:
                continue
            label = self._identity_labels.get(peer.identity_id, peer.display_name)
            self._move_popup.addItemWithTitle_(label)
            self._move_popup.lastItem().setRepresentedObject_(peer.identity_id)
            peer_ids.append(peer.identity_id)
        if self._move_target_identity_id in peer_ids:
            self._move_popup.selectItemAtIndex_(peer_ids.index(self._move_target_identity_id) + 1)
        else:
            self._move_target_identity_id = ""
        self._move_popup.setEnabled_(bool(peer_ids))
        card.addSubview_(self._move_popup)
        has_move_target = bool(self._move_target_identity_id)
        self._button(card, 218.0, 34.0, 82.0, 28.0, "선택 이동", "moveSelectedFaces:", enabled=bool(selected_count) and has_move_target)
        merge_button = self._button(
            card,
            310.0,
            34.0,
            112.0,
            28.0,
            "그룹 전체 병합",
            "mergeSelectedIdentity:",
            enabled=has_move_target and not selected_count,
        )
        merge_button.setToolTip_("선택한 얼굴이 없을 때 현재 그룹 전체를 대상 그룹에 합칩니다.")
        merge_button.setAccessibilityHelp_("병합 전에 이동할 얼굴 수와 대상 그룹을 다시 확인합니다.")
        undo_title = self._undo_message or "마지막 변경을 되돌릴 수 있습니다."
        self._label(card, 20.0, 10.0, max(180.0, width - 170.0), 16.0, undo_title, 8.3, False, secondary=True)
        undo_button = self._button(card, width - 132.0, 34.0, 112.0, 28.0, "실행 취소", "undoLastChange:", enabled=self._undo_snapshot is not None)
        undo_button.setKeyEquivalent_("z")
        undo_button.setKeyEquivalentModifierMask_(NSEventModifierFlagCommand)

    @objc.python_method
    def _alias_detail_panel(self, parent: Any, x: float, y: float, width: float, height: float, alias: Any) -> None:
        card = self._card(parent, x, y, width, height, selected=True)
        detail, selected_face = self._selected_alias_face_review(alias)
        faces = list((detail or {}).get("faces") or [])
        resolved_aliases = list(
            (detail or {}).get("resolved_alias_assignments") or []
        )
        resolved_only = bool(resolved_aliases) and not faces
        self._label(card, 20.0, height - 42.0, width - 40.0, 24.0, "Apple Photos 이름 후보", 16.0, True)
        self._label(card, 20.0, height - 68.0, width - 40.0, 20.0, alias.private_display_label, 12.0, True, accent=True)
        try:
            asset = self._menu_controller._state_store.run_repository.get_local_recommendation_asset_by_id(
                alias.local_asset_id
            ) or {}
        except (OSError, sqlite3.Error):
            asset = {}
        capture_date = str(asset.get("capture_date_local") or "")[:10]
        source_line = "Apple Photos" + (f" · {capture_date}" if capture_date else "")
        self._label(card, 20.0, height - 92.0, width - 236.0, 18.0, source_line, 9.5, False, secondary=True)
        reindexing = self._index_running and self._alias_indexing_asset_id == alias.local_asset_id
        if not resolved_only:
            reindex_button = self._button(
                card,
                width - 206.0,
                height - 100.0,
                186.0,
                30.0,
                "얼굴 찾는 중…" if reindexing else "이 사진 얼굴 다시 찾기",
                "reindexAliasPhoto:",
                enabled=not self._index_running and self._face_runtime.status == "ready",
            )
            reindex_button.setAccessibilityHelp_(
                "현재 사진만 다시 분석하고, 기존에 직접 확정한 얼굴 연결은 보존합니다."
            )
        if resolved_only:
            guide = (
                f"얼굴 {len(resolved_aliases)}명의 인물 연결이 이미 완료되었습니다. "
                "아래 이름 연결을 확인하고 이 사진을 마무리하세요."
            )
        elif faces:
            guide = "번호와 얼굴 crop을 비교해 이 이름에 해당하는 사람을 선택하세요."
        else:
            guide = "검출된 얼굴이 없습니다. 이 사진의 얼굴을 다시 찾아 주세요."
        if self._alias_index_message and (
            not self._alias_indexing_asset_id
            or self._alias_indexing_asset_id == alias.local_asset_id
        ):
            guide = f"{self._alias_index_message} · {guide}"
        self._label(card, 20.0, height - 114.0, width - 40.0, 18.0, guide, 9.5, False, secondary=True)
        suggestion = dict((selected_face or {}).get("identity_suggestion") or {})
        if not suggestion and selected_face:
            suggested_name = str(selected_face.get("suggested_display_name") or "").strip()
            if suggested_name:
                suggestion = {
                    "display_name": suggested_name,
                    "match_likelihood_percent": int(
                        round(float(selected_face.get("confidence_estimate") or 0.0) * 100.0)
                    ),
                    "supporting_photo_count": int(
                        selected_face.get("supporting_asset_count") or 0
                    ),
                    "person_identity_id": str(
                        selected_face.get("suggested_person_identity_id") or ""
                    ),
                    "tier": str(selected_face.get("suggestion_tier") or "suggested"),
                }
        if suggestion:
            self._label(
                card,
                20.0,
                height - 136.0,
                width - 40.0,
                18.0,
                "추천 인물 · {name} · 일치 가능성 {likelihood}% · 확정 사진 {support}장".format(
                    name=str(suggestion.get("display_name") or "인물"),
                    likelihood=int(suggestion.get("match_likelihood_percent") or 0),
                    support=int(suggestion.get("supporting_photo_count") or 0),
                ),
                9.5,
                True,
                accent=True,
            )
        image_path = None
        workspace = PeopleWorkspaceService(
            self._identity_repository,
            run_repository=self._menu_controller._state_store.run_repository,
        )
        numbered_context_ref = str((detail or {}).get("numbered_context_ref") or "")
        if numbered_context_ref:
            try:
                image_path = workspace.artifact_path(numbered_context_ref)
            except (ValueError, FileNotFoundError):
                image_path = None
        try:
            if image_path is None:
                image_path = ShareImageService(
                    self._menu_controller._state_store.run_repository
                ).derivative(
                    share_id="people-review-mac",
                    public_asset_id=alias.alias_id,
                    local_asset_id=alias.local_asset_id,
                    kind="preview",
                )
        except (ShareImageError, RuntimeError):
            pass
        image_height = max(150.0, height - 410.0)
        image_view = NSImageView.alloc().initWithFrame_(NSMakeRect(20.0, 272.0, width - 40.0, image_height))
        image_view.setImageScaling_(NSImageScaleProportionallyUpOrDown)
        if image_path is not None:
            image_view.setImage_(cached_image(str(image_path)))
        image_view.setAccessibilityLabel_(f"{alias.private_display_label} 후보 사진")
        card.addSubview_(image_view)
        display_faces = resolved_aliases if resolved_only else faces
        face_summary = (
            f"확인 완료된 얼굴 {len(display_faces)}명 · 이름 후보 연결을 마무리하세요"
            if resolved_only
            else f"사진 속 얼굴 {len(display_faces)}개 · 이름에 해당하는 얼굴을 선택하세요"
        )
        self._label(
            card,
            20.0,
            246.0,
            width - 40.0,
            18.0,
            face_summary,
            9.5,
            True,
            secondary=not bool(display_faces),
        )
        face_scroll = NSScrollView.alloc().initWithFrame_(
            NSMakeRect(20.0, 150.0, width - 40.0, 92.0)
        )
        face_scroll.setHasHorizontalScroller_(True)
        face_scroll.setDrawsBackground_(False)
        face_document_width = max(width - 40.0, len(display_faces) * 110.0)
        face_document = NSView.alloc().initWithFrame_(
            NSMakeRect(0.0, 0.0, face_document_width, 78.0)
        )
        face_scroll.setDocumentView_(face_document)
        for index, face in enumerate(display_faces):
            face_id = str(face.get("face_observation_id") or "")
            selected = face_id == self._selected_alias_face_id
            button = NSButton.alloc().initWithFrame_(NSMakeRect(index * 110.0, 0.0, 102.0, 74.0))
            resolved_name = str(face.get("confirmed_display_name") or "")
            button.setTitle_(
                f"{resolved_name} ✓"
                if resolved_only
                else f"얼굴 {index + 1}" + (" ✓" if selected else "")
            )
            button.setImagePosition_(NSImageAbove)
            if not resolved_only:
                button.setTarget_(self)
                button.setAction_("selectAliasFace:")
            button.setIdentifier_(face_id)
            button.setAccessibilityLabel_(
                f"{resolved_name}, 확인 완료"
                if resolved_only
                else f"얼굴 {index + 1}, {'선택됨' if selected else '선택 안 됨'}"
            )
            try:
                crop_path = workspace.artifact_path(str(face.get("review_crop_ref") or ""))
                button.setImage_(cached_image(str(crop_path)))
            except (ValueError, FileNotFoundError):
                pass
            face_document.addSubview_(button)
        card.addSubview_(face_scroll)
        if resolved_only:
            relationship = " · ".join(
                f"{item.get('alias_display_label')} → {item.get('confirmed_display_name')}"
                for item in resolved_aliases
            )
            self._label(
                card,
                20.0,
                116.0,
                width - 40.0,
                24.0,
                relationship,
                11.0,
                True,
                accent=True,
            )
            can_complete = bool(
                (detail or {}).get("can_complete_resolved_aliases")
            )
            self._button(
                card,
                20.0,
                68.0,
                min(280.0, width - 244.0),
                34.0,
                f"이 사진 이름 후보 {len(resolved_aliases)}건 완료",
                "completeResolvedAliases:",
                primary=True,
                enabled=can_complete,
            )
            self._button(card, width - 214.0, 68.0, 94.0, 30.0, "후보 제외", "rejectAlias:")
            self._button(card, width - 110.0, 68.0, 90.0, 30.0, "나중에", "deferAlias:")
            self._label(
                card,
                20.0,
                34.0,
                width - 40.0,
                18.0,
                "완료하면 기존 얼굴 연결은 그대로 두고 Apple Photos 이름 후보만 확정합니다.",
                8.8,
                False,
                secondary=True,
            )
            return
        self._alias_identity_popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSMakeRect(20.0, 112.0, min(260.0, width - 220.0), 30.0), False
        )
        self._alias_identity_popup.addItemWithTitle_("연결할 기존 인물 선택")
        for identity in self._catalog.identities:
            if not identity.stable_identity_id:
                continue
            label = self._identity_labels.get(identity.identity_id, identity.display_name)
            self._alias_identity_popup.addItemWithTitle_(label)
            self._alias_identity_popup.lastItem().setRepresentedObject_(identity.stable_identity_id)
        suggested_identity_id = str(suggestion.get("person_identity_id") or "")
        if suggested_identity_id and str(suggestion.get("tier") or "") == "ready_to_confirm":
            for item in self._alias_identity_popup.itemArray():
                if str(item.representedObject() or "") == suggested_identity_id:
                    self._alias_identity_popup.selectItem_(item)
                    break
        self._alias_identity_popup.setAccessibilityLabel_("연결할 기존 인물")
        card.addSubview_(self._alias_identity_popup)
        can_assign = selected_face is not None
        self._button(card, width - 188.0, 112.0, 168.0, 30.0, "선택 얼굴에 연결", "connectAlias:", primary=True, enabled=can_assign)
        self._button(card, 20.0, 68.0, 152.0, 30.0, "선택 얼굴 새 인물", "createPersonFromAlias:", enabled=can_assign)
        self._button(card, 182.0, 68.0, 124.0, 30.0, "후보 제외", "rejectAlias:")
        self._button(card, 316.0, 68.0, 112.0, 30.0, "나중에", "deferAlias:")
        remaining = len(self._aliases)
        ignore = self._button(
            card,
            20.0,
            28.0,
            174.0,
            30.0,
            "모르는 사람 · 무시",
            "ignoreUnknownAliasFace:",
            enabled=can_assign,
        )
        ignore.setAccessibilityHelp_(
            "실제 얼굴이지만 아는 사람이 아닌 경우 인물 목록과 Story에서 제외합니다."
        )
        self._label(card, 208.0, 34.0, width - 228.0, 18.0, f"확인 대기 {remaining}건 · 사용자 확인 전에는 Story 이름에 반영되지 않습니다.", 8.8, False, secondary=True)

    @objc.python_method
    def _stable_identity_details(
        self,
        card: Any,
        width: float,
        height: float,
        identity: PersonIdentity,
    ) -> None:
        photo_count, anchor_count, maturity, _auto_enabled = self._identity_metrics(identity)
        maturity_label = {
            "auto_ready": "안정됨",
            "review_ready": "확인 가능",
            "learning": "학습 중",
        }.get(maturity, "학습 중")
        crop_path = self._representative_crop_path(identity)
        portrait = NSImageView.alloc().initWithFrame_(
            NSMakeRect(20.0, height - 344.0, 164.0, 164.0)
        )
        portrait.setImageScaling_(NSImageScaleProportionallyUpOrDown)
        portrait.setWantsLayer_(True)
        portrait.layer().setCornerRadius_(14.0)
        portrait.layer().setMasksToBounds_(True)
        if crop_path:
            portrait.setImage_(cached_image(crop_path))
        else:
            portrait.setImage_(
                NSImage.imageWithSystemSymbolName_accessibilityDescription_(
                    "person.crop.square", "대표 얼굴 준비 중"
                )
            )
            if hasattr(portrait, "setContentTintColor_"):
                portrait.setContentTintColor_(NSColor.tertiaryLabelColor())
        portrait.setAccessibilityLabel_(
            f"{self._identity_labels.get(identity.identity_id, identity.display_name)} 대표 얼굴"
        )
        card.addSubview_(portrait)
        self._label(
            card,
            208.0,
            height - 174.0,
            width - 232.0,
            22.0,
            f"관리 중 · 사진 {photo_count}장 · 직접 확인 {anchor_count}장",
            12.0,
            True,
        )
        self._label(
            card,
            208.0,
            height - 200.0,
            width - 232.0,
            22.0,
            f"자동 인식 상태는 {maturity_label}입니다. 확실한 새 사진은 이 이름으로 자동 연결됩니다.",
            10.0,
            False,
            secondary=True,
        )
        if self._identity_repository is not None:
            self._add_identity_auto_toggle(
                card,
                height - 240.0,
                width,
                identity,
                x=208.0,
                control_width=width - 232.0,
            )
            self._label(card, 208.0, height - 276.0, width - 232.0, 20.0, "Story에 이름 표시", 10.5, True)
            consent_rows = (
                ("owner", "내 Story에 이름 표시"),
                ("family_share", "가족 공유 Story에 이름 표시"),
            )
            for index, (audience, title) in enumerate(consent_rows):
                toggle = NSButton.alloc().initWithFrame_(
                    NSMakeRect(208.0, height - 310.0 - (index * 36.0), width - 232.0, 28.0)
                )
                toggle.setButtonType_(NSButtonTypeSwitch)
                toggle.setTitle_(title)
                toggle.setIdentifier_(audience)
                toggle.setTarget_(self)
                toggle.setAction_("toggleStableConsent:")
                try:
                    consent_allowed = self._identity_repository.current_consent(
                        identity.stable_identity_id,
                        audience,
                    )
                except (KeyError, OSError, sqlite3.Error, ValueError):
                    consent_allowed = False
                    toggle.setEnabled_(False)
                    toggle.setToolTip_("인물 저장소를 읽을 수 없어 현재 설정을 표시할 수 없습니다.")
                toggle.setState_(NSControlStateValueOn if consent_allowed else 0)
                toggle.setAccessibilityLabel_(title)
                card.addSubview_(toggle)
        pending = []
        if self._catalog.pending_alias_count:
            pending.append(f"이름 확인 {self._catalog.pending_alias_count}건")
        if self._catalog.pending_lineage_hold_count:
            pending.append(f"기존 얼굴 연결 {self._catalog.pending_lineage_hold_count}건")
        pending_text = " · ".join(pending) if pending else "대기 중인 인물 검토가 없습니다."
        self._label(
            card,
            20.0,
            104.0,
            width - 40.0,
            20.0,
            pending_text,
            9.5,
            False,
            secondary=True,
        )
        undo_title = self._undo_message or "이름 변경은 안정 인물 저장소에 기록됩니다."
        self._label(card, 20.0, 50.0, max(180.0, width - 180.0), 18.0, undo_title, 8.8, False, secondary=True)
        undo_button = self._button(
            card,
            width - 140.0,
            38.0,
            116.0,
            30.0,
            "실행 취소",
            "undoLastChange:",
            enabled=self._stable_name_undo is not None,
        )
        undo_button.setKeyEquivalent_("z")
        undo_button.setKeyEquivalentModifierMask_(NSEventModifierFlagCommand)

    @objc.python_method
    def _face_gallery(self, parent: Any, x: float, y: float, width: float, height: float, identity: PersonIdentity) -> None:
        scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(x, y, width, height))
        scroll.setHasVerticalScroller_(True)
        scroll.setDrawsBackground_(False)
        self._gallery_scroll_view = scroll
        document_width = max(_FACE_CARD_WIDTH, width - 16.0)
        columns = max(1, int((document_width + _FACE_CARD_GAP) // (_FACE_CARD_WIDTH + _FACE_CARD_GAP)))
        rows = max(1, (len(identity.faces) + columns - 1) // columns)
        content_height = rows * (_FACE_CARD_HEIGHT + _FACE_CARD_GAP) + _FACE_CARD_GAP
        # A short group used to be placed at the document's bottom when the
        # enclosing scroll view was taller than its content.
        document_height = max(height, content_height)
        document = NSView.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, document_width, document_height))
        scroll.setDocumentView_(document)
        for index, face in enumerate(identity.faces):
            column, row = index % columns, index // columns
            card_x = column * (_FACE_CARD_WIDTH + _FACE_CARD_GAP)
            card_y = document_height - ((row + 1) * (_FACE_CARD_HEIGHT + _FACE_CARD_GAP))
            self._face_card(document, card_x, card_y, face, index)
        parent.addSubview_(scroll)
        if self._gallery_scroll_origin is not None:
            scroll.contentView().scrollToPoint_(NSMakePoint(*self._gallery_scroll_origin))
            scroll.reflectScrolledClipView_(scroll.contentView())

    @objc.python_method
    def _face_card(self, parent: Any, x: float, y: float, face: Any, index: int) -> None:
        card = self._card(parent, x, y, _FACE_CARD_WIDTH, _FACE_CARD_HEIGHT, selected=False)
        identity = self._selected_identity()
        identity_label = self._identity_labels.get(
            self._selected_identity_id,
            identity.display_name if identity is not None else "인물",
        )
        face_label = f"{identity_label}의 얼굴 {index + 1}"
        image_view = NSImageView.alloc().initWithFrame_(NSMakeRect(8.0, 40.0, _FACE_CARD_WIDTH - 16.0, 132.0))
        image_view.setImageScaling_(NSImageScaleProportionallyUpOrDown)
        image_view.setImage_(cached_image(face.crop_path))
        image_view.setAccessibilityLabel_(face_label)
        card.addSubview_(image_view)
        selection = NSButton.alloc().initWithFrame_(NSMakeRect(8.0, _FACE_CARD_HEIGHT - 34.0, 30.0, 26.0))
        selection.setButtonType_(NSButtonTypeSwitch)
        selection.setTitle_("")
        selection.setTarget_(self)
        selection.setAction_("toggleFaceSelection:")
        selection.setIdentifier_(face.face_id)
        selection.setState_(NSControlStateValueOn if face.face_id in self._selected_face_ids else 0)
        selection.setAccessibilityLabel_(f"{face_label} 선택")
        card.addSubview_(selection)
        if face.face_id == self._focused_face_selection_id:
            self.performSelector_withObject_afterDelay_("restoreFaceSelectionFocus:", selection, 0.0)
        handle = FaceDragHandle.alloc().initWithPayload_controller_(
            self._face_drag_payload(identity, [face.face_id]) if identity is not None else {}, self
        )
        handle.setFrame_(NSMakeRect(_FACE_CARD_WIDTH - 40.0, _FACE_CARD_HEIGHT - 40.0, 32.0, 32.0))
        handle.setAccessibilityLabel_(f"{face_label} 이동")
        handle.setToolTip_(f"{face_label}을 다른 인물 그룹으로 드래그")
        card.addSubview_(handle)
        toolbar_width = 76.0
        toolbar_x = (_FACE_CARD_WIDTH - toolbar_width) / 2.0
        self._icon_button(
            card,
            toolbar_x,
            5.0,
            "photo.on.rectangle",
            "openFaceSource:",
            identifier=face.face_id,
            accessibility_label=f"{face_label} 원본 사진 보기",
            accessibility_help="이 얼굴이 포함된 원본 사진을 미리보기로 엽니다.",
            tooltip="원본 사진 보기",
        )
        self._icon_button(
            card,
            toolbar_x + 44.0,
            5.0,
            "person.crop.circle.badge.xmark",
            "excludeFace:",
            identifier=face.face_id,
            accessibility_label=f"{face_label} 얼굴 아님으로 표시",
            accessibility_help="이 얼굴만 Photos MCP 인물 관리에서 숨깁니다. 원본 사진은 변경하지 않으며 실행 취소할 수 있습니다.",
            tooltip="얼굴 아님으로 표시",
            tint=NSColor.systemOrangeColor(),
        )

    @objc.python_method
    def _refresh_controls_only(self) -> None:
        self._main_controller.rebuild()

    @objc.python_method
    def _card(self, parent: Any, x: float, y: float, width: float, height: float, *, selected: bool) -> Any:
        card = NSView.alloc().initWithFrame_(NSMakeRect(x, y, width, height))
        self._style_card(card, selected=selected)
        parent.addSubview_(card)
        return card

    @objc.python_method
    def _style_card(self, card: Any, *, selected: bool) -> None:
        card.setWantsLayer_(True)
        card.layer().setCornerRadius_(12.0)
        card.layer().setBackgroundColor_(panel_background_color().CGColor())
        border = accent_color() if selected else subtle_border_color()
        card.layer().setBorderColor_(border.colorWithAlphaComponent_(0.7 if selected else 1.0).CGColor())
        card.layer().setBorderWidth_(2.0 if selected else 1.0)

    @objc.python_method
    def _remember_undo(self, message: str) -> None:
        self._undo_snapshot = self._registry.snapshot()
        self._stable_name_undo = None
        self._undo_message = message

    @objc.python_method
    def _remember_successful_change(self, snapshot: dict[str, Any], message: str) -> None:
        self._undo_snapshot = snapshot
        self._stable_name_undo = None
        self._undo_message = f"{message}  ⌘Z로 실행 취소할 수 있습니다."

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
    def _icon_button(
        self,
        parent: Any,
        x: float,
        y: float,
        symbol: str,
        action: str,
        *,
        identifier: str,
        accessibility_label: str,
        accessibility_help: str,
        tooltip: str,
        tint: Any | None = None,
    ) -> Any:
        """Create a compact face-card action without relying on clipped text."""

        button = NSButton.alloc().initWithFrame_(NSMakeRect(x, y, 32.0, 32.0))
        button.setTitle_("")
        button.setTarget_(self)
        button.setAction_(action)
        button.setIdentifier_(identifier)
        image = NSImage.imageWithSystemSymbolName_accessibilityDescription_(symbol, accessibility_label)
        if image is not None:
            button.setImage_(image)
            button.setImagePosition_(NSImageOnly)
        if tint is not None and hasattr(button, "setContentTintColor_"):
            button.setContentTintColor_(tint)
        button.setToolTip_(tooltip)
        button.setAccessibilityLabel_(accessibility_label)
        button.setAccessibilityHelp_(accessibility_help)
        parent.addSubview_(button)
        return button

    @objc.python_method
    def _alert(self, title: str, detail: str) -> None:
        alert = NSAlert.alloc().init()
        alert.setMessageText_(title)
        alert.setInformativeText_(detail)
        alert.addButtonWithTitle_("확인")
        alert.runModal()
