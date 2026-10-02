"""Choosing what to record: which microphone, and which app's sound to mix in
(the meeting app, say), so both sides of a call end up in the recording.

The work is done by the system's sound service: pulse.py on Linux
(PulseAudio or PipeWire) and wasapi.py on Windows. Both find the chosen
app's sound streams and capture each one; AppAudio here follows those
streams for the whole recording and mixes them, in step with the microphone.
"""

from __future__ import annotations

import sys
import threading
from collections import deque
from dataclasses import dataclass

import numpy as np

# Instead of one app: everything the computer plays.
ALL_SOUND = "@all@"


@dataclass(frozen=True)
class Microphone:
    name: str  # the system's name or id for it, used to record
    description: str


@dataclass(frozen=True)
class Application:
    key: str  # the program, so new streams from the same app are found too
    name: str


def _backend():
    if sys.platform.startswith("linux"):
        from . import pulse

        return pulse if pulse.available() else None
    if sys.platform == "win32":
        from . import wasapi

        return wasapi if wasapi.available() else None
    return None


def can_choose_microphone() -> bool:
    return _backend() is not None


def can_record_apps() -> bool:
    backend = _backend()
    return backend is not None and backend.can_record_apps()


def microphones() -> list[Microphone]:
    backend = _backend()
    return backend.microphones() if backend else []


def default_microphone() -> str | None:
    backend = _backend()
    return backend.default_microphone() if backend else None


def applications() -> list[Application]:
    """The apps playing sound right now, by name."""
    backend = _backend()
    return backend.applications() if backend else []


def app_audio(target: str, rate: int) -> AppAudio:
    backend = _backend()
    return AppAudio(lambda: backend.find_streams(target),
                    lambda spec, on_audio: backend.open_stream(spec, rate, on_audio), rate)


class JitterBuffer:
    """Audio from one stream, waiting to be mixed in. Two clocks never run at
    exactly the same speed, and an app delivers sound in bursts, so it keeps
    a little in reserve: after running dry it waits until `prebuffer` has
    collected again, and if too much piles up it skips ahead."""

    def __init__(self, rate: int) -> None:
        self.prebuffer = rate // 10  # 100 ms
        self.limit = rate // 2
        self._chunks: deque[np.ndarray] = deque()
        self._size = 0
        self._primed = False
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return self._size

    def push(self, samples: np.ndarray) -> None:
        with self._lock:
            self._chunks.append(samples)
            self._size += len(samples)
            excess = self._size - self.prebuffer if self._size > self.limit else 0
            while excess > 0:
                first = self._chunks[0]
                if len(first) <= excess:
                    self._chunks.popleft()
                    cut = len(first)
                else:
                    self._chunks[0] = first[excess:]
                    cut = excess
                self._size -= cut
                excess -= cut

    def take(self, count: int) -> np.ndarray:
        """Exactly `count` samples, padded with silence when there aren't enough."""
        out = np.zeros(count, dtype=np.float32)
        with self._lock:
            if not self._primed:
                if self._size < self.prebuffer:
                    return out
                self._primed = True
            filled = 0
            while filled < count and self._chunks:
                first = self._chunks[0]
                n = min(len(first), count - filled)
                out[filled:filled + n] = first[:n]
                if n == len(first):
                    self._chunks.popleft()
                else:
                    self._chunks[0] = first[n:]
                filled += n
            self._size -= filled
            if filled < count:
                self._primed = False
        return out


class AppAudio:
    """What one app plays, from all its sound streams, including ones it
    opens after the recording started (a call that begins later, a new tab
    in a browser). Or, with ALL_SOUND, everything the computer plays.

    `find_streams()` returns the streams to capture right now as {key: spec}
    (None when it couldn't look), and `open_stream(spec, on_audio)` returns
    a capture with start(), stop() and `running`, delivering mono float
    samples to on_audio from its own thread.

    The microphone sets the pace: for every block of microphone audio, take()
    returns the same number of samples of the app's sound to mix in."""

    POLL_SECONDS = 1.0

    def __init__(self, find_streams, open_stream, rate: int) -> None:
        self._find = find_streams
        self._open = open_stream
        self._rate = rate
        self._streams: dict = {}  # key: (capture, JitterBuffer)
        self._lock = threading.Lock()
        self._stopping = threading.Event()
        self._watcher: threading.Thread | None = None

    def start(self) -> None:
        self._find_streams()
        self._watcher = threading.Thread(target=self._watch, daemon=True)
        self._watcher.start()

    def stop(self) -> None:
        self._stopping.set()
        if self._watcher is not None:
            self._watcher.join(timeout=5)
        with self._lock:
            streams = list(self._streams.values())
            self._streams.clear()
        for capture, _ in streams:
            capture.stop()

    @property
    def stream_count(self) -> int:
        with self._lock:
            return sum(capture.running for capture, _ in self._streams.values())

    def take(self, count: int) -> np.ndarray:
        mixed = np.zeros(count, dtype=np.float32)
        with self._lock:
            buffers = [buffer for _, buffer in self._streams.values()]
        for buffer in buffers:
            mixed += buffer.take(count)
        return mixed

    def _add(self, key, spec) -> None:
        buffer = JitterBuffer(self._rate)
        capture = self._open(spec, buffer.push)
        try:
            capture.start()
        except OSError:
            return
        with self._lock:
            self._streams[key] = (capture, buffer)

    def _find_streams(self) -> None:
        try:
            wanted = self._find()
        except OSError:
            wanted = None
        if wanted is None:
            return
        with self._lock:
            # Captures that have ended and played out are let go (and started
            # again if the stream is still there).
            for key in [k for k, (capture, buffer) in self._streams.items()
                        if not capture.running and not len(buffer)]:
                del self._streams[key]
            new = [key for key in wanted if key not in self._streams]
        for key in new:
            if not self._stopping.is_set():
                self._add(key, wanted[key])

    def _watch(self) -> None:
        while not self._stopping.wait(self.POLL_SECONDS):
            self._find_streams()
