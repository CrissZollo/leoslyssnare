"""Where the app keeps its files. Everything stays on this computer."""

from __future__ import annotations

import os
from datetime import datetime

from platformdirs import user_data_dir, user_documents_dir

APP_DIR_NAME = "LeosLyssnare"


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


def _timestamp() -> str:
    # No colons: they aren't allowed in Windows file names.
    return datetime.now().strftime("%Y-%m-%d %H.%M.%S")


def new_recording_path() -> str:
    return os.path.join(recordings(), f"Meeting {_timestamp()}.m4a")


def transcript_path_for(audio_path: str) -> str:
    name = os.path.splitext(os.path.basename(audio_path))[0]
    path = os.path.join(transcripts(), f"{name}.txt")
    if os.path.exists(path):
        path = os.path.join(transcripts(), f"{name} {_timestamp()}.txt")
    return path
