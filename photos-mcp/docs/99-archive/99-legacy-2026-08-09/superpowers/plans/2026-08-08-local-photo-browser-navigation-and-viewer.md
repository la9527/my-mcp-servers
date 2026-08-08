# Local Photo Browser Navigation and Viewer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the global browser toolbar with pane-owned controls, add clear pane separators and an aspect-correct single-photo viewer without changing classification selection semantics.

**Architecture:** Keep the behavior in `PhotosMcpLocalPhotoSelectionController` because folder navigation, filtering, focus, and classification selection already share that owner. Add an explicit grid/single-view state and reusable aspect-correct ImageIO conversion helper, then exercise geometry and state transitions through AppKit tests before live bundle validation.

**Tech Stack:** Python 3.12, PyObjC AppKit, ImageIO/CoreGraphics, pytest, py2app standalone bundle, Peekaboo.

---

### Task 1: Preserve decoded image aspect ratio

**Files:**
- Modify: `src/photos_mcp/local_file_selection_appkit.py`
- Test: `tests/test_menu_appkit_layout.py`

- [ ] **Step 1: Write the failing test**

Add a non-square PNG fixture test that calls `_decode_thumbnail` and asserts `image.size().width / image.size().height` equals the source ratio within `0.01`.

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
./.venv/bin/pytest tests/test_menu_appkit_layout.py -q -k 'preserves_source_aspect_ratio'
```

Expected: FAIL because the decoded `NSImage` currently has a square logical size.

- [ ] **Step 3: Implement the minimal correction**

Read `CGImageGetWidth(image)` and `CGImageGetHeight(image)`, then initialize `NSImage` with that actual size rather than `NSMakeSize(max_pixels, max_pixels)`.

- [ ] **Step 4: Run focused test**

Run the same pytest command and expect PASS.

### Task 2: Move controls into pane headers and add continuous dividers

**Files:**
- Modify: `src/photos_mcp/local_file_selection_appkit.py`
- Test: `tests/test_menu_appkit_layout.py`

- [ ] **Step 1: Write failing ownership and geometry tests**

Assert that the global `_toolbar` no longer exists, the location button is a sidebar descendant, navigation/search controls are content descendants, split view consumes the full content view, and both divider positions remain continuous across the pane height.

- [ ] **Step 2: Verify RED**

Run:

```bash
./.venv/bin/pytest tests/test_menu_appkit_layout.py -q -k 'owns_navigation_controls or uses_continuous_pane_dividers'
```

Expected: FAIL against the current global toolbar structure.

- [ ] **Step 3: Build pane-owned headers**

Create the location button in `_build_sidebar`; create back, forward, search, and a `격자 | 한 장` segmented control in `_build_content`; remove `_toolbar`; make `_split_view` fill the content view; use `NSSplitViewDividerStyleThin` and explicit divider color support available to the current AppKit runtime.

- [ ] **Step 4: Update responsive layout**

Lay out sidebar and content headers in their pane coordinate spaces. Preserve a two-row content header below `640pt`, keep at least 8pt between controls, and ensure collection content begins below the header.

- [ ] **Step 5: Verify GREEN**

Run the focused ownership and divider tests and expect PASS.

### Task 3: Add single-photo viewing mode

**Files:**
- Modify: `src/photos_mcp/local_file_selection_appkit.py`
- Test: `tests/test_menu_appkit_layout.py`

- [ ] **Step 1: Write failing mode tests**

Cover mode switching, current focused image display, previous/next focus movement in filtered order, disabled first/last controls, and empty-result fallback to grid.

- [ ] **Step 2: Verify RED**

Run:

```bash
./.venv/bin/pytest tests/test_menu_appkit_layout.py -q -k 'single_photo_view'
```

Expected: FAIL because the single-photo view API and controls do not exist.

- [ ] **Step 3: Implement viewer state and controls**

Add `_view_mode`, an aspect-fit `NSImageView`, overlay previous/next buttons, a classification checkbox, filename/counter labels, and actions for mode switching and photo navigation. Reuse `_focused_path`, `_selected_paths`, `_visible_photos`, `thumbnail_for`, and `_set_photo_checked`.

- [ ] **Step 4: Synchronize both modes**

When focus, search, sort, folder, or checkbox state changes, update the collection, Inspector, and single-view controls from the same state. Keep checkbox actions independent from focus movement.

- [ ] **Step 5: Verify GREEN**

Run the focused single-view tests and expect PASS.

### Task 4: Harden responsive geometry

**Files:**
- Modify: `src/photos_mcp/local_file_selection_appkit.py`
- Test: `tests/test_menu_appkit_layout.py`

- [ ] **Step 1: Write failing minimum geometry tests**

At a `500×616pt` center pane, assert header controls, single-photo stage, overlay navigation, checkbox, and footer remain within the pane and do not overlap. Repeat key assertions at a wide center width.

- [ ] **Step 2: Verify RED**

Run:

```bash
./.venv/bin/pytest tests/test_menu_appkit_layout.py -q -k 'single_photo_view_layout'
```

Expected: FAIL until the new layout is implemented.

- [ ] **Step 3: Implement compact and regular layouts**

Size the stage from available bounds, anchor overlay controls to the stage rather than the image, constrain long labels, and maintain the existing sidebar/content/Inspector width rules.

- [ ] **Step 4: Verify GREEN**

Run focused geometry tests and expect PASS.

### Task 5: Full verification and live resize sweep

**Files:**
- Validate: `src/photos_mcp/local_file_selection_appkit.py`
- Validate: `tests/test_menu_appkit_layout.py`

- [ ] **Step 1: Run focused regression suite**

```bash
./.venv/bin/pytest tests/test_direct_classification.py tests/test_menu_appkit_layout.py tests/test_photo_ranker_selection.py -q
git diff --check
```

Expected: all tests PASS and no whitespace errors.

- [ ] **Step 2: Build and relaunch standalone bundle**

```bash
./scripts/build_framework_standalone.sh
```

Restart `~/Applications/PhotosMcp.app` and verify `http://127.0.0.1:18791/health` reports ready.

- [ ] **Step 3: Exercise both modes at four sizes**

Use exact-window Peekaboo captures at `1180×700`, `1280×760`, `1440×860`, and `1680×900`. At every size inspect grid and single-photo mode for clipping, overlap, broken dividers, and inaccessible overlay controls.

- [ ] **Step 4: Verify image ratios live**

Open at least one portrait and one landscape image. Confirm thumbnail, single-photo stage, and Inspector use aspect-fit with no stretch or crop.

- [ ] **Step 5: Run final automated verification**

Repeat the focused regression suite, `git diff --check`, and VS Code diagnostics after the live sweep.
