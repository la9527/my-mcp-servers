#!/usr/bin/env zsh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

FRAMEWORK_VERSION="${PHOTOS_MCP_FRAMEWORK_VERSION:-3.12}"
DIST_DIR="${PHOTOS_MCP_DIST_DIR:-$ROOT_DIR/dist-framework-standalone}"
BUILD_DIR="${PHOTOS_MCP_BUILD_DIR:-$ROOT_DIR/build-framework-standalone}"
INSTALL_BUNDLE_PATH="${PHOTOS_MCP_INSTALL_BUNDLE_PATH:-$HOME/Applications/PhotosMcp.app}"
TCL_LIBRARY_DEFAULT="$ROOT_DIR/.framework-python-cache/python-3.12.10-expanded/Python_Framework.pkg/Payload/Versions/3.12/lib/tcl8.6"
TK_LIBRARY_DEFAULT="$ROOT_DIR/.framework-python-cache/python-3.12.10-expanded/Python_Framework.pkg/Payload/Versions/3.12/lib/tk8.6"
ICON_PYTHON="${PHOTOS_MCP_ICON_PYTHON:-$ROOT_DIR/.venv/bin/python}"
APP_BUNDLE="$DIST_DIR/PhotosMcp.app"
ALT_APP_BUNDLE="$DIST_DIR/photos-mcp.app"
FRAMEWORK_RUNTIME_DIR_OVERRIDE="${PHOTOS_MCP_FRAMEWORK_RUNTIME_DIR:-}"
SITE_PACKAGES_DIR_OVERRIDE="${PHOTOS_MCP_SITE_PACKAGES_DIR:-}"
FRAMEWORK_SHORT_VERSION="${FRAMEWORK_VERSION//./}"

find_framework_runtime_dir() {
	setopt local_options null_glob

	local candidate=""
	local formula="python@$FRAMEWORK_VERSION"
	local candidates=(
		"$ROOT_DIR/.framework-python-runtime"
		"/Library/Frameworks"
		/opt/homebrew/Cellar/$formula/*/Frameworks
		/usr/local/Cellar/$formula/*/Frameworks
	)

	for candidate in $candidates; do
		[[ -d "$candidate" ]] || continue
		if [[ -x "$candidate/Python.framework/Versions/$FRAMEWORK_VERSION/bin/python$FRAMEWORK_VERSION" ]]; then
			echo "$candidate"
			return 0
		fi
	done

	return 1
}

find_site_packages_dir() {
	local candidates=(
		"$ROOT_DIR/.venv-framework$FRAMEWORK_SHORT_VERSION/lib/python$FRAMEWORK_VERSION/site-packages"
		"$ROOT_DIR/.venv/lib/python$FRAMEWORK_VERSION/site-packages"
	)
	local candidate=""

	for candidate in $candidates; do
		if [[ -d "$candidate" ]]; then
			echo "$candidate"
			return 0
		fi
	done

	return 1
}

resolve_optional_library_dir() {
	local explicit_path="$1"
	shift
	local candidate=""

	if [[ -n "$explicit_path" ]]; then
		echo "$explicit_path"
		return 0
	fi

	for candidate in "$@"; do
		if [[ -d "$candidate" ]]; then
			echo "$candidate"
			return 0
		fi
	done

	return 1
}

find_liblzma_source() {
	setopt local_options null_glob

	local candidates=(
		"/opt/homebrew/lib/liblzma.5.dylib"
		"/usr/local/lib/liblzma.5.dylib"
		/opt/homebrew/Cellar/xz/*/lib/liblzma.5.dylib
		/usr/local/Cellar/xz/*/lib/liblzma.5.dylib
	)
	local candidate=""

	for candidate in $candidates; do
		if [[ -f "$candidate" ]]; then
			echo "$candidate"
			return 0
		fi
	done

	return 1
}

repair_problematic_framework_dylibs() {
	local bundle_path="$1"
	local bundled_liblzma="$bundle_path/Contents/Frameworks/liblzma.5.dylib"
	local liblzma_source="$(find_liblzma_source || true)"

	if [[ -n "$liblzma_source" && -f "$bundled_liblzma" ]]; then
		cp "$liblzma_source" "$bundled_liblzma"
	fi
}

depth_first_codesign_bundle() {
	local bundle_path="$1"

	env -u PYTHONPATH -u DYLD_FRAMEWORK_PATH -u DYLD_LIBRARY_PATH -u TCL_LIBRARY -u TK_LIBRARY \
		BUNDLE_PATH="$bundle_path" "$ICON_PYTHON" - <<'PY'
import os
from py2app.util import codesign_adhoc

codesign_adhoc(os.environ["BUNDLE_PATH"])
PY
}

FRAMEWORK_RUNTIME_DIR="${FRAMEWORK_RUNTIME_DIR_OVERRIDE:-$(find_framework_runtime_dir || true)}"
BASE_PYTHON="$FRAMEWORK_RUNTIME_DIR/Python.framework/Versions/$FRAMEWORK_VERSION/bin/python$FRAMEWORK_VERSION"
FRAMEWORK_LIB_DIR="$FRAMEWORK_RUNTIME_DIR/Python.framework/Versions/$FRAMEWORK_VERSION/lib"
SITE_PACKAGES_DIR="${SITE_PACKAGES_DIR_OVERRIDE:-$(find_site_packages_dir || true)}"
TCL_LIBRARY_RESOLVED="$(resolve_optional_library_dir "${TCL_LIBRARY:-}" "$FRAMEWORK_LIB_DIR/tcl8.6" "$TCL_LIBRARY_DEFAULT" || true)"
TK_LIBRARY_RESOLVED="$(resolve_optional_library_dir "${TK_LIBRARY:-}" "$FRAMEWORK_LIB_DIR/tk8.6" "$TK_LIBRARY_DEFAULT" || true)"

if [[ ! -x "$ICON_PYTHON" ]]; then
	echo "missing icon python runtime: $ICON_PYTHON" >&2
	exit 1
fi

if [[ ! -x "$BASE_PYTHON" ]]; then
	echo "missing framework python runtime: $BASE_PYTHON" >&2
	echo "set PHOTOS_MCP_FRAMEWORK_RUNTIME_DIR or install python@$FRAMEWORK_VERSION via Homebrew first" >&2
	exit 1
fi

if [[ ! -d "$SITE_PACKAGES_DIR" ]]; then
	echo "missing framework build site-packages: $SITE_PACKAGES_DIR" >&2
	echo "set PHOTOS_MCP_SITE_PACKAGES_DIR or prepare .venv-framework$FRAMEWORK_SHORT_VERSION / .venv first" >&2
	exit 1
fi

"$ICON_PYTHON" "$ROOT_DIR/scripts/generate_app_icon.py"

rm -rf "$DIST_DIR" "$BUILD_DIR"

export PYTHONPATH="$SITE_PACKAGES_DIR:$ROOT_DIR/src"
export DYLD_FRAMEWORK_PATH="$FRAMEWORK_RUNTIME_DIR"
export DYLD_LIBRARY_PATH="$FRAMEWORK_LIB_DIR"
export PHOTOS_MCP_SKIP_PY2APP_CODESIGN=1
if [[ -n "$TCL_LIBRARY_RESOLVED" ]]; then
	export TCL_LIBRARY="$TCL_LIBRARY_RESOLVED"
fi
if [[ -n "$TK_LIBRARY_RESOLVED" ]]; then
	export TK_LIBRARY="$TK_LIBRARY_RESOLVED"
fi

cd "$ROOT_DIR"
"$BASE_PYTHON" setup.py py2app --dist-dir "$DIST_DIR" --bdist-base "$BUILD_DIR"

if [[ ! -d "$APP_BUNDLE" && -d "$ALT_APP_BUNDLE" ]]; then
	mv "$ALT_APP_BUNDLE" "$APP_BUNDLE"
fi

if [[ ! -d "$APP_BUNDLE" ]]; then
	echo "expected app bundle not found in $DIST_DIR" >&2
	exit 1
fi

repair_problematic_framework_dylibs "$APP_BUNDLE"
depth_first_codesign_bundle "$APP_BUNDLE"
codesign --verify --deep --strict "$APP_BUNDLE"
"$APP_BUNDLE/Contents/MacOS/PhotosMcp" --health

if [[ -n "$INSTALL_BUNDLE_PATH" ]]; then
	mkdir -p "$(dirname "$INSTALL_BUNDLE_PATH")"
	rm -rf "$INSTALL_BUNDLE_PATH"
	ditto "$APP_BUNDLE" "$INSTALL_BUNDLE_PATH"
	codesign --verify --deep --strict "$INSTALL_BUNDLE_PATH"
	"$INSTALL_BUNDLE_PATH/Contents/MacOS/PhotosMcp" --health
fi