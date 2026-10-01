# PyInstaller spec for Leos Lyssnare (Windows and Linux).
# Build from the desktop/ folder with:  pyinstaller --noconfirm packaging/leoslyssnare.spec
import os
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

datas = [(os.path.join(ROOT, "leoslyssnare", "resources"), os.path.join("leoslyssnare", "resources"))]
datas += collect_data_files("faster_whisper")  # Silero VAD model
datas += collect_data_files("certifi")

binaries = collect_dynamic_libs("ctranslate2") + collect_dynamic_libs("sherpa_onnx")

# On Linux, bundle a PortAudio built by build-appimage.sh (ALSA only, no JACK),
# so recording works without installing anything.
portaudio = os.environ.get("LEOSLYSSNARE_PORTAUDIO")
if sys.platform.startswith("linux") and portaudio:
    binaries.append((portaudio, "."))

a = Analysis(
    [os.path.join(ROOT, "run.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=["sherpa_onnx", "sounddevice", "_sounddevice_data"],
    excludes=["tkinter", "matplotlib", "IPython", "pytest", "torch"],
)

# Audio and graphics drivers must come from the user's system, not the build
# machine, or sound and Qt can break on other distributions.
SYSTEM_LIBS = ("libasound.so", "libjack.so", "libpulse", "libpipewire", "libGL.so", "libEGL.so",
               "libGLX", "libGLdispatch", "libdrm.so", "libgbm.so", "libwayland-", "libxcb.so",
               "libX11.so", "libfontconfig.so", "libfreetype.so", "libdbus-1.so")
if sys.platform.startswith("linux"):
    a.binaries = [b for b in a.binaries if not os.path.basename(b[0]).startswith(SYSTEM_LIBS)]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LeosLyssnare",
    console=False,
    icon=os.path.join(ROOT, "packaging", "icon.ico"),
    upx=False,
)

coll = COLLECT(exe, a.binaries, a.datas, name="LeosLyssnare", upx=False)
