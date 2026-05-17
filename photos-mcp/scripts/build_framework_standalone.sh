#!/usr/bin/env zsh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

FRAMEWORK_RUNTIME_DIR="${PHOTOS_MCP_FRAMEWORK_RUNTIME_DIR:-$ROOT_DIR/.framework-python-runtime}"
FRAMEWORK_VERSION="${PHOTOS_MCP_FRAMEWORK_VERSION:-3.12}"
DIST_DIR="${PHOTOS_MCP_DIST_DIR:-$ROOT_DIR/dist-framework-standalone}"
BUILD_DIR="${PHOTOS_MCP_BUILD_DIR:-$ROOT_DIR/build-framework-standalone}"
SITE_PACKAGES_DIR="${PHOTOS_MCP_SITE_PACKAGES_DIR:-$ROOT_DIR/.venv-framework312/lib/python3.12/site-packages}"
INSTALL_BUNDLE_PATH="${PHOTOS_MCP_INSTALL_BUNDLE_PATH:-$HOME/Applications/PhotosMcp.app}"
TCL_LIBRARY_DEFAULT="$ROOT_DIR/.framework-python-cache/python-3.12.10-expanded/Python_Framework.pkg/Payload/Versions/3.12/lib/tcl8.6"
TK_LIBRARY_DEFAULT="$ROOT_DIR/.framework-python-cache/python-3.12.10-expanded/Python_Framework.pkg/Payload/Versions/3.12/lib/tk8.6"
BASE_PYTHON="$FRAMEWORK_RUNTIME_DIR/Python.framework/Versions/$FRAMEWORK_VERSION/bin/python$FRAMEWORK_VERSION"
FRAMEWORK_LIB_DIR="$FRAMEWORK_RUNTIME_DIR/Python.framework/Versions/$FRAMEWORK_VERSION/lib"
ICON_PYTHON="${PHOTOS_MCP_ICON_PYTHON:-$ROOT_DIR/.venv/bin/python}"
APP_BUNDLE="$DIST_DIR/PhotosMcp.app"
ALT_APP_BUNDLE="$DIST_DIR/photos-mcp.app"

if [[ ! -x "$ICON_PYTHON" ]]; then
	echo "missing icon python runtime: $ICON_PYTHON" >&2
	exit 1
fi

if [[ ! -x "$BASE_PYTHON" ]]; then
	echo "missing framework python runtime: $BASE_PYTHON" >&2
	echo "set PHOTOS_MCP_FRAMEWORK_RUNTIME_DIR or prepare .framework-python-runtime first" >&2
	exit 1
fi

if [[ ! -d "$SITE_PACKAGES_DIR" ]]; then
	echo "missing framework build site-packages: $SITE_PACKAGES_DIR" >&2
	exit 1
fi

"$ICON_PYTHON" "$ROOT_DIR/scripts/generate_app_icon.py"

rm -rf "$DIST_DIR" "$BUILD_DIR"

export PYTHONPATH="$SITE_PACKAGES_DIR:$ROOT_DIR"
export DYLD_FRAMEWORK_PATH="$FRAMEWORK_RUNTIME_DIR"
export DYLD_LIBRARY_PATH="$FRAMEWORK_LIB_DIR"
export TCL_LIBRARY="${TCL_LIBRARY:-$TCL_LIBRARY_DEFAULT}"
export TK_LIBRARY="${TK_LIBRARY:-$TK_LIBRARY_DEFAULT}"

cd "$ROOT_DIR"
"$BASE_PYTHON" setup.py py2app --dist-dir "$DIST_DIR" --bdist-base "$BUILD_DIR"

if [[ ! -d "$APP_BUNDLE" && -d "$ALT_APP_BUNDLE" ]]; then
	mv "$ALT_APP_BUNDLE" "$APP_BUNDLE"
fi

if [[ ! -d "$APP_BUNDLE" ]]; then
	echo "expected app bundle not found in $DIST_DIR" >&2
	exit 1
fi

find "$APP_BUNDLE" -type f \( -perm -111 -o -name '*.so' -o -name '*.dylib' \) -print0 \
	| xargs -0 -I{} codesign --force --sign - '{}'

codesign --force --deep --sign - "$APP_BUNDLE"
codesign --verify --deep --strict "$APP_BUNDLE"
"$APP_BUNDLE/Contents/MacOS/PhotosMcp" --health

if [[ -n "$INSTALL_BUNDLE_PATH" ]]; then
	mkdir -p "$(dirname "$INSTALL_BUNDLE_PATH")"
	rm -rf "$INSTALL_BUNDLE_PATH"
	ditto "$APP_BUNDLE" "$INSTALL_BUNDLE_PATH"
	codesign --verify --deep --strict "$INSTALL_BUNDLE_PATH"
	"$INSTALL_BUNDLE_PATH/Contents/MacOS/PhotosMcp" --health
fi