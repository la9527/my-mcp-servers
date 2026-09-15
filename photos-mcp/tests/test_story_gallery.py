import json
import shutil
import subprocess

import pytest

from photos_mcp.interfaces.http.story_gallery import GALLERY_CSS, GALLERY_JS


def test_gallery_module_exposes_reusable_mount_contract() -> None:
    assert "window.PhotosStoryGallery=Object.freeze({mount})" in GALLERY_JS
    assert "['quiet_memories','moment_clusters','all']" in GALLERY_JS
    assert "options.initialTile" in GALLERY_JS
    assert "options.title" in GALLERY_JS
    assert "options.intro" in GALLERY_JS
    assert "getActiveTile()" in GALLERY_JS
    assert "destroy()" in GALLERY_JS


def test_gallery_keeps_source_tiles_read_only_and_uses_ratio_safe_derivatives() -> None:
    assert "Array.from(options.tiles||[])" in GALLERY_JS
    assert "onOpen(item.tile)" in GALLERY_JS
    assert "tile.dataset.thumb" in GALLERY_JS
    assert "tile.dataset.preview" in GALLERY_JS
    assert "tile.dataset.width" in GALLERY_JS
    assert "tile.dataset.height" in GALLERY_JS
    assert "image.naturalWidth/image.naturalHeight" in GALLERY_JS
    assert "image.loading=kind==='preview'?'eager':'lazy'" in GALLERY_JS
    assert "tile.remove" not in GALLERY_JS
    assert "tile.replaceChildren" not in GALLERY_JS


def test_all_photo_gallery_uses_explicit_non_overlapping_justified_geometry() -> None:
    assert "grid-template-columns:none!important" in GALLERY_CSS
    assert "aspect-ratio:auto!important" in GALLERY_CSS
    assert "position:absolute!important" in GALLERY_CSS
    assert "function justifiedGeometry(" in GALLERY_JS
    assert "button.style.left=box.left+'px'" in GALLERY_JS
    assert "button.style.top=box.top+'px'" in GALLERY_JS
    assert "button.style.width=box.width+'px'" in GALLERY_JS
    assert "button.style.height=box.height+'px'" in GALLERY_JS
    assert "rowHeight=last?Math.min(safeTarget,natural):natural" in GALLERY_JS
    assert "target=compact?168:172" in GALLERY_JS
    assert "canvas.style.height=geometry.height+'px'" in GALLERY_JS


def test_quiet_and_cluster_themes_are_independent_accessible_surfaces() -> None:
    assert ".psg-quiet-main img{object-fit:contain!important}" in GALLERY_CSS
    assert ".psg-quiet-strip" in GALLERY_CSS
    assert ".psg-cluster-grid" in GALLERY_CSS
    assert ".psg-photo.psg-feature.psg-portrait" in GALLERY_CSS
    assert "button.setAttribute('aria-current'" in GALLERY_JS
    assert "button.setAttribute('aria-pressed'" in GALLERY_JS
    assert "grid.setAttribute('role','group')" in GALLERY_JS
    assert "button.setAttribute('aria-label'" in GALLERY_JS
    assert "button.psg-photo:focus-visible" in GALLERY_CSS
    assert "strip.scrollTo({left:Math.max(0,left)" in GALLERY_JS
    assert "selectQuiet(index,false,true)" in GALLERY_JS
    assert "ArrowLeft" in GALLERY_JS
    assert "ArrowRight" in GALLERY_JS
    assert "Home" in GALLERY_JS
    assert "End" in GALLERY_JS
    assert "[data-viewer].open,[data-viewer][open]" in GALLERY_JS
    assert "resizeObserver?.disconnect()" in GALLERY_JS
    assert "@media(prefers-reduced-motion:reduce)" in GALLERY_CSS
    assert "\nbody" not in GALLERY_CSS


def test_cluster_supporting_copy_meets_wcag_normal_text_contrast() -> None:
    def relative_luminance(value: str) -> float:
        channels = [int(value[index : index + 2], 16) / 255 for index in (1, 3, 5)]
        red, green, blue = [
            channel / 12.92
            if channel <= 0.04045
            else ((channel + 0.055) / 1.055) ** 2.4
            for channel in channels
        ]
        return 0.2126 * red + 0.7152 * green + 0.0722 * blue

    foreground = "#686a70"
    background = "#fbfbfa"
    lighter = max(relative_luminance(foreground), relative_luminance(background))
    darker = min(relative_luminance(foreground), relative_luminance(background))

    assert f"color:{foreground}" in GALLERY_CSS
    assert (lighter + 0.05) / (darker + 0.05) >= 4.5


def test_gallery_image_failure_is_scoped_recoverable_and_keeps_original_open() -> None:
    assert ".psg-photo.psg-load-error img{opacity:0}" in GALLERY_CSS
    assert ".psg-photo-error{position:absolute;inset:0" in GALLERY_CSS
    assert "사진을 불러오지 못했어요" in GALLERY_JS
    assert "button.setAttribute('aria-describedby',notice.id)" in GALLERY_JS
    assert "listen(host,'error'" in GALLERY_JS
    assert "listen(host,'load'" in GALLERY_JS
    assert "button.classList.remove('psg-load-error')" in GALLERY_JS
    assert "button.querySelector('.psg-photo-error')?.remove()" in GALLERY_JS
    assert "onOpen(item.tile)" in GALLERY_JS


def _javascript_geometry(ratios: list[float]) -> dict[str, object]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for the JavaScript gallery geometry contract")
    instrumented = GALLERY_JS.replace(
        "window.PhotosStoryGallery=Object.freeze({mount});",
        "window.__galleryTest=Object.freeze({justifiedGeometry});",
    )
    script = "\n".join(
        (
            "global.window={};",
            instrumented,
            ("process.stdout.write(JSON.stringify("
            f"window.__galleryTest.justifiedGeometry({json.dumps(ratios)},378,5,168)"
            "));"),
        )
    )
    completed = subprocess.run(
        [node],
        input=script,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


@pytest.mark.parametrize("count", [0, 1, 2, 7, 1000])
def test_javascript_geometry_preserves_order_ratio_and_spacing(count: int) -> None:
    mixed = [0.12, 0.75, 1.0, 4 / 3, 1.5, 8.0, 2 / 3]
    ratios = [mixed[index % len(mixed)] for index in range(count)]
    result = _javascript_geometry(ratios)
    boxes = result["boxes"]

    assert len(boxes) == count
    assert result["height"] == (0 if count == 0 else pytest.approx(boxes[-1]["top"] + boxes[-1]["height"]))
    for index, box in enumerate(boxes):
        expected_ratio = ratios[index]
        assert box["width"] / box["height"] == pytest.approx(expected_ratio, abs=0.0002)
        assert box["left"] >= 0
        assert box["left"] + box["width"] <= 378.001
        if index:
            previous = boxes[index - 1]
            assert box["top"] >= previous["top"]
            if box["top"] == pytest.approx(previous["top"]):
                assert box["left"] >= previous["left"] + previous["width"] + 4.999

    for left_index, left in enumerate(boxes):
        for right in boxes[left_index + 1 :]:
            overlap_width = min(left["left"] + left["width"], right["left"] + right["width"]) - max(left["left"], right["left"])
            overlap_height = min(left["top"] + left["height"], right["top"] + right["height"]) - max(left["top"], right["top"])
            assert overlap_width <= 0 or overlap_height <= 0

    if boxes:
        last_top = boxes[-1]["top"]
        last_row = [box for box in boxes if box["top"] == pytest.approx(last_top)]
        assert last_row[0]["left"] == pytest.approx(0)


def test_mobile_typical_photos_form_two_or_three_item_rows() -> None:
    ratios = [0.75, 4 / 3, 1.5, 1.0, 0.75] * 20
    boxes = _javascript_geometry(ratios)["boxes"]
    rows: dict[float, int] = {}
    for box in boxes:
        rows[box["top"]] = rows.get(box["top"], 0) + 1
    complete_rows = list(rows.values())[:-1]
    assert complete_rows
    assert all(count in {2, 3} for count in complete_rows)
