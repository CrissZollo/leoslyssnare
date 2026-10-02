"""Where the app keeps its files. Everything stays on this computer."""

from __future__ import annotations

import os
import sys
from datetime import datetime

from platformdirs import user_data_dir, user_documents_dir, user_downloads_dir

APP_DIR_NAME = "LeosLyssnare"

AUDIO_EXTENSIONS = (".m4a", ".mp3", ".wav", ".aac", ".flac", ".ogg", ".opus", ".mp4", ".mov",
                    ".webm", ".wma", ".aiff", ".aif", ".caf")


def system_env() -> dict[str, str]:
    """The environment for system programs the app starts. A PyInstaller build
    points LD_LIBRARY_PATH at its own Qt, and Qt programs such as Dolphin or
    kde-open fail to start when they load that instead of the system's."""
    env = dict(os.environ)
    if getattr(sys, "frozen", False):
        original = env.pop("LD_LIBRARY_PATH_ORIG", None)
        if original is None:
            env.pop("LD_LIBRARY_PATH", None)
        else:
            env["LD_LIBRARY_PATH"] = original
        for name in ("QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH", "QML2_IMPORT_PATH"):
            env.pop(name, None)
    return env


def _ensure(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    return path


def models() -> str:
    """Windows: %LOCALAPPDATA%\\LeosLyssnare\\Models
    Linux:   ~/.local/share/LeosLyssnare/Models"""
    return _ensure(os.path.join(user_data_dir(APP_DIR_NAME, appauthor=False), "Models"))


def documents() -> str:
    """~/Documents/LeosLyssnare (on Linux, wherever XDG_DOCUMENTS_DIR points)."""
    return _ensure(os.path.join(user_documents_dir(), APP_DIR_NAME))


def recordings() -> str:
    return _ensure(os.path.join(documents(), "Recordings"))


def transcripts() -> str:
    return _ensure(os.path.join(documents(), "Transcripts"))


def transcript_data(transcript_path: str) -> str:
    """Where the exact timings for a .txt transcript are kept, out of sight:
    Windows: %LOCALAPPDATA%\\LeosLyssnare\\Transcript data
    Linux:   ~/.local/share/LeosLyssnare/Transcript data"""
    folder = _ensure(os.path.join(user_data_dir(APP_DIR_NAME, appauthor=False), "Transcript data"))
    return os.path.join(folder, os.path.basename(transcript_path) + ".json")


def find_audio(source_path: str, transcript_path: str) -> str | None:
    """The recording a transcript was made from. Looks where it was, next to
    the transcript, among the recordings and in Downloads: first for the same
    file name, then for the same name with another audio extension."""
    if os.path.isfile(source_path):
        return source_path
    name = os.path.basename(source_path)
    stem = os.path.splitext(name)[0]
    folders: list[str] = []
    for folder in (os.path.dirname(source_path), os.path.dirname(os.path.abspath(transcript_path)),
                   recordings(), user_downloads_dir(), documents()):
        if folder and folder not in folders and os.path.isdir(folder):
            folders.append(folder)
    for candidate in [os.path.join(f, name) for f in folders] + [
            os.path.join(f, stem + ext) for f in folders for ext in AUDIO_EXTENSIONS]:
        if os.path.isfile(candidate):
            return candidate
    return None


def _timestamp() -> str:
    # No colons: they aren't allowed in Windows file names.
    return datetime.now().strftime("%Y-%m-%d %H.%M.%S")


def new_recording_path(extension: str) -> str:
    return os.path.join(recordings(), f"Meeting {_timestamp()}{extension}")


def transcript_path_for(audio_path: str) -> str:
    name = os.path.splitext(os.path.basename(audio_path))[0]
    path = os.path.join(transcripts(), f"{name}.txt")
    if os.path.exists(path):
        path = os.path.join(transcripts(), f"{name} {_timestamp()}.txt")
    return path
