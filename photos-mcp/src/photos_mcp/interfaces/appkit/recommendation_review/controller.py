"""Keyboard-first AppKit window for scene recommendation review."""

from __future__ import annotations

from typing import Any

import objc
from AppKit import (
    NSAlert,
    NSBackingStoreBuffered,
    NSButton,
    NSButtonTypeRadio,
    NSButtonTypeSwitch,
    NSColor,
    NSEventModifierFlagShift,
    NSImageScaleProportionallyUpOrDown,
    NSMakeRect,
    NSScrollView,
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

from photos_mcp.application.recommendation_review import (
    first_unreviewed_person_composition_index,
    first_unreviewed_scene_index,
    load_or_create_recommendation_review,
    summarize_recommendation_review,
    write_recommendation_review_queue,
)
from photos_mcp.interfaces.appkit.results.collection_item import cached_image
from photos_mcp.interfaces.appkit.results.photo_viewer import PhotosMcpPhotoViewerController
from photos_mcp.interfaces.appkit.shared.theme import (
    accent_color,
    app_font,
    panel_background_color,
    subtle_border_color,
)


_WINDOW_SIZE = (1280.0, 820.0)
_MIN_SIZE = (1020.0, 680.0)
_SIDEBAR_WIDTH = 300.0
_MARGIN = 24.0


class RecommendationReviewContentView(NSView):
    def acceptsFirstResponder(self) -> bool:
        return True

    def keyDown_(self, event) -> None:
        controller = getattr(self, "_review_controller", None)
        if controller is None:
            objc.super(RecommendationReviewContentView, self).keyDown_(event)
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
            controller.nextScene_(None)
            return
        objc.super(RecommendationReviewContentView, self).keyDown_(event)


class PhotosMcpRecommendationReviewController(NSWindowController):
    """Compare photos within one detected scene and persist private labels."""

    def initWithResultPayload_(self, result_payload: dict[str, Any]):
        return self._init_with_result_payload(result_payload, person_composition_review=False)

    def initWithResultPayload_personCompositionReview_(
        self,
        result_payload: dict[str, Any],
        person_composition_review: bool,
    ):
        return self._init_with_result_payload(
            result_payload,
            person_composition_review=bool(person_composition_review),
        )

    @objc.python_method
    def _init_with_result_payload(
        self,
        result_payload: dict[str, Any],
        *,
        person_composition_review: bool,
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
        self = objc.super(PhotosMcpRecommendationReviewController, self).initWithWindow_(window)
        if self is None:
            return None
        self._queue_path, self._payload = load_or_create_recommendation_review(result_payload)
        self._person_composition_review = person_composition_review
        self._index = (
            first_unreviewed_person_composition_index(self._payload)
            if self._person_composition_review
            else first_unreviewed_scene_index(self._payload)
        )
        self._primary_id = ""
        self._secondary_id = ""
        self._boundary = "correct"
        self._person_composition = "unreviewed"
        self._failure_codes: set[str] = set()
        self._viewer_controller = PhotosMcpPhotoViewerController.alloc().init()
        self._is_rendering = False
        root = RecommendationReviewContentView.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, *_WINDOW_SIZE))
        root._review_controller = self
        window.setContentView_(root)
        window.setTitle_("인물 구성 검토" if self._person_composition_review else "추천 품질 검토")
        window.setMinSize_(NSMakeSize(*_MIN_SIZE))
        window.setFrameAutosaveName_("PhotosMcpRecommendationQualityReview")
        window.setCollectionBehavior_(NSWindowCollectionBehaviorFullScreenPrimary)
        window.setReleasedWhenClosed_(False)
        window.setDelegate_(self)
        zoom_button = window.standardWindowButton_(NSWindowZoomButton)
        if zoom_button is not None:
            zoom_button.setEnabled_(True)
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

    @objc.python_method
    def selectCandidateAtIndex_(self, index: int, as_secondary: bool) -> None:
        photos = self._current_item()["photos"]
        if not 0 <= index < len(photos):
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

    def openPhoto_(self, sender) -> None:
        index = int(sender.tag())
        photos = self._current_item()["photos"]
        if not 0 <= index < len(photos):
            return
        viewer_items = [
            {
                "photo_id": str(photo.get("photo_id") or ""),
                "preview_path": str(photo.get("preview_path") or ""),
                "source_photo_path": str(photo.get("source_photo_path") or ""),
                "scene_description": "추천 품질 검토",
                "event_type": str(photo.get("event_type") or "other"),
            }
            for photo in photos
        ]
        self._viewer_controller.show_items(
            viewer_items,
            str(photos[index].get("photo_id") or ""),
        )

    def setBoundary_(self, sender) -> None:
        self._boundary = str(sender.identifier() or "correct")
        self.render()

    def setPersonComposition_(self, sender) -> None:
        self._person_composition = str(sender.identifier() or "unreviewed")
        self.render()

    def toggleFailure_(self, sender) -> None:
        code = str(sender.identifier() or "")
        if code in self._failure_codes:
            self._failure_codes.remove(code)
        elif code:
            self._failure_codes.add(code)

    def clearPrimary_(self, _sender) -> None:
        self._primary_id = ""
        self._secondary_id = ""
        self.render()

    def previousScene_(self, _sender) -> None:
        if self._index > 0:
            self._index -= 1
            self._load_current_draft()
            self.render()

    def nextScene_(self, _sender) -> None:
        if self._index + 1 < len(self._payload["items"]):
            self._index += 1
            self._load_current_draft()
            self.render()

    def skipScene_(self, _sender) -> None:
        if self._person_composition_review:
            self._current_item()["labels"]["person_composition"] = "uncertain"
            self._persist_and_advance()
            return
        self._current_item()["labels"].update(
            {
                "review_status": "skipped",
                "best_photo_ids": [],
                "scene_boundary": self._boundary,
                "failure_codes": sorted(self._failure_codes),
            }
        )
        self._persist_and_advance()

    def saveAndNext_(self, _sender) -> None:
        if self._person_composition_review:
            if self._person_composition == "unreviewed":
                self._alert(
                    "인물 구성을 선택하세요",
                    "판단이 어려우면 ‘판단 보류’로 저장할 수 있습니다.",
                )
                return
            self._current_item()["labels"]["person_composition"] = self._person_composition
            self._persist_and_advance()
            return
        if not self._primary_id:
            self._alert(
                "가장 좋은 사진을 한 장 선택하세요",
                "판단하기 어려운 장면은 ‘건너뛰기’를 사용할 수 있습니다.",
            )
            return
        best_ids = [self._primary_id]
        if self._secondary_id and self._secondary_id != self._primary_id:
            best_ids.append(self._secondary_id)
        self._current_item()["labels"].update(
            {
                "review_status": "completed",
                "scene_boundary": self._boundary,
                "best_photo_ids": best_ids,
                "failure_codes": sorted(self._failure_codes),
            }
        )
        self._persist_and_advance()

    @objc.python_method
    def _persist_and_advance(self) -> None:
        write_recommendation_review_queue(self._queue_path, self._payload)
        if self._index + 1 < len(self._payload["items"]):
            self._index += 1
            self._load_current_draft()
            self.render()
            return
        summary = summarize_recommendation_review(self._payload)
        self.render()
        self._alert("장면 검토를 완료했습니다", self._summary_text(summary))

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
        self._person_composition = str(labels.get("person_composition") or "unreviewed")
        self._failure_codes = set(str(value) for value in labels.get("failure_codes") or [])

    @objc.python_method
    def _summary_text(self, summary: dict[str, Any]) -> str:
        if self._person_composition_review:
            return (
                f"인물 구성 완료 {summary['person_composition_completed_scene_count']} / "
                f"{summary['scene_count']}장면\n"
                f"남음 {summary['person_composition_remaining_scene_count']}장면"
            )
        top1 = summary.get("auto_top1_match_rate")
        top2 = summary.get("auto_primary_recall_at_2")
        top1_text = "계산 전" if top1 is None else f"{float(top1) * 100:.1f}%"
        top2_text = "계산 전" if top2 is None else f"{float(top2) * 100:.1f}%"
        return (
            f"완료 {summary['completed_scene_count']} / {summary['scene_count']}장면\n"
            f"자동 1순위 적중 {top1_text} · 자동 Top-2 포함 {top2_text}"
        )

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
            summary = summarize_recommendation_review(self._payload)

            title = "인물 구성 검토" if self._person_composition_review else "추천 품질 검토"
            helper = (
                "주요 인물이 같은 사진인지 판단합니다. 배경 행인만 달라진 경우는 별도로 표시하세요."
                if self._person_composition_review
                else "사진을 클릭하면 1순위, Shift+숫자 또는 ‘2순위’ 버튼으로 두 번째 사진을 선택합니다."
            )
            self._label(root, _MARGIN, height - 56.0, width - 2 * _MARGIN, 32.0, title, 22.0, True)
            progress_text = (
                f"장면 {self._index + 1} / {len(self._payload['items'])} · "
                f"인물 구성 완료 {summary['person_composition_completed_scene_count']} · "
                f"남음 {summary['person_composition_remaining_scene_count']}장면"
                if self._person_composition_review
                else f"장면 {self._index + 1} / {len(self._payload['items'])} · "
                f"완료 {summary['completed_scene_count']} · 건너뜀 {summary['skipped_scene_count']}"
            )
            self._label(
                root,
                _MARGIN,
                height - 84.0,
                width - 2 * _MARGIN,
                20.0,
                progress_text,
                11.0,
                False,
                secondary=True,
            )
            self._label(
                root,
                _MARGIN,
                height - 108.0,
                width - _SIDEBAR_WIDTH - 3 * _MARGIN,
                20.0,
                helper,
                10.0,
                False,
                secondary=True,
            )

            sidebar_x = width - _SIDEBAR_WIDTH - _MARGIN
            content_width = sidebar_x - 2 * _MARGIN
            content_height = height - 146.0
            scroll = NSScrollView.alloc().initWithFrame_(
                NSMakeRect(_MARGIN, 22.0, content_width, content_height)
            )
            scroll.setHasVerticalScroller_(True)
            scroll.setAutohidesScrollers_(True)
            scroll.setDrawsBackground_(False)
            scroll.setBorderType_(0)
            scroll.setDocumentView_(self._build_grid(content_width - 14.0, item))
            root.addSubview_(scroll)
            self._build_sidebar(root, sidebar_x, 22.0, _SIDEBAR_WIDTH, content_height, summary)
            self.window().makeFirstResponder_(root)
        finally:
            self._is_rendering = False

    @objc.python_method
    def _build_grid(self, width: float, item: dict[str, Any]) -> Any:
        photos = list(item.get("photos") or [])
        columns = min(2, max(1, int(width // 360.0)))
        gap = 14.0
        tile_width = (width - gap * (columns - 1)) / columns
        image_height = min(390.0, max(230.0, tile_width * 0.72))
        tile_height = image_height + 82.0
        rows = max(1, (len(photos) + columns - 1) // columns)
        grid_height = rows * (tile_height + gap) - gap
        grid = NSView.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, width, grid_height))
        for index, photo in enumerate(photos):
            column = index % columns
            row = index // columns
            x = column * (tile_width + gap)
            y = grid_height - (row + 1) * tile_height - row * gap
            tile = NSView.alloc().initWithFrame_(NSMakeRect(x, y, tile_width, tile_height))
            tile.setWantsLayer_(True)
            photo_id = str(photo.get("photo_id") or "")
            selected = photo_id in {self._primary_id, self._secondary_id}
            tile.layer().setCornerRadius_(12.0)
            tile.layer().setBackgroundColor_(panel_background_color().CGColor())
            tile.layer().setBorderWidth_(2.0 if selected else 1.0)
            tile.layer().setBorderColor_((accent_color() if selected else subtle_border_color()).CGColor())
            grid.addSubview_(tile)

            image_button = NSButton.alloc().initWithFrame_(
                NSMakeRect(8.0, 64.0, tile_width - 16.0, image_height - 10.0)
            )
            image_button.setBordered_(False)
            image_button.setTag_(index)
            image_button.setTarget_(self)
            image_button.setAction_(
                "openPhoto:" if self._person_composition_review else "selectPrimary:"
            )
            image_button.setImageScaling_(NSImageScaleProportionallyUpOrDown)
            image_button.setAccessibilityLabel_(
                f"사진 {index + 1} 크게 보기"
                if self._person_composition_review
                else f"사진 {index + 1}, 사람 평가 1순위로 선택"
            )
            preview = str(photo.get("preview_path") or "")
            image = cached_image(preview)
            if image is not None:
                image_button.setImage_(image)
            else:
                image_button.setTitle_("미리보기를 찾을 수 없습니다")
            tile.addSubview_(image_button)

            human_label = (
                f"사진 {index + 1}"
                if self._person_composition_review
                else "사람 1순위"
                if photo_id == self._primary_id
                else "사람 2순위"
                if photo_id == self._secondary_id
                else f"사진 {index + 1}"
            )
            auto_slot = int(photo.get("recommendation_slot") or 0)
            auto_label = f" · 자동추천 {auto_slot}" if auto_slot else ""
            self._label(
                tile,
                12.0,
                34.0,
                tile_width - 178.0,
                22.0,
                f"{human_label}{auto_label}",
                10.5,
                True,
                accent=selected,
            )
            score = float(photo.get("total_score") or 0.0)
            self._label(tile, 12.0, 12.0, tile_width - 178.0, 18.0, f"종합 {score:.0f}", 9.5, False, secondary=True)
            preview_button = self._button(
                tile,
                tile_width - 162.0,
                20.0,
                72.0,
                30.0,
                "크게 보기",
                "openPhoto:",
            )
            preview_button.setTag_(index)
            if not self._person_composition_review:
                second = self._button(
                    tile,
                    tile_width - 82.0,
                    20.0,
                    70.0,
                    30.0,
                    "2순위",
                    "toggleSecondary:",
                )
                second.setTag_(index)
                second.setEnabled_(photo_id != self._primary_id)
        return grid

    @objc.python_method
    def _build_sidebar(
        self,
        root: Any,
        x: float,
        y: float,
        width: float,
        height: float,
        summary: dict[str, Any],
    ) -> None:
        panel = NSView.alloc().initWithFrame_(NSMakeRect(x, y, width, height))
        panel.setWantsLayer_(True)
        panel.layer().setCornerRadius_(14.0)
        panel.layer().setBackgroundColor_(panel_background_color().CGColor())
        panel.layer().setBorderColor_(subtle_border_color().CGColor())
        panel.layer().setBorderWidth_(1.0)
        root.addSubview_(panel)
        self._label(panel, 18.0, height - 46.0, width - 36.0, 24.0, "검토 결과", 15.0, True)
        self._label(
            panel,
            18.0,
            height - 100.0,
            width - 36.0,
            44.0,
            self._summary_text(summary),
            10.0,
            False,
            secondary=True,
        )
        if self._person_composition_review:
            self._label(panel, 18.0, height - 160.0, width - 36.0, 34.0, "주요 인물 구성", 12.0, True)
            self._label(
                panel,
                18.0,
                height - 184.0,
                width - 36.0,
                20.0,
                "사람 이름 대신 사진 간 구성을 비교합니다.",
                9.0,
                False,
                secondary=True,
            )
            row_y = height - 222.0
            for code, title in (
                ("same_primary_subjects", "주요 인물이 모두 같음"),
                ("different_primary_subjects", "주요 인물 구성이 다름"),
                ("background_people_only", "배경 행인만 다름"),
                ("face_detection_unavailable", "얼굴 검출이 어려움"),
                ("uncertain", "판단 보류"),
            ):
                button = self._button(
                    panel, 18.0, row_y, width - 36.0, 28.0, title, "setPersonComposition:"
                )
                button.setButtonType_(NSButtonTypeRadio)
                button.setIdentifier_(code)
                button.setState_(1 if code == self._person_composition else 0)
                row_y -= 34.0
            save_title = "인물 구성 저장하고 다음 (Enter)"
        else:
            self._button(
                panel,
                18.0,
                height - 140.0,
                width - 36.0,
                30.0,
                "선택 모두 해제 (0)",
                "clearPrimary:",
            )
            self._label(panel, 18.0, height - 184.0, width - 36.0, 20.0, "장면 묶음", 12.0, True)
            row_y = height - 216.0
            for code, title in (
                ("correct", "같은 장면"),
                ("over_merged", "서로 다른 장면"),
                ("uncertain", "판단 보류"),
            ):
                button = self._button(panel, 18.0, row_y, width - 36.0, 28.0, title, "setBoundary:")
                button.setIdentifier_(code)
                button.setState_(1 if code == self._boundary else 0)
                row_y -= 34.0

            self._label(panel, 18.0, row_y - 4.0, width - 36.0, 20.0, "자동추천이 놓친 이유", 12.0, True)
            row_y -= 36.0
            for code, title in (
                ("eyes_closed", "눈 감김"),
                ("blur", "흐림"),
                ("bad_expression", "표정"),
                ("duplicate", "유사 사진 중복"),
                ("other", "기타"),
            ):
                toggle = NSButton.alloc().initWithFrame_(NSMakeRect(16.0, row_y, width - 32.0, 25.0))
                toggle.setButtonType_(NSButtonTypeSwitch)
                toggle.setTitle_(title)
                toggle.setState_(1 if code in self._failure_codes else 0)
                toggle.setTarget_(self)
                toggle.setAction_("toggleFailure:")
                toggle.setIdentifier_(code)
                toggle.setFont_(app_font(9.5))
                toggle.setAccessibilityLabel_(f"자동추천 실패 이유: {title}")
                panel.addSubview_(toggle)
                row_y -= 29.0
            save_title = "저장하고 다음 (Enter)"

        self._button(panel, 18.0, 76.0, width - 36.0, 36.0, save_title, "saveAndNext:", primary=True)
        nav_width = (width - 46.0) / 2.0
        self._button(panel, 18.0, 36.0, nav_width, 30.0, "이전", "previousScene:")
        self._button(panel, 28.0 + nav_width, 36.0, nav_width, 30.0, "건너뛰기", "skipScene:")

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
        label.setTextColor_(
            accent_color()
            if accent
            else NSColor.secondaryLabelColor()
            if secondary
            else NSColor.labelColor()
        )
        label.setLineBreakMode_(4)
        label.setMaximumNumberOfLines_(2)
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
        button.setFont_(app_font(9.5, "semibold"))
        button.setAccessibilityLabel_(title)
        if primary and hasattr(button, "setKeyEquivalent_"):
            button.setKeyEquivalent_("\r")
        parent.addSubview_(button)
        return button

    @objc.python_method
    def _alert(self, title: str, detail: str) -> None:
        alert = NSAlert.alloc().init()
        alert.setMessageText_(title)
        alert.setInformativeText_(detail)
        alert.addButtonWithTitle_("확인")
        alert.runModal()
