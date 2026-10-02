"""Linux sound through PulseAudio or PipeWire (which speaks the same protocol):
lists the microphones and the apps that are playing sound, and captures
either one.

It uses the sound server's own command-line tools, pactl and parec, which
every desktop with PulseAudio or PipeWire has. Capturing an app means
listening to its playback streams, so the app sounds exactly as it does in
the speakers, and nothing about its playback changes.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
from collections import deque
from dataclasses import dataclass

import numpy as np

from .paths import system_env

RATE = 48_000
# Instead of one app: everything the computer plays.
ALL_SOUND = "@all@"


@dataclass(frozen=True)
class Microphone:
    name: str  # the sound server's name for it, used to record
    description: str


@dataclass(frozen=True)
class Application:
    key: str  # the program, so new streams from the same app are found too
    name: str


def _tool(name: str) -> str | None:
    return shutil.which(name) if sys.platform.startswith("linux") else None


def available() -> bool:
    """Whether microphones and apps can be chosen here."""
    return bool(_tool("pactl") and _tool("parec")) and _pactl("info") is not None


def _pactl(*args: str) -> str | None:
    env = system_env()
    env["LC_ALL"] = "C"  # the output is translated otherwise
    try:
        result = subprocess.run([_tool("pactl") or "pactl", *args], env=env, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout if result.returncode == 0 else None


def _blocks(text: str, header: str) -> list[tuple[int, dict[str, str], dict[str, str]]]:
    """Splits pactl's `list` output into (index, fields, properties) per object."""
    blocks: list[tuple[int, dict[str, str], dict[str, str]]] = []
    for line in text.splitlines():
        if line.startswith(header + " #"):
            blocks.append((int(line.split("#", 1)[1]), {}, {}))
        elif blocks and line.startswith("\t\t") and " = " in line:
            key, _, value = line.strip().partition(" = ")
            blocks[-1][2][key] = value.strip('"')
        elif blocks and line.startswith("\t") and not line.startswith("\t\t") and ":" in line:
            key, _, value = line.strip().partition(":")
            blocks[-1][1][key] = value.strip()
    return blocks


def default_microphone() -> str | None:
    for line in (_pactl("info") or "").splitlines():
        if line.startswith("Default Source:"):
            return line.partition(":")[2].strip() or None
    return None


def microphones() -> list[Microphone]:
    """Every input except the monitors, which play back what an output plays."""
    found = []
    for _, fields, properties in _blocks(_pactl("list", "sources") or "", "Source"):
        if fields.get("Monitor of Sink", "n/a") != "n/a" or properties.get("device.class") == "monitor":
            continue
        if "Name" in fields:
            found.append(Microphone(fields["Name"], fields.get("Description") or fields["Name"]))
    return found


def _app_key(properties: dict[str, str]) -> str | None:
    return properties.get("application.process.binary") or properties.get("application.name")


def _app_streams(text: str) -> list[tuple[int, dict[str, str]]]:
    """The playback streams of other programs than this one."""
    own = str(os.getpid())
    return [(index, properties) for index, _, properties in _blocks(text, "Sink Input")
            if _app_key(properties) and properties.get("application.process.id") != own]


def applications() -> list[Application]:
    """The apps playing sound right now. Browsers and meeting apps only show
    up once they play something, for example once a call has started."""
    found: dict[str, Application] = {}
    for _, properties in _app_streams(_pactl("list", "sink-inputs") or ""):
        key = _app_key(properties)
        name = properties.get("application.name") or key
        binary = properties.get("application.process.binary")
        # Electron apps call themselves "Chromium": tell them apart by their program.
        if binary and binary.lower() not in name.lower():
            name = f"{name} ({binary})"
        found.setdefault(key, Application(key, name))
    return sorted(found.values(), key=lambda app: app.name.lower())


class Capture:
    """One parec process delivering mono float samples at RATE to a callback,
    on its own thread."""

    def __init__(self, source_args: list[str], on_audio) -> None:
        self._args = source_args
        self._on_audio = on_audio
        self._process: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self.started = threading.Event()  # the first audio has arrived
        self.error = ""

    def start(self) -> None:
        self._process = subprocess.Popen(
            [_tool("parec") or "parec", *self._args, "--raw", "--format=float32le", f"--rate={RATE}",
             "--channels=1", "--latency-msec=20", "--client-name=Leos Lyssnare"],
            env=system_env(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self._thread = threading.Thread(target=self._read, daemon=True)
        self._thread.start()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def stop(self) -> None:
        if self._process is not None and self._process.poll() is None:
            self._process.terminate()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _read(self) -> None:
        process = self._process
        fd = process.stdout.fileno()
        leftover = b""
        while data := os.read(fd, 16_384):
            data = leftover + data
            usable = len(data) - len(data) % 4
            leftover = data[usable:]
            if usable:
                self._on_audio(np.frombuffer(data[:usable], dtype=np.float32).copy())
                self.started.set()
        process.wait()
        if process.returncode not in (0, -15):  # -15: stopped by us
            self.error = (process.stderr.read().decode(errors="replace").strip()
                          or f"parec exited with code {process.returncode}")
        process.stdout.close()
        process.stderr.close()
        self.started.set()


class _JitterBuffer:
    """Audio from one stream, waiting to be mixed in. Two clocks never run at
    exactly the same speed, and an app delivers sound in bursts, so it keeps
    a little in reserve: after running dry it waits until PREBUFFER has
    collected again, and if too much piles up it skips ahead."""

    PREBUFFER = RATE // 10  # 100 ms
    LIMIT = RATE // 2

    def __init__(self) -> None:
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
            excess = self._size - self.PREBUFFER if self._size > self.LIMIT else 0
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
                if self._size < self.PREBUFFER:
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
    """What one app plays, from all its playback streams, including ones it
    opens after the recording started (a call that begins later, a new tab
    in a browser). Or, with ALL_SOUND, everything the computer plays.

    The microphone sets the pace: for every block of microphone audio, take()
    returns the same number of samples of the app's sound to mix in."""

    POLL_SECONDS = 1.0

    def __init__(self, target: str) -> None:
        self.target = target
        self._streams: dict[int, tuple[Capture, _JitterBuffer]] = {}
        self._lock = threading.Lock()
        self._stopping = threading.Event()
        self._watcher: threading.Thread | None = None

    def start(self) -> None:
        if self.target == ALL_SOUND:
            self._add(-1, ["--device=@DEFAULT_MONITOR@"])
            return
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

    def _add(self, index: int, source_args: list[str]) -> None:
        buffer = _JitterBuffer()
        capture = Capture(source_args, buffer.push)
        try:
            capture.start()
        except OSError:
            return
        with self._lock:
            self._streams[index] = (capture, buffer)

    def _find_streams(self) -> None:
        text = _pactl("list", "sink-inputs")
        if text is None:
            return
        wanted = {index for index, properties in _app_streams(text) if _app_key(properties) == self.target}
        with self._lock:
            # Captures that have ended and played out are let go (and started
            # again if the stream is still there).
            for index in [i for i, (capture, buffer) in self._streams.items()
                          if not capture.running and not len(buffer)]:
                del self._streams[index]
            new = wanted - self._streams.keys()
        for index in sorted(new):
            self._add(index, [f"--monitor-stream={index}"])

    def _watch(self) -> None:
        while not self._stopping.wait(self.POLL_SECONDS):
            self._find_streams()

