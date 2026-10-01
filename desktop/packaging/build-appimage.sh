#!/usr/bin/env bash
# Builds "Leos_Lyssnare-x86_64.AppImage". Run on Linux from the desktop/ folder:
#   ./packaging/build-appimage.sh
#
# Needs: python3 (3.10+) with venv, cmake, a C compiler, curl, and the ALSA
# headers (Debian/Ubuntu: sudo apt install python3-venv cmake build-essential libasound2-dev curl).
# Build on the oldest distribution you want to support (Ubuntu 22.04 in CI):
# the AppImage runs on that and anything newer.
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$PWD"
WORK="$ROOT/build/linux"
PORTAUDIO_VERSION="v19.7.0"
ARCH="$(uname -m)"
mkdir -p "$WORK"

echo "▶ Python environment…"
if [ ! -d "$WORK/venv" ]; then
    python3 -m venv "$WORK/venv"
fi
# shellcheck disable=SC1091
source "$WORK/venv/bin/activate"
pip install --upgrade pip wheel >/dev/null
pip install -r requirements.txt pyinstaller

echo "▶ PortAudio $PORTAUDIO_VERSION (ALSA only, so it works with PulseAudio and PipeWire)…"
PORTAUDIO_LIB="$WORK/portaudio-install/lib/libportaudio.so.2"
if [ ! -f "$PORTAUDIO_LIB" ]; then
    rm -rf "$WORK/portaudio"
    git clone --depth 1 --branch "$PORTAUDIO_VERSION" https://github.com/PortAudio/portaudio.git "$WORK/portaudio"
    cmake -S "$WORK/portaudio" -B "$WORK/portaudio/build" \
        -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$WORK/portaudio-install" \
        -DCMAKE_INSTALL_LIBDIR=lib -DPA_USE_ALSA=ON -DPA_USE_JACK=OFF -DPA_BUILD_SHARED=ON -DPA_BUILD_STATIC=OFF
    cmake --build "$WORK/portaudio/build" --parallel
    cmake --install "$WORK/portaudio/build"
    # Some PortAudio versions name the file differently; normalise it.
    if [ ! -f "$PORTAUDIO_LIB" ]; then
        cp "$(find "$WORK/portaudio-install" -name 'libportaudio.so*' -type f | head -n1)" "$PORTAUDIO_LIB"
    fi
fi

echo "▶ Icons…"
QT_QPA_PLATFORM=offscreen python packaging/make_icons.py

echo "▶ PyInstaller…"
LEOSLYSSNARE_PORTAUDIO="$PORTAUDIO_LIB" pyinstaller --noconfirm --clean \
    --distpath "$WORK/dist" --workpath "$WORK/pyinstaller" packaging/leoslyssnare.spec

echo "▶ AppDir…"
APPDIR="$WORK/LeosLyssnare.AppDir"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/lib" "$APPDIR/usr/share/applications" "$APPDIR/usr/share/icons/hicolor/256x256/apps"
cp -a "$WORK/dist/LeosLyssnare" "$APPDIR/usr/lib/leoslyssnare"
cp packaging/leoslyssnare.desktop "$APPDIR/leoslyssnare.desktop"
cp packaging/leoslyssnare.desktop "$APPDIR/usr/share/applications/"
cp leoslyssnare/resources/icon.png "$APPDIR/leoslyssnare.png"
cp leoslyssnare/resources/icon.png "$APPDIR/usr/share/icons/hicolor/256x256/apps/leoslyssnare.png"
ln -sf leoslyssnare.png "$APPDIR/.DirIcon"
cat > "$APPDIR/AppRun" <<'APPRUN'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/lib/leoslyssnare/LeosLyssnare" "$@"
APPRUN
chmod +x "$APPDIR/AppRun"

echo "▶ appimagetool…"
TOOL="$WORK/appimagetool-$ARCH.AppImage"
if [ ! -x "$TOOL" ]; then
    curl -fsSL -o "$TOOL" \
        "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-$ARCH.AppImage"
    chmod +x "$TOOL"
fi
OUTPUT="$ROOT/build/Leos_Lyssnare-$ARCH.AppImage"
# APPIMAGE_EXTRACT_AND_RUN lets this work without FUSE (for example in CI or Docker).
ARCH="$ARCH" APPIMAGE_EXTRACT_AND_RUN=1 "$TOOL" --no-appstream "$APPDIR" "$OUTPUT"

echo "✅ Done: $OUTPUT"
echo "   Start it with:  chmod +x \"$OUTPUT\" && \"$OUTPUT\""
