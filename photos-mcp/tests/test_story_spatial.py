from photos_mcp.interfaces.http.story_spatial import SPATIAL_CSS, SPATIAL_JS


def test_spatial_story_module_exposes_isolated_mount_contract() -> None:
    assert "window.PhotosStorySpatial=Object.freeze({mount})" in SPATIAL_JS
    assert "getActiveTile()" in SPATIAL_JS
    assert "options.initialTile" in SPATIAL_JS
    assert "options.title" in SPATIAL_JS
    assert "options.intro" in SPATIAL_JS
    assert "host.__photosStorySpatial" in SPATIAL_JS
    assert "destroy()" in SPATIAL_JS


def test_spatial_story_preserves_source_tiles_and_shared_viewer_boundary() -> None:
    assert "Array.from(options.tiles||[])" in SPATIAL_JS
    assert "onOpen(records[index].tile)" in SPATIAL_JS
    assert "tile.dataset.thumb" in SPATIAL_JS
    assert "tile.dataset.preview" in SPATIAL_JS
    assert "tile.closest?.('.chapter')" in SPATIAL_JS
    assert ".chapter-head h2, h2" in SPATIAL_JS
    assert ".chapter-copy" in SPATIAL_JS
    assert "sourceTiles.splice" not in SPATIAL_JS
    assert "tile.remove" not in SPATIAL_JS


def test_spatial_story_uses_swiper_input_with_custom_physical_depth() -> None:
    assert "new window.Swiper(stage" in SPATIAL_JS
    assert "watchSlidesProgress:true" in SPATIAL_JS
    assert "freeMode:{enabled:!reduceMotion,sticky:true,momentum:true" in SPATIAL_JS
    assert "slidesPerView:compact?mobileCount:desktopCount" in SPATIAL_JS
    assert "records.length>=7?7" in SPATIAL_JS
    assert "Math.min(3,records.length)" in SPATIAL_JS
    assert "rotateY(" in SPATIAL_JS
    assert "translate3d('+depth.x+'px," in SPATIAL_JS
    assert "sizeFrame(record,record.image.naturalWidth/record.image.naturalHeight)" in SPATIAL_JS
    assert "outward=distance<.08?0:-side*" in SPATIAL_JS
    assert "brightness(" in SPATIAL_JS


def test_spatial_story_has_accessible_timeline_lazy_loading_and_reduced_motion() -> None:
    assert "aria-roledescription','사진 회전 갤러리'" in SPATIAL_JS
    assert "aria-label','Story 장면'" in SPATIAL_JS
    assert "timeline.hidden=groups.length===1" in SPATIAL_JS
    assert "slide.setAttribute('role','group')" not in SPATIAL_JS
    assert "a11y:{enabled:true,slideRole:null" in SPATIAL_JS
    assert "ArrowLeft" in SPATIAL_JS
    assert "ArrowRight" in SPATIAL_JS
    assert "event.key==='Home'" in SPATIAL_JS
    assert "event.key==='End'" in SPATIAL_JS
    assert "d.querySelector('[data-viewer]')?.open" in SPATIAL_JS
    assert "for(let item=Math.max(0,index-4)" in SPATIAL_JS
    assert "distance<=1?'preview':'thumb'" in SPATIAL_JS
    assert "lastSyncedIndex!==activeIndex" in SPATIAL_JS
    assert "speed:reduceMotion?0:520" in SPATIAL_JS
    assert "@media(prefers-reduced-motion:reduce)" in SPATIAL_CSS
    assert "resizeObserver?.disconnect()" in SPATIAL_JS
    assert "swiper.destroy(true,true)" in SPATIAL_JS


def test_spatial_story_visual_contract_has_stage_curve_floor_and_reflection() -> None:
    assert "perspective:1900px" in SPATIAL_CSS
    assert ".photos-spatial__stage::before" in SPATIAL_CSS
    assert ".photos-spatial__stage::after" in SPATIAL_CSS
    assert "-webkit-box-reflect:below" in SPATIAL_CSS
    assert "flex:0 0 auto" in SPATIAL_CSS
    assert "object-fit:contain" in SPATIAL_CSS
    assert "transform-style:preserve-3d" in SPATIAL_CSS
    assert "@media(max-width:760px)" in SPATIAL_CSS
