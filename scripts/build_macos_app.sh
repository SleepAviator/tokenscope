#!/bin/bash
set -euo pipefail

VERSION="${VERSION:-}"
ARCH="${ARCH:-}"
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"
if [[ ! "$VERSION" =~ ^v?[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "VERSION must be a semantic version such as v0.1.0" >&2
  exit 2
fi
if [[ "$ARCH" != arm64 && "$ARCH" != x86_64 ]]; then
  echo "ARCH must be arm64 or x86_64" >&2
  exit 2
fi
RELEASE_VERSION="${VERSION#v}"
BUILD_ROOT="$PROJECT_ROOT/build/macos-$ARCH"
DIST_DIR="$BUILD_ROOT/dist"
APP_DIR="$BUILD_ROOT/TokenScope.app"
APP_CONTENTS="$APP_DIR/Contents"

python -m PyInstaller \
  --noconfirm --clean --onedir --name TokenScopeServer \
  --distpath "$DIST_DIR" --workpath "$BUILD_ROOT/work" --specpath "$BUILD_ROOT" \
  --hidden-import update --hidden-import collect --hidden-import live_meter --hidden-import live_probe \
  --hidden-import live_telemetry \
  --add-data "$PROJECT_ROOT/update.py:." --add-data "$PROJECT_ROOT/collect.py:." \
  --add-data "$PROJECT_ROOT/live_meter.py:." --add-data "$PROJECT_ROOT/live_probe.py:." \
  --add-data "$PROJECT_ROOT/live_telemetry.py:." \
  --add-data "$PROJECT_ROOT/THIRD_PARTY_NOTICES.md:." \
  --add-data "$PROJECT_ROOT/config.example.ini:." --add-data "$PROJECT_ROOT/web.html:." \
  --add-data "$PROJECT_ROOT/web.js:." --add-data "$PROJECT_ROOT/session_usage.js:." --add-data "$PROJECT_ROOT/response_speed.js:." \
  --add-data "$PROJECT_ROOT/i18n.js:." --add-data "$PROJECT_ROOT/web.css:." \
  app.py

mkdir -p "$APP_CONTENTS/MacOS" "$APP_CONTENTS/Resources"
cp macos/Info.plist "$APP_CONTENTS/Info.plist"
cp macos/TokenScope.icns "$APP_CONTENTS/Resources/TokenScope.icns"
mkdir -p "$APP_CONTENTS/Resources/TokenScopeServer"
cp -R "$DIST_DIR/TokenScopeServer/." "$APP_CONTENTS/Resources/TokenScopeServer/"
cp config.example.ini "$APP_CONTENTS/Resources/config.example.ini"
cp THIRD_PARTY_NOTICES.md "$APP_CONTENTS/Resources/THIRD_PARTY_NOTICES.md"
swiftc -O -framework AppKit -o "$APP_CONTENTS/MacOS/TokenScope" macos/launcher.swift
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $RELEASE_VERSION" "$APP_CONTENTS/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleVersion $RELEASE_VERSION" "$APP_CONTENTS/Info.plist"

mkdir -p "$PROJECT_ROOT/release-output"
ditto -c -k --sequesterRsrc --keepParent "$APP_DIR" \
  "$PROJECT_ROOT/release-output/TokenScope-$VERSION-macOS-$ARCH.zip"
