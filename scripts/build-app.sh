#!/bin/zsh
# Builds "Leos Lyssnare.app" for Apple Silicon. Run on the Mac from the project root:
#   ./scripts/build-app.sh
set -euo pipefail

cd "$(dirname "$0")/.."

APP_NAME="Leos Lyssnare"
EXECUTABLE="LeosLyssnare"
APP="build/${APP_NAME}.app"

echo "▶ Building (release, arm64)…"
swift build -c release --arch arm64
BIN_DIR="$(swift build -c release --arch arm64 --show-bin-path)"

echo "▶ Assembling ${APP}…"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN_DIR/$EXECUTABLE" "$APP/Contents/MacOS/$EXECUTABLE"
cp Support/Info.plist "$APP/Contents/Info.plist"

# Copy any SwiftPM resource bundles from dependencies.
for bundle in "$BIN_DIR"/*.bundle(N); do
    cp -R "$bundle" "$APP/Contents/Resources/"
done

echo "▶ Signing (ad-hoc, for this Mac)…"
codesign --force --deep --sign - "$APP"

echo "✅ Done: $APP"
echo "   Start it with:  open \"$APP\""
echo "   Or drag it into /Applications."
