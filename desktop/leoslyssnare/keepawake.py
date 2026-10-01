"""Keeps the computer from idle-sleeping during a recording or transcription.

Reference counted, so recording and transcribing at the same time works.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import threading

_lock = threading.Lock()
_count = 0
_inhibitor: subprocess.Popen | None = None

# Windows SetThreadExecutionState flags.
_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001


def _enable() -> None:
    global _inhibitor
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.kernel32.SetThreadExecutionState(_ES_CONTINUOUS | _ES_SYSTEM_REQUIRED)
    elif sys.platform.startswith("linux"):
        inhibit = shutil.which("systemd-inhibit")
        if inhibit:
            try:
                _inhibitor = subprocess.Popen(
                    [inhibit, "--what=idle:sleep", "--who=Leos Lyssnare",
                     "--why=Recording or transcribing a meeting", "--mode=block",
                     "sleep", "infinity"],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            except OSError:
                _inhibitor = None


def _disable() -> None:
    global _inhibitor
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.kernel32.SetThreadExecutionState(_ES_CONTINUOUS)
    elif _inhibitor is not None:
        _inhibitor.terminate()
        _inhibitor = None


def acquire() -> None:
    global _count
    with _lock:
        _count += 1
        if _count == 1:
            _enable()


def release() -> None:
    global _count
    with _lock:
        if _count == 0:
            return
        _count -= 1
        if _count == 0:
            _disable()
